"""Production Cloud component admission; no framework imports or discovery fallback.

Inventories are authenticated by compiled build digests. Windows read leases
span hashing, process launch, lazy imports and final process cleanup.
"""
from contextlib import ExitStack
import ctypes
import ast
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import zipimport
import threading
import ntpath
from functools import lru_cache

from cloud_contract import CloudInputError

MAX_DESCRIPTOR = 256 * 1024
MAX_FILES = 60000
MAX_BYTES = 8 * 1024 ** 3
KINDS = frozenset(('common', 'cpu', 'dml', 'cuda', 'sdk', 'sam', 'startup', 'bootstrap'))
_leases = {}
_dll_handles = []
_guard = None
_native_kernel = None
_native_handles = []
_audit_installed = False
_policy_files = set()
_policy_directories = set()
_policy_system = set()
_original_spec_probe = importlib.util.find_spec
SYSTEM_LIBRARIES = frozenset(('kernel32.dll', 'ntdll.dll', 'advapi32.dll',
    'crypt32.dll', 'cryptbase.dll', 'bcrypt.dll', 'ncrypt.dll', 'ole32.dll',
    'oleaut32.dll', 'shell32.dll', 'user32.dll', 'gdi32.dll', 'setupapi.dll',
    'ws2_32.dll', 'psapi.dll', 'version.dll', 'wintrust.dll', 'shlwapi.dll',
    'winmm.dll', 'ucrtbase.dll'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def relative(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or value.startswith('/') or len(value) > 1024):
        raise CloudInputError('Invalid Cloud component path.')
    path = PurePosixPath(value)
    if path.as_posix() != value or any(p in ('', '.', '..') or p.endswith(('.', ' '))
            or any(ord(c) < 32 or c in '<>"|?*' for c in p)
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I)
            for p in path.parts):
        raise CloudInputError('Ambiguous Cloud component path.')
    return value


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CloudInputError('Duplicate Cloud inventory field.')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as error:
        raise CloudInputError('Invalid Cloud inventory.') from error


def _identity():
    from product_build import EDITION, CLOUD_ROLE
    if EDITION not in ('free', 'premium') or CLOUD_ROLE not in ('gui', 'cloud-worker'):
        raise CloudInputError('Invalid compiled Cloud role.')
    if not getattr(sys, 'frozen', False):
        raise CloudInputError('Production Cloud requires a packaged build.')
    return EDITION, CLOUD_ROLE


def worker_root():
    _, role = _identity()
    executable = Path(sys.executable).absolute()
    if role == 'gui' and executable.name == 'VaporLockControlCenter.exe':
        return executable.parent / 'cloud'
    if (role == 'cloud-worker' and executable.name == 'VaporLockCloudTools.exe'
            and executable.parent.name == 'cloud'):
        return executable.parent
    raise CloudInputError('The Cloud executable is outside its fixed layout.')


def component_root(kind):
    if kind not in KINDS - {'startup', 'bootstrap'}:
        raise CloudInputError('Unknown production Cloud component.')
    root = worker_root()
    if kind == 'sdk':
        return root / 'cloud_source/4.0.0'
    if kind == 'sam':
        return root / 'assets/cloud-annotation'
    return root.parent.parent / 'runtime/cloud' / ('common-opencv-v2' if kind == 'common' else kind)


class BundleLease:
    """Authenticated sharded inventory, held handles and closed file namespace."""
    def __init__(self, root, expected, kind, *, edition=None):
        if kind not in KINDS or not re.fullmatch('[0-9a-f]{64}', expected or ''):
            raise CloudInputError('Missing authenticated Cloud inventory identity.')
        from cloud_project import ProjectLease
        self.root = Path(root).absolute()
        self.guard = ProjectLease(self.root)
        self.files = set()
        self.hashes = {}
        self.handles = {}
        self.names = {}
        self.read_lock = threading.Lock()
        self.total_bytes = 0
        try:
            self.guard._directory(self.root)
            raw = self._pin('inventory.json', MAX_DESCRIPTOR, expected, bounded=True)
            index = _json(raw)
            if (type(index) is not dict or set(index) != {'schema', 'abi', 'kind', 'edition', 'files', 'bytes', 'shards'}
                    or index['schema'] != 1 or index['abi'] != 'cp312-win-amd64'
                    or index['kind'] != kind or index['edition'] != edition
                    or type(index['files']) is not int or not 1 <= index['files'] <= MAX_FILES
                    or type(index['bytes']) is not int or not 0 <= index['bytes'] <= MAX_BYTES
                    or type(index['shards']) is not list or not 1 <= len(index['shards']) <= 128):
                raise CloudInputError('Cloud inventory is for a different component or build.')
            entries = []
            for number, shard in enumerate(index['shards']):
                if (type(shard) is not dict or set(shard) != {'path', 'bytes', 'sha256'}
                        or shard['path'] != '.inventory/' + str(number).zfill(4) + '.json'
                        or type(shard['bytes']) is not int or not 1 <= shard['bytes'] <= MAX_DESCRIPTOR):
                    raise CloudInputError('Invalid Cloud inventory shard.')
                entries.extend(_json(self._pin(shard['path'], shard['bytes'], shard['sha256'])))
                if len(entries) > MAX_FILES:
                    raise CloudInputError('Cloud inventory exceeds its entry budget.')
            seen = set()
            for entry in entries:
                if type(entry) is not dict or set(entry) != {'path', 'bytes', 'sha256'}:
                    raise CloudInputError('Invalid Cloud component entry.')
                name = relative(entry['path'])
                if name.casefold() in seen or name == 'inventory.json' or name.startswith('.inventory/'):
                    raise CloudInputError('Duplicate Cloud component path.')
                if type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= MAX_BYTES:
                    raise CloudInputError('Invalid Cloud component size.')
                seen.add(name.casefold())
                self.total_bytes += entry['bytes']
                if self.total_bytes > MAX_BYTES:
                    raise CloudInputError('Cloud component exceeds its byte budget.')
                self._pin(name, entry['bytes'], entry['sha256'])
            if len(entries) != index['files'] or self.total_bytes != index['bytes']:
                raise CloudInputError('Cloud component totals do not match.')
            self._closed_namespace()
        except BaseException:
            self.close()
            raise

    def _pin(self, name, size, expected, *, bounded=False):
        from dataset_transaction import HeldFile
        if not re.fullmatch('[0-9a-f]{64}', expected or ''):
            raise CloudInputError('Invalid Cloud component checksum.')
        name = relative(name)
        path = self.root / name
        self.guard._directory(path.parent)
        held = self.guard.stack.enter_context(HeldFile(path, read_only=True))
        info = held.info()
        actual_size = (info.size_high << 32) | info.size_low
        if (actual_size > size if bounded else actual_size != size):
            raise CloudInputError('Cloud component size changed.')
        # Check the final identity from the same handle used for hashing.
        from ctypes import wintypes as W
        function = held.api.GetFinalPathNameByHandleW
        buffer = ctypes.create_unicode_buffer(32768)
        count = function(held.handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer) or _normal(buffer.value) != _normal(path):
            raise CloudInputError('Cloud component path identity changed.')
        checksum = hashlib.sha256()
        saved = []
        for block in held.blocks():
            checksum.update(block)
            if bounded or name.startswith('.inventory/'):
                saved.append(block)
        if checksum.hexdigest() != expected:
            raise CloudInputError('Cloud component integrity check failed.')
        self.files.add(_normal(path))
        self.hashes[_normal(path)] = expected
        self.handles[_normal(path)] = held
        self.names[_normal(path)] = name
        return b''.join(saved)

    def source(self, path):
        name = _normal(path)
        held = self.handles[name]
        info = held.info()
        if info.size_high or info.size_low > 8 * 1024 ** 2:
            raise ImportError('Cloud module source exceeds its bounded size.')
        with self.read_lock:
            raw = b''.join(held.blocks())
        if digest(raw) != self.hashes[name]:
            raise ImportError('Cloud source identity changed.')
        return raw

    def _closed_namespace(self):
        expected_dirs = {_normal(self.root)}
        for name in self.files:
            path = Path(name)
            for parent in path.parents:
                expected_dirs.add(_normal(parent))
                if _normal(parent) == _normal(self.root):
                    break
        for folder, directories, files in os.walk(self.root, followlinks=False):
            for name in directories:
                path = Path(folder) / name
                self.guard._directory(path)
                if _normal(path) not in expected_dirs:
                    raise CloudInputError('An unlisted Cloud component folder was found.')
            for name in files:
                if _normal(Path(folder) / name) not in self.files:
                    raise CloudInputError('An unlisted Cloud component file was found.')

    def close(self):
        self.guard.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@lru_cache(maxsize=65536)
def _absolute_normal(path):
    from dataset_paths import local_path
    return os.path.normcase(str(local_path(path)))


def _normal(path):
    from dataset_paths import local_path
    value = os.fspath(path)
    # Only lexical absolute paths are cached. Relative/drive-relative inputs
    # keep their working-directory-dependent validation on each invocation.
    if os.name == 'nt' and ntpath.splitdrive(value)[0] and ntpath.isabs(value):
        return _absolute_normal(value)
    return os.path.normcase(str(local_path(value)))


class CloudInvocation(list):
    """argv plus the parent-owned startup lease; never serialized to a child."""
    def __init__(self, values, lease):
        super().__init__(values)
        self.cloud_lease = lease


def invocation(*, annotation=False):
    edition, role = _identity()
    if role != 'gui':
        raise CloudInputError('Only the packaged interface launches a Cloud child.')
    from product_build import CLOUD_STARTUP_SHA256
    root = worker_root()
    lease = BundleLease(root, CLOUD_STARTUP_SHA256, 'startup', edition=edition)
    return CloudInvocation([str(root / 'VaporLockCloudTools.exe'),
        '--annotation-gated' if annotation else '--gated'], lease)


def _component_index():
    from product_build import CLOUD_COMPONENTS_SHA256
    from dataset_transaction import HeldFile
    from cloud_project import ProjectLease
    from dataset_paths import local_path
    path = worker_root() / 'components.json'
    with ExitStack() as stack:
        guard = ProjectLease(path)
        stack.callback(guard.stack.close)
        guard._directory(path.parent)
        held = stack.enter_context(HeldFile(path, read_only=True))
        info = held.info()
        if info.size_high or info.size_low > MAX_DESCRIPTOR:
            raise CloudInputError('Cloud component index is too large.')
        raw = b''.join(held.blocks())
    if digest(raw) != CLOUD_COMPONENTS_SHA256:
        raise CloudInputError('Cloud component index failed authentication.')
    value = _json(raw)
    if (type(value) is not dict or set(value) != {'schema', 'abi', 'components'}
            or value['schema'] != 1 or value['abi'] != 'cp312-win-amd64'
            or type(value['components']) is not dict
            or set(value['components']) != KINDS - {'startup', 'bootstrap'}):
        raise CloudInputError('Invalid authenticated Cloud component index.')
    return value['components']


def admit(kind):
    """Worker-only admission before first codec/framework/SDK import."""
    _, role = _identity()
    if role != 'cloud-worker' or kind not in KINDS - {'startup'}:
        raise CloudInputError('Invalid production Cloud admission role.')
    if kind not in _leases:
        if kind == 'bootstrap':
            from product_build import CLOUD_BOOTSTRAP_SHA256
            root = worker_root() / '_internal'
            if _normal(root) != _normal(sys._MEIPASS):
                raise CloudInputError('Cloud bootstrap is outside its compiled layout.')
            expected = CLOUD_BOOTSTRAP_SHA256
        else:
            root = component_root(kind)
            expected = _component_index()[kind]
        _leases[kind] = BundleLease(root, expected, kind)
        _refresh_native_policy()
    return _leases[kind].root


class _SourceOnlyLoader(importlib.abc.Loader):
    """Compile the pinned source handle; never inspect timestamp/size .pyc caches."""
    def __init__(self, original, origin, lease, fullname):
        self.original, self.origin, self.lease, self.fullname = original, origin, lease, fullname

    def create_module(self, spec):
        return None

    def get_source(self, fullname):
        if fullname != self.fullname:
            raise ImportError('Cloud source loader name mismatch.')
        source = importlib.util.decode_source(self.lease.source(self.origin))
        if fullname == 'torch':
            from product_build import CLOUD_TORCH_SOURCE_SHAS
            if digest(self.lease.source(self.origin)) not in CLOUD_TORCH_SOURCE_SHAS:
                raise ImportError('Unsupported Torch bootstrap source identity.')
            tree = ast.parse(source)
            functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                         and node.name == '_load_dll_libraries']
            if len(functions) != 1:
                raise ImportError('Unsupported Torch native bootstrap structure.')
            node = functions[0]
            lines = source.splitlines(keepends=True)
            # The pinned Windows DLL-loader body alone is replaced. Provider
            # tensors, kernels, precision and model equations remain untouched.
            lines[node.body[0].lineno - 1:node.end_lineno] = [
                ' ' * (node.col_offset + 4) + 'return __vaporlock_load_torch_libraries__(__file__)\n']
            source = ''.join(lines)
        return source

    def exec_module(self, module):
        if self.fullname == 'torch':
            module.__dict__['__vaporlock_load_torch_libraries__'] = _load_torch_libraries
        exec(compile(self.get_source(self.fullname), self.origin, 'exec', dont_inherit=True), module.__dict__)

    def get_filename(self, fullname):
        return self.origin

    def get_resource_reader(self, fullname):
        method = getattr(self.original, 'get_resource_reader', None)
        return method(fullname) if method else None


