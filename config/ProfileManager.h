#pragma once

#include "config/AppConfig.h"

#include <QString>

namespace vaporlock {

class ProfileManager {
public:
    explicit ProfileManager(QString rootPath);

    [[nodiscard]] QString rootPath() const;
    [[nodiscard]] QString defaultProfilePath() const;
    [[nodiscard]] AppConfig loadDefault() const;
    [[nodiscard]] AppConfig load(const QString& path) const;
    bool save(const QString& path, const AppConfig& config, QString* error) const;
    bool ensureDefaultProfile(QString* error) const;

private:
    QString m_rootPath;
};

} // namespace vaporlock
