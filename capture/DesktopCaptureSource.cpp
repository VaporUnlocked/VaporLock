#include "capture/DesktopCaptureSource.h"

#include <QGuiApplication>
#include <QPainter>
#include <QScreen>

#include <cmath>

namespace vaporlock {

DesktopCaptureSource::DesktopCaptureSource() {
    m_timer.start();
}

Frame DesktopCaptureSource::capture() {
    Frame frame;
    frame.sequence = ++m_sequence;
    frame.capturedAtUtc = QDateTime::currentDateTimeUtc();

    if (auto* screen = QGuiApplication::primaryScreen()) {
        const auto pixmap = screen->grabWindow(0);
        frame.image = pixmap.toImage().convertToFormat(QImage::Format_RGB32);
        frame.sourceSize = frame.image.size();
    }

    if (!frame.isValid()) {
        constexpr int width = 1280;
        constexpr int height = 720;
        frame.image = QImage(width, height, QImage::Format_RGB32);
        frame.image.fill(QColor(17, 24, 39));

        QPainter painter(&frame.image);
        painter.setRenderHint(QPainter::Antialiasing);
        painter.fillRect(QRect(0, 0, width, height), QColor(22, 30, 46));
        painter.setPen(QPen(QColor(86, 185, 255), 3));
        const auto seconds = m_timer.elapsed() / 1000.0;
        const int x = static_cast<int>((std::sin(seconds * 0.9) * 0.35 + 0.5) * width);
        const int y = static_cast<int>((std::cos(seconds * 0.7) * 0.25 + 0.5) * height);
        painter.drawEllipse(QPoint(x, y), 52, 52);
        painter.setPen(QColor(218, 230, 242));
        painter.drawText(24, 42, "Vapor Lock synthetic capture");
        frame.sourceSize = frame.image.size();
    }

    return frame;
}

QString DesktopCaptureSource::name() const {
    return "Desktop Capture";
}

} // namespace vaporlock
