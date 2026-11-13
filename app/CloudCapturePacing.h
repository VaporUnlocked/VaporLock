#pragma once
#include <algorithm>
#include <chrono>

namespace vaporlock::pipeline {

// Rate-limit committed acquisitions. Pointer updates and unsuccessful polls
// leave the next image's slot available. The caller bounds the source wait.
class FreshFrameSchedule {
public:
    using Clock=std::chrono::steady_clock;
    using Time=Clock::time_point;
    static Clock::duration period(int fps) noexcept {
        fps=std::max(1,fps);
        return std::max(Clock::duration{1},
            std::chrono::ceil<Clock::duration>(std::chrono::duration<double>(1./fps)));
    }
    bool hasFresh() const noexcept { return m_valid; }
    Time next(int fps) const noexcept {
        return m_valid ? m_last+period(fps) : Time::min();
    }
    bool commit(Time acquired,int fps) noexcept {
        if(m_valid && acquired<next(fps))return false;
        m_last=acquired;m_valid=true;return true;
    }
    void reset() noexcept {m_valid=false;}
private:
    Time m_last{};
    bool m_valid=false;
};

using CloudFreshFrameSchedule = FreshFrameSchedule;

// DXGI's finite timeout can wake on a coarse Windows clock. Poll with a
// high-resolution cancellable timer during motion and back off when idle.
inline auto desktopPollDelay(FreshFrameSchedule::Clock::duration sinceFresh) noexcept {
    using namespace std::chrono_literals;
    return sinceFresh < 100ms ? 500us : 4ms;
}
}
