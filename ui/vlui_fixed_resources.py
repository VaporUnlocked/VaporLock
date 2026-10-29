"""Read one native-pinned25-entry byte map; no directory serving or Qt imports."""
import hashlib
from pathlib import Path

from vlui_host_admission import Resource
from vlui_live_protocol import decode_object_bytes


from vlui_resource_root import browser_resource_root, browser_resource_manifest
_REPOSITORY = browser_resource_root()
_MANIFEST = browser_resource_manifest()


def _no_links(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('FIXED_RESOURCE_REJECTED')


def load_fixed_resources(*, reviewed_manifest_sha256):
    """Expected hash comes from the exact completed source receipt, never JS/config."""
    _no_links(_MANIFEST)
    with _MANIFEST.open('rb') as stream:
        raw = stream.read(32769)
    if (type(reviewed_manifest_sha256) is not str or len(reviewed_manifest_sha256) != 64
            or not all(char in '0123456789abcdef' for char in reviewed_manifest_sha256)
            or len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != reviewed_manifest_sha256):
        raise ValueError('FIXED_RESOURCE_REJECTED')
    manifest = decode_object_bytes(raw, 32768, max_items=64)
    if set(manifest) != {'resourceVersion', 'resources'} or type(manifest['resourceVersion']) is not int or manifest['resourceVersion'] != 1:
        raise ValueError('FIXED_RESOURCE_REJECTED')
    entries = manifest['resources']
    if type(entries) is not list or len(entries) != 25:
        raise ValueError('FIXED_RESOURCE_REJECTED')
    output = []
    for entry in entries:
        if type(entry) is not dict or set(entry) != {'url', 'mime', 'requestType', 'file', 'bytes', 'sha256'}:
            raise ValueError('FIXED_RESOURCE_REJECTED')
        name = entry['file']
        if (type(name) is not str or not name or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-' for char in name)
                or name.startswith('/') or any(part in {'', '.', '..'} for part in name.split('/'))):
            raise ValueError('FIXED_RESOURCE_REJECTED')
        path = _REPOSITORY.joinpath(*name.split('/'))
        path.relative_to(_REPOSITORY)
        _no_links(path)
        expected_size = entry['bytes']
        if type(expected_size) is not int or not 1 <= expected_size <= 3145728:
            raise ValueError('FIXED_RESOURCE_REJECTED')
        with path.open('rb') as stream:
            data = stream.read(expected_size + 1)
        if len(data) != expected_size:
            raise ValueError('FIXED_RESOURCE_REJECTED')
        output.append(Resource(entry['url'], entry['mime'], entry['requestType'], data, entry['sha256']))
    if len({resource.url for resource in output}) != 25 or sum(len(resource.data) for resource in output) > 16 * 1024 * 1024:
        raise ValueError('FIXED_RESOURCE_REJECTED')
    return tuple(output)
