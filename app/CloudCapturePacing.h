#pragma once
#include <algorithm>
#include <chrono>

namespace vaporlock::pipeline {

// Rate-limit committed fresh acquisitions. An unsuccessful desktop poll must
// not consume another image's rate slot. The caller separately bounds its
// source wait, cancellation and retry work. The caller checks hasFresh() before waiting on the initial deadline.
class CloudFreshFrameSchedule {
public:
    using Clock=std::chrono::steady_clock;
    using Time=Clock::time_point;
    static Clock::duration period(int fps) noexcept {
        fps=std::clamp(fps,1,1000000);
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
}
