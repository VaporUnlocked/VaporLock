"""Fixed isolated raster-worker entry. Never import this into the client core.

Untrusted input is accepted only on stdin after the parent verifies the OS
sandbox. There are no theme paths, commands, plugin names or archive inputs.
"""
from __future__ import annotations

import ctypes
import hashlib
import io
import json
import math
import struct
import sys
import time
import warnings
import zlib

# Apply the DLL policy before importing codec extensions. The dedicated bundle
# and System32 are the only search directories; no PATH/cwd/profile lookup.
if sys.platform != "win32":
    raise SystemExit(120)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.SetDefaultDllDirectories.argtypes = [ctypes.c_uint32]
kernel.SetDefaultDllDirectories.restype = ctypes.c_int
if not kernel.SetDefaultDllDirectories(0x1000):
    raise SystemExit(121)

from PIL import Image, ImageFile, GifImagePlugin, JpegImagePlugin, PngImagePlugin, WebPImagePlugin

Image.MAX_IMAGE_PIXELS = 16777216
ImageFile.LOAD_TRUNCATED_IMAGES = False
warnings.simplefilter("error", Image.DecompressionBombWarning)

MAX_ASSET = 8388608
MAX_POSTER = 2097152
FORMATS = ("PNG", "JPEG", "WEBP", "GIF")


