#include "detection/CudaPreprocess.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <limits>

namespace vaporlock::cuda {
namespace {

__device__ float sampleBilinear(const std::uint8_t* pixels, int width, int height, float x, float y, int channel) {
    const int x0 = static_cast<int>(x);
    const int y0 = static_cast<int>(y);
    const int x1 = min(x0 + 1, width - 1);
    const int y1 = min(y0 + 1, height - 1);
    const float wx = x - x0;
    const float wy = y - y0;
    const auto at = [&](int sampleX, int sampleY) {
        return static_cast<float>(pixels[(sampleY * width + sampleX) * 4 + channel]);
    };
    const float upper = at(x0, y0) * (1.0F - wx) + at(x1, y0) * wx;
    const float lower = at(x0, y1) * (1.0F - wx) + at(x1, y1) * wx;
    return upper * (1.0F - wy) + lower * wy;
}

__global__ void bgraToChw(const std::uint8_t* input, float* output, int sourceWidth, int sourceHeight,
                          int outputWidth, int outputHeight, float scaleX, float scaleY, float padX, float padY) {
    const int destination = blockIdx.x * blockDim.x + threadIdx.x;
    const int count = outputWidth * outputHeight;
    if (destination >= count) return;

    const int destinationX = destination % outputWidth;
    const int destinationY = destination / outputWidth;
    float sourceX = (destinationX - padX + 0.5F) / scaleX - 0.5F;
    float sourceY = (destinationY - padY + 0.5F) / scaleY - 0.5F;
    const int plane = outputWidth * outputHeight;
    if (sourceX < -0.5F || sourceY < -0.5F || sourceX >= sourceWidth - 0.5F || sourceY >= sourceHeight - 0.5F) {
        const float pad = 114.0F / 255.0F;
        output[destination] = pad;
        output[plane + destination] = pad;
        output[plane * 2 + destination] = pad;
        return;
    }
    // Half-pixel resize samples at the image boundary extend the edge pixel;
    // only samples outside that boundary are letterbox padding.
    sourceX = fminf(fmaxf(sourceX, 0.0F), sourceWidth - 1.0F);
    sourceY = fminf(fmaxf(sourceY, 0.0F), sourceHeight - 1.0F);
    output[destination] = sampleBilinear(input, sourceWidth, sourceHeight, sourceX, sourceY, 2) / 255.0F;
    output[plane + destination] = sampleBilinear(input, sourceWidth, sourceHeight, sourceX, sourceY, 1) / 255.0F;
    output[plane * 2 + destination] = sampleBilinear(input, sourceWidth, sourceHeight, sourceX, sourceY, 0) / 255.0F;
}

bool succeeded(cudaError_t status) {
    return status == cudaSuccess;
}

bool validDimensions(int sourceWidth, int sourceHeight, int outputWidth, int outputHeight) {
    // Kernel offsets are signed ints, including BGRA and CHW plane offsets.
    constexpr auto limit = std::numeric_limits<int>::max();
    return sourceWidth > 0 && sourceHeight > 0 && outputWidth > 0 && outputHeight > 0
        && static_cast<std::uint64_t>(sourceWidth) * sourceHeight <= limit / 4U
        && static_cast<std::uint64_t>(outputWidth) * outputHeight <= limit / 3U;
}

}  // namespace

bool activateDevice(int index) { return index >= 0 && cudaSetDevice(index) == cudaSuccess; }

bool selectDevice(int index, std::string& name) {
    int count = 0; cudaDeviceProp properties{};
    if(index < 0 || index >= 64 || cudaGetDeviceCount(&count) != cudaSuccess || index >= count
        || !activateDevice(index) || cudaGetDeviceProperties(&properties,index) != cudaSuccess) return false;
    name = properties.name; return true;
}

bool ensureBuffers(std::uint8_t*& bgra, std::size_t& bgraCapacity, float*& chw, std::size_t& chwCapacity,
                   int sourceWidth, int sourceHeight, int outputWidth, int outputHeight) {
    if (!validDimensions(sourceWidth, sourceHeight, outputWidth, outputHeight)) return false;
    const std::size_t requiredBgra = static_cast<std::size_t>(sourceWidth) * sourceHeight * 4U;
    const std::size_t requiredChw = static_cast<std::size_t>(outputWidth) * outputHeight * 3U;
    if (bgraCapacity != requiredBgra) {
        if (bgra) cudaFree(bgra);
        bgra = nullptr;
        bgraCapacity = 0;
        if (!succeeded(cudaMalloc(&bgra, requiredBgra))) return false;
        bgraCapacity = requiredBgra;
    }
    if (chwCapacity != requiredChw) {
        if (chw) cudaFree(chw);
        chw = nullptr;
        chwCapacity = 0;
        if (!succeeded(cudaMalloc(&chw, requiredChw * sizeof(float)))) return false;
        chwCapacity = requiredChw;
    }
    return true;
}

bool preprocessBgra(const std::uint8_t* hostBgra, std::uint8_t* deviceBgra, float* deviceChw,
                    int sourceWidth, int sourceHeight, int outputWidth, int outputHeight, bool letterbox) {
    if (!hostBgra || !deviceBgra || !deviceChw
        || !validDimensions(sourceWidth, sourceHeight, outputWidth, outputHeight)) return false;
    const std::size_t sourceBytes = static_cast<std::size_t>(sourceWidth) * sourceHeight * 4U;
    float scaleX = static_cast<float>(outputWidth) / sourceWidth;
    float scaleY = static_cast<float>(outputHeight) / sourceHeight;
    float padX = 0.0F, padY = 0.0F;
    if (letterbox) {
        scaleX = scaleY = std::min(scaleX, scaleY);
        // Match the host's geometry used when decoding boxes. Device FMA in
        // floor(output - source * scale) can shift a near-integer pad by 1 px.
        padX = std::floor((outputWidth - sourceWidth * scaleX) * 0.5F);
        padY = std::floor((outputHeight - sourceHeight * scaleY) * 0.5F);
    }
    if (!succeeded(cudaMemcpyAsync(deviceBgra, hostBgra, sourceBytes, cudaMemcpyHostToDevice, nullptr))) return false;
    const int pixels = outputWidth * outputHeight;
    constexpr int threads = 256;
    bgraToChw<<<(pixels + threads - 1) / threads, threads>>>(deviceBgra, deviceChw, sourceWidth, sourceHeight, outputWidth, outputHeight, scaleX, scaleY, padX, padY);
    const auto launch = cudaGetLastError();
    // ORT owns its compute stream. do_copy_in_default_stream does not make it
    // our stream, so finish this stream before lending ORT the input tensor.
    // Also drain the upload on launch failure before the caller frees its frame.
    const auto completed = cudaStreamSynchronize(nullptr);
    return succeeded(launch) && succeeded(completed);
}

void releaseBuffers(std::uint8_t*& bgra, std::size_t& bgraCapacity, float*& chw, std::size_t& chwCapacity) {
    if (bgra) cudaFree(bgra);
    if (chw) cudaFree(chw);
    bgra = nullptr;
    chw = nullptr;
    bgraCapacity = 0;
    chwCapacity = 0;
}

}  // namespace vaporlock::cuda
