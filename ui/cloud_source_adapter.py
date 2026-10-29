"""Import the pinned owner source, without modifying its model mathematics."""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import os
from pathlib import Path
import sys

from cloud_contract import CloudInputError, json_value
from dataset_paths import reject_reparse, display_path


_HELD_SOURCES = []


class _PinnedImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, base, package, entries):
        self.base = base; self.package = package; self.entries = entries

    def _entry(self, fullname):
        relative = fullname.replace('.', '/')
        for suffix, is_package in (('/__init__.py', True), ('.py', False)):
            name = relative + suffix
            if name in self.entries:
                return name, is_package
        raise ImportError('The requested .cloud module is not in its pinned source component.', name=fullname)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.package and not fullname.startswith(self.package + '.'):
            return None
        name, is_package = self._entry(fullname)
        # No package search directories: later files cannot join this namespace.
        # Every relative import is resolved by this pinned finder instead.
        return importlib.util.spec_from_file_location(fullname, self.base / name, loader=self,
            submodule_search_locations=[] if is_package else None)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name, _ = self._entry(module.__name__)
        entry = self.entries[name]; source = self.base / name
        # Bytes were captured under the manifest/file leases. No path is
        # reopened when a lazy import occurs during an active live session.
        raw = entry['source']
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ImportError('The pinned .cloud module changed before import.')
        exec(compile(raw, str(source), 'exec', dont_inherit=True), module.__dict__)


def source_root():
    # Frozen workers carry the same directory beside the executable. Development
    # uses the preserved baseline one directory above ui/.
    executable = Path(sys.executable).parent
    if getattr(sys, 'frozen', False):
        from cloud_production import admit
        return admit('sdk')
    return Path(__file__).resolve().parent.parent / 'cloud_source' / '4.0.0'


def verify_source(kind):
    if kind not in ('runtime', 'training'):
        raise CloudInputError('Unknown .cloud source component.')
    from cloud_project import ProjectLease
    lease = ProjectLease('.')
    try:
        root = lease._directory(source_root())
        descriptor = lease.hold_file(root / 'IMPORT_MANIFEST.json', 128 * 1024)
        raw = descriptor.read_bytes()
        manifest = json_value(raw, 128 * 1024)
        prefix = 'runtime_sdk/' if kind == 'runtime' else 'training_source/'
        expected = {entry['path']: entry for entry in manifest['files'] if entry['path'].startswith(prefix)}
        for relative, entry in expected.items():
            path = lease.hold_file(root / relative, entry['bytes'])
            if (path.stat().st_size != entry['bytes']
                    or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']):
                raise CloudInputError('The preserved .cloud source failed its integrity check.')
        base = root / prefix.rstrip('/')
        # Do not traverse a reparse directory, even for discovery. Unlisted
        # bytecode must never supersede a verified source file on import.
        for folder, directories, filenames in os.walk(base, followlinks=False):
            for name in directories:
                lease._directory(Path(folder) / name)
            for name in filenames:
                path = reject_reparse(Path(folder) / name)
                if path.suffix.lower() in ('.py', '.pyc', '.pyo', '.pyd', '.dll', '.pth'):
                    relative = path.relative_to(root).as_posix()
                    if relative not in expected:
                        raise CloudInputError('An unlisted executable file was found in the .cloud source component.')
        entries = {relative.removeprefix(prefix): {**entry,'source':(root/relative).read_bytes()}
                   for relative, entry in expected.items() if relative.endswith('.py')}
        for entry in entries.values():
            if len(entry['source'])!=entry['bytes'] or hashlib.sha256(entry['source']).hexdigest()!=entry['sha256']:
                raise CloudInputError('The preserved .cloud source changed during its snapshot.')
        return Path(display_path(base)), lease, entries
    except BaseException:
        lease.stack.close(); raise


def enable_source(kind):
    base, lease, entries = verify_source(kind)
    package = 'cloud_sdk' if kind == 'runtime' else 'cloudstudio'
    existing = sys.modules.get(package)
    if existing is not None:
        lease.stack.close()
        raise CloudInputError('The .cloud source package was imported before verification.')
    # Worker safety modules are imported by the worker before this point.
    # No source/project directory is inserted into the ordinary import path.
    sys.meta_path.insert(0, _PinnedImporter(base, package, entries))
    lease.stack.close()
    return base
