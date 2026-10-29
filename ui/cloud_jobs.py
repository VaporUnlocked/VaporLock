"""Qt ownership for isolated .cloud jobs and the native live-session peer."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import queue
import secrets

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


class CloudJob(QObject):
    received = Signal(dict)
    completed = Signal(bool, str)

    def __init__(self, parent, request, *, training=False):
        super().__init__(parent)
        from cloud_dependencies import command
        from cloud_process import OwnedChild
        self.request = deepcopy(request); self.training = training; self.done = False
        self.terminal = None; self._closing = False
        self.child = OwnedChild(command(), cwd=Path(__file__).resolve().parents[1],
            memory_bytes=(4 if training else 2) * 1024**3, wall_seconds=86400)
        try:
            self.child.send(self.request)
        except BaseException:
            self.child.terminate()
            self.child.process.wait(timeout=5)
            self.child.close()
            raise
        self.timer = QTimer(self); self.timer.setInterval(25); self.timer.timeout.connect(self.poll); self.timer.start()

    def state(self):
        return QProcess.ProcessState.NotRunning if self.done else QProcess.ProcessState.Running

    def stop(self):
        if self.done or self._closing: return
        self._closing = True
        if self.training: self.child.request_stop()
        else: self.child.terminate()

    def kill(self):
        if not self.done: self.child.terminate()

    def poll(self):
        if self.done: return
        for _ in range(32):
            try: event = self.child.events.get_nowait()
            except queue.Empty: break
            if event.get('type') in ('error', 'trained', 'project_checked', 'resume_checked','model_checked','inference_closed'):
                self.terminal = event
            self.received.emit(event)
        code = self.child.poll()
        if code is None: return
        try:
            drained=self.child.begin_exit_cleanup()
        except Exception:
            self.child.failure=self.child.failure or 'Cloud worker job cleanup could not complete.'
            drained=False
        if not drained:
            if self.child.failure and not getattr(self,'_cleanup_notice',False):
                self._cleanup_notice=True
                self.received.emit(dict(type='error',message=self.child.failure))
            return
        # Drain a terminal that arrived just after the bounded event loop above.
        try:
            while True:
                event = self.child.events.get_nowait(); self.terminal = event if event.get('type') in ('error','trained','project_checked','resume_checked','model_checked','inference_closed') else self.terminal
                self.received.emit(event)
        except queue.Empty: pass
        try: self.child.close()
        except Exception:
            return  # retain ownership and retry cleanup; never report a released worker
        self.done = True; self.timer.stop()
        success = code == 0 and self.terminal is not None and self.terminal.get('type') != 'error' and not self.child.failure
        message = (self.terminal or {}).get('message') or self.child.failure or ('' if success else '.cloud processing ended without a valid completion.')
        self.completed.emit(success, message)


class CloudLiveSession(QObject):
    def __init__(self, bridge, settings, provider):
        super().__init__(bridge)
        self.bridge = bridge; self.starting = True; self.ready = None; self._stopped = False
        self._finished=False;self._completion_result=None
        self._rejected=False;self._terminal_seen=False
        from cloud_access import edition
        self.edition=edition()
        from vapor_lock_gui import EDITION
        if EDITION!=self.edition:
            from cloud_contract import CloudInputError
            raise CloudInputError('Conflicting .cloud interface build edition.')
        self.pipe = 'VaporLock.Cloud.' + self.edition + '.' + secrets.token_hex(16)
        index = max(0, getattr(settings, 'inference_dml_device' if provider == 'dml' else 'inference_cuda_device', -1))
        device = 'directml:' + str(index) if provider == 'dml' else 'cuda:' + str(index) if provider == 'cuda' else 'cpu'
        from vapor_lock_gui import resolve_product_path
        self.job = CloudJob(self, {'operation':'inference', 'model':str(resolve_product_path(settings.model_path)),
            'backend':'torch' if provider == 'cuda' else 'ort', 'device':device, 'native_pipe':self.pipe})
        self.job.received.connect(self.handle_event); self.job.completed.connect(self.completed)

    @property
    def active(self): return not self._finished

    def handle_event(self, event):
        if self.bridge._cloud_session is not self or self._finished:return
        if self._rejected or self._terminal_seen:return
        if event.get('type') == 'ready' and not self._stopped:
            from cloud_contract import worker_event,CloudInputError
            try:worker_event(event,'inference')
            except CloudInputError:
                self._reject_ready('The .cloud runtime sent invalid readiness.');return
            if (self.ready is not None or event.get('edition')!=self.edition
                    or event.get('pid') != self.job.child.process.pid):
                self._reject_ready('The .cloud runtime readiness did not match its admitted build/process.');return
            self.ready = event; self.starting = False
            self.bridge.message.emit('.cloud model loaded with ' + event['device'] + '. Starting the native pipeline.')
            self.bridge._launch_provider()
        elif event.get('type') == 'error':
            self._reject_ready(event['message'])
        elif event.get('type') == 'inference_closed':
            self._terminal_seen=True;self.ready=None;self.starting=False

    def _reject_ready(self,message):
        if self._rejected:return
        self._rejected=True;self.ready=None;self.starting=False
        self.bridge.message.emit(message)
        self.job.kill()
        if self.bridge.process.state()!=QProcess.ProcessState.NotRunning:self.bridge.process.kill()

    def native_started(self):
        if (self.bridge._cloud_session is self and self.ready and not self._stopped and not self._finished
                and not self._rejected and not self._terminal_seen):
            self.job.child.send({'command':'client', 'pid':int(self.bridge.process.processId())})

    def stop(self):
        self._stopped = True; self.starting = False; self.job.stop()

    def completed(self, success, message):
        self.starting = False
        if self.bridge._cloud_session is not self: return
        self._completion_result=(success,message)
        if self.bridge.process.state() != QProcess.ProcessState.NotRunning:
            self.bridge.state_changed.emit('Stopping')
            self.bridge.process.kill()
        self._finish_if_ready()

    def native_finished(self):
        if self.bridge._cloud_session is not self:return
        self.starting=False
        if not self.job.done:
            self.bridge.state_changed.emit('Stopping')
            self.job.stop()
        self._finish_if_ready()

    def _finish_if_ready(self):
        if (self._finished or self.bridge._cloud_session is not self or not self.job.done
                or self._completion_result is None or self.bridge.process.state()!=QProcess.ProcessState.NotRunning):return
        self._finished=True
        success,message=self._completion_result
        self.bridge._cloud_session = None
        self.bridge._release_manual_lock_event();self.bridge._controller_host_session=None
        if self._rejected or (not success and not self._stopped):
            if not self._rejected:self.bridge.message.emit(message or '.cloud runtime stopped.')
            self.bridge.startup_rejected.emit()
        self.bridge.state_changed.emit('Stopped')
