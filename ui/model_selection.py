"""Stopped-session model selection; no dialog, discovery or tuning suite."""
from copy import deepcopy
from dataclasses import asdict
import threading
import time
from PySide6.QtCore import QObject, QTimer


class ModelSelection(QObject):
    def __init__(self, center):
        super().__init__(center)
        self.center = center
        self.busy = False
        self._operation = None
        self._cancelled = threading.Event()
        self._generation = 0
        self._next = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)

    def set_status(self, source, **values):
        message = self.center.tf(source, **values)
        if hasattr(self.center, 'model_inspection'):
            self.center.model_inspection.setText(message)
        if hasattr(self.center, 'show_model_selection_status'):
            self.center.show_model_selection_status(source, values)
        self.center.log('INFO', message)

    def _refresh_buttons(self):
        self.center.set_start_enabled(not self.center.engine.active)

    def invalidate(self):
        self.cancel()

    def cancel(self):
        self._generation += 1
        self._cancelled.set()
        self._next = None
        if self.busy:
            self.set_status('Cancellation requested. Previous configuration retained.')
        self.busy = False
        self._operation = None

    def switch(self, field, value):
        if field not in ('model_path', 'provider'):
            raise ValueError('Invalid model selection field')
        candidate = deepcopy(self.center.settings)
        setattr(candidate, field, value)
        # Validate the complete settings before stopping or launching anything.
        from vlui_live_context import validated_native_settings
        validated_native_settings(asdict(candidate), tuple((name, str(spec.type))
            for name, spec in candidate.__dataclass_fields__.items()))
        if asdict(candidate) == asdict(self.center.settings):
            return False
        def ready():
            # Selection is not a benchmark or a compatibility test. Load the
            # chosen model through normal Start, after this stopped transaction.
            self._commit({'model_path': candidate.model_path, 'provider': candidate.provider})
        return self._begin('switch', ready)

    def _begin(self, operation, callback):
        c=self.center
        if (self.busy or c.model_probe_busy() or c._profile_transition_pending or c._native_closing
                or c.acceleration.active or c._calibration_active or False):
            return False
        owner=getattr(c,'_live_owner',None)
        if owner is not None and owner.background_conflict(): return False
        from vlui_native_injection import native_injection
        if native_injection() is not None: return False
        c.stop_capture()
        self.busy=True; self._operation=operation; self._generation+=1
        self._cancelled=threading.Event()
        self._settings=deepcopy(c.settings); self._stamp=asdict(c.settings)
        self._session_generation=c.engine._launch_generation
        self._deadline=time.monotonic()+5
        generation = self._generation
        self._next = lambda: callback() if generation == self._generation and self._current() else None
        self.set_status('Waiting for the previous session to stop…')
        self._refresh_buttons(); c.set_start_enabled(False); c.stop_button.setEnabled(True)
        self.timer.start()
        return True

    def _current(self):
        return (self.busy and not self._cancelled.is_set() and not self.center._native_closing
                and not False
                and not self.center.acceleration.active and not self.center._profile_transition_pending
                and not self.center._calibration_active
                and self._stamp==asdict(self.center.settings)
                and self._session_generation==self.center.engine._launch_generation)

    def _poll(self):
        self._refresh_buttons()
        if not self.busy: self.center.set_start_enabled(not self.center.engine.active)
        if self.busy and not self._current():
            self.center.cancel_model_probes(); return
        if self._next is not None:
            c=self.center
            stopped=(not c.engine.active and not c.xbox_compatible_bridge.preparing
                and c.xbox_compatible_bridge.safely_stopped()
                and c._pending_controller_start is None and not c._policy_start_pending
                and getattr(c.engine,'_controller_host_session',None) is None)
            if stopped:
                callback,self._next=self._next,None; callback()
            elif time.monotonic()>self._deadline:
                self._fail('Previous session did not stop. Configuration retained.'); return
        if not self.busy:
            self.timer.stop()

    def _fail(self, message, details=()):
        self.busy=False; self._operation=None; self._next=None
        stopped=not self.center.engine.active and self.center.xbox_compatible_bridge.safely_stopped()
        suffix='Previous configuration retained — output stopped.' if stopped else 'Previous configuration retained; Stop is still pending.'
        self.set_status('{message}\n{outcome}',message=self.center.t(message),outcome=self.center.t(suffix))
        for detail in details[:4]: self.center.log('WARN', detail)
        self._refresh_buttons(); self.center.set_start_enabled(not self.center.engine.active)

    def _complete(self, message):
        self.busy=False; self._operation=None
        self.set_status(message)
        self._refresh_buttons(); self.center.set_start_enabled(not self.center.engine.active)

    def _commit(self, fields):
        c = self.center
        if (not self._current() or c.engine.active or c._policy_start_pending
                or c._pending_controller_start is not None or c.xbox_compatible_bridge.preparing
                or not c.xbox_compatible_bridge.safely_stopped()
                or getattr(c.engine, '_controller_host_session', None) is not None
                or c._model_probes):
            self._fail('Configuration could not be applied.')
            return
        self.busy=False; self._operation=None
        try:
            self.center.commit_model_selection(fields)
        except ValueError:
            self._fail('Configuration could not be applied.'); return
        self._complete('Selection updated. Select Start to load it.')

