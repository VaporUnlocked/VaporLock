#pragma once

#include "core/Interfaces.h"

#include <unordered_map>

namespace vaporlock {

class CentroidTracker final : public ITracker {
public:
    Tracks update(const Detections& detections, const AppConfig& config) override;
    void reset() override;

private:
    struct State {
        Track track;
        QPointF smoothedCenter;
    };

    int m_nextId = 1;
    std::unordered_map<int, State> m_tracks;
};

} // namespace vaporlock
