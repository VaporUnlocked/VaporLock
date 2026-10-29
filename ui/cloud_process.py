"""Owned Cloud workers: gated startup, a Windows job and bounded event queues.

This is resource/lifecycle isolation, not a network or operating-system sandbox.
No owner model/training module runs until the parent has assigned its exact
created process handle to the kill-on-close, memory-limited job.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from cloud_contract import CloudInputError, MAX_JSON, frame_header, json_value, worker_event


class EventQueue:
    """Bound serialized bytes as well as event count; terminal has its own slot."""
    def __init__(self):
        self._events = queue.Queue(maxsize=16); self._bytes = 0
        self._terminal = None; self._lock = threading.Lock()
        self._progress = None

    def put(self, payload, size, terminal):
        with self._lock:
            if payload.get('type') == 'progress':
                if size > 128 * 1024:
                    raise CloudInputError('Cloud progress record exceeds its byte budget.')
                self._progress = (payload, size)
                return
            if terminal:
                if self._terminal is not None:
                    raise CloudInputError('Duplicate Cloud terminal event.')
                self._terminal = (payload, size)
                return
            if self._bytes + size > 2 * 1024 * 1024:
                raise CloudInputError('Cloud event aggregate byte budget exceeded.')
            self._events.put_nowait((payload, size)); self._bytes += size

    def get_nowait(self):
        with self._lock:
            try:
                payload, size = self._events.get_nowait(); self._bytes -= size
                return payload
            except queue.Empty:
                if self._progress is not None:
                    payload, _ = self._progress; self._progress = None
                    return payload
                if self._terminal is None:
                    raise
                payload, _ = self._terminal; self._terminal = None
                return payload


class _Basic(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', W.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                ('active', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class _Limits(ctypes.Structure):
    _fields_ = [('basic', _Basic), ('io', _IO), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


class ProcessJob:
    def __init__(self, memory_bytes, *, active_processes=2):
        if os.name != 'nt':
            raise CloudInputError('Cloud workers require the Windows job supervisor on this build.')
        if type(memory_bytes) is not int or not 128 * 1024 ** 2 <= memory_bytes <= 16 * 1024 ** 3:
            raise CloudInputError('Invalid Cloud worker memory budget.')
        if type(active_processes) is not int or not 1 <= active_processes <= 2:
            raise CloudInputError('Invalid Cloud worker process limit.')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, arguments, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
            ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
            ('TerminateJobObject', [W.HANDLE, W.UINT], W.BOOL),
            ('CloseHandle', [W.HANDLE], W.BOOL)):
            function = getattr(self.api, name); function.argtypes = arguments; function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Limits(); limits.basic.flags = 0x2000 | 0x8 | 0x200
        # Windows venv python.exe is a redirector plus its interpreter child.
        # Both stay in this same memory-limited, non-breakaway job.
        limits.basic.active = active_processes; limits.job_memory = memory_bytes
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close(); raise CloudInputError('Cannot apply Cloud worker resource limits.')

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
            raise CloudInputError('Cannot assign the created Cloud worker to its resource job.')

    def terminate(self):
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise CloudInputError('Cloud worker termination could not be verified.')

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise CloudInputError('Cloud worker job handle could not be released.')
            self.handle = None


class OwnedChild:
    def __init__(self, command, *, cwd, environment=None, memory_bytes=2 * 1024 ** 3, wall_seconds=60, active_processes=2):
        if not command or type(wall_seconds) not in (int, float) or not 0 < wall_seconds <= 86400:
            raise CloudInputError('Invalid Cloud worker invocation.')
        self.events = EventQueue(); self.process = None; self.job = None
        self._startup_lease = getattr(command, 'cloud_lease', None)
        self.deadline = time.monotonic() + wall_seconds
        self.failure = None; self.stop_requested = False; self._closed = False
        self._failure_lock = threading.Lock(); self._writes = queue.Queue(maxsize=4)
        self._stdout_done = threading.Event(); self._stderr_done = threading.Event()
        self._terminal_seen = False; self._operation = None; self._stop_deadline = None
        self._frame_lock = threading.Lock(); self._awaiting_frame = None; self._input_closing = False
        self._admitted_run = None
        inherited = os.environ if environment is None else environment
        allowed = {'SYSTEMROOT','WINDIR','SYSTEMDRIVE','USERPROFILE','HOMEDRIVE','HOMEPATH',
                   'USERNAME','USERDOMAIN','APPDATA','LOCALAPPDATA','TEMP','TMP','PROGRAMDATA',
                   'PROGRAMFILES','PROGRAMFILES(X86)','COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)'}
        env = {key:value for key,value in inherited.items() if key.upper() in allowed}
        env['PATH'] = str(Path(env.get('SystemRoot', env.get('SYSTEMROOT', r'C:\Windows'))) / 'System32')
        env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
        try:
            self.job = ProcessJob(memory_bytes,active_processes=active_processes)
            self.process = subprocess.Popen(command, cwd=str(Path(cwd)), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.job.assign(self.process)
            self.process.stdin.write(b'VLCLOUD-GO-1\n'); self.process.stdin.flush()
        except BaseException:
            # Before GO the child is blocked on a fixed bounded stdlib read.
            if self.process is not None:
                self.process.kill(); self.process.wait(timeout=5)
            if self.job is not None:
                self.job.close()
            if self._startup_lease is not None:
                self._startup_lease.close()
                self._startup_lease = None
            raise
        self._reader = threading.Thread(target=self._stdout, name='CloudWorkerEvents', daemon=True)
        self._diagnostics = threading.Thread(target=self._stderr, name='CloudWorkerDiagnostics', daemon=True)
        self._reader.start(); self._diagnostics.start()
        threading.Thread(target=self._writer, name='CloudWorkerCommands', daemon=True).start()
        threading.Thread(target=self._watchdog, name='CloudWorkerDeadline', daemon=True).start()

    def _fail(self, reason):
        with self._failure_lock:
            if self.failure is None:
                self.failure = reason
        try:
            if not self._closed and self.process.poll() is None:
                self.job.terminate()
        except Exception:
            if self.process.poll() is None:
                self.process.kill()

    def _stdout(self):
        try:
            while not self._closed:
                raw = self.process.stdout.readline(MAX_JSON + 1)
                if not raw:
                    return
                if len(raw) > MAX_JSON or not raw.endswith(b'\n'):
                    raise CloudInputError('Cloud worker event exceeded its record limit.')
                if self._operation != 'inference' and len(raw) > 128 * 1024:
                    raise CloudInputError('Cloud status event exceeds its operation limit.')
                payload = json_value(raw)
                terminal = worker_event(payload, self._operation)
                if payload.get('type') == 'trained':
                    from dataset_paths import local_path
                    canonical = lambda value: os.path.normcase(str(local_path(value)))
                    if self._admitted_run is None or canonical(payload['output']) != canonical(self._admitted_run):
                        raise CloudInputError('Cloud completion output does not match its admitted run.')
                    for key, filename in (('best_cloud', 'best.cloud'), ('last_cloud', 'last.cloud'), ('checkpoint', 'last.cloudtrain')):
                        if payload[key] is not None and canonical(payload[key]) != canonical(self._admitted_run / filename):
                            raise CloudInputError('Cloud artifact path does not belong to its admitted run.')
                if payload.get('type') == 'detections':
                    with self._frame_lock:
                        expected = self._awaiting_frame
                        actual = (payload['sequence'], payload['captured_ns'], payload['width'], payload['height'])
                        if expected != actual:
                            raise CloudInputError('Cloud result does not match its admitted frame.')
                        self._awaiting_frame = None
                if self._terminal_seen:
                    raise CloudInputError('Cloud worker emitted output after its terminal event.')
                self._terminal_seen = terminal
                self.events.put(payload, len(raw), terminal)
        except Exception:
            self._fail('Cloud worker event stream failed or exceeded its bounded queue.')
        finally:
            self._stdout_done.set()

    def _stderr(self):
        # Drain bounded chunks to prevent pipe deadlock. Tracebacks may contain
        # owner paths or data, so they are not copied into UI/API/engine logs.
        total = 0
        try:
            while not self._closed:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                total += len(chunk)
                if total > 1024 * 1024:
                    self._fail('Cloud worker exceeded its diagnostic limit.'); return
        except (OSError, ValueError):
            return
        finally:
            self._stderr_done.set()

    def _writer(self):
        try:
            while not self._closed and self.process.poll() is None:
                try:
                    chunks = self._writes.get(timeout=.1)
                except queue.Empty:
                    continue
                if chunks is None:
                    self.process.stdin.close()
                    return
                for data in chunks:
                    view = memoryview(data)
                    while view:
                        written = self.process.stdin.write(view)
                        if not written:
                            raise BrokenPipeError('Cloud command write stopped.')
                        view = view[written:]
                self.process.stdin.flush()
        except (OSError, ValueError):
            if self.process.poll() is None:
                self._fail('Cloud worker command pipe failed.')

    def _watchdog(self):
        while not self._closed and self.process.poll() is None:
            if time.monotonic() >= self.deadline:
                self._fail('Cloud worker exceeded its time budget.'); return
            if self._stop_deadline is not None and time.monotonic() >= self._stop_deadline:
                self._fail('Cloud worker did not complete cooperative stop within 30 seconds. New run outputs may be incomplete; source inputs were not modified.'); return
            time.sleep(.1)

    def send(self, payload):
        data = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(data) > MAX_JSON or self._closed or self.process.poll() is not None:
            raise CloudInputError('Cloud worker is unavailable or its command exceeds the limit.')
        if self._operation is None:
            operation = payload.get('operation')
            if operation not in ('train', 'check_project', 'check_resume', 'check_model', 'inference'):
                raise CloudInputError('Invalid initial Cloud operation.')
            self._operation = operation
            if operation == 'train':
                from dataset_paths import local_path
                import re
                name = payload.get('run_name')
                if type(name) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
                    raise CloudInputError('Invalid admitted Cloud run name.')
                self._admitted_run = local_path(payload['output_parent']) / name
        try:
            self._writes.put_nowait((data,))
        except queue.Full as error:
            self._fail('Cloud command queue exceeded its bounded capacity.')
            raise CloudInputError('Cloud command queue is full.') from error

    def send_frame(self, header, pixels):
        header = bytes(header)
        sequence, captured_ns, width, height, length, *_ = frame_header(header)
        with self._frame_lock:
            if (self._operation != 'inference' or self._input_closing or self._closed
                    or self.process.poll() is not None or type(pixels) is not bytes or len(pixels) != length):
                raise CloudInputError('Invalid or unavailable Cloud frame submission.')
            if self._awaiting_frame is not None:
                raise CloudInputError('A Cloud frame is already awaiting its matching result.')
            self._awaiting_frame = (sequence, captured_ns, width, height)
            try:
                self._writes.put_nowait((header, pixels))
            except queue.Full as error:
                self._awaiting_frame = None
                raise CloudInputError('Cloud frame command queue is full.') from error

    def finish_inference(self):
        if self._operation != 'inference' or self._closed:
            raise CloudInputError('There is no Cloud inference worker to finish.')
        with self._frame_lock:
            if not self._input_closing:
                self._input_closing = True
                self._stop_deadline = time.monotonic() + 30
                try:
                    self._writes.put_nowait(None)
                except queue.Full as error:
                    self._fail('Cloud inference shutdown could not be queued.')
                    raise CloudInputError('Cloud inference shutdown queue is full.') from error

    def request_stop(self):
        if self._operation != 'train':
            raise CloudInputError('Cooperative stop is specific to Cloud training.')
        if not self.stop_requested:
            self.stop_requested = True; self._stop_deadline = time.monotonic() + 30
            self.send({'command': 'stop'})

    def poll(self):
        return self.process.poll()

    def terminate(self):
        self._fail('Cloud worker was terminated before cooperative completion.')

    def begin_exit_cleanup(self):
        """Close the job after root exit, before waiting on inherited pipe EOF."""
        if self.process.poll() is None:
            return False
        if not hasattr(self,'_exit_cleanup_started'):
            self._exit_cleanup_started=time.monotonic()
        # Kill-on-close releases descendants that inherited stdout/stderr.
        # Readers remain active and retain any buffered terminal event.
        self.job.close()
        drained=self._stdout_done.is_set() and self._stderr_done.is_set()
        if not drained and time.monotonic()-self._exit_cleanup_started>5:
            self.failure=self.failure or 'Cloud worker exit-pipe cleanup exceeded its drain budget.'
        return drained

    def close(self):
        if self._closed:
            return
        if self.process.poll() is None:
            raise CloudInputError('A running Cloud worker cannot be released without stopping it.')
        # Closing the owned job also removes any descendant retaining a pipe.
        # Every cleanup step is attempted, including on a reader timeout.
        try:
            self.job.close()
        except Exception:
            self.failure = self.failure or 'Cloud worker job handle could not be released.'
        if not self._stdout_done.wait(2) or not self._stderr_done.wait(2):
            self.failure = self.failure or 'Cloud process exited without its event readers completing.'
        if not self._terminal_seen:
            self.failure = self.failure or 'Cloud process exited without a valid completion event.'
        self._closed = True
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                self.failure = self.failure or 'Cloud worker pipe close failed.'
        if self.job.handle is not None:
            # Keep this object retained by its caller for cleanup retry.
            self._closed = False
            raise CloudInputError(self.failure or 'Cloud worker cleanup remains incomplete.')
        if self._startup_lease is not None:
            self._startup_lease.close()
            self._startup_lease = None
