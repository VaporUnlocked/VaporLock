"""Click-through capture overlay positioned in native screen coordinates."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from engine_stream import native_observation_age_ms, native_tick_ms


class CaptureOverlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, wintypes.UINT)
        self.api.SetWindowPos.restype = wintypes.BOOL
        self.api.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
        self.api.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self.settings = None
        self.payload = None
        self.geometry_key = None
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.hide)

    def show_observation(self, payload, settings):
        now = native_tick_ms()
        remaining = settings.output_max_observation_age_ms - native_observation_age_ms(payload, now)
        if remaining <= 0 or payload.get('source_boundary') or not payload.get('reference_inside', True):
            self.hide()
            return
        from crosshair_reference import remaining_ms

        remaining = min(remaining, remaining_ms(payload, now))
        if remaining <= 0:
            self.hide()
            return
        key = tuple(payload[name] for name in ('capture_left', 'capture_top', 'width', 'height'))
        if key != self.geometry_key or not self.isVisible():
            handle = int(self.winId())
            if not self.api.SetWindowDisplayAffinity(handle, 0x11) or not self.api.SetWindowPos(
                handle, -1, *key, 0x0010 | 0x0200,
            ):
                self.hide()
                raise OSError('The desktop overlay could not be excluded from capture.')
            self.geometry_key = key
        self.settings, self.payload = settings, payload
        self.setWindowOpacity(max(.15, min(1., settings.desktop_overlay_opacity / 100)))
        self.show()
        self.expiry.start(max(1, int(remaining)))
        self.update()

    def paintEvent(self, event):
        if self.payload is None:
            return
        painter = QPainter(self)
        settings, payload = self.settings, self.payload
        painter.scale(self.width() / payload['width'], self.height() / payload['height'])
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if settings.desktop_overlay_capture_bounds:
            painter.setPen(QPen(QColor(settings.desktop_overlay_fov_color), 1.0))
            painter.drawRect(QRectF(1, 1, payload['width'] - 2, payload['height'] - 2))
        if settings.overlay_fov:
            painter.setPen(QPen(QColor(settings.desktop_overlay_fov_color), 1.0))
            x, y = payload['reference_x'], payload['reference_y']
            for radius in (settings.snap_radius, settings.near_radius, settings.acquisition_radius):
                area = QRectF(x - radius, y - radius, radius * 2, radius * 2)
                if settings.fov_shape == 'circle':
                    painter.drawEllipse(area)
                else:
                    painter.drawRect(area)
        for track in payload['tracks']:
            color = settings.desktop_overlay_selected_color if track['selected'] else settings.desktop_overlay_track_color
            painter.setPen(QPen(QColor(color), 2.0 if track['selected'] else 1.0))
            if settings.overlay_boxes:
                painter.drawRect(QRectF(track['x'], track['y'], track['w'], track['h']))
            if settings.desktop_overlay_labels:
                painter.drawText(QPointF(track['x'], track['y'] - 4), track['label'])
            if settings.desktop_overlay_motion and track['guide_valid']:
                painter.drawLine(QPointF(track['anchor_x'], track['anchor_y']),
                                 QPointF(track['predicted_x'], track['predicted_y']))
