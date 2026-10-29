#pragma once
#include <chrono>
#include <cstdint>
#include <vector>

namespace vaporlock {
// Probe only after image delivery stalls. Two GDI observations are compared
// with each other; DXGI/GDI composition or alpha differences are not motion.
class CaptureRecovery {
public:
    using Clock = std::chrono::steady_clock;
    using Time = Clock::time_point;
    static constexpr auto interval = std::chrono::milliseconds(250);
    static constexpr auto starvation = std::chrono::milliseconds(100);
    static constexpr auto confirmation = std::chrono::milliseconds(25);
    void reset(Time now) {
        lastImage = now; lastProbe = {}; recovering = false; clearProbe();
    }
    bool probeDue(Time now) const {
        const auto delay = previous.empty() ? interval : confirmation;
        return recovering || (now-lastImage >= starvation &&
               (lastProbe == Time{} || now-lastProbe >= delay));
    }
    void probing(Time now) { lastProbe = now; ++probes; }
    void failedProbe() { clearProbe(); }
    void recover() {
        if (!recovering) ++transitions;
        recovering = true; clearProbe();
    }
    bool acceptPresentation(Time presented, Time now, Time lastAccepted) {
        // A proven alternate source stays selected until the caller explicitly
        // resets the capture geometry/session. Brief DXGI bursts are not proof
        // that a stalled source has become reliable again.
        if (recovering) return false;
        if (presented == Time{} || presented <= lastAccepted || presented > now || now-presented > interval)
            return false;
        return true;
    }
    void presented(Time now) {
        lastImage = now;
        lastProbe = {};
        clearProbe();
    }
    bool progressing(const std::vector<unsigned char>& pixels, int width, int height) {
        if (width <= 0 || height <= 0 || static_cast<std::uint64_t>(width)*height*4 != pixels.size()) {
            clearProbe(); return false;
        }
        bool changed = false;
        if (width == probeWidth && height == probeHeight && pixels.size() == previous.size()) {
            for (std::size_t i=0; i<pixels.size(); i+=4) {
                if (pixels[i]!=previous[i] || pixels[i+1]!=previous[i+1] || pixels[i+2]!=previous[i+2]) {
                    changed = true; break;
                }
            }
            if (!changed) { clearProbe(); return false; }
        }
        previous = pixels; probeWidth = width; probeHeight = height;
        return changed;
    }
    bool recovering = false;
    std::uint64_t probes = 0, transitions = 0;
private:
    void clearProbe() { previous.clear(); probeWidth = probeHeight = 0; }
    Time lastImage{}, lastProbe{};
    int probeWidth = 0, probeHeight = 0;
    std::vector<unsigned char> previous;
};
}