def _refresh_native_policy():
    global _policy_files, _policy_directories
    _policy_files = {name for lease in _leases.values() for name in lease.files
                     if Path(name).suffix in ('.dll', '.pyd')}
    _policy_directories = {_normal(Path(name).parent) for name in _policy_files}


def _native_files():
    return _policy_files


def _native_audit(event, args):
    """Supplementary ctypes/directory checks, not an OS or native-code sandbox."""
    if event == 'os.add_dll_directory':
        if _normal(args[0]) not in _policy_directories:
            raise OSError('Unlisted Cloud native search directory rejected.')
    elif event == 'ctypes.dlopen':
        value = args[0]
        if type(value) is not str:
            raise OSError('Cloud native loads require a declared file identity.')
        if Path(value).is_absolute():
            normalized = _normal(value)
        else:
            # Bare names may only reopen an ALREADY loaded exact module, never
            # initiate a search through filesystem/environment directories.
            handle = _native_kernel.GetModuleHandleW(value)
            buffer = ctypes.create_unicode_buffer(32768)
            if not handle or not _native_kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
                raise OSError('Unresolved Cloud native basename rejected.')
            normalized = _normal(buffer.value)
        if normalized not in _native_files() | _policy_system:
            raise OSError('Unlisted Cloud native load rejected.')


