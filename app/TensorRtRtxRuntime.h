#pragma once

#include <cuda_runtime_api.h>
#include <NvInfer.h>
#include <NvOnnxParser.h>
#include "app/TensorRtCache.h"
#include "app/OnnxCacheEligibility.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace vaporlock::tensorrt_rtx {

struct Output {
    std::string name;
    std::vector<int64_t> shape;
    std::vector<float> values;
};

class Runtime final {
public:
    Runtime() = default;
    Runtime(const Runtime&) = delete;
    Runtime& operator=(const Runtime&) = delete;
    ~Runtime() { clear(); }

    bool load(const std::filesystem::path& model, std::string inputName, const std::vector<std::string>& outputNames,
              const std::array<int64_t, 4>& inputShape, std::wstring& error, int deviceIndex = 0) {
        clear();
        if(deviceIndex < 0 || deviceIndex >= 64 || !cudaOk(cudaSetDevice(deviceIndex), error)) return false;
        if (inputName.empty() || outputNames.empty()) {
            error = L"TensorRT RTX could not determine this model's image input and outputs.";
            return false;
        }
        m_inputName = std::move(inputName);
        m_outputNames = outputNames;
        m_inputShape.assign(inputShape.begin(), inputShape.end());
        if (!validShape(m_inputShape)) {
            error = L"TensorRT RTX requires resolved four-dimensional image input dimensions.";
            return false;
        }
        cudaDeviceProp device{};
        if (!cudaOk(cudaGetDevice(&m_device), error) || !cudaOk(cudaGetDeviceProperties(&device, m_device), error)
            || !cudaOk(cudaDriverGetVersion(&m_driver), error)) return false;
        if (device.major * 10 + device.minor < 75) {
            error = L"TensorRT RTX requires an NVIDIA Turing or newer GPU. Select another backend for this GPU.";
            return false;
        }
        m_deviceName = wideError(device.name);
        m_modelBytes = tensorrt_cache::readBounded(model);
        if (m_modelBytes.empty()) { error = L"TensorRT RTX could not safely read this model (512 MiB limit)."; return false; }
        m_cacheEligible = tensorrt_cache::isSelfContainedOnnx({m_modelBytes.data(),m_modelBytes.size()});
        std::ostringstream identity;
        identity << "on-device-v2:opt3:workspace1073741824:model-precision:" << NV_TENSORRT_MAJOR << ':' << NV_TENSORRT_MINOR << ':' << NV_TENSORRT_PATCH
                 << ':' << NV_TENSORRT_BUILD << ':' << m_driver << ':' << device.major << ':' << device.minor
                 << ':' << tensorrt_cache::digest(&device.uuid, sizeof(device.uuid)) << ':'
                 << tensorrt_cache::digest(m_modelBytes.data(), m_modelBytes.size()) << ':' << m_inputName;
        for (auto d : m_inputShape) identity << ':' << d;
        for (const auto& name : m_outputNames) identity << ':' << name.size() << ':' << name;
        const auto key = identity.str();
        m_cacheIdentity = tensorrt_cache::digest(key.data(), key.size());
        m_runtime.reset(nvinfer1::createInferRuntime(m_logger));
        if (!m_runtime) {
            error = L"TensorRT RTX could not create its runtime. Check that the NVIDIA display driver is current.";
            return false;
        }
        m_cachePath = cachePathFor(model, m_cacheIdentity);
        if (loadCachedEngine()) {
            std::vector<char>().swap(m_modelBytes);
            return prepareExecution(error);
        }
        if (!buildEngine(model, error)) return false;
        std::vector<char>().swap(m_modelBytes);
        if (!prepareExecution(error)) return false;
        return true;
    }

    bool ready() const { return m_context != nullptr; }
    const std::wstring& deviceName() const noexcept { return m_deviceName; }

