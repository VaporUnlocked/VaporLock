#pragma once
#include <onnxruntime_cxx_api.h>
#include <array>
#include <atomic>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <stdexcept>
#include <thread>
#include <vector>
#include "CpuPlacement.h"

namespace vaporlock::inference {

// One session, one Run owner. CPU-visible results are returned only after Run
// completes successfully. The environment, names, stable input storage, tensor
// wrappers share the same lifetime. Output slots are reset before every Run:
// provider-owned allocations must be refreshed even for declared static shapes.
class Runtime final {
public:
    Runtime(std::unique_ptr<Ort::Env> environment,std::unique_ptr<Ort::Session> session,
            std::string input,std::vector<std::string> outputs,
            std::array<std::int64_t,4> shape,ONNXTensorElementDataType type,const CpuPlacement* placement=nullptr)
        :m_environment(std::move(environment)),m_session(std::move(session)),
         m_inputName(std::move(input)),m_outputNames(std::move(outputs)),m_shape(shape),
         m_type(type),m_owner(std::this_thread::get_id()),m_placement(placement) {
        if(!m_environment || !m_session || m_inputName.empty() || m_outputNames.empty())
            throw std::invalid_argument("Incomplete inference session.");
        const auto count=checkedElements(shape.data(),shape.size());
        if(type==ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) m_floats.resize(count);
        else if(type==ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8) m_bytes.resize(count);
        else throw std::invalid_argument("Unsupported image tensor type.");
        for(const auto& name:m_outputNames) m_outputPointers.push_back(name.c_str());
        m_outputs.reserve(m_outputNames.size());
        for(std::size_t index=0;index<m_outputNames.size();++index) m_outputs.emplace_back(nullptr);
        bindCpuInput();
    }
    Runtime(const Runtime&)=delete;
    Runtime& operator=(const Runtime&)=delete;
    std::vector<float>& floats() noexcept { return m_floats; }
    std::vector<std::uint8_t>& bytes() noexcept { return m_bytes; }
    // Only cancellation may be called from another thread. The owner must join
    // its Run before destroying this object. A stuck provider remains subject
    // to the existing owned-process Stop timeout, never a detached thread.
    void cancel() { m_cancelled=true; m_options.SetTerminate(); }
    // TensorRT executes the model itself. Keep only tensor storage/ORT views;
    // discard the metadata session's CPU graph, weights and thread pool.
    void retireMetadataSession() {
        if (std::this_thread::get_id() != m_owner || m_cancelled)
            throw std::runtime_error("Cannot retire a foreign or cancelled metadata session.");
        m_session.reset();
    }
    const std::vector<Ort::Value>& run(float* deviceInput=nullptr,std::size_t deviceCapacity=0,int deviceId=0) {
        if (!m_session) throw std::runtime_error("TensorRT metadata session cannot execute CPU inference.");
        if(std::this_thread::get_id()!=m_owner || m_cancelled)
            throw std::runtime_error("Inference session is cancelled or used by another owner.");
        if(deviceInput) {
            if(m_type!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || deviceCapacity<m_floats.size() || deviceId<0 || deviceId>=64)
                throw std::runtime_error("CUDA image tensor capacity is invalid.");
            if(deviceInput!=m_boundDevice || deviceId!=m_boundDeviceId) {
                Ort::MemoryInfo memory("Cuda",OrtDeviceAllocator,deviceId,OrtMemTypeDefault);
                m_input=Ort::Value::CreateTensor<float>(memory,deviceInput,m_floats.size(),m_shape.data(),m_shape.size());
                m_boundDevice=deviceInput;
                m_boundDeviceId=deviceId;
            }
        } else if(m_boundDevice) bindCpuInput();
        // DirectML mixed-output fixture regression showed stale fixed-shape
        // results when caller allocations were retained. Keep the vector/names,
        // but let ORT provide every result anew; never assume shape proves reuse.
        for(auto& output:m_outputs) output=Ort::Value{nullptr};
        const char* input=m_inputName.c_str();
        // Run synchronizes device work before these CPU output tensors are
        // decoded. Never disable synchronization or expose device memory as CPU.
        {
            CpuPlacement::Scope placement(m_placement);
            m_session->Run(m_options,&input,&m_input,1,m_outputPointers.data(),m_outputs.data(),m_outputs.size());
        }
        if(m_cancelled) throw std::runtime_error("Inference session was cancelled.");
        return m_outputs;
    }
private:
    static std::size_t checkedElements(const std::int64_t* dimensions,std::size_t count) {
        std::size_t elements=1;
        for(std::size_t i=0;i<count;++i) {
            if(dimensions[i]<=0 || static_cast<std::uint64_t>(dimensions[i])>
                    std::numeric_limits<std::size_t>::max()/elements)
                throw std::invalid_argument("Inference tensor dimensions are invalid.");
            elements*=static_cast<std::size_t>(dimensions[i]);
        }
        // Conservative element bound covers up to 1 GiB even for float64/int64.
        if(elements>128U*1024U*1024U) throw std::invalid_argument("Inference tensor exceeds the supported allocation bound.");
        return elements;
    }
    void bindCpuInput() {
        auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
        if(m_type==ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)
            m_input=Ort::Value::CreateTensor<float>(memory,m_floats.data(),m_floats.size(),m_shape.data(),m_shape.size());
        else m_input=Ort::Value::CreateTensor<std::uint8_t>(memory,m_bytes.data(),m_bytes.size(),m_shape.data(),m_shape.size());
        m_boundDevice=nullptr;
        m_boundDeviceId=-1;
    }
    std::unique_ptr<Ort::Env> m_environment;
    std::unique_ptr<Ort::Session> m_session;
    std::string m_inputName;
    std::vector<std::string> m_outputNames;
    std::vector<const char*> m_outputPointers;
    std::array<std::int64_t,4> m_shape;
    ONNXTensorElementDataType m_type;
    std::thread::id m_owner;
    const CpuPlacement* m_placement=nullptr;
    std::vector<float> m_floats;
    std::vector<std::uint8_t> m_bytes;
    Ort::Value m_input{nullptr};
    float* m_boundDevice=nullptr;
    int m_boundDeviceId=-1;
    std::vector<Ort::Value> m_outputs;
    Ort::RunOptions m_options;
    std::atomic<bool> m_cancelled{false};
};
} // namespace vaporlock::inference
