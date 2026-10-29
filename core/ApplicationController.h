#pragma once

#include "config/ProfileManager.h"
#include "core/FramePipeline.h"

#include <QObject>

namespace vaporlock {

class ApplicationController final : public QObject {
    Q_OBJECT

public:
    explicit ApplicationController(QObject* parent = nullptr);

    [[nodiscard]] FramePipeline* pipeline();
    [[nodiscard]] const FramePipeline* pipeline() const;
    [[nodiscard]] AppConfig config() const;
    [[nodiscard]] QString profilePath() const;

public slots:
    void start();
    void stop();
    void applyConfig(const vaporlock::AppConfig& config);
    bool loadProfile(const QString& path);
    bool saveProfile(const QString& path);
    bool loadModel(const QString& path, QString* error);

signals:
    void configChanged(const vaporlock::AppConfig& config);
    void profilePathChanged(const QString& path);
    void logMessage(const QString& subsystem, const QString& message);

private:
    ProfileManager m_profiles;
    FramePipeline m_pipeline;
    AppConfig m_config;
    QString m_profilePath;
};

} // namespace vaporlock
