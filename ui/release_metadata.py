"""One coherent production metadata layout, including retained legacy releases."""
import hashlib
import json
from pathlib import Path
import stat

NAMES = ('RELEASE_MANIFEST.json', 'RELEASE_CHECKSUMS.sha256', 'VERIFIED_RELEASE.json')
MAX_METADATA_BYTES = 2 * 1024 * 1024
LIMITS = {'RELEASE_MANIFEST.json': 16 * 1024 * 1024,
          'RELEASE_CHECKSUMS.sha256': 8 * 1024 * 1024,
          'VERIFIED_RELEASE.json': MAX_METADATA_BYTES}
OWNED_PATHS = frozenset(name.casefold() for name in NAMES) | frozenset(
    'information/' + name.casefold() for name in NAMES)


class MetadataError(ValueError):
    pass


def _stat(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise MetadataError('Release metadata contains a linked path.')
    return info


def _entries(root):
    root = Path(root)
    # Do not allow metadata admission through a linked installation ancestor.
    for part in (root, *root.parents):
        info = _stat(part)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise MetadataError('The release metadata root is not a folder.')
    folder = root / 'information'
    info = _stat(folder)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise MetadataError('The information path is not a folder.')
    rows = []
    for parent in (root, folder):
        if parent.is_dir():
            aliases = {}
            for child in parent.iterdir():
                folded = child.name.casefold()
                if folded in {name.casefold() for name in NAMES}:
                    if folded in aliases:
                        raise MetadataError('Release metadata contains duplicate names.')
                    aliases[folded] = child.name
                    if child.name not in NAMES:
                        raise MetadataError('Release metadata uses an ambiguous filename.')
        for name in NAMES:
            path = parent / name
            info = _stat(path)
            if info is not None and not stat.S_ISREG(info.st_mode):
                raise MetadataError('Release metadata is not a regular file.')
            rows.append((path, info))
    return rows


def directory(root):
    rows = _entries(root)
    legacy = any(info is not None for _, info in rows[:3])
    current = any(info is not None for _, info in rows[3:])
    if legacy and current:
        raise MetadataError('Conflicting release metadata layouts. No installation files were replaced.')
    return Path(root) / 'information' if current else Path(root)


def path(root, name):
    if name not in NAMES:
        raise MetadataError('Unknown release metadata name.')
    return directory(root) / name


def read(root, name):
    selected = path(root, name)
    limit = LIMITS[name]
    with selected.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise MetadataError('The installed release metadata is too large.')
    return raw


def identity(root):
    """Presence and content in BOTH locations participate in change detection."""
    rows = []
    for item, info in _entries(root):
        relative = item.relative_to(root).as_posix()
        if info is None:
            rows.append((relative, 'missing'))
            continue
        with item.open('rb') as stream:
            raw = stream.read(LIMITS[item.name] + 1)
        if len(raw) > LIMITS[item.name]:
            raise MetadataError('The installed release metadata is too large.')
        rows.append((relative, 'file', len(raw), hashlib.sha256(raw).hexdigest()))
    directory(root)  # invalid or conflicting entries never select legacy fallback
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode('utf8')).hexdigest()
