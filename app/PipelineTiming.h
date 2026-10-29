#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <ostream>

namespace vaporlock::pipeline {

// Fixed storage, no allocation or sorting on the frame path. Percentiles are
// upper bucket edges at 0.25 ms resolution; overflow uses the exact maximum.
// The caller excludes warm-up and starts a fresh instance for each session.
class TimingHistogram {
public:
    void record(double milliseconds) noexcept {
        if (!std::isfinite(milliseconds) || milliseconds < 0.0) return;
        const auto index = milliseconds >= 127.75 ? 511U
            : static_cast<unsigned>(std::floor(milliseconds * 4.0));
        ++m_buckets[index];
        ++m_count;
        m_sum += milliseconds;
        m_maximum = std::max(m_maximum, milliseconds);
    }
    std::uint64_t count() const noexcept { return m_count; }
    double maximum() const noexcept { return m_maximum; }
    double mean() const noexcept { return m_count ? m_sum / m_count : 0.0; }
    double percentile(double fraction) const noexcept {
        if (!m_count) return 0.0;
        const auto rank = std::max<std::uint64_t>(1,
            static_cast<std::uint64_t>(std::ceil(std::clamp(fraction, 0.0, 1.0) * m_count)));
        std::uint64_t cumulative = 0;
        for (unsigned i = 0; i < m_buckets.size(); ++i) {
            cumulative += m_buckets[i];
            if (cumulative >= rank) return i == 511 ? m_maximum
                : std::min(m_maximum, (i + 1) * 0.25);
        }
        return m_maximum;
    }
    void json(std::ostream& stream) const {
        stream << "{\"count\":" << count() << ",\"mean_ms\":" << mean()
            << ",\"p50_ms\":" << percentile(0.50) << ",\"p95_ms\":" << percentile(0.95)
            << ",\"p99_ms\":" << percentile(0.99) << ",\"max_ms\":" << maximum()
            << ",\"overflow_count\":" << m_buckets.back() << '}';
    }
private:
    std::array<std::uint64_t, 512> m_buckets{};
    std::uint64_t m_count = 0;
    double m_sum = 0.0, m_maximum = 0.0;
};

struct PipelineTiming {
    TimingHistogram captureWall,captureAcquire;
    TimingHistogram capture, preprocess, model, postprocess, tracking;
    TimingHistogram loopInterval, observationInterval, completionInterval, observationAge;
    void json(std::ostream& stream) const {
        stream << "{\"schema_version\":1,\"scope\":\"session-after-2s\",\"bucket_ms\":0.25"
            << ",\"percentiles\":\"bucket-upper-bound\",\"overflow_from_ms\":127.75";
        const auto emit = [&](const char* name, const TimingHistogram& histogram) {
            stream << ",\"" << name << "\":"; histogram.json(stream);
        };
        emit("capture_wall",captureWall); emit("capture_acquire",captureAcquire);
        emit("capture",capture); emit("preprocess",preprocess); emit("model",model);
        emit("postprocess",postprocess); emit("tracking",tracking);
        emit("loop_interval",loopInterval); emit("observation_interval",observationInterval);
        emit("completion_interval",completionInterval); emit("observation_age",observationAge);
        stream << '}';
    }
};
} // namespace vaporlock::pipeline
