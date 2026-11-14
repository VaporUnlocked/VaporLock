#pragma once

#include <limits>
#include <string_view>

namespace vaporlock::pipeline {

inline constexpr int kMaximumFrameRate = std::numeric_limits<int>::max();

inline bool parseFrameRate(std::wstring_view text, int& fps) noexcept {
    if (text.empty()) return false;
    int parsed = 0;
    for (const auto character : text) {
        if (character < L'0' || character > L'9') return false;
        const int digit = character - L'0';
        if (parsed > (kMaximumFrameRate - digit) / 10) return false;
        parsed = parsed * 10 + digit;
    }
    if (parsed == 0) return false;
    fps = parsed;
    return true;
}

} // namespace vaporlock::pipeline
