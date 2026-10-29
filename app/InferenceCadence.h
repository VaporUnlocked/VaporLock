#pragma once
#include <algorithm>
#include <chrono>
#include <cstdint>

namespace vaporlock {

// One decision per acquired frame. Never queues old frames or catches up by
// repeatedly evaluating a cached image. Explicit skipping and rate limiting
// are independent reasons, so callers can report the actual work discarded.
class InferenceCadence {
public:
    using Clock = std::chrono::steady_clock;
    enum class Decision { Run, NoFreshFrame, ExplicitSkip, RateLimited };

    Decision evaluate(Clock::time_point now, bool fresh, int captureFps,
                      int inferenceFps, int frameSkip, bool capturePaced) {
        captureFps = std::max(1, captureFps);
        inferenceFps = std::max(1, inferenceFps);
        frameSkip = std::clamp(frameSkip, 0, 600);
        if (captureFps != m_captureFps || inferenceFps != m_inferenceFps
                || frameSkip != m_frameSkip || capturePaced != m_capturePaced) {
            m_captureFps = captureFps;
            m_inferenceFps = inferenceFps;
            m_frameSkip = frameSkip;
            m_capturePaced = capturePaced;
            m_phase = 0;
            m_hasDeadline = false;
        }
        if (!fresh) return Decision::NoFreshFrame;
        m_phase = (m_phase + 1) % (frameSkip + 1);
        if (m_phase != 0) return Decision::ExplicitSkip;

        // The headless capture loop already limits acquisition to captureFps.
        // Applying a second equal/slower clock here can discard fresh frames
        // solely because capture latency varies from one iteration to another.
        if (capturePaced && inferenceFps >= captureFps) return Decision::Run;

        const auto period = std::max(Clock::duration{1},
            std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1.0 / inferenceFps)));
        // Presentation and acquisition clocks have small phase jitter. Permit
        // at most 1/16 interval (capped at 250 us) of early admission, retaining
        // the original deadline afterwards. This is bounded timing credit,
        // not extra average throughput or a reason to replay an old image.
        const auto jitter = std::min(period / 16,
            std::chrono::duration_cast<Clock::duration>(std::chrono::microseconds(250)));
        if (m_hasDeadline && now + jitter < m_next) return Decision::RateLimited;
        if (!m_hasDeadline || now - m_next >= period) m_next = now + period;
        else m_next += period; // Preserve phase across ordinary capture jitter.
        m_hasDeadline = true;
        return Decision::Run;
    }

    void reset() { *this = InferenceCadence{}; }

private:
    int m_captureFps = 0, m_inferenceFps = 0, m_frameSkip = 0, m_phase = 0;
    bool m_capturePaced = false, m_hasDeadline = false;
    Clock::time_point m_next{};
};

} // namespace vaporlock
