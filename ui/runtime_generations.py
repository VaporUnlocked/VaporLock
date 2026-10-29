"""Pinned native CUDA generations shared by setup, launch and app updates."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile
import threading
import urllib.request
from urllib.parse import urlsplit
import uuid
import zipfile

from cuda_runtime_lock import ARCHIVES, LOCK_ID, NOTICES

VC_NAMES = ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_1.dll',
            'msvcp140_2.dll', 'msvcp140_codecvt_ids.dll', 'concrt140.dll')
PAYLOAD = {item['target']: item for archive in ARCHIVES for item in archive['payload']}
_status_cache = {}
_status_lock = threading.RLock()
if len(PAYLOAD) != sum(len(item['payload']) for item in ARCHIVES):
    raise RuntimeError('The publisher CUDA lock contains colliding destinations.')


class RuntimeIntegrityError(RuntimeError):
    pass


def plain(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise RuntimeIntegrityError('Runtime files cannot use linked paths.')


def digest(path: Path) -> str:
    plain(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid_payload(path: Path, entry: dict) -> bool:
    try:
        plain(path)
        return path.is_file() and path.stat().st_size == entry['bytes'] and digest(path) == entry['sha256']
    except OSError:
        return False


def require_x64(path: Path) -> None:
    with path.open('rb') as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b'MZ':
            raise RuntimeIntegrityError('The runtime is not a Windows native library.')
        offset = struct.unpack_from('<I', dos, 60)[0]
        if offset > 1024 * 1024:
            raise RuntimeIntegrityError('Invalid runtime PE header.')
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0\x64\x86':
            raise RuntimeIntegrityError('The runtime must be Windows x64.')


def binding_sources(root: Path) -> dict:
    from native_engine_layout import source_engine
    root=Path(root)
    engine=source_engine(root,'cpu') or root/'engine/VaporLock.exe'
    plain(engine)
    files={'engine/VaporLock.exe': engine}
    for name in VC_NAMES:
        candidate=next((directory/name for directory in (engine.parent,root/'app/_internal')
                        if (directory/name).is_file()),None)
        if candidate is not None:
            plain(candidate);require_x64(candidate);files['engine/'+name]=candidate
    return files


def current_binding(root: Path) -> dict:
    # Bind the local built engine/dependencies, never a publisher authorization.
    return {'engine_files': {relative:digest(path) for relative,path in binding_sources(root).items()}, 'lock': LOCK_ID}


def generation_root(root: Path) -> Path:
    return root / 'acceleration/cuda/generations'


def _pointer(root: Path) -> dict:
    path = root / 'acceleration/cuda/active.json'
    plain(path)
    if path.stat().st_size > 8192:
        raise RuntimeIntegrityError('Invalid CUDA runtime pointer.')
    value = json.loads(path.read_text(encoding='utf8'))
    if (type(value) is not dict or set(value) != {'schema', 'generation', 'binding'} or value['schema'] != 1
            or type(value['generation']) is not str or not re.fullmatch('[0-9a-f]{32}', value['generation'])):
        raise RuntimeIntegrityError('Invalid CUDA runtime pointer.')
    return value


@dataclass(frozen=True)
class NativeRuntime:
    executable: Path
    directory: Path
    identity: str

    def environment(self, inherited: dict[str, str]) -> dict[str, str]:
        # Libraries are adjacent to the engine, not resolved from another app's
        # Python/NVIDIA directories or an inherited CUDA_PATH.
        env = {key: value for key, value in inherited.items()
               if not key.upper().startswith(('PYTHON', 'PIP_', 'CUDA_PATH', 'CUDNN_PATH'))}
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        env['PATH'] = os.pathsep.join((str(self.directory), str(windows / 'System32'), str(windows)))
        return env


def resolve_cuda(root: Path) -> NativeRuntime:
    plain(root)
    pointer = _pointer(root)
    binding = current_binding(root)
    if pointer['binding'] != binding:
        raise RuntimeIntegrityError('CUDA runtime must be refreshed for this release.')
    folder = generation_root(root) / pointer['generation']
    plain(folder)
    expected = set(PAYLOAD) | set(binding['engine_files']) | set(NOTICES)
    actual = set()
    for path in folder.rglob('*'):
        plain(path)
        if path.is_file(): actual.add(path.relative_to(folder).as_posix())
    if actual != expected:
        raise RuntimeIntegrityError('CUDA runtime has missing or unexpected files.')
    for relative, entry in PAYLOAD.items():
        if not valid_payload(folder / relative, entry):
            raise RuntimeIntegrityError('CUDA runtime integrity verification failed.')
    for relative, entry in NOTICES.items():
        if not valid_payload(folder / relative, entry): raise RuntimeIntegrityError('CUDA notices are incomplete.')
    for relative, expected_digest in binding['engine_files'].items():
        if digest(folder / relative) != expected_digest:
            raise RuntimeIntegrityError('CUDA runtime contains an obsolete or modified engine.')
    return NativeRuntime(folder / 'engine/VaporLock.exe', folder / 'engine', pointer['generation'])


def cuda_installed(root: Path) -> bool:
    """Cache UI status by file identity; every actual launch verifies again."""
    try:
        pointer = _pointer(root)
        folder = generation_root(root) / pointer['generation']
        files = [root / 'acceleration/cuda/active.json', *binding_sources(root).values()]
        for path in folder.rglob('*'):
            plain(path)
            if path.is_file(): files.append(path)
        state = tuple((str(path), path.stat().st_ino, path.stat().st_size,
                       path.stat().st_mtime_ns, path.stat().st_ctime_ns) for path in files)
        with _status_lock:
            if _status_cache.get(str(root)) == state: return True
        resolve_cuda(root)
        with _status_lock: _status_cache[str(root)] = state
        return True
    except (OSError, ValueError, KeyError, RuntimeIntegrityError):
        return False


def _archive_path(cache: Path, entry: dict, *, download: bool, report) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / entry['filename']
    plain(path)
    if valid_payload(path, entry): return path
    if not download:
        raise RuntimeIntegrityError('A verified CUDA dependency archive is unavailable locally.')
    parsed = urlsplit(entry['url'])
    if parsed.scheme != 'https' or parsed.hostname not in ('files.pythonhosted.org', 'api.nuget.org'):
        raise RuntimeIntegrityError('Unapproved dependency source.')
    report('Downloading verified ' + entry['name'] + ' ' + entry['version'] + '...')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        request = urllib.request.Request(entry['url'], headers={'User-Agent': 'VaporLock-Runtime'})
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open('xb') as output:
            resolved = urlsplit(response.geturl())
            if resolved.scheme != 'https' or resolved.hostname not in ('files.pythonhosted.org', 'api.nuget.org'):
                raise RuntimeIntegrityError('Unapproved dependency redirect.')
            received = 0
            while block := response.read(8 * 1024 * 1024):
                received += len(block)
                if received > entry['bytes']: raise RuntimeIntegrityError('Dependency archive exceeds its pinned size.')
                output.write(block)
        if not valid_payload(temporary, entry):
            raise RuntimeIntegrityError('Dependency archive does not match its publisher hash.')
        temporary.replace(path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def _extract_payload(archive_path: Path, entry: dict, folder: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(name.casefold() for name in names)):
            raise RuntimeIntegrityError('Dependency archive contains duplicate paths.')
        for item in entry['payload']:
            parts = PurePosixPath(item['target']).parts
            if len(parts) != 2 or parts[0] != 'engine' or ':' in parts[1] or '\\' in parts[1]:
                raise RuntimeIntegrityError('Unsafe publisher payload path.')
            info = archive.getinfo(item['member'])
            if info.file_size != item['bytes'] or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeIntegrityError('Invalid dependency archive member.')
            target = folder / item['target']
            target.parent.mkdir(parents=True, exist_ok=True)
            plain(target)
            with archive.open(info) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            if not valid_payload(target, item):
                raise RuntimeIntegrityError('Extracted runtime does not match its publisher hash.')
            require_x64(target)


def _local_payloads(root: Path) -> dict[str, Path]:
    """Untrusted paths are merely candidates; CURRENT publisher hashes decide."""
    candidates = [root / 'engine']
    old = root / 'acceleration/cuda/python/nvidia'
    if old.is_dir():
        plain(old)
        candidates += list(old.glob('*/bin'))
    generations = generation_root(root)
    if generations.is_dir():
        plain(generations)
        candidates += [path / 'engine' for path in generations.iterdir()
                       if re.fullmatch('[0-9a-f]{32}', path.name)]
    result = {}
    for relative, entry in PAYLOAD.items():
        for directory in candidates:
            path = directory / Path(relative).name
            if valid_payload(path, entry):
                result[relative] = path
                break
    return result


def prepare_cuda(root: Path, *, reuse_root: Path | None = None, download=True, report=lambda _message: None) -> NativeRuntime:
    """Caller holds the common app/runtime installation mutex through commit."""
    plain(root)
    binding = current_binding(root)
    source_root = reuse_root or root
    payloads = _local_payloads(source_root)
    identifier = uuid.uuid4().hex
    folder = generation_root(root) / identifier
    plain(folder)
    folder.mkdir(parents=True)
    (folder / 'engine').mkdir()
    try:
        for entry in ARCHIVES:
            missing = [item for item in entry['payload'] if item['target'] not in payloads]
            if missing:
                cache = root / 'acceleration/cuda/cache'
                local_cache = source_root / 'acceleration/cuda/cache' / entry['filename']
                if source_root != root and valid_payload(local_cache, entry):
                    cache.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_cache, cache / entry['filename'])
                path = _archive_path(cache, entry, download=download, report=report)
                _extract_payload(path, entry, folder)
            else:
                for item in entry['payload']:
                    target = folder / item['target']
                    shutil.copy2(payloads[item['target']], target)
                    if not valid_payload(target,item): raise RuntimeIntegrityError('A local dependency changed during reuse.')
                    require_x64(target)
        for relative, source in binding_sources(root).items():
            shutil.copy2(source, folder / relative)
        for relative, entry in NOTICES.items():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(entry['data'], validate=True))
            if not valid_payload(target, entry): raise RuntimeIntegrityError('Invalid bundled runtime notice.')
        if current_binding(root) != binding: raise RuntimeIntegrityError('The application changed during runtime preparation.')
        for relative, expected_digest in binding['engine_files'].items():
            if digest(folder / relative) != expected_digest: raise RuntimeIntegrityError('The copied engine changed during preparation.')
        pointer = root / 'acceleration/cuda/active.json'
        temporary = pointer.with_name('active.' + identifier + '.tmp')
        temporary.write_text(json.dumps({'schema':1,'generation':identifier,'binding':binding},sort_keys=True),encoding='utf8')
        temporary.replace(pointer)
        return NativeRuntime(folder / 'engine/VaporLock.exe', folder / 'engine', identifier)
    except BaseException:
        # Only this fresh generation is ours. Older running generations and the
        # prior active pointer are retained on all failures before publication.
        if folder.resolve().parent != generation_root(root).resolve(): raise
        shutil.rmtree(folder, ignore_errors=True)
        raise
