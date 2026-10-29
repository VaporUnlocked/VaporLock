#pragma once
#include <Windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <thread>

namespace vaporlock::timing {
struct FrameTimerApi {
    static HANDLE create() {
        return CreateWaitableTimerExW(nullptr, nullptr,
            CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_MODIFY_STATE | SYNCHRONIZE);
    }
    static bool arm(HANDLE timer, LARGE_INTEGER due) {
        return SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE) != FALSE;
    }
    static bool wait(HANDLE timer, DWORD timeout) { return WaitForSingleObject(timer, timeout) == WAIT_OBJECT_0; }
    static void close(HANDLE timer) { CloseHandle(timer); }
};

// One reusable, private timer per capture loop. No process-wide timer resolution
// change, spin loop, queued work, or replay of expired frame deadlines.
// API parameter is a test seam for resource/failure handling, not a user setting.
template<class Api = FrameTimerApi>
class FrameDeadlineWait {
public:
    using Clock = std::chrono::steady_clock;
    FrameDeadlineWait() : timer_(Api::create()) {}
    ~FrameDeadlineWait() { if (timer_) Api::close(timer_); }
    FrameDeadlineWait(const FrameDeadlineWait&) = delete;
    FrameDeadlineWait& operator=(const FrameDeadlineWait&) = delete;
    bool highResolutionAvailable() const { return timer_ != nullptr; }
    void until(Clock::time_point deadline) {
        const auto remaining = deadline - Clock::now();
        if (remaining <= Clock::duration::zero()) return;
        LARGE_INTEGER due{};
        due.QuadPart = -static_cast<LONGLONG>(std::ceil(
            std::chrono::duration<double>(remaining).count() * 10000000.0));
        // Headless frame periods are at most one second. A missed timer signal
        // must still return to the original deadline rather than wait forever.
        const DWORD timeout = static_cast<DWORD>(std::clamp(std::ceil(
            std::chrono::duration<double, std::milli>(remaining).count()) + 1.0, 1.0, 1001.0));
        if (timer_ && Api::arm(timer_, due) && Api::wait(timer_, timeout)) return;
        // Recompute remaining time through sleep_until if the timer is unavailable
        // or failed. Do not add a second full period after a failed wait.
        std::this_thread::sleep_until(deadline);
    }
private:
    HANDLE timer_ = nullptr;
};
}