def _configure_native():
    global _native_kernel, _audit_installed, _policy_system
    from ctypes import wintypes as W
    if _native_kernel is None:
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = [W.LPWSTR, W.UINT]
        kernel.GetSystemDirectoryW.restype = W.UINT
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        system = Path(buffer.value)
        _policy_system = {_normal(system / name) for name in SYSTEM_LIBRARIES}
        for name in sorted(SYSTEM_LIBRARIES):
            _native_handles.append(ctypes.WinDLL(str(system / name)))
        _native_kernel = ctypes.WinDLL(str(system / 'kernel32.dll'), use_last_error=True)
        for name, arguments, result in (
                ('GetModuleHandleW', [W.LPCWSTR], W.HANDLE),
                ('GetModuleFileNameW', [W.HANDLE, W.LPWSTR, W.DWORD], W.DWORD),
                ('LoadLibraryExW', [W.LPCWSTR, W.HANDLE, W.DWORD], W.HANDLE)):
            function = getattr(_native_kernel, name)
            function.argtypes, function.restype = arguments, result
    if not _audit_installed:
        sys.addaudithook(_native_audit)
        _audit_installed = True


def _declared_torch_dlls(origin):
    provider = next((lease for kind, lease in _leases.items()
                     if kind in ('cpu', 'cuda') and _normal(origin) in lease.files), None)
    if provider is None or _normal(origin) != _normal(provider.root / 'torch/__init__.py'):
        raise ImportError('Torch is outside its selected authenticated cohort.')
    folder = provider.root / 'torch/lib'
    declared = sorted(name for name in provider.files
        if Path(name).parent == Path(_normal(folder)) and Path(name).suffix == '.dll')
    actual = {_normal(path) for path in folder.glob('*.dll')}
    if not declared or actual != set(declared):
        raise ImportError('The Torch native directory contains an unlisted or missing DLL.')
    # Use the manifest-derived list even if a new file appears after scanning.
    return declared


