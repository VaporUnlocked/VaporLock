"""Real ControlCenter adapter for the reviewed first live slice.

All effects use the single supplied ControlCenter. No engine service, profile
field, path, epoch or license capability is accepted from the renderer. Qt source
is not imported in disconnected tests; actual runtime evidence remains separate.
"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QFileDialog

from vlui_live_commands import NativeCommandStamp
from vlui_live_context import LiveContextProducer, NativeSource, canonical
from vlui_live_protocol import ROLES, valid_display_label
from vlui_native_injection import native_injection, writable_native_path
from vlui_profile_transition import StopBeforeReplace
from vlui_profile_snapshot import read_profile_snapshot


_DISPLAY_ONLY = frozenset(('setLocale', 'setTextScale', 'setInterfaceMode', 'setPaletteColor',
                          'setPaletteOpacity', 'resetPalette', 'setProfileName', 'openSettingsGuide',
                          'saveProfile', 'saveProfileAs', 'selectProfile', 'openProfile', 'resetSettings',
                          'undoTuning', 'setCaptureRate', 'setNumericSetting', 'setDiscreteSetting'))
_GROUPS = ('local-training', 'training-status', 'model-inspection', 'provider-benchmark',
           'official-models', 'data-collection', 'data-review')


def _canonical_native_palette(palette):
    """Canonicalize native RGB output, never parse renderer/profile authority."""
    if type(palette) is not dict or set(palette) != ROLES:
        raise ValueError('PREFERENCES_REJECTED')
    result = {}
    for role, color in palette.items():
        if (type(color) is not str or len(color) != 7 or color[0] != '#'
                or any(char not in '0123456789abcdefABCDEF' for char in color[1:])):
            raise ValueError('PREFERENCES_REJECTED')
        result[role] = color.lower()
    return result


class NativeLiveOwner(QObject):
    def __init__(self, center):
        super().__init__(center)
        # Import the actual same module only after ControlCenter construction has
        # begun; this is not a second Settings/controller implementation.
        import vapor_lock_gui as native
        self._native, self._center = native, center
        self._source_revision = self._display_revision = self._operation_epoch = self._selection_epoch = 1
        self._authorization_revision = self._cancellation_epoch = self._settings_revision = 1
        self._fingerprint = self._mutation_fingerprint = self._semantic = self._authority = self._targets = None
        self._model_keys, self._profile_keys = {}, {}
        self._catalog = {}
        self._profile_target = self._model_target = None
        self._model_selection_signature = None
        self._capture_session = object()
        self._last_start_intent = getattr(center, '_start_intent_generation', 0)
        self._transition = None
        self._transition_lane = None
        self._transition_failure = None
        self._notifier = None
        self._notification_pending = False
        self._scale = self._read_scale()
        # The .cloud suite is native-only. Its local paths/recipes do not add
        # renderer controls or expand the existing 251-field HTML contract.
        field_specs = tuple((name, str(field.type)) for name, field in native.Settings.__dataclass_fields__.items()
                            if not name.startswith('cloud_'))
        self.producer = LiveContextProducer(read_native_source=self.read_native_source, field_specs=field_specs)
        self.handlers = {'setLocale': self.set_locale, 'setInterfaceMode': self.set_interface,
            'setPaletteColor': self.set_color, 'setPaletteOpacity': self.set_opacity,
            'resetPalette': self.reset_palette, 'setProfileName': self.set_profile_name,
            'selectProfile': self.select_profile, 'openProfile': self.open_profile,
            'saveProfile': self.save_profile, 'saveProfileAs': self.save_profile_as,
            'resetSettings': self.reset_settings, 'undoTuning': self.undo,
            'setCaptureRate': self.set_capture_rate, 'setTextScale': self.set_scale,
            'setNumericSetting': self.set_numeric_setting, 'setDiscreteSetting': self.set_discrete_setting,
            'openSettingsGuide': self.open_settings_guide, 'removeVirtualMouse': self.remove_virtual_mouse}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(25)
        self._poll_timer.timeout.connect(self._poll_transition)

    def _read_scale(self):
        from localization import preference_path
        try:
            raw = json.loads(preference_path().read_text(encoding='utf-8'))
            return raw.get('textScale') if type(raw) is dict and raw.get('textScale') in ('1', '1.5', '2') else '1'
        except (OSError, ValueError):
            return '1'

    def bind_display_notifier(self, callback):
        self._notifier = callback

    def native_changed(self):
        # Coalesce native/API/hotkey/compatibility edits, not private semantic
        # revisions. Reading the source synchronously also detects unsignaled edits.
        if self._notifier is not None and not self._notification_pending:
            self._notification_pending = True
            QTimer.singleShot(0, self._notify)

    def _notify(self):
        self._notification_pending = False
        if self._notifier is not None:
            self._notifier()

    def _label(self, value, fallback):
        return value if valid_display_label(value) else self._center.t(fallback)

    def _native_model(self, settings):
        raw = settings['model_path']
        if not raw:
            return 'none', None, None
        try:
            path = writable_native_path(self._native.resolve_product_path(raw))
            info = path.stat()
            if not path.is_file() or path.suffix.lower() != '.onnx':
                return 'unknown', None, None
            key = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if key not in self._model_keys:
                if len(self._model_keys) >= 4096:
                    return 'unknown', None, None
                self._model_keys[key] = object()
            return 'known', self._model_keys[key], self._label(path.stem, 'Selected model')
        except (OSError, ValueError):
            return 'unknown', None, None

    def _native_catalog(self):
        center = self._center
        catalog, mapping = [], {}
        try:
            paths = center.local_profile_files()
            if len(paths) > 128:
                self._profile_target = None
                return [], {}, {'state': 'unknown', 'displayId': None, 'name': None,
                               'dirty': center.dirty, 'undoAvailable': bool(center._undo_history)}
            active = str(center._active_profile_path.resolve()) if center._active_profile_path is not None else None
            for path in paths:
                path = writable_native_path(path.resolve())
                target = str(path)
                if target not in self._profile_keys:
                    if len(self._profile_keys) >= 4096:
                        raise ValueError('PROFILE_CATALOG_FULL')
                    self._profile_keys[target] = (object(), str(uuid4()))
                private_key, display_id = self._profile_keys[target]
                name = self._label(center.settings.profile_name if target == active else path.stem, 'Profile')
                catalog.append({'displayId': display_id, 'name': name})
                mapping[display_id] = (path, private_key)
            profile = {'state': 'none' if active is None else 'unknown', 'displayId': None, 'name': None,
                       'dirty': center.dirty, 'undoAvailable': bool(center._undo_history)}
            self._profile_target = None
            for item in catalog:
                path, key = mapping[item['displayId']]
                if str(path) == active:
                    profile.update(state='selected', displayId=item['displayId'], name=item['name'])
                    self._profile_target = key
                    break
            # The contract has no unknown catalog variant: any incomplete/overflow
            # catalog becomes unknown profile + empty list, never a truncated lie.
            if profile['state'] == 'unknown':
                return [], {}, profile
            return catalog, mapping, profile
        except (OSError, ValueError):
            self._profile_target = None
            return [], {}, {'state': 'unknown', 'displayId': None, 'name': None,
                           'dirty': center.dirty, 'undoAvailable': bool(center._undo_history)}

    def _status(self):
        center = self._center
        if center._policy_start_pending or center.xbox_compatible_bridge.preparing:
            state = 'starting'
        elif center.engine.active:
            state = 'running' if center.engine.session_running else 'starting'
            if center._last_engine_state == 'Stopping':
                state = 'stopping'
        else:
            state = 'idle'
        return {'state': state, 'startPending': bool(center._policy_start_pending)}

    def read_native_source(self):
        center = self._center
        settings = {key:value for key,value in asdict(center.settings).items() if not key.startswith('cloud_')}
        model_state, model_key, model_label = self._native_model(settings)
        selection_signature = (settings['model_path'], model_state, model_key)
        if selection_signature != self._model_selection_signature:
            self._model_target = object() if settings['model_path'] else None
            self._model_selection_signature = selection_signature
        catalog, self._catalog, profile = self._native_catalog()
        from pipeline_tools import pipeline_projection
        prefs = {'pipeline': pipeline_projection(center.pipeline_display_metrics()), 'locale': center.ui_language, 'textScale': self._scale, 'profile': profile,
            'profileName': self._label(settings['profile_name'], 'Profile'),
            'catalog': catalog, 'appearance': {'mode': center.ui_appearance.layout,
                'colors': _canonical_native_palette(self._native.appearance_palette(center.ui_appearance, True)),
                'opacities': dict(center.ui_appearance.opacities)},
            'captureRate': settings['capture_fps'], 'nativeStatus': self._status(),
            'discrete': center._discrete_registry.projection(settings, free=False,
                denied=False),
            'numeric': center._numeric_registry.projection(settings, free=False,
                denied=False),
            'coverage': {group: 'partial' if group in ('model-inspection', 'provider-benchmark', 'official-models')
                         else 'unconnected' for group in _GROUPS}}
        semantic = canonical({key: value for key, value in settings.items() if key != 'profile_name'})
        fingerprint = (canonical(settings), canonical(prefs), model_state, model_key, model_label)
        native_cloud = tuple((key,canonical(value)) for key,value in sorted(asdict(center.settings).items()) if key.startswith('cloud_'))
        mutation_fingerprint = (canonical(settings), native_cloud, canonical(prefs['appearance']), canonical(profile),
                                canonical(catalog), prefs['locale'], prefs['textScale'], model_state, model_key)
        targets = self._profile_target, self._model_target
        authority = (native_injection() is not None, self._cancellation_epoch, center._native_closing,
                      getattr(center, 'session_mode', 'control'), center._numeric_registry.signature(), center._discrete_registry.signature())
        if ((self._fingerprint is not None and fingerprint != self._fingerprint)
                or (self._authority is not None and authority != self._authority)):
            self._source_revision += 1
            self._display_revision += 1
        if self._mutation_fingerprint is not None and mutation_fingerprint != self._mutation_fingerprint:
            self._settings_revision += 1
        if self._semantic is not None and (semantic != self._semantic or targets[1] is not self._targets[1]):
            self._operation_epoch += 1
        if self._targets is not None and targets != self._targets:
            self._selection_epoch += 1
        if self._authority is not None and authority != self._authority:
            self._authorization_revision += 1
        self._fingerprint, self._semantic, self._targets, self._authority = fingerprint, semantic, targets, authority
        self._mutation_fingerprint = mutation_fingerprint
        # Explicit native summaries, never QWidget text scraping or inspection facts.
        # Credential changes affect private revisions above, but neither the
        # plaintext nor encrypted profile value enters producer/display data.
        settings['ultralytics_key_protected'] = ''
        return NativeSource(self._source_revision, self._display_revision, self._operation_epoch,
            self._selection_epoch, settings, model_state, model_key, model_label, prefs,
            {'input': None, 'resize': self._label(center.t(settings['resize_mode']), 'Unknown'),
             'output': self._label(center.t(settings['model_output_format']), 'Unknown'),
             'coordinates': self._label(center.t(settings['model_coordinate_units']), 'Unknown')},
            {'source': self._label(center.t('Configured monitor'), 'Unknown'),
             'region': f"{settings['region_width']} × {settings['region_height']}",
             'frameRate': f"{settings['capture_fps']} fps"},
            {'configuration': center.t('Current configuration'), 'input': center.t('Input settings'),
             'capture': center.t('Capture settings')})

    def stamp(self):
        self.read_native_source()
        return NativeCommandStamp(self._settings_revision, self._authorization_revision,
            self._cancellation_epoch, self._selection_epoch, self._profile_target, self._model_target)

    def authorize(self, name):
        if getattr(self._center, '_tuning_locked', False) and name in {
                'selectProfile', 'openProfile', 'resetSettings', 'undoTuning',
                'setCaptureRate', 'setNumericSetting', 'setDiscreteSetting', 'setProfileName'}:
            return False
        # Appearance is Free+Premium. Native engine readiness is never
        # granted by these edit commands or by a disposable fixture.
        if name in ('openSettingsGuide', 'removeVirtualMouse') and (native_injection() is not None
                or getattr(self._center, '_manual_services', False)):
            return False
        return (not self._center._native_closing and name in self.handlers
                and (name in _DISPLAY_ONLY or not False))

    def background_conflict(self):
        center = self._center
        if center.model_probe_busy() or center.acceleration.active or center._calibration_active:
            return True
        for name in ('_training_worker', '_dataset_worker', '_cloud_training_job', '_cloud_model_check_job'):
            process = getattr(center, name, None)
            if process is not None and process.state() != self._native.QProcess.ProcessState.NotRunning:
                return True
        return False

    def _effect(self, command, transaction, lane, callback, success='OK', failure='IO_ERROR'):
        if self._transition is not None:
            lane.finish(transaction, status='rejected', code='BUSY')
            return
        if not lane.may_effect(transaction):
            lane.finish(transaction, status='rejected', code='STALE')
            return
        committed = callback() is True
        self.native_changed()
        lane.finish(transaction, code=success if committed else failure,
                    status='completed' if committed else 'failed', effect_committed=committed)

    def open_settings_guide(self, command, transaction, lane):
        self._effect(command, transaction, lane, self._center.open_settings_guide,
                     success='GUIDE_SENT', failure='GUIDE_FAILED')

    def _numeric_effect(self, transaction, lane, apply):
        if self._transition is not None:
            lane.finish(transaction, status='rejected', code='BUSY')
            return
        if not lane.may_effect(transaction):
            lane.finish(transaction, status='rejected', code='STALE')
            return
        outcome = apply(lambda: lane.may_effect(transaction))
        committed = outcome == 'OK'
        self.native_changed()
        lane.finish(transaction, status='completed' if committed else 'rejected',
                    code=outcome, effect_committed=committed)

    def remove_virtual_mouse(self, command, transaction, lane):
        if not lane.may_effect(transaction) or not self.authorize('removeVirtualMouse'):
            lane.finish(transaction, status='rejected', code='DENIED'); return
        outcome = self._center.remove_virtual_mouse_component(native_guard=lambda:
            self.authorize('removeVirtualMouse') and lane.may_effect(transaction))
        self.native_changed()
        status = ('completed' if outcome == 'MOUSE_REMOVAL_STARTED' else 'cancelled' if outcome == 'CANCELLED'
                  else 'rejected' if outcome in ('BUSY', 'STALE') else 'failed')
        lane.finish(transaction, status=status, code=outcome if outcome in
            ('MOUSE_REMOVAL_STARTED', 'CANCELLED', 'BUSY', 'STALE') else 'MOUSE_REMOVAL_FAILED',
            effect_committed=outcome == 'MOUSE_REMOVAL_STARTED')

    def set_numeric_setting(self, command, transaction, lane):
        self._numeric_effect(transaction, lane, lambda guard: self._center._numeric_registry.apply(
            command.payload['fieldId'], command.payload['value'], command.payload['input'],
            free=False,
            denied=False, guard=guard))

    def set_discrete_setting(self, command, transaction, lane):
        self._numeric_effect(transaction, lane, lambda guard: self._center._discrete_registry.apply(
            command.payload['bindingId'], command.payload['value'],
            free=False,
            denied=False, guard=guard))

    def _persist_interface(self, *, locale=None, scale=None):
        from localization import preference_path
        path = writable_native_path(preference_path())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if type(value) is not dict: value = {}
            except (OSError, ValueError): value = {}
            value.update(language=locale or self._center.ui_language, textScale=scale or self._scale)
            temporary = writable_native_path(path.with_suffix('.tmp'))
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            writable_native_path(path)
            temporary.replace(path)
            return True
        except OSError:
            return False

    def set_locale(self, command, transaction, lane):
        def apply():
            if not self._persist_interface(locale=command.payload['locale']): return False
            self._center.ui_language = command.payload['locale']
            self._center.apply_interface_language()
            return True
        self._effect(command, transaction, lane, apply)

    def set_scale(self, command, transaction, lane):
        def apply():
            if not self._persist_interface(scale=command.payload['scale']): return False
            self._scale = command.payload['scale']
            self._center._live_coordinator.apply_scale(self._scale)
            return True
        self._effect(command, transaction, lane, apply)

    def _appearance(self, command, transaction, lane, change):
        def apply():
            replacement = deepcopy(self._center.ui_appearance)
            change(replacement)
            if not self._native.save_ui_appearance(replacement): return False
            self._center.ui_appearance = replacement
            self._center.apply_ui_appearance(save=False)
            return True
        self._effect(command, transaction, lane, apply)

    def set_interface(self, command, transaction, lane):
        if command.payload['mode'] != 'smoky':
            lane.finish(transaction, status='rejected', code='DENIED')
            return
        def change(appearance):
            appearance.custom_palettes[appearance.layout] = {'colors': dict(appearance.colors), 'opacities': dict(appearance.opacities)}
            saved = appearance.custom_palettes.get('smoky', {})
            appearance.layout = 'smoky'
            appearance.colors = {role: self._native.normalized_ui_color(saved.get('colors', {}).get(role), fallback)
                for role, fallback in self._native.theme_for('smoky').palette().items()}
            appearance.opacities = {role: self._native.normalized_ui_opacity(saved.get('opacities', {}).get(role), 100)
                for role in self._native.DEFAULT_UI_OPACITIES}
        self._appearance(command, transaction, lane, change)

    def set_color(self, command, transaction, lane):
        self._appearance(command, transaction, lane,
            lambda value: value.colors.__setitem__(command.payload['role'], command.payload['color']))

    def set_opacity(self, command, transaction, lane):
        self._appearance(command, transaction, lane,
            lambda value: value.opacities.__setitem__(command.payload['role'], command.payload['opacity']))

    def reset_palette(self, command, transaction, lane):
        def change(appearance):
            appearance.colors = self._native.theme_for(appearance.layout).palette()
            appearance.opacities = dict(self._native.DEFAULT_UI_OPACITIES)
        self._appearance(command, transaction, lane, change)

    def set_profile_name(self, command, transaction, lane):
        def apply():
            self._center.change('profile_name', command.payload['name'])
            return self._center.settings.profile_name == command.payload['name']
        self._effect(command, transaction, lane, apply)

    def set_capture_rate(self, command, transaction, lane):
        self._numeric_effect(transaction, lane, lambda guard: self._center._numeric_registry.apply_capture_rate(
            command.payload['fps'], free=False,
            denied=False, guard=guard))

    def undo(self, command, transaction, lane):
        if self.background_conflict() or not self._center._undo_history:
            lane.finish(transaction, status='rejected', code='BUSY' if self.background_conflict() else 'DENIED')
            return
        def apply(guard):
            if not guard(): return 'STALE'
            return 'OK' if self._center.undo_tuning() else 'DENIED'
        self._numeric_effect(transaction, lane, apply)

    def _save(self, command, transaction, lane, path, code):
        self._effect(command, transaction, lane,
            lambda: self._center.write_profile(path, native_guard=lambda: lane.may_effect(transaction)), code)

    def save_profile(self, command, transaction, lane):
        self._native.PROFILE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        self._save(command, transaction, lane, self._native.PROFILE_DIRECTORY / 'default.json', 'SAVED_DEFAULT')

    def save_profile_as(self, command, transaction, lane):
        if not lane.may_effect(transaction):
            lane.finish(transaction, status='rejected', code='STALE'); return
        path, _filter = QFileDialog.getSaveFileName(self._center, self._center.t('Save Vapor Lock profile'),
            str(self._native.PROFILE_DIRECTORY / 'new-profile.json'), 'JSON profiles (*.json)')
        if not path:
            lane.finish(transaction, status='cancelled', code='CANCELLED'); return
        self._save(command, transaction, lane, Path(path).with_suffix('.json'), 'SAVED_AS')

    def select_profile(self, command, transaction, lane):
        entry = self._catalog.get(command.payload['profileId'])
        if entry is None:
            lane.finish(transaction, status='rejected', code='INVALID'); return
        self._load(entry[0], transaction, lane)

    def open_profile(self, command, transaction, lane):
        if not lane.may_effect(transaction):
            lane.finish(transaction, status='rejected', code='STALE'); return
        path, _filter = QFileDialog.getOpenFileName(self._center, self._center.t('Open Vapor Lock profile'),
            str(self._native.PROFILE_DIRECTORY), 'JSON profiles (*.json)')
        if not path:
            lane.finish(transaction, status='cancelled', code='CANCELLED'); return
        self._load(Path(path), transaction, lane)

    def _load(self, path, transaction, lane):
        if not lane.may_effect(transaction):
            lane.finish(transaction, status='rejected', code='STALE'); return
        try:
            path = writable_native_path(path.resolve())
            snapshot = read_profile_snapshot(path)
            replacement = self._center.prepare_profile_bytes(snapshot.data)
            if not snapshot.matches_path(path):
                raise ValueError('PROFILE_CHANGED')
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            lane.finish(transaction, status='failed', code='IO_ERROR'); return
        def current():
            unchanged = snapshot.matches_path(path)
            return unchanged and lane.may_effect(transaction)
        self._replace(replacement, path, current, lambda code, committed: lane.finish(transaction,
            status='completed' if committed else 'cancelled' if code == 'CANCELLED' else 'failed' if code == 'IO_ERROR' else 'rejected',
            code=code, effect_committed=committed), lane)

    def reset_settings(self, command, transaction, lane):
        path = self._native.PROFILE_DIRECTORY / 'default.json'
        if path.exists():
            self._load(path, transaction, lane)
        else:
            self._replace(self._native.Settings(), None, lambda: lane.may_effect(transaction),
                lambda code, committed: lane.finish(transaction, status='completed' if committed else
                    'failed' if code == 'IO_ERROR' else 'cancelled' if code == 'CANCELLED' else 'rejected',
                    code=code, effect_committed=committed), lane)

    def _replace(self, replacement, path, current, completed, lane=None):
        if self._transition is not None:
            completed('BUSY', False); return False
        center = self._center
        center._profile_transition_pending = True
        self._transition_lane = lane
        def cancel_start():
            center._start_intent_generation = getattr(center, '_start_intent_generation', 0) + 1
            center._policy_start_pending = False
            center._policy_start_generation = None
            center._pending_controller_start = None
            self._last_start_intent = center._start_intent_generation
        def stopped(session):
            return (session is self._capture_session and not center.engine.active
                and not center.xbox_compatible_bridge.preparing and center._pending_controller_start is None
                and center.xbox_compatible_bridge.safely_stopped()
                and not center._policy_start_pending and getattr(center.engine, '_controller_host_session', None) is None)
        def commit():
            center.commit_profile_replacement(replacement, path)
            return center.settings is replacement
        def finished(code, committed):
            self._poll_timer.stop()
            self._transition = self._transition_lane = None
            self._transition_failure = None
            center._profile_transition_pending = False
            self.native_changed()
            completed(code, committed)
        self._transition = StopBeforeReplace(may_effect=current, conflict=self.background_conflict,
            capture_session=lambda: self._capture_session, cancel_pending_start=cancel_start,
            request_stop=lambda session: center.stop_capture(_profile_transition=True) if session is self._capture_session else None,
            safely_stopped=stopped, commit=commit, completed=finished, clock=time.monotonic)
        def failed_callback():
            committed = center.settings is replacement
            finished('OK' if committed else 'IO_ERROR', committed)
            center.log('WARN', 'Profile transition callback failed.')
        self._transition_failure = failed_callback
        pending = self._transition
        try:
            state = pending.begin()
        except Exception:
            # Stop may already have been requested. Release the mutation lane,
            # retain the real settings/session, and let subsequent observations
            # report the native state; never manufacture a replacement receipt.
            if self._transition is pending:
                failed_callback()
            return False
        if state == 'STOPPING': self._poll_timer.start()
        return state in ('STOPPING', 'COMMITTED')

    def _poll_transition(self):
        if self._transition is not None:
            pending = self._transition
            try:
                pending.poll()
            except Exception:
                if self._transition is pending:
                    self._transition_failure()

    def tuning_lock_changed(self):
        # Invalidate already admitted requests, including a lock/unlock cycle
        # completed before the next coalesced UI notification.
        self._cancellation_epoch += 1
        if self._transition is not None:
            self._transition.cancel()
        self.native_changed()

    def user_stop(self):
        self._cancellation_epoch += 1
        if self._transition is not None:
            self._transition.cancel()
        if self._center._live_coordinator is not None:
            self._center._live_coordinator.user_stop()
        self.native_changed()

    def capture_started(self):
        self._capture_session = object()
        self.native_changed()

    def load_native_profile(self, path, *, captured_stamp=None):
        if not self.authorize('openProfile'):
            return False
        try:
            stamp = captured_stamp or self.stamp()
            path = writable_native_path(Path(path))
            snapshot = read_profile_snapshot(path)
            replacement = self._center.prepare_profile_bytes(snapshot.data)
            if not snapshot.matches_path(path):
                raise ValueError('PROFILE_CHANGED')
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            self._center.log('WARN', 'Profile could not be loaded.'); return False
        def current():
            try: return self.authorize('openProfile') and self.stamp() == stamp and snapshot.matches_path(path)
            except OSError: return False
        return self._replace(replacement, Path(path), current,
            lambda code, _committed: self._center.log('INFO' if code == 'OK' else 'WARN', 'Profile transition: ' + code))

    def reset_native_settings(self):
        if not self.authorize('resetSettings'):
            return False
        path = self._native.PROFILE_DIRECTORY / 'default.json'
        if path.exists(): return self.load_native_profile(path)
        stamp = self.stamp()
        return self._replace(self._native.Settings(), None, lambda: self.stamp() == stamp,
            lambda code, _committed: self._center.log('INFO' if code == 'OK' else 'WARN', 'Profile transition: ' + code))

    def open_native_profile(self):
        if not self.authorize('openProfile') or self._transition is not None:
            return False
        stamp = self.stamp()
        path, _filter = QFileDialog.getOpenFileName(self._center, self._center.t('Open Vapor Lock profile'),
            str(self._native.PROFILE_DIRECTORY), 'JSON profiles (*.json)')
        if not path or not self.authorize('openProfile') or self.stamp() != stamp:
            return False
        return self.load_native_profile(Path(path), captured_stamp=stamp)

    def save_native_profile(self, *, picker=False):
        if not self.authorize('saveProfileAs' if picker else 'saveProfile') or self._transition is not None:
            self._center.log('WARN', self._center.t('Profile save is unavailable while authorization or a profile transition is pending.'))
            return False
        stamp = self.stamp()
        path = self._center._active_profile_path or self._native.PROFILE_DIRECTORY / 'default.json'
        captured_target = self._center._active_profile_path
        if picker:
            chosen, _filter = QFileDialog.getSaveFileName(self._center, self._center.t('Save Vapor Lock profile'),
                str(self._native.PROFILE_DIRECTORY / 'new-profile.json'), 'JSON profiles (*.json)')
            if not chosen:
                return False
            path = Path(chosen).with_suffix('.json')
        def current():
            return (self.authorize('saveProfileAs' if picker else 'saveProfile') and self.stamp() == stamp
                    and self._transition is None and self._center._active_profile_path == captured_target)
        if not current():
            self._center.log('WARN', self._center.t('Profile changed before saving. Save again with the current settings.'))
            return False
        try:
            path = writable_native_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not current():
                self._center.log('WARN', self._center.t('Profile changed before saving. Save again with the current settings.'))
                return False
            result = self._center.write_profile(path, native_guard=current)
        except (OSError, ValueError):
            self._center.log('WARN', 'Profile could not be saved.')
            return False
        self.native_changed()
        if not result:
            self._center.log('WARN', self._center.t('Profile could not be saved. Current changes are still unsaved.'))
        return result
