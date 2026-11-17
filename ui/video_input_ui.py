"""Capture-source controls and asynchronous Windows video-device discovery."""
import json

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

VIDEO_FIELDS = frozenset(("capture_source", "video_device", "video_file", "video_full_frame"))


def group(layout):
    widget = QWidget()
    body = QVBoxLayout(widget)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(layout.spacing())
    layout.addWidget(widget)
    return widget, body


class CaptureSourceControls(QObject):
    def __init__(self, center, layout):
        super().__init__(layout.parentWidget())
        self.center, self.discovery = center, None
        self.source = QComboBox()
        for label, key in (("Desktop", "desktop"), ("Window", "window"), ("Capture card or camera", "device"), ("Video file", "file")):
            self.source.addItem(center.t(label), key)
        self.source.currentIndexChanged.connect(lambda index: center.change("capture_source", self.source.itemData(index)))
        center.setting(layout, "Capture source", "Choose a desktop display, a window, a video input, or a local video file.", self.source)

        self.desktop, self.desktop_layout = group(layout)
        self.window_panel, window_layout = group(layout)
        window_row = QWidget()
        row = QHBoxLayout(window_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.window = QComboBox()
        self.window.setProperty("_vaporlock_i18n_skip", True)
        row.addWidget(self.window, 1)
        self.refresh_window_button = QPushButton(center.t("Refresh windows"))
        self.refresh_window_button.clicked.connect(self.refresh_windows)
        row.addWidget(self.refresh_window_button)
        center.setting(window_layout, "Capture window", "Choose an open window. Capture follows it across displays and stops when it closes.", window_row)
        self.window.addItem(center.t("Select a window"), None)
        self.device_panel, device_layout = group(layout)
        self.device = QComboBox()
        self.device.setProperty("_vaporlock_i18n_skip", True)
        self.device.currentIndexChanged.connect(self.choose_device)
        refresh = QPushButton(center.t("Refresh devices"))
        refresh.setProperty("missionIcon", "refresh")
        refresh.clicked.connect(self.refresh_devices)
        self.refresh_button = refresh
        device_row = QWidget()
        row = QHBoxLayout(device_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.device, 1)
        row.addWidget(refresh)
        center.setting(device_layout, "Video device", "Select a connected capture card, camera, or virtual camera.", device_row)
        self.device_status = QLabel()
        self.device_status.setObjectName("detail")
        self.device_status.setWordWrap(True)
        device_layout.addWidget(self.device_status)

        self.file_panel, file_layout = group(layout)
        file_row = QWidget()
        row = QHBoxLayout(file_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText(center.t("Choose a local video file"))
        browse = QPushButton(center.t("Browse…"))
        browse.setProperty("missionIcon", "folder")
        browse.clicked.connect(self.choose_file)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        center.setting(file_layout, "Recording", "Play once at the recorded timing in Observe mode.", file_row)

        self.video_panel, video_layout = group(layout)
        self.full_frame = QCheckBox(center.t("Use full source frame"))
        self.full_frame.toggled.connect(lambda checked: center.change("video_full_frame", checked))
        center.setting(video_layout, "Input framing", "Use the native frame dimensions, or apply the centered crop below.", self.full_frame)
        self.crop_panel, self.crop_layout = group(layout)
        self.desktop_picker = None
        self.devices = []
        self.sync()
        if center.settings.capture_source == "device":
            QTimer.singleShot(0, self.refresh_devices)
        if center.settings.capture_source == "window":
            QTimer.singleShot(0, self.refresh_windows)

    def sync(self):
        settings = self.center.settings
        source = settings.capture_source
        for widget, callback in (
            (self.source, lambda: self.source.setCurrentIndex(max(0, self.source.findData(source)))),
            (self.full_frame, lambda: self.full_frame.setChecked(settings.video_full_frame)),
        ):
            blocked = widget.blockSignals(True)
            callback()
            widget.blockSignals(blocked)
        self.path.setText(settings.video_file)
        self.desktop.setVisible(source == "desktop")
        self.window_panel.setVisible(source == "window")
        self.device_panel.setVisible(source == "device")
        self.file_panel.setVisible(source == "file")
        self.video_panel.setVisible(source != "desktop")
        self.crop_panel.setVisible(source == "desktop" or not settings.video_full_frame)
        if self.desktop_picker is not None:
            self.desktop_picker.setVisible(source == "desktop")
        self.fill_devices()

    def set_idle(self, idle):
        for control in (self.source, self.device, self.refresh_button, self.path, self.file_panel, self.full_frame,
                        self.window, self.refresh_window_button):
            control.setEnabled(idle)
        if self.discovery is not None:
            self.refresh_button.setEnabled(False)

    def fill_devices(self):
        selected = self.center.settings.video_device
        blocked = self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem(self.center.t("Select a video device"), "")
        for name, key in self.devices:
            self.device.addItem(name, key)
        if selected and self.device.findData(selected) < 0:
            self.device.addItem(self.center.t("Saved device unavailable"), selected)
        self.device.setCurrentIndex(max(0, self.device.findData(selected)))
        self.device.blockSignals(blocked)

    def choose_device(self, index):
        if index >= 0:
            self.center.change("video_device", str(self.device.itemData(index)))

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self.center, self.center.t("Choose video file"), self.path.text(),
            self.center.t("Video files (*.mp4 *.m4v *.mov *.wmv *.avi *.mkv);;All files (*)"))
        if path:
            self.center.change("video_file", path)

    def refresh_windows(self):
        from window_capture import windows_for_capture
        selected = self.window.currentData()
        self.window.clear()
        self.window.addItem(self.center.t("Select a window"), None)
        for name, handle, pid in windows_for_capture():
            self.window.addItem(f"{name} · {pid}", f"{handle}:{pid}")
        self.window.setCurrentIndex(max(0, self.window.findData(selected)))

    def refresh_devices(self):
        from vapor_lock_gui import EngineBridge
        if self.discovery is not None or self.center.engine.active:
            return
        executable, environment = EngineBridge.launch_for("cpu")
        if executable is None:
            self.device_status.setText(self.center.t("Build the native engine to list video devices."))
            return
        process = self.discovery = QProcess(self)
        process.setProcessEnvironment(environment)
        process.finished.connect(self.finish_discovery)
        process.errorOccurred.connect(lambda error: self.finish_discovery(-1) if error == QProcess.ProcessError.FailedToStart else None)
        self.refresh_button.setEnabled(False)
        self.device_status.setText(self.center.t("Looking for Windows video devices…"))
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(process.kill)
        timeout.start(6000)
        process.start(str(executable), ["--headless", "--list-video-devices"])

    def finish_discovery(self, code):
        process, self.discovery = self.discovery, None
        if process is None:
            return
        raw = bytes(process.readAllStandardOutput())
        process.deleteLater()
        self.refresh_button.setEnabled(not self.center.engine.active)
        try:
            if code != 0 or len(raw) > 1024 * 1024:
                raise ValueError()
            result = json.loads(raw)
            items = result["devices"]
            if result["type"] != "video-devices" or type(items) is not list or len(items) > 64:
                raise ValueError()
            devices = {}
            for item in items:
                key, name = item["id"], item["name"]
                if (type(key) is not str or type(name) is not str or not 1 <= len(key) <= 2048
                        or not 1 <= len(name) <= 256 or any(ord(c) < 32 for c in key)):
                    raise ValueError()
                devices[key] = name
        except (ValueError, KeyError, TypeError):
            self.device_status.setText(self.center.t("Windows video devices could not be listed."))
            return
        self.devices = [(name, key) for key, name in devices.items()]
        self.fill_devices()
        message = "" if self.devices else self.center.t("No Windows video devices found.")
        self.device_status.setText(message)
