"""Bounded format-1 container preflight for untrusted .vlui bytes.

This is the first validation stage ONLY. It does not validate the theme's
schemas, CSS semantics or decoded raster frames. Its output must never be
installed, previewed or served until those additional stages have passed.
No filesystem access, extraction, renderer or application commands live here.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
import hashlib
import json
import math
import re
import stat
import struct
import time
from types import MappingProxyType
from typing import Callable, Mapping
import zlib


LIMITS = MappingProxyType({
    "archiveBytes": 33554432, "assetBytes": 8388608,
    "aggregateInflatedBytes": 67108864, "entryCount": 128,
    "rasterAssetCount": 64, "manifestBytes": 32768,
    "styleBytes": 65536, "layoutBytes": 131072,
    "aggregateJSONBytes": 229376, "cssBytes": 65536,
    "maximumCompressionRatio": 100, "jsonDepth": 16,
    "jsonNodes": 10000, "jsonStringBytes": 4096,
    "cssRules": 256, "cssDeclarations": 2048, "cssNesting": 4,
    "importDeadlineMilliseconds": 30000,
})
FILE_LIMITS = {"manifest.json": LIMITS["manifestBytes"],
               "style.json": LIMITS["styleBytes"],
               "layout.json": LIMITS["layoutBytes"],
               "custom.css": LIMITS["cssBytes"],
               "preview.webp": LIMITS["assetBytes"]}
RASTER_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z", re.ASCII)
RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CLOCK$",
                      *(f"COM{i}" for i in range(1, 10)),
                      *(f"LPT{i}" for i in range(1, 10))})


class VluiArchiveError(ValueError):
    """A bounded, non-secret error suitable for localization by the caller."""


@dataclass(frozen=True)
class StructureSnapshot:
    """Immutable bytes with container checks passed; not an accepted theme."""

    sha256: str
    archive: bytes
    members: Mapping[str, bytes]
    validation_stage: str = "container-only"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VluiArchiveError(message)


def canonical_member(name: str, *, directory: bool = False) -> str:
    require(type(name) is str and 0 < len(name) <= 240, "Invalid archive path length.")
    require(name.isascii(), "Archive paths must use the format-1 ASCII grammar.")
    require(directory == name.endswith("/"), "Archive directory marker is inconsistent.")
    bare = name[:-1] if directory else name
    parts = bare.split("/")
    require(1 <= len(parts) <= 5, "Invalid archive path depth.")
    for part in parts:
        require(bool(SEGMENT.fullmatch(part)) and not part.endswith("."), "Invalid archive path segment.")
        require(part.split(".", 1)[0].upper() not in RESERVED, "Reserved archive path segment.")
    if directory:
        require(parts[0] == "assets", "Only assets directory entries are supported.")
    elif name not in FILE_LIMITS:
        require(len(parts) >= 2 and parts[0] == "assets", "Unsupported archive member.")
        leaf = parts[-1].rsplit(".", 1)
        require(len(leaf) == 2 and bool(leaf[0]) and leaf[1] in RASTER_EXTENSIONS,
                "Unsupported raster extension.")
    return name


def _json_text_budget(text: str) -> None:
    # Bound nesting before JSON's recursive parser allocates nested containers.
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            require(depth <= LIMITS["jsonDepth"], "JSON nesting limit exceeded.")
        elif character in "]}":
            depth -= 1


def strict_json(payload: bytes, *, node_budget: list[int] | None = None) -> dict:
    require(type(payload) is bytes and len(payload) <= LIMITS["layoutBytes"], "JSON byte limit exceeded.")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON object key.")
            result[key] = value
        return result

    def reject_constant(_value):
        raise VluiArchiveError("Non-finite JSON number.")

    try:
        text = payload.decode("utf-8", errors="strict")
        _json_text_budget(text)
        result = json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except VluiArchiveError:
        raise
    except (UnicodeError, ValueError, RecursionError, OverflowError) as error:
        raise VluiArchiveError("Malformed UTF-8 JSON document.") from error
    require(type(result) is dict, "JSON document must be an object.")
    budget = node_budget if node_budget is not None else [0]

    def visit(value, depth):
        budget[0] += 1
        require(budget[0] <= LIMITS["jsonNodes"], "JSON node limit exceeded.")
        require(depth <= LIMITS["jsonDepth"], "JSON nesting limit exceeded.")
        if type(value) is str:
            try:
                length = len(value.encode("utf-8", errors="strict"))
            except UnicodeError as error:
                raise VluiArchiveError("Invalid Unicode JSON string.") from error
            require(length <= LIMITS["jsonStringBytes"], "JSON string limit exceeded.")
        elif type(value) is float:
            require(math.isfinite(value), "Non-finite JSON number.")
        elif type(value) is dict:
            for key, item in value.items():
                visit(key, depth)
                visit(item, depth + 1)
        elif type(value) is list:
            for item in value:
                visit(item, depth + 1)

    visit(result, 1)
    return result


def inspect_container(snapshot: bytes, *, cancelled: Callable[[], bool] | None = None,
                      clock: Callable[[], float] = time.monotonic) -> StructureSnapshot:
    """Check an immutable ZIP subset, inflate with hard output limits, check JSON.

    Callers must read/copy once with the archive-byte ceiling BEFORE calling.
    A passing result remains container-only until schema/CSS/media validation.
    """
    require(type(snapshot) is bytes, "Validation requires an immutable byte snapshot.")
    require(22 <= len(snapshot) <= LIMITS["archiveBytes"], "Archive byte limit exceeded.")
    deadline = clock() + LIMITS["importDeadlineMilliseconds"] / 1000

    def checkpoint():
        require(not (cancelled and cancelled()), "Theme validation cancelled.")
        require(clock() <= deadline, "Theme validation deadline exceeded.")

    checkpoint()
    try:
        eocd = struct.unpack_from("<4s4H2LH", snapshot, len(snapshot) - 22)
        signature, disk, cd_disk, on_disk, count, cd_size, cd_offset, comment_length = eocd
        require(signature == b"PK\x05\x06" and comment_length == 0, "Unsupported ZIP footer or appended payload.")
        require(disk == cd_disk == 0 and on_disk == count, "Split ZIP archives are unsupported.")
        require(1 <= count <= LIMITS["entryCount"], "Archive entry limit exceeded.")
        require(cd_offset + cd_size == len(snapshot) - 22, "ZIP central directory boundary mismatch.")
        entries, names, files, directories = [], set(), set(), set()
        cursor, total_declared, total_compressed, raster_count, json_bytes = cd_offset, 0, 0, 0, 0
        for _index in range(count):
            checkpoint()
            require(cursor + 46 <= cd_offset + cd_size, "Truncated ZIP central header.")
            central = struct.unpack_from("<4s6H3L5H2L", snapshot, cursor)
            (magic, made, needed, flags, method, dos_time, dos_date, crc, compressed,
             inflated, name_length, extra_length, comment, entry_disk, internal,
             external, offset) = central
            require(magic == b"PK\x01\x02", "Invalid ZIP central header.")
            require(needed in {10, 20} and made & 255 in {10, 20}, "Unsupported ZIP version or ZIP64.")
            require(made >> 8 in {0, 3}, "Unsupported ZIP creator attributes.")
            require(flags & ~0x800 == 0 and method in {0, 8}, "Unsupported ZIP flags or compression.")
            require(entry_disk == 0 and extra_length == comment == 0 and internal == 0,
                    "Unsupported ZIP extra fields or entry metadata.")
            require(compressed != 0xFFFFFFFF and inflated != 0xFFFFFFFF and offset != 0xFFFFFFFF,
                    "ZIP64 is unsupported.")
            cursor += 46
            require(0 < name_length <= 240 and cursor + name_length <= cd_offset + cd_size,
                    "Invalid ZIP member name length.")
            raw_name = snapshot[cursor:cursor + name_length]
            name = raw_name.decode("ascii", errors="strict")
            directory = name.endswith("/")
            canonical_member(name, directory=directory)
            folded = name.rstrip("/").casefold()
            require(folded not in names, "Duplicate or ambiguous archive member.")
            names.add(folded)
            mode = stat.S_IFMT(external >> 16)
            require(mode in ({0, stat.S_IFDIR} if directory else {0, stat.S_IFREG}),
                    "Links and special archive members are unsupported.")
            require(external & 0xFFFF & ~0x21 == (0x10 if directory else 0),
                    "Unsupported ZIP file attributes.")
            cursor += name_length
            if directory:
                require(inflated == 0 and crc == 0, "Directory entry contains content.")
                directories.add(folded)
                limit = 0
            else:
                files.add(folded)
                limit = FILE_LIMITS.get(name, LIMITS["assetBytes"])
                if name.endswith(".json"):
                    json_bytes += inflated
                if name == "preview.webp" or name.startswith("assets/"):
                    raster_count += 1
                require(name not in {"layout.json", "custom.css"} or inflated > 0,
                        "Empty optional layout or CSS is unsupported.")
            require(inflated <= limit, "Archive member byte limit exceeded.")
            require(inflated <= LIMITS["maximumCompressionRatio"] * max(compressed, 1),
                    "Archive expansion ratio exceeded.")
            total_declared += inflated
            total_compressed += compressed
            require(total_declared <= LIMITS["aggregateInflatedBytes"], "Inflated archive limit exceeded.")
            require(json_bytes <= LIMITS["aggregateJSONBytes"], "Aggregate JSON byte limit exceeded.")
            require(raster_count <= LIMITS["rasterAssetCount"], "Raster asset count exceeded.")
            entries.append((name, raw_name, directory, needed, flags, method, dos_time,
                            dos_date, crc, compressed, inflated, offset))
        require(cursor == cd_offset + cd_size, "Unlisted central directory payload.")
        require({"manifest.json", "style.json"} <= files, "Required theme documents are missing.")
        for name in files | directories:
            parts = name.split("/")
            for index in range(1, len(parts)):
                require("/".join(parts[:index]) not in files, "File/directory archive collision.")
        require(total_declared <= LIMITS["maximumCompressionRatio"] * max(total_compressed, 1),
                "Aggregate expansion ratio exceeded.")

        cursor, actual_total, node_budget, members = 0, 0, [0], {}
        for entry in sorted(entries, key=lambda item: item[-1]):
            checkpoint()
            (name, raw_name, directory, needed, flags, method, dos_time, dos_date,
             crc, compressed, inflated, offset) = entry
            require(offset == cursor and offset + 30 <= cd_offset,
                    "Overlapping/unlisted ZIP payload or preamble.")
            local = struct.unpack_from("<4s5H3L2H", snapshot, offset)
            require(local[:9] == (b"PK\x03\x04", needed, flags, method, dos_time, dos_date,
                                  crc, compressed, inflated), "ZIP local/central header disagreement.")
            require(local[9:] == (len(raw_name), 0), "ZIP local name/extra fields disagree.")
            data_start = offset + 30 + len(raw_name)
            require(snapshot[offset + 30:data_start] == raw_name, "ZIP local path disagreement.")
            data_end = data_start + compressed
            require(data_end <= cd_offset, "ZIP payload overlaps central directory.")
            chunks, actual, computed_crc = [], 0, 0

            def accept(chunk):
                nonlocal actual, actual_total, computed_crc
                checkpoint()
                actual += len(chunk)
                actual_total += len(chunk)
                require(actual <= inflated and actual_total <= LIMITS["aggregateInflatedBytes"],
                        "Actual inflated bytes exceed declared or aggregate limit.")
                computed_crc = binascii.crc32(chunk, computed_crc)
                chunks.append(chunk)

            if method == 0:
                require(compressed == inflated, "Stored member size mismatch.")
                for position in range(data_start, data_end, 65536):
                    accept(snapshot[position:min(position + 65536, data_end)])
            else:
                inflater = zlib.decompressobj(-15)
                for position in range(data_start, data_end, 65536):
                    pending = snapshot[position:min(position + 65536, data_end)]
                    while pending:
                        accept(inflater.decompress(pending, min(65536, inflated - actual + 1)))
                        require(not inflater.unused_data, "Trailing deflate payload.")
                        pending = inflater.unconsumed_tail
                require(inflater.eof and not inflater.unused_data, "Truncated deflate stream.")
            require(actual == inflated and computed_crc & 0xFFFFFFFF == crc, "ZIP length or CRC mismatch.")
            payload = b"".join(chunks)
            if not directory:
                if name.endswith(".json"):
                    strict_json(payload, node_budget=node_budget)
                members[name] = payload
            cursor = data_end
        require(cursor == cd_offset, "Unlisted payload before central directory.")
        require(actual_total <= LIMITS["maximumCompressionRatio"] * max(total_compressed, 1),
                "Actual aggregate expansion ratio exceeded.")
        checkpoint()
        return StructureSnapshot(hashlib.sha256(snapshot).hexdigest(), snapshot, MappingProxyType(members))
    except (UnicodeError, struct.error, zlib.error) as error:
        raise VluiArchiveError("Malformed or unsupported ZIP container.") from error
