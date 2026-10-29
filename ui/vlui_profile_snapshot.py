"""Bound a native profile's exact immutable bytes to stable file identity."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

MAX_PROFILE_BYTES = 1048576

def _identity(info):
    # Reading can update atime. It is not a content or object identity field.
    # The pinned Windows Python exposes birthtime on both fstat and stat.
    # Its legacy ctime differs between those APIs on this host. Compare the
    # explicit creation field; other platforms retain their change-time check.
    creation = getattr(info, 'st_birthtime_ns', info.st_ctime_ns)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, creation)

@dataclass(frozen=True, slots=True)
class NativeProfileSnapshot:
    data: bytes
    identity: tuple
    sha256: bytes

    def matches_path(self, path):
        try:
            current = read_profile_snapshot(path)
            return current.identity == self.identity and current.sha256 == self.sha256
        except (OSError, ValueError, TypeError, OverflowError):
            return False

def read_profile_snapshot(path):
    path = Path(path)
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_PROFILE_BYTES:
            raise ValueError('PROFILE_FILE_REJECTED')
        data = stream.read(MAX_PROFILE_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (len(data) > MAX_PROFILE_BYTES or len(data) != after.st_size
                or _identity(before) != _identity(after)
                or _identity(path.stat()) != _identity(after)):
            raise ValueError('PROFILE_CHANGED')
    return NativeProfileSnapshot(data, _identity(after), hashlib.sha256(data).digest())
