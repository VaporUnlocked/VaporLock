"""Candidate annotation transport on the existing exact-handle Job supervisor."""
from copy import deepcopy
import json
import threading
import time

from cloud_contract import CloudInputError, MAX_JSON, json_value
from cloud_annotation_protocol import command,event
from cloud_process import OwnedChild


class AnnotationChild(OwnedChild):
    def __init__(self,invocation,*,cwd):
        self.pending={};self.pending_lock=threading.Lock();self.started=False
        self.ready_seen=False;self.start_deadline=time.monotonic()+30;self.peer=None
        super().__init__(invocation,cwd=cwd,memory_bytes=2*1024**3,wall_seconds=1800,active_processes=1)

    def submit(self,payload):
        request=None
        if not self.started:
            if set(payload)!={'operation','device'} or payload['operation']!='annotation' or payload['device'] not in ('cpu','cuda'):
                raise CloudInputError('Invalid annotation worker startup.')
            self.started=True;self._operation='annotation'
            from cloud_access import edition
            from cloud_annotation_protocol import AnnotationPeer
            self.peer=AnnotationPeer(self.process.pid,edition(),payload['device'])
        else:
            kind=command(payload)
            with self.pending_lock:self.peer.expect(payload)
            if kind!='close':
                request=payload['identity']['request'] if kind=='mask' else payload['request']
                with self.pending_lock:
                    if len(self.pending)>=2 or request in self.pending:
                        raise CloudInputError('Wait for the current mask or image request before queuing more work.')
                    self.pending[request]=(deepcopy(payload),time.monotonic()+120)
        try:self.send(payload)
        except BaseException:
            if request is not None:
                with self.pending_lock:self.pending.pop(request,None)
            raise

    def _stdout(self):
        try:
            while not self._closed:
                raw=self.process.stdout.readline(MAX_JSON+1)
                if not raw:return
                if len(raw)>MAX_JSON or not raw.endswith(b'\n'):
                    raise CloudInputError('Annotation event exceeds its byte limit.')
                payload=json_value(raw)
                with self.pending_lock:
                    if self.peer is None:raise CloudInputError('Unexpected annotation startup output.')
                    terminal=self.peer.receive(payload)
                kind=payload['type']
                if kind=='annotation_ready':self.ready_seen=True
                if kind in ('mask_proposed','mask_failed','decoded','decode_failed'):
                    request=payload['identity']['request'] if kind.startswith('mask_') else payload['request']
                    with self.pending_lock:
                        expected=self.pending.pop(request,None)
                    if expected is None:raise CloudInputError('Unrequested annotation response.')
                if self._terminal_seen:raise CloudInputError('Annotation output followed its terminal event.')
                self._terminal_seen=terminal;self.events.put(payload,len(raw),terminal)
        except Exception:self._fail('Annotation worker stream failed its bounded protocol.')
        finally:self._stdout_done.set()

    def _watchdog(self):
        while not self._closed and self.process.poll() is None:
            now=time.monotonic()
            with self.pending_lock:expired=any(deadline<=now for _,deadline in self.pending.values())
            if (now>=self.deadline or expired or (not self.ready_seen and now>=self.start_deadline)
                    or (self._stop_deadline is not None and now>=self._stop_deadline)):
                self._fail('Annotation worker exceeded its time budget; unapproved edits were retained.');return
            time.sleep(.1)