    bool run(const std::vector<float>& input, std::vector<Output>& outputs, std::wstring& error,
             float* deviceInput = nullptr, std::size_t deviceCapacity = 0) {
        outputs.clear();
        if(!cudaOk(cudaSetDevice(m_device),error)) return false;
        const std::size_t expectedInputValues = elementCount(m_inputShape);
        if (!ready() || !expectedInputValues || (deviceInput ? deviceCapacity < expectedInputValues : input.size() != expectedInputValues)) {
            error = L"TensorRT RTX received an invalid image tensor.";
            return false;
        }
        // Preprocessing completes its stream before lending this device tensor.
        // Drain queued copies on every error before caller-owned output storage dies.
        struct Drain { cudaStream_t stream; ~Drain() { cudaStreamSynchronize(stream); } } drain{m_stream};
        if (deviceInput) {
            cudaPointerAttributes attributes{};
            if (!cudaOk(cudaPointerGetAttributes(&attributes, deviceInput), error)
                || attributes.type != cudaMemoryTypeDevice || attributes.device != m_device) {
                if (error.empty()) error = L"TensorRT RTX preprocessing tensor belongs to the wrong GPU.";
                return false;
            }
        } else if (!cudaOk(cudaMemcpyAsync(m_inputBuffer, input.data(), input.size() * sizeof(float), cudaMemcpyHostToDevice, m_stream), error)) return false;
        if (!m_context->setTensorAddress(m_inputName.c_str(), deviceInput ? deviceInput : m_inputBuffer)) {
            if (error.empty()) error = L"TensorRT RTX could not bind the image input.";
            return false;
        }
        for (auto& output : m_outputs) {
            if (!m_context->setTensorAddress(output.name.c_str(), output.deviceBuffer)) {
                error = L"TensorRT RTX could not bind a model output.";
                return false;
            }
        }
        if (!m_context->enqueueV3(m_stream)) {
            error = L"TensorRT RTX could not execute this model.";
            return false;
        }
        outputs.reserve(m_outputs.size());
        for (const auto& output : m_outputs) {
            Output result;
            result.name = output.name;
            result.shape = output.shape;
            result.values.resize(output.values);
            if (!cudaOk(cudaMemcpyAsync(result.values.data(), output.deviceBuffer, result.values.size() * sizeof(float), cudaMemcpyDeviceToHost, m_stream), error)) {
                cudaStreamSynchronize(m_stream);
                return false;
            }
            outputs.push_back(std::move(result));
        }
        if (!cudaOk(cudaStreamSynchronize(m_stream), error)) return false;
        return true;
    }

private:
    class Logger final : public nvinfer1::ILogger {
    public:
        void log(Severity severity, char const* message) noexcept override {
            if (severity > Severity::kWARNING || !message) return;
            // NVIDIA may report from internal worker threads. Never race the
            // reader or let allocation/locking exceptions escape its callback.
            try {
                std::size_t length = 0;
                while (length < 8192 && message[length]) ++length;
                std::lock_guard<std::mutex> guard(m_mutex);
                m_last.assign(message, length);
            } catch (...) {}
        }
        std::string last() const { std::lock_guard<std::mutex> guard(m_mutex); return m_last; }
        void reset() { std::lock_guard<std::mutex> guard(m_mutex); m_last.clear(); }
    private:
        mutable std::mutex m_mutex;
        std::string m_last;
    };

    struct DeviceOutput {
        std::string name;
        std::vector<int64_t> shape;
        std::size_t values = 0;
        void* deviceBuffer = nullptr;
    };

    static bool validShape(const std::vector<int64_t>& shape) {
        return shape.size() == 4 && tensorrt_cache::elements(shape) != 0;
    }

    static std::size_t elementCount(const std::vector<int64_t>& shape) {
        return tensorrt_cache::elements(shape);
    }

