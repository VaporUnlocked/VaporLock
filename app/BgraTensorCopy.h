#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <type_traits>
#if defined(_M_X64) || defined(__SSE2__)
#include <emmintrin.h>
#endif

namespace vaporlock::image {

inline bool identityGeometry(int width, int height, int inputWidth, int inputHeight,
                             float scaleX, float scaleY, float padX, float padY) {
    return width > 0 && height > 0 && width == inputWidth && height == inputHeight
        && scaleX == 1.0F && scaleY == 1.0F && padX == 0.0F && padY == 0.0F;
}

// For identity geometry only. Keep division (not reciprocal multiplication)
// identical to the general bilinear preprocessing path's normalization.
template<class T>
void copyBgraToRgb(std::span<const std::uint8_t> bgra, std::span<T> rgb, bool nhwc) {
    static_assert(std::is_same_v<T, float> || std::is_same_v<T, std::uint8_t>);
    if (bgra.empty() || bgra.size() % 4 || rgb.size() % 3 || bgra.size() / 4 != rgb.size() / 3)
        throw std::invalid_argument("Invalid identity preprocessing buffers.");
    const std::size_t pixels = bgra.size() / 4;
    const auto convert = [](std::uint8_t value) -> T {
        if constexpr (std::is_same_v<T, float>) return static_cast<float>(value) / 255.0F;
        else return value;
    };
    if (nhwc) {
        for (std::size_t i = 0; i < pixels; ++i) {
            rgb[i * 3] = convert(bgra[i * 4 + 2]);
            rgb[i * 3 + 1] = convert(bgra[i * 4 + 1]);
            rgb[i * 3 + 2] = convert(bgra[i * 4]);
        }
    } else {
        std::size_t i = 0;
#if defined(_M_X64) || defined(__SSE2__)
        if constexpr (std::is_same_v<T, float>) {
            const auto mask = _mm_set1_epi32(255);
            const auto divisor = _mm_set1_ps(255.0F);
            // Four complete BGRA pixels, unaligned-safe. Use division to retain
            // the scalar path's rounding; never read past the last full group.
            for (; pixels - i >= 4; i += 4) {
                const auto packed = _mm_loadu_si128(reinterpret_cast<const __m128i*>(bgra.data() + i * 4));
                const auto red = _mm_and_si128(_mm_srli_epi32(packed, 16), mask);
                const auto green = _mm_and_si128(_mm_srli_epi32(packed, 8), mask);
                const auto blue = _mm_and_si128(packed, mask);
                _mm_storeu_ps(rgb.data() + i, _mm_div_ps(_mm_cvtepi32_ps(red), divisor));
                _mm_storeu_ps(rgb.data() + pixels + i, _mm_div_ps(_mm_cvtepi32_ps(green), divisor));
                _mm_storeu_ps(rgb.data() + pixels * 2 + i, _mm_div_ps(_mm_cvtepi32_ps(blue), divisor));
            }
        }
#endif
        for (; i < pixels; ++i) {
            rgb[i] = convert(bgra[i * 4 + 2]);
            rgb[pixels + i] = convert(bgra[i * 4 + 1]);
            rgb[pixels * 2 + i] = convert(bgra[i * 4]);
        }
    }
}

} // namespace vaporlock::image
