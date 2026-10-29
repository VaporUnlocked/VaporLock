#pragma once

#include <QMetaType>
#include <QString>

namespace vaporlock {

struct RuntimeStats {
    double captureFps = 0.0;
    double pipelineFps = 0.0;
    double captureLatencyMs = 0.0;
    double inferenceLatencyMs = 0.0;
    double trackingLatencyMs = 0.0;
    int detectionCount = 0;
    int trackCount = 0;
    bool outputActive = false;
    QString detectorStatus;
};

} // namespace vaporlock

Q_DECLARE_METATYPE(vaporlock::RuntimeStats)