    static std::wstring wideError(const std::string& text) {
        if (text.empty()) return L"an unspecified TensorRT RTX error";
        const int size = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
        std::wstring result(size, L'\0');
        MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size);
        return result;
    }

    static std::string utf8(const std::filesystem::path& path) {
        const auto text = path.wstring();
        const int size = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
        std::string result(size, '\0');
        WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
        return result;
    }

    static std::filesystem::path cachePathFor(const std::filesystem::path& model, const std::string& identity) {
        const auto modelsDirectory = model.parent_path();
        const auto productRoot = modelsDirectory.filename() == L"models" ? modelsDirectory.parent_path() : modelsDirectory;
        std::wostringstream name;
        name << wideError(identity) << L".vlcache";
        return productRoot / L"cache" / L"tensorrt-rtx-v2" / name.str();
    }

    static std::vector<int64_t> dimensions(const nvinfer1::Dims& dims) {
        if (dims.nbDims <= 0 || dims.nbDims > 8) return {};
        std::vector<int64_t> result;
        result.reserve(static_cast<std::size_t>(dims.nbDims));
        for (int index = 0; index < dims.nbDims; ++index) result.push_back(dims.d[index]);
        return result;
    }

    static nvinfer1::Dims dimensions(const std::vector<int64_t>& shape) {
        nvinfer1::Dims result{};
        result.nbDims = static_cast<int>(shape.size());
        for (int index = 0; index < result.nbDims; ++index) result.d[index] = static_cast<int>(shape[static_cast<std::size_t>(index)]);
        return result;
    }

    bool cudaOk(cudaError_t status, std::wstring& error) const {
        if (status == cudaSuccess) return true;
        error = L"TensorRT RTX CUDA operation failed: " + wideError(cudaGetErrorString(status));
        return false;
    }

    bool loadCachedEngine() {
        if (!m_cacheEligible) return false;
        auto serialized = tensorrt_cache::load(m_cachePath, m_cacheIdentity);
        if (serialized.empty()) return false;
        m_engine.reset(m_runtime->deserializeCudaEngine(serialized.data(), serialized.size()));
        return m_engine != nullptr;
    }

    bool buildEngine(const std::filesystem::path& model, std::wstring& error) {
        m_logger.reset();
        std::unique_ptr<nvinfer1::IBuilder> builder(nvinfer1::createInferBuilder(m_logger));
        if (!builder) {
            error = L"TensorRT RTX could not create its engine builder.";
            return false;
        }
        std::unique_ptr<nvinfer1::INetworkDefinition> network(builder->createNetworkV2(0U));
        std::unique_ptr<nvonnxparser::IParser> parser(network ? nvonnxparser::createParser(*network, m_logger) : nullptr);
        if (!network || !parser || !parser->parse(m_modelBytes.data(), m_modelBytes.size(), utf8(model).c_str())) {
            error = L"TensorRT RTX could not parse this ONNX model: " + wideError(m_logger.last());
            return false;
        }
        auto* input = network->getInput(0);
        if (!input || input->getType() != nvinfer1::DataType::kFLOAT) {
            error = L"TensorRT RTX currently requires a float image-input model.";
            return false;
        }
        std::unique_ptr<nvinfer1::IBuilderConfig> config(builder->createBuilderConfig());
        if (!config) {
            error = L"TensorRT RTX could not create an engine configuration.";
            return false;
        }
        if (!config->setNbComputeCapabilities(1)
            || !config->setComputeCapability(nvinfer1::ComputeCapability::kCURRENT, 0)) {
            error = L"TensorRT RTX could not target the current GPU for this local engine.";
            return false;
        }
        config->setMemoryPoolLimit(nvinfer1::MemoryPoolType::kWORKSPACE, 1024ULL * 1024ULL * 1024ULL);
        const auto inputDims = input->getDimensions();
        bool dynamic = false;
        for (int index = 0; index < inputDims.nbDims; ++index) dynamic = dynamic || inputDims.d[index] < 0;
        if (dynamic) {
            auto* profile = builder->createOptimizationProfile();
            const auto fixed = dimensions(m_inputShape);
            if (!profile || !profile->setDimensions(m_inputName.c_str(), nvinfer1::OptProfileSelector::kMIN, fixed)
                || !profile->setDimensions(m_inputName.c_str(), nvinfer1::OptProfileSelector::kOPT, fixed)
                || !profile->setDimensions(m_inputName.c_str(), nvinfer1::OptProfileSelector::kMAX, fixed)
                || config->addOptimizationProfile(profile) < 0) {
                error = L"TensorRT RTX could not create a fixed image-size profile for this dynamic model.";
                return false;
            }
        }
        config->setBuilderOptimizationLevel(3);
        std::unique_ptr<nvinfer1::IHostMemory> serialized(builder->buildSerializedNetwork(*network, *config));
        if (!serialized || serialized->size() > tensorrt_cache::maximumBytes - 136) {
            error = L"TensorRT RTX could not build an engine for this model: " + wideError(m_logger.last());
            return false;
        }
        m_engine.reset(m_runtime->deserializeCudaEngine(serialized->data(), serialized->size()));
        if (!m_engine) {
            error = L"TensorRT RTX built an engine that could not be loaded on this computer.";
            return false;
        }
        if (m_cacheEligible) tensorrt_cache::save(m_cachePath, m_cacheIdentity, serialized->data(), serialized->size());
        return true;
    }

    bool prepareExecution(std::wstring& error) {
        if (!m_engine) return false;
        if (m_engine->getTensorIOMode(m_inputName.c_str()) != nvinfer1::TensorIOMode::kINPUT
            || m_engine->getTensorDataType(m_inputName.c_str()) != nvinfer1::DataType::kFLOAT
            || m_engine->getTensorLocation(m_inputName.c_str()) != nvinfer1::TensorLocation::kDEVICE) {
            error = L"TensorRT RTX requires a float device image input.";
            return false;
        }
        m_context.reset(m_engine->createExecutionContext());
        if (!m_context || !m_context->setInputShape(m_inputName.c_str(), dimensions(m_inputShape))) {
            error = L"TensorRT RTX could not set this model's image dimensions.";
            return false;
        }
        if (!cudaOk(cudaStreamCreateWithFlags(&m_stream, cudaStreamNonBlocking), error)) return false;
        const std::size_t inputValues = elementCount(m_inputShape);
        if (!inputValues || !cudaOk(cudaMalloc(&m_inputBuffer, inputValues * sizeof(float)), error)) return false;
        std::size_t totalOutputValues = 0;
        for (const auto& expected : m_outputNames) {
            const auto shape = dimensions(m_context->getTensorShape(expected.c_str()));
            if (shape.empty() || elementCount(shape) == 0 || m_engine->getTensorDataType(expected.c_str()) != nvinfer1::DataType::kFLOAT
                || m_engine->getTensorIOMode(expected.c_str()) != nvinfer1::TensorIOMode::kOUTPUT
                || m_engine->getTensorLocation(expected.c_str()) != nvinfer1::TensorLocation::kDEVICE) {
                std::wostringstream detail;
                detail << L"TensorRT RTX needs resolved float outputs. Output '" << wideError(expected) << L"' has shape [";
                for (std::size_t index = 0; index < shape.size(); ++index) {
                    if (index) detail << L", ";
                    detail << shape[index];
                }
                detail << L"] and data type " << static_cast<int>(m_engine->getTensorDataType(expected.c_str())) << L".";
                error = detail.str();
                return false;
            }
            DeviceOutput output;
            output.name = expected;
            output.shape = shape;
            output.values = elementCount(shape);
            if (output.values > tensorrt_cache::maximumElements - totalOutputValues) {
                error = L"TensorRT RTX outputs exceed the supported allocation bound.";
                return false;
            }
            totalOutputValues += output.values;
            if (!output.values || !cudaOk(cudaMalloc(&output.deviceBuffer, output.values * sizeof(float)), error)) return false;
            m_outputs.push_back(std::move(output));
        }
        return true;
    }

    void clear() {
        if(m_stream || m_inputBuffer || m_context) cudaSetDevice(m_device);
        if (m_stream) cudaStreamSynchronize(m_stream);
        for (auto& output : m_outputs) if (output.deviceBuffer) cudaFree(output.deviceBuffer);
        m_outputs.clear();
        if (m_inputBuffer) cudaFree(m_inputBuffer);
        m_inputBuffer = nullptr;
        if (m_stream) cudaStreamDestroy(m_stream);
        m_stream = nullptr;
        m_context.reset();
        m_engine.reset();
        m_runtime.reset();
        m_inputName.clear();
        m_outputNames.clear();
        m_inputShape.clear();
        m_cachePath.clear();
        m_modelBytes.clear(); m_cacheIdentity.clear(); m_deviceName.clear();
        m_cacheEligible = false;
    }

    Logger m_logger;
    std::unique_ptr<nvinfer1::IRuntime> m_runtime;
    std::unique_ptr<nvinfer1::ICudaEngine> m_engine;
    std::unique_ptr<nvinfer1::IExecutionContext> m_context;
    std::string m_inputName;
    std::vector<std::string> m_outputNames;
    std::vector<int64_t> m_inputShape;
    std::filesystem::path m_cachePath;
    std::string m_cacheIdentity;
    std::vector<char> m_modelBytes;
    std::wstring m_deviceName;
    int m_device = 0, m_driver = 0;
    bool m_cacheEligible = false;
    cudaStream_t m_stream = nullptr;
    void* m_inputBuffer = nullptr;
    std::vector<DeviceOutput> m_outputs;
};

}  // namespace vaporlock::tensorrt_rtx