def _load_torch_libraries(origin):
    """Original closed Windows bootstrap; no userbase/CUDA_PATH/PATH fallback."""
    declared = _declared_torch_dlls(origin)
    bootstrap = _leases['bootstrap'].root
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'):
        selected = bootstrap / name
        if _normal(selected) not in _native_files():
            raise ImportError('The fixed Cloud Visual C++ runtime is missing.')
        handle = _native_kernel.LoadLibraryExW(str(selected), None, 0x1100)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        _native_handles.append(handle)
    for selected in declared:
        handle = _native_kernel.LoadLibraryExW(selected, None, 0x1100)
        if not handle:
            raise OSError(ctypes.get_last_error(), 'A bundled Torch dependency could not initialize.')
        _native_handles.append(handle)


class _AbsentComponent(ModuleNotFoundError):
    """No captured finder found this name; actual imports still terminate."""


def _guarded_spec_probe(name, package=None):
    try:
        return _original_spec_probe(name, package)
    except _AbsentComponent as error:
        # No parent execution occurs for a top-level probe. Dotted/relative,
        # transitive, ordinary missing and unauthorized-present errors survive.
        if type(name) is str and name.isidentifier() and error.name == name:
            return None
        raise


def _install_spec_probe():
    if importlib.util.find_spec is _guarded_spec_probe: return
    if importlib.util.find_spec is not _original_spec_probe:
        raise CloudInputError('Cloud module probing changed before admission.')
    importlib.util.find_spec = _guarded_spec_probe


