"""Owned, bounded native model probes. No activation or profile persistence."""
from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, QTimer
from engine_stream import EngineLineReader
from tuning_tools import InferenceMeasurement


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str
    measurement: dict | None = None
    inspection: str = ""


class ModelProbe(QObject):
    """Keep ownership until the exact child stops, including timeout/cancel.

    Results distinguish an intentional measurement stop from a crash. Pipe
    payloads are untrusted and bounded; stderr never becomes user-facing text.
    This is an operational bound, not a sandbox for hostile native model code.
    """
    MAX_OUTPUT = 4 * 1024 * 1024
    MAX_LINE = 64 * 1024

    def __init__(self, parent, *, executable, arguments, environment, inspect=False,
                 completed, timeout_ms=60_000):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.setProcessEnvironment(environment)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.cancel("Probe timed out"))
        self._callback = completed
        self._inspect = inspect
        self._reader = EngineLineReader(self.MAX_LINE)
        self._measurement = InferenceMeasurement()
        self._model = None
        self._bytes = 0
        self._reason = None
        self._measured_stop = False
        self._finished = False
        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(250)
        self._memory_timer.timeout.connect(self._check_memory)
        self.process.readyReadStandardOutput.connect(self._stdout)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._error)
        self._launch = (str(executable), list(arguments), timeout_ms)

    @property
    def active(self):
        return not self._finished

    def start(self):
        executable, arguments, timeout_ms = self._launch
        self.timer.start(timeout_ms)
        self._memory_timer.start()
        self.process.start(executable, arguments)

    def _check_memory(self):
        # Operational resident/private-memory guard for our exact child PID.
        # Failure to query on Windows ends the probe; it never relaxes the bound.
        if self.process.state() != QProcess.ProcessState.Running: return
        try:
            if process_memory(int(self.process.processId())) > 8 * 1024**3:
                self.cancel('Model probe exceeded its memory limit')
        except (OSError, ImportError, RuntimeError):
            self.cancel('Model probe memory could not be checked')

    def cancel(self, reason="Probe cancelled"):
        self._reason = reason
        if self._finished:
            return
        self.process.kill()
        # Starting is owned too: wait for FailedToStart or finished, never
        # dispose a child merely because a kill request has been submitted.
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._finish(-1, QProcess.ExitStatus.CrashExit)

    def _budget(self, chunk):
        self._bytes += len(chunk)
        if self._bytes > self.MAX_OUTPUT:
            self.cancel("Probe output exceeded its limit")
            return False
        return self._reason is None

    def _stderr(self):
        self._budget(bytes(self.process.readAllStandardError()))

    def _stdout(self):
        chunk = bytes(self.process.readAllStandardOutput())
        if not self._budget(chunk):
            return
        for line in self._reader.feed(chunk):
            if line is None:
                self.cancel("Probe record exceeded its limit")
                return
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                self.cancel("Probe returned malformed data")
                return
            if type(value) is not dict:
                self.cancel("Probe returned malformed data")
                return
            if value.get('type') in ('error','output-error','fatal'):
                self.cancel('Native model probe reported an error')
                return
            if self._inspect and value.get("type") == "model":
                message = value.get("message")
                if type(message) is not str or len(message) > 4096:
                    self.cancel("Probe returned invalid model details")
                    return
                self._model = message
            elif not self._inspect and value.get("type") == "stats":
                from pipeline_tools import finite
                if not all(finite(value.get(key)) for key in
                           ('inference_total','uptime_seconds','inference_work_ms','model_work_ms')):
                    self.cancel('Probe returned malformed data')
                    return
                self._measurement.observe(value)
                if (self._measurement.result() is not None and not self._measured_stop
                        and not self._finished and self.process.state() == QProcess.ProcessState.Running):
                    self._measured_stop = True
                    self.process.kill()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._reason = "Native engine could not be started"
            self._finish(-1, QProcess.ExitStatus.CrashExit)

    def _finish(self, code, status):
        if self._finished or self.process.state() != QProcess.ProcessState.NotRunning:
            return
        # Set first to prevent reentrant failure completion while draining.
        self._finished = True
        self.timer.stop()
        self._memory_timer.stop()
        self._stdout()
        self._stderr()
        measured = self._measurement.result()
        normal = code == 0 and status == QProcess.ExitStatus.NormalExit
        ok = self._reason is None and ((normal and self._model is not None) if self._inspect
                                      else ((self._measured_stop or normal) and measured is not None))
        result = ProbeResult(ok, "Complete" if ok else self._reason or "Native model probe failed",
                             measured if ok else None, self._model if ok and self._inspect else "")
        callback, self._callback = self._callback, None
        try:
            callback(result)
        finally:
            self.deleteLater()


def inert_settings(settings):
    """A new settings object: no output, driver, preview, event, or dataset job."""
    from copy import deepcopy
    result = deepcopy(settings)
    for name in ("movement_enabled", "motion_compensation_enabled", "event_action_enabled",
                 "dataset_enabled", "preview_enabled", "desktop_overlay_enabled"):
        if hasattr(result, name):
            setattr(result, name, False)
    result.output_transport = "mouse"
    return result


def process_memory(pid):
    """Read only the owned Windows child; no optional Python dependency."""
    import ctypes as c
    from ctypes import wintypes as w
    class Counters(c.Structure):
        _fields_ = [('cb',w.DWORD),('faults',w.DWORD)] + [(name,c.c_size_t) for name in
            ('peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
    kernel=c.WinDLL('kernel32',use_last_error=True)
    api=c.WinDLL('psapi',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]; kernel.CloseHandle.restype=w.BOOL
    api.GetProcessMemoryInfo.argtypes=[w.HANDLE,c.POINTER(Counters),w.DWORD]
    api.GetProcessMemoryInfo.restype=w.BOOL
    handle=kernel.OpenProcess(0x0410,False,pid)
    if not handle: raise c.WinError(c.get_last_error())
    try:
        info=Counters(); info.cb=c.sizeof(info)
        if not api.GetProcessMemoryInfo(handle,c.byref(info),info.cb): raise c.WinError(c.get_last_error())
        return max(info.ws,info.private)
    finally: kernel.CloseHandle(handle)
