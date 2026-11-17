#pragma once

#include <algorithm>
#include <array>
#include <sstream>
#include <string>
#include <vector>

namespace vaporlock::privacy {

// Coordinates are in ten-thousandths of the final capture extent. Outward
// rounding ensures a fractional pixel at any mask edge is covered.
using Mask = std::array<int, 4>;
constexpr int scale = 10000;
constexpr size_t maxMasks = 16;

inline bool parse(const std::wstring& value, std::vector<Mask>& result) {
    std::wistringstream stream(value);
    std::vector<Mask> masks;
    for (;;) {
        stream >> std::ws;
        if (stream.eof()) break;
        Mask mask{};
        if (masks.size() == maxMasks || !(stream >> mask[0] >> mask[1] >> mask[2] >> mask[3])
            || mask[0] < 0 || mask[1] < 0 || mask[2] > scale || mask[3] > scale
            || mask[0] >= mask[2] || mask[1] >= mask[3]) return false;
        masks.push_back(mask);
    }
    result = std::move(masks);
    return true;
}

inline void redact(std::vector<unsigned char>& pixels, int width, int height,
                   const std::vector<Mask>& masks) {
    for (const auto& mask : masks) {
        const int x0 = mask[0] * width / scale, x1 = (mask[2] * width + scale - 1) / scale;
        const int y0 = mask[1] * height / scale, y1 = (mask[3] * height + scale - 1) / scale;
        for (int y = y0; y < y1; ++y) {
            auto* row = pixels.data() + (static_cast<size_t>(y) * width + x0) * 4;
            for (int x = x0; x < x1; ++x, row += 4) {
                row[0] = row[1] = row[2] = 0;
                row[3] = 255;
            }
        }
    }
}

}
