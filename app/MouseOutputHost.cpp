#include "MouseOutputHost.h"

#include <sddl.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <array>
#include <cstddef>
#include <cwchar>

namespace {
using namespace vaporlock::mouse_host;

bool elevated() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
    const Handle held(token);
    TOKEN_ELEVATION information{};
    DWORD bytes = 0;
    return GetTokenInformation(held.get(), TokenElevation, &information, sizeof(information), &bytes)
        && information.TokenIsElevated;
}

std::wstring engineUser(HANDLE owner) {
    HANDLE token = nullptr;
    if (!OpenProcessToken(owner, TOKEN_QUERY, &token)) return {};
    const Handle held(token);
    alignas(TOKEN_USER) std::array<std::byte, 256> buffer{};
    DWORD length = 0;
    if (!GetTokenInformation(held.get(), TokenUser, buffer.data(), static_cast<DWORD>(buffer.size()), &length)) return {};
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<const TOKEN_USER*>(buffer.data())->User.Sid, &sid)) return {};
    std::wstring result(sid);
    LocalFree(sid);
    return result;
}

bool isEngine(DWORD child, DWORD owner, HANDLE process) {
    wchar_t path[32768]{};
    DWORD length = _countof(path);
    if (!QueryFullProcessImageNameW(process, 0, path, &length)) return false;
    const std::wstring_view image(path, length);
    const auto name = image.substr(image.find_last_of(L"\\/") + 1);
    if (_wcsicmp(name.data(), L"VaporLock.exe") != 0 && _wcsicmp(name.data(), L"VaporLockDml.exe") != 0
        && _wcsicmp(name.data(), L"VaporLockTensorRt.exe") != 0)
        return false;

    const Handle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    if (!snapshot || !Process32FirstW(snapshot.get(), &entry)) return false;
    do {
        if (entry.th32ProcessID == child) return entry.th32ParentProcessID == owner;
    } while (Process32NextW(snapshot.get(), &entry));
    return false;
}

bool activeDesktop() {
    const HDESK input = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    if (!input) return false;
    wchar_t active[256]{}, current[256]{};
    DWORD needed = 0;
    const bool same = GetUserObjectInformationW(input, UOI_NAME, active, sizeof(active), &needed)
        && GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()), UOI_NAME, current, sizeof(current), &needed)
        && wcscmp(active, current) == 0;
    CloseDesktop(input);
    return same;
}

class PipeIo {
public:
    PipeIo(HANDLE pipe, HANDLE owner, HANDLE cancel)
        : m_pipe(pipe), m_owner(owner), m_cancel(cancel), m_event(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}

    bool available() const {
        return m_event && WaitForSingleObject(m_owner, 0) == WAIT_TIMEOUT
            && WaitForSingleObject(m_cancel, 0) == WAIT_TIMEOUT;
    }

    bool connect() {
        if (!available()) return false;
        OVERLAPPED operation = next();
        if (ConnectNamedPipe(m_pipe, &operation)) return true;
        const DWORD result = GetLastError();
        if (result == ERROR_PIPE_CONNECTED) return true;
        DWORD ignored = 0;
        return result == ERROR_IO_PENDING && wait(operation, 120000, ignored);
    }

    bool read(Request& request) {
        OVERLAPPED operation = next();
        const BOOL ready = ReadFile(m_pipe, &request, sizeof(request), nullptr, &operation);
        DWORD transferred = 0;
        return finish(ready, operation, INFINITE, transferred) && transferred == sizeof(request);
    }

    bool write(const Reply& reply) {
        OVERLAPPED operation = next();
        const BOOL ready = WriteFile(m_pipe, &reply, sizeof(reply), nullptr, &operation);
        DWORD transferred = 0;
        return finish(ready, operation, 100, transferred) && transferred == sizeof(reply);
    }

private:
    OVERLAPPED next() {
        ResetEvent(m_event.get());
        OVERLAPPED operation{};
        operation.hEvent = m_event.get();
        return operation;
    }

    bool finish(BOOL ready, OVERLAPPED& operation, DWORD limit, DWORD& transferred) {
        if (ready) return GetOverlappedResult(m_pipe, &operation, &transferred, FALSE);
        return GetLastError() == ERROR_IO_PENDING && wait(operation, limit, transferred);
    }

