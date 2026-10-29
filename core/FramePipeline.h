#pragma once

#include "core/Detection.h"
#include "core/Interfaces.h"
#include "core/RuntimeStats.h"

#include <QElapsedTimer>
#include <QObject>
#include <QTimer>

#include <memory>

namespace vaporlock {

class FramePipeline final : public QObject {
    Q_OBJECT

public:
    explicit FramePipeline(QObject* parent = nullptr);

    void setFrameSource(std::unique_ptr<IFrameSource> source);
    void setDetector(std::unique_ptr<IDetectionEngine> detector);
    void setTracker(std::unique_ptr<ITracker> tracker);
    void setSelector(std::unique_ptr<ITargetSelector> selector);
    void setOutput(std::unique_ptr<IOutputModule> output);

    void start();
    void stop();
    void setConfig(AppConfig config);
    [[nodiscard]] AppConfig config() const;
    [[nodiscard]] bool isRunning() const;
    [[nodiscard]] IDetectionEngine* detector() const;
    [[nodiscard]] IOutputModule* output() const;

signals:
    void frameReady(const vaporlock::Frame& frame, const vaporlock::Tracks& tracks, const vaporlock::SelectedTrack& selected);
    void statsReady(const vaporlock::RuntimeStats& stats);
    void logMessage(const QString& subsystem, const QString& message);

private slots:
    void processFrame();

private:
    std::unique_ptr<IFrameSource> m_source;
    std::unique_ptr<IDetectionEngine> m_detector;
    std::unique_ptr<ITracker> m_tracker;
    std::unique_ptr<ITargetSelector> m_selector;
    std::unique_ptr<IOutputModule> m_output;
    AppConfig m_config;
    QTimer m_timer;
    RuntimeStats m_stats;
    qint64 m_processedFrames = 0;
    QElapsedTimer m_uptime;
};

} // namespace vaporlock
