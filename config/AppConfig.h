#pragma once

#include <QJsonObject>
#include <QMetaType>
#include <QString>
#include <QStringList>

namespace vaporlock {

enum class SelectionStrategy {
    ClosestToCenter,
    HighestConfidence,
    LargestObject,
    SmallestObject,
    LowestMovement
};

enum class ActivationMode {
    Hold,
    Toggle
};

struct AppConfig {
    int version = 1;
    QString profileName = "Default";

    QString modelPath;
    double confidenceThreshold = 0.55;
    double iouThreshold = 0.45;
    QStringList classFilter;

    QString captureSource = "desktop";
    int captureFps = 30;

    int maxLostFrames = 18;
    double smoothing = 0.35;

    SelectionStrategy selectionStrategy = SelectionStrategy::ClosestToCenter;
    int acquisitionRadius = 420;
    int retentionRadius = 540;
    double switchingHysteresis = 0.18;

    bool movementEnabled = false;
    double movementGain = 0.18;
    int maxMovementStep = 24;
    ActivationMode activationMode = ActivationMode::Hold;
    QString activationKey = "RightMouseButton";

    bool overlayEnabled = true;
    int queueDepth = 2;
    int frameSkipping = 0;
    QString logLevel = "Info";

    static AppConfig defaults();
    static AppConfig fromJson(const QJsonObject& object);
    [[nodiscard]] QJsonObject toJson() const;
};

QString toString(SelectionStrategy strategy);
SelectionStrategy selectionStrategyFromString(const QString& value);
QString toString(ActivationMode mode);
ActivationMode activationModeFromString(const QString& value);

} // namespace vaporlock

Q_DECLARE_METATYPE(vaporlock::AppConfig)
