"""Bounded continuous capture/inference, independent of Qt widgets.

One worker owns its MSS instance from construction through close. It captures
only when ready to infer, and publishes into a one-item mailbox. The GUI polls
that mailbox instead of queuing one Qt signal (and image) for every result.

The caller must reserve the runtime exclusively until ``is_running`` is false.
Stopping is cooperative: an in-flight native inference call finishes before
the worker closes capture. No thread is killed and no external process is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import threading
import time
import traceback
from typing import Callable

from PIL import Image


MAX_CAPTURE_PIXELS = 50_000_000
DEFAULT_SETTINGS = {"confidence": .35, "nms_iou": .5, "max_detections": 32,
                    "debug": False, "min_height": 0.0}


def normalize_capture_region(region: dict) -> dict[str, int]:
    if not isinstance(region, dict):
        raise ValueError("Capture region must contain left, top, width, and height in physical pixels.")
    required = ("left", "top", "width", "height")
    if any(type(region.get(key)) is not int for key in required):
        raise ValueError("Capture region coordinates and dimensions must be integers.")
    result = {key: region[key] for key in required}
    if result["width"] <= 0 or result["height"] <= 0:
        raise ValueError("Capture region dimensions must be positive.")
    if result["width"] * result["height"] > MAX_CAPTURE_PIXELS:
        raise ValueError("Capture region exceeds 50 million pixels; select a smaller region.")
    if any(not -(2 ** 31) <= result[key] <= 2 ** 31 - 1 for key in required):
        raise ValueError("Capture region exceeds supported desktop coordinate bounds.")
    return result


def _validated_settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError("Live settings support confidence, nms_iou, max_detections, debug, and min_height.")
    result = dict(DEFAULT_SETTINGS)
    result.update(value)
    for name in ("confidence", "nms_iou"):
        setting = result[name]
        if isinstance(setting, bool) or not isinstance(setting, (int, float)) or not math.isfinite(setting) or not 0 <= setting <= 1:
            raise ValueError(f"{name} must be a finite number from 0 to 1.")
    if type(result["max_detections"]) is not int or not 1 <= result["max_detections"] <= 256:
        raise ValueError("max_detections must be an integer from 1 to 256.")
    if type(result["debug"]) is not bool:
        raise ValueError("debug must be true or false.")
    minimum = result["min_height"]
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or minimum < 0:
        raise ValueError("min_height must be finite and nonnegative.")
    return result


def _validated_fps(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 120:
        raise ValueError("Target FPS must be a finite number from 1 to 120.")
    return float(value)


@dataclass(frozen=True)
class LiveFrame:
    """One immutable pairing; image/result ownership transfers to the consumer.

    ``capture_monotonic`` is taken immediately before the grab, conservatively
    measuring frame age even when capture or inference is slow. ``inference_ms``
    measures the complete runtime.predict call, including its preprocessing.
    """

    image: Image.Image
    result: dict
    region: dict[str, int]
    sequence: int
    capture_monotonic: float
    completed_monotonic: float
    capture_utc: str
    inference_ms: float
    capture_ms: float = 0.0

    def age_ms(self, now: float | None = None) -> float:
        return max(0.0, ((time.monotonic() if now is None else now) - self.capture_monotonic) * 1000)

    def is_stale(self, max_age_ms: float = 750, now: float | None = None) -> bool:
        return self.age_ms(now) > max_age_ms


class LiveSession:
    """A single-use continuous capture worker with a latest-only mailbox."""

    def __init__(self, runtime, region: dict, *, settings: dict | None = None,
                 target_fps: float = 10, capture_factory: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.runtime = runtime
        self._region = normalize_capture_region(region)
        self._settings = _validated_settings(settings or {})
        self._target_fps = _validated_fps(target_fps)
        self._capture_factory = capture_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: LiveFrame | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._traceback: str | None = None
        self._processed = 0
        self._dropped = 0
        self._monitors: list[dict] = []
        self._started_monotonic: float | None = None

    @property
    def region(self) -> dict[str, int]:
        """Return a copy of the session's fixed physical capture rectangle."""
        return dict(self._region)

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("A LiveSession is single-use; create a new session to restart capture.")
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="Cloud-live-capture", daemon=False)
        self._thread.start()

    def update_settings(self, settings: dict | None = None, target_fps: float | None = None) -> None:
        """Apply a validated snapshot at the next frame; never mutate in-flight settings."""
        with self._lock:
            if settings is not None and not isinstance(settings, dict):
                raise ValueError("Live settings must be an object.")
            candidate = dict(self._settings)
            if settings is not None:
                candidate.update(settings)
            candidate = _validated_settings(candidate)
            rate = self._target_fps if target_fps is None else _validated_fps(target_fps)
            self._settings, self._target_fps = candidate, rate

    def request_stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._latest = None
            if self._state in ("starting", "running"):
                self._state = "stopping"

    def join(self, timeout: float = 0) -> bool:
        """Return whether shutdown completed; GUI callers should use timeout=0."""
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Join timeout must be finite and nonnegative.")
        thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                raise RuntimeError("A live worker cannot join itself.")
            thread.join(timeout)
        return not self.is_running

    def poll(self) -> LiveFrame | None:
        with self._lock:
            latest, self._latest = self._latest, None
            return latest

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state, "processed": self._processed, "dropped": self._dropped,
                    "last_error": self._last_error, "traceback": self._traceback,
                    "pending_results": int(self._latest is not None),
                    "monitors": [dict(monitor) for monitor in self._monitors],
                    "target_fps": self._target_fps, "settings": dict(self._settings),
                    "started_monotonic": self._started_monotonic}

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            message = f"{type(exc).__name__}: {exc}"
            self._last_error = message if self._last_error is None else self._last_error + "\n" + message
            self._traceback = traceback.format_exc()
            self._state = "error"
            self._latest = None

    def _run(self) -> None:
        capture = None
        try:
            if self._stop.is_set():
                return
            # Windows MSS has thread-local handles, so construction also belongs here.
            if self._capture_factory is None:
                import mss
                capture = mss.mss()
            else:
                capture = self._capture_factory()
            monitors = [dict(monitor) for monitor in capture.monitors]
            with self._lock:
                self._monitors = monitors
                self._started_monotonic = self._clock()
                self._state = "stopping" if self._stop.is_set() else "running"
            while not self._stop.is_set():
                with self._lock:
                    settings, target_fps = dict(self._settings), self._target_fps
                capture_time = self._clock()
                capture_utc = datetime.now(timezone.utc).isoformat()
                raw = capture.grab(dict(self._region))
                image = Image.frombytes("RGB", raw.size, raw.rgb)
                del raw
                if image.size != (self._region["width"], self._region["height"]):
                    raise RuntimeError("Capture returned unexpected dimensions; the desktop layout may have changed.")
                captured = self._clock()
                if self._stop.is_set():
                    break
                result = self.runtime.predict(image, **settings)
                completed = self._clock()
                if not isinstance(result, dict) or not isinstance(result.get("detections"), list):
                    raise RuntimeError("The loaded runtime did not return a valid detection result.")
                with self._lock:
                    self._processed += 1
                    if self._stop.is_set():
                        break
                    frame = LiveFrame(image=image, result=result, region=dict(self._region),
                                      sequence=self._processed, capture_monotonic=capture_time,
                                      completed_monotonic=completed, capture_utc=capture_utc,
                                      inference_ms=max(0, completed - captured) * 1000,
                                      capture_ms=max(0, captured - capture_time) * 1000)
                    if self._latest is not None:
                        self._dropped += 1
                    self._latest = frame
                # Interruptible pacing; capture cannot race ahead of a slow model.
                self._stop.wait(max(0, 1 / target_fps - (self._clock() - capture_time)))
        except Exception as exc:
            self._record_error(exc)
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception as exc:
                    self._record_error(exc)
            with self._lock:
                if self._state != "error":
                    self._state = "stopped"
