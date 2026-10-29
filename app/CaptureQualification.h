#pragma once
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <vector>

// Capture eligibility retains only private current-frame facts, never boxes,
// labels, mask coefficients or later training annotations.
struct CaptureFrameIdentity {
    std::uint64_t session = 0, sourceGeneration = 0, frameId = 0, configRevision = 0;
    bool operator==(const CaptureFrameIdentity&) const = default;
};

struct CaptureQualification {
    CaptureFrameIdentity identity;
    bool observed = false, qualifies = false;

    template<class Detection>
    void observeFeed(const std::vector<Detection>& feed, double cutoff, bool succeeded) {
        observed = succeeded;
        qualifies = false;
        for (const auto& detection : feed) {
            const auto& box = detection.box;
            observe(detection.confidence, cutoff, true, box.x, box.y, box.x + box.w, box.y + box.h);
        }
    }

    void observe(double score, double cutoff, bool allowedClass,
                 double x1, double y1, double x2, double y2) {
        if (observed && allowedClass && std::isfinite(score) && score >= 0.0 && score <= 1.0
            && std::isfinite(cutoff) && cutoff >= 0.01 && cutoff <= 1.0 && score >= cutoff
            && std::isfinite(x1) && std::isfinite(y1) && std::isfinite(x2) && std::isfinite(y2)
            && x2 > x1 && y2 > y1) qualifies = true;
    }
};

template<class TargetDetection>
struct DetectionBatch {
    std::vector<TargetDetection> targeting;
    CaptureQualification capture;
};

inline bool captureBgraBytes(int width, int height, std::size_t actualBytes, std::size_t& bytes) {
    constexpr std::size_t maximumPixels = 16777216, maximumBytes = 64U * 1024U * 1024U;
    bytes = 0;
    if (width <= 0 || height <= 0 || width > 16384 || height > 16384) return false;
    const auto w = static_cast<std::size_t>(width), h = static_cast<std::size_t>(height);
    if (w > maximumPixels / h) return false;
    const auto pixels = w * h;
    if (pixels > maximumBytes / 4U) return false;
    bytes = pixels * 4U;
    return bytes == actualBytes;
}

inline bool captureTensorElements(const std::vector<std::int64_t>& shape, std::size_t actualElements,
                                  bool requiresSingleBatch = true) {
    if (shape.empty() || (requiresSingleBatch && shape[0] != 1)) return false;
    std::size_t product = 1;
    for (const auto dimension : shape) {
        if (dimension <= 0 || static_cast<std::uint64_t>(dimension) > actualElements
            || product > actualElements / static_cast<std::size_t>(dimension)) return false;
        product *= static_cast<std::size_t>(dimension);
    }
    return product == actualElements;
}
