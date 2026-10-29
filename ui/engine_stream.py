"""Bounded framing and capture-age validation for native visual messages."""

from __future__ import annotations

from collections.abc import Iterator
import ctypes
import math
import sys


if sys.platform == "win32":
    _get_tick_count64 = ctypes.WinDLL("kernel32").GetTickCount64
    _get_tick_count64.argtypes = []
    _get_tick_count64.restype = ctypes.c_uint64
else:
    _get_tick_count64 = None


def native_tick_ms() -> int | None:
    # Use the same boot-relative Windows clock as the engine, not an assumed
    # shared epoch between Python monotonic() and C++ steady_clock.
    return int(_get_tick_count64()) if _get_tick_count64 is not None else None


def native_observation_age_ms(payload: dict, now: int | None) -> float:
    emitted = payload.get("emitted_tick_ms")
    age = payload.get("observation_age_ms")
    if (type(now) is not int or type(emitted) is not int or not 0 <= emitted <= now
            or type(age) not in (int, float)):
        return math.inf
    try:
        age = float(age)
        if not math.isfinite(age) or age < 0:
            return math.inf
        return age + (now - emitted)
    except OverflowError:
        return math.inf


class OverlayFrameGate:
    """Display-only acceptance for one owned engine launch and command epoch."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.identity = None
        self.pending_boundary = None

    def accept(self, payload: dict, epoch: int, now: int | None) -> bool:
        if payload.get('schema') != 2 or payload.get('control_epoch') != epoch:
            return False
        names = ('session', 'source_generation', 'frame_id', 'config_revision')
        identity = tuple(payload.get(name) for name in names)
        if any(type(v) is not int or not 0 < v < 2**53 for v in identity):
            return False
        if type(payload.get('control_epoch')) is not int or not math.isfinite(native_observation_age_ms(payload, now)):
            return False
        geometry = [payload.get(name) for name in ('capture_left', 'capture_top', 'width', 'height')]
        if any(type(v) is not int for v in geometry):
            return False
        left, top, width, height = geometry
        if (abs(left) > 1000000 or abs(top) > 1000000 or not 0 < width <= 16384
                or not 0 < height <= 16384 or width * height > 16777216):
            return False
        tracks = payload.get('tracks')
        if type(tracks) is not list or len(tracks) > 4096:
            return False
        if type(payload.get('selected_track')) is not int or payload['selected_track'] < 0:
            return False
        boundary = payload.get('source_boundary', False)
        if type(boundary) is not bool or (boundary and (tracks or payload['selected_track'])):
            return False
        for name in ('reference_x', 'reference_y'):
            value = payload.get(name)
            if type(value) not in (int, float) or abs(value) > 1000000 or not math.isfinite(value):
                return False
        seen = set()
        for track in tracks:
            if (type(track) is not dict or type(track.get('id')) is not int
                    or not 0 < track['id'] < 2**53 or track['id'] in seen
                    or type(track.get('selected')) is not bool or type(track.get('eligible')) is not bool
                    or type(track.get('guide_valid')) is not bool
                    or type(track.get('label')) is not str or len(track['label']) > 4096
                    or type(track.get('missed')) is not int or track['missed'] < 0):
                return False
            seen.add(track['id'])
            for name in ('x', 'y', 'w', 'h', 'anchor_x', 'anchor_y', 'predicted_x', 'predicted_y', 'confidence'):
                value = track.get(name)
                if type(value) not in (int, float) or abs(value) > 1000000 or not math.isfinite(value):
                    return False
            age = track.get('observation_age_ms')
            if (age is not None and (type(age) not in (int, float) or not 0 <= age <= 2**53 or not math.isfinite(age))
                    or track['w'] <= 0 or track['h'] <= 0 or not 0 <= track['confidence'] <= 1):
                return False
            if track['selected'] != (track['id'] == payload['selected_track']):
                return False
        if self.identity is not None:
            session, source, frame, revision = self.identity
            completion = not boundary and identity == self.pending_boundary
            if (identity[0] != session or identity[1] < source or identity[2] < frame
                    or (identity[2] == frame and not completion) or identity[3] < revision):
                return False
        self.identity = identity
        self.pending_boundary = identity if boundary else None
        return True


class PreviewFrameGate(OverlayFrameGate):
    """JPEGs have their own frame rate, but cannot cross an observed source boundary."""
    def accept(self, payload: dict, epoch: int, now: int | None, boundary=None) -> bool:
        identity = tuple(payload.get(k) for k in ('session', 'source_generation', 'frame_id', 'config_revision'))
        if boundary is not None:
            if identity[0] != boundary[0] or identity[1] != boundary[1] or identity[3] != boundary[3]:
                return False
        # Preview pixels do not carry a target list. Reuse the bounded identity,
        # geometry, monotonic clock and command-epoch checks from the overlay.
        validation = dict(payload, tracks=[], selected_track=0, reference_x=0, reference_y=0,
                          capture_left=0, capture_top=0, source_boundary=False)
        return super().accept(validation, epoch, now)


class EngineLineReader:
    def __init__(self, maximum_line_bytes: int = 64 * 1024 * 1024) -> None:
        self.maximum_line_bytes = max(1, maximum_line_bytes)
        self._pending = bytearray()
        self._discarding = False

    def reset(self) -> None:
        self._pending.clear()
        self._discarding = False

    def feed(self, data: bytes) -> Iterator[bytes | None]:
        """Yield complete lines, or None once per overlarge record.

        Decode only after framing so a multibyte character may cross any pipe
        boundary. Discard an overlarge line through its newline, not a suffix
        that could be mistaken for an independent engine message.
        """
        offset = 0
        while offset < len(data):
            newline = data.find(b"\n", offset)
            end = len(data) if newline < 0 else newline
            if not self._discarding:
                if len(self._pending) + end - offset > self.maximum_line_bytes:
                    self._pending.clear()
                    self._discarding = True
                    yield None
                else:
                    self._pending.extend(data[offset:end])
            if newline < 0:
                return
            if not self._discarding:
                yield bytes(self._pending)
            self._pending.clear()
            self._discarding = False
            offset = newline + 1
