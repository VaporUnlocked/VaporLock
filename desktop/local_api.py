"""Connect the optional authenticated control service to the desktop."""

from dataclasses import asdict
from pathlib import Path
import secrets

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from local_control_api import LocalControlApi
from model_discovery import discover_entries

from .settings import PROFILE_PATH_FIELDS, SETTING_ENUMS, Settings, validate_settings


PRIVATE_FIELDS = frozenset(PROFILE_PATH_FIELDS) | {
    'crosshair_enabled', 'crosshair_calibration', 'inference_cpu_placement', 'exclusion_zones',
    'model_class_aliases', 'training_extra_arguments', 'training_platform_project',
    'hardware_serial_port', 'kmbox_net_address', 'kmbox_net_device_code', 'kmbox_a_library_path',
    'inference_dml_device', 'inference_cuda_device', 'inference_trt_device', 'dataset_annotation_batch',
    'dataset_annotation_additive',
}
METRICS = ('capture_fps', 'inference_fps', 'output_fps', 'capture_ms', 'inference_ms',
           'preprocess_ms', 'tracking_ms', 'tracks', 'selected_track', 'selected_class', 'output_active')


class DesktopApi(QObject):
    command_received = Signal(dict)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.server = LocalControlApi(self.command_received.emit, token=secrets.token_urlsafe(32))
        self.command_received.connect(self.dispatch)
        self.entries = ()
        self.last_snapshot = None

    def start(self, settings):
        if not self.server.running and not self.server.cleanup_complete():
            return False, 'The previous local API session is still stopping.'
        self.last_snapshot = None
        self.refresh_catalog()
        return self.server.start(allow_lan=settings.local_api_lan_enabled, port=settings.local_api_port)

    def refresh_catalog(self):
        paths = self.window.paths
        self.entries, _ = discover_entries(paths.root / 'models', paths.profiles)

    def publish(self, engine):
        if not self.server.running:
            return
        options = {key: value for key, value in asdict(self.window.settings).items()
                   if key not in PRIVATE_FIELDS and not key.startswith(('ultralytics_', 'cloud_', 'local_api_', 'hotkey_'))}
        profiles = [{'id': name, 'name': path.stem} for kind, name, path in self.entries if kind == 'Profile']
        models = [{'id': name.replace('\\', '/'), 'name': path.name}
                  for kind, name, path in self.entries if kind == 'Model']
        state = {
            'status': {'version': self.window.paths.version, 'state': engine.state, 'provider': engine.provider,
                       'active': engine.state in ('starting', 'running', 'stopping'),
                       'session_mode': 'observe' if engine.observe_only else 'control',
                       'metrics': {key: engine.stats[key] for key in METRICS if key in engine.stats}},
            'settings': options, 'profiles': profiles, 'models': models,
            'catalog': {'profiles': profiles, 'models': models},
            'schema': {'protocol_version': '2', 'readable_settings': sorted(options),
                       'writable_settings': sorted(set(options) - {'profile_name'}),
                       'enum_values': {key: values for key, values in SETTING_ENUMS.items() if key in options},
                       'session_actions': ['start', 'stop', 'toggle', 'set-mode'],
                       'session_modes': ['control', 'observe'],
                       'profile_actions': ['load'], 'model_actions': ['select', 'clear', 'refresh'],
                       'actions': ['emergency-stop', 'toggle-overlay', 'toggle-target-lock']},
        }
        if state != self.last_snapshot:
            self.server.update_snapshot(state)
            self.last_snapshot = state

    def dispatch(self, command):
        try:
            self.apply(command['type'], command['payload'])
        except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            result = {'state': 'rejected', 'error': str(error)[:512]}
        else:
            result = {'state': 'dispatched'}
        self.server.finish_command(command['command_id'], result)

    def apply(self, kind, payload):
        window = self.window
        action = payload.get('action')
        if (kind, action) in (('action', 'emergency-stop'), ('session', 'stop')):
            window.stop_capture()
            return
        if not self.server.running or QApplication.activeModalWidget() is not None:
            raise ValueError('The desktop cannot accept this command right now.')
        if window.closing:
            raise ValueError('The desktop is closing.')
        if kind == 'session' and action in ('start', 'toggle', 'set-mode'):
            active = window.engine.snapshot(include_overlay=False).state in ('starting', 'running', 'stopping')
            mode = payload.get('mode')
            if mode is not None:
                if active or mode not in ('observe', 'control'):
                    raise ValueError('The session mode can be changed before capture starts.')
                window.session_mode.setCurrentIndex(0 if mode == 'observe' else 1)
            elif action == 'set-mode':
                raise ValueError('Choose Observe or Control.')
            if action == 'toggle' and active:
                window.stop_capture()
            elif action in ('start', 'toggle') and not active:
                window.start_capture(report_dialog=False)
            elif action == 'start':
                raise ValueError('A capture session is already active.')
            return
        if kind == 'action' and action == 'toggle-overlay':
            window.toggle_overlay()
            return
        if kind == 'action' and action == 'toggle-target-lock':
            window.engine.trigger_manual_lock()
            return
        if kind == 'settings':
            updates = payload.get('values')
            writable = self.server.snapshot().get('schema', {}).get('writable_settings', ())
            if not isinstance(updates, dict) or len(updates) > 128 or any(key not in writable for key in updates):
                raise ValueError('One or more settings are unavailable through the API.')
            options = asdict(window.edited_settings())
            options.update(updates)
            settings = Settings(**options)
            validate_settings(settings)
            window.apply_settings(settings)
            return
        if kind == 'model' and action in ('select', 'clear', 'refresh'):
            if action == 'refresh':
                self.refresh_catalog()
            else:
                window.edited_settings()
                selected = self.catalog_path('Model', payload.get('id')) if action == 'select' else ''
                window.settings.model_path = window.paths.portable(str(selected))
                window.settings_panel.set_settings(window.settings)
                window.settings_changed()
            return
        if kind == 'profile' and action == 'load':
            if asdict(window.settings) != window.saved or window.settings_panel.invalid:
                raise ValueError('Save or discard the open profile changes first.')
            window.open_profile(self.catalog_path('Profile', payload.get('id')), report_dialog=False)
            return
        raise ValueError('Unsupported desktop API command.')

    def catalog_path(self, category, identity):
        for kind, name, path in self.entries:
            if kind == category and name.replace('\\', '/') == identity and Path(path).is_file():
                return path
        raise ValueError('The requested catalog entry is unavailable.')