class _ImportGuard(importlib.abc.MetaPathFinder):
    def __init__(self, finders):
        self.finders = tuple(finders)

    def find_spec(self, fullname, path=None, target=None):
        # The SDK's own captured-byte importer owns these closed namespaces.
        if fullname.split('.')[0] in ('cloud_sdk', 'cloudstudio'):
            raise ImportError('Cloud SDK import attempted before captured-source admission.')
        for finder in self.finders:
            spec = finder.find_spec(fullname, path, target)
            if spec is None:
                continue
            loader_module = type(spec.loader).__module__
            if spec.origin in ('built-in', 'frozen') or loader_module.startswith('pyimod'):
                return spec
            allowed = set().union(*(lease.files for lease in _leases.values()))
            if (isinstance(spec.loader, zipimport.zipimporter)
                    and _normal(spec.loader.archive) in allowed
                    and _normal(spec.loader.archive) == _normal(Path(sys._MEIPASS) / 'base_library.zip')):
                return spec
            if spec.origin and _normal(spec.origin) in allowed:
                if isinstance(spec.loader, importlib.machinery.SourceFileLoader):
                    lease = next(lease for lease in _leases.values() if _normal(spec.origin) in lease.files)
                    spec.loader = _SourceOnlyLoader(spec.loader, spec.origin, lease, fullname)
                return spec
            # No namespace may expand outside an admitted component directory.
            if spec.origin is None and spec.submodule_search_locations:
                for location in spec.submodule_search_locations:
                    if not any(name.startswith(_normal(location) + '\\') for name in allowed):
                        raise ImportError('Unlisted Cloud namespace import rejected.')
                return spec
            raise ImportError('Unlisted Cloud module import rejected.', name=fullname)
        raise _AbsentComponent('No admitted Cloud module named ' + fullname, name=fullname)


def activate(kind):
    global _guard
    if kind not in ('common', 'cpu', 'dml', 'cuda'):
        raise CloudInputError('Unknown Cloud production provider.')
    if kind != 'common' and any(name in _leases for name in ('cpu', 'dml', 'cuda') if name != kind):
        raise CloudInputError('A Cloud worker cannot mix provider cohorts.')
    admit('bootstrap')
    common = admit('common')
    selected = common if kind == 'common' else admit(kind)
    if _guard is None:
        _guard = _ImportGuard(sys.meta_path)
        sys.meta_path.insert(0, _guard)
    _install_spec_probe()
    directories = set()
    basenames = {}
    for lease in _leases.values():
        for filename in lease.files:
            path = Path(filename)
            if path.suffix.lower() in ('.dll', '.pyd'):
                identity = lease.hashes[filename]
                if path.name in basenames and basenames[path.name] != identity:
                    raise CloudInputError('Conflicting Cloud native dependency names.')
                basenames[path.name] = identity
                directories.add(path.parent)
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.SetDefaultDllDirectories.argtypes = [ctypes.c_uint32]
    api.SetDefaultDllDirectories.restype = ctypes.c_int
    if not api.SetDefaultDllDirectories(0x800 | 0x400):
        raise ctypes.WinError(ctypes.get_last_error())
    _configure_native()
    for directory in sorted(directories, key=str):
        _dll_handles.append(os.add_dll_directory(directory))
    sys.path[:0] = [str(selected), str(common)]
    return selected, common
