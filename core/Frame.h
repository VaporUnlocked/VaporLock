#pragma once

#include <QDateTime>
#include <QImage>
#include <QMetaType>
#include <QSize>

#include <cstdint>

namespace vaporlock {

struct Frame {
    QImage image;
    QSize sourceSize;
    qint64 sequence = 0;
    QDateTime capturedAtUtc;

    [[nodiscard]] bool isValid() const {
        return !image.isNull() && sourceSize.isValid();
    }
};

} // namespace vaporlock

Q_DECLARE_METATYPE(vaporlock::Frame)
