"""Qt ownership for isolated Cloud project and training jobs."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import queue

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
