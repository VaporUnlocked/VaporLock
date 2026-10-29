"""One scoped Stop-only reader for our inherited Windows training stdin pipe."""
import ctypes
from ctypes import wintypes as W
import os
import sys
import threading

from cloud_contract import CloudInputError, MAX_JSON, json_value


class StopListener:
    def __init__(self, stop):
        self.stop = stop
        self.finishing = threading.Event()
        self.thread = None
        self.failure = None
        self.saved_blocking = None

    def __enter__(self):
        if os.name != 'nt': raise CloudInputError('This training command reader requires Windows pipes.')
        import msvcrt
        self.stream = sys.stdin.buffer
        self.fd = self.stream.fileno()
        self.handle = W.HANDLE(msvcrt.get_osfhandle(self.fd))
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.GetFileType.argtypes = [W.HANDLE]; self.api.GetFileType.restype = W.DWORD
        self.api.PeekNamedPipe.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD,
            ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD), ctypes.POINTER(W.DWORD)]
        self.api.PeekNamedPipe.restype = W.BOOL
        if self.api.GetFileType(self.handle) != 3:
            raise CloudInputError('Training commands require the owned stdin pipe.')
        self.saved_blocking = os.get_blocking(self.fd)
        os.set_blocking(self.fd, False)
        try:
            # Non-daemon: Python must not finalize buffered stdin while this
            # reader lives. The parent Job remains the ultimate process bound.
            self.thread = threading.Thread(target=self._read, name='CloudTrainingStop')
            self.thread.start()
        except BaseException:
            os.set_blocking(self.fd, self.saved_blocking)
            raise
        return self

    def _pipe_eof(self):
        available = W.DWORD()
        if self.api.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
            return False  # zero available bytes on a LIVE pipe is not EOF
        error = ctypes.get_last_error()
        if error in (109, 232, 233): return True  # broken/closing/disconnected pipe
        raise ctypes.WinError(error)

    def _read(self):
        pending = bytearray()
        try:
            while not self.finishing.is_set():
                try:
                    # Preserves bytes prefetched by the initial request reader.
                    block = self.stream.read1(4096)
                except BlockingIOError:
                    block = None
                except OSError as error:
                    if getattr(error, 'winerror', None) in (109, 232, 233):
                        if self._pipe_eof(): self.stop.set(); return
                        block = None
                    else: raise
                if block:
                    pending.extend(block)
                    newline = pending.find(b'\n')
                    if newline >= 0:
                        raw = bytes(pending[:newline + 1])
                        if len(raw) > MAX_JSON: self.stop.set(); return
                        try:
                            payload = json_value(raw)
                            if type(payload) is not dict or payload != {'command': 'stop'}:
                                raise CloudInputError('Unsupported training command.')
                        except (CloudInputError, ValueError, UnicodeError):
                            self.stop.set(); return
                        self.stop.set(); return
                    if len(pending) > MAX_JSON: self.stop.set(); return
                else:
                    if self._pipe_eof(): self.stop.set(); return
                    self.finishing.wait(.01)
        except BaseException as error:
            self.failure = error
            self.stop.set()

    def __exit__(self, kind, error, traceback):
        self.finishing.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            # Do not put the pipe back into blocking mode beneath a live reader.
            raise CloudInputError('Training command reader did not finish.')
        os.set_blocking(self.fd, self.saved_blocking)
        if self.failure is not None:
            raise CloudInputError('Training command reader failed.') from self.failure
        return False
