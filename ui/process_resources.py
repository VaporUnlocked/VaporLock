"""Read-only counters for explicitly supplied local process IDs; no discovery."""
import ctypes
from ctypes import wintypes as W
import os
import time


def cpu_percent(previous, current, logical_cpus):
    """Machine-normalized CPU; unknown on first sample, reset or invalid clock."""
    if previous is None: return None
    elapsed = current[0] - previous[0]; used = current[1] - previous[1]
    if elapsed <= 0 or used < 0 or logical_cpus < 1: return None
    return max(0., min(100., 100 * used / elapsed / logical_cpus))


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('PageFaultCount', W.DWORD)] + [
        (key, ctypes.c_size_t) for key in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


class ProcessSample:
    def __init__(self, pid):
        self.pid = pid; self.handle = None; self.previous = None
        if os.name != 'nt' or type(pid) is not int or pid <= 0: raise OSError('Unavailable')
        k = self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; k.OpenProcess.restype = W.HANDLE
        k.CloseHandle.argtypes = [W.HANDLE]; k.CloseHandle.restype = W.BOOL
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
        k.GetProcessTimes.restype = W.BOOL
        k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]; k.GetExitCodeProcess.restype = W.BOOL
        self.ps = ctypes.WinDLL('psapi', use_last_error=True)
        self.ps.GetProcessMemoryInfo.argtypes = [W.HANDLE, ctypes.POINTER(MemoryCounters), W.DWORD]
        self.ps.GetProcessMemoryInfo.restype = W.BOOL
        self.handle = k.OpenProcess(0x1000, False, pid)
        if not self.handle: raise OSError('Unavailable')

    def close(self):
        if self.handle: self.k.CloseHandle(self.handle); self.handle = None

    def __del__(self): self.close()

    def read(self):
        if not self.handle: raise OSError('Unavailable')
        code = W.DWORD()
        if not self.k.GetExitCodeProcess(self.handle, ctypes.byref(code)) or code.value != 259:
            raise OSError('Process exited')
        creation, exit_time, kernel, user = (W.FILETIME() for _ in range(4))
        if not self.k.GetProcessTimes(self.handle, *[ctypes.byref(x) for x in (creation, exit_time, kernel, user)]):
            raise OSError('Unavailable')
        ticks = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
        current = (time.monotonic(), (ticks(kernel) + ticks(user)) / 1e7)
        cpu = cpu_percent(self.previous, current, os.cpu_count() or 1); self.previous = current
        memory = MemoryCounters(); memory.cb = ctypes.sizeof(memory)
        if not self.ps.GetProcessMemoryInfo(self.handle, ctypes.byref(memory), memory.cb): raise OSError('Unavailable')
        return {'pid': self.pid, 'cpu': cpu, 'ram': memory.WorkingSetSize,
                'private': memory.PrivateUsage, 'created': ticks(creation)}


class ResourceSampler:
    def __init__(self): self.processes = {}
    def close(self):
        for _, sample in self.processes.values():
            if sample: sample.close()
        self.processes.clear()
    def sample(self, identities):
        # identity includes the owning QProcess object, not only a reusable PID.
        for key in list(self.processes):
            if key not in identities or identities[key] != self.processes[key][0]:
                old=self.processes.pop(key)[1]
                if old: old.close()
        result = {}
        for key, identity in identities.items():
            try:
                if key not in self.processes:
                    self.processes[key] = (identity,None)
                    self.processes[key] = (identity, ProcessSample(identity[1]))
                if self.processes[key][1] is None: raise OSError('Unavailable')
                result[key] = self.processes[key][1].read()
            except OSError:
                result[key] = None
                # Keep an exited handle until owner identity changes; never reopen
                # the same PID and accidentally report its replacement process.
        return result
