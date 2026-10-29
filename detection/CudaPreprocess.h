#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace vaporlock::cuda {
bool selectDevice(int index, std::string& name);
bool activateDevice(int index);

bool ensureBuffers(std::uint8_t*& bgra, std::size_t& bgraCapacity, float*& chw, std::size_t& chwCapacity,
                   int sourceWidth, int sourceHeight, int outputWidth, int outputHeight);

// On success the uploaded input and output tensor are complete, including when
// the caller supplied pinned host memory. No inference stream ordering assumed.
bool preprocessBgra(const std::uint8_t* hostBgra, std::uint8_t* deviceBgra, float* deviceChw,
                    int sourceWidth, int sourceHeight, int outputWidth, int outputHeight, bool letterbox);

void releaseBuffers(std::uint8_t*& bgra, std::size_t& bgraCapacity, float*& chw, std::size_t& chwCapacity);

}  // namespace vaporlock::cuda
