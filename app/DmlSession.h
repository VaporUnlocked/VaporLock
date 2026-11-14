#pragma once

#include <onnxruntime_cxx_api.h>

#include <array>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace vaporlock::inference {

#if ORT_API_VERSION >= 22 && !defined(ORT_MINIMAL_BUILD)
inline std::vector<std::int64_t> specializeImageInput(const Ort::Env& environment, const wchar_t* modelPath,
                                                     std::int64_t imageSize, Ort::SessionOptions& options) {
    // Read the input metadata without compiling a second execution graph.
    const auto metadata = Ort::Session::CreateModelEditorSession(environment, modelPath, Ort::SessionOptions{});
    if (metadata.GetInputCount() != 1) return {};
    const auto type = metadata.GetInputTypeInfo(0);
    if (type.GetONNXType() != ONNX_TYPE_TENSOR) return {};
    const auto tensor = type.GetTensorTypeAndShapeInfo();
    const auto element = tensor.GetElementType();
    if (element != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT && element != ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8) return {};
    auto shape = tensor.GetShape();
    if (shape.size() != 4 || shape[0] > 1 || (shape[1] != 3 && shape[3] != 3)) return {};

    const std::array<std::int64_t, 4> defaults = shape[1] == 3
        ? std::array<std::int64_t, 4>{1, 3, imageSize, imageSize}
        : std::array<std::int64_t, 4>{1, imageSize, imageSize, 3};
    std::array<const char*, 4> symbols{};
    tensor.GetSymbolicDimensions(symbols.data(), symbols.size());
    std::map<std::string, std::int64_t> overrides;
    for (std::size_t index = 0; index < shape.size(); ++index) {
        if (shape[index] > 0) continue;
        if (!symbols[index] || !symbols[index][0]) return {};
        const auto [entry, inserted] = overrides.try_emplace(symbols[index], defaults[index]);
        if (!inserted && entry->second != defaults[index]) return {};
        shape[index] = defaults[index];
    }
    if (overrides.empty()) return {};
    for (const auto& [name, dimension] : overrides)
        Ort::ThrowOnError(Ort::GetApi().AddFreeDimensionOverrideByName(options, name.c_str(), dimension));
    return shape;
}
#endif

inline Ort::Session openSpecializedImageSession(const Ort::Env& environment, const wchar_t* modelPath,
                                               const Ort::SessionOptions& options, std::int64_t imageSize) {
#if ORT_API_VERSION >= 22 && !defined(ORT_MINIMAL_BUILD)
    try {
        auto specialized = options.Clone();
        const auto fixedShape = specializeImageInput(environment, modelPath, imageSize, specialized);
        if (!fixedShape.empty()) {
            Ort::Session session(environment, modelPath, specialized);
            if (session.GetInputCount() == 1) {
                const auto type = session.GetInputTypeInfo(0);
                if (type.GetONNXType() == ONNX_TYPE_TENSOR &&
                    type.GetTensorTypeAndShapeInfo().GetShape() == fixedShape) return session;
            }
        }
    } catch (const Ort::Exception&) {
        // Unresolved or incompatible models retain the ordinary session.
    }
#else
    (void)imageSize;
#endif
    return Ort::Session(environment, modelPath, options);
}

} // namespace vaporlock::inference
