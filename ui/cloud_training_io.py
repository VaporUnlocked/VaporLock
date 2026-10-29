"""Identity-protected append log adapter for the unchanged owner trainer."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import re


@contextmanager
def protected_training_log(training):
    original = training._prepare_output
    stream = None
    target = None

    class AppendView:
        def __enter__(self): return self
        def write(self, value): return stream.write(value)
        def __exit__(self, *args):
            stream.flush()

    class OutputPath(type(Path())):
        def open(self, mode='r', *args, **kwargs):
            nonlocal stream
            if target is not None and self == target and mode == 'a':
                if stream is None:
                    # CREATE_NEW prevents adopting a planted file or link. The
                    # single held writer denies replacement and other writers
                    # throughout training; every append uses this same handle.
                    from dataset_transaction import _api
                    import msvcrt
                    api = _api()
                    handle = api.CreateFileW(str(self), 0x40000000, 1, None, 1, 0x00200000, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
                    except BaseException:
                        api.CloseHandle(handle)
                        raise
                    stream = os.fdopen(fd, 'w', encoding='utf-8', newline='\n')
                return AppendView()
            return super().open(mode, *args, **kwargs)

    def prepare(*args, **kwargs):
        nonlocal target
        output = OutputPath(original(*args, **kwargs))
        target = output / 'training.jsonl'
        return output

    training._prepare_output = prepare
    try:
        yield
    finally:
        training._prepare_output = original
        if stream is not None:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()


@contextmanager
def protected_training_outputs(output):
    """Private-alpha, identity-held writes; NOT atomic crash replacement.

    Strong directory/reparse pins remain held by NewRunLease. Windows forbids
    renaming children under those pins, so this scoped adapter copies completed
    temporary files to retained output identities. Resume is supported only
    after successful completion/cooperative stop, not an interrupted write.
    """
    from dataset_paths import local_path
    from dataset_transaction import _api,HeldFile
    from cloud_contract import CloudInputError,MAX_CHECKPOINT
    import msvcrt
    root=local_path(output);writers={};original=os.replace;api=_api()
    fixed={'config.json','metrics.json','run.json','best.cloud','last.cloud','last.cloudtrain'}
    def replace(source,destination,*args,**kwargs):
        src=local_path(source);dst=local_path(destination)
        if (args or kwargs or src.parent!=root or dst.parent!=root
                or (dst.name not in fixed and re.fullmatch(r'epoch_[0-9]{4,5}\.cloud',dst.name) is None)
                or not src.name.startswith('.') or not src.name.endswith('.tmp')
                or src==dst):
            raise CloudInputError('A training output write left its admitted new run.')
        with HeldFile(src,read_only=True) as held:
            info=held.info();size=(info.size_high<<32)|info.size_low
            if not 0<size<=MAX_CHECKPOINT:raise CloudInputError('Training output exceeds its size budget.')
            stream=writers.get(dst.name)
            if stream is None:
                handle=api.CreateFileW(str(dst),0xc0000000,1,None,1,0x00200000,None)
                if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
                try:fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
                except BaseException:
                    api.CloseHandle(handle);raise
                stream=os.fdopen(fd,'r+b',buffering=0);writers[dst.name]=stream
            stream.seek(0)
            for block in held.blocks():
                view=memoryview(block)
                while view:
                    count=stream.write(view)
                    if not count:raise OSError('Training output write stopped.')
                    view=view[count:]
            stream.truncate(size);stream.flush();os.fsync(stream.fileno())
        # The baseline finally block owns temporary-file deletion.
    os.replace=replace
    try:yield
    finally:
        os.replace=original
        for stream in writers.values():stream.close()
