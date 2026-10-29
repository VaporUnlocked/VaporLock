#pragma once

#include <windows.h>

namespace vaporlock::io {

// One caller, one outstanding write. The handle must use FILE_FLAG_OVERLAPPED.
class BoundedWindowsWrite {
public:
    BoundedWindowsWrite() : m_event(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
    ~BoundedWindowsWrite() { if (m_event) CloseHandle(m_event); }
    BoundedWindowsWrite(const BoundedWindowsWrite&) = delete;
    BoundedWindowsWrite& operator=(const BoundedWindowsWrite&) = delete;

    [[nodiscard]] bool ready() const { return m_event != nullptr; }

    [[nodiscard]] DWORD write(HANDLE handle, const void* data, DWORD size, DWORD timeoutMs) noexcept {
        if (!m_event) return ERROR_NOT_ENOUGH_MEMORY;
        if (!data || !size || timeoutMs == INFINITE) return ERROR_INVALID_PARAMETER;
        if (!ResetEvent(m_event)) return GetLastError();
        OVERLAPPED operation{};
        operation.hEvent = m_event;
        if (!WriteFile(handle, data, size, nullptr, &operation)) {
            const DWORD failure = GetLastError();
            if (failure != ERROR_IO_PENDING) return failure;
            const DWORD waited = WaitForSingleObject(m_event, timeoutMs);
            if (waited != WAIT_OBJECT_0) {
                const DWORD waitFailure = waited == WAIT_TIMEOUT ? ERROR_TIMEOUT : GetLastError();
                drainCancelled(handle, operation);
                return waitFailure;
            }
        }
        DWORD written = 0;
        if (!GetOverlappedResult(handle, &operation, &written, FALSE)) {
            const DWORD failure = GetLastError();
            if (failure == ERROR_IO_INCOMPLETE) drainCancelled(handle, operation);
            return failure;
        }
        return written == size ? ERROR_SUCCESS : ERROR_WRITE_FAULT;
    }

private:
    static void drainCancelled(HANDLE handle, OVERLAPPED& operation) noexcept {
        CancelIoEx(handle, &operation);
        // Cancellation is a request, not completion. Keep the caller's buffer
        // and OVERLAPPED alive until Windows releases them; never replay it.
        DWORD discarded = 0;
        GetOverlappedResult(handle, &operation, &discarded, TRUE);
    }

    HANDLE m_event = nullptr;
};

}  // namespace vaporlock::io
