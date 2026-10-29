#pragma once

namespace vaporlock {
inline bool validTriggerThresholds(int press, int release) {
    return release >= 1 && release <= press && press <= 255;
}

class ControllerTrigger {
public:
    bool sample(int value, int press, int release, int identity) {
        if (!validTriggerThresholds(press, release) || value < 0 || value > 255) {
            reset(); return false;
        }
        if (identity != m_identity || press != m_press || release != m_release) {
            reset(); m_identity = identity; m_press = press; m_release = release;
        }
        m_down = value >= (m_down ? release : press);
        return m_down;
    }
    void reset() { m_down = false; m_identity = -1; m_press = m_release = -1; }
private:
    bool m_down = false;
    int m_identity = -1, m_press = -1, m_release = -1;
};
}
