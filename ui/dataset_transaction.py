"""Handle-protected Windows dataset commits and bounded crash recovery.

Original bytes stay in the same dataset until a durable commit marker exists.
Every rename is exclusive and applies to an open file identity, not a path lookup.
"""
from contextlib import ExitStack
from functools import lru_cache
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

JOURNAL = '.vaporlock-annotation-transaction.json'
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
MAX_FILE = 256 * 1024 * 1024
MAX_JOURNAL = 32 * 1024 * 1024
MAX_ENTRIES = 100000
JOURNAL_RETRY_SECONDS = 2.0
JOURNAL_RETRY_ATTEMPTS = 41
_retained_closes = []


class RecoveryConflict(OSError):
    pass


class AnnotationCommitUncertain(OSError):
    """A publication outcome needs fresh recovery; do not claim task rollback."""
    def __init__(self):
        super().__init__('Annotation commit outcome is uncertain. Files and backups were retained; recovery is required before the next dataset task.')


def _journal_acquire(operation, *, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + JOURNAL_RETRY_SECONDS
    error = None
    for attempt in range(JOURNAL_RETRY_ATTEMPTS):
        if attempt and time.monotonic() >= deadline:
            raise error
        try:
            return operation()
        except OSError as current:
            error = current
            remaining = deadline - time.monotonic()
            if getattr(current, 'winerror', None) not in (5, 32, 33) or remaining <= 0:
                raise
            time.sleep(min(.05, remaining))
    raise error


class _Info(ctypes.Structure):
    _fields_ = [('attributes', W.DWORD), ('created', W.FILETIME), ('accessed', W.FILETIME),
                ('written', W.FILETIME), ('volume', W.DWORD), ('size_high', W.DWORD),
                ('size_low', W.DWORD), ('links', W.DWORD), ('index_high', W.DWORD), ('index_low', W.DWORD)]


@lru_cache(maxsize=1)
def _api():
    # Reuse only the fully configured native binding object. Each acquisition
    # still opens/checks its own file handle; no path or authority result is cached.
    # use_last_error keeps Win32 errors private to each calling thread.
    if os.name != 'nt':
        raise OSError('Recoverable image removal requires Windows file identity protection.')
    dll = ctypes.WinDLL('kernel32', use_last_error=True)
    dll.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
    dll.CreateFileW.restype = W.HANDLE
    dll.GetFileInformationByHandle.argtypes = [W.HANDLE, ctypes.POINTER(_Info)]
    dll.GetFileInformationByHandle.restype = W.BOOL
    dll.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
    dll.GetFinalPathNameByHandleW.restype = W.DWORD
    dll.SetFileInformationByHandle.argtypes = [W.HANDLE, ctypes.c_int, W.LPVOID, W.DWORD]
    dll.SetFileInformationByHandle.restype = W.BOOL
    dll.CloseHandle.argtypes = [W.HANDLE]
    dll.CloseHandle.restype = W.BOOL
    dll.SetFilePointerEx.argtypes = [W.HANDLE, ctypes.c_longlong, W.LPVOID, W.DWORD]
    dll.SetFilePointerEx.restype = W.BOOL
    dll.ReadFile.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID]
    dll.ReadFile.restype = W.BOOL
    dll.WriteFile.argtypes = [W.HANDLE, W.LPVOID, W.DWORD, ctypes.POINTER(W.DWORD), W.LPVOID]
    dll.WriteFile.restype = W.BOOL
    dll.FlushFileBuffers.argtypes = [W.HANDLE]
    dll.FlushFileBuffers.restype = W.BOOL
    dll.SetEndOfFile.argtypes = [W.HANDLE]
    dll.SetEndOfFile.restype = W.BOOL
    return dll


