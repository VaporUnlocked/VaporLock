#pragma once
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace vaporlock::hardware {
// Interception's public C ABI uses natural four-byte alignment, unlike the
// packed KMBox network packets. Preserve any caller packing around this type.
#pragma pack(push, 4)
struct InterceptionMouseStroke {
    std::uint16_t state = 0;
    std::uint16_t flags = 0;
    std::int16_t rolling = 0;
    std::int32_t x = 0;
    std::int32_t y = 0;
    std::uint32_t information = 0;
};
#pragma pack(pop)
static_assert(std::is_standard_layout_v<InterceptionMouseStroke>);
static_assert(sizeof(InterceptionMouseStroke) == 20 && alignof(InterceptionMouseStroke) == 4);
static_assert(offsetof(InterceptionMouseStroke, state) == 0);
static_assert(offsetof(InterceptionMouseStroke, flags) == 2);
static_assert(offsetof(InterceptionMouseStroke, rolling) == 4);
static_assert(offsetof(InterceptionMouseStroke, x) == 8);
static_assert(offsetof(InterceptionMouseStroke, y) == 12);
static_assert(offsetof(InterceptionMouseStroke, information) == 16);
}
