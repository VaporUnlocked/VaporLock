"""Qt owner for the bounded annotation child. No Torch or codecs in the GUI."""
from pathlib import Path
import queue
import time

from PySide6.QtCore import QObject,QProcess,QTimer,Signal


class AnnotationJob(QObject):
    received=Signal(dict)
    completed=Signal(bool,str)

    def __init__(self,parent,device='cpu'):
        super().__init__(parent)
        from cloud_dependencies import command
        from cloud_annotation_child import AnnotationChild
        self.done=False;self.ready=False;self.device=device;self.closing=False;self.waiting=[];self.terminal=None
        self.rejected=False
        invocation=command(annotation=True)
        self.child=AnnotationChild(invocation,cwd=Path(__file__).resolve().parents[1])
        try:self.child.submit(dict(operation='annotation',device=device))
        except BaseException:
            self.child.terminate();self.child.process.wait(timeout=5);self.child.close();raise
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(25)

    def state(self):
        return QProcess.ProcessState.NotRunning if self.done else QProcess.ProcessState.Running

    def submit(self,request):
        if self.done or self.closing:raise ValueError('Annotation worker is stopping.')
        if not self.ready:
            if len(self.waiting)>=2:raise ValueError('Wait for the current image or mask request.')
            self.waiting.append(request)
        else:self.child.submit(request)

    def stop(self):
        if self.done or self.closing:return
        self.closing=True;self.waiting=[]
        self.child._stop_deadline=time.monotonic()+5
        if self.child.pending:self.child.terminate()
        else:self.child.submit(dict(command='close'))

    def kill(self):
        if not self.done:self.closing=True;self.waiting=[];self.child.terminate()

    def _event(self,value):
        if self.child.failure or (self.child.peer is not None and (self.child.peer.rejected or self.child.peer.terminal=='error')):
            self.rejected=True;self.waiting=[]
        if self.rejected or self.closing:
            if value['type'] in ('error','annotation_closed'):self.terminal=value
            if value['type']=='error' and not self.closing:self.received.emit(value)
            return
        if value['type']=='annotation_ready':
            from cloud_access import edition
            if self.ready or value['pid']!=self.child.process.pid or value['edition']!=edition() or value['device']!=self.device:
                self.rejected=True;self.waiting=[]
                self.child._fail('Annotation readiness did not match its created process and build.');return
            self.ready=True
            try:
                for request in self.waiting:self.child.submit(request)
                self.waiting=[]
            except Exception:
                self.rejected=True;self.waiting=[]
                self.child._fail('Annotation startup could not submit its bounded requests.');return
        if value['type'] in ('error','annotation_closed'):self.terminal=value
        self.received.emit(value)

    def poll(self):
        if self.done:return
        if self.child.failure or (self.child.peer is not None and (self.child.peer.rejected or self.child.peer.terminal=='error')):
            self.rejected=True;self.waiting=[]
        try:
            for _ in range(16):self._event(self.child.events.get_nowait())
        except queue.Empty:pass
        code=self.child.poll()
        if code is None:return
        try:
            if not self.child.begin_exit_cleanup():return
        except Exception:
            self.child.failure=self.child.failure or 'Annotation cleanup has not completed.';return
        try:
            while True:self._event(self.child.events.get_nowait())
        except queue.Empty:pass
        try:self.child.close()
        except Exception:return
        self.done=True;self.ready=False;self.timer.stop()
        ok=code==0 and self.terminal is not None and self.terminal['type']=='annotation_closed' and not self.child.failure
        message=(self.terminal or {}).get('message') or self.child.failure or ''
        self.completed.emit(ok,message)
