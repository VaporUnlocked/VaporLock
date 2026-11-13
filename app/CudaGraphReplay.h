#pragma once

#include <windows.h>
#include <onnxruntime_cxx_api.h>

#include <array>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <vector>

namespace vaporlock::inference {

inline void appendCudaProvider(Ort::SessionOptions& options, int device, bool replay) {
    Ort::CUDAProviderOptions cuda;
    cuda.Update({{"device_id", std::to_string(device)}, {"do_copy_in_default_stream", "1"},
                 {"enable_cuda_graph", replay ? "1" : "0"},
                 {"cudnn_conv_algo_search", replay ? "HEURISTIC" : "EXHAUSTIVE"}});
    options.AppendExecutionProvider_CUDA_V2(*cuda);
}

// This path owns CUDA device allocations separately from the ordinary ORT Run.
// Their addresses stay fixed for the session and each replay copies fresh data.
class CudaGraphReplay {
public:
    CudaGraphReplay(Ort::Session& session, HMODULE ortModule, int device)
        : m_cuda(ortModule), m_device(device), m_memory("Cuda", OrtDeviceAllocator, device, OrtMemTypeDefault),
          m_gpuAllocator(session, m_memory), m_binding(session) {
        if (session.GetInputCount() != 1 || session.GetOutputCount() == 0)
            throw std::runtime_error("CUDA Graph requires one image input and tensor outputs.");
        const auto inputTypeInfo = session.GetInputTypeInfo(0);
        const auto input = tensorInfo(inputTypeInfo);
        const auto shape = input.GetShape();
        const auto inputType = input.GetElementType();
        if (shape.size() != 4 || shape.front() != 1 || (shape[1] != 3 && shape[3] != 3)
            || (inputType != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT && inputType != ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8))
            throw std::runtime_error("CUDA Graph requires a fixed image input.");
        m_inputBytes = tensorBytes(input);
        m_gpuInput = Ort::Value::CreateTensor(m_gpuAllocator, shape.data(), shape.size(), inputType);
        m_binding.BindInput(session.GetInputNameAllocated(0, m_cpuAllocator).get(), m_gpuInput);
        std::size_t outputBudget = maxBytes;
        for (std::size_t index = 0; index < session.GetOutputCount(); ++index) {
            const auto outputTypeInfo = session.GetOutputTypeInfo(index);
            const auto info = tensorInfo(outputTypeInfo);
            const auto outputShape = info.GetShape();
            const auto bytes = tensorBytes(info);
            if (bytes > outputBudget) throw std::runtime_error("CUDA Graph output storage exceeds the allocation bound.");
            outputBudget -= bytes;
            m_outputBytes.push_back(bytes);
            m_gpuOutputs.push_back(Ort::Value::CreateTensor(
                m_gpuAllocator, outputShape.data(), outputShape.size(), info.GetElementType()));
            m_outputs.push_back(Ort::Value::CreateTensor(
                m_cpuAllocator, outputShape.data(), outputShape.size(), info.GetElementType()));
            m_binding.BindOutput(session.GetOutputNameAllocated(index, m_cpuAllocator).get(), m_gpuOutputs.back());
        }
        std::vector<unsigned char> warmInput(m_inputBytes, 0);
        Ort::RunOptions warmup;
        run(session, warmup, warmInput.data(), false);
        run(session, warmup, warmInput.data(), false);
    }

    const std::vector<Ort::Value>& run(Ort::Session& session, const Ort::RunOptions& options,
                                      const void* input, bool deviceInput) {
        // Copying from pageable host memory may return before the DMA completes.
        // Synchronize the transfer before ORT queues work on its own stream.
        constexpr int hostToDevice = 1, deviceToHost = 2, deviceToDevice = 3;
        if (m_cuda.setDevice(m_device) != 0
            || m_cuda.copy(m_gpuInput.GetTensorMutableRawData(), input, m_inputBytes,
                           deviceInput ? deviceToDevice : hostToDevice) != 0
            || m_cuda.syncStream(nullptr) != 0)
            throw std::runtime_error("CUDA Graph input transfer failed.");
        session.Run(options, m_binding);
        m_binding.SynchronizeOutputs();
        for (std::size_t index = 0; index < m_outputs.size(); ++index) {
            if (m_cuda.copy(m_outputs[index].GetTensorMutableRawData(), m_gpuOutputs[index].GetTensorRawData(),
                            m_outputBytes[index], deviceToHost) != 0)
                throw std::runtime_error("CUDA Graph output transfer failed.");
        }
        return m_outputs;
    }

private:
    static constexpr std::size_t maxBytes = 512U * 1024U * 1024U;

    static Ort::ConstTensorTypeAndShapeInfo tensorInfo(const Ort::TypeInfo& type) {
        if (type.GetONNXType() != ONNX_TYPE_TENSOR)
            throw std::runtime_error("CUDA Graph requires tensor inputs and outputs.");
        return type.GetTensorTypeAndShapeInfo();
    }

    static std::size_t tensorBytes(const Ort::ConstTensorTypeAndShapeInfo& info) {
        const auto type = info.GetElementType();
        if (type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT && type != ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8)
            throw std::runtime_error("CUDA Graph does not support this tensor type.");
        std::size_t bytes = type == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ? sizeof(float) : sizeof(std::uint8_t);
        for (auto dimension : info.GetShape()) {
            if (dimension <= 0 || static_cast<std::uint64_t>(dimension) > maxBytes / bytes)
                throw std::runtime_error("CUDA Graph requires fixed tensor dimensions within the allocation bound.");
            bytes *= static_cast<std::size_t>(dimension);
        }
        return bytes;
    }

    struct TransferApi {
        HMODULE module = nullptr;
        int(WINAPI* copy)(void*, const void*, std::size_t, int) = nullptr;
        int(WINAPI* syncStream)(void*) = nullptr;
        int(WINAPI* setDevice)(int) = nullptr;

        ~TransferApi() { if (module) FreeLibrary(module); }

        explicit TransferApi(HMODULE ortModule) {
            std::vector<wchar_t> file(32768);
            const DWORD length = GetModuleFileNameW(ortModule, file.data(), static_cast<DWORD>(file.size()));
            if (!length || length >= file.size()) throw std::runtime_error("CUDA runtime location is unavailable.");
            const auto directory = std::filesystem::path(std::wstring(file.data(), length)).parent_path();
            for (const auto* name : {L"cudart64_13.dll", L"cudart64_12.dll"}) {
                module = LoadLibraryExW((directory / name).c_str(), nullptr,
                    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
                if (module) break;
            }
            if (module) {
                copy = reinterpret_cast<decltype(copy)>(GetProcAddress(module, "cudaMemcpy"));
                syncStream = reinterpret_cast<decltype(syncStream)>(GetProcAddress(module, "cudaStreamSynchronize"));
                setDevice = reinterpret_cast<decltype(setDevice)>(GetProcAddress(module, "cudaSetDevice"));
            }
            if (!copy || !syncStream || !setDevice) {
                if (module) FreeLibrary(module);
                throw std::runtime_error("The private CUDA runtime does not expose the required transfer functions.");
            }
        }
    };

    TransferApi m_cuda;
    int m_device;
    Ort::MemoryInfo m_memory;
    Ort::Allocator m_gpuAllocator;
    Ort::AllocatorWithDefaultOptions m_cpuAllocator;
    Ort::Value m_gpuInput{nullptr};
    std::size_t m_inputBytes = 0;
    std::vector<Ort::Value> m_gpuOutputs, m_outputs;
    std::vector<std::size_t> m_outputBytes;
    Ort::IoBinding m_binding;
};

} // namespace vaporlock::inference
