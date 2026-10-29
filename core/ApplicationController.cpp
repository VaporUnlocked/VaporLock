#include "core/ApplicationController.h"

#include "capture/DesktopCaptureSource.h"
#include "detection/OnnxDetectionEngine.h"
#include "output/MouseOutputModule.h"
#include "selection/TargetSelector.h"
#include "tracking/CentroidTracker.h"

#include <QCoreApplication>
#include <QDir>

#include <memory>

namespace vaporlock {

ApplicationController::ApplicationController(QObject* parent)
    : QObject(parent)
    , m_profiles(QDir(QCoreApplication::applicationDirPath()).filePath("profiles")) {
    QString error;
    if (!m_profiles.ensureDefaultProfile(&error)) {
        emit logMessage("Profiles", QString("Could not create default profile: %1").arg(error));
    }

    m_config = m_profiles.loadDefault();
    m_profilePath = m_profiles.defaultProfilePath();

    m_pipeline.setFrameSource(std::make_unique<DesktopCaptureSource>());
    m_pipeline.setDetector(std::make_unique<OnnxDetectionEngine>());
    m_pipeline.setTracker(std::make_unique<CentroidTracker>());
    m_pipeline.setSelector(std::make_unique<TargetSelector>());
    m_pipeline.setOutput(std::make_unique<MouseOutputModule>());
    m_pipeline.setConfig(m_config);

    connect(&m_pipeline, &FramePipeline::logMessage, this, &ApplicationController::logMessage);
}

FramePipeline* ApplicationController::pipeline() {
    return &m_pipeline;
}

const FramePipeline* ApplicationController::pipeline() const {
    return &m_pipeline;
}

AppConfig ApplicationController::config() const {
    return m_config;
}

QString ApplicationController::profilePath() const {
    return m_profilePath;
}

void ApplicationController::start() {
    m_pipeline.start();
}

void ApplicationController::stop() {
    m_pipeline.stop();
}

void ApplicationController::applyConfig(const AppConfig& config) {
    m_config = config;
    m_pipeline.setConfig(m_config);
    emit configChanged(m_config);
}

bool ApplicationController::loadProfile(const QString& path) {
    m_config = m_profiles.load(path);
    m_profilePath = path;
    m_pipeline.setConfig(m_config);
    emit configChanged(m_config);
    emit profilePathChanged(m_profilePath);
    emit logMessage("Profiles", QString("Loaded %1").arg(path));
    return true;
}

bool ApplicationController::saveProfile(const QString& path) {
    QString error;
    if (!m_profiles.save(path, m_config, &error)) {
        emit logMessage("Profiles", QString("Save failed: %1").arg(error));
        return false;
    }

    m_profilePath = path;
    emit profilePathChanged(m_profilePath);
    emit logMessage("Profiles", QString("Saved %1").arg(path));
    return true;
}

bool ApplicationController::loadModel(const QString& path, QString* error) {
    if (m_pipeline.detector() == nullptr) {
        if (error != nullptr) {
            *error = "No detector is configured.";
        }
        return false;
    }

    if (!m_pipeline.detector()->loadModel(path, error)) {
        return false;
    }

    m_config.modelPath = path;
    m_pipeline.setConfig(m_config);
    emit configChanged(m_config);
    emit logMessage("Detection", m_pipeline.detector()->status());
    return true;
}

} // namespace vaporlock
