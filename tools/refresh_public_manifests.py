"""Refresh editable-source inventories; no publisher keys or official-build gate."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_public_source import read_plain, _windows_handle, _output_identity


def plain(path: Path) -> Path:
    for node in (path, *path.parents):
        if node.exists() and (node.is_symlink() or getattr(node.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked manifest source rejected')
    return path


def relative_member(value):
    if type(value) is not str or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('Invalid source-relative manifest member')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('Invalid source-relative manifest member')
    return Path(*path.parts)


def source_files(folder, stack):
    plain(folder)
    if os.name == 'nt':
        stack.enter_context(_windows_handle(folder, directory=True, deny_directory_write=True))
    # Hold a directory before scanning it, and hold each child before descent.
    with os.scandir(folder) as scan:
        names = sorted(entry.name for entry in scan)
    for name in names:
        if name == '__pycache__':
            continue
        path = plain(folder / name)
        if path.is_dir():
            yield from source_files(path, stack)
        elif path.is_file() and path.suffix not in ('.pyc', '.pyo'):
            yield path


def write_outputs(outputs):
    """Admit all regular/single-link outputs before modifying either manifest."""
    with ExitStack() as stack:
        opened = []
        for path, payload in outputs:
            plain(path)
            if os.name == 'nt':
                import ctypes
                import msvcrt
                from ctypes import wintypes
                for parent in reversed(path.parents):
                    stack.enter_context(_windows_handle(parent, directory=True))
                native = stack.enter_context(_windows_handle(path, directory=False, writable=True, create=not path.exists()))
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                duplicate = wintypes.HANDLE()
                current = kernel.GetCurrentProcess()
                if not kernel.DuplicateHandle(current, native, current, ctypes.byref(duplicate), 0, False, 2):
                    raise OSError(ctypes.get_last_error(), 'Cannot retain manifest output')
                descriptor = msvcrt.open_osfhandle(duplicate.value, os.O_RDWR | os.O_BINARY)
            else:
                flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
                if not path.exists():
                    flags |= os.O_CREAT | os.O_EXCL
                descriptor = os.open(path, flags, 0o600)
            file = stack.enter_context(os.fdopen(descriptor, 'r+b'))
            _output_identity(path, file, links=1)
            opened.append((path, file, payload.encode('utf-8')))
        for path, file, raw in opened:
            _output_identity(path, file, links=1)
            file.seek(0)
            file.write(raw)
            file.truncate()
            file.flush()
            os.fsync(file.fileno())
            file.seek(0)
            if file.read() != raw:
                raise ValueError('Manifest output readback failed')
            _output_identity(path, file, links=1)


def refresh(root: Path) -> dict:
    root = plain(root.absolute())
    cloud = root / 'cloud_source/4.0.0'
    with ExitStack() as stack:
        for ancestor in reversed((root, *root.parents)):
            if os.name == 'nt':
                stack.enter_context(_windows_handle(ancestor, directory=True))
        resource_path = plain(root / 'ui_system/runtime/resources.json')
        # This descriptor is an editable build input, validated before either
        # output is written. read_plain's lease must end before rewriting it.
        with read_plain(resource_path) as raw:
            resources = json.loads(raw)
        selected = []
        for entry in resources['resources']:
            member = relative_member(entry['file'])
            source = plain(root / member)
            if not source.is_relative_to(root):
                raise ValueError('Resource outside source tree')
            selected.append((entry, source))
        entries = []
        for branch in ('runtime_sdk', 'training_source'):
            for path in source_files(plain(cloud / branch), stack):
                raw = stack.enter_context(read_plain(path))
                entries.append({'path': path.relative_to(cloud).as_posix(),
                                'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        if not entries:
            raise ValueError('Cloud source is absent')
        for entry, source in selected:
            raw = stack.enter_context(read_plain(source))
            entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        target = plain(cloud / 'IMPORT_MANIFEST.json')
        write_outputs([(target, json.dumps({'version': '4.0.0', 'files': entries}, indent=2) + '\n'),
                       (resource_path, json.dumps(resources, indent=2) + '\n')])
    return {'cloud_files': len(entries), 'ui_resources': len(resources['resources'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    print(json.dumps(refresh(parser.parse_args().root)))
