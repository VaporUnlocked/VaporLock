#pragma once

#include "core/Interfaces.h"

#include <QElapsedTimer>

namespace vaporlock {

class OnnxDetectionEngine final : public IDetectionEngine {
public:
    OnnxDetectionEngine();

    bool loadModel(const QString& path, QString* error) override;
    Detections detect(const Frame& frame, const AppConfig& config) override;
    QString status() const override;

private:
    QString m_modelPath;
    QString m_status;
    QElapsedTimer m_timer;
};

} // namespace vaporlock
