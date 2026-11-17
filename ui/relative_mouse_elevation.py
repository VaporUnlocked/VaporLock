"""One Windows administrator approval for a relative-mouse output session."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(frozen=True)
class MouseHostSession:
    token: str
    pid: int


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inherit", wintypes.BOOL),
    ]


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateEventW.argtypes = (ctypes.POINTER(_SecurityAttributes), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = (wintypes.HANDLE,)
    api.SetEvent.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    api.LocalFree.argtypes = (wintypes.HLOCAL,)
    api.LocalFree.restype = wintypes.HLOCAL
    return api


def _events(api, token):
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID)
    convert.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    # The logon that created the events and the approved elevated process
    # share the readiness and cancellation signals.
    if not convert("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
    handles = []
    try:
        for kind in ("Ready", "Cancel"):
            handle = api.CreateEventW(ctypes.byref(attrs), True, False, f"Local\\VaporLockMouse{kind}-{token}")
            error = ctypes.get_last_error()
            if handle:
                handles.append(handle)
            if not handle or error == 183:
                raise ctypes.WinError(error)
        return tuple(handles)
    except OSError:
        for handle in handles:
            api.CloseHandle(handle)
        raise
    finally:
        api.LocalFree(descriptor)


def _launch_mouse_host(result, executable, token, owner):
    from vlui_owned_process import OwnedProcessExit

    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference = 'Stop'; "
        "try { $p = Start-Process -FilePath " + quote(executable)
        + " -WorkingDirectory " + quote(executable.parent)
        + f" -ArgumentList '{owner} {token}' -Verb RunAs -WindowStyle Hidden -PassThru; "
          "[Console]::WriteLine($p.Id) } catch { "
          "$e = $_.Exception; while ($e -and $e -isnot [ComponentModel.Win32Exception]) { $e = $e.InnerException }; "
          "if ($e -and $e.NativeErrorCode -eq 1223) { exit 2 }; exit 1 }"
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        launch = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            cwd=executable.parent, creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True, encoding="ascii", errors="replace", check=False,
        )
        if launch.returncode:
            result.put((None, 1223 if launch.returncode == 2 else launch.returncode))
        else:
            result.put((OwnedProcessExit(int(launch.stdout.strip()), executable), 0))
    except (OSError, ValueError) as error:
        result.put((None, getattr(error, "winerror", None) or 1))


class RelativeMouseElevation(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.session = None
        self.message = ""
        self._api = None
        self._ready = self._cancel = self._process = None
        self._token = ""
        self._executable = None
        self._waiting = self._stopping = False
        self._result = SimpleQueue()
        self._poller = QTimer(self)
        self._poller.setInterval(75)
        self._poller.timeout.connect(self._poll)

    @property
    def preparing(self):
        return self._cancel is not None and self.session is None and not self._stopping

    @property
    def busy(self):
        return self._cancel is not None

    def prepare(self, source: Path):
        if self.busy or sys.platform != "win32":
            return
        self._api = _kernel()
        self._token = uuid.uuid4().hex
        try:
            # Use the native helper by itself. A mapped source drive can be
            # absent from the administrator logon created by UAC.
            local_temp = Path(os.environ["LOCALAPPDATA"]) / "Temp" if "LOCALAPPDATA" in os.environ else None
            directory = Path(tempfile.mkdtemp(prefix="VaporLockMouseHost-", dir=local_temp))
            self._executable = directory / "VaporLockMouseHost.exe"
            shutil.copyfile(source, self._executable)
            self._ready, self._cancel = _events(self._api, self._token)
        except OSError:
            self._release("Windows could not prepare the administrator mouse helper.")
            return
        self._waiting = True
        self.message = "Waiting for Windows administrator approval."
        self.changed.emit()
        self._poller.start()
        threading.Thread(target=_launch_mouse_host,
                         args=(self._result, self._executable, self._token, os.getpid()),
                         daemon=True).start()

    def stop(self):
        if not self.busy or self._stopping:
            return
        self._api.SetEvent(self._cancel)
        self._stopping = True
        self.session = None
        self.message = "Stopping administrator mouse output."
        self.changed.emit()

    def _poll(self):
        if self._waiting:
            try:
                self._process, error = self._result.get_nowait()
            except Empty:
                return
            self._waiting = False
            if not self._process:
                message = "Windows administrator approval was declined." if error == 1223 else (
                    f"Windows could not start the administrator mouse helper (error {error or 1}).")
                self._release(message)
                return

        if self._process.observed_exit():
            message = "" if self._stopping else (
                "Administrator mouse approval expired or the helper stopped." if self.session is not None else
                "The administrator mouse helper stopped before it was ready.")
            self._release(message)
            return
        if not self._stopping and self.session is None and self._api.WaitForSingleObject(self._ready, 0) == 0:
            self.session = MouseHostSession(self._token, self._process.native_pid)
            self.message = "Administrator mouse output is ready. Start a Control session."
            self.changed.emit()

    def _release(self, message):
        self._poller.stop()
        if self._cancel:
            self._api.SetEvent(self._cancel)
        for handle in (self._ready, self._cancel):
            if handle:
                self._api.CloseHandle(handle)
        if self._process:
            self._process.close()
        if self._executable:
            try:
                self._executable.unlink(missing_ok=True)
                self._executable.parent.rmdir()
            except OSError:
                pass
        self._ready = self._cancel = self._process = None
        self._waiting = self._stopping = False
        self._executable = None
        self.session = None
        self.message = message
        self.changed.emit()