class MediaFailure(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise MediaFailure(code)


def exact(stream, length):
    value = stream.read(length)
    need(len(value) == length, "MEDIA_PROTOCOL")
    return value


def structure(data, kind):
    """Bound frame enumeration and prove complete raster container consumption.

    This does not decode pixels. Only fixed PNG/JPEG/GIF/WebP grammar is read.
    The isolated codecs subsequently load every composited frame.
    """
    size, frames, animated = len(data), 0, False
    if kind == "PNG":
        need(data.startswith(b"\x89PNG\r\n\x1a\n"), "MEDIA_SIGNATURE")
        offset, ended, actl = 8, False, None
        while offset < size:
            need(offset + 12 <= size, "MEDIA_STRUCTURE")
            length = struct.unpack_from(">I", data, offset)[0]
            end = offset + 12 + length
            need(end <= size, "MEDIA_STRUCTURE")
            tag = data[offset + 4:offset + 8]
            need(all(65 <= c <= 90 or 97 <= c <= 122 for c in tag), "MEDIA_STRUCTURE")
            body = data[offset + 8:end - 4]
            need(zlib.crc32(tag + body) & 0xffffffff == struct.unpack_from(">I", data, end - 4)[0],
                 "MEDIA_CRC")
            if offset == 8:
                need(tag == b"IHDR" and length == 13, "MEDIA_STRUCTURE")
            elif tag == b"IHDR":
                raise MediaFailure("MEDIA_STRUCTURE")
            if tag == b"acTL":
                need(actl is None and length == 8, "MEDIA_STRUCTURE")
                actl = struct.unpack_from(">I", body)[0]
                need(1 <= actl <= 300, "MEDIA_FRAMES")
                animated = True
            if tag == b"fcTL":
                need(actl is not None and length == 26, "MEDIA_STRUCTURE")
                frames += 1
                need(frames <= 300, "MEDIA_FRAMES")
            if tag == b"IEND":
                need(length == 0 and end == size, "MEDIA_TRAILING")
                ended = True
            offset = end
        need(ended and (actl is None or frames == actl), "MEDIA_STRUCTURE")
        return frames if animated else 1, animated
    if kind == "WEBP":
        need(size >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP", "MEDIA_SIGNATURE")
        need(struct.unpack_from("<I", data, 4)[0] + 8 == size, "MEDIA_TRAILING")
        offset = 12
        while offset < size:
            need(offset + 8 <= size, "MEDIA_STRUCTURE")
            tag, length = data[offset:offset + 4], struct.unpack_from("<I", data, offset + 4)[0]
            end = offset + 8 + length + (length & 1)
            need(end <= size, "MEDIA_STRUCTURE")
            if length & 1:
                need(data[end - 1] == 0, "MEDIA_STRUCTURE")
            if tag == b"ANMF":
                frames += 1
                need(frames <= 300, "MEDIA_FRAMES")
            offset = end
        need(offset == size, "MEDIA_STRUCTURE")
        return frames or 1, frames > 0
    if kind == "GIF":
        need(size >= 13 and data[:6] in (b"GIF87a", b"GIF89a"), "MEDIA_SIGNATURE")
        offset = 13 + (3 * (1 << ((data[10] & 7) + 1)) if data[10] & 128 else 0)

        def blocks(at):
            while True:
                need(at < size, "MEDIA_STRUCTURE")
                n = data[at]
                at += 1
                need(at + n <= size, "MEDIA_STRUCTURE")
                if n == 0:
                    return at
                at += n

        while offset < size:
            tag = data[offset]
            offset += 1
            if tag == 0x3b:
                need(offset == size and frames > 0, "MEDIA_TRAILING")
                return frames, frames > 1
            if tag == 0x21:
                need(offset < size, "MEDIA_STRUCTURE")
                offset = blocks(offset + 1)
            elif tag == 0x2c:
                need(offset + 9 <= size, "MEDIA_STRUCTURE")
                packed = data[offset + 8]
                offset += 9 + (3 * (1 << ((packed & 7) + 1)) if packed & 128 else 0)
                need(offset < size and 2 <= data[offset] <= 8, "MEDIA_STRUCTURE")
                offset = blocks(offset + 1)
                frames += 1
                need(frames <= 300, "MEDIA_FRAMES")
            else:
                raise MediaFailure("MEDIA_STRUCTURE")
        raise MediaFailure("MEDIA_STRUCTURE")
    need(size >= 4 and data[:2] == b"\xff\xd8", "MEDIA_SIGNATURE")
    offset = 2
    entropy = False
    while offset < size:
        if entropy:
            while offset < size and data[offset] != 255:
                offset += 1
            need(offset < size, "MEDIA_STRUCTURE")
        need(data[offset] == 255, "MEDIA_STRUCTURE")
        while offset < size and data[offset] == 255:
            offset += 1
        need(offset < size, "MEDIA_STRUCTURE")
        marker = data[offset]
        offset += 1
        if entropy and (marker == 0 or 0xd0 <= marker <= 0xd7):
            continue
        entropy = False
        if marker == 0xd9:
            need(offset == size, "MEDIA_TRAILING")
            return 1, False
        need(marker not in (0, 0xd8) and not 0xd0 <= marker <= 0xd7, "MEDIA_STRUCTURE")
        if marker == 1:
            continue
        need(offset + 2 <= size, "MEDIA_STRUCTURE")
        length = struct.unpack_from(">H", data, offset)[0]
        need(length >= 2 and offset + length <= size, "MEDIA_STRUCTURE")
        offset += length
        entropy = marker == 0xda
    raise MediaFailure("MEDIA_STRUCTURE")


def decode(data, kind, preview, remaining):
    declared, animation = structure(data, kind)
    work, durations, poster = 0, [], None
    deadline = time.monotonic() + 5
    with Image.open(io.BytesIO(data), formats=[kind]) as image:
        need(image.format == kind, "MEDIA_FORMAT")
        width, height = image.size
        default_image = kind == "PNG" and bool(image.info.get("default_image", False))
        total = declared + int(default_image)
        need(1 <= total <= 300, "MEDIA_FRAMES")
        need(not preview or (kind == "WEBP" and not animation and width <= 1024 and height <= 1024),
             "MEDIA_PREVIEW")
        side, pixels = (1920, 2073600) if animation else (4096, 16777216)
        need(0 < width <= side and 0 < height <= side and width * height <= pixels, "MEDIA_DIMENSIONS")
        need(width * height * total <= min(128000000, remaining), "MEDIA_PIXEL_WORK")
        for index in range(total):
            need(time.monotonic() < deadline, "MEDIA_DEADLINE")
            # Avoid n_frames: GIF's implementation can scan/seek the entire file.
            image.seek(index)
            need(image.size == (width, height), "MEDIA_FRAME_DIMENSIONS")
            work += width * height
            need(work <= min(128000000, remaining), "MEDIA_PIXEL_WORK")
            image.load()
            converted = image.convert("RGBA")
            try:
                converted.load()
                if animation and not (default_image and index == 0):
                    value = image.info.get("duration", 0)
                    need(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 60000,
                         "MEDIA_DURATION")
                    durations.append(max(33, round(value)))
                # APNG's non-animated default image is validated, but its poster
                # is the first animated frame shown by the actual playback.
                if poster is None and not (default_image and index == 0):
                    factor = min(1, 1024 / width, 1024 / height)
                    target = max(1, round(width * factor)), max(1, round(height * factor))
                    small = converted.resize(target, Image.Resampling.LANCZOS)
                    clean = Image.frombytes("RGBA", target, small.tobytes())
                    try:
                        output = io.BytesIO()
                        clean.save(output, format="WEBP", quality=75, method=0)
                        poster = output.getvalue()
                        need(len(poster) <= MAX_POSTER, "MEDIA_POSTER_LIMIT")
                    finally:
                        clean.close()
                        small.close()
            finally:
                converted.close()
        try:
            image.seek(total)
        except EOFError:
            pass
        else:
            raise MediaFailure("MEDIA_FRAMES")
    need(poster is not None, "MEDIA_POSTER")
    return {"ok": True, "format": kind, "width": width, "height": height,
            "frames": total, "animated": animation, "defaultImage": default_image,
            "pixelWork": work, "durations": durations, "sha256": hashlib.sha256(data).hexdigest(),
            "posterWidth": target[0], "posterHeight": target[1]}, poster


def main():
    try:
        header = exact(sys.stdin.buffer, 15)
        need(header[:5] == b"VLMI\x01", "MEDIA_PROTOCOL")
        format_id, preview, remaining, length = struct.unpack("<BBII", header[5:])
        need(format_id < 4 and preview in (0, 1) and 0 < length <= MAX_ASSET
             and 0 < remaining <= 256000000, "MEDIA_PROTOCOL")
        data = exact(sys.stdin.buffer, length)
        need(sys.stdin.buffer.read(1) == b"", "MEDIA_PROTOCOL")
        metadata, poster = decode(data, FORMATS[format_id], bool(preview), remaining)
    except MediaFailure as error:
        metadata, poster = {"ok": False, "code": str(error)}, b""
    except (ValueError, OSError, EOFError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, OverflowError, MemoryError):
        metadata, poster = {"ok": False, "code": "MEDIA_DECODE"}, b""
    except Exception:
        metadata, poster = {"ok": False, "code": "MEDIA_WORKER_FAILURE"}, b""
    result = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    need(len(result) <= 16384 and len(poster) <= MAX_POSTER, "MEDIA_PROTOCOL")
    sys.stdout.buffer.write(b"VLMO\x01" + struct.pack("<II", len(result), len(poster)) + result + poster)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
