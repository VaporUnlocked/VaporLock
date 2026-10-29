#include "output/MouseOutputModule.h"

#include <QPointF>

#include <algorithm>
#include <cmath>

#if defined(Q_OS_WIN)
#include <windows.h>
#endif

namespace vaporlock {

namespace {

bool activationKeyDown(const AppConfig& config) {
#if defined(Q_OS_WIN)
    if (config.activationKey.compare("RightMouseButton", Qt::CaseInsensitive) == 0) {
        return (GetAsyncKeyState(VK_RBUTTON) & 0x8000) != 0;
    }
#else
    Q_UNUSED(config);
#endif
    return true;
}

} // namespace

void MouseOutputModule::emitTarget(const SelectedTrack& target, const QSize& frameSize, const AppConfig& config) {
    const bool keyDown = activationKeyDown(config);
    if (config.activationMode == ActivationMode::Toggle && keyDown && !m_previousKeyDown) {
        m_toggleActive = !m_toggleActive;
    }
    m_previousKeyDown = keyDown;
    m_outputActive = m_enabled && config.movementEnabled && (config.activationMode == ActivationMode::Hold ? keyDown : m_toggleActive);

    if (!m_outputActive || !target.has_value() || !frameSize.isValid()) {
        return;
    }

    const QPointF frameCenter(frameSize.width() / 2.0, frameSize.height() / 2.0);
    const QPointF delta = target->box.center() - frameCenter;
    const int maxStep = std::max(1, config.maxMovementStep);
    const int dx = std::clamp(static_cast<int>(std::round(delta.x() * config.movementGain)), -maxStep, maxStep);
    const int dy = std::clamp(static_cast<int>(std::round(delta.y() * config.movementGain)), -maxStep, maxStep);

    if (dx == 0 && dy == 0) {
        return;
    }

#if defined(Q_OS_WIN)
    INPUT input{};
    input.type = INPUT_MOUSE;
    input.mi.dx = dx;
    input.mi.dy = dy;
    input.mi.dwFlags = MOUSEEVENTF_MOVE;
    SendInput(1, &input, sizeof(INPUT));
#else
    Q_UNUSED(dx);
    Q_UNUSED(dy);
#endif
}

void MouseOutputModule::setActive(bool active) {
    m_enabled = active;
    if (!m_enabled) {
        m_outputActive = false;
        m_toggleActive = false;
    }
}

bool MouseOutputModule::isActive() const {
    return m_outputActive;
}

QString MouseOutputModule::name() const {
    return "Win32 Relative Mouse";
}

} // namespace vaporlock
