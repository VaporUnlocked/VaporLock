#pragma once

#include <windows.h>
#include <algorithm>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace vaporlock::mouse_host {

constexpr std::uint32_t magic = 0x324D4C56;  // VLM2
constexpr std::int32_t maxDelta = 1000000;
constexpr ULONGLONG maxAgeMs = 75;

struct Request {
    std::uint32_t protocol = magic;
    DWORD foregroundPid = 0;
    std::int32_t dx = 0, dy = 0;
    ULONGLONG sentAt = 0;
    ULONGLONG sequence = 0;
    std::uint32_t controls = 0;
    std::uint32_t reserved = 0;
};

struct Reply {
    std::uint32_t protocol = magic;
    DWORD error = ERROR_SUCCESS;
    std::uint32_t accepted = 0;
    std::uint32_t held = 0;
};

static_assert(sizeof(Request) == 40 && sizeof(Reply) == 16);

struct InputState {
    bool activation = false;
    bool compensation = false;
    bool quickRelease = false;
};

inline std::uint32_t packControls(int activation, int compensation, int quickRelease) {
    return static_cast<std::uint32_t>(activation) | static_cast<std::uint32_t>(compensation) << 8
        | static_cast<std::uint32_t>(quickRelease) << 16;
}

inline int controlKey(std::uint32_t controls, unsigned index) {
    return static_cast<int>((controls >> (8 * index)) & 0xff);
}

inline bool validControls(std::uint32_t controls) {
    const auto common = [](int key) {
        return key == VK_LSHIFT || key == VK_LCONTROL || key == VK_SPACE;
    };
    const int activation = controlKey(controls, 0);
    const int compensation = controlKey(controls, 1);
    const int quickRelease = controlKey(controls, 2);
    return !(controls >> 24)
        && (activation == VK_RBUTTON || common(activation))
        && (!compensation || common(compensation) || compensation == VK_LBUTTON
            || compensation == VK_RBUTTON || compensation == VK_MBUTTON || compensation == 'F' || compensation == 'G')
        && (!quickRelease || common(quickRelease) || quickRelease == VK_MBUTTON
            || quickRelease == VK_XBUTTON1 || quickRelease == VK_XBUTTON2);
}

inline bool validToken(std::wstring_view token) {
    return token.size() == 32 && std::all_of(token.begin(), token.end(), [](wchar_t c) {
        return (c >= L'0' && c <= L'9') || (c >= L'a' && c <= L'f');
    });
}

inline std::wstring pipeName(std::wstring_view token) {
    return L"\\\\.\\pipe\\VaporLockMouseV2-" + std::wstring(token);
}

inline std::wstring eventName(std::wstring_view suffix, std::wstring_view token) {
    return L"Local\\VaporLockMouse" + std::wstring(suffix) + L"-" + std::wstring(token);
}

inline DWORD foregroundProcess() {
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    return pid;
}

inline bool validRequest(const Request& request, ULONGLONG now, ULONGLONG lastSequence) {
    if (request.protocol != magic || request.reserved || !request.foregroundPid || request.sentAt > now
        || now - request.sentAt > maxAgeMs || request.sequence <= lastSequence)
        return false;
    if (request.controls) return !request.dx && !request.dy && validControls(request.controls);
    return (request.dx || request.dy)
        && request.dx >= -maxDelta && request.dx <= maxDelta && request.dy >= -maxDelta && request.dy <= maxDelta;
}

class Handle {
public:
    explicit Handle(HANDLE value = nullptr) : m_value(value) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    [[nodiscard]] HANDLE get() const { return m_value; }
    explicit operator bool() const { return m_value && m_value != INVALID_HANDLE_VALUE; }
    void reset(HANDLE value = nullptr) {
        if (*this) CloseHandle(m_value);
        m_value = value;
    }
private:
    HANDLE m_value;
};

class Client {
public:
    Client() : m_event(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}

    [[nodiscard]] bool connected() const { return static_cast<bool>(m_pipe); }

    bool connect(std::wstring_view token, DWORD expectedHost) {
        if (!m_event || !validToken(token) || !expectedHost) return false;
        m_pipe.reset(CreateFileW(pipeName(token).c_str(), GENERIC_READ | FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES,
            0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr));
        ULONG actualHost = 0;
        DWORD mode = PIPE_READMODE_MESSAGE;
        if (m_pipe && GetNamedPipeServerProcessId(m_pipe.get(), &actualHost) && actualHost == expectedHost
            && SetNamedPipeHandleState(m_pipe.get(), &mode, nullptr, nullptr))
            return true;
        m_pipe.reset();
        return false;
    }

    std::optional<InputState> sample(int activation, int compensation, int quickRelease) {
        m_sampledForeground = 0;
        Request request{magic, foregroundProcess(), 0, 0, GetTickCount64(), ++m_sequence,
                        packControls(activation, compensation, quickRelease)};
        Reply reply{};
        if (!validRequest(request, request.sentAt, m_sequence - 1) || !exchange(request, reply)
            || !reply.accepted || reply.error || (reply.held & ~7u))
            return std::nullopt;
        m_sampledForeground = request.foregroundPid;
        return InputState{(reply.held & 1u) != 0, (reply.held & 2u) != 0, (reply.held & 4u) != 0};
    }

    // A failed exchange retires the connection. A completed exchange returns
    // the exact Windows send result, with no queued or replayed movement.
    bool send(LONG dx, LONG dy, Reply& reply) {
        if (!m_pipe) return false;
        Request request{magic, m_sampledForeground, dx, dy, GetTickCount64(), ++m_sequence};
        if (!validRequest(request, request.sentAt, m_sequence - 1)) {
            reply.error = ERROR_INVALID_PARAMETER;
            return true;
        }
        return exchange(request, reply) && reply.held == 0;
    }

private:
    bool exchange(Request& request, Reply& reply) {
        if (!m_pipe || !ResetEvent(m_event.get())) {
            m_pipe.reset();
            return false;
        }
        OVERLAPPED operation{};
        operation.hEvent = m_event.get();
        if (!TransactNamedPipe(m_pipe.get(), &request, sizeof(request), &reply, sizeof(reply), nullptr, &operation)) {
            if (GetLastError() != ERROR_IO_PENDING) {
                m_pipe.reset();
                return false;
            }
            if (WaitForSingleObject(m_event.get(), 25) != WAIT_OBJECT_0) {
                CancelIoEx(m_pipe.get(), &operation);
                DWORD discarded = 0;
                GetOverlappedResult(m_pipe.get(), &operation, &discarded, TRUE);
                m_pipe.reset();
                return false;
            }
        }
        DWORD received = 0;
        const bool ok = GetOverlappedResult(m_pipe.get(), &operation, &received, FALSE) && received == sizeof(reply)
            && reply.protocol == magic && reply.accepted <= 1;
        if (!ok) m_pipe.reset();
        return ok;
    }

    Handle m_event;
    Handle m_pipe;
    ULONGLONG m_sequence = 0;
    DWORD m_sampledForeground = 0;
};

}  // namespace vaporlock::mouse_host
