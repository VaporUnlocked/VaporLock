#include "config/ProfileManager.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>

#include <utility>

namespace vaporlock {

ProfileManager::ProfileManager(QString rootPath)
    : m_rootPath(std::move(rootPath)) {}

QString ProfileManager::rootPath() const {
    return m_rootPath;
}

QString ProfileManager::defaultProfilePath() const {
    return QDir(m_rootPath).filePath("default.json");
}

AppConfig ProfileManager::loadDefault() const {
    return load(defaultProfilePath());
}

AppConfig ProfileManager::load(const QString& path) const {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return AppConfig::defaults();
    }

    const auto document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) {
        return AppConfig::defaults();
    }

    return AppConfig::fromJson(document.object());
}

bool ProfileManager::save(const QString& path, const AppConfig& config, QString* error) const {
    QFileInfo info(path);
    QDir().mkpath(info.absolutePath());

    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        if (error != nullptr) {
            *error = file.errorString();
        }
        return false;
    }

    file.write(QJsonDocument(config.toJson()).toJson(QJsonDocument::Indented));
    return true;
}

bool ProfileManager::ensureDefaultProfile(QString* error) const {
    if (QFile::exists(defaultProfilePath())) {
        return true;
    }
    return save(defaultProfilePath(), AppConfig::defaults(), error);
}

} // namespace vaporlock
