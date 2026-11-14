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
    enum class Pacing { MinimumSpacing, KeepPhase };

    explicit FreshFrameSchedule(Pacing pacing = Pacing::MinimumSpacing) noexcept : m_pacing(pacing) {}

    static Clock::duration period(int fps) noexcept {
        fps=std::max(1,fps);
        return std::max(Clock::duration{1},
            std::chrono::ceil<Clock::duration>(std::chrono::duration<double>(1./fps)));
    }
    bool hasFresh() const noexcept { return m_valid; }
    Time next(int fps) const noexcept {
        if (!m_valid) return Time::min();
        return m_fps == fps ? m_next : m_last + period(fps);
    }
    bool commit(Time acquired,int fps) noexcept {
        const auto due = next(fps);
        if (acquired < due) return false;
        const auto interval = period(fps);
        // Keep the rate phase across ordinary timer and source jitter. Never
        // bank a whole missed slot or bring two fresh captures closer than
        // a quarter of a period. Each deadline advances by at least one
        // period. A rolling interval can gain at most one frame from jitter.
        if (m_valid && m_fps == fps && m_pacing == Pacing::KeepPhase && acquired - due < interval) {
            m_next = std::max(due + interval, acquired + interval / 4);
        } else {
            m_next = acquired + interval;
        }
        m_last = acquired;
        m_fps = fps;
        m_valid = true;
        return true;
    }
    void reset() noexcept {m_valid=false;}
private:
    Pacing m_pacing;
    Time m_last{}, m_next{};
    int m_fps = 0;
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
