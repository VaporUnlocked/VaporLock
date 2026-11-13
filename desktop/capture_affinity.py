"""Exclude VaporLock windows from the capture used to locate a crosshair."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget


def _exclude_window(window: QWidget, handle: int) -> bool:
    if isinstance(window, QFileDialog) and not window.testOption(QFileDialog.Option.DontUseNativeDialog):
        return False
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    exclude = user32.SetWindowDisplayAffinity
    exclude.argtypes = [wintypes.HWND, wintypes.DWORD]
    exclude.restype = wintypes.BOOL
    return bool(exclude(int(handle), 0x11))  # WDA_EXCLUDEFROMCAPTURE


def exclude_windows() -> bool:
    return all(_exclude_window(window, window.winId()) for window in QApplication.topLevelWidgets()
               if window.isWindow() and window.isVisible())


class CaptureExclusionGuard(QObject):
    """Keep shown and recreated top-level handles excluded during capture."""

    def __init__(self, parent: QWidget, required, failed):
        super().__init__(parent)
        self.required = required
        self.failed = failed
        self._busy = False
        self._application = QApplication.instance()
        self._application.installEventFilter(self)

    def eventFilter(self, window, event):
        if (self._busy or event.type() not in (QEvent.Type.Show, QEvent.Type.WinIdChange)
                or not isinstance(window, QWidget) or not window.isWindow() or not self.required()):
            return False
        self._busy = True
        try:
            handle = window.effectiveWinId()
            # Destruction also changes the window ID. Leave that handle closed.
            if handle or event.type() == QEvent.Type.Show:
                if not _exclude_window(window, handle or window.winId()):
                    self.failed()
        except (AttributeError, OSError, RuntimeError):
            self.failed()
        finally:
            self._busy = False
        return False

    def close(self):
        self._application.removeEventFilter(self)
