#include "core/FramePipeline.h"

#include <QElapsedTimer>

#include <algorithm>
#include <utility>

namespace vaporlock {

FramePipeline::FramePipeline(QObject* parent)
    : QObject(parent) {
    connect(&m_timer, &QTimer::timeout, this, &FramePipeline::processFrame);
    m_uptime.start();
}

void FramePipeline::setFrameSource(std::unique_ptr<IFrameSource> source) {
    m_source = std::move(source);
}

void FramePipeline::setDetector(std::unique_ptr<IDetectionEngine> detector) {
    m_detector = std::move(detector);
}

void FramePipeline::setTracker(std::unique_ptr<ITracker> tracker) {
    m_tracker = std::move(tracker);
}

void FramePipeline::setSelector(std::unique_ptr<ITargetSelector> selector) {
    m_selector = std::move(selector);
}

void FramePipeline::setOutput(std::unique_ptr<IOutputModule> output) {
    m_output = std::move(output);
}

void FramePipeline::start() {
    if (isRunning()) {
        return;
    }

    const int fps = std::max(1, m_config.captureFps);
    m_timer.start(1000 / fps);
    emit logMessage("Pipeline", "Started");
}

void FramePipeline::stop() {
    if (!isRunning()) {
        return;
    }

    m_timer.stop();
    if (m_output) {
        m_output->setActive(false);
    }
    emit logMessage("Pipeline", "Stopped");
}

void FramePipeline::setConfig(AppConfig config) {
    m_config = std::move(config);
    if (isRunning()) {
        const int fps = std::max(1, m_config.captureFps);
        m_timer.setInterval(1000 / fps);
    }
}

AppConfig FramePipeline::config() const {
    return m_config;
}

bool FramePipeline::isRunning() const {
    return m_timer.isActive();
}

IDetectionEngine* FramePipeline::detector() const {
    return m_detector.get();
}

IOutputModule* FramePipeline::output() const {
    return m_output.get();
}

void FramePipeline::processFrame() {
    if (!m_source || !m_detector || !m_tracker || !m_selector || !m_output) {
        return;
    }

    QElapsedTimer total;
    total.start();

    QElapsedTimer stage;
    stage.start();
    auto frame = m_source->capture();
    m_stats.captureLatencyMs = stage.elapsed();

    stage.restart();
    auto detections = m_detector->detect(frame, m_config);
    m_stats.inferenceLatencyMs = stage.elapsed();

    stage.restart();
    auto tracks = m_tracker->update(detections, m_config);
    auto selected = m_selector->select(tracks, frame.sourceSize, m_config);
    m_stats.trackingLatencyMs = stage.elapsed();

    const bool active = m_config.movementEnabled;
    m_output->setActive(active);
    m_output->emitTarget(selected, frame.sourceSize, m_config);

    for (auto& track : tracks) {
        track.selected = selected.has_value() && selected->id == track.id;
    }

    ++m_processedFrames;
    const auto elapsedSeconds = std::max(0.001, m_uptime.elapsed() / 1000.0);
    m_stats.pipelineFps = m_processedFrames / elapsedSeconds;
    m_stats.captureFps = 1000.0 / std::max<qint64>(1, total.elapsed());
    m_stats.detectionCount = static_cast<int>(detections.size());
    m_stats.trackCount = static_cast<int>(tracks.size());
    m_stats.outputActive = m_output->isActive();
    m_stats.detectorStatus = m_detector->status();

    emit frameReady(frame, tracks, selected);
    emit statsReady(m_stats);
}

} // namespace vaporlock
