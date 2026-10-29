#include <NvInfer.h>
#include <NvOnnxParser.h>

#include <iostream>

namespace {

class TensorRtLogger final : public nvinfer1::ILogger {
public:
    void log(Severity severity, char const* message) noexcept override {
        if (severity <= Severity::kWARNING && message) std::cerr << message << '\n';
    }
};

}  // namespace

int main() {
    TensorRtLogger logger;
    auto* builder = nvinfer1::createInferBuilder(logger);
    if (!builder) {
        std::cerr << "TensorRT RTX could not create an inference builder.\n";
        return 1;
    }
    // TensorRT RTX uses explicit batch dimensions by default.
    auto* network = builder->createNetworkV2(0U);
    if (!network) {
        std::cerr << "TensorRT RTX could not create an explicit-batch network.\n";
        delete builder;
        return 1;
    }
    auto* parser = nvonnxparser::createParser(*network, logger);
    if (!parser) {
        std::cerr << "TensorRT RTX could not create its ONNX parser.\n";
        delete network;
        delete builder;
        return 1;
    }
    std::cout << "TensorRT RTX " << NV_TENSORRT_MAJOR << '.' << NV_TENSORRT_MINOR << '.' << NV_TENSORRT_PATCH
              << " builder and ONNX parser are available.\n";
    delete parser;
    delete network;
    delete builder;
    return 0;
}