class HeldFile:
    def __init__(self, path, *, directory=False, journal=False, create=False, deadline=None, read_only=False):
        from dataset_paths import local_path
        self.path = local_path(path)
        self.api = _api()
        if journal or create:
            if directory or not journal or self.path.name != JOURNAL:
                raise ValueError('Journal acquisition requires an exact journal file.')
        self.journal = journal
        self.created_journal = create
        if read_only and (directory or journal or create):
            raise ValueError('Read-only protection requires an existing regular file.')
        access = 0x80 if directory else 0x80000000 | (0 if read_only else 0x10000)
        if journal:
            access |= 0x40000000  # same journal identity also permits appending markers
        share = 3 if directory else 1  # directories deny replacement; files deny writes/replacement
        def acquire():
            handle = self.api.CreateFileW(str(self.path), access, share, None, 1 if create else 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                error = ctypes.WinError(ctypes.get_last_error())
                error._journal_acquisition_failed = True
                raise error
            return handle
        if journal:
            self.handle = _journal_acquire(acquire, deadline=deadline)
        else:
            self.handle = acquire()
        try:
            info = self.info()
            if bool(info.attributes & 0x10) != directory or info.attributes & 0x400:
                raise ValueError('Dataset paths must not contain reparse points.')
            if not directory and info.links != 1:
                raise ValueError('Dataset transaction files must not be hard linked.')
        except BaseException:
            self.close()
            raise

    def info(self):
        info = _Info()
        if not self.api.GetFileInformationByHandle(self.handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info

    def blocks(self):
        if not self.api.SetFilePointerEx(self.handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        info = self.info()
        capacity = min(1024 * 1024, max(1, (info.size_high << 32) | info.size_low))
        buffer = ctypes.create_string_buffer(capacity)
        count = W.DWORD()
        while True:
            if not self.api.ReadFile(self.handle, buffer, capacity, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                return
            if count.value > capacity:
                raise OSError('Native file read exceeded its requested block size.')
            # Initial size chooses a buffer, never the EOF boundary. Continue
            # reading to real EOF, copying only bytes returned by ReadFile.
            yield ctypes.string_at(buffer, count.value)

    def snapshot(self):
        info = self.info()
        size = (info.size_high << 32) | info.size_low
        if size > MAX_FILE:
            raise ValueError('Dataset transaction file exceeds the supported size.')
        digest = hashlib.sha256()
        for block in self.blocks():
            digest.update(block)
        return {'volume': info.volume, 'index': (info.index_high << 32) | info.index_low,
                'size': size, 'written': (info.written.dwHighDateTime << 32) | info.written.dwLowDateTime,
                'sha256': digest.hexdigest()}

    def rename(self, destination):
        destination = Path(destination)
        name = str(destination).encode('utf-16-le')
        class Rename(ctypes.Structure):
            _fields_ = [('replace', W.BOOL), ('root', W.HANDLE), ('length', W.DWORD),
                        ('name', W.WCHAR * (len(name) // 2 + 1))]
        request = Rename()
        request.replace, request.root, request.length = False, None, len(name)
        ctypes.memmove(ctypes.addressof(request) + Rename.name.offset, name, len(name))
        if not self.api.SetFileInformationByHandle(self.handle, 3, ctypes.byref(request), ctypes.sizeof(request)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.path = destination

    def journal_bytes(self):
        if not self.journal:
            raise ValueError('An annotation journal handle is required.')
        info = self.info()
        if info.size_high or info.size_low > MAX_JOURNAL:
            raise ValueError('Annotation recovery journal exceeds size limit.')
        return b''.join(self.blocks())

    def _append(self, data):
        info = self.info()
        if not self.journal or info.size_high or info.size_low + len(data) > MAX_JOURNAL:
            raise ValueError('Annotation journal append exceeds size limit.')
        boundary = info.size_low
        if not self.api.SetFilePointerEx(self.handle, 0, None, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(data)
        written = W.DWORD()
        try:
            if not self.api.WriteFile(self.handle, buffer, len(data), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value != len(data):
                raise OSError('Annotation journal marker write was incomplete.')
            if not self.api.FlushFileBuffers(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError as error:
            # A visible but unflushed marker cannot authorize cleanup in this
            # invocation. Restore the last boundary through the SAME identity.
            error._journal_write_failed = True
            if data.startswith(b'\nD:'):
                # D is emitted only after C was flushed successfully. A full
                # D proves that C flushed, but D may still be unflushed. Report
                # publication honestly; recovery must flush before deletion.
                if written.value == len(data):
                    error._journal_commit_published = True
                    raise
                try:
                    if _journal_layout(self.journal_bytes())[0].get('state') == 'committed':
                        error._journal_commit_published = True
                        raise error
                except OSError as inspection_error:
                    if inspection_error is error:
                        raise
                    error._journal_outcome_uncertain = True
                    error._journal_confirmation_uncertain = True
            try:
                self._truncate(boundary)
            except OSError:
                error._journal_outcome_uncertain = True
            raise

    def _truncate(self, size):
        if not self.journal or not 0 <= size <= MAX_JOURNAL:
            raise ValueError('Invalid annotation journal boundary.')
        if not self.api.SetFilePointerEx(self.handle, size, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.SetEndOfFile(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.FlushFileBuffers(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def flush_journal(self):
        if not self.journal:
            raise ValueError('An annotation journal handle is required.')
        if not self.api.FlushFileBuffers(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def start_journal(self, payload):
        if not self.created_journal or self.info().size_low or self.info().size_high:
            raise ValueError('A new empty annotation journal is required.')
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') + b'\n'
        if len(data) > MAX_JOURNAL - 1024:
            raise ValueError('Annotation transaction exceeds journal size limit.')
        self._append(data)

    def journal_state(self, state):
        raw = self.journal_bytes()
        payload, header_digest, boundary = _journal_layout(raw, include_boundary=True)
        current = payload.get('state')
        if (current, state) not in (('pending', 'prepared'), ('prepared', 'committed'),
                ('pending', 'recovered'), ('prepared', 'recovered'), ('committed', 'recovered')):
            raise ValueError('Invalid annotation journal state transition.')
        if boundary is not None:
            self._truncate(boundary)
        # Fixed, header-bound frames distinguish a complete marker from a torn
        # write. A new separator abandons an earlier partial frame safely.
        marker = b'\n' + {'prepared': b'C:', 'committed': b'D:', 'recovered': b'R:'}[state] + header_digest.encode('ascii') + b';'
        self._append(marker)

    def delete(self):
        # FileDispositionInfo marks only this held identity for deletion on close.
        disposition = ctypes.c_ubyte(1)
        if not self.api.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            if not self.api.CloseHandle(self.handle):
                if self not in _retained_closes:
                    _retained_closes.append(self)
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _absolute_root(root):
    from dataset_paths import local_path
    return local_path(root)  # rejects UNC/devices; never resolves away reparse points


def _hold_directories(stack, root, paths):
    directories = set(root.parents) | {root}
    for path in paths:
        directories.update(parent for parent in path.parents if parent == root or parent.is_relative_to(root))
    for path in sorted(directories, key=lambda p: len(p.parts)):
        stack.enter_context(HeldFile(path, directory=True))


def snapshot_file(path):
    """Capture identity and bytes before inference; commit rechecks both."""
    from dataset_paths import local_path
    path = local_path(path)
    with ExitStack() as stack:
        for parent in reversed(path.parents):
            stack.enter_context(HeldFile(parent, directory=True))
        return stack.enter_context(HeldFile(path)).snapshot()


def snapshot_bytes(path, *, maximum=MAX_FILE):
    """Inference/protection must consume these exact immutable snapshot bytes."""
    from dataset_paths import local_path
    path = local_path(path)
    with ExitStack() as stack:
        for parent in reversed(path.parents):
            stack.enter_context(HeldFile(parent, directory=True))
        held = stack.enter_context(HeldFile(path))
        snapshot = held.snapshot()
        if snapshot['size'] > maximum:
            raise ValueError('Annotation input exceeds the supported size.')
        data = b''.join(held.blocks())
        if hashlib.sha256(data).hexdigest() != snapshot['sha256']:
            raise RecoveryConflict('Annotation input changed while being read.')
        return snapshot, data


def _relative(root, name, kind):
    if (type(name) is not str or len(name) > 1024 or '\\' in name or ':' in name
            or any(ord(c) < 32 or c in '<>"|?*' for c in name)
            or name.startswith('/') or any(p in ('', '.', '..') or p[-1:] in ('.', ' ') for p in name.split('/'))):
        raise ValueError('Invalid dataset transaction path.')
    path = root / name
    parts = name.split('/')
    if kind in ('image', 'image-replacement'):
        valid = len(parts) == 2 and parts[0] == 'images' and path.suffix.lower() in IMAGE_EXTENSIONS
    else:
        valid = name in ('classes.txt', '.vaporlock-review.json') or (len(parts) == 2 and parts[0] == 'labels' and path.suffix == '.txt')
    if not valid or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', path.name, re.I):
        raise ValueError('Invalid dataset transaction target.')
    return path


def _identity(value):
    if (type(value) is not dict or set(value) != {'volume', 'index', 'size', 'written', 'sha256'}
            or any(type(value[k]) is not int or not 0 <= value[k] < 2**64 for k in ('volume', 'index', 'size', 'written'))
            or value['size'] > MAX_FILE or type(value['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', value['sha256'])):
        raise ValueError('Invalid transaction identity.')


def _validate(root, payload):
    if (type(payload) is not dict or set(payload) != {'schema', 'state', 'id', 'entries'} or payload['schema'] != 2
            or payload['state'] not in ('pending', 'committed', 'recovered') or type(payload['id']) is not str
            or not re.fullmatch('[0-9a-f]{32}', payload['id']) or type(payload['entries']) is not list
            or not 1 <= len(payload['entries']) <= MAX_ENTRIES):
        raise ValueError('Invalid annotation recovery journal. Original files were preserved.')
    seen = set()
    for index, entry in enumerate(payload['entries']):
        expected = {'path', 'kind', 'before', 'after', 'drop'}
        if type(entry) is dict and entry.get('kind') == 'image-replacement':
            expected |= {'label', 'label_snapshot'}
        if (type(entry) is not dict or set(entry) != expected
                or entry['kind'] not in ('image', 'image-replacement', 'metadata') or type(entry['drop']) is not bool):
            raise ValueError('Invalid recovery entry.')
        _relative(root, entry['path'], entry['kind'])
        if entry['path'].casefold() in seen:
            raise ValueError('Duplicate recovery target.')
        seen.add(entry['path'].casefold())
        for key in ('before', 'after'):
            if entry[key] is not None:
                _identity(entry[key])
        if entry['kind'] == 'image' and (entry['before'] is None or entry['after'] is not None or not entry['drop']):
            raise ValueError('Invalid image removal entry.')
        if entry['kind'] == 'image-replacement':
            if (entry['before'] is None or entry['after'] is None or entry['drop']
                    or entry['label'] != 'labels/' + Path(entry['path']).stem + '.txt'):
                raise ValueError('Invalid binary image replacement entry.')
            _relative(root, entry['label'], 'metadata')
            _identity(entry['label_snapshot'])
        if entry['drop'] and entry['kind'] == 'metadata':
            if not entry['path'].startswith('labels/') or entry['after'] is None or entry['after']['sha256'] != hashlib.sha256(b'').hexdigest():
                raise ValueError('Only an empty claimed image label may be removed.')
        if not entry['drop'] and (entry['kind'] not in ('metadata', 'image-replacement') or entry['after'] is None):
            raise ValueError('Invalid metadata replacement state.')
    images = {Path(e['path']).stem.casefold() for e in payload['entries'] if e['kind'] == 'image'}
    labels = {Path(e['path']).stem.casefold() for e in payload['entries'] if e['kind'] == 'metadata' and e['drop']}
    if images != labels:
        raise ValueError('Image removal requires exact matching label claims.')
    return root / ('.vaporlock-annotation-' + payload['id'])


def _held_if_exists(stack, path):
    try:
        return stack.enter_context(HeldFile(path))
    except OSError as error:
        if getattr(error, 'winerror', None) in (2, 3):
            return None
        raise


def _matches(held, expected):
    return held is not None and held.snapshot() == expected


def _journal_layout(raw, *, include_boundary=False):
    text = raw.decode('utf-8')
    start = len(text) - len(text.lstrip())
    payload, end = json.JSONDecoder().raw_decode(text, start)
    if type(payload) is not dict:
        raise ValueError('Invalid annotation recovery journal.')
    header_digest = hashlib.sha256(text[:end].encode('utf-8')).hexdigest()
    remainder = text[end:]
    if remainder and not remainder[0].isspace():
        raise ValueError('Invalid annotation journal marker separator.')
    if len(remainder.encode('utf-8')) > 1024:
        raise ValueError('Annotation journal marker history exceeds its bound.')
    lines = remainder.splitlines(keepends=True)
    records = [line.rstrip('\r\n') for line in lines if line.rstrip('\r\n') and not line.isspace()]
    if len(records) > 16:
        raise ValueError('Annotation journal marker history exceeds its bound.')
    frames = {'C:' + header_digest + ';': 'prepared', 'D:' + header_digest + ';': 'committed',
              'R:' + header_digest + ';': 'recovered'}
    state = payload.get('state')
    offset = len(text[:end].encode('utf-8'))
    boundary = None
    for line in lines:
        record = line.rstrip('\r\n')
        if not record or record.isspace():
            offset += len(line.encode('utf-8'))
            continue
        if boundary is not None:
            raise ValueError('Annotation journal has records after a torn marker.')
        if record in frames:
            following = frames[record]
            if (state, following) not in (('pending', 'prepared'), ('prepared', 'committed'),
                    ('pending', 'recovered'), ('prepared', 'recovered'), ('committed', 'recovered')):
                raise ValueError('Invalid annotation journal marker order.')
            state = following
        else:
            allowed = ({'prepared', 'recovered'} if state == 'pending' else {'committed', 'recovered'}
                       if state == 'prepared' else {'recovered'} if state == 'committed' else set())
            if not any(following in allowed and frame.startswith(record) and len(record) < len(frame)
                       for frame, following in frames.items()):
                raise ValueError('Invalid annotation journal marker. Files were retained.')
            boundary = offset
        offset += len(line.encode('utf-8'))
    payload['state'] = state
    return (payload, header_digest, boundary) if include_boundary else (payload, header_digest)


def _parse_journal(raw):
    payload = _journal_layout(raw)[0]
    if payload.get('state') == 'prepared':
        payload['state'] = 'pending'  # C alone never authorizes image deletion.
    return payload


def _journal_handle(path, *, create=False):
    return HeldFile(path, journal=True, create=create)


def recover_transaction(root, *, checkpoint=lambda _stage: None):
    root = _absolute_root(root)
    if any(item.handle is not None and (item.path == root or item.path.is_relative_to(root))
           for item in _retained_closes):
        raise RecoveryConflict('An annotation file handle is awaiting release. Close Vapor Lock before retrying recovery.')
    journal = root / JOURNAL
    if not journal.exists():
        return
    with ExitStack() as roots:
        _hold_directories(roots, root, [journal])
        try:
            handle = _journal_handle(journal)
        except OSError as error:
            if getattr(error, '_journal_acquisition_failed', False) and getattr(error, 'winerror', None) in (2, 3):
                return  # A prior delete completed while its last reader closed.
            raise
        with handle:
            payload = _parse_journal(handle.journal_bytes())
            if payload == {'state': 'committed'}:
                handle.delete()
                return
            quarantine = _validate(root, payload)
            # A complete D proves that C flushed successfully. Before deleting
            # backups in THIS invocation, make the full observed state durable.
            handle.flush_journal()
            if payload['state'] == 'recovered':
                if quarantine.exists():
                    with HeldFile(quarantine, directory=True):
                        if any(quarantine.iterdir()):
                            raise RecoveryConflict('Recovered quarantine contains unexpected files. They were retained.')
                    quarantine.rmdir()
                checkpoint('quarantine-removed')
                checkpoint('journal-deleting')
                handle.delete()
                return
            if not quarantine.is_dir():
                raise RecoveryConflict('Annotation recovery bytes are missing. No files were overwritten.')
            targets = [_relative(root, e['path'], e['kind']) for e in payload['entries']]
            with ExitStack() as stack:
                label_targets = [root / e['label'] for e in payload['entries'] if e['kind'] == 'image-replacement']
                _hold_directories(stack, root, [quarantine / 'placeholder', *targets, *label_targets])
                # Inspect every entry before any recovery mutation, including conflicts.
                held = []
                for index, (entry, target) in enumerate(zip(payload['entries'], targets)):
                    if entry['kind'] == 'image-replacement':
                        label = _held_if_exists(stack, root / entry['label'])
                        if not _matches(label, entry['label_snapshot']):
                            raise RecoveryConflict('Accepted labels changed during image recovery. Recoverable bytes were retained.')
                    current = _held_if_exists(stack, target)
                    old = _held_if_exists(stack, quarantine / f'{index}.old')
                    new = _held_if_exists(stack, quarantine / f'{index}.new')
                    if old is not None and not _matches(old, entry['before']):
                        raise RecoveryConflict('Recovery backup changed; bytes were preserved.')
                    if new is not None and not _matches(new, entry['after']):
                        raise RecoveryConflict('Recovery staged file changed; bytes were preserved.')
                    if entry['drop'] and entry['kind'] == 'metadata' and entry['before'] is not None:
                        protected = old if old is not None else current if _matches(current, entry['before']) else None
                        if protected is not None and b''.join(protected.blocks()).decode('utf-8').strip():
                            raise RecoveryConflict('A protected label conflicts with removal recovery.')
                    if payload['state'] == 'pending':
                        if current is not None and not (_matches(current, entry['before']) or _matches(current, entry['after'])):
                            raise RecoveryConflict('A replacement conflicts with recovery. Recoverable bytes were retained.')
                        if entry['before'] is not None and old is None and not _matches(current, entry['before']):
                            raise RecoveryConflict('Original recovery bytes are missing.')
                        if old is not None and current is not None and not _matches(current, entry['after']):
                            raise RecoveryConflict('Recovery would overwrite an original or replacement.')
                    held.append((entry, target, current, old, new))
                for entry, target, current, old, new in reversed(held):
                    if payload['state'] == 'pending':
                        if _matches(current, entry['after']):
                            current.delete()
                            current.close()
                        if old is not None:
                            old.rename(target)
                    elif old is not None:
                        old.delete()
                        old.close()
                    if payload['state'] == 'committed' and entry['drop'] and _matches(current, entry['after']):
                        current.delete()
                        current.close()
                    if new is not None:
                        new.delete()
                        new.close()
                    checkpoint('recovery-entry')
                handle.journal_state('recovered')
                checkpoint('recovered-marker')
            # Data/quarantine handles close first; the same journal identity
            # stays protected through empty-directory cleanup and deletion.
            quarantine.rmdir()
            checkpoint('quarantine-removed')
            checkpoint('journal-deleting')
            handle.delete()


def _producer_snapshot(held,expected_sha256,expected_size):
    snapshot=held.snapshot()
    if snapshot['sha256']!=expected_sha256 or snapshot['size']!=expected_size:
        raise RecoveryConflict('Annotation staging changed before publication. Original data was preserved.')
    return snapshot


def commit_transaction(root, updates, removals=(), *, replacements=(), expected_updates=None, checkpoint=lambda _stage: None):
    """Call under dataset_lock. Removals contain exact image and label snapshots."""
    root = _absolute_root(root)
    from dataset_paths import local_path
    normalised={}
    for path,text in updates.items():
        path=local_path(path)
        if path in normalised:raise ValueError('Duplicate annotation update alias.')
        normalised[path]=text
    updates=normalised
    expected_updates = {local_path(p): value for p,value in (expected_updates or {}).items()}
    if not set(expected_updates).issubset(updates):
        raise ValueError('Expected metadata must be part of the transaction.')
    removals=[{**r,'image':local_path(r['image']),'label':local_path(r['label'])} for r in removals]
    replacements=[{**r,**{key:local_path(r[key]) for key in ('image','label','replacement')}} for r in replacements]
    recover_transaction(root)
    if len(updates) + 2 * len(removals) + len(replacements) > MAX_ENTRIES:
        raise ValueError('Annotation transaction exceeds the supported entry count.')
    if replacements and (updates or removals):
        raise ValueError('Binary effects replace images in a separate accepted-label transaction.')
    identity = uuid.uuid4().hex
    quarantine = root / ('.vaporlock-annotation-' + identity)
    journal = root / JOURNAL
    entries = []
    durable = False
    committed = False
    staged_paths = []
    journal_handle = None
    with ExitStack() as stack:
        paths = list(updates) + [Path(r['image']) for r in removals] + [Path(r['label']) for r in removals]
        paths += [Path(r[key]) for r in replacements for key in ('image', 'label', 'replacement')]
        _hold_directories(stack, root, paths)
        quarantine.mkdir(exist_ok=False)
        stack.enter_context(HeldFile(quarantine, directory=True))
        try:
            sources = []
            for path, text in updates.items():
                relative = path.relative_to(root).as_posix()
                _relative(root, relative, 'metadata')
                original = _held_if_exists(stack, path)
                if path in expected_updates:
                    expected = expected_updates[path]
                    if (expected is None and original is not None) or (expected is not None and not _matches(original, expected)):
                        raise RecoveryConflict('Dataset metadata changed. Refresh before saving; existing files were preserved.')
                entry = {'path': relative, 'kind': 'metadata', 'before': original.snapshot() if original else None, 'after': None, 'drop': False}
                staged = quarantine / f'{len(entries)}.new'
                staged_paths.append(staged)
                encoded=text.encode('utf-8');expected_hash=hashlib.sha256(encoded).hexdigest()
                with staged.open('xb') as stream:
                    if stream.write(encoded)!=len(encoded):raise OSError('Annotation metadata staging was incomplete.')
                    stream.flush()
                    os.fsync(stream.fileno())
                fresh = stack.enter_context(HeldFile(staged))
                entry['after'] = _producer_snapshot(fresh,expected_hash,len(encoded))
                entries.append(entry)
                sources.append((original, fresh, path))
            for replacement in replacements:
                path = Path(replacement['image'])
                relative = path.relative_to(root).as_posix()
                _relative(root, relative, 'image-replacement')
                label_path = Path(replacement['label'])
                label_relative = label_path.relative_to(root).as_posix()
                if label_relative != 'labels/' + path.stem + '.txt':
                    raise ValueError('Image replacement needs its accepted label.')
                original = _held_if_exists(stack, path)
                label = _held_if_exists(stack, label_path)
                if not _matches(original, replacement['image_snapshot']) or not _matches(label, replacement['label_snapshot']):
                    raise RecoveryConflict('An image or accepted label changed before applying effects.')
                if not b''.join(label.blocks()).strip():
                    raise RecoveryConflict('Unannotated images must not receive effects.')
                input_path = Path(replacement['replacement'])
                relative_input = input_path.relative_to(root)
                if (len(relative_input.parts) != 3 or not re.fullmatch(r'vaporlock-effects-[0-9a-f]{32}', relative_input.parts[0])
                        or relative_input.parts[1] != 'images' or input_path.suffix.lower() != path.suffix.lower()):
                    raise ValueError('Invalid effects staging path.')
                input_file = _held_if_exists(stack, input_path)
                if not _matches(input_file, replacement['replacement_snapshot']):
                    raise RecoveryConflict('Encoded effects changed before applying.')
                staged = quarantine / f'{len(entries)}.new'
                staged_paths.append(staged)
                with staged.open('xb') as stream:
                    for block in input_file.blocks():
                        if stream.write(block) != len(block):
                            raise OSError('Binary image staging was incomplete.')
                    stream.flush()
                    os.fsync(stream.fileno())
                fresh = stack.enter_context(HeldFile(staged))
                verified_after=_producer_snapshot(fresh,replacement['replacement_snapshot']['sha256'],replacement['replacement_snapshot']['size'])
                entries.append({'path': relative, 'kind': 'image-replacement', 'before': original.snapshot(),
                    'after': verified_after, 'drop': False, 'label': label_relative, 'label_snapshot': label.snapshot()})
                sources.append((original, fresh, path))
            for removal in removals:
                # Claim the empty label slot before moving its image. An open
                # exclusive-write claim blocks concurrently added human labels.
                for key, kind in (('label', 'metadata'), ('image', 'image')):
                    path = Path(removal[key])
                    relative = path.relative_to(root).as_posix()
                    _relative(root, relative, kind)
                    original = _held_if_exists(stack, path)
                    expected = removal[key + '_snapshot']
                    if (expected is None and original is not None) or (expected is not None and not _matches(original, expected)):
                        raise RecoveryConflict('An image or label changed during annotation. Dataset was preserved.')
                    if key == 'label' and original is not None and b''.join(original.blocks()).decode('utf-8').strip():
                        raise RecoveryConflict('Existing nonempty labels protect their source images.')
                    fresh = None
                    after = None
                    if key == 'label':
                        staged = quarantine / f'{len(entries)}.new'
                        staged_paths.append(staged)
                        with staged.open('xb') as stream:
                            stream.flush()
                            os.fsync(stream.fileno())
                        fresh = stack.enter_context(HeldFile(staged))
                        after = _producer_snapshot(fresh,hashlib.sha256(b'').hexdigest(),0)
                    entries.append({'path': relative, 'kind': kind, 'before': expected, 'after': after, 'drop': True})
                    sources.append((original, fresh, path))
            payload = {'schema': 2, 'state': 'pending', 'id': identity, 'entries': entries}
            _validate(root, payload)
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            if len(raw) > MAX_JOURNAL - 1024:
                raise ValueError('Annotation transaction exceeds journal size limit.')
            journal_handle = stack.enter_context(_journal_handle(journal, create=True))
            checkpoint('journal-created')
            journal_handle.start_journal(payload)
            durable = True
            checkpoint('journal')
            for index, (original, fresh, path) in enumerate(sources):
                if original is not None:
                    original.rename(quarantine / f'{index}.old')
                checkpoint(f'original:{index}')
                if fresh is not None:
                    fresh.rename(path)
                checkpoint(f'replacement:{index}')
            journal_handle.journal_state('prepared')
            checkpoint('prepared')
            journal_handle.journal_state('committed')
            committed = True
            checkpoint('committed')
            # Status is recorded before closing a published journal identity.
            stack.close()
        except BaseException as error:
            committed = committed or bool(getattr(error, '_journal_commit_published', False))
            if committed:
                try:
                    stack.close()
                except OSError:
                    pass  # Failed handles retain their owner; publication is known.
                if isinstance(error, OSError):
                    return {'removed': len(removals), 'cleanup_pending': True}
                raise
            if not durable:
                # No originals have moved. Delete only our held identities;
                # incomplete, unclaimed staging bytes remain for inspection.
                for _original, fresh, _path in sources:
                    if fresh is not None and fresh.handle is not None:
                        fresh.delete()
                if journal_handle is not None and journal_handle.handle is not None:
                    journal_handle.delete()
            # ExitStack releases file handles before recovery reopens them.
            try:
                stack.close()
            except OSError:
                if getattr(error, '_journal_confirmation_uncertain', False):
                    raise AnnotationCommitUncertain() from error
                raise
            if getattr(error, '_journal_confirmation_uncertain', False):
                raise AnnotationCommitUncertain() from error
            if durable and not getattr(error, '_journal_write_failed', False):
                recover_transaction(root)
            elif not durable:
                if not any(quarantine.iterdir()):
                    quarantine.rmdir()
            raise
    try:
        checkpoint('cleanup')
        recover_transaction(root)
        cleanup_pending = False
    except OSError:
        if not committed:
            raise
        cleanup_pending = True
    return {'removed': len(removals), 'cleanup_pending': cleanup_pending}
