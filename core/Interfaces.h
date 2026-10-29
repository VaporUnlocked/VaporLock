#pragma once

#include "core/Detection.h"
#include "core/Frame.h"
#include "core/RuntimeStats.h"
#include "config/AppConfig.h"

#include <QString>

namespace vaporlock {

class IFrameSource {
public:
    virtual ~IFrameSource() = default;
    virtual Frame capture() = 0;
    virtual QString name() const = 0;
};

class IDetectionEngine {
public:
    virtual ~IDetectionEngine() = default;
    virtual bool loadModel(const QString& path, QString* error) = 0;
    virtual Detections detect(const Frame& frame, const AppConfig& config) = 0;
    virtual QString status() const = 0;
};

class ITracker {
public:
    virtual ~ITracker() = default;
    virtual Tracks update(const Detections& detections, const AppConfig& config) = 0;
    virtual void reset() = 0;
};

class ITargetSelector {
public:
    virtual ~ITargetSelector() = default;
    virtual SelectedTrack select(const Tracks& tracks, const QSize& frameSize, const AppConfig& config) = 0;
    virtual void reset() = 0;
};

class IOutputModule {
public:
    virtual ~IOutputModule() = default;
    virtual void emitTarget(const SelectedTrack& target, const QSize& frameSize, const AppConfig& config) = 0;
    virtual void setActive(bool active) = 0;
    virtual bool isActive() const = 0;
    virtual QString name() const = 0;
};

} // namespace vaporlock
