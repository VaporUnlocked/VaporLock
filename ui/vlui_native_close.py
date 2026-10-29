"""Bounded native close observation; timeout retains the live window and owners."""
import time
from PySide6.QtCore import QProcess, QTimer


class NativeCloseOwner:
    def __init__(self, center):
        self._center = center
        self._requested = False
        self._completed = False
        self._deadline = None
        self._timer = QTimer(center)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self.poll)

    def handle(self, event):
        if self._completed:
            event.accept()
            return
        event.ignore()
        center = self._center
        if not self._requested:
            workspace=getattr(getattr(center,'_cloud_suite',None),'workspace',None)
            if workspace is not None and not workspace.allow_close():return
            self._requested = True
            center._native_closing = True
            if center._live_coordinator is not None:
                center._live_coordinator.begin_close()
            else:
                center.stop_capture()
            center.stop_training_worker()
            if getattr(center,"_cloud_suite",None):center._cloud_suite.stop()
            annotation=getattr(center,'_cloud_annotation_job',None)
            if annotation is not None and not annotation.done:annotation.stop()
            cloud_check=getattr(center,"_cloud_model_check_job",None)
            if cloud_check and not cloud_check.done:cloud_check.stop()
            center.stop_dataset_worker()
            center.engine_capabilities.stop()
            center.cancel_motion_compensation_calibration()
            center.local_api.request_stop_async()
            center.unregister_global_hotkeys()
            for name in ('_benchmark_process', '_validation_process'):
                process = getattr(center, name, None)
                if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
            center.desktop_overlay.hide()
        self._deadline = time.monotonic() + 32.0
        self._timer.start()
        self.poll()

    def _safe(self):
        center = self._center
        workspace=getattr(getattr(center,'_cloud_suite',None),'workspace',None)
        if workspace is not None and not workspace.close_complete():return False
        if center.model_probe_busy():
            return False
        if center.engine.active or center.xbox_compatible_bridge.preparing or not center.xbox_compatible_bridge.safely_stopped():
            return False
        if center._policy_start_pending or center._pending_controller_start is not None or center.acceleration.active:
            return False
        for name in ('_benchmark_process', '_validation_process', '_training_worker', '_dataset_worker', '_cloud_training_job', '_cloud_model_check_job','_cloud_annotation_job'):
            process = getattr(center, name, None)
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                return False
        return ((center._live_coordinator is None or center._live_coordinator.disposal_complete()) and center.engine_capabilities.cleanup_complete()
                and center.local_api.cleanup_complete())

    def poll(self):
        if self._completed or not self._requested:
            return
        if self._safe():
            self._completed = True
            self._timer.stop()
            owner = getattr(self._center, '_live_owner', None)
            if owner is not None:
                owner.producer.retire()
            # A safe result may be synchronous inside the original ignored close
            # event. Qt ignores a recursive close there; finish on the next turn.
            QTimer.singleShot(0, self._center.close)
        elif time.monotonic() >= self._deadline:
            self._timer.stop()
            message = self._center.t('Shutdown is pending. Native Stop remains available. Try closing again after the operation finishes.')
            if self._center._live_coordinator is not None:
                notice = self._center._live_coordinator.notice
                notice.setText(message)
                notice.show()
            else:
                self._center.log('WARN', message)

    def native_observation(self):
        return {'requested': self._requested, 'complete': self._completed, 'retained': self._requested and not self._completed}
