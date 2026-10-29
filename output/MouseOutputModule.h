#pragma once

#include "core/Interfaces.h"

namespace vaporlock {

class MouseOutputModule final : public IOutputModule {
public:
    void emitTarget(const SelectedTrack& target, const QSize& frameSize, const AppConfig& config) override;
    void setActive(bool active) override;
    bool isActive() const override;
    QString name() const override;

private:
    bool m_enabled = false;
    bool m_outputActive = false;
    bool m_toggleActive = false;
    bool m_previousKeyDown = false;
};

} // namespace vaporlock
