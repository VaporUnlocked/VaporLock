#include "config/AppConfig.h"

#include <QJsonArray>

namespace vaporlock {

namespace {

QJsonObject childObject(const QJsonObject& parent, const QString& key) {
    return parent.value(key).toObject();
}

QStringList stringListFromJson(const QJsonArray& values) {
    QStringList result;
    for (const auto& value : values) {
        if (value.isString()) {
            result.push_back(value.toString());
        }
    }
    return result;
}

QJsonArray stringListToJson(const QStringList& values) {
    QJsonArray result;
    for (const auto& value : values) {
        result.push_back(value);
    }
    return result;
}

} // namespace

AppConfig AppConfig::defaults() {
    return {};
}

AppConfig AppConfig::fromJson(const QJsonObject& object) {
    AppConfig config = AppConfig::defaults();
    config.version = object.value("version").toInt(config.version);

    const auto general = childObject(object, "general");
    config.profileName = general.value("profileName").toString(config.profileName);

    const auto model = childObject(object, "model");
    config.modelPath = model.value("path").toString(config.modelPath);
    config.confidenceThreshold = model.value("confidenceThreshold").toDouble(config.confidenceThreshold);
    config.iouThreshold = model.value("iouThreshold").toDouble(config.iouThreshold);
    config.classFilter = stringListFromJson(model.value("classFilter").toArray());

    const auto capture = childObject(object, "capture");
    config.captureSource = capture.value("source").toString(config.captureSource);
    config.captureFps = capture.value("fps").toInt(config.captureFps);

    const auto tracking = childObject(object, "tracking");
    config.maxLostFrames = tracking.value("maxLostFrames").toInt(config.maxLostFrames);
    config.smoothing = tracking.value("smoothing").toDouble(config.smoothing);

    const auto selection = childObject(object, "selection");
    config.selectionStrategy = selectionStrategyFromString(selection.value("strategy").toString(toString(config.selectionStrategy)));
    config.acquisitionRadius = selection.value("acquisitionRadius").toInt(config.acquisitionRadius);
    config.retentionRadius = selection.value("retentionRadius").toInt(config.retentionRadius);
    config.switchingHysteresis = selection.value("switchingHysteresis").toDouble(config.switchingHysteresis);

    const auto movement = childObject(object, "movement");
    config.movementEnabled = movement.value("enabled").toBool(config.movementEnabled);
    config.movementGain = movement.value("gain").toDouble(config.movementGain);
    config.maxMovementStep = movement.value("maxStep").toInt(config.maxMovementStep);
    config.activationMode = activationModeFromString(movement.value("activationMode").toString(toString(config.activationMode)));
    config.activationKey = movement.value("activationKey").toString(config.activationKey);

    const auto overlay = childObject(object, "overlay");
    config.overlayEnabled = overlay.value("enabled").toBool(config.overlayEnabled);

    const auto performance = childObject(object, "performance");
    config.queueDepth = performance.value("queueDepth").toInt(config.queueDepth);
    config.frameSkipping = performance.value("frameSkipping").toInt(config.frameSkipping);

    const auto diagnostics = childObject(object, "diagnostics");
    config.logLevel = diagnostics.value("logLevel").toString(config.logLevel);

    return config;
}

QJsonObject AppConfig::toJson() const {
    QJsonObject object;
    object["version"] = version;
    object["general"] = QJsonObject{{"profileName", profileName}};
    object["model"] = QJsonObject{
        {"path", modelPath},
        {"confidenceThreshold", confidenceThreshold},
        {"iouThreshold", iouThreshold},
        {"classFilter", stringListToJson(classFilter)}
    };
    object["capture"] = QJsonObject{{"source", captureSource}, {"fps", captureFps}};
    object["tracking"] = QJsonObject{{"maxLostFrames", maxLostFrames}, {"smoothing", smoothing}};
    object["selection"] = QJsonObject{
        {"strategy", toString(selectionStrategy)},
        {"acquisitionRadius", acquisitionRadius},
        {"retentionRadius", retentionRadius},
        {"switchingHysteresis", switchingHysteresis}
    };
    object["movement"] = QJsonObject{
        {"enabled", movementEnabled},
        {"gain", movementGain},
        {"maxStep", maxMovementStep},
        {"activationMode", toString(activationMode)},
        {"activationKey", activationKey}
    };
    object["overlay"] = QJsonObject{{"enabled", overlayEnabled}};
    object["performance"] = QJsonObject{{"queueDepth", queueDepth}, {"frameSkipping", frameSkipping}};
    object["diagnostics"] = QJsonObject{{"logLevel", logLevel}};
    return object;
}

QString toString(SelectionStrategy strategy) {
    switch (strategy) {
    case SelectionStrategy::HighestConfidence:
        return "confidence";
    case SelectionStrategy::LargestObject:
        return "largest";
    case SelectionStrategy::SmallestObject:
        return "smallest";
    case SelectionStrategy::LowestMovement:
        return "lowestMovement";
    case SelectionStrategy::ClosestToCenter:
    default:
        return "center";
    }
}

SelectionStrategy selectionStrategyFromString(const QString& value) {
    const auto normalized = value.trimmed().toLower();
    if (normalized == "confidence") {
        return SelectionStrategy::HighestConfidence;
    }
    if (normalized == "largest") {
        return SelectionStrategy::LargestObject;
    }
    if (normalized == "smallest") {
        return SelectionStrategy::SmallestObject;
    }
    if (normalized == "lowestmovement") {
        return SelectionStrategy::LowestMovement;
    }
    return SelectionStrategy::ClosestToCenter;
}

QString toString(ActivationMode mode) {
    return mode == ActivationMode::Toggle ? "toggle" : "hold";
}

ActivationMode activationModeFromString(const QString& value) {
    return value.trimmed().compare("toggle", Qt::CaseInsensitive) == 0 ? ActivationMode::Toggle : ActivationMode::Hold;
}

} // namespace vaporlock
