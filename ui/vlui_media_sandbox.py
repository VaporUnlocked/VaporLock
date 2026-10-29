"""Development-only Windows AppContainer/Job launcher for a pinned worker.

Not imported by the client or theme runtime. No raw theme paths or executables
are accepted. No unsandboxed fallback exists. Job limits bound committed memory.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
import uuid

if os.name != "nt":
    raise ImportError("Windows sandbox required")

k = C.WinDLL("kernel32", use_last_error=True)
a = C.WinDLL("advapi32", use_last_error=True)
u = C.WinDLL("userenv", use_last_error=True)
P, SZ, DWORD = C.c_void_p, C.c_size_t, W.DWORD
MAX_OUTPUT, STDERR_CAPTURE = 2097152 + 16384 + 13, 65536
MEMORY = 268435456
MITIGATIONS = 1 | sum(1 << bit for bit in (12, 16, 20, 24, 28, 32, 36, 40, 52, 56))


class SandboxFailure(ValueError):
    pass


def need(value, code):
    if not value:
        raise SandboxFailure(code)


def api(dll, name, args, result=W.BOOL):
    value = getattr(dll, name)
    value.argtypes, value.restype = args, result
    return value


class SA(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", P), ("inherit", W.BOOL)]


class SI(C.Structure):
    _fields_ = [("cb", DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
                ("x", DWORD), ("y", DWORD), ("xSize", DWORD), ("ySize", DWORD),
                ("xCount", DWORD), ("yCount", DWORD), ("fill", DWORD), ("flags", DWORD),
                ("show", W.WORD), ("reservedSize", W.WORD), ("reservedBytes", P),
                ("stdin", P), ("stdout", P), ("stderr", P)]


class SIX(C.Structure):
    _fields_ = [("startup", SI), ("attributes", P)]


class PI(C.Structure):
    _fields_ = [("process", P), ("thread", P), ("pid", DWORD), ("tid", DWORD)]


class CAP(C.Structure):
    _fields_ = [("sid", P), ("capabilities", P), ("count", DWORD), ("reserved", DWORD)]


class BASIC(C.Structure):
    _fields_ = [("processTime", C.c_int64), ("jobTime", C.c_int64), ("flags", DWORD),
                ("minWorking", SZ), ("maxWorking", SZ), ("active", DWORD),
                ("affinity", SZ), ("priority", DWORD), ("scheduling", DWORD)]


class IO(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in ("read", "write", "other", "readBytes", "writeBytes", "otherBytes")]


class EXTENDED(C.Structure):
    _fields_ = [("basic", BASIC), ("io", IO), ("processMemory", SZ), ("jobMemory", SZ),
                ("peakProcess", SZ), ("peakJob", SZ)]


class MODULE(C.Structure):
    _fields_ = [("size", DWORD), ("moduleId", DWORD), ("pid", DWORD),
                ("globalUses", DWORD), ("processUses", DWORD), ("base", P),
                ("imageBytes", DWORD), ("moduleHandle", P),
                ("name", C.c_wchar * 256), ("path", C.c_wchar * 260)]


class TRUSTEE(C.Structure):
    _fields_ = [("multiple", P), ("operation", DWORD), ("form", DWORD), ("type", DWORD), ("name", P)]


class ACCESS(C.Structure):
    _fields_ = [("permissions", DWORD), ("mode", DWORD), ("inheritance", DWORD), ("trustee", TRUSTEE)]


CloseHandle = api(k, "CloseHandle", [P])
LocalFree = api(k, "LocalFree", [P], P)
FreeSid = api(a, "FreeSid", [P], P)
CreateProfile = api(u, "CreateAppContainerProfile", [W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, P, DWORD, C.POINTER(P)], C.c_long)
DeleteProfile = api(u, "DeleteAppContainerProfile", [W.LPCWSTR], C.c_long)
ProfileFolder = api(u, "GetAppContainerFolderPath", [W.LPCWSTR, C.POINTER(W.LPWSTR)], C.c_long)
TaskFree = api(C.WinDLL("ole32"), "CoTaskMemFree", [P], None)
SidString = api(a, "ConvertSidToStringSidW", [P, C.POINTER(W.LPWSTR)])
GetSecurity = api(a, "GetNamedSecurityInfoW", [W.LPCWSTR, DWORD, DWORD, P, P, C.POINTER(P), P, C.POINTER(P)], DWORD)
SetSecurity = api(a, "SetNamedSecurityInfoW", [W.LPWSTR, DWORD, DWORD, P, P, P, P], DWORD)
EntriesAcl = api(a, "SetEntriesInAclW", [DWORD, C.POINTER(ACCESS), P, C.POINTER(P)], DWORD)
SDString = api(a, "ConvertSecurityDescriptorToStringSecurityDescriptorW", [P, DWORD, DWORD, C.POINTER(W.LPWSTR), P])
CreatePipe = api(k, "CreatePipe", [C.POINTER(P), C.POINTER(P), C.POINTER(SA), DWORD])
HandleFlags = api(k, "SetHandleInformation", [P, DWORD, DWORD])
GetHandleFlags = api(k, "GetHandleInformation", [P, C.POINTER(DWORD)])
InitAttributes = api(k, "InitializeProcThreadAttributeList", [P, DWORD, DWORD, C.POINTER(SZ)])
UpdateAttribute = api(k, "UpdateProcThreadAttribute", [P, DWORD, SZ, P, SZ, P, P])
DeleteAttributes = api(k, "DeleteProcThreadAttributeList", [P], None)
CreateProcess = api(k, "CreateProcessW", [W.LPCWSTR, W.LPWSTR, P, P, W.BOOL, DWORD, P, W.LPCWSTR, P, C.POINTER(PI)])
CreateJob = api(k, "CreateJobObjectW", [P, W.LPCWSTR], P)
SetJob = api(k, "SetInformationJobObject", [P, C.c_int, P, DWORD])
GetJob = api(k, "QueryInformationJobObject", [P, C.c_int, P, DWORD, P])
AssignJob = api(k, "AssignProcessToJobObject", [P, P])
InJob = api(k, "IsProcessInJob", [P, P, C.POINTER(W.BOOL)])
KillJob = api(k, "TerminateJobObject", [P, DWORD])
Terminate = api(k, "TerminateProcess", [P, DWORD])
Resume = api(k, "ResumeThread", [P], DWORD)
Wait = api(k, "WaitForSingleObject", [P, DWORD], DWORD)
ExitCode = api(k, "GetExitCodeProcess", [P, C.POINTER(DWORD)])
OpenToken = api(a, "OpenProcessToken", [P, DWORD, C.POINTER(P)])
TokenInfo = api(a, "GetTokenInformation", [P, C.c_int, P, DWORD, C.POINTER(DWORD)])
EqualSid = api(a, "EqualSid", [P, P])
Policy = api(k, "GetProcessMitigationPolicy", [P, C.c_int, P, SZ])
Read = api(k, "ReadFile", [P, P, DWORD, C.POINTER(DWORD), P])
Write = api(k, "WriteFile", [P, P, DWORD, C.POINTER(DWORD), P])
Snapshot = api(k, "CreateToolhelp32Snapshot", [DWORD, DWORD], P)
ModuleFirst = api(k, "Module32FirstW", [P, C.POINTER(MODULE)])
ModuleNext = api(k, "Module32NextW", [P, C.POINTER(MODULE)])
OpenThread = api(k, "OpenThread", [DWORD, W.BOOL, DWORD], P)
ThreadProcess = api(k, "GetProcessIdOfThread", [P], DWORD)
CancelIo = api(k, "CancelSynchronousIo", [P])


def sid_text(sid):
    text = W.LPWSTR()
    need(SidString(sid, C.byref(text)), "SANDBOX_SID")
    try:
        return text.value
    finally:
        LocalFree(text)


def plain(path):
    for part in (path, *path.parents):
        info = part.lstat()
        need(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "SANDBOX_REPARSE")


def acl(path, sid, *, remove=False):
    plain(path)
    old, descriptor, merged = P(), P(), P()
    need(GetSecurity(str(path), 1, 4, None, None, C.byref(old), None, C.byref(descriptor)) == 0, "SANDBOX_ACL_READ")
    string = W.LPWSTR()
    try:
        need(SDString(descriptor, 1, 4, C.byref(string), None), "SANDBOX_ACL_READ")
        before = string.value
        if not remove:
            need(sid_text(sid) not in before, "SANDBOX_SID_EXISTING")
        access = ACCESS(0x1200a9, 4 if remove else 1, 3, TRUSTEE(None, 0, 0, 1, sid))
        need(EntriesAcl(1, C.byref(access), old, C.byref(merged)) == 0, "SANDBOX_ACL_MERGE")
        need(SetSecurity(str(path), 1, 4, None, None, merged, None) == 0, "SANDBOX_ACL_WRITE")
        return before
    finally:
        if merged:
            LocalFree(merged)
        if string:
            LocalFree(string)
        LocalFree(descriptor)


def acl_snapshot(path):
    plain(path)
    old, descriptor, string = P(), P(), W.LPWSTR()
    need(GetSecurity(str(path), 1, 4, None, None, C.byref(old), None, C.byref(descriptor)) == 0, "SANDBOX_ACL_READ")
    try:
        need(SDString(descriptor, 1, 4, C.byref(string), None), "SANDBOX_ACL_READ")
        return string.value
    finally:
        if string:
            LocalFree(string)
        LocalFree(descriptor)


class WorkerSandbox:
    """Parent-owned import sandbox; only fixed worker.py is launched.

    Constructor arguments are developer/trusted build configuration. There is
    deliberately no production integration until actual review is complete.
    """
    def __init__(self, bundle: Path, manifest_sha256: str, *, deadline=None, capture_startup_modules=False):
        self.bundle = Path(bundle).absolute()
        allowed = Path(__file__).resolve().parents[1] / "build/verification/ui-v2"
        need(self.bundle.parent == allowed and self.bundle.name.startswith("media-worker-"), "SANDBOX_CONTAINMENT")
        self.expected = manifest_sha256
        self.deadline = deadline or time.monotonic() + 30
        self.sid, self.name, self.granted = P(), None, False
        self.evidence = {}
        need(type(capture_startup_modules) is bool, "SANDBOX_PROBE_OPTION")
        self.capture_startup_modules = capture_startup_modules

    def checkpoint(self):
        need(time.monotonic() < self.deadline, "SANDBOX_IMPORT_DEADLINE")

    def verify_bundle(self):
        self.checkpoint()
        plain(self.bundle)
        raw = (self.bundle / "manifest.json").read_bytes()
        need(hashlib.sha256(raw).hexdigest() == self.expected, "SANDBOX_MANIFEST")
        manifest = json.loads(raw)
        native = manifest.get('purpose') == 'development fixed native isolation probe; not a decoder or release'
        self.native_probe = native
        need(not self.capture_startup_modules or native, "SANDBOX_PROBE_OPTION")
        need(type(manifest["files"]) is dict and len(manifest["files"]) <= 512, "SANDBOX_MANIFEST")
        found = set()
        for file in self.bundle.rglob("*"):
            plain(file)
            if file.is_file():
                found.add(file.relative_to(self.bundle).as_posix())
        need(found == set(manifest["files"]) | {"manifest.json"}, "SANDBOX_MANIFEST")
        for name, entry in manifest["files"].items():
            self.checkpoint()
            need(type(name) is str and not name.startswith(("/", "\\")) and "\\" not in name
                 and all(p not in ("", ".", "..") for p in name.split("/")) and ":" not in name, "SANDBOX_MANIFEST")
            file = self.bundle / name
            plain(file)
            need(file.stat().st_size == entry["bytes"], "SANDBOX_MANIFEST")
            need(hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"], "SANDBOX_MANIFEST")
        self.evidence["manifestSHA256"] = self.expected

    def __enter__(self):
        self.verify_bundle()
        self.name = "VaporLock.UIValidation.Dev." + uuid.uuid4().hex
        try:
            need(CreateProfile(self.name, "Vapor Lock development raster worker", "Disposable isolated decoder", None, 0,
                               C.byref(self.sid)) == 0, "SANDBOX_PROFILE")
            self.evidence["sid"] = sid_text(self.sid)
            folder = W.LPWSTR()
            need(ProfileFolder(self.evidence["sid"], C.byref(folder)) == 0, "SANDBOX_PROFILE_FOLDER")
            try:
                self.scratch = folder.value
            finally:
                TaskFree(folder)
            self.evidence["aclBefore"] = acl(self.bundle, self.sid)
            self.granted = True
            self.checkpoint()
            return self
        except BaseException:
            self.close(raise_errors=False)
            raise

    def close(self, *, raise_errors=True):
        errors = []
        if self.granted:
            try:
                self.evidence["aclWithGrant"] = acl(self.bundle, self.sid, remove=True)
                self.evidence["aclAfter"] = acl_snapshot(self.bundle)
                self.evidence["sidGrantRemoved"] = self.evidence["sid"] not in self.evidence["aclAfter"]
                need(self.evidence["sidGrantRemoved"], "SANDBOX_ACL_CLEANUP")
            except BaseException:
                errors.append("SANDBOX_ACL_CLEANUP")
                self.evidence.setdefault("sidGrantRemoved", None)
            finally:
                self.granted = False
        if self.name and self.sid:
            try:
                deleted = DeleteProfile(self.name)
                self.evidence["profileDeleteHRESULT"] = deleted
                need(deleted == 0, "SANDBOX_PROFILE_CLEANUP")
                self.evidence["profileScratchExistsAfterDelete"] = Path(self.scratch).exists()
            except BaseException:
                errors.append("SANDBOX_PROFILE_CLEANUP")
            finally:
                FreeSid(self.sid)
                self.sid, self.name = P(), None
        self.evidence["profileCleanupErrors"] = errors
        if errors and raise_errors:
            raise SandboxFailure(errors[0])

    def __exit__(self, kind, _error, _trace):
        self.close(raise_errors=kind is None)

    def startup_modules(self, pid, expiry, cancelled, record):
        """Bounded read-only snapshots of this retained live probe child only.

        These are startup samples, not an exhaustive DLL/decoder loading trace.
        """
        record.update(pid=pid, modules=[], status="unknown", attempts=0, snapshotOpened=False)
        handle = None
        for attempt in range(3):
            need(time.monotonic() < expiry, "SANDBOX_ASSET_DEADLINE")
            need(not cancelled(), "SANDBOX_CANCELLED")
            record["attempts"] = attempt + 1
            handle = Snapshot(0x18, pid)
            if handle != P(-1).value:
                record["snapshotOpened"] = True
                break
            error = C.get_last_error()
            record["winerror"] = error
            if error != 24:
                record["status"] = "snapshotError"
                return record
        else:
            record["status"] = "retryLimit"
            return record
        try:
            flags = DWORD()
            need(GetHandleFlags(handle, C.byref(flags)) and not flags.value & 1, "SANDBOX_SNAPSHOT_INHERITANCE")
            item = MODULE(); item.size = C.sizeof(MODULE)
            if not ModuleFirst(handle, C.byref(item)):
                record.update(status="enumerationError", winerror=C.get_last_error())
                return record
            total = 0
            for _ in range(128):
                need(time.monotonic() < expiry, "SANDBOX_ASSET_DEADLINE")
                need(not cancelled(), "SANDBOX_CANCELLED")
                entry = {"name": item.name, "path": item.path, "imageBytes": item.imageBytes}
                total += len(json.dumps(entry, ensure_ascii=True).encode("ascii"))
                if item.pid != pid or total > 16384 or len(item.path) >= 259 or len(item.name) >= 255:
                    record["status"] = "partialOrCap"
                    return record
                record["modules"].append(entry)
                if not ModuleNext(handle, C.byref(item)):
                    error = C.get_last_error()
                    record.update(status="complete" if error == 18 else "enumerationError", winerror=error)
                    return record
            record["status"] = "moduleCap"
            return record
        finally:
            record["snapshotCloseSucceeded"] = bool(CloseHandle(handle))

    def verify_process(self, process, job):
        token = P()
        need(OpenToken(process, 8, C.byref(token)), "SANDBOX_TOKEN")
        try:
            def info(kind):
                length = DWORD()
                TokenInfo(token, kind, None, 0, C.byref(length))
                need(0 < length.value <= 4096, "SANDBOX_TOKEN")
                output = C.create_string_buffer(length.value)
                need(TokenInfo(token, kind, output, length, C.byref(length)), "SANDBOX_TOKEN")
                return output
            need(DWORD.from_buffer(info(29)).value == 1, "SANDBOX_APPCONTAINER")
            # Keep the backing buffer alive while EqualSid dereferences it.
            sid_buffer = info(31)
            need(EqualSid(P.from_buffer(sid_buffer).value, self.sid), "SANDBOX_PACKAGE_SID")
            integrity = info(25)
            need(sid_text(P.from_buffer(integrity).value) == "S-1-16-4096", "SANDBOX_INTEGRITY")
            need(DWORD.from_buffer(info(30)).value == 0, "SANDBOX_CAPABILITIES")
        finally:
            CloseHandle(token)
        policies = {}
        # DWORD flag layouts are defined in winnt.h. DEP has trailing BOOLEAN.
        for kind, mask in ((0, 1), (1, 5), (2, 1), (3, 3), (4, 1), (6, 1), (7, 1), (10, 3), (13, 1)):
            output = C.create_string_buffer(8 if kind == 0 else 4)
            need(Policy(process, kind, output, len(output)), "SANDBOX_MITIGATION_QUERY")
            flags = DWORD.from_buffer(output).value
            need(flags & mask == mask, "SANDBOX_MITIGATION")
            policies[str(kind)] = flags
        member = W.BOOL()
        need(InJob(process, job, C.byref(member)) and member.value, "SANDBOX_JOB")
        limits = EXTENDED()
        need(GetJob(job, 9, C.byref(limits), C.sizeof(limits), None), "SANDBOX_JOB")
        need(limits.basic.flags == 0x2308 and limits.basic.active == 1 and limits.processMemory == MEMORY
             and limits.jobMemory == MEMORY, "SANDBOX_JOB_LIMITS")
        self.evidence.update({"appContainer": True, "capabilities": 0, "integrity": "low",
                              "mitigations": policies, "jobFlags": limits.basic.flags,
                              "processLimit": 1, "committedMemoryBytes": MEMORY})

    def run(self, payload: bytes, *, cancelled=lambda: False):
        need(type(payload) is bytes and len(payload) <= 8388608 + 15, "SANDBOX_INPUT")
        need(bool(self.sid) and self.granted, "SANDBOX_NOT_OPEN")
        self.checkpoint()
        expiry = min(self.deadline, time.monotonic() + 5)
        self.verify_bundle()
        self.evidence.update(processPhase="notCreated", resumeSucceeded=False,
                             stdinExpectedBytes=len(payload))
        handles, child, job, attributes = [], PI(), None, None
        output, stderr, failure = bytearray(), bytearray(), []
        readers, writer = [], None
        primary, attributes_ready = None, False
        handle_lock = threading.Lock()
        io_state = {name: {"observed": 0, "status": "notStarted", "winerror": None}
                    for name in ("stdout", "stderr", "stdin")}
        samples = []
        resumed_at = None
        handle_close_errors = []

        def cancellation(phase):
            observed = bool(cancelled())
            if observed:
                self.evidence["cancellationObserved"] = {"phase": phase,
                    "postResumeSeconds": None if resumed_at is None else time.monotonic() - resumed_at}
            return observed

        def owned(handle):
            handles.append(handle)
            return handle
        def close(handle):
            with handle_lock:
                if handle in handles:
                    result = bool(CloseHandle(handle))
                    handles.remove(handle)
                    if not result: handle_close_errors.append("SANDBOX_HANDLE_CLEANUP")
                    return result
            return True
        def pipe(parent_reads):
            left, right = P(), P()
            security = SA(C.sizeof(SA), None, True)
            need(CreatePipe(C.byref(left), C.byref(right), C.byref(security), 0), "SANDBOX_PIPE")
            owned(left.value)
            owned(right.value)
            parent = left.value if parent_reads else right.value
            child_end = right.value if parent_reads else left.value
            need(HandleFlags(parent, 1, 0), "SANDBOX_HANDLE")
            return parent, child_end

        def read(handle, target, maximum, discard, name):
            state = io_state[name]
            try:
                buffer, count = C.create_string_buffer(65536), DWORD()
                state["status"] = "reading"
                while True:
                    C.set_last_error(0)
                    ok = Read(handle, buffer, len(buffer), C.byref(count), None)
                    error = 0 if ok else C.get_last_error()
                    if not ok:
                        state.update(status="eof" if error == 109 else "readError", winerror=error)
                        if error not in (0, 109): failure.append("SANDBOX_PIPE_READ")
                        break
                    if not count.value:
                        state.update(status="eof", winerror=0)
                        break
                    state["observed"] += count.value
                    if not discard and len(target) + count.value > maximum:
                        failure.append("SANDBOX_OUTPUT_LIMIT")
                        state.update(status="outputLimit", winerror=None, limitJobKillSucceeded=bool(KillJob(job, 123)))
                        break
                    target.extend(buffer.raw[:min(count.value, maximum - len(target))])
            except BaseException:
                state["status"] = "readException"
                failure.append("SANDBOX_PIPE_READ")

        def write(handle):
            try:
                io_state["stdin"]["status"] = "writing"
                count = DWORD()
                for start in range(0, len(payload), 65536):
                    block = payload[start:start + 65536]
                    need(Write(handle, block, len(block), C.byref(count), None) and count.value == len(block), "SANDBOX_PIPE_WRITE")
                    io_state["stdin"]["observed"] += count.value
                io_state["stdin"]["status"] = "complete"
            except BaseException:
                io_state["stdin"].update(status="writeError", winerror=C.get_last_error())
                failure.append("SANDBOX_PIPE_WRITE")
            finally:
                io_state["stdin"]["closeSucceeded"] = close(handle)

        try:
            input_parent, input_child = pipe(False)
            output_parent, output_child = pipe(True)
            error_parent, error_child = pipe(True)
            job = owned(CreateJob(None, None))
            need(job, "SANDBOX_JOB_CREATE")
            limits = EXTENDED()
            limits.basic.flags, limits.basic.active = 0x2308, 1
            limits.processMemory = limits.jobMemory = MEMORY
            need(SetJob(job, 9, C.byref(limits), C.sizeof(limits)), "SANDBOX_JOB_LIMITS")
            capabilities = CAP(self.sid, None, 0, 0)
            inherit = (P * 3)(input_child, output_child, error_child)
            mitigation, restricted = C.c_uint64(MITIGATIONS), DWORD(1)
            required = SZ()
            InitAttributes(None, 4, 0, C.byref(required))
            need(0 < required.value < 16384, "SANDBOX_ATTRIBUTES")
            attributes = C.create_string_buffer(required.value)
            need(InitAttributes(attributes, 4, 0, C.byref(required)), "SANDBOX_ATTRIBUTES")
            attributes_ready = True
            for key, value in ((0x20009, capabilities), (0x20002, inherit), (0x20007, mitigation), (0x2000e, restricted)):
                need(UpdateAttribute(attributes, 0, key, C.byref(value), C.sizeof(value), None, None), "SANDBOX_ATTRIBUTE")
            startup = SIX()
            startup.startup.cb, startup.startup.flags = C.sizeof(SIX), 0x100
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = input_child, output_child, error_child
            startup.attributes = C.cast(attributes, P)
            exe = self.bundle / ("worker.exe" if self.native_probe else "python.exe")
            arguments = [str(exe)] if self.native_probe else [str(exe), "-I", "-S", "-B", "-u", str(self.bundle / "worker.py")]
            command = C.create_unicode_buffer(subprocess.list2cmdline(arguments))
            system = os.environ.get("SystemRoot", "C:\\Windows")
            # Windows needs standard profile variables when constructing a
            # lowbox environment. Values point only to its disposable profile,
            # never the parent's user-data location; no ambient env is copied.
            variables = {"PATH": system + "\\System32", "SystemRoot": system,
                         "SystemDrive": str(Path(system).drive), "USERPROFILE": self.scratch,
                         "APPDATA": self.scratch, "LOCALAPPDATA": self.scratch,
                         "TEMP": self.scratch + "\\Temp", "TMP": self.scratch + "\\Temp"}
            environment = C.create_unicode_buffer("\0".join(name + "=" + value for name, value in
                                                            sorted(variables.items(), key=lambda item: item[0].upper())) + "\0\0")
            created = CreateProcess(str(exe), command, None, None, True, 0x08080404, environment,
                                    str(self.bundle), C.byref(startup), C.byref(child))
            self.evidence["createProcessWin32Error"] = 0 if created else C.get_last_error()
            need(created, "SANDBOX_CREATE_PROCESS")
            owned(child.process)
            owned(child.thread)
            self.evidence["retainedChildPid"] = child.pid
            self.evidence["processPhase"] = "createdSuspended"
            for end in (input_child, output_child, error_child):
                close(end)
            need(AssignJob(job, child.process), "SANDBOX_ASSIGN_JOB")
            self.verify_process(child.process, job)
            for handle in (job, child.process, child.thread, input_parent, output_parent, error_parent):
                flags = DWORD()
                need(GetHandleFlags(handle, C.byref(flags)) and not flags.value & 1, "SANDBOX_PARENT_INHERITANCE")
            self.evidence["inheritedPipeEndpoints"] = 3
            self.evidence["preResumeVerified"] = True
            need(time.monotonic() < expiry, "SANDBOX_ASSET_DEADLINE")
            need(not cancellation("preResume"), "SANDBOX_CANCELLED")
            suspended = Resume(child.thread)
            self.evidence["resumeSuspendCount"] = suspended
            need(suspended == 1, "SANDBOX_RESUME")
            resumed_at = time.monotonic()
            self.evidence.update(resumeSucceeded=True, processPhase="resumed",
                                 resumeMonotonic=resumed_at, resumedExecutionBudgetSeconds=expiry-resumed_at)
            for handle, target, bound, discard, name in ((output_parent, output, MAX_OUTPUT, False, "stdout"),
                                                        (error_parent, stderr, STDERR_CAPTURE, True, "stderr")):
                thread = threading.Thread(target=read, args=(handle, target, bound, discard, name), daemon=True)
                readers.append(thread)
                thread.start()
            writer = threading.Thread(target=write, args=(input_parent,), daemon=True)
            writer.start()
            while Wait(child.process, 10) == 258:
                need(not cancellation("running"), "SANDBOX_CANCELLED")
                need(time.monotonic() < expiry, "SANDBOX_ASSET_DEADLINE")
                need(not failure, failure[0] if failure else "SANDBOX_IO")
                if self.capture_startup_modules and len(samples) < 3 and time.monotonic() - resumed_at >= (.1, .2, .4)[len(samples)]:
                    sample = {}; samples.append(sample)
                    self.startup_modules(child.pid, expiry, lambda: cancellation("running"), sample)
            for thread in [writer, *readers]:
                thread.join(max(0, expiry - time.monotonic()))
                need(not thread.is_alive(), "SANDBOX_ASSET_DEADLINE")
            code = DWORD()
            need(ExitCode(child.process, C.byref(code)), "SANDBOX_WORKER_EXIT")
            self.evidence["workerExitCode"] = code.value
            self.evidence["stderrCapturedBytes"] = len(stderr)
            usage = EXTENDED()
            if GetJob(job, 9, C.byref(usage), C.sizeof(usage), None):
                self.evidence["peakProcessCommitBytes"] = usage.peakProcess
                self.evidence["peakJobCommitBytes"] = usage.peakJob
            need(code.value == 0, "SANDBOX_WORKER_EXIT")
            need(not failure, failure[0] if failure else "SANDBOX_IO")
            self.evidence["stderrCapturedBytes"] = len(stderr)
            return bytes(output)
        except BaseException as error:
            primary = error
            self.evidence["primaryCause"] = str(error) if isinstance(error, SandboxFailure) else "UNEXPECTED_SANDBOX_FAILURE"
            raise
        finally:
            cleanup_errors = []
            cleanup_deadline = time.monotonic() + 5
            self.evidence["primaryCause"] = self.evidence.get("primaryCause", "success")
            self.evidence["startupModuleSamples"] = samples
            self.evidence["postResumeElapsedSeconds"] = None if resumed_at is None else time.monotonic() - resumed_at
            if job:
                try:
                    result = bool(KillJob(job, 124))
                    self.evidence["cleanupJobKill"] = {"succeeded": result, "winerror": 0 if result else C.get_last_error()}
                    if not result: cleanup_errors.append("SANDBOX_JOB_TERMINATION")
                except BaseException:
                    cleanup_errors.append("SANDBOX_JOB_TERMINATION")
            if child.process:
                try:
                    result = bool(Terminate(child.process, 124))
                    self.evidence["cleanupProcessTerminate"] = {"succeeded": result, "winerror": 0 if result else C.get_last_error()}
                    waited = Wait(child.process, min(2000, max(1, int((cleanup_deadline - time.monotonic()) * 1000))))
                    self.evidence["cleanupProcessWait"] = waited
                    if waited != 0: cleanup_errors.append("SANDBOX_TERMINATION")
                except BaseException:
                    cleanup_errors.append("SANDBOX_TERMINATION")
                try:
                    code = DWORD(); result = bool(ExitCode(child.process, C.byref(code)))
                    self.evidence["finalExitQuery"] = {"succeeded": result, "exitCode": code.value if result else None,
                                                       "winerror": 0 if result else C.get_last_error()}
                except BaseException:
                    self.evidence["finalExitQuery"] = {"succeeded": False, "exitCode": None, "winerror": None}
            for thread in ([writer] if writer else []) + readers:
                cancellation_record = {"threadId": thread.native_id, "status": "notNeeded"}
                self.evidence.setdefault("ioCancellation", []).append(cancellation_record)
                try:
                    if thread.is_alive() and thread.native_id:
                        thread_handle = OpenThread(0x801, False, thread.native_id)
                        cancellation_record.update(status="openFailed", winerror=C.get_last_error() if not thread_handle else 0)
                        if thread_handle:
                            try:
                                owner = ThreadProcess(thread_handle)
                                cancellation_record["sameParentProcess"] = owner == os.getpid()
                                if owner == os.getpid():
                                    result = bool(CancelIo(thread_handle))
                                    cancellation_record.update(status="cancelRequested" if result else "cancelFailed",
                                                               winerror=0 if result else C.get_last_error())
                                else:
                                    cancellation_record["status"] = "ownerMismatch"
                                    cleanup_errors.append("SANDBOX_IO_CLEANUP")
                            finally:
                                closed = bool(CloseHandle(thread_handle))
                                cancellation_record["threadHandleCloseSucceeded"] = closed
                                if not closed: cleanup_errors.append("SANDBOX_HANDLE_CLEANUP")
                    thread.join(max(0, cleanup_deadline - time.monotonic()))
                    if thread.is_alive(): cleanup_errors.append("SANDBOX_IO_CLEANUP")
                except BaseException:
                    cleanup_errors.append("SANDBOX_IO_CLEANUP")
            self.evidence["io"] = {name: dict(value) for name, value in io_state.items()}
            self.evidence["stdoutRetainedBytes"] = len(output)
            self.evidence["stderrCapturedBytes"] = len(stderr)
            self.evidence["stderrDiscardedBytes"] = max(0, io_state["stderr"]["observed"] - len(stderr))
            self.evidence["ioFailures"] = failure[:8]
            self.evidence["ioThreadsJoined"] = not any(t.is_alive() for t in ([writer] if writer else []) + readers)
            if job:
                try:
                    usage = EXTENDED(); result = bool(GetJob(job, 9, C.byref(usage), C.sizeof(usage), None))
                    self.evidence["finalJobPeakQuery"] = {"succeeded": result,
                        "processBytes": usage.peakProcess if result else None, "jobBytes": usage.peakJob if result else None,
                        "winerror": 0 if result else C.get_last_error()}
                except BaseException:
                    self.evidence["finalJobPeakQuery"] = {"succeeded": False, "processBytes": None, "jobBytes": None, "winerror": None}
            if attributes_ready:
                try: DeleteAttributes(attributes)
                except BaseException: cleanup_errors.append("SANDBOX_ATTRIBUTE_CLEANUP")
            for handle in list(reversed(handles)):
                try:
                    if not close(handle): cleanup_errors.append("SANDBOX_HANDLE_CLEANUP")
                except BaseException:
                    cleanup_errors.append("SANDBOX_HANDLE_CLEANUP")
            cleanup_errors.extend(handle_close_errors[:16])
            self.evidence["processCleanupErrors"] = cleanup_errors[:32]
            self.evidence["cleanupDeadlineExceeded"] = time.monotonic() > cleanup_deadline
            if cleanup_errors and primary is None:
                raise SandboxFailure(cleanup_errors[0])
