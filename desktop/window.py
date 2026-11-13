"""VaporLock's native desktop window."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from desktop.controls import QUICK_FIELDS, SettingRow, SettingsPanel
from desktop.engine import EngineController, PROVIDER_LABELS
from desktop.settings import ProductPaths, Settings, read_profile, save_profile, validate_settings
from desktop.shortcuts import GlobalHotkeys
from desktop.style import STYLESHEET


def label(text, style):
    widget = QLabel(text)
    widget.setObjectName(style)
    return widget


def button(text, handler, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(handler)
    return widget


class CapturePreview(QLabel):
    def __init__(self):
        super().__init__('Start capture to see the live preview.')
        self.setObjectName('preview')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(260, 190)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.frame = None
        self.source = QPixmap()

    def update_frame(self, frame, message):
        if frame is self.frame and frame is not None:
            return
        if frame is None:
            if self.frame is not None or self.text() != message:
                self.source = QPixmap()
                self.clear()
                self.setText(message)
        else:
            image = QPixmap()
            if image.loadFromData(frame.jpeg, 'JPEG'):
                self.source = image
                self.rescale()
        self.frame = frame

    def rescale(self):
        if not self.source.isNull():
            self.setPixmap(self.source.scaled(self.contentsRect().size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.FastTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.rescale()


class DesktopWindow(QMainWindow):
    def __init__(self, paths, engine=None, *, global_shortcuts=True):
        super().__init__()
        self.paths = paths
        self.engine = engine or EngineController(paths)
        self.settings = Settings()
        self.workspace = None
        self.profile_path = None
        self.saved = asdict(self.settings)
        self.affinity_required = False
        self.affinity_guard = None
        self.overlay = None
        self.session_settings = None
        self.show_overlay = False
        self.overlay_identity = None
        self.last_messages = None
        self.last_error = ''
        self.api = None
        self.api_snapshot_at = 0.0
        self.closing = False
        self.hotkeys = GlobalHotkeys() if global_shortcuts else None
        self.setWindowTitle('VaporLock')
        self.resize(1220, 790)
        self.setMinimumSize(760, 575)
        if paths.icon.is_file():
            self.setWindowIcon(QIcon(str(paths.icon)))

        root = QWidget()
        root.setObjectName('desktopRoot')
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        masthead = QWidget()
        masthead.setObjectName('masthead')
        header = QHBoxLayout(masthead)
        header.setContentsMargins(24, 12, 24, 12)
        header.addWidget(label('V', 'brandMark'))
        header.addWidget(label('VaporLock', 'brand'))
        header.addSpacing(20)
        self.navigation = QButtonGroup(self)
        for index, title in enumerate(('Capture', 'Workspace', 'Settings', 'Diagnostics')):
            item = QPushButton(title)
            item.setCheckable(True)
            item.setObjectName('nav')
            self.navigation.addButton(item, index)
            header.addWidget(item)
        self.navigation.button(0).setChecked(True)
        self.navigation.idClicked.connect(self.navigate)
        header.addStretch(1)
        self.state_chip = label('Idle', 'stateChip')
        header.addWidget(self.state_chip)
        shell.addWidget(masthead)
        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self.pages.addWidget(self.capture_page())
        self.workspace_page, self.workspace_layout = self.page('Workspace')
        self.pages.addWidget(self.workspace_page)
        self.pages.addWidget(self.settings_page())
        self.pages.addWidget(self.diagnostics_page())
        self.statusBar().addPermanentWidget(label(paths.version, 'muted'))

        default = paths.profiles / 'default.json'
        if default.is_file():
            self.open_profile(default)
        self.shortcuts = []
        self.install_shortcuts()
        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self.refresh_capture)
        self.poll.start()
        self.refresh_capture()

    @staticmethod
    def page(title, subtitle=''):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 14, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(label(title, 'pageTitle'))
        if subtitle:
            layout.addWidget(label(subtitle, 'muted'))
        return page, layout

    def capture_page(self):
        page, layout = self.page('Capture')
        content = QHBoxLayout()
        content.setSpacing(14)
        layout.addLayout(content, 1)
        live = QVBoxLayout()
        content.addLayout(live, 3)
        self.preview = CapturePreview()
        live.addWidget(self.preview, 1)
        self.preview_status = label('', 'muted')
        live.addWidget(self.preview_status)
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, name in (('capture_fps', 'Capture FPS'), ('inference_fps', 'Inference FPS'),
                          ('inference_ms', 'Inference time'), ('detections', 'Detections')):
            tile = QFrame()
            tile.setObjectName('metric')
            tile_layout = QVBoxLayout(tile)
            tile_layout.addWidget(label(name, 'metricName'))
            value = label('—', 'metricValue')
            tile_layout.addWidget(value)
            self.metrics[key] = value
            metrics.addWidget(tile, 1)
        live.addLayout(metrics)

        inspector = QFrame()
        inspector.setObjectName('panel')
        inspector.setMinimumWidth(315)
        controls = QVBoxLayout(inspector)
        controls.addWidget(label('Session setup', 'sectionTitle'))
        self.quick = {}
        for name in QUICK_FIELDS:
            row = SettingRow(name, getattr(self.settings, name), self.paths)
            self.quick[name] = row
            row.changed.connect(lambda name=name, row=row: self.quick_change(name, row))
            controls.addWidget(row)
        self.session_mode = QComboBox()
        self.session_mode.setAccessibleName('Session mode')
        self.session_mode.addItem('Observe only', True)
        self.session_mode.addItem('Use configured output', False)
        controls.addWidget(self.session_mode)
        self.start_button = button('Start capture', self.start_capture, True)
        self.stop_button = button('Stop', self.stop_capture)
        actions = QHBoxLayout()
        actions.addWidget(self.start_button, 1)
        actions.addWidget(self.stop_button)
        controls.addLayout(actions)
        controls.addWidget(button('All settings', lambda: self.navigate(2)))
        controls.addStretch(1)
        content.addWidget(inspector, 2)
        return page

    def settings_page(self):
        page, layout = self.page('Settings', 'Changes take effect the next time capture starts.')
        profile_bar = QHBoxLayout()
        self.profile_name = label('Default profile', 'sectionTitle')
        profile_bar.addWidget(self.profile_name, 1)
        profile_bar.addWidget(button('Open profile', self.choose_profile))
        profile_bar.addWidget(button('Save profile', self.save_current_profile))
        profile_bar.addWidget(button('Save as', lambda: self.save_current_profile(save_as=True)))
        profile_bar.addWidget(button('Calibrate crosshair', self.calibrate_crosshair))
        layout.addLayout(profile_bar)
        self.settings_panel = SettingsPanel(self.paths, self.settings)
        self.settings_panel.changed.connect(self.settings_changed)
        layout.addWidget(self.settings_panel, 1)
        return page

    def diagnostics_page(self):
        page, layout = self.page('Diagnostics')
        self.backend = label('No engine running', 'sectionTitle')
        layout.addWidget(self.backend)
        self.diagnostic_metrics = label('', 'muted')
        self.diagnostic_metrics.setWordWrap(True)
        layout.addWidget(self.diagnostic_metrics)
        self.hotkey_status = label('', 'muted')
        self.hotkey_status.setWordWrap(True)
        layout.addWidget(self.hotkey_status)
        api_bar = QHBoxLayout()
        self.api_status = label('Local API is stopped.', 'muted')
        api_bar.addWidget(self.api_status, 1)
        self.copy_api_token = button('Copy API token', self.copy_local_api_token)
        self.copy_api_token.setEnabled(False)
        api_bar.addWidget(self.copy_api_token)
        api_bar.addWidget(button('Apply API settings', self.apply_api_settings))
        layout.addLayout(api_bar)
        self.log = QPlainTextEdit()
        self.log.setAccessibleName('Engine log')
        self.log.setReadOnly(True)
        self.log.setPlaceholderText('Engine messages will appear here.')
        layout.addWidget(self.log, 1)
        return page

    def navigate(self, index):
        if index == 1 and self.workspace is None:
            from desktop.workspaces import WorkspacePanel

            self.workspace = WorkspacePanel(self.paths, self)
            self.workspace.apply_settings(self.settings)
            self.workspace_layout.addWidget(self.workspace, 1)
        if index == 2 and not self.settings_panel.rows:
            self.settings_panel.rebuild()
        self.navigation.button(index).setChecked(True)
        self.pages.setCurrentIndex(index)
        self.refresh_capture()

    def quick_change(self, name, row):
        try:
            setattr(self.settings, name, row.value())
        except ValueError:
            return
        other = self.settings_panel.rows.get(name)
        if other:
            other.set_value(getattr(self.settings, name))

    def settings_changed(self):
        for name, row in self.quick.items():
            row.set_value(getattr(self.settings, name))

    def edited_settings(self):
        for name, row in self.quick.items():
            setattr(self.settings, name, row.value())
        self.settings_panel.validate_visible()
        validate_settings(self.settings)
        return self.settings

    def choose_profile(self):
        if QApplication.activeModalWidget() is not None or not self.allow_discard():
            return
        directory = self.paths.profiles if self.paths.profiles.is_dir() else self.paths.root
        path, _ = QFileDialog.getOpenFileName(self, 'Open VaporLock profile', str(directory), 'Profiles (*.json)',
                                              options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.open_profile(Path(path))

    def open_profile(self, path, *, report_dialog=True):
        try:
            settings = read_profile(path, self.paths)
        except (OSError, ValueError) as error:
            if not report_dialog:
                raise
            self.show_error('Could not open profile', error)
            return
        self.profile_path, self.saved = path, asdict(settings)
        self.apply_settings(settings)
        self.profile_name.setText(path.stem)
        if hasattr(self, 'shortcuts'):
            self.install_shortcuts()
        return True

    def apply_settings(self, settings):
        self.settings = settings
        self.settings_panel.set_settings(settings)
        self.settings_changed()
        if self.workspace is not None:
            self.workspace.apply_settings(settings)

    def save_current_profile(self, *, save_as=False):
        try:
            settings = self.edited_settings()
            path = self.profile_path
            if path is None or save_as:
                self.paths.profiles.mkdir(parents=True, exist_ok=True)
                name, _ = QFileDialog.getSaveFileName(self, 'Save VaporLock profile',
                    str(self.paths.profiles / 'default.json'), 'Profiles (*.json)',
                    options=QFileDialog.Option.DontUseNativeDialog)
                if not name:
                    return
                path = Path(name)
                if path.suffix.lower() != '.json':
                    path = path.with_suffix('.json')
            settings.profile_name = path.stem
            save_profile(path, settings, self.paths)
            self.profile_path, self.saved = path, asdict(settings)
            self.profile_name.setText(path.stem)
            self.install_shortcuts()
            if self.api is not None and self.api.server.running:
                self.api.refresh_catalog()
        except (OSError, ValueError) as error:
            self.show_error('Could not save profile', error)
            return
        self.statusBar().showMessage(f'Saved {path.name}', 4000)

    def allow_discard(self):
        if not self.settings_panel.invalid and asdict(self.settings) == self.saved:
            return True
        choice = QMessageBox.question(self, 'Unsaved profile', 'Discard changes to this profile?',
                                      QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        return choice == QMessageBox.StandardButton.Discard

    def install_shortcuts(self):
        for shortcut in self.shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.shortcuts = []
        used = set()
        global_bindings = (
            (self.settings.hotkey_emergency_stop, self.stop_capture),
            (self.settings.hotkey_start_stop, self.toggle_capture),
            (self.settings.hotkey_toggle_target_lock, self.engine.trigger_manual_lock),
            (self.settings.hotkey_save_screenshot, self.save_screenshot),
            (self.settings.hotkey_next_profile, self.next_profile),
            (self.settings.hotkey_toggle_overlay, self.toggle_overlay),
            (self.settings.hotkey_calibration, self.calibrate_crosshair),
            *((getattr(self.settings, f'hotkey_profile_{slot}'),
               lambda slot=slot: self.select_profile_slot(slot)) for slot in (1, 2, 3)),
        )
        registered = self.hotkeys.bind(int(self.winId()), global_bindings) if self.hotkeys is not None else set()
        unavailable = ', '.join(dict.fromkeys(keys for keys, _ in global_bindings if keys and keys not in registered))
        self.hotkey_status.setText(f'Window-only shortcuts: {unavailable}' if unavailable else '')
        bindings = (
            ('Escape', self.stop_capture),
            ('Ctrl+F', lambda: (self.navigate(2), self.settings_panel.search.setFocus())),
            *((keys, callback) for keys, callback in global_bindings if keys not in registered),
        )
        for keys, callback in bindings:
            sequence = QKeySequence(keys)
            canonical = sequence.toString()
            if canonical and canonical not in used:
                shortcut = QShortcut(sequence, self)
                shortcut.activated.connect(callback)
                self.shortcuts.append(shortcut)
                used.add(canonical)

    def nativeEvent(self, event_type, message):
        if self.hotkeys is not None and event_type == b'windows_generic_MSG':
            action = self.hotkeys.action_for(message)
            if action is not None:
                QTimer.singleShot(0, action)
                return True, 0
        return super().nativeEvent(event_type, message)

    def next_profile(self):
        if QApplication.activeModalWidget() is not None:
            return
        paths = sorted(self.paths.profiles.glob('*.json'))
        if not paths or not self.allow_discard():
            return
        index = paths.index(self.profile_path) + 1 if self.profile_path in paths else 0
        self.open_profile(paths[index % len(paths)])

    def select_profile_slot(self, slot):
        if QApplication.activeModalWidget() is not None:
            return
        name = getattr(self.settings, f'hotkey_profile_{slot}_target').strip()
        if not name or Path(name).name != name:
            return
        path = self.paths.profiles / name
        if path.suffix != '.json':
            path = path.with_suffix('.json')
        if path.is_file() and self.allow_discard():
            self.open_profile(path)

    def toggle_capture(self):
        if self.engine.snapshot().state in ('starting', 'running', 'stopping'):
            self.stop_capture()
        else:
            self.start_capture()

    def stop_capture(self):
        self.engine.stop()

    def start_capture(self, *, report_dialog=True):
        if self.closing or QApplication.activeModalWidget() is not None:
            return
        try:
            settings = Settings(**asdict(self.edited_settings()))
            if settings.motion_compensation_enabled and settings.motion_compensation_mode in ('preset', 'combined'):
                from motion_compensation import find_motion_preset, load_motion_compensation

                library = self.paths.resolve(settings.motion_compensation_library)
                presets, _ = load_motion_compensation(library)
                preset = find_motion_preset(presets, settings.motion_compensation_preset)
                settings.motion_compensation_vertical = preset.vertical if preset else 0.0
                settings.motion_compensation_horizontal = preset.horizontal if preset else 0.0
            self.affinity_required = bool(settings.crosshair_enabled)
            if self.affinity_required:
                from desktop.capture_affinity import CaptureExclusionGuard, exclude_windows

                if not exclude_windows():
                    raise ValueError('VaporLock windows could not be excluded from crosshair capture.')
                if self.affinity_guard is None:
                    self.affinity_guard = CaptureExclusionGuard(self, lambda: self.affinity_required,
                                                                self.engine.stop)
            self.engine.start(settings, observe_only=self.session_mode.currentData(),
                              capture_excluded=self.affinity_required)
            self.session_settings = settings
            self.show_overlay = settings.desktop_overlay_enabled and settings.desktop_overlay_visible
        except (OSError, RuntimeError, ValueError) as error:
            self.affinity_required = False
            if not report_dialog:
                raise
            self.show_error('Could not start capture', error)
        self.refresh_capture()

    def save_screenshot(self):
        if QApplication.activeModalWidget() is not None:
            return
        frame = self.engine.snapshot().preview
        if frame is None:
            self.statusBar().showMessage('No preview frame is available.', 4000)
            return
        name, _ = QFileDialog.getSaveFileName(self, 'Save preview frame', 'capture.jpg', 'JPEG (*.jpg)',
                                             options=QFileDialog.Option.DontUseNativeDialog)
        if name:
            try:
                Path(name).write_bytes(frame.jpeg)
            except OSError as error:
                self.show_error('Could not save preview', error)

    def refresh_capture(self):
        snapshot = self.engine.snapshot(include_overlay=self.show_overlay)
        if self.api is not None and self.api.server.running:
            now = time.monotonic()
            if now >= self.api_snapshot_at:
                self.api.publish(snapshot)
                self.api_snapshot_at = now + .25
        active = snapshot.state in ('starting', 'running', 'stopping')
        if not active:
            self.affinity_required = False
            self.show_overlay = False
        self.state_chip.setText(snapshot.state.capitalize())
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(active and snapshot.state != 'stopping')
        self.session_mode.setEnabled(not active)
        provider = PROVIDER_LABELS.get(snapshot.provider, snapshot.provider)
        self.backend.setText(provider or 'No engine running')
        if self.pages.currentIndex() == 0:
            empty = 'Waiting for a preview frame…' if active else 'Start capture to see the live preview.'
            if active and self.session_settings is not None and not self.session_settings.preview_enabled:
                empty = 'Preview is disabled for this session.'
            self.preview.update_frame(snapshot.preview, empty)
            if snapshot.preview is None:
                preview_status = ''
            else:
                preview_status = ('Live preview' if time.monotonic() <= snapshot.preview.live_until else 'Last captured frame')
            if self.preview_status.text() != preview_status:
                self.preview_status.setText(preview_status)
            for key, value in self.metrics.items():
                number = snapshot.stats.get(key)
                shown = '—' if not isinstance(number, (int, float)) else (
                    f'{number:.1f} ms' if key.endswith('_ms') else
                    str(number) if key == 'detections' else f'{number:.1f}'
                )
                if value.text() != shown:
                    value.setText(shown)
        if self.show_overlay and snapshot.overlay and self.session_settings is not None and sys.platform == 'win32':
            identity = tuple(snapshot.overlay.get(key) for key in (
                'session', 'source_generation', 'frame_id', 'config_revision', 'source_boundary',
            ))
            if self.overlay is None:
                from desktop.overlay import CaptureOverlay

                self.overlay = CaptureOverlay()
            try:
                if identity != self.overlay_identity:
                    self.overlay.show_observation(snapshot.overlay, self.session_settings)
                    self.overlay_identity = identity
            except OSError as error:
                self.show_overlay = False
                self.statusBar().showMessage(str(error), 10000)
        elif self.overlay is not None:
            self.overlay.hide()
            self.overlay_identity = None
        if self.pages.currentIndex() == 3 and snapshot.messages != self.last_messages:
            self.last_messages = snapshot.messages
            self.log.setPlainText('\n'.join(snapshot.messages))
            scrollbar = self.log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        if self.pages.currentIndex() == 3:
            parts = []
            for key, title in (('capture_ms', 'Capture'), ('preprocess_ms', 'Preprocessing'),
                               ('model_ms', 'Model'), ('postprocess_ms', 'Postprocessing'),
                               ('tracking_ms', 'Tracking')):
                value = snapshot.stats.get(key)
                if isinstance(value, (int, float)):
                    parts.append(f'{title} {value:.2f} ms')
            summary = '  ·  '.join(parts)
            if summary != self.diagnostic_metrics.text():
                self.diagnostic_metrics.setText(summary)
        if snapshot.error != self.last_error:
            self.last_error = snapshot.error
            if snapshot.error:
                self.statusBar().showMessage(snapshot.error, 10000)

    def show_error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def apply_api_settings(self):
        if self.closing:
            return
        try:
            settings = self.edited_settings()
            if not settings.local_api_enabled:
                if self.api is not None:
                    self.api.server.request_stop_async()
                self.api_status.setText('Local API is stopped.')
                self.copy_api_token.setEnabled(False)
                return
            if settings.local_api_lan_enabled:
                answer = QMessageBox.question(self, 'Enable LAN API',
                    'Allow authenticated local network clients to control VaporLock over HTTPS?')
                if answer != QMessageBox.StandardButton.Yes:
                    return
            if self.api is None:
                from desktop.local_api import DesktopApi

                self.api = DesktopApi(self)
            started, detail = self.api.start(settings)
            self.api_status.setText(detail)
            self.copy_api_token.setEnabled(started)
        except (OSError, RuntimeError, ValueError) as error:
            self.show_error('Could not apply API settings', error)

    def copy_local_api_token(self):
        if self.api is not None and self.api.server.running:
            QApplication.clipboard().setText(self.api.server.token)
            self.statusBar().showMessage('Local API token copied.', 4000)

    def calibrate_crosshair(self):
        if QApplication.activeModalWidget() is not None:
            return
        if self.engine.snapshot(include_overlay=False).state in ('starting', 'running', 'stopping'):
            self.statusBar().showMessage('Stop capture before calibrating the crosshair.', 5000)
            return
        try:
            from desktop.calibration import calibrate

            result = calibrate(self, self.edited_settings())
        except (OSError, RuntimeError, ValueError) as error:
            self.show_error('Could not calibrate crosshair', error)
            return
        if result is not None:
            self.settings.crosshair_calibration = result
            self.settings_panel.set_settings(self.settings)
            self.statusBar().showMessage('Crosshair calibration saved to the current settings.', 5000)

    def toggle_overlay(self):
        if self.session_settings is not None and self.session_settings.desktop_overlay_enabled:
            self.show_overlay = not self.show_overlay
            self.refresh_capture()

    def closeEvent(self, event):
        if not self.closing:
            if (self.workspace is not None and not self.workspace.can_close()) or not self.allow_discard():
                event.ignore()
                return
            self.closing = True
            self.centralWidget().setEnabled(False)
        if self.api is not None:
            self.api.server.request_stop_async()
            if not self.api.server.cleanup_complete():
                self.engine.stop()
                self.statusBar().showMessage('Waiting for the local API to stop…')
                event.ignore()
                QTimer.singleShot(100, self.close)
                return
        if not self.engine.close(timeout=0):
            self.statusBar().showMessage('Waiting for capture to stop…')
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        if self.workspace is not None:
            self.workspace.close_project()
        if self.affinity_guard is not None:
            self.affinity_guard.close()
        if self.hotkeys is not None:
            self.hotkeys.close()
        if self.overlay is not None:
            self.overlay.close()
        super().closeEvent(event)


def main():
    paths = ProductPaths.discover()
    probe = len(sys.argv) == 3 and sys.argv[1] == '--verify-qt-startup'
    if probe:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication(sys.argv[:1] if probe else sys.argv)
    app.setApplicationName('VaporLock')
    app.setStyle('Fusion')
    if app.platformName() == 'offscreen' and sys.platform == 'win32':
        fonts = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Fonts'
        for name in ('segoeui.ttf', 'segoeuib.ttf'):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    app.setStyleSheet(STYLESHEET)
    if probe:
        window = DesktopWindow(paths, global_shortcuts=False)
        window.show()
        app.processEvents()
        image = window.grab()
        Path(sys.argv[2]).write_text(json.dumps({
            'version': paths.version, 'platform': sys.platform,
            'rendered': not image.isNull(), 'pages': window.pages.count(),
        }), encoding='utf-8')
        window.close()
        return 0 if not image.isNull() else 1
    guard = None
    if os.name == 'nt':
        from single_instance import InstanceGuard, focus_existing

        try:
            guard = InstanceGuard(paths.root, 'premium')
        except OSError as error:
            QMessageBox.critical(None, 'VaporLock', str(error))
            return 1
        if not guard.primary:
            guard.close()
            return 0
    try:
        window = DesktopWindow(paths)
        if guard is not None:
            focus_timer = QTimer(window)
            focus_timer.setInterval(250)
            focus_timer.timeout.connect(lambda: focus_existing(app, window) if guard.take_focus_request() else None)
            focus_timer.start()
        window.show()
        return app.exec()
    finally:
        if guard is not None:
            guard.close()
