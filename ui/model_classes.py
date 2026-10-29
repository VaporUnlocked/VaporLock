"""Read bounded ONNX class-name metadata as data, without an inference runtime.

ONNX ModelProto metadata_props is field 14; its string pair uses fields 1/2.
Graph/tensor payloads are skipped, never decoded or loaded. No external data.
"""
import ast
import ctypes as C
from ctypes import wintypes as W
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import unicodedata

MAX_CLASSES = 1024
MAX_MODEL = 4 * 1024**3


def clean_label(value):
    if (type(value) is not str or len(value) > 128 or any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('Invalid class label')
    return value.strip()


def validate_aliases(entries):
    if type(entries) is not list or len(entries) > 16:
        raise ValueError('Invalid model aliases')
    hashes = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {'model', 'aliases'}:
            raise ValueError('Invalid model aliases')
        key = entry['model']
        if type(key) is not str or not re.fullmatch('[0-9a-f]{64}', key) or key in hashes:
            raise ValueError('Invalid model aliases')
        hashes.add(key)
        aliases = entry['aliases']
        if type(aliases) is not dict or len(aliases) > 128:
            raise ValueError('Too many class aliases')
        for class_id, alias in aliases.items():
            if type(class_id) is not str or not re.fullmatch(r'0|[1-9][0-9]{0,5}', class_id) or not clean_label(alias):
                raise ValueError('Invalid class alias')
    if len(json.dumps(entries).encode('utf8')) > 131072:
        raise ValueError('Too many model aliases')
    return entries


def parse_names(text):
    if len(text.encode('utf8')) > 65536: raise ValueError('Class metadata too large')
    tree = ast.parse(text, mode='eval')
    if sum(1 for _ in ast.walk(tree)) > 4096: raise ValueError('Class metadata too complex')
    value = tree.body
    if isinstance(value, (ast.List, ast.Tuple)):
        pairs = list(enumerate(value.elts))
    elif isinstance(value, ast.Dict):
        pairs = []
        for key, label in zip(value.keys, value.values):
            if not isinstance(key, ast.Constant) or type(key.value) not in (str, int):
                raise ValueError('Invalid class ID')
            raw = str(key.value)
            if not re.fullmatch(r'0|[1-9][0-9]{0,5}', raw): raise ValueError('Invalid class ID')
            pairs.append((int(raw), label))
    else: raise ValueError('Invalid class metadata')
    if len(pairs) > MAX_CLASSES: raise ValueError('Too many model classes')
    result = {}
    for class_id, node in pairs:
        if class_id in result or not isinstance(node, ast.Constant): raise ValueError('Invalid class label')
        result[class_id] = clean_label(node.value)
    return result


def _varint(stream):
    value = 0
    for shift in range(0, 70, 7):
        byte = stream.read(1)
        if not byte: raise ValueError('Truncated metadata')
        n = byte[0]
        if shift == 63 and n > 1: raise ValueError('Invalid metadata integer')
        value |= (n & 127) << shift
        if n < 128: return value
    raise ValueError('Invalid metadata integer')


def _fields(stream, end):
    count = 0
    while stream.tell() < end:
        count += 1
        if count > 4096: raise ValueError('Too many metadata fields')
        tag = _varint(stream); number, wire = tag >> 3, tag & 7
        if not number or number >= 2**29: raise ValueError('Invalid metadata field')
        if wire == 0:
            _varint(stream); continue
        if wire not in (1, 2, 5): raise ValueError('Unsupported metadata field')
        size = _varint(stream) if wire == 2 else 8 if wire == 1 else 4
        start = stream.tell()
        if start + size > end: raise ValueError('Truncated metadata field')
        yield number, wire, size
        stream.seek(start + size)


def _names_from_stream(stream, length):
    total = 0; seen = False; names = {}
    for field, wire, size in _fields(stream, length):
        if field != 14 or wire != 2: continue
        total += size
        if total > 524288 or size > 131072: raise ValueError('Metadata too large')
        pair = io.BytesIO(stream.read(size)); values = {}
        for key, pair_wire, pair_size in _fields(pair, size):
            if key not in (1, 2) or pair_wire != 2 or key in values: raise ValueError('Invalid metadata pair')
            values[key] = pair.read(pair_size).decode('utf8')
        if values.get(1) == 'names':
            if seen: raise ValueError('Duplicate names metadata')
            seen = True; names = parse_names(values.get(2, ''))
    return names


def _open_locked(path):
    if os.name != 'nt': return path.open('rb')
    import msvcrt
    kernel = C.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    create.restype = W.HANDLE
    drive_type = kernel.GetDriveTypeW; drive_type.argtypes = [W.LPCWSTR]; drive_type.restype = W.UINT
    if drive_type(path.anchor) not in (2, 3): raise ValueError('Local model required')
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == C.c_void_p(-1).value: raise OSError('Model is unavailable')
    try: fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close = kernel.CloseHandle; close.argtypes = [W.HANDLE]; close(handle); raise
    return os.fdopen(fd, 'rb')


def inspect_classes(path, cancelled):
    path = Path(path)
    if str(path).startswith(('\\\\', '//')) or path.suffix.lower() != '.onnx': raise ValueError('Local ONNX model required')
    path = path.resolve(strict=True)
    deadline = time.monotonic() + 20
    def identity(info): return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with _open_locked(path) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MODEL: raise ValueError('Model size unavailable')
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            if cancelled.is_set() or time.monotonic() > deadline: raise ValueError('Class lookup cancelled')
            block = stream.read(min(remaining, 1048576))
            if not block: raise ValueError('Model changed')
            digest.update(block); remaining -= len(block)
        stream.seek(0)
        try:
            names = _names_from_stream(stream, info.st_size)
            available = bool(names)
        except (ValueError, SyntaxError, RecursionError, UnicodeError):
            names = {}; available = False
        if cancelled.is_set() or identity(info) != identity(os.fstat(stream.fileno())) or identity(info) != identity(path.stat()):
            raise ValueError('Model changed')
        return {'sha256': digest.hexdigest(), 'names': names, 'available': available,
                'identity': identity(info), 'path': str(path)}
