// Exercise unrestricted operation without capture, external output, a license
// server, or any user activation state.
#include "app/Win32VaporLock.cpp"

static void requirePublic(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

int main() {
    try {
        requirePublic(cloud_native::validOperation(cloud_native::Operation::Inspect), "Cloud inspection denied");
        requirePublic(cloud_native::validOperation(cloud_native::Operation::Infer), "Cloud inference denied");
        requirePublic(!cloud_native::validOperation(static_cast<cloud_native::Operation>(100)), "Invalid protocol operation accepted");
        requirePublic(std::wstring(cloud_native::pipePrefix()) == L"VaporLock.Cloud.premium.", "Existing worker namespace changed");

        AppConfig config;
        config.datasetEnabled = true;
        config.strategy = 2;
        config.predictLost = true;
        config.motionCompensationEnabled = true;
        config.eventActionEnabled = true;
        config.manualTargetLockEnabled = true;
        config.responseCurve = 1;
        parseClassPolicies(L"1 2 10 0 0 0", config.classPolicies);
        requirePublic(config.datasetEnabled && config.strategy == 2 && config.predictLost
            && config.motionCompensationEnabled && config.eventActionEnabled
            && config.manualTargetLockEnabled && config.responseCurve == 1 && !config.classPolicies.empty(),
            "Public feature configuration was restricted");
        // Safety remains a separate requirement: Observe mode cannot inject
        // movement merely because a full-featured profile enabled it.
        config.observeOnly = true;
        config.movementEnabled = true;
        applySessionSafety(config);
        requirePublic(!config.movementEnabled && !config.eventActionEnabled
            && !config.motionCompensationEnabled, "Observe-only output safety was removed");

        OnnxDetector detector;
        std::wstring error;
        requirePublic(detector.loadModel(L"", InferenceProvider::Cpu, 0, 0, false, false, error),
            "Empty model operation required external authorization");
        wchar_t engine[] = L"VaporLock.exe", headless[] = L"--headless", inspect[] = L"--inspect-model";
        wchar_t* arguments[] = {engine, headless, inspect};
        std::ostringstream stream;
        auto* previous = std::cout.rdbuf(stream.rdbuf());
        int result = 0;
        try { result = runHeadless(3, arguments); }
        catch (...) { std::cout.rdbuf(previous); throw; }
        std::cout.rdbuf(previous);
        requirePublic(result == 2 && stream.str().find("Choose an ONNX model") != std::string::npos,
            "Headless inspection was blocked by retired licensing or Start policy");
        std::cout << "Public native access tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
