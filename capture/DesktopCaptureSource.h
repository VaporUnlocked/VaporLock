#pragma once

#include "core/Interfaces.h"

#include <QElapsedTimer>

namespace vaporlock {

class DesktopCaptureSource final : public IFrameSource {
public:
    DesktopCaptureSource();

    Frame capture() override;
    QString name() const override;

private:
    qint64 m_sequence = 0;
    QElapsedTimer m_timer;
};

} // namespace vaporlock
