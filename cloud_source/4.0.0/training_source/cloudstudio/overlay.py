"""Ordinary desktop overlay windows; no process injection, hooks, or input control.

Live output is supported on Windows 10 2004+ and Windows 11 only when Windows
confirms WDA_EXCLUDEFROMCAPTURE for every overlay window and DWM is composing.
Otherwise the caller continues in its embedded preview. Capture exclusion is an
OS capture policy, not a security guarantee for every possible capture product.
Exclusive-fullscreen applications may cover ordinary desktop overlay windows.

Physical capture pixels and Qt's device-independent screen geometry are mapped
per monitor. A global multiply/divide by devicePixelRatio is incorrect on mixed
DPI Windows desktops, whose Qt screen origins can form islands/gaps.

Official references:
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getwindowdisplayaffinity
https://doc.qt.io/qt-6/highdpi.html
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import sys
import time

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QThread, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from .live import LiveFrame, normalize_capture_region


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    def __post_init__(self):
        if not all(math.isfinite(value) for value in (self.left, self.top, self.width, self.height)):
            raise ValueError("Rectangle values must be finite.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Rectangle dimensions must be positive.")

    @classmethod
    def from_region(cls, region: dict) -> "Rect":
        return cls(**normalize_capture_region(region))

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def intersection(self, other: "Rect") -> "Rect | None":
        left, top = max(self.left, other.left), max(self.top, other.top)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(left, top, right - left, bottom - top) if right > left and bottom > top else None


@dataclass(frozen=True)
class ScreenMapping:
    name: str
    physical: Rect
    logical: Rect
    device_pixel_ratio: float = 1.0

    def __post_init__(self):
        if not math.isfinite(self.device_pixel_ratio) or self.device_pixel_ratio <= 0:
            raise ValueError("Screen device pixel ratio must be positive and finite.")

    def global_physical_to_local(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.physical.left) * self.logical.width / self.physical.width,
                (y - self.physical.top) * self.logical.height / self.physical.height)


def map_capture_points(points, capture_region: dict | Rect, screen: ScreenMapping) -> list[tuple[float, float]]:
    """Map frame-local physical pixels to one overlay window's local Qt pixels.

    Coordinates outside this monitor deliberately remain outside; QPainter clips
    crossing polygons at the window boundary instead of distorting their vertices.
    """
    capture = capture_region if isinstance(capture_region, Rect) else Rect.from_region(capture_region)
    result = []
    for point in points:
        if len(point) != 2 or not all(math.isfinite(float(value)) for value in point):
            raise ValueError("Overlay geometry must contain finite x/y pairs.")
        result.append(screen.global_physical_to_local(capture.left + float(point[0]),
                                                      capture.top + float(point[1])))
    return result


def map_capture_rect(capture_region: dict | Rect, screen: ScreenMapping) -> Rect | None:
    capture = capture_region if isinstance(capture_region, Rect) else Rect.from_region(capture_region)
    overlap = capture.intersection(screen.physical)
    if overlap is None:
        return None
    left, top = screen.global_physical_to_local(overlap.left, overlap.top)
    right, bottom = screen.global_physical_to_local(overlap.right, overlap.bottom)
    return Rect(left, top, right - left, bottom - top)


def _windows_monitors() -> dict[str, Rect]:
    """Query physical monitor bounds in a temporary per-monitor DPI thread context."""
    if sys.platform != "win32":
        raise RuntimeError("Desktop overlays require Windows 10 2004+ or Windows 11; using preview only.")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not hasattr(user32, "SetThreadDpiAwarenessContext"):
        raise RuntimeError("Windows cannot provide verified per-monitor DPI coordinates; using preview only.")
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise RuntimeError("Cannot query the physical desktop in per-monitor DPI mode; using preview only.")

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    monitors, errors = {}, []

    def collect(handle, hdc, rect, data):
        # Exceptions must not escape a ctypes callback: ctypes would only print
        # them and enumeration could appear to have succeeded with partial data.
        try:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                raise RuntimeError("GetMonitorInfoW failed")
            r = info.rcMonitor
            key = _screen_key(info.szDevice)
            if not key or key in monitors:
                raise RuntimeError("Windows returned an ambiguous monitor identity")
            monitors[key] = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
            return True
        except Exception as exc:
            errors.append(str(exc))
            return False

    callback = callback_type(collect)
    try:
        ok = user32.EnumDisplayMonitors(None, None, callback, 0)
    finally:
        user32.SetThreadDpiAwarenessContext(previous)
    if not ok or errors or not monitors:
        raise RuntimeError("Could not verify the physical Windows monitor layout; using preview only.")
    return monitors


def _screen_key(name: str) -> str:
    return str(name).replace("\\", "").replace(".", "").casefold()


def _screen_mappings() -> list[tuple[object, ScreenMapping]]:
    physical = _windows_monitors()
    result = []
    for screen in QGuiApplication.screens():
        key = _screen_key(screen.name())
        if key not in physical:
            raise RuntimeError(f"Cannot match Qt screen {screen.name()!r} to its physical monitor; using preview only.")
        geometry = screen.geometry()
        logical = Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
        native = physical[key]
        ratio = float(screen.devicePixelRatio())
        # Rounding is allowed; an unexplained scale mismatch must not draw misplaced outlines.
        if abs(logical.width * ratio - native.width) > 2 or abs(logical.height * ratio - native.height) > 2:
            raise RuntimeError(f"Physical/Qt DPI mapping is inconsistent for {screen.name()}; using preview only.")
        result.append((screen, ScreenMapping(screen.name(), native, logical, ratio)))
    if len(result) != len(physical):
        raise RuntimeError("Qt and Windows report different monitor sets; using preview only.")
    return result


def verify_capture_exclusion(hwnd: int) -> tuple[bool, str]:
    """Set/verify native no-input styles and exact capture exclusion for our window."""
    if sys.platform != "win32":
        return False, "Desktop capture exclusion is supported here only on Windows; using preview only."
    if sys.getwindowsversion().build < 19041:
        return False, "Windows 10 version 2004 or newer is required to exclude overlay windows; using preview only."
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
        dwm.DwmIsCompositionEnabled.restype = ctypes.c_long
        composing = wintypes.BOOL()
        if dwm.DwmIsCompositionEnabled(ctypes.byref(composing)) != 0 or not composing.value:
            return False, "Windows desktop composition is unavailable; using preview only."
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.argtypes, get_style.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
        set_style.argtypes, set_style.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        required_styles = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080  # layered/transparent/noactivate/toolwindow
        style = get_style(hwnd, -20)
        ctypes.set_last_error(0)
        prior = set_style(hwnd, -20, style | required_styles)
        if not prior and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if get_style(hwnd, -20) & required_styles != required_styles:
            return False, "Windows could not verify a click-through, no-focus overlay; using preview only."
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        if not user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0020):
            raise ctypes.WinError(ctypes.get_last_error())
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
        if not user32.SetWindowDisplayAffinity(hwnd, 0x11):
            raise ctypes.WinError(ctypes.get_last_error())
        affinity = wintypes.DWORD()
        if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
            raise ctypes.WinError(ctypes.get_last_error())
        if affinity.value != 0x11:
            return False, "Windows did not confirm WDA_EXCLUDEFROMCAPTURE; using preview only."
        return True, "Overlay enabled; Windows confirms capture exclusion and click-through/no-focus styles."
    except Exception as exc:
        return False, f"Overlay capture exclusion could not be verified ({exc}); using preview only."


class _OverlayWindow(QWidget):
    def __init__(self, screen, mapping: ScreenMapping):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.mapping = mapping
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent;")
        self.setWindowTitle("Cloud Studio capture-excluded overlay")
        self.winId()
        if screen is not None:
            self.windowHandle().setScreen(screen)
        geometry = mapping.logical
        self.setGeometry(QRect(round(geometry.left), round(geometry.top), round(geometry.width), round(geometry.height)))
        self._polygons, self._boxes, self._masks = [], [], []
        self._capture_time = None
        self._clip = None
        self._hud = ""
        self._fps = 0.0

    def set_result(self, frame: LiveFrame, diagnostic: bool, fps: float):
        capture = Rect.from_region(frame.region)
        self._clip = map_capture_rect(capture, self.mapping)
        self._capture_time = frame.capture_monotonic
        self._fps = fps
        self._polygons, self._boxes, self._masks = [], [], []
        detections = frame.result.get("detections", [])
        for detection in detections:
            if detection.get("mask") is not None:
                from .mask_paint import make_mask_image
                try:
                    if detection["mask"].get("size") != [frame.image.height, frame.image.width]:
                        raise ValueError("The overlay mask belongs to a different source image size.")
                    mask_image, (left, top) = make_mask_image(detection["mask"])
                    if not mask_image.isNull():
                        (x1, y1), (x2, y2) = map_capture_points(
                            ((left, top), (left + mask_image.width(), top + mask_image.height())),
                            capture, self.mapping)
                        self._masks.append((mask_image, QRectF(x1, y1, x2 - x1, y2 - y1)))
                except (TypeError, ValueError, KeyError):
                    pass
                # An invalid authoritative mask must never silently fall back
                # to a polygon that can fill its holes or discard pieces.
                continue
            points = detection.get("wrapper", [])
            if 3 <= len(points) <= 2048:
                try:
                    self._polygons.append(QPolygonF([QPointF(x, y) for x, y in
                                                      map_capture_points(points, capture, self.mapping)]))
                except (TypeError, ValueError):
                    pass
        primary = frame.result.get("diagnostics", {}).get("primary_detections", detections)
        if diagnostic:
            for detection in primary:
                box = detection.get("primary_box")
                if isinstance(box, (list, tuple)) and len(box) == 4:
                    try:
                        (x1, y1), (x2, y2) = map_capture_points((box[:2], box[2:]), capture, self.mapping)
                        if x2 > x1 and y2 > y1:
                            self._boxes.append(QRectF(x1, y1, x2 - x1, y2 - y1))
                    except (TypeError, ValueError):
                        pass
        count = len(detections)
        self._hud = f"{count} encasings" if count else "No encasings"
        if diagnostic:
            self._hud += f" · {len(primary)} main boxes"
        invalid = frame.result.get("invalid_contours", 0)
        if invalid:
            self._hud += f" · {invalid} invalid"
        self.update()

    def clear(self):
        self._polygons, self._boxes, self._masks = [], [], []
        self._capture_time = None
        self._hud = ""
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self._capture_time is None or self._clip is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = self._clip
        painter.setClipRect(QRectF(clip.left, clip.top, clip.width, clip.height))
        for mask_image, destination in self._masks:
            painter.drawImage(destination, mask_image)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(83, 239, 201, 245), 1.8))
        for polygon in self._polygons:
            painter.drawPolygon(polygon)
        painter.setPen(QPen(QColor(255, 180, 62, 240), 1.4, Qt.PenStyle.DashLine))
        for box in self._boxes:
            painter.drawRect(box)
        age = max(0, time.monotonic() - self._capture_time) * 1000
        hud = f"Cloud · {self._hud} · {self._fps:.1f} FPS · {age:.0f} ms old"
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        hud_width = min(clip.width - 8, painter.fontMetrics().horizontalAdvance(hud) + 16)
        if hud_width > 0:
            painter.fillRect(QRectF(clip.left + 4, clip.top + 4, hud_width, 25), QColor(9, 17, 26, 220))
            painter.setPen(QColor(211, 246, 237))
            painter.drawText(QPointF(clip.left + 11, clip.top + 21), hud)


class OverlayManager(QObject):
    """GUI-thread owner of verified per-screen windows and a stale-result timer."""
    def __init__(self, parent=None, stale_after_ms: float = 750, *, screen_provider=None,
                 window_factory=None, exclusion_verifier=None, clock=time.monotonic):
        super().__init__(parent)
        if not isinstance(stale_after_ms, (int, float)) or not math.isfinite(stale_after_ms) or stale_after_ms <= 0:
            raise ValueError("Overlay expiry must be a positive finite number of milliseconds.")
        self.stale_after_ms = float(stale_after_ms)
        self._clock = clock
        self._screen_provider = screen_provider or _screen_mappings
        self._window_factory = window_factory or _OverlayWindow
        self._exclusion_verifier = exclusion_verifier or verify_capture_exclusion
        self._windows = []
        self._connections = []
        self._verified_handles = {}
        self._region = None
        self._capture_time = None
        self._previous_sequence = None
        self._previous_capture_time = None
        self.enabled = False
        self.reason = "Overlay is off."
        self._timer = QTimer(self)
        self._timer.setInterval(max(10, min(50, round(self.stale_after_ms / 4))))
        self._timer.timeout.connect(self._expire)
        self._assert_gui_thread()

    def _assert_gui_thread(self):
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise RuntimeError("Overlay windows must be created and updated on the QApplication GUI thread.")

    def enable(self, region: dict) -> tuple[bool, str]:
        self._assert_gui_thread()
        self.close()
        try:
            self._region = normalize_capture_region(region)
            capture = Rect.from_region(region)
            mappings = [(screen, mapping) for screen, mapping in self._screen_provider()
                        if capture.intersection(mapping.physical) is not None]
            if not mappings:
                raise RuntimeError("The capture region does not intersect a verified monitor; using preview only.")
            for screen, mapping in mappings:
                window = self._window_factory(screen, mapping)
                self._windows.append(window)
                ok, reason = self._exclusion_verifier(int(window.winId()))
                if not ok:
                    raise RuntimeError(reason)
                self._verified_handles[id(window)] = int(window.winId())
            # All windows are blank until every native exclusion check succeeds.
            for window in self._windows:
                window.show()
                ok, reason = self._exclusion_verifier(int(window.winId()))
                if not ok:
                    raise RuntimeError(reason)
                self._verified_handles[id(window)] = int(window.winId())
            for screen, _ in mappings:
                if screen is not None:
                    for signal_name in ("geometryChanged", "logicalDotsPerInchChanged", "physicalDotsPerInchChanged"):
                        signal = getattr(screen, signal_name, None)
                        if signal is not None:
                            signal.connect(self._display_changed)
                            self._connections.append(signal)
            app = QGuiApplication.instance()
            for signal in (app.screenAdded, app.screenRemoved):
                signal.connect(self._display_changed)
                self._connections.append(signal)
            self.enabled = True
            self.reason = "Overlay enabled; capture exclusion verified. Windowed/borderless desktop display is required."
            self._timer.start()
            return True, self.reason
        except Exception as exc:
            self.close()
            self.reason = str(exc)
            return False, self.reason

    def show_frame(self, frame: LiveFrame, diagnostic: bool = False) -> bool:
        self._assert_gui_thread()
        if not self.enabled:
            return False
        if frame.region != self._region:
            self.close()
            self.reason = "Capture coordinates changed; restart the overlay to remap the desktop."
            return False
        if frame.age_ms(self._clock()) > self.stale_after_ms:
            self.clear()
            return False
        try:
            for window in self._windows:
                handle = int(window.winId())
                if handle != self._verified_handles.get(id(window)):
                    ok, reason = self._exclusion_verifier(handle)
                    if not ok:
                        raise RuntimeError(reason)
                    self._verified_handles[id(window)] = handle
            fps = 0.0
            if self._previous_capture_time is not None and frame.capture_monotonic > self._previous_capture_time:
                fps = max(0, frame.sequence - self._previous_sequence) / (frame.capture_monotonic - self._previous_capture_time)
            elif frame.inference_ms + frame.capture_ms > 0:
                fps = 1000 / (frame.inference_ms + frame.capture_ms)
            self._capture_time = frame.capture_monotonic
            self._previous_capture_time, self._previous_sequence = frame.capture_monotonic, frame.sequence
            for window in self._windows:
                window.set_result(frame, diagnostic, fps)
            return True
        except Exception as exc:
            self.close()
            self.reason = f"Overlay stopped: {exc}. Using preview only."
            return False

    def clear(self):
        self._assert_gui_thread()
        self._capture_time = None
        for window in self._windows:
            window.clear()

    def _expire(self):
        if self._capture_time is not None:
            if (self._clock() - self._capture_time) * 1000 > self.stale_after_ms:
                self.clear()
            else:
                for window in self._windows:
                    window.update()

    def _display_changed(self, *args):
        self.close()
        self.reason = "Display geometry or DPI changed; restart the overlay to remap the desktop."

    def close(self):
        self._assert_gui_thread()
        self._timer.stop()
        for signal in self._connections:
            try:
                signal.disconnect(self._display_changed)
            except (RuntimeError, TypeError):
                pass
        self._connections = []
        for window in self._windows:
            window.clear()
            window.hide()
            window.close()
            window.deleteLater()
        self._windows = []
        self._verified_handles = {}
        self._capture_time = None
        self._previous_sequence = None
        self._previous_capture_time = None
        self._region = None
        self.enabled = False
        self.reason = "Overlay is off."
