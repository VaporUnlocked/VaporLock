#pragma once

#include <cstdint>

namespace vaporlock::xinput_proxy {

inline constexpr std::uint32_t kMagic = 0x31504C56; // "VLP1"
inline constexpr std::uint32_t kVersion = 1;
inline constexpr wchar_t kMappingName[] = L"Local\\VaporLockXInputProxy";

// This is a deliberately small, non-identifying shared-memory contract. The
// target-side proxy reads only the current additive stick request.
#pragma pack(push, 1)
struct SharedState {
    std::uint32_t magic = kMagic;
    std::uint32_t version = kVersion;
    std::int16_t right_x = 0;
    std::int16_t right_y = 0;
    std::int16_t left_x = 0;
    std::int16_t left_y = 0;
    std::uint8_t right_enabled = 0;
    std::uint8_t left_enabled = 0;
    std::uint8_t reserved[6]{};
};
#pragma pack(pop)

static_assert(sizeof(SharedState) == 24);

} // namespace vaporlock::xinput_proxy
