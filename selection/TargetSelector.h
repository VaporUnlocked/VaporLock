#pragma once

#include "core/Interfaces.h"

namespace vaporlock {

class TargetSelector final : public ITargetSelector {
public:
    SelectedTrack select(const Tracks& tracks, const QSize& frameSize, const AppConfig& config) override;
    void reset() override;

private:
    int m_retainedId = 0;
};

} // namespace vaporlock