    bool wait(OVERLAPPED& operation, DWORD limit, DWORD& transferred) {
        // Prioritize termination over an input packet arriving at the same time.
        const HANDLE handles[]{m_cancel, m_owner, m_event.get()};
        if (WaitForMultipleObjects(_countof(handles), handles, FALSE, limit) == WAIT_OBJECT_0 + 2)
            return GetOverlappedResult(m_pipe, &operation, &transferred, FALSE);
        CancelIoEx(m_pipe, &operation);
        GetOverlappedResult(m_pipe, &operation, &transferred, TRUE);
        return false;
    }

    HANDLE m_pipe, m_owner, m_cancel;
    Handle m_event;
};

void serve(PipeIo& io, HANDLE engine) {
    Request request{};
    ULONGLONG last = 0;
    DWORD inputForeground = 0;
    ULONGLONG inputSampleAt = 0;
    while (io.available() && io.read(request)) {
        if (!io.available() || WaitForSingleObject(engine, 0) != WAIT_TIMEOUT
            || !validRequest(request, GetTickCount64(), last)) return;
        last = request.sequence;
        Reply reply{};
        if (!activeDesktop() || foregroundProcess() != request.foregroundPid
            || !validRequest(request, GetTickCount64(), last - 1)) {
            inputForeground = 0;
            reply.error = ERROR_RETRY;
        } else if (request.controls) {
            for (unsigned index = 0; index < 3; ++index) {
                const int key = controlKey(request.controls, index);
                if (key && (GetAsyncKeyState(key) & 0x8000)) reply.held |= 1u << index;
            }
            reply.accepted = 1;
            inputForeground = request.foregroundPid;
            inputSampleAt = GetTickCount64();
        } else if (request.foregroundPid != inputForeground || GetTickCount64() - inputSampleAt > maxAgeMs) {
            reply.error = ERROR_RETRY;
        } else {
            INPUT input{};
            input.type = INPUT_MOUSE;
            input.mi.dx = request.dx;
            input.mi.dy = request.dy;
            input.mi.dwFlags = MOUSEEVENTF_MOVE;
            SetLastError(ERROR_SUCCESS);
            reply.accepted = SendInput(1, &input, sizeof(input));
            if (!reply.accepted) reply.error = GetLastError();
        }
        if (!io.write(reply)) return;
    }
}

int run(DWORD ownerPid, std::wstring_view token) {
    const Handle owner(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, ownerPid));
    const Handle ready(OpenEventW(EVENT_MODIFY_STATE, FALSE, eventName(L"Ready", token).c_str()));
    const Handle cancel(OpenEventW(SYNCHRONIZE, FALSE, eventName(L"Cancel", token).c_str()));
    DWORD ownSession = 0, ownerSession = 0;
    if (!owner || !ready || !cancel || !ProcessIdToSessionId(ownerPid, &ownerSession)
        || !ProcessIdToSessionId(GetCurrentProcessId(), &ownSession) || ownSession != ownerSession)
        return 3;
    const auto user = engineUser(owner.get());
    if (user.empty()) return 3;
    // Grant the UI owner read/write-data and attribute access. The client
    // cannot create another pipe instance under this name.
    const auto sddl = L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x12018b;;;" + user + L")S:(ML;;NW;;;ME)";
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1, &descriptor, nullptr))
        return 3;
    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};
    const Handle pipe(CreateNamedPipeW(pipeName(token).c_str(),
        PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_REJECT_REMOTE_CLIENTS, 1, 128, 128, 0, &security));
    LocalFree(descriptor);
    if (!pipe) return 3;

    PipeIo io(pipe.get(), owner.get(), cancel.get());
    if (!io.available() || !SetEvent(ready.get())) return 3;
    while (io.connect()) {
        ULONG pid = 0;
        if (GetNamedPipeClientProcessId(pipe.get(), &pid)) {
            const Handle engine(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
            if (engine && isEngine(pid, ownerPid, engine.get())) serve(io, engine.get());
        }
        DisconnectNamedPipe(pipe.get());
    }
    return 0;
}
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32);
    SetDllDirectoryW(L"");
    int count = 0;
    LPWSTR* args = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!args) return 2;
    int result = 2;
    if (count == 3 && elevated() && vaporlock::mouse_host::validToken(args[2])) {
        wchar_t* end = nullptr;
        const DWORD pid = wcstoul(args[1], &end, 10);
        if (pid && end && *end == L'\0') result = run(pid, args[2]);
    }
    LocalFree(args);
    return result;
}
