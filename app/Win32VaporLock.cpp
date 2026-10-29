#include <windows.h>
#include <commctrl.h>
#include <commdlg.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <shellscalingapi.h>
#include <shellapi.h>
#include <wincodec.h>
#include <wrl/client.h>

#include "app/HardwareOutput.h"
#include "app/CrosshairReference.h"
#include "app/BoundedWindowsWrite.h"
#include "app/AsyncTelemetry.h"
#include "app/CaptureSettingParse.h"
#include "app/CaptureQualification.h"
#include "app/CaptureFile.h"
#include "app/BgraTensorCopy.h"
#include "app/BgraTensorResize.h"
#include "app/InferenceCadence.h"
#include "app/PipelineTiming.h"
#include "app/PipelineScheduler.h"
#include "app/CloudCapturePacing.h"
#include "app/CaptureRecovery.h"
#include "app/ControllerTrigger.h"
#include "app/CudaDevice.h"

#ifdef VAPORLOCK_HAS_VIRTUAL_CONTROLLER
#include <ViGEm/Client.h>
#endif

#if VAPORLOCK_HAS_ONNXRUNTIME
#define ORT_API_MANUAL_INIT
#include <onnxruntime_cxx_api.h>
#include "app/InferenceRuntime.h"
#if VAPORLOCK_HAS_DML
#include <dml_provider_factory.h>
#endif
#if VAPORLOCK_FORCE_DML
#include "app/DmlAdapter.h"
#endif
#if VAPORLOCK_FORCE_TENSORRT_RTX
#include "app/TensorRtRtxRuntime.h"
#endif
#if VAPORLOCK_HAS_CUDA_PREPROCESS
#include "detection/CudaPreprocess.h"
#endif
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cwctype>
#include <cctype>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <future>
#include <functional>
#include <deque>
#include <iomanip>
#include <iostream>
#include <locale>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <regex>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <thread>
#include <utility>
#include <vector>

namespace {

using AimClock = std::chrono::steady_clock;
using AimTime = AimClock::time_point;
constexpr double kLegacyResponseHz = 60.0;

double timeRetention(double retainedAt60Hz, double elapsed) {
    return std::pow(std::clamp(retainedAt60Hz, 0.0, 0.999), std::max(0.0, elapsed) * kLegacyResponseHz);
}

double legacyDurationMs(int frames, int explicitMs) {
    return explicitMs >= 0 ? explicitMs : std::max(0, frames) * 1000.0 / kLegacyResponseHz;
}

constexpr int kFrameTimer = 1001;
constexpr COLORREF kWindow = RGB(13, 18, 27);
constexpr COLORREF kSidebar = RGB(17, 25, 38);
constexpr COLORREF kPanel = RGB(23, 33, 48);
constexpr COLORREF kPanelRaised = RGB(29, 42, 60);
constexpr COLORREF kField = RGB(10, 15, 23);
constexpr COLORREF kText = RGB(237, 243, 250);
constexpr COLORREF kMuted = RGB(150, 170, 194);
constexpr COLORREF kAccent = RGB(54, 203, 171);
constexpr COLORREF kWarm = RGB(247, 184, 72);

enum ControlId {
    IdRun = 2001, IdStop, IdNavLive, IdNavDetection, IdNavCapture, IdNavTracking,
    IdNavSelection, IdNavOutput, IdNavOverlay, IdNavProfiles, IdNavDiagnostics,
    IdModelPath, IdBrowseModel, IdUnloadModel, IdConfidence, IdNmsIou, IdClassFilter,
    IdCaptureSource, IdCaptureFps, IdInferenceFps, IdFrameSkip, IdTrackIou, IdSmoothing,
    IdMaxMissed, IdPredictLost, IdStrategy, IdAcquireRadius, IdRetainRadius, IdSwitching,
    IdMovement, IdActivationMode, IdActivationKey, IdGain, IdMaxStep, IdDeadzone,
    IdOverlay, IdOverlayBoxes, IdOverlayLabels, IdOverlayConfidence, IdOverlayIds,
    IdOverlayMotion, IdProfileName, IdLoadProfile, IdSaveProfile, IdResetProfile,
    IdClearLog, IdCopyDiagnostics
};

enum class Page { Live, Detection, Capture, Tracking, Selection, Output, Overlay, Profiles, Diagnostics };

struct RectF {
    double x = 0.0, y = 0.0, w = 0.0, h = 0.0;
    [[nodiscard]] double cx() const { return x + w * 0.5; }
    [[nodiscard]] double cy() const { return y + h * 0.5; }
    [[nodiscard]] double area() const { return w * h; }
};

struct Detection {
    RectF box;
    double confidence = 0.0;
    int classId = 0;
    std::wstring label = L"object";
    // Instance-segmentation masks are intentionally stored at prototype
    // resolution. They are used only for debug rendering and never leave the
    // native process.
    std::vector<std::uint8_t> segmentationMask;
    int segmentationMaskWidth = 0;
    int segmentationMaskHeight = 0;
    std::vector<float> segmentationCoefficients;
    float segmentationScaleX = 1.0F;
    float segmentationScaleY = 1.0F;
    float segmentationPadX = 0.0F;
    float segmentationPadY = 0.0F;
    int segmentationInputWidth = 0;
    int segmentationInputHeight = 0;
};
struct Track {
    int id = 0;
    Detection detection;
    double vx = 0.0, vy = 0.0, ax = 0.0, ay = 0.0;
    int age = 1, missed = 0;
    bool selected = false;
    double stableAnchorX = 0.0, stableAnchorY = 0.0;
    bool hasStableAnchor = false;
    RectF observedBox{};
    bool hasObservation = false;
    bool identityCertain = true;
    double motionConfidence = 0.0;
    int motionSamples = 0;
    double motionEvidenceSeconds = 0.0;
    std::deque<std::tuple<AimTime, double, double>> observations;
    // Capture time of the last real observation, never refreshed by prediction.
    AimTime lastUpdate{};
};

enum class InferenceProvider { Cpu, Cuda, DirectMl, TensorRtRtx };

struct Transform {
    float scaleX = 1.0F;
    float scaleY = 1.0F;
    float padX = 0.0F;
    float padY = 0.0F;
};

struct CaptureRegion {
    int xOffset = 0;
    int yOffset = 0;
    int width = 640;
    int height = 640;
};

struct ClassPolicy {
    int priority = 0;
    int anchorMode = 0;
    double anchorX = 0.0, anchorY = 0.0;
};

bool parseClassPolicies(const std::wstring& value, std::map<int, ClassPolicy>& target) {
    std::wistringstream input(value);
    input.imbue(std::locale::classic());
    int count = 0;
    if (!(input >> count) || count < 0 || count > 128) return false;
    std::map<int, ClassPolicy> parsed;
    for (int i = 0; i < count; ++i) {
        int classId = 0;
        ClassPolicy rule;
        if (!(input >> classId >> rule.priority >> rule.anchorMode >> rule.anchorX >> rule.anchorY)
            || classId < 0 || classId > 1000000 || parsed.contains(classId)
            || rule.priority < -1000000 || rule.priority > 1000000
            || rule.anchorMode < 0 || rule.anchorMode > 3
            || !std::isfinite(rule.anchorX) || !std::isfinite(rule.anchorY)
            || std::abs(rule.anchorX) > 1 || std::abs(rule.anchorY) > 1) return false;
        parsed.emplace(classId, rule);
    }
    input >> std::ws;
    if (!input.eof()) return false;
    target = std::move(parsed);
    return true;
}

struct ExclusionZone { int x, y, width, height; };

bool parseExclusionZones(const std::wstring& value, std::vector<ExclusionZone>& target) {
    if (value.size() > 1024) return false;
    std::wistringstream input(value);
    int count;
    if (!(input >> count) || count < 0 || count > 16) return false;
    std::vector<ExclusionZone> parsed;
    for (int i = 0; i < count; ++i) {
        ExclusionZone z{};
        if (!(input >> z.x >> z.y >> z.width >> z.height)
            || z.x < 0 || z.y < 0 || z.x >= 1000000 || z.y >= 1000000
            || z.width < 1 || z.height < 1 || z.width > 1000000-z.x || z.height > 1000000-z.y) return false;
        parsed.push_back(z);
    }
    input >> std::ws;
    if (!input.eof()) return false;
    target = std::move(parsed); return true;
}

struct AppConfig {
    std::vector<ExclusionZone> exclusionZones;
    std::map<int, ClassPolicy> classPolicies;
    std::wstring profileName = L"Default";
    std::wstring modelPath;
    std::wstring cloudPipe;
    DWORD cloudWorkerPid = 0;
    InferenceProvider provider = InferenceProvider::Cpu;
    std::wstring classFilter;
    double confidence = 0.55;
    double acquireConfidence = 0.0; // Zero follows the detector floor.
    double retainConfidence = 0.0;
    double iou = 0.45;
    bool nmsEnabled = true;
    bool nmsClassAgnostic = false;
    // 0 auto, 1 YOLO class scores, 2 YOLO objectness, 3 end-to-end xyxy.
    int modelOutputFormat = 0;
    int modelCoordinateUnits = 0; // 0: metadata/pixels, 1: pixels, 2: normalized
    int maxDetections = 300;
    bool letterbox = true;
    // Monitor is one-based. Zero means the virtual desktop and uses the GDI
    // compatibility path; physical monitors use DXGI Desktop Duplication.
    int captureMonitor = 1;
    CaptureRegion captureRegion{};
    int captureFps = 30;
    int inferenceFps = 30;
    int frameSkip = 0;
    int ortIntraOpThreads = 0;
    int inferenceDevice = -1;
    std::wstring cpuSets;
    int ortInterOpThreads = 0;
    bool cudaGraphReplay = true;
    bool cudaPreprocess = true;
    double matchIou = 0.30;
    int maxMissed = 18;
    int trackRetentionMs = -1;
    double smoothing = 0.35;
    double boxSmoothing = 0.25;
    // A dedicated anchor filter prevents detection-box micro-jitter from
    // becoming movement output. It is independent from box smoothing so the
    // preview can remain informative without destabilizing the lock.
    double anchorSmoothing = 0.65;
    double anchorSettleRadius = 2.0;
    bool predictLost = true;
    int predictionLeadMs = 50;
    int predictionLimitMs = 500;
    double predictionMaxSpeed = 2000.0;
    double predictionMaxAcceleration = 12000.0;
    double predictionMaxDistance = 80.0;
    double predictionNoiseFloor = 1.0;
    int acquisitionRadius = 420;
    int nearRadius = 180;
    int snapRadius = 48;
    int retentionRadius = 540;
    // Zero is circular; one is rectangular/square distance.
    int fovShape = 0;
    double switchingThreshold = 0.18;
    int lockPersistenceMs = 900;
    int lostTargetTimeoutFrames = 12;
    int minimumTrackAge = 2;
    bool snapBypassesConfirmation = false;
    int nearCandidateWaitMs = 150;
    int lostTargetTimeoutMs = -1;
    int anchorMode = 0;
    double anchorOffsetX = 0.0;
    double anchorOffsetY = 0.0;
    // Optional class IDs that receive a tie-breaking preference during
    // acquisition. An empty watchlist preserves normal policy behavior.
    std::wstring watchlist;
    bool occlusionHandling = true;
    int occlusionGraceFrames = 12;
    int occlusionGraceMs = -1;
    int occlusionReacquireRadius = 180;
    int strategy = 0;
    // Session-level safety boundary. This is deliberately not a profile
    // setting: when present, no runtime update may enable an output path.
    bool observeOnly = false;
    bool movementEnabled = false;
    int activationMode = 0;
    int activationKey = 0;
    // Keyboard, controller, or either input can authorize movement output.
    int activationSource = 0;
    // Zero chooses the first connected XInput controller; one through four
    // choose a specific player slot.
    int controllerSlot = 0;
    int controllerBinding = 15;
    int controllerTriggerPress = 30, controllerTriggerRelease = 30;
    bool manualTargetLockEnabled = false;
    int manualLockSource = 0;
    std::wstring manualLockKey = L"middle";
    int manualLockControllerBinding = 4;
    // A per-session, GUI-created auto-reset event used by global
    // shortcuts. It carries no capture, model, or user data.
    std::wstring manualLockEvent;
    double movementGain = 0.18;
    double assistanceX = 1.0;
    double assistanceY = 1.0;
    int maxStep = 24;
    int deadzone = 8;
    double movementSmoothing = 0.35;
    int outputRate = 120;
    int outputMaxObservationAgeMs = 150;
    // Relative-input units per capture pixel. Controller output interprets
    // these as virtual units before normalization to a stick deflection.
    double inputUnitsPerPixelX = 1.0;
    double inputUnitsPerPixelY = 1.0;
    double maxSpeed = 1440.0;
    // 0 capture center, 1 configured crosshair offset from capture center.
    bool crosshairEnabled = false;
    vaporlock::crosshair::Template crosshairTemplate;
    vaporlock::crosshair::Reference frameReference;
    int aimReference = 0;
    double aimReferenceX = 0.0, aimReferenceY = 0.0;
    // Named compensation presets describe observed image drift. Native output
    // always applies the inverse. 0 preset, 1 automatic, 2 combined.
    bool motionCompensationEnabled = false;
    // 0 hold, 1 one-shot click, 2 constant, 3 automatic gate.
    int motionCompensationActivationMode = 0;
    std::wstring motionCompensationActivationKey = L"left";
    int motionCompensationMode = 0;
    double motionCompensationVertical = 0.0;
    double motionCompensationHorizontal = 0.0;
    double motionCompensationStrength = 1.0;
    int motionCompensationAutoMinTracks = 3;
    double motionCompensationAutoConsensus = 24.0;
    double motionCompensationAutoSmoothing = 0.75;
    double motionCompensationAutoMaxRate = 240.0;
    // Snap-radius event action. It follows aim activation by default,
    // including both hold and toggle activation modes.
    bool eventActionEnabled = false;
    bool eventActionRequiresActivation = true;
    // Zero is a repeated click, one holds the configured button while valid.
    int eventActionMode = 0;
    // Zero left, one right, two middle.
    int eventActionButton = 0;
    int eventActionCooldownMs = 1000;
    // Mouse output is the portable default. The virtual Xbox route creates a
    // distinct device and never writes to a customer's physical controller.
    int outputTransport = vaporlock::hardware::kMouseTransport;
    // The primary-slot bridge mirrors a physical controller into Vapor Lock's
    // virtual controller after the user deliberately gives that virtual device
    // XInput slot 0. It is opt-in because slot order is controlled by Windows.
    int virtualControllerMode = 0;
    int controllerOutputStick = 1;
    double controllerOutputStrength = 1.0;
    double controllerOutputDeadzone = 0.0;
    bool virtualControllerManualPriority = true;
    // The optional Xbox One/Series bridge is hosted in a small elevated local
    // companion. The native engine only receives a random per-session pipe
    // name and never needs a controller-driver dependency itself.
    std::wstring controllerHostPipe;
    int controllerHostVirtualSlot = -1;
    // Hardware transports are deliberately movement-only. Device connection
    // details stay local to the profile and are never emitted as telemetry.
    std::wstring hardwareSerialPort;
    int hardwareSerialBaud = 115200;
    std::wstring kmboxNetAddress;
    int kmboxNetPort = 0;
    std::wstring kmboxNetDeviceCode;
    std::wstring kmboxALibraryPath;
    int kmboxAVendorId = 0x6688;
    int kmboxAProductId = 0x2021;
    std::wstring virtualMouseLibraryPath;
    bool invertX = false;
    bool invertY = false;
    int responseCurve = 0;
    bool overlayEnabled = true;
    bool overlayBoxes = true;
    bool overlayLabels = true;
    bool overlayConfidence = true;
    bool overlayIds = true;
    bool overlayMotion = true;
    bool overlayFov = true;
    // Instance masks are debug visualizations. Reconstructing many on each
    // inference can delay an otherwise stable output path, so opt in and cap
    // the default to one visible instance.
    bool overlaySegmentationMasks = false;
    int segmentationMaskLimit = 1;
    double segmentationMaskThreshold = 0.50;
    bool overlayMetadata = false;
    // Echoed display fence; never grants movement authority.
    std::uint64_t visualEpoch = 0;
    bool previewEnabled = false;
    int previewFps = 5;
    int previewQuality = 80;
    bool datasetEnabled = false;
    bool datasetCaptureConfidenceEnabled = false;
    std::wstring datasetDirectory;
    double datasetMinConfidence = 0.50;
    int datasetIntervalMs = 1000;
    int datasetMaxImages = 0;
    int datasetMaxMiB = 0;
    bool datasetCaptureRequiresActivation = true;
    int datasetJpegQuality = 92;
};

struct ModelOutputLayout { bool endToEnd = false; bool objectness = false; };

float modelOutputValue(const float* values, int rows, int columns, bool transposed, int row, int column) {
    return transposed ? values[static_cast<std::size_t>(column) * rows + row]
                      : values[static_cast<std::size_t>(row) * columns + column];
}

// A six-column tensor is ambiguous: raw YOLO can expose two class scores,
// while end-to-end Ultralytics exports use xyxy, confidence, class ID. Only
// declare it end-to-end when its non-empty rows actually look like final boxes.
bool looksLikeEndToEndOutput(const float* values, int rows, int columns, bool transposed, int classCount) {
    if (!values || rows <= 0 || columns != 6 || transposed) return false;
    const int sampleCount = std::min(rows, 1024);
    int scoredRows = 0;
    int validBoxRows = 0;
    for (int row = 0; row < sampleCount; ++row) {
        const float score = modelOutputValue(values, rows, columns, transposed, row, 4);
        if (!std::isfinite(score) || score <= 0.001F || score > 1.0001F) continue;
        ++scoredRows;
        const float x1 = modelOutputValue(values, rows, columns, transposed, row, 0);
        const float y1 = modelOutputValue(values, rows, columns, transposed, row, 1);
        const float x2 = modelOutputValue(values, rows, columns, transposed, row, 2);
        const float y2 = modelOutputValue(values, rows, columns, transposed, row, 3);
        const float rawClassId = modelOutputValue(values, rows, columns, transposed, row, 5);
        if (!std::isfinite(rawClassId) || rawClassId < 0.0F || rawClassId >= 4096.0F) continue;
        const int classId = static_cast<int>(std::lround(rawClassId));
        const bool validClass = std::isfinite(rawClassId)
                                && std::abs(rawClassId - static_cast<float>(classId)) < 0.001F
                                && classId >= 0
                                && (classCount > 0 ? classId < classCount : classId < 4096);
        if (validClass && std::isfinite(x1) && std::isfinite(y1) && std::isfinite(x2) && std::isfinite(y2)
            && x2 > x1 && y2 > y1) {
            ++validBoxRows;
        }
    }
    return scoredRows > 0 && validBoxRows * 100 >= scoredRows * 85;
}

ModelOutputLayout resolveModelOutputLayout(int columns, int classCount, int configuredFormat) {
    if (configuredFormat == 3) return {true, false};
    if (configuredFormat == 2) return {false, true};
    if (configuredFormat == 1) return {false, false};
    if (classCount > 0 && columns == classCount + 4) return {false, false};
    if (classCount > 0 && columns == classCount + 5) return {false, true};
    // Do not guess that every six-column tensor is final xyxy output. The
    // runtime probe above can prove that case from output values instead.
    return {false, columns >= 85};
}

bool modelOutputTransposed(int64_t first, int64_t second, int classCount, int maskChannels, int format, bool endToEnd) {
    if (format == 3 || (format == 0 && endToEnd)) return false;
    const auto isFeatureCount = [&](int64_t dimension) {
        if (classCount <= 0) return false;
        return (format != 2 && dimension == 4LL + classCount + maskChannels)
            || (format != 1 && dimension == 5LL + classCount + maskChannels);
    };
    const bool firstFeatures = isFeatureCount(first), secondFeatures = isFeatureCount(second);
    if (firstFeatures != secondFeatures) return firstFeatures;
    // Final boxes may contain just one row. Only use the smaller-axis fallback
    // when both dimensions could actually contain raw detection features.
    return first >= 5 && first < second && second != 6 + maskChannels;
}

void applySessionSafety(AppConfig& config) {
    if (!config.observeOnly) return;
    config.movementEnabled = false;
    config.motionCompensationEnabled = false;
    config.eventActionEnabled = false;
    // Observing must not initialize an adapter, driver, serial device,
    // network endpoint, or virtual controller merely because the profile
    // selected one for a future Control session.
    config.outputTransport = vaporlock::hardware::kMouseTransport;
    config.controllerHostPipe.clear();
    config.controllerHostVirtualSlot = -1;
}

struct ScopedWinHandle {
    HANDLE value = nullptr;
    ~ScopedWinHandle() {
        if (value) CloseHandle(value);
    }
};

struct RuntimeStats {
    double fps = 0.0, captureMs = 0.0, detectMs = 0.0, trackMs = 0.0;
    int detections = 0, tracks = 0;
    bool outputActive = false, inferenceRan = false;
};

double boxIou(const RectF& a, const RectF& b) {
    const double left = std::max(a.x, b.x), top = std::max(a.y, b.y);
    const double right = std::min(a.x + a.w, b.x + b.w), bottom = std::min(a.y + a.h, b.y + b.h);
    const double overlap = std::max(0.0, right - left) * std::max(0.0, bottom - top);
    const double total = a.area() + b.area() - overlap;
    return total > 0.0 ? overlap / total : 0.0;
}

double distanceTo(double ax, double ay, double bx, double by) { return std::hypot(ax - bx, ay - by); }

std::wstring executableDirectory() {
    wchar_t path[MAX_PATH]{};
    GetModuleFileNameW(nullptr, path, MAX_PATH);
    return std::filesystem::path(path).parent_path().wstring();
}

std::filesystem::path defaultDatasetRoot() {
    // Portable releases intentionally keep datasets beside their other
    // release assets. The CPU/CUDA binary is in engine and the DirectML
    // binary is in engine/dml, so walk upward until that shared folder is
    // found.
    auto directory = std::filesystem::path(executableDirectory());
    for (int level = 0; level < 3; ++level) {
        const auto candidate = directory / L"datasets";
        if (std::filesystem::is_directory(candidate)) return candidate;
        if (!directory.has_parent_path()) break;
        directory = directory.parent_path();
    }
    return std::filesystem::path(executableDirectory()).parent_path() / L"datasets";
}

std::wstring readFile(const std::wstring& path) {
    std::wifstream stream(path);
    std::wstringstream value;
    value << stream.rdbuf();
    return value.str();
}

std::wstring jsonObject(const std::wstring& text, const std::wstring& key) {
    const auto keyAt = text.find(L"\"" + key + L"\"");
    if (keyAt == std::wstring::npos) return {};
    const auto open = text.find(L'{', keyAt);
    if (open == std::wstring::npos) return {};
    int depth = 0;
    for (std::size_t i = open; i < text.size(); ++i) {
        if (text[i] == L'{') ++depth;
        if (text[i] == L'}' && --depth == 0) return text.substr(open, i - open + 1);
    }
    return {};
}

std::wstring jsonString(const std::wstring& text, const std::wstring& key, const std::wstring& fallback = L"") {
    const auto keyAt = text.find(L"\"" + key + L"\"");
    if (keyAt == std::wstring::npos) return fallback;
    const auto colon = text.find(L':', keyAt);
    if (colon == std::wstring::npos) return fallback;
    const auto open = text.find_first_not_of(L" \t\r\n", colon + 1);
    if (open == std::wstring::npos || text[open] != L'\"') return fallback;
    const auto close = text.find(L'\"', open + 1);
    return close == std::wstring::npos ? fallback : text.substr(open + 1, close - open - 1);
}

double jsonNumber(const std::wstring& text, const std::wstring& key, double fallback) {
    const auto keyAt = text.find(L"\"" + key + L"\"");
    if (keyAt == std::wstring::npos) return fallback;
    const auto colon = text.find(L':', keyAt);
    const auto start = colon == std::wstring::npos ? std::wstring::npos : text.find_first_of(L"-0123456789", colon);
    if (start == std::wstring::npos) return fallback;
    wchar_t* end = nullptr;
    const double result = wcstod(text.c_str() + start, &end);
    return end == text.c_str() + start ? fallback : result;
}

bool jsonBool(const std::wstring& text, const std::wstring& key, bool fallback) {
    const auto keyAt = text.find(L"\"" + key + L"\"");
    if (keyAt == std::wstring::npos) return fallback;
    const auto colon = text.find(L':', keyAt);
    if (colon == std::wstring::npos) return fallback;
    const auto value = text.substr(colon + 1, 8);
    if (value.find(L"true") != std::wstring::npos) return true;
    if (value.find(L"false") != std::wstring::npos) return false;
    return fallback;
}

std::wstring jsonEscape(const std::wstring& value) {
    std::wstring escaped;
    for (wchar_t character : value) {
        if (character == L'\\' || character == L'\"') escaped.push_back(L'\\');
        escaped.push_back(character);
    }
    return escaped;
}

std::wstring strategyName(int strategy) {
    static const std::array<const wchar_t*, 5> names{L"Closest to center", L"Highest confidence", L"Largest object", L"Smallest object", L"Lowest movement"};
    return names[std::clamp(strategy, 0, 4)];
}

std::wstring strategyJsonName(int strategy) {
    static const std::array<const wchar_t*, 5> names{L"center", L"confidence", L"largest", L"smallest", L"movement"};
    return names[std::clamp(strategy, 0, 4)];
}

int strategyFromName(const std::wstring& value) {
    if (value == L"confidence") return 1;
    if (value == L"largest") return 2;
    if (value == L"smallest") return 3;
    if (value == L"movement") return 4;
    return 0;
}

bool classAllowed(const std::wstring& filter, int classId) {
    if (filter.empty()) return true;
    std::wstringstream items(filter);
    std::wstring item;
    while (std::getline(items, item, L',')) {
        const auto first = item.find_first_not_of(L" \t");
        if (first == std::wstring::npos) continue;
        const auto last = item.find_last_not_of(L" \t");
        wchar_t* end = nullptr;
        const long value = wcstol(item.substr(first, last - first + 1).c_str(), &end, 10);
        if (end && *end == L'\0' && value == classId) return true;
    }
    return false;
}

bool classListed(const std::wstring& list, int classId) {
    if (list.empty()) return false;
    std::wistringstream input(list);
    input.imbue(std::locale::classic());
    std::wstring token;
    while (std::getline(input, token, L',')) {
        try {
            std::size_t consumed = 0;
            const int value = std::stoi(token, &consumed);
            if (consumed == token.size() && value == classId) return true;
        } catch (...) {
            // Invalid watchlist entries are ignored; the remaining entries
            // continue to work and the GUI validates ordinary input.
        }
    }
    return false;
}

std::wstring narrowToWide(const std::string& text) {
    if (text.empty()) return {};
    const int size = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    std::wstring result(size, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size);
    return result;
}

std::string wideToUtf8(const std::wstring& text) {
    if (text.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
    return result;
}

#if VAPORLOCK_HAS_ONNXRUNTIME
struct OrtRuntimeState {
    HMODULE module = nullptr;
    std::wstring error;
    std::once_flag initialized;
};

OrtRuntimeState& ortRuntimeState() {
    static OrtRuntimeState state;
    return state;
}

std::wstring windowsErrorMessage(DWORD code) {
    wchar_t* buffer = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, code, 0, reinterpret_cast<wchar_t*>(&buffer), 0, nullptr);
    std::wstring message = length && buffer ? std::wstring(buffer, length) : L"Windows error " + std::to_wstring(code);
    if (buffer) LocalFree(buffer);
    while (!message.empty() && (message.back() == L'\r' || message.back() == L'\n' || message.back() == L' ')) message.pop_back();
    return message;
}

bool initializeOrtRuntime(std::wstring& error) {
    auto& state = ortRuntimeState();
    std::call_once(state.initialized, [&state] {
        std::vector<wchar_t> executablePath(32768);
        const DWORD length = GetModuleFileNameW(nullptr, executablePath.data(), static_cast<DWORD>(executablePath.size()));
        if (length == 0 || length >= executablePath.size()) {
            state.error = L"Vapor Lock could not locate its native engine directory.";
            return;
        }
        const auto runtimePath = std::filesystem::path(std::wstring(executablePath.data(), length)).parent_path() / L"onnxruntime.dll";
        if (!std::filesystem::is_regular_file(runtimePath)) {
            state.error = L"The private ONNX Runtime is missing beside the native engine: " + runtimePath.wstring();
            return;
        }
        state.module = LoadLibraryExW(
            runtimePath.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!state.module) {
            state.error = L"Vapor Lock could not load its private ONNX Runtime: " + windowsErrorMessage(GetLastError());
            return;
        }
        using GetApiBaseFunction = const OrtApiBase* (ORT_API_CALL*)();
        const auto getApiBase = reinterpret_cast<GetApiBaseFunction>(GetProcAddress(state.module, "OrtGetApiBase"));
        if (!getApiBase) {
            state.error = L"The private ONNX Runtime does not expose OrtGetApiBase.";
            FreeLibrary(state.module);
            state.module = nullptr;
            return;
        }
        const OrtApiBase* apiBase = getApiBase();
        const OrtApi* api = apiBase ? apiBase->GetApi(ORT_API_VERSION) : nullptr;
        if (!api) {
            state.error = L"The private ONNX Runtime is incompatible with this Vapor Lock engine.";
            FreeLibrary(state.module);
            state.module = nullptr;
            return;
        }
        Ort::InitApi(api);
    });
    if (!state.module) {
        error = state.error.empty() ? L"The private ONNX Runtime could not be initialized." : state.error;
        return false;
    }
    return true;
}
#endif

std::string base64Encode(const std::vector<unsigned char>& bytes) {
    static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string encoded;
    encoded.reserve((bytes.size() + 2U) / 3U * 4U);
    for (std::size_t index = 0; index < bytes.size(); index += 3U) {
        const unsigned value = static_cast<unsigned>(bytes[index]) << 16U |
            (index + 1U < bytes.size() ? static_cast<unsigned>(bytes[index + 1U]) << 8U : 0U) |
            (index + 2U < bytes.size() ? static_cast<unsigned>(bytes[index + 2U]) : 0U);
        encoded.push_back(alphabet[(value >> 18U) & 63U]);
        encoded.push_back(alphabet[(value >> 12U) & 63U]);
        encoded.push_back(index + 1U < bytes.size() ? alphabet[(value >> 6U) & 63U] : '=');
        encoded.push_back(index + 2U < bytes.size() ? alphabet[value & 63U] : '=');
    }
    return encoded;
}

bool encodeJpeg(int width, int height, const std::vector<unsigned char>& bgra, int quality, std::vector<unsigned char>& encoded) {
    if (width <= 0 || height <= 0 || bgra.size() != static_cast<std::size_t>(width) * height * 4U) return false;
    Microsoft::WRL::ComPtr<IWICImagingFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(factory.GetAddressOf())))) return false;
    Microsoft::WRL::ComPtr<IWICBitmap> bitmap;
    if (FAILED(factory->CreateBitmapFromMemory(width, height, GUID_WICPixelFormat32bppBGRA, width * 4,
                                                static_cast<UINT>(bgra.size()), const_cast<BYTE*>(bgra.data()), bitmap.GetAddressOf()))) return false;
    Microsoft::WRL::ComPtr<IStream> stream;
    if (FAILED(CreateStreamOnHGlobal(nullptr, TRUE, stream.GetAddressOf()))) return false;
    Microsoft::WRL::ComPtr<IWICBitmapEncoder> encoder;
    if (FAILED(factory->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, encoder.GetAddressOf())) ||
        FAILED(encoder->Initialize(stream.Get(), WICBitmapEncoderNoCache))) return false;
    Microsoft::WRL::ComPtr<IWICBitmapFrameEncode> frame;
    Microsoft::WRL::ComPtr<IPropertyBag2> properties;
    if (FAILED(encoder->CreateNewFrame(frame.GetAddressOf(), properties.GetAddressOf()))) return false;
    PROPBAG2 option{};
    option.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT value;
    VariantInit(&value);
    value.vt = VT_R4;
    value.fltVal = static_cast<float>(std::clamp(quality, 1, 100)) / 100.0F;
    if (FAILED(properties->Write(1, &option, &value)) || FAILED(frame->Initialize(properties.Get())) ||
        FAILED(frame->WriteSource(bitmap.Get(), nullptr)) || FAILED(frame->Commit()) || FAILED(encoder->Commit())) return false;
    HGLOBAL global = nullptr;
    if (FAILED(GetHGlobalFromStream(stream.Get(), &global)) || !global) return false;
    const auto size = GlobalSize(global);
    const auto* data = static_cast<const unsigned char*>(GlobalLock(global));
    if (!data || size == 0) return false;
    encoded.assign(data, data + size);
    GlobalUnlock(global);
    return true;
}

void drawLine(std::vector<unsigned char>& bgra, int width, int height, int x0, int y0, int x1, int y1, const std::array<unsigned char, 3>& color) {
    if (width <= 0 || height <= 0 || bgra.size() != static_cast<std::size_t>(width) * height * 4U
        || std::max(x0, x1) < 0 || std::min(x0, x1) >= width
        || std::max(y0, y1) < 0 || std::min(y0, y1) >= height) return;
    const auto dx = std::abs(static_cast<std::int64_t>(x1) - x0);
    const auto dy = std::abs(static_cast<std::int64_t>(y1) - y0);
    const int sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    const bool horizontal = dx >= dy;
    const auto major = horizontal ? dx : dy, minor = horizontal ? dy : dx;
    const auto origin = static_cast<std::int64_t>(horizontal ? x0 : y0);
    const auto extent = static_cast<std::int64_t>(horizontal ? width : height);
    const int direction = horizontal ? sx : sy;
    const auto first = std::max<std::int64_t>(0, direction > 0 ? -origin : origin - extent + 1);
    const auto last = std::min(major, direction > 0 ? extent - 1 - origin : origin);
    // Jump to the visible major-axis interval, preserving Bresenham's original
    // rounding phase. Work is bounded by the viewport, not off-screen distance.
    for (auto step = first; step <= last; ++step) {
        const auto offset = major ? static_cast<std::int64_t>(
            (static_cast<std::uint64_t>(minor) * step + major / 2) / major) : 0;
        const auto x = static_cast<std::int64_t>(x0) + sx * (horizontal ? step : offset);
        const auto y = static_cast<std::int64_t>(y0) + sy * (horizontal ? offset : step);
        if (x < 0 || y < 0 || x >= width || y >= height) continue;
        const auto index = (static_cast<std::size_t>(y) * width + x) * 4U;
        bgra[index] = color[2]; bgra[index + 1U] = color[1]; bgra[index + 2U] = color[0];
    }
}

void drawRectangle(std::vector<unsigned char>& bgra, int width, int height, const RectF& box, const std::array<unsigned char, 3>& color) {
    const int left = static_cast<int>(std::floor(box.x));
    const int top = static_cast<int>(std::floor(box.y));
    const int right = static_cast<int>(std::ceil(box.x + box.w));
    const int bottom = static_cast<int>(std::ceil(box.y + box.h));
    for (int thickness = 0; thickness < 2; ++thickness) {
        drawLine(bgra, width, height, left - thickness, top - thickness, right + thickness, top - thickness, color);
        drawLine(bgra, width, height, right + thickness, top - thickness, right + thickness, bottom + thickness, color);
        drawLine(bgra, width, height, right + thickness, bottom + thickness, left - thickness, bottom + thickness, color);
        drawLine(bgra, width, height, left - thickness, bottom + thickness, left - thickness, top - thickness, color);
    }
}

void drawFovGuide(std::vector<unsigned char>& bgra, int width, int height, int radius, bool rectangle,
                  const std::array<unsigned char, 3>& color, double offsetX = 0, double offsetY = 0) {
    if (radius <= 0) return;
    const int centerX = static_cast<int>(std::lround(width * 0.5 + offsetX));
    const int centerY = static_cast<int>(std::lround(height * 0.5 + offsetY));
    if (rectangle) {
        drawRectangle(bgra, width, height,
                      {static_cast<double>(centerX - radius), static_cast<double>(centerY - radius),
                       static_cast<double>(radius * 2), static_cast<double>(radius * 2)}, color);
        return;
    }
    constexpr int segments = 128;
    constexpr double tau = 6.28318530717958647692;
    int previousX = centerX + radius, previousY = centerY;
    for (int index = 1; index <= segments; ++index) {
        const double angle = tau * index / segments;
        const int x = static_cast<int>(std::lround(centerX + std::cos(angle) * radius));
        const int y = static_cast<int>(std::lround(centerY + std::sin(angle) * radius));
        drawLine(bgra, width, height, previousX, previousY, x, y, color);
        previousX = x;
        previousY = y;
    }
}

std::pair<double, double> rawTargetAnchorForBox(const Track& track, const AppConfig& config, const RectF& box) {
    double x = box.cx();
    double y = box.cy();
    const auto rule = config.classPolicies.find(track.detection.classId);
    const int mode = rule == config.classPolicies.end() ? config.anchorMode : rule->second.anchorMode;
    const double offsetX = rule == config.classPolicies.end() ? config.anchorOffsetX : rule->second.anchorX;
    const double offsetY = rule == config.classPolicies.end() ? config.anchorOffsetY : rule->second.anchorY;
    if (mode == 1) y = box.y;
    else if (mode == 2) y = box.y + box.h;
    else if (mode == 3) y = box.y + box.h * 0.25;
    x += box.w * std::clamp(offsetX, -1.0, 1.0);
    y += box.h * std::clamp(offsetY, -1.0, 1.0);
    return {x, y};
}

std::pair<double, double> rawTargetAnchor(const Track& track, const AppConfig& config, bool observation = false) {
    const auto& box = observation && track.hasObservation && track.hasStableAnchor ? track.observedBox : track.detection.box;
    return rawTargetAnchorForBox(track,config,box);
}

std::pair<double, double> targetAnchor(const Track& track, const AppConfig& config) {
    if (track.hasStableAnchor) return {track.stableAnchorX, track.stableAnchorY};
    return rawTargetAnchor(track, config);
}

bool pointExcluded(double x, double y, const AppConfig& config, int width=1000000, int height=1000000) {
    if (width <= 0 || height <= 0 || x < 0 || y < 0 || x > width-1 || y > height-1) return false;
    for (const auto& z : config.exclusionZones)
        if (x >= z.x && x <= std::min(z.x+z.width-1,width-1)
            && y >= z.y && y <= std::min(z.y+z.height-1,height-1)) return true;
    return false;
}

bool targetExcluded(const Track& track, const AppConfig& config, int width=1000000, int height=1000000) {
    if (config.exclusionZones.empty()) return false;
    const auto [x,y] = targetAnchor(track,config);
    // Observation geometry remains authoritative even during anchor smoothing.
    const auto [ox,oy] = rawTargetAnchorForBox(track,config,track.hasObservation ? track.observedBox : track.detection.box);
    return pointExcluded(x,y,config,width,height) || pointExcluded(ox,oy,config,width,height);
}

void updateStableAnchor(Track& track, const AppConfig& config, double elapsed = 1.0 / kLegacyResponseHz) {
    const auto [rawX, rawY] = rawTargetAnchor(track, config, true);
    if (!track.hasStableAnchor) {
        track.stableAnchorX = rawX;
        track.stableAnchorY = rawY;
        track.hasStableAnchor = true;
        return;
    }
    const double retained = timeRetention(std::clamp(config.anchorSmoothing, 0.0, 0.98), elapsed);
    track.stableAnchorX = track.stableAnchorX * retained + rawX * (1.0 - retained);
    track.stableAnchorY = track.stableAnchorY * retained + rawY * (1.0 - retained);
}

std::pair<double, double> aimingReference(int width, int height, const AppConfig& config) {
    if (config.frameReference.resolved) return {config.frameReference.x, config.frameReference.y};
    return {width * 0.5 + (config.aimReference == 1 ? config.aimReferenceX : 0.0),
            height * 0.5 + (config.aimReference == 1 ? config.aimReferenceY : 0.0)};
}

std::pair<double, double> settledOutputAnchor(const Track& track, int width, int height, const AppConfig& config) {
    auto [x, y] = targetAnchor(track, config);
    if (width <= 0 || height <= 0) return {x, y};
    const auto [centerX, centerY] = aimingReference(width, height, config);
    const double settleRadius = std::max(0.0, config.anchorSettleRadius);
    if (settleRadius > 0.0 && distanceTo(x, y, centerX, centerY) <= settleRadius) return {centerX, centerY};
    return {x, y};
}

double shapedDistance(double ax, double ay, double bx, double by, const AppConfig& config) {
    return config.fovShape == 1
        ? std::max(std::abs(ax - bx), std::abs(ay - by))
        : distanceTo(ax, ay, bx, by);
}

double anchorDistanceFromFrameCenter(double anchorX, double anchorY, int width, int height, const AppConfig& config) {
    const auto [x, y] = aimingReference(width, height, config);
    return shapedDistance(anchorX, anchorY, x, y, config);
}

std::pair<int, int> captureSourcePoint(int x, int y, int sourceWidth, int sourceHeight, DXGI_MODE_ROTATION rotation) {
    if (rotation == DXGI_MODE_ROTATION_ROTATE90) return {y, sourceHeight - 1 - x};
    if (rotation == DXGI_MODE_ROTATION_ROTATE180) return {sourceWidth - 1 - x, sourceHeight - 1 - y};
    if (rotation == DXGI_MODE_ROTATION_ROTATE270) return {sourceWidth - 1 - y, x};
    return {x, y};
}

class DesktopCapture {
public:
    DesktopCapture() : m_session(++s_sessionCounter) {}
    static HMONITOR sourceMonitor(int index) { return monitorFromIndex(index); }
    DesktopCapture(const DesktopCapture&) = delete;
    DesktopCapture& operator=(const DesktopCapture&) = delete;
    ~DesktopCapture() {
        resetDxgi();
        if (m_memory) { SelectObject(m_memory, m_oldBitmap); DeleteDC(m_memory); }
        if (m_bitmap) DeleteObject(m_bitmap);
    }

    void restartSession() {
        resetDxgi();
        m_bgra.clear();
        m_observedAt = {}; m_presentedAt = {}; m_capturedAtMs = 0;
        m_frameUpdated = false; m_captureMs = 0.0;
        m_nextDxgiAttempt = {}; m_requestedMonitor = 0;
        m_geometry = {}; m_actualGeometry = {};
        m_committedBackend = m_acquiredBackend = 0;
        m_maskedContent = false; m_recoveryReason = "none";
        m_recovery = vaporlock::CaptureRecovery{};
        m_recovery.reset(AimClock::now());
        m_dxgiFrames = m_dxgiTimeouts = 0;
        ++m_sourceGeneration; // Frame IDs remain monotonic on this object.
    }

    bool capture(const AppConfig& config) {
        return capture(config,0,[]{return false;});
    }

    template<class Cancelled>
    bool capture(const AppConfig& config,unsigned waitMs,Cancelled cancelled) {
        const auto started = std::chrono::steady_clock::now();
        m_frameUpdated = false;
        m_presentedAt = {}; m_acquiredAt = {}; m_acquisitionMs = 0;
        if(cancelled()){m_captureMs=0;return true;}
        const auto geometry = std::make_tuple(config.captureMonitor, config.captureRegion.xOffset,
            config.captureRegion.yOffset, config.captureRegion.width, config.captureRegion.height);
        if (geometry != m_geometry) {
            m_bgra.clear();
            m_observedAt = {};
            m_geometry = geometry;
            ++m_sourceGeneration;
            m_recovery.reset(started);
            m_nextDxgiAttempt = {};
        }
        if (config.captureMonitor <= 0 && m_duplication) resetDxgi();
        const bool captured = config.captureMonitor > 0 ? captureDxgi(config.captureMonitor, config.captureRegion,std::min(waitMs,4U),cancelled) : captureGdi(config);
        if (captured && m_frameUpdated) {
            if (m_committedBackend != m_acquiredBackend) {
                ++m_sourceGeneration;
                m_committedBackend = m_acquiredBackend;
            }
            const auto actualGeometry = std::make_tuple(m_left, m_top, m_width, m_height);
            if (actualGeometry != m_actualGeometry) {
                m_actualGeometry = actualGeometry;
                ++m_sourceGeneration;
            }
            ++m_frameId;
            m_capturedAtMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            m_observedAt = m_presentedAt == AimTime{} ? started : m_presentedAt;
        }
        m_captureMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
        return captured;
    }

    AimTime acquiredAt() const { return m_acquiredAt; }
    double acquisitionMilliseconds() const { return m_acquisitionMs; }
    double workMilliseconds() const { return std::max(0.,m_captureMs-m_acquisitionMs); }
    HBITMAP bitmap() const { return m_bitmap; }
    int width() const { return m_width; }
    int height() const { return m_height; }
    int left() const { return m_left; }
    int top() const { return m_top; }
    bool freshFrame() const { return m_frameUpdated; }
    bool hasCommittedFrame() const { return m_observedAt != AimTime{} && !m_bgra.empty(); }
    const char* backend() const { return m_committedBackend == 1 ? "dxgi" : m_committedBackend == 2 ? "gdi" : "none"; }
    const char* recoveryReason() const { return m_recoveryReason; }
    std::uint64_t recoveryProbes() const { return m_recovery.probes; }
    std::uint64_t recoveryTransitions() const { return m_recovery.transitions; }
    std::uint64_t dxgiTimeouts() const { return m_dxgiTimeouts; }
    std::uint64_t dxgiFrames() const { return m_dxgiFrames; }
    AimTime observedAt() const { return m_observedAt; }
    std::int64_t capturedAtMs() const { return m_capturedAtMs; }
    const std::vector<unsigned char>& bgra() const { return m_bgra; }
    CaptureFrameIdentity frameIdentity(std::uint64_t revision) const {
        return {m_session, m_sourceGeneration, m_frameId, revision};
    }

private:
#ifdef VAPORLOCK_CAPTURE_PACING_FIXTURE
    friend struct CloudCaptureProbe;
#endif
    inline static std::atomic<std::uint64_t> s_sessionCounter{0};
    const std::uint64_t m_session;
    std::uint64_t m_sourceGeneration = 0, m_frameId = 0;
    std::int64_t m_capturedAtMs = 0;
    std::tuple<int, int, int, int> m_actualGeometry{};
    AimTime m_observedAt{};
    AimTime m_presentedAt{}, m_acquiredAt{};
    std::tuple<int, int, int, int, int> m_geometry{};
    bool captureGdi(const AppConfig& config) {
        const auto acquisitionStarted = AimClock::now();
        HDC screen = GetDC(nullptr);
        if (!screen) return false;
        int desktopX = GetSystemMetrics(SM_XVIRTUALSCREEN);
        int desktopY = GetSystemMetrics(SM_YVIRTUALSCREEN);
        int desktopWidth = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        int desktopHeight = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        if (config.captureMonitor > 0) {
            MONITORINFO info{sizeof(MONITORINFO)};
            if (!GetMonitorInfoW(monitorFromIndex(config.captureMonitor), &info)) { ReleaseDC(nullptr, screen); return false; }
            desktopX = info.rcMonitor.left; desktopY = info.rcMonitor.top;
            desktopWidth = info.rcMonitor.right - info.rcMonitor.left;
            desktopHeight = info.rcMonitor.bottom - info.rcMonitor.top;
        }
        if (desktopWidth <= 0 || desktopHeight <= 0) { ReleaseDC(nullptr, screen); return false; }
        const int width = std::clamp(config.captureRegion.width, 1, desktopWidth);
        const int height = std::clamp(config.captureRegion.height, 1, desktopHeight);
        const int x = desktopX + std::clamp((desktopWidth - width) / 2 + config.captureRegion.xOffset, 0, desktopWidth - width);
        const int y = desktopY + std::clamp((desktopHeight - height) / 2 + config.captureRegion.yOffset, 0, desktopHeight - height);
        if (!m_memory || width != m_gdiWidth || height != m_gdiHeight) {
            if (m_memory) { SelectObject(m_memory, m_oldBitmap); DeleteDC(m_memory); m_memory = nullptr; }
            if (m_bitmap) { DeleteObject(m_bitmap); m_bitmap = nullptr; }
            BITMAPINFO info{};
            info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
            info.bmiHeader.biWidth = width;
            info.bmiHeader.biHeight = -height;
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            info.bmiHeader.biCompression = BI_RGB;
            m_memory = CreateCompatibleDC(screen);
            m_bitmap = CreateDIBSection(screen, &info, DIB_RGB_COLORS, &m_gdiPixels, nullptr, 0);
            if (!m_memory || !m_bitmap) {
                if (m_memory) { DeleteDC(m_memory); m_memory = nullptr; }
                if (m_bitmap) { DeleteObject(m_bitmap); m_bitmap = nullptr; }
                ReleaseDC(nullptr, screen);
                return false;
            }
            m_oldBitmap = SelectObject(m_memory, m_bitmap);
            m_gdiWidth = width; m_gdiHeight = height;
        }
        const BOOL copied = BitBlt(m_memory, 0, 0, width, height, screen, x, y, SRCCOPY | CAPTUREBLT);
        GdiFlush();
        ReleaseDC(nullptr, screen);
        if (!copied) return false;
        m_width = width;
        m_height = height;
        m_bgra.resize(static_cast<std::size_t>(width) * height * 4U);
        memcpy(m_bgra.data(), m_gdiPixels, m_bgra.size());
        m_left = x;
        m_top = y;
        m_frameUpdated = true;
        m_presentedAt = acquisitionStarted; m_acquiredAt = acquisitionStarted;
        m_acquiredBackend = 2;
        return true;
    }

    bool recoverGdi(int monitor, const CaptureRegion& region, const char* reason, bool unavailable = false) {
        // This flag describes pixels Windows already masked in a DXGI frame;
        // it is not a permanent prohibition on capturing the whole monitor.
        // Ordinary screen-DC capture retains Windows display-affinity rules.
        // Keep the last DXGI mask observation visible in recovery diagnostics.
        if (m_maskedContent) reason = "dxgi-stalled-after-masked-frame";
        const auto now = AimClock::now();
        const bool first = m_bgra.empty();
        // An empty startup buffer is not evidence that DXGI is unavailable.
        // It may initially report only a pointer event or an old idle desktop.
        // Give image delivery the same bounded probe policy used mid-session.
        if (!(first && unavailable) && !m_recovery.probeDue(now)) return true;
        AppConfig fallback; fallback.captureMonitor = monitor; fallback.captureRegion = region;
        if ((first && unavailable) || m_recovery.recovering) {
            if (!captureGdi(fallback)) return false;
            m_recovery.recover(); m_recoveryReason = reason;
            return true;
        }
        m_recovery.probing(now);
        // A probe is provisional. Do not replace the committed pixels,
        // geometry, identity or clock until actual GDI progression is verified.
        auto retained = std::move(m_bgra);
        const auto geometry = std::make_tuple(m_width, m_height, m_left, m_top);
        const auto presented = m_presentedAt;
        const auto acquiredBackend = m_acquiredBackend;
        const bool captured = captureGdi(fallback);
        if (!captured) m_recovery.failedProbe();
        if (captured && m_recovery.progressing(m_bgra, m_width, m_height)) {
            m_recovery.recover(); m_recoveryReason = reason;
            return true;
        }
        if (captured && first) {
            // One real startup observation also serves a motionless desktop.
            // Keep the probe comparison/deadline, but do not latch GDI: the
            // next genuinely newer DXGI presentation can still take over.
            m_recoveryReason = "dxgi-startup-snapshot";
            return true;
        }
        m_bgra = std::move(retained);
        std::tie(m_width, m_height, m_left, m_top) = geometry;
        m_presentedAt = presented; m_acquiredBackend = acquiredBackend;
        m_frameUpdated = false;
        return true; // Successful provisional poll; no image was committed.
    }

    template<class Cancelled>
    bool captureDxgi(int monitor,const CaptureRegion& region,unsigned waitMs,Cancelled cancelled) {
        if (m_requestedMonitor != monitor) {
            resetDxgi(); m_requestedMonitor = monitor;
            m_recovery.failedProbe(); m_nextDxgiAttempt = {};
        }
        if (m_recovery.recovering) {
            // Keep a verified, progressing source for this capture geometry.
            // Repeated promotion on short DXGI bursts caused half-second gaps
            // and retired valid target/preview state on every failed return.
            // Initial DXGI-unavailable fallback follows the same policy; an
            // explicit monitor/crop reset or a new session retries DXGI.
            if (m_duplication || m_device) resetDxgi();
            return recoverGdi(monitor, region, m_recoveryReason);
        }
        if (!m_duplication) {
            const auto now = AimClock::now();
            if (now < m_nextDxgiAttempt) return recoverGdi(monitor, region, "dxgi-unavailable", true);
            m_nextDxgiAttempt = now + vaporlock::CaptureRecovery::interval;
            if (!initializeDxgi(monitor)) return recoverGdi(monitor, region, "dxgi-unavailable", true);
        }
        DXGI_OUTDUPL_FRAME_INFO frameInfo{};
        Microsoft::WRL::ComPtr<IDXGIResource> resource;
        // DXGI can accumulate dirty regions while we own the previous frame.
        // Release immediately before Acquire, avoiding redundant GPU desktop
        // copies while inference is using our independent CPU snapshot.
        if (FAILED(releaseOwnedFrame())) {
            resetDxgi();
            return recoverGdi(monitor, region, "dxgi-release-failed", true);
        }
        // Only the fresh-image-paced Cloud caller supplies a finite wait;
        // other model paths keep their existing zero-wait polling behavior.
        if(cancelled())return true;
        const auto acquiring=AimClock::now();
        const HRESULT acquired = m_duplication->AcquireNextFrame(waitMs, &frameInfo, resource.GetAddressOf());
        const auto acquiredAt=AimClock::now();
        m_acquisitionMs=std::chrono::duration<double,std::milli>(acquiredAt-acquiring).count();
        // AcquireNextFrame cannot be cancelled. Retire an intervening config,
        // shutdown or revocation before copying/committing any image.
        if(cancelled()) {
            if(SUCCEEDED(acquired)) {
                m_frameAcquired=true;
                if(FAILED(releaseOwnedFrame()))resetDxgi();
            }
            return true;
        }
        if (acquired == DXGI_ERROR_WAIT_TIMEOUT) {
            ++m_dxgiTimeouts;
            return recoverGdi(monitor, region, "dxgi-timeout");
        }
        if (acquired == DXGI_ERROR_ACCESS_LOST) {
            resetDxgi();
            return recoverGdi(monitor, region, "dxgi-access-lost", true);
        }
        if (FAILED(acquired)) {
            return recoverGdi(monitor, region, "dxgi-acquire-failed", true);
        }
        m_frameAcquired = true; m_acquiredAt=acquiredAt;
        if (frameInfo.ProtectedContentMaskedOut) m_maskedContent = true;
        auto release = [&]() {
            if (FAILED(releaseOwnedFrame())) resetDxgi();
        };
        if (frameInfo.LastPresentTime.QuadPart == 0) {
            release();
            return recoverGdi(monitor, region, "dxgi-no-image-update");
        }
        LARGE_INTEGER counter{}, frequency{};
        if (frameInfo.LastPresentTime.QuadPart > 0 && QueryPerformanceCounter(&counter)
            && QueryPerformanceFrequency(&frequency) && frequency.QuadPart > 0
            && frameInfo.LastPresentTime.QuadPart <= counter.QuadPart) {
            const double age = static_cast<double>(counter.QuadPart - frameInfo.LastPresentTime.QuadPart)
                                                 / static_cast<double>(frequency.QuadPart);
            m_presentedAt = AimClock::now() - std::chrono::duration_cast<AimClock::duration>(std::chrono::duration<double>(age));
        }
        if (!m_recovery.acceptPresentation(m_presentedAt, AimClock::now(), m_observedAt)) {
            release();
            return recoverGdi(monitor, region, "dxgi-stale-presentation");
        }
        Microsoft::WRL::ComPtr<ID3D11Texture2D> desktopTexture;
        const HRESULT queried = resource.As(&desktopTexture);
        if (FAILED(queried)) { release(); return false; }
        D3D11_TEXTURE2D_DESC source{};
        desktopTexture->GetDesc(&source);
        DXGI_OUTPUT_DESC outputDescription{};
        if (FAILED(m_output->GetDesc(&outputDescription))) { release(); return false; }
        const auto rotation = outputDescription.Rotation;
        const bool swapped = rotation == DXGI_MODE_ROTATION_ROTATE90 || rotation == DXGI_MODE_ROTATION_ROTATE270;
        const int logicalWidth = static_cast<int>(swapped ? source.Height : source.Width);
        const int logicalHeight = static_cast<int>(swapped ? source.Width : source.Height);
        const int width = std::clamp(region.width, 1, logicalWidth);
        const int height = std::clamp(region.height, 1, logicalHeight);
        const int left = std::clamp((logicalWidth - width) / 2 + region.xOffset, 0, logicalWidth - width);
        const int top = std::clamp((logicalHeight - height) / 2 + region.yOffset, 0, logicalHeight - height);
        const auto [x0, y0] = captureSourcePoint(left, top, source.Width, source.Height, rotation);
        const auto [x1, y1] = captureSourcePoint(left + width - 1, top + height - 1, source.Width, source.Height, rotation);
        const int cropLeft = std::min(x0, x1), cropTop = std::min(y0, y1);
        const int cropWidth = std::abs(x1 - x0) + 1, cropHeight = std::abs(y1 - y0) + 1;
        const D3D11_BOX crop{static_cast<UINT>(cropLeft), static_cast<UINT>(cropTop), 0,
            static_cast<UINT>(cropLeft + cropWidth), static_cast<UINT>(cropTop + cropHeight), 1};
        if (!ensureStagingSurface(source, cropWidth, cropHeight)) { release(); return false; }
        m_context->CopySubresourceRegion(m_staging.Get(), 0, 0, 0, 0, desktopTexture.Get(), 0, &crop);
        D3D11_MAPPED_SUBRESOURCE mapped{};
        const HRESULT mappedResult = m_context->Map(m_staging.Get(), 0, D3D11_MAP_READ, 0, &mapped);
        if (FAILED(mappedResult)) { release(); return false; }
        m_width = width;
        m_height = height;
        m_left = outputDescription.DesktopCoordinates.left + left;
        m_top = outputDescription.DesktopCoordinates.top + top;
        m_bgra.resize(static_cast<std::size_t>(m_width) * m_height * 4U);
        const auto* pixels = static_cast<const unsigned char*>(mapped.pData);
        const std::size_t rowBytes = static_cast<std::size_t>(m_width) * 4U;
        for (int row = 0; row < m_height; ++row) {
            if (rotation == DXGI_MODE_ROTATION_IDENTITY || rotation == DXGI_MODE_ROTATION_UNSPECIFIED) {
                memcpy(m_bgra.data() + static_cast<std::size_t>(row) * rowBytes, pixels + static_cast<std::size_t>(row) * mapped.RowPitch, rowBytes);
            } else {
                for (int column = 0; column < m_width; ++column) {
                    const auto [sx, sy] = captureSourcePoint(left + column, top + row, source.Width, source.Height, rotation);
                    memcpy(m_bgra.data() + static_cast<std::size_t>(row) * rowBytes + column * 4U,
                        pixels + static_cast<std::size_t>(sy - cropTop) * mapped.RowPitch + (sx - cropLeft) * 4U, 4U);
                }
            }
        }
        m_context->Unmap(m_staging.Get(), 0);
        m_frameUpdated = true;
        m_acquiredBackend = 1;
        m_maskedContent = frameInfo.ProtectedContentMaskedOut != FALSE;
        ++m_dxgiFrames;
        m_recovery.presented(AimClock::now());
        m_recoveryReason = "none";
        return true;
    }

    bool initializeDxgi(int monitor) {
        const HMONITOR target = monitorFromIndex(monitor);
        if (!target) return false;
        Microsoft::WRL::ComPtr<IDXGIFactory1> factory;
        if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(factory.GetAddressOf())))) return false;
        for (UINT adapterIndex = 0;; ++adapterIndex) {
            Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;
            if (factory->EnumAdapters1(adapterIndex, adapter.GetAddressOf()) == DXGI_ERROR_NOT_FOUND) break;
            DXGI_ADAPTER_DESC1 description{};
            if (FAILED(adapter->GetDesc1(&description)) || (description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
            for (UINT outputIndex = 0;; ++outputIndex) {
                Microsoft::WRL::ComPtr<IDXGIOutput> output;
                if (adapter->EnumOutputs(outputIndex, output.GetAddressOf()) == DXGI_ERROR_NOT_FOUND) break;
                DXGI_OUTPUT_DESC outputDescription{};
                if (FAILED(output->GetDesc(&outputDescription)) || outputDescription.Monitor != target) continue;
                if (FAILED(output.As(&m_output))) continue;
                m_adapter = adapter;
                break;
            }
            if (m_output) break;
        }
        if (!m_adapter || !m_output) { resetDxgi(); return false; }
        UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
        D3D_FEATURE_LEVEL feature{};
        if (FAILED(D3D11CreateDevice(m_adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags, nullptr, 0, D3D11_SDK_VERSION,
                                     m_device.GetAddressOf(), &feature, m_context.GetAddressOf()))) {
            resetDxgi();
            return false;
        }
        Microsoft::WRL::ComPtr<IDXGIDevice1> dxgiDevice;
        if (SUCCEEDED(m_device.As(&dxgiDevice))) dxgiDevice->SetMaximumFrameLatency(1);
        if (FAILED(m_output->DuplicateOutput(m_device.Get(), m_duplication.GetAddressOf()))) { resetDxgi(); return false; }
        m_captureMonitor = monitor;
        return true;
    }

    static BOOL CALLBACK enumerateMonitor(HMONITOR monitor, HDC, LPRECT, LPARAM context) {
        reinterpret_cast<std::vector<HMONITOR>*>(context)->push_back(monitor);
        return TRUE;
    }

    static HMONITOR monitorFromIndex(int monitor) {
        std::vector<HMONITOR> monitors;
        EnumDisplayMonitors(nullptr, nullptr, enumerateMonitor, reinterpret_cast<LPARAM>(&monitors));
        return monitor > 0 && static_cast<std::size_t>(monitor) <= monitors.size() ? monitors[static_cast<std::size_t>(monitor - 1)] : nullptr;
    }

    bool ensureStagingSurface(const D3D11_TEXTURE2D_DESC& source, int width, int height) {
        if (m_staging && static_cast<UINT>(width) == m_stagingWidth && static_cast<UINT>(height) == m_stagingHeight && source.Format == m_stagingFormat) return true;
        m_staging.Reset();
        D3D11_TEXTURE2D_DESC staging{};
        staging.Width = static_cast<UINT>(width);
        staging.Height = static_cast<UINT>(height);
        staging.MipLevels = 1;
        staging.ArraySize = 1;
        staging.Format = source.Format;
        staging.SampleDesc.Count = 1;
        staging.Usage = D3D11_USAGE_STAGING;
        staging.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (FAILED(m_device->CreateTexture2D(&staging, nullptr, m_staging.GetAddressOf()))) return false;
        m_stagingWidth = static_cast<UINT>(width);
        m_stagingHeight = static_cast<UINT>(height);
        m_stagingFormat = source.Format;
        return true;
    }

    HRESULT releaseOwnedFrame() noexcept {
        if (!m_frameAcquired) return S_OK;
        m_frameAcquired = false;
        return m_duplication ? m_duplication->ReleaseFrame() : E_UNEXPECTED;
    }

    void resetDxgi() {
        releaseOwnedFrame();
        m_staging.Reset();
        m_duplication.Reset();
        m_context.Reset();
        m_device.Reset();
        m_output.Reset();
        m_adapter.Reset();
        m_stagingWidth = m_stagingHeight = 0;
        m_stagingFormat = DXGI_FORMAT_UNKNOWN;
        m_captureMonitor = 0;
    }

    HBITMAP m_bitmap = nullptr;
    HDC m_memory = nullptr;
    HGDIOBJ m_oldBitmap = nullptr;
    void* m_gdiPixels = nullptr;
    int m_gdiWidth = 0, m_gdiHeight = 0;
    int m_width = 1, m_height = 1;
    int m_left = 0, m_top = 0;
    double m_captureMs = 0.0, m_acquisitionMs=0.0;
    bool m_frameUpdated = false;
    bool m_frameAcquired = false;
    int m_acquiredBackend = 0, m_committedBackend = 0;
    bool m_maskedContent = false;
    int m_requestedMonitor = 0;
    AimTime m_nextDxgiAttempt{};
    vaporlock::CaptureRecovery m_recovery;
    std::uint64_t m_dxgiTimeouts = 0, m_dxgiFrames = 0;
    const char* m_recoveryReason = "none";
    std::vector<unsigned char> m_bgra;
    Microsoft::WRL::ComPtr<IDXGIAdapter1> m_adapter;
    Microsoft::WRL::ComPtr<IDXGIOutput1> m_output;
    Microsoft::WRL::ComPtr<ID3D11Device> m_device;
    Microsoft::WRL::ComPtr<ID3D11DeviceContext> m_context;
    Microsoft::WRL::ComPtr<IDXGIOutputDuplication> m_duplication;
    Microsoft::WRL::ComPtr<ID3D11Texture2D> m_staging;
    UINT m_stagingWidth = 0, m_stagingHeight = 0;
    DXGI_FORMAT m_stagingFormat = DXGI_FORMAT_UNKNOWN;
    int m_captureMonitor = 0;
};

void drawSegmentationMask(std::vector<unsigned char>& bgra, int width, int height, const Detection& detection,
                          const std::array<unsigned char, 3>& color) {
    if (detection.segmentationMask.empty() || detection.segmentationMaskWidth <= 0 || detection.segmentationMaskHeight <= 0
        || detection.segmentationInputWidth <= 0 || detection.segmentationInputHeight <= 0) return;
    const int left = std::max(0, static_cast<int>(std::floor(detection.box.x)));
    const int top = std::max(0, static_cast<int>(std::floor(detection.box.y)));
    const int right = std::min(width, static_cast<int>(std::ceil(detection.box.x + detection.box.w)));
    const int bottom = std::min(height, static_cast<int>(std::ceil(detection.box.y + detection.box.h)));
    for (int y = top; y < bottom; ++y) {
        const int maskY = std::clamp(static_cast<int>(((y * detection.segmentationScaleY + detection.segmentationPadY)
                            / detection.segmentationInputHeight) * detection.segmentationMaskHeight), 0, detection.segmentationMaskHeight - 1);
        for (int x = left; x < right; ++x) {
            const int maskX = std::clamp(static_cast<int>(((x * detection.segmentationScaleX + detection.segmentationPadX)
                                / detection.segmentationInputWidth) * detection.segmentationMaskWidth), 0, detection.segmentationMaskWidth - 1);
            if (!detection.segmentationMask[static_cast<std::size_t>(maskY) * detection.segmentationMaskWidth + maskX]) continue;
            const auto offset = (static_cast<std::size_t>(y) * width + x) * 4U;
            // A restrained alpha makes the actual object edge visible without
            // hiding the debug frame beneath it.
            bgra[offset] = static_cast<unsigned char>((static_cast<int>(bgra[offset]) * 3 + color[2] * 2) / 5);
            bgra[offset + 1U] = static_cast<unsigned char>((static_cast<int>(bgra[offset + 1U]) * 3 + color[1] * 2) / 5);
            bgra[offset + 2U] = static_cast<unsigned char>((static_cast<int>(bgra[offset + 2U]) * 3 + color[0] * 2) / 5);
        }
    }
}

std::vector<unsigned char> visualizedFrame(int width, int height, std::vector<unsigned char> pixels,
                                         const std::vector<Track>& tracks, const std::optional<Track>& selected, const AppConfig& config) {
    if (!config.overlayEnabled) return pixels;
    if (config.overlayFov) {
        const auto [refX, refY] = aimingReference(width, height, config);
        const double offsetX = refX - width * 0.5, offsetY = refY - height * 0.5;
        drawFovGuide(pixels, width, height, config.acquisitionRadius, config.fovShape == 1, {70, 120, 170}, offsetX, offsetY);
        drawFovGuide(pixels, width, height, config.nearRadius, config.fovShape == 1, {70, 180, 210}, offsetX, offsetY);
        drawFovGuide(pixels, width, height, config.snapRadius, config.fovShape == 1, {232, 135, 197}, offsetX, offsetY);
    }
    for (const auto& track : tracks) {
        if (config.overlaySegmentationMasks) drawSegmentationMask(pixels, width, height, track.detection,
                                                                  track.selected ? std::array<unsigned char, 3>{255, 190, 50} : std::array<unsigned char, 3>{50, 210, 170});
        if (config.overlayBoxes) drawRectangle(pixels, width, height, track.detection.box, track.selected ? std::array<unsigned char, 3>{255, 190, 50} : std::array<unsigned char, 3>{50, 210, 170});
    }
    if (selected) {
        const auto [anchorX, anchorY] = targetAnchor(*selected, config);
        const double seconds = std::clamp(config.predictionLeadMs / 1000.0, 0.0, config.predictionLimitMs / 1000.0);
        const double predictedX = anchorX + selected->vx * seconds + 0.5 * selected->ax * seconds * seconds;
        const double predictedY = anchorY + selected->vy * seconds + 0.5 * selected->ay * seconds * seconds;
        const std::array<unsigned char, 3> warm{255, 190, 50};
        drawLine(pixels, width, height, static_cast<int>(anchorX) - 8, static_cast<int>(anchorY), static_cast<int>(anchorX) + 8, static_cast<int>(anchorY), warm);
        drawLine(pixels, width, height, static_cast<int>(anchorX), static_cast<int>(anchorY) - 8, static_cast<int>(anchorX), static_cast<int>(anchorY) + 8, warm);
        if (config.overlayMotion) drawLine(pixels, width, height, static_cast<int>(anchorX), static_cast<int>(anchorY), static_cast<int>(predictedX), static_cast<int>(predictedY), warm);
    }
    return pixels;
}

#include "app/DatasetWriter.h"
#include "app/CloudPipeDetector.h"

class OnnxDetector {
public:
    ~OnnxDetector() { clearModel(); }

    void clearModel() {
        m_cloud.reset();
#if VAPORLOCK_HAS_CUDA_PREPROCESS
        if(m_usingCudaDevice) vaporlock::cuda::activateDevice(m_inferenceDevice);
#endif
        m_modelPath.clear();
        m_providerName.clear();
        m_deviceName.clear();
#if VAPORLOCK_HAS_ONNXRUNTIME
#if VAPORLOCK_FORCE_TENSORRT_RTX
        m_tensorRt.reset();
#endif
        m_runtime.reset();
        m_cpuPlacement.configure(L"");
        m_inputName.clear();
        m_outputNames.clear();
        m_inputNhwc = false;
        m_modelClassCount = -1;
        m_modelEndToEnd = false;
        m_modelLabels.clear();
        m_outputShapes.clear();
        m_detectionOutputIndex = -1;
        m_prototypeOutputIndex = -1;
        m_semanticOutputIndex = -1;
        m_maskChannels = 0;

#endif
#if VAPORLOCK_HAS_CUDA_PREPROCESS
        vaporlock::cuda::releaseBuffers(m_cudaBgra, m_cudaBgraCapacity, m_cudaChw, m_cudaChwCapacity);
        m_cudaPreprocessEnabled = false;
#endif
    }

    bool loadModel(const std::wstring& path, InferenceProvider requestedProvider, int intraOpThreads, int interOpThreads, bool cudaGraphReplay, bool cudaPreprocess, std::wstring& error, int deviceIndex = -1, const std::wstring& cpuSets = L"", const std::wstring& cloudPipe = L"", DWORD cloudWorkerPid = 0) {
        if(deviceIndex < -1 || deviceIndex >= 64) { error=L"Invalid inference GPU index."; return false; }
        if (path.empty()) { clearModel(); return true; }
        if (!std::filesystem::exists(path)) { error = L"The selected model file no longer exists."; return false; }
        auto extension=std::filesystem::path(path).extension().wstring();
        std::transform(extension.begin(),extension.end(),extension.begin(),[](wchar_t c){return static_cast<wchar_t>(towlower(c));});
        if(extension==L".cloud") {
            clearModel();
            if(requestedProvider==InferenceProvider::TensorRtRtx){error=L"This .cloud alpha supports DirectML, CUDA and CPU. TensorRT RTX remains available for ONNX models.";return false;}
            try {
            m_cloud=std::make_unique<cloud_native::Detector>();m_cloud->connect(cloudPipe,cloudWorkerPid);
                m_modelPath=path;m_inferenceDevice=requestedProvider==InferenceProvider::Cpu?-1:std::max(0,deviceIndex);
                m_usingCudaDevice=false;
                m_providerName=requestedProvider==InferenceProvider::DirectMl?L".cloud DirectML":requestedProvider==InferenceProvider::Cuda?L".cloud CUDA":L".cloud CPU";
                m_deviceName=m_inferenceDevice<0?L"CPU":m_providerName+L" GPU "+std::to_wstring(m_inferenceDevice);
                return true;
            } catch(const std::exception& e){clearModel();error=narrowToWide(e.what());return false;}
        }
        if (std::filesystem::path(path).extension() != L".onnx") { error = L"Choose an ONNX model file (.onnx)."; return false; }
        clearModel();
        m_inferenceDevice = requestedProvider==InferenceProvider::Cpu ? -1 : std::max(0,deviceIndex);
        m_usingCudaDevice = requestedProvider==InferenceProvider::Cuda || requestedProvider==InferenceProvider::TensorRtRtx;
#if VAPORLOCK_HAS_CUDA_PREPROCESS
        if(m_usingCudaDevice) {
            std::string name;
            if(!vaporlock::cuda::selectDevice(m_inferenceDevice,name)) {
                error=L"The selected CUDA GPU is unavailable or incompatible. Choose another inference GPU."; return false;
            }
            m_deviceName=narrowToWide(name);
        }
#endif
#if !VAPORLOCK_HAS_CUDA_PREPROCESS
        (void)cudaPreprocess;
#endif
#if VAPORLOCK_HAS_ONNXRUNTIME
        if (!initializeOrtRuntime(error)) return false;
        try {
            if(m_usingCudaDevice && m_deviceName.empty()) m_deviceName=narrowToWide(vaporlock::inference::cudaDeviceName(m_inferenceDevice));
            auto environment = std::make_unique<Ort::Env>(ORT_LOGGING_LEVEL_WARNING, "VaporLock");
            m_cpuPlacement.configure(requestedProvider==InferenceProvider::Cpu ? cpuSets : L"");
            Ort::SessionOptions options;
            options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
            if (intraOpThreads > 0) options.SetIntraOpNumThreads(intraOpThreads);
            if (interOpThreads > 0) options.SetInterOpNumThreads(interOpThreads);
            if(requestedProvider==InferenceProvider::Cpu) m_cpuPlacement.options(options,intraOpThreads);
#if VAPORLOCK_FORCE_DML
            (void)requestedProvider;
            (void)cudaGraphReplay;
            (void)cudaPreprocess;
            options.DisableMemPattern();
            options.SetExecutionMode(ExecutionMode::ORT_SEQUENTIAL);
            const OrtDmlApi* dmlApi = nullptr;
            Ort::ThrowOnError(Ort::GetApi().GetExecutionProviderApi(
                "DML", ORT_API_VERSION, reinterpret_cast<const void**>(&dmlApi)));
            if (!dmlApi) throw std::runtime_error("The private ONNX Runtime does not expose the DirectML provider API.");
            const auto dmlAdapter = vaporlock::inference::chooseDmlAdapter(deviceIndex);
            m_inferenceDevice = static_cast<int>(dmlAdapter.legacyIndex);
            Ort::ThrowOnError(dmlApi->SessionOptionsAppendExecutionProvider_DML(options, dmlAdapter.legacyIndex));
            const std::wstring providerName = L"DirectML";
#else
            std::wstring providerName = L"CPU";
#if VAPORLOCK_FORCE_TENSORRT_RTX
            (void)cudaGraphReplay;
            if (requestedProvider == InferenceProvider::TensorRtRtx) {
                providerName = L"TensorRT RTX";
                options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
                options.SetIntraOpNumThreads(1);
                options.SetInterOpNumThreads(1);
                options.AddConfigEntry("session.intra_op.allow_spinning", "0");
                options.AddConfigEntry("session.inter_op.allow_spinning", "0");
            } else if (requestedProvider == InferenceProvider::DirectMl) {
                error = L"DirectML uses the VaporLockDml engine. Choose DirectML from the control center so it launches that engine.";
                return false;
            } else if (requestedProvider != InferenceProvider::Cpu) {
                error = L"This TensorRT RTX engine accepts only TensorRT RTX or CPU.";
                return false;
            }
#else
#if VAPORLOCK_HAS_CUDA
            if (requestedProvider == InferenceProvider::Cuda) {
                Ort::CUDAProviderOptions cudaOptions;
                // Session::Run below does not bind persistent CUDA outputs. ORT
                // graph replay requires stable device addresses via I/O binding.
                cudaOptions.Update({{"device_id", std::to_string(m_inferenceDevice)}, {"do_copy_in_default_stream", "1"}, {"enable_cuda_graph", "0"}});
                options.AppendExecutionProvider_CUDA_V2(*cudaOptions);
                providerName = cudaGraphReplay ? L"CUDA (graph replay unavailable)" : L"CUDA";
            } else if (requestedProvider == InferenceProvider::DirectMl) {
                error = L"DirectML uses the VaporLockDml engine. Choose DirectML from the control center so it launches that engine.";
                return false;
            }
#else
            (void)cudaGraphReplay;
            (void)cudaPreprocess;
            if (requestedProvider != InferenceProvider::Cpu) {
                error = L"This Vapor Lock engine was built without the requested inference provider.";
                return false;
            }
#endif
#endif
#endif
            auto session = std::make_unique<Ort::Session>(*environment, path.c_str(), options);
            if (session->GetInputCount() < 1 || session->GetOutputCount() < 1) {
                error = L"A supported detector must expose at least one image input and one output (found "
                        + std::to_wstring(session->GetInputCount()) + L" input(s), "
                        + std::to_wstring(session->GetOutputCount()) + L" output(s)).";
                return false;
            }
            Ort::AllocatorWithDefaultOptions allocator;
            std::string selectedInputName;
            std::vector<int64_t> selectedInputShape;
            ONNXTensorElementDataType selectedInputType = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
            bool foundImageInput = false;
            std::wstring discoveredInputs;
            for (std::size_t index = 0; index < session->GetInputCount(); ++index) {
                auto inputName = session->GetInputNameAllocated(index, allocator);
                const auto inputType = session->GetInputTypeInfo(index);
                if (!discoveredInputs.empty()) discoveredInputs += L", ";
                discoveredInputs += narrowToWide(inputName.get());
                if (inputType.GetONNXType() != ONNX_TYPE_TENSOR) continue;
                const auto candidate = inputType.GetTensorTypeAndShapeInfo();
                const auto candidateShape = candidate.GetShape();
                const bool nchwCandidate = candidateShape.size() == 4 && candidateShape[1] == 3;
                const bool nhwcCandidate = candidateShape.size() == 4 && candidateShape[3] == 3;
                if (!foundImageInput && (nchwCandidate || nhwcCandidate)) {
                    selectedInputName = inputName.get();
                    selectedInputShape = candidateShape;
                    selectedInputType = candidate.GetElementType();
                    foundImageInput = true;
                }
            }
            if (!foundImageInput) {
                error = L"This model does not expose a four-dimensional NCHW or NHWC, three-channel image input. Inputs found: "
                        + (discoveredInputs.empty() ? L"none" : discoveredInputs) + L".";
                return false;
            }
            std::vector<std::string> outputNames;
            std::vector<std::vector<int64_t>> outputShapes;
            int detectionOutputIndex = -1;
            int prototypeOutputIndex = -1;
            int semanticOutputIndex = -1;
            int maskChannels = 0;
            for (std::size_t index = 0; index < session->GetOutputCount(); ++index) {
                auto outputName = session->GetOutputNameAllocated(index, allocator);
                outputNames.emplace_back(outputName.get());
                const auto outputType = session->GetOutputTypeInfo(index);
                std::vector<int64_t> outputShape;
                if (outputType.GetONNXType() == ONNX_TYPE_TENSOR) outputShape = outputType.GetTensorTypeAndShapeInfo().GetShape();
                if ((outputShape.size() == 2 || outputShape.size() == 3) && detectionOutputIndex < 0) detectionOutputIndex = static_cast<int>(index);
                if (outputShape.size() == 4 && prototypeOutputIndex < 0 && outputShape.size() > 1 && outputShape[1] > 0) {
                    prototypeOutputIndex = static_cast<int>(index);
                    maskChannels = static_cast<int>(outputShape[1]);
                }
                outputShapes.push_back(std::move(outputShape));
            }
            if (detectionOutputIndex < 0 && prototypeOutputIndex >= 0) {
                semanticOutputIndex = prototypeOutputIndex;
                prototypeOutputIndex = -1;
                maskChannels = 0;
            }
            const auto& shape = selectedInputShape;
            const auto inputType = selectedInputType;
            std::wstring shapeDescription = L"[";
            for (std::size_t index = 0; index < shape.size(); ++index) {
                if (index) shapeDescription += L", ";
                shapeDescription += shape[index] > 0 ? std::to_wstring(shape[index]) : L"dynamic";
            }
            shapeDescription += L"]";
            int modelClassCount = -1;
            bool modelEndToEnd = false;
            bool modelNormalizedCoordinates = false;
            std::vector<std::wstring> modelLabels;
            try {
                auto names = session->GetModelMetadata().LookupCustomMetadataMapAllocated("names", allocator);
                if (names && names.get()) {
                    const std::string text(names.get());
                    const std::regex dictionaryKey(R"((^|[\{,])\s*['\"]?[0-9]+['\"]?\s*:)");
                    modelClassCount = static_cast<int>(std::distance(
                        std::sregex_iterator(text.begin(), text.end(), dictionaryKey), std::sregex_iterator()));
                    if (modelClassCount <= 0 && text.size() >= 2 && text.front() == '[' && text.back() == ']') {
                        modelClassCount = text == "[]" ? 0 : 1 + static_cast<int>(std::count(text.begin(), text.end(), ','));
                    }
                    const std::regex dictionaryValue(R"(['\"]?([0-9]+)['\"]?\s*:\s*['\"]([^'\"]+)['\"])");
                    for (std::sregex_iterator match(text.begin(), text.end(), dictionaryValue), end; match != end; ++match) {
                        const int classId = std::stoi((*match)[1].str());
                        if (classId < 0 || classId >= 4096) continue;
                        if (static_cast<std::size_t>(classId) >= modelLabels.size()) modelLabels.resize(static_cast<std::size_t>(classId) + 1);
                        modelLabels[static_cast<std::size_t>(classId)] = narrowToWide((*match)[2].str());
                    }
                }
            } catch (const std::exception&) {
                // Class metadata is optional. Shape/runtime validation remains authoritative.
            }
            try {
                auto endToEnd = session->GetModelMetadata().LookupCustomMetadataMapAllocated("end2end", allocator);
                if (endToEnd && endToEnd.get()) {
                    std::string value(endToEnd.get());
                    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char character) {
                        return static_cast<char>(std::tolower(character));
                    });
                    modelEndToEnd = value == "true" || value == "1" || value == "yes";
                }
            } catch (const std::exception&) {
                // End-to-end metadata is optional; the value probe remains available.
            }
            const bool nchw = shape.size() == 4 && shape[1] == 3;
            try {
                auto units = session->GetModelMetadata().LookupCustomMetadataMapAllocated("coordinate_units", allocator);
                if (units && units.get()) modelNormalizedCoordinates = std::string(units.get()) == "normalized";
            } catch (const std::exception&) {
                // Standard YOLO exports use pixels when no unit metadata exists.
            }
            const bool nhwc = shape.size() == 4 && shape[3] == 3;
            if (!nchw && !nhwc) {
                error = L"This detector needs one four-dimensional NCHW or NHWC, three-channel image input; found shape "
                        + shapeDescription + L".";
                return false;
            }
            if (inputType != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT && inputType != ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8) {
                error = L"This detector supports float or uint8 image inputs. This model uses an unsupported input type.";
                return false;
            }
            const int inputHeight = static_cast<int>((nchw ? shape[2] : shape[1]) > 0 ? (nchw ? shape[2] : shape[1]) : 640);
            const int inputWidth = static_cast<int>((nchw ? shape[3] : shape[2]) > 0 ? (nchw ? shape[3] : shape[2]) : 640);
            if (inputWidth <= 0 || inputHeight <= 0) {
                error = L"The model input dimensions could not be resolved.";
                return false;
            }

            m_inputName = std::move(selectedInputName);
            m_outputNames = std::move(outputNames);
            m_inputHeight = inputHeight;
            m_inputWidth = inputWidth;
            m_inputNhwc = nhwc;
            m_inputShape = nhwc ? std::array<int64_t, 4>{1, m_inputHeight, m_inputWidth, 3}
                                : std::array<int64_t, 4>{1, 3, m_inputHeight, m_inputWidth};
            m_inputType = inputType;
            m_modelClassCount = modelClassCount;
            m_modelEndToEnd = modelEndToEnd;
            m_modelNormalizedCoordinates = modelNormalizedCoordinates;
            m_modelLabels = std::move(modelLabels);
            m_outputShapes = std::move(outputShapes);
            m_detectionOutputIndex = detectionOutputIndex;
            m_prototypeOutputIndex = prototypeOutputIndex;
            m_semanticOutputIndex = semanticOutputIndex;
            m_maskChannels = maskChannels;
#if VAPORLOCK_FORCE_TENSORRT_RTX
            if (requestedProvider == InferenceProvider::TensorRtRtx) {
                if (inputType != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
                    error = L"TensorRT RTX currently requires a float image-input model.";
                    return false;
                }
                auto tensorRt = std::make_unique<vaporlock::tensorrt_rtx::Runtime>();
                if (!tensorRt->load(std::filesystem::path(path), m_inputName, m_outputNames, m_inputShape, error,m_inferenceDevice)) return false;
                m_tensorRt = std::move(tensorRt);
            }
#endif
#if VAPORLOCK_HAS_CUDA_PREPROCESS
            m_cudaPreprocessEnabled = (requestedProvider == InferenceProvider::Cuda || requestedProvider == InferenceProvider::TensorRtRtx)
                && cudaPreprocess && !m_inputNhwc && inputType == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT;
            if (m_cudaPreprocessEnabled) providerName += L" + GPU preprocess";
#if !VAPORLOCK_FORCE_DML
            else if ((requestedProvider == InferenceProvider::Cuda || requestedProvider == InferenceProvider::TensorRtRtx) && cudaPreprocess)
                providerName += L" + CPU preprocess (GPU preprocessing requires float NCHW input)";
#endif
#elif VAPORLOCK_HAS_CUDA && !VAPORLOCK_FORCE_DML
            if (requestedProvider == InferenceProvider::Cuda && cudaPreprocess)
                providerName += L" + CPU preprocess (GPU preprocessing not included in this engine)";
#endif
            m_runtime = std::make_unique<vaporlock::inference::Runtime>(
                std::move(environment), std::move(session), m_inputName,
                m_outputNames, m_inputShape, m_inputType,&m_cpuPlacement);
#if VAPORLOCK_FORCE_TENSORRT_RTX
            if (m_tensorRt) {
                m_runtime->retireMetadataSession();
                m_deviceName = m_tensorRt->deviceName();
#if VAPORLOCK_HAS_CUDA_PREPROCESS
                if (!m_cudaPreprocessEnabled && !cudaPreprocess) providerName += L" + CPU preprocess (GPU preprocessing disabled)";
#else
                providerName += L" + CPU preprocess (GPU preprocessing not included in this engine)";
#endif
            }
#endif
            m_modelPath = path;
            m_providerName = providerName;
#if VAPORLOCK_FORCE_DML
            m_deviceName = dmlAdapter.name;
#endif
            return true;
        } catch (const std::exception& exception) {
            error = L"ONNX Runtime could not open this model: " + narrowToWide(exception.what());
            return false;
        }
#else
        error = L"ONNX Runtime was not found when Vapor Lock was built.";
        return false;
#endif
    }

    DetectionBatch<Detection> detectBatch(int width, int height, const std::vector<unsigned char>& bgra,
                                         const AppConfig& config, CaptureFrameIdentity identity) {
        DetectionBatch<Detection> batch;
        batch.capture.identity = identity;
        m_feedObserved = false;
        m_cloudIdentity = identity;
        batch.targeting = detect(width, height, bgra, config, nullptr);
        if (config.datasetCaptureConfidenceEnabled) {
            if(m_cloud && m_feedObserved) {
                batch.capture.observed=true;
                const auto score=m_cloud->last.maximum;
                batch.capture.qualifies=classAllowed(config.classFilter,0)&&score>=config.datasetMinConfidence;
                return batch;
            }
            // These are the exact current decoded detections, before tracking.
            // Testing every valid score is equivalent to testing their maximum.
            batch.capture.observeFeed(batch.targeting, config.datasetMinConfidence, m_feedObserved);
        }
        return batch;
    }

    std::vector<Detection> detect(int width, int height, const std::vector<unsigned char>& bgra, const AppConfig& config,
                                  CaptureQualification* captureQualification = nullptr) {
        if (captureQualification) captureQualification->observed = captureQualification->qualifies = false;
        if(m_cloud) {
            auto result=m_cloud->detect(width,height,bgra,config,m_cloudIdentity);
            m_preprocessMs=m_cloud->last.preprocess;m_modelMs=m_cloud->last.model;m_postprocessMs=m_cloud->last.postprocess;
            m_feedObserved=true;return result;
        }
#if VAPORLOCK_HAS_ONNXRUNTIME
        if (m_runtime && !bgra.empty()) {
            return detectOnnx(width, height, bgra, config, captureQualification);
        }
#else
        (void)bgra;
#endif
        m_preprocessMs = 0.0;
        m_modelMs = 0.0;
        m_postprocessMs = 0.0;
        return simulate(width, height, config);
    }

    std::wstring status() const {
        if(m_cloud)return L"Model ready: "+std::filesystem::path(m_modelPath).filename().wstring()+L" ("+m_providerName+L"; native pipeline; .cloud worker)";
#if VAPORLOCK_HAS_ONNXRUNTIME
        if (m_runtime) {
            std::wstring shape;
            const auto& primaryOutputShape = m_detectionOutputIndex >= 0
                ? m_outputShapes[static_cast<std::size_t>(m_detectionOutputIndex)]
                : m_outputShapes.empty() ? std::vector<int64_t>{} : m_outputShapes.front();
            for (const auto dimension : primaryOutputShape) {
                if (!shape.empty()) shape += L" x ";
                shape += dimension > 0 ? std::to_wstring(dimension) : L"dynamic";
            }
            const std::wstring classes = m_modelClassCount > 0 ? L"; class metadata " + std::to_wstring(m_modelClassCount) : L"; no class-count metadata";
            std::wstring layout = m_prototypeOutputIndex >= 0 ? L"; instance segmentation" : m_semanticOutputIndex >= 0 ? L"; semantic segmentation" : L"";
            if (m_modelEndToEnd) layout += L"; end-to-end xyxy output";
            return L"Model ready: " + std::filesystem::path(m_modelPath).filename().wstring() + L" (" + m_providerName
                   + L"; input " + std::to_wstring(m_inputWidth) + L" x " + std::to_wstring(m_inputHeight)
                   + (m_inputNhwc ? L" NHWC" : L" NCHW")
                   + (shape.empty() ? L"; non-tensor output" : L"; output " + shape) + classes + layout + L")";
        }
#endif
        return L"No model loaded - test signal active";
    }

    double preprocessMilliseconds() const { return m_preprocessMs; }
    double modelMilliseconds() const { return m_modelMs; }
    double postprocessMilliseconds() const { return m_postprocessMs; }
    const std::wstring& deviceName() const noexcept { return m_deviceName; }
    int deviceIndex() const noexcept { return m_inferenceDevice; }
    int cpuPlacementCount() const noexcept {
#if VAPORLOCK_HAS_ONNXRUNTIME
        return static_cast<int>(m_cpuPlacement.ids.size());
#else
        return 0;
#endif
    }

private:
    std::vector<Detection> simulate(int width, int height, const AppConfig& config) {
        const double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - m_started).count();
        std::vector<Detection> detections;
        Detection primary;
        primary.box = {(std::sin(t * 0.85) * 0.34 + 0.5) * width - width * 0.045, (std::cos(t * 0.60) * 0.27 + 0.5) * height - height * 0.065, width * 0.09, height * 0.13};
        primary.confidence = 0.86;
        primary.classId = 0;
        primary.label = L"test-object";
        if (primary.confidence >= config.confidence && classAllowed(config.classFilter, 0)) detections.push_back(primary);
        Detection secondary;
        secondary.box = {width * 0.15, height * 0.18, width * 0.07, height * 0.10};
        secondary.confidence = 0.61;
        secondary.classId = 1;
        secondary.label = L"reference";
        if (secondary.confidence >= config.confidence && classAllowed(config.classFilter, 1)) detections.push_back(secondary);
        return detections;
    }

#if VAPORLOCK_HAS_ONNXRUNTIME
    std::vector<Detection> detectOnnx(int width, int height, const std::vector<unsigned char>& bgra, const AppConfig& config,
                                      CaptureQualification* captureQualification) {
#if VAPORLOCK_HAS_CUDA_PREPROCESS
        if(m_usingCudaDevice && !vaporlock::cuda::activateDevice(m_inferenceDevice))
            throw std::runtime_error("The selected CUDA GPU is no longer available.");
#endif
        if (width <= 0 || height <= 0 || bgra.size() != static_cast<std::size_t>(width) * height * 4U) {
            throw std::runtime_error("Desktop capture returned an invalid frame.");
        }
        const auto started = std::chrono::steady_clock::now();
        const Transform transform = makeTransform(width, height, config.letterbox);
#if VAPORLOCK_HAS_CUDA_PREPROCESS
        const bool useCudaPreprocess = m_cudaPreprocessEnabled;
        if (useCudaPreprocess && (!vaporlock::cuda::ensureBuffers(m_cudaBgra, m_cudaBgraCapacity, m_cudaChw, m_cudaChwCapacity, width, height, m_inputWidth, m_inputHeight) ||
                                  !vaporlock::cuda::preprocessBgra(bgra.data(), m_cudaBgra, m_cudaChw, width, height, m_inputWidth, m_inputHeight, config.letterbox))) {
            throw std::runtime_error("CUDA preprocessing failed. Disable CUDA preprocessing to use the CPU image-preprocessing path.");
        }
#else
        const bool useCudaPreprocess = false;
#endif
        if (!useCudaPreprocess) preprocess(width, height, bgra, transform);
        const auto afterPreprocess = std::chrono::steady_clock::now();
        const std::vector<Ort::Value>* outputValues = nullptr;
#if VAPORLOCK_FORCE_TENSORRT_RTX
        std::vector<vaporlock::tensorrt_rtx::Output> tensorRtOutputs;
        std::vector<Ort::Value> tensorRtViews;
        if (m_tensorRt) {
            std::wstring tensorRtError;
            float* tensorRtInput = nullptr;
            std::size_t tensorRtCapacity = 0;
#if VAPORLOCK_HAS_CUDA_PREPROCESS
            if (useCudaPreprocess) { tensorRtInput = m_cudaChw; tensorRtCapacity = m_cudaChwCapacity; }
#endif
            if (!m_tensorRt->run(m_runtime->floats(), tensorRtOutputs, tensorRtError, tensorRtInput, tensorRtCapacity))
                throw std::runtime_error(wideToUtf8(tensorRtError));
            auto memory = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
            tensorRtViews.reserve(tensorRtOutputs.size());
            for (auto& output : tensorRtOutputs)
                tensorRtViews.emplace_back(Ort::Value::CreateTensor<float>(memory, output.values.data(), output.values.size(),
                                                                         output.shape.data(), output.shape.size()));
            outputValues = &tensorRtViews;
        } else
#endif
        if (useCudaPreprocess) {
#if VAPORLOCK_HAS_CUDA_PREPROCESS
            outputValues = &m_runtime->run(m_cudaChw, m_cudaChwCapacity,m_inferenceDevice);
#endif
        } else outputValues = &m_runtime->run();
        if (!outputValues) throw std::runtime_error("Inference did not produce a completed output set.");
        const auto& outputs = *outputValues;
        const auto afterModel = std::chrono::steady_clock::now();
        m_preprocessMs = std::chrono::duration<double, std::milli>(afterPreprocess - started).count();
        m_modelMs = std::chrono::duration<double, std::milli>(afterModel - afterPreprocess).count();
        const auto finish = [&](std::vector<Detection> result) {
            m_postprocessMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - afterModel).count();
            return result;
        };
        if (m_detectionOutputIndex < 0) {
            if (m_semanticOutputIndex >= 0 && static_cast<std::size_t>(m_semanticOutputIndex) < outputs.size()) {
                return finish(decodeSemanticMasks(outputs[static_cast<std::size_t>(m_semanticOutputIndex)], transform, width, height, config, captureQualification));
            }
            return finish({});
        }
        if (static_cast<std::size_t>(m_detectionOutputIndex) >= outputs.size()
            || !outputs[static_cast<std::size_t>(m_detectionOutputIndex)].IsTensor()) return finish({});
        const auto& detectionOutput = outputs[static_cast<std::size_t>(m_detectionOutputIndex)];
        const auto information = detectionOutput.GetTensorTypeAndShapeInfo();
        if (information.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) return finish({});
        const auto outputShape = information.GetShape();
        if (outputShape.size() != 2 && outputShape.size() != 3) return finish({});
        const int64_t first = outputShape.size() == 3 ? outputShape[1] : outputShape[0];
        const int64_t second = outputShape.size() == 3 ? outputShape[2] : outputShape[1];
        const int maskChannels = m_prototypeOutputIndex >= 0 ? m_maskChannels : 0;
        const bool transposed = modelOutputTransposed(first, second, m_modelClassCount, maskChannels, config.modelOutputFormat, m_modelEndToEnd);
        const int rows = static_cast<int>(transposed ? second : first);
        const int columns = static_cast<int>(transposed ? first : second);
        if (rows < 0 || columns < 5) return finish({});
        const float* values = detectionOutput.GetTensorData<float>();
        const bool feedObserved = config.datasetCaptureConfidenceEnabled
            && captureTensorElements(outputShape, information.GetElementCount(), outputShape.size() == 3)
            && std::all_of(values, values + information.GetElementCount(), [](float value) { return std::isfinite(value); });
        auto at = [&](int row, int column) { return modelOutputValue(values, rows, columns, transposed, row, column); };
        ModelOutputLayout layout = resolveModelOutputLayout(columns, m_modelClassCount, config.modelOutputFormat);
        if (config.modelOutputFormat == 0 && maskChannels > 0 && m_modelClassCount > 0) {
            if (columns == m_modelClassCount + maskChannels + 4) layout = {false, false};
            else if (columns == m_modelClassCount + maskChannels + 5) layout = {false, true};
        }
        if (config.modelOutputFormat == 0 && columns == 6 + maskChannels
            && (m_modelEndToEnd || looksLikeEndToEndOutput(values, rows, columns, transposed, m_modelClassCount))) {
            layout = {true, false};
        }
        const bool endToEnd = layout.endToEnd;
        const bool objectness = layout.objectness;
        if (endToEnd && columns < 6) return finish({});
        if (rows == 0) {
            m_feedObserved = information.GetElementCount() == 0 && columns <= 8197
                && (outputShape.size() == 2 || outputShape[0] == 1);
            return finish({});
        }
        m_feedObserved = feedObserved && rows <= 1048576 && columns <= 8197;
        const int activeMaskChannels = maskChannels;
        const bool normalizedCoordinates = config.modelCoordinateUnits == 2
            || (config.modelCoordinateUnits == 0 && m_modelNormalizedCoordinates);
        const auto captureRows = transposed ? second : first;
        const auto captureColumns = transposed ? first : second;
        const std::int64_t inferredCaptureClasses = (captureColumns > 0 && captureColumns <= 8197
            && activeMaskChannels >= 0 && activeMaskChannels <= 4096)
                ? captureColumns - (objectness ? 5 : 4) - activeMaskChannels : 0;
        const std::int64_t captureClassCount = endToEnd ? 4096
            : (m_modelClassCount > 0 ? std::min<std::int64_t>(m_modelClassCount, inferredCaptureClasses) : inferredCaptureClasses);
        if (captureQualification) {
            std::size_t sourceBytes = 0;
            // Postprocessed/end-to-end rows may already suppress candidates at
            // an internal cutoff. Do not claim an independent lower cutoff.
            captureQualification->observed = (!endToEnd && captureRows > 0 && captureRows <= 1048576
                && captureColumns > 0 && captureColumns <= 8197 && captureClassCount > 0 && captureClassCount <= 4096
                && activeMaskChannels >= 0 && activeMaskChannels <= 4096
                && captureTensorElements(outputShape, information.GetElementCount(), outputShape.size() == 3)
                && captureBgraBytes(width, height, bgra.size(), sourceBytes));
        }
        struct DecodedCandidate {
            float x1 = 0.0F, y1 = 0.0F, x2 = 0.0F, y2 = 0.0F, score = 0.0F;
            int classId = 0;
            std::vector<float> maskCoefficients;
        };
        std::vector<DecodedCandidate> decoded;
        decoded.reserve(static_cast<std::size_t>(rows));
        for (int row = 0; row < rows; ++row) {
            float x1, y1, x2, y2, score;
            int classId = 0;
            int maskStart = 0;
            bool captureClassFactsValid = true;
            std::vector<float> maskCoefficients;
            if (endToEnd) {
                const float rawClassId = at(row, 5);
                if (!std::isfinite(rawClassId) || rawClassId < 0.0F || rawClassId >= 4096.0F
                    || std::abs(rawClassId - std::round(rawClassId)) > 0.001F) continue;
                x1 = at(row, 0); y1 = at(row, 1); x2 = at(row, 2); y2 = at(row, 3); score = at(row, 4); classId = static_cast<int>(std::lround(rawClassId));
                if (columns < 6 + activeMaskChannels) throw std::runtime_error("End-to-end segmentation output is missing mask coefficients.");
                maskStart = 6;
            } else {
                const float centerX = at(row, 0), centerY = at(row, 1), boxWidth = at(row, 2), boxHeight = at(row, 3);
                const int classStart = objectness ? 5 : 4;
                const int inferredClassCount = columns - classStart - activeMaskChannels;
                const int classCount = m_modelClassCount > 0 ? std::min(m_modelClassCount, inferredClassCount) : inferredClassCount;
                if (classCount <= 0) continue;
                float classScore = -1.0F;
                for (int column = classStart; column < classStart + classCount; ++column) {
                    if (at(row, column) > classScore) { classScore = at(row, column); classId = column - classStart; }
                }
                score = objectness ? at(row, 4) * classScore : classScore;
                captureClassFactsValid = std::isfinite(classScore) && classScore >= 0.0F && classScore <= 1.0F
                    && (!objectness || (std::isfinite(at(row, 4)) && at(row, 4) >= 0.0F && at(row, 4) <= 1.0F));
                x1 = centerX - boxWidth * 0.5F; y1 = centerY - boxHeight * 0.5F;
                x2 = centerX + boxWidth * 0.5F; y2 = centerY + boxHeight * 0.5F;
                maskStart = classStart + classCount;
            }
            // Same inference's raw candidate, before the independent targeting
            // cutoff/NMS. No low-confidence Detection/mask/annotation allocation.
            if (captureQualification && captureQualification->observed && captureClassFactsValid
                && std::isfinite(x1) && std::isfinite(y1) && std::isfinite(x2) && std::isfinite(y2)) {
                float captureX1 = x1, captureY1 = y1, captureX2 = x2, captureY2 = y2;
                if (normalizedCoordinates) {
                    captureX1 *= m_inputWidth; captureX2 *= m_inputWidth;
                    captureY1 *= m_inputHeight; captureY2 *= m_inputHeight;
                }
                captureX1 = std::clamp((captureX1 - transform.padX) / transform.scaleX, 0.0F, static_cast<float>(width));
                captureX2 = std::clamp((captureX2 - transform.padX) / transform.scaleX, 0.0F, static_cast<float>(width));
                captureY1 = std::clamp((captureY1 - transform.padY) / transform.scaleY, 0.0F, static_cast<float>(height));
                captureY2 = std::clamp((captureY2 - transform.padY) / transform.scaleY, 0.0F, static_cast<float>(height));
                captureQualification->observe(score, config.datasetMinConfidence,
                    classId >= 0 && classAllowed(config.classFilter, classId), captureX1, captureY1, captureX2, captureY2);
            }
            if (!std::isfinite(x1) || !std::isfinite(y1) || !std::isfinite(x2) || !std::isfinite(y2)
                || !std::isfinite(score) || score < config.confidence || score > 1.0001F
                || classId < 0 || !classAllowed(config.classFilter, classId)) continue;
            if (activeMaskChannels > 0) {
                maskCoefficients.reserve(static_cast<std::size_t>(activeMaskChannels));
                for (int channel = 0; channel < activeMaskChannels; ++channel) maskCoefficients.push_back(at(row, maskStart + channel));
            }
            decoded.push_back({x1, y1, x2, y2, score, classId, std::move(maskCoefficients)});
        }
        std::vector<Detection> candidates;
        candidates.reserve(decoded.size());
        for (const auto& raw : decoded) {
            float x1 = raw.x1, y1 = raw.y1, x2 = raw.x2, y2 = raw.y2;
            if (normalizedCoordinates) {
                x1 *= m_inputWidth; x2 *= m_inputWidth; y1 *= m_inputHeight; y2 *= m_inputHeight;
            }
            x1 = std::clamp((x1 - transform.padX) / transform.scaleX, 0.0F, static_cast<float>(width));
            x2 = std::clamp((x2 - transform.padX) / transform.scaleX, 0.0F, static_cast<float>(width));
            y1 = std::clamp((y1 - transform.padY) / transform.scaleY, 0.0F, static_cast<float>(height));
            y2 = std::clamp((y2 - transform.padY) / transform.scaleY, 0.0F, static_cast<float>(height));
            if (x2 <= x1 || y2 <= y1) continue;
            Detection detection;
            detection.box = {x1, y1, x2 - x1, y2 - y1};
            detection.confidence = raw.score;
            detection.classId = raw.classId;
            // Coefficients survive NMS so a retained instance can be decoded
            // against the prototype tensor after duplicate removal.
            detection.segmentationMask.clear();
            detection.segmentationCoefficients = raw.maskCoefficients;
            detection.segmentationScaleX = transform.scaleX;
            detection.segmentationScaleY = transform.scaleY;
            detection.segmentationPadX = transform.padX;
            detection.segmentationPadY = transform.padY;
            detection.segmentationInputWidth = m_inputWidth;
            detection.segmentationInputHeight = m_inputHeight;
            detection.label = raw.classId >= 0 && static_cast<std::size_t>(raw.classId) < m_modelLabels.size()
                              && !m_modelLabels[static_cast<std::size_t>(raw.classId)].empty()
                                  ? m_modelLabels[static_cast<std::size_t>(raw.classId)]
                                  : L"class-" + std::to_wstring(raw.classId);
            candidates.push_back(detection);
        }
        std::sort(candidates.begin(), candidates.end(), [](const Detection& left, const Detection& right) { return left.confidence > right.confidence; });
        std::vector<Detection> kept;
        for (const auto& candidate : candidates) {
            const bool duplicate = config.nmsEnabled && !endToEnd && std::any_of(kept.begin(), kept.end(), [&](const Detection& accepted) {
                return (config.nmsClassAgnostic || accepted.classId == candidate.classId)
                       && boxIou(accepted.box, candidate.box) >= config.iou;
            });
            if (!duplicate) kept.push_back(candidate);
            if (kept.size() >= static_cast<std::size_t>(config.maxDetections)) break;
        }
        if (config.previewEnabled && config.overlayEnabled && config.overlaySegmentationMasks && m_prototypeOutputIndex >= 0
            && static_cast<std::size_t>(m_prototypeOutputIndex) < outputs.size()) {
            decodeInstanceMasks(kept, outputs[static_cast<std::size_t>(m_prototypeOutputIndex)], config);
        }
        return finish(std::move(kept));
    }

    std::vector<Detection> decodeSemanticMasks(const Ort::Value& output, const Transform& transform, int sourceWidth, int sourceHeight, const AppConfig& config,
                                               CaptureQualification* captureQualification) {
        if (!output.IsTensor()) return {};
        const auto info = output.GetTensorTypeAndShapeInfo();
        if (info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) return {};
        const auto shape = info.GetShape();
        if (shape.size() != 4 || shape[0] <= 0 || shape[1] <= 1 || shape[2] <= 0 || shape[3] <= 0) return {};
        const int classes = static_cast<int>(shape[1]);
        const int maskHeight = static_cast<int>(shape[2]);
        const int maskWidth = static_cast<int>(shape[3]);
        const std::size_t plane = static_cast<std::size_t>(maskWidth) * maskHeight;
        if (captureQualification) {
            std::size_t sourceBytes = 0;
            captureQualification->observed = (classes <= 4096 && shape[1] <= 4096
                && shape[2] <= 16384 && shape[3] <= 16384 && plane <= 16777216
                && captureTensorElements(shape, info.GetElementCount())
                && captureBgraBytes(sourceWidth, sourceHeight, static_cast<std::size_t>(sourceWidth) * sourceHeight * 4U, sourceBytes));
        }
        const float* logits = output.GetTensorData<float>();
        m_feedObserved = config.datasetCaptureConfidenceEnabled && classes <= 4096
            && shape[2] <= 16384 && shape[3] <= 16384 && plane <= 16777216
            && captureTensorElements(shape, info.GetElementCount())
            && std::all_of(logits, logits + info.GetElementCount(), [](float value) { return std::isfinite(value); });
        std::vector<int> labels(plane, 0);
        std::vector<float> confidence(plane, 0.0F);
        for (std::size_t pixel = 0; pixel < plane; ++pixel) {
            int bestClass = 0;
            float best = logits[pixel];
            float total = 0.0F;
            for (int classId = 0; classId < classes; ++classId) {
                const float value = logits[static_cast<std::size_t>(classId) * plane + pixel];
                if (captureQualification && !std::isfinite(value)) captureQualification->observed = false;
                if (value > best) { best = value; bestClass = classId; }
            }
            for (int classId = 0; classId < classes; ++classId) total += std::exp(std::clamp(logits[static_cast<std::size_t>(classId) * plane + pixel] - best, -60.0F, 0.0F));
            labels[pixel] = bestClass;
            confidence[pixel] = 1.0F / std::max(1.0F, total);
        }
        std::vector<std::uint8_t> visited(plane, 0);
        std::vector<Detection> detections;
        const int minimumPixels = std::max(4, static_cast<int>(plane / 20000));
        std::vector<std::size_t> queue;
        for (std::size_t start = 0; start < plane; ++start) {
            if (visited[start] || labels[start] == 0) continue;
            const int classId = labels[start];
            queue.clear();
            queue.push_back(start);
            visited[start] = 1;
            int minX = static_cast<int>(start % maskWidth), maxX = minX, minY = static_cast<int>(start / maskWidth), maxY = minY;
            double score = 0.0;
            for (std::size_t cursor = 0; cursor < queue.size(); ++cursor) {
                const std::size_t pixel = queue[cursor];
                score += confidence[pixel];
                const int x = static_cast<int>(pixel % maskWidth), y = static_cast<int>(pixel / maskWidth);
                minX = std::min(minX, x); maxX = std::max(maxX, x); minY = std::min(minY, y); maxY = std::max(maxY, y);
                const std::array<std::pair<int, int>, 4> neighbours{{{x - 1, y}, {x + 1, y}, {x, y - 1}, {x, y + 1}}};
                for (const auto [nextX, nextY] : neighbours) {
                    if (nextX < 0 || nextY < 0 || nextX >= maskWidth || nextY >= maskHeight) continue;
                    const std::size_t next = static_cast<std::size_t>(nextY) * maskWidth + nextX;
                    if (!visited[next] && labels[next] == classId) { visited[next] = 1; queue.push_back(next); }
                }
            }
            const double average = score / std::max<std::size_t>(1, queue.size());
            auto sourceX = [&](int x) { return std::clamp((x * static_cast<float>(m_inputWidth) / maskWidth - transform.padX) / transform.scaleX, 0.0F, static_cast<float>(sourceWidth)); };
            auto sourceY = [&](int y) { return std::clamp((y * static_cast<float>(m_inputHeight) / maskHeight - transform.padY) / transform.scaleY, 0.0F, static_cast<float>(sourceHeight)); };
            const float x1 = sourceX(minX), y1 = sourceY(minY), x2 = sourceX(maxX + 1), y2 = sourceY(maxY + 1);
            if (captureQualification && queue.size() >= static_cast<std::size_t>(minimumPixels)) {
                captureQualification->observe(average, config.datasetMinConfidence,
                    classAllowed(config.classFilter, classId), x1, y1, x2, y2);
            }
            // After the original first-max target set, bounded continuation
            // visits capture-only components without Detection/mask allocation.
            if (detections.size() >= static_cast<std::size_t>(config.maxDetections)) {
                if (!captureQualification || !captureQualification->observed || captureQualification->qualifies) break;
                continue;
            }
            if (static_cast<int>(queue.size()) < minimumPixels || average < config.confidence || !classAllowed(config.classFilter, classId)) continue;
            if (x2 <= x1 || y2 <= y1) continue;
            Detection detection;
            detection.box = {x1, y1, x2 - x1, y2 - y1};
            detection.confidence = average;
            detection.classId = classId;
            detection.label = static_cast<std::size_t>(classId) < m_modelLabels.size() && !m_modelLabels[static_cast<std::size_t>(classId)].empty()
                                  ? m_modelLabels[static_cast<std::size_t>(classId)] : L"class-" + std::to_wstring(classId);
            detection.segmentationMask.assign(plane, 0);
            for (const auto pixel : queue) detection.segmentationMask[pixel] = 1;
            detection.segmentationMaskWidth = maskWidth;
            detection.segmentationMaskHeight = maskHeight;
            detection.segmentationScaleX = transform.scaleX;
            detection.segmentationScaleY = transform.scaleY;
            detection.segmentationPadX = transform.padX;
            detection.segmentationPadY = transform.padY;
            detection.segmentationInputWidth = m_inputWidth;
            detection.segmentationInputHeight = m_inputHeight;
            detections.push_back(std::move(detection));
            if (detections.size() >= static_cast<std::size_t>(config.maxDetections)
                && (!captureQualification || !captureQualification->observed || captureQualification->qualifies)) break;
        }
        return detections;
    }

    void decodeInstanceMasks(std::vector<Detection>& detections, const Ort::Value& prototypeOutput, const AppConfig& config) const {
        if (!prototypeOutput.IsTensor() || config.segmentationMaskLimit <= 0) return;
        const auto info = prototypeOutput.GetTensorTypeAndShapeInfo();
        if (info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) return;
        const auto shape = info.GetShape();
        if (shape.size() != 4 || shape[0] <= 0 || shape[1] <= 0 || shape[2] <= 0 || shape[3] <= 0) return;
        const int channels = static_cast<int>(shape[1]);
        const int maskHeight = static_cast<int>(shape[2]);
        const int maskWidth = static_cast<int>(shape[3]);
        const float* prototypes = prototypeOutput.GetTensorData<float>();
        const std::size_t plane = static_cast<std::size_t>(maskWidth) * maskHeight;
        int decoded = 0;
        for (auto& detection : detections) {
            if (decoded >= config.segmentationMaskLimit || static_cast<int>(detection.segmentationCoefficients.size()) != channels) continue;
            detection.segmentationMask.assign(plane, 0);
            detection.segmentationMaskWidth = maskWidth;
            detection.segmentationMaskHeight = maskHeight;
            const float inputLeft = static_cast<float>(detection.box.x) * detection.segmentationScaleX + detection.segmentationPadX;
            const float inputTop = static_cast<float>(detection.box.y) * detection.segmentationScaleY + detection.segmentationPadY;
            const float inputRight = static_cast<float>(detection.box.x + detection.box.w) * detection.segmentationScaleX + detection.segmentationPadX;
            const float inputBottom = static_cast<float>(detection.box.y + detection.box.h) * detection.segmentationScaleY + detection.segmentationPadY;
            for (std::size_t pixel = 0; pixel < plane; ++pixel) {
                const int maskX = static_cast<int>(pixel % static_cast<std::size_t>(maskWidth));
                const int maskY = static_cast<int>(pixel / static_cast<std::size_t>(maskWidth));
                const float inputX = (static_cast<float>(maskX) + 0.5F) * detection.segmentationInputWidth / maskWidth;
                const float inputY = (static_cast<float>(maskY) + 0.5F) * detection.segmentationInputHeight / maskHeight;
                if (inputX < inputLeft || inputX > inputRight || inputY < inputTop || inputY > inputBottom) continue;
                double logit = 0.0;
                for (int channel = 0; channel < channels; ++channel) {
                    logit += static_cast<double>(detection.segmentationCoefficients[static_cast<std::size_t>(channel)])
                             * prototypes[static_cast<std::size_t>(channel) * plane + pixel];
                }
                const double probability = 1.0 / (1.0 + std::exp(-std::clamp(logit, -60.0, 60.0)));
                detection.segmentationMask[pixel] = probability >= config.segmentationMaskThreshold ? 1 : 0;
            }
            detection.segmentationCoefficients.clear();
            ++decoded;
        }
    }

    Transform makeTransform(int width, int height, bool letterbox) const {
        Transform transform;
        if (letterbox) {
            const float scale = std::min(static_cast<float>(m_inputWidth) / width, static_cast<float>(m_inputHeight) / height);
            transform.scaleX = scale;
            transform.scaleY = scale;
            transform.padX = std::floor((m_inputWidth - width * scale) * 0.5F);
            transform.padY = std::floor((m_inputHeight - height * scale) * 0.5F);
        } else {
            transform.scaleX = static_cast<float>(m_inputWidth) / width;
            transform.scaleY = static_cast<float>(m_inputHeight) / height;
        }
        return transform;
    }

    void preprocess(int width, int height, const std::vector<unsigned char>& bgra, const Transform& transform) {
        if (vaporlock::image::identityGeometry(width, height, m_inputWidth, m_inputHeight,
                transform.scaleX, transform.scaleY, transform.padX, transform.padY)) {
            if (m_inputType == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)
                vaporlock::image::copyBgraToRgb<float>(bgra, m_runtime->floats(), m_inputNhwc);
            else
                vaporlock::image::copyBgraToRgb<std::uint8_t>(bgra, m_runtime->bytes(), m_inputNhwc);
            return;
        }
        if (m_inputType == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)
            m_resize.run<float>(bgra, m_runtime->floats(), width, height, m_inputWidth, m_inputHeight,
                transform.scaleX, transform.scaleY, transform.padX, transform.padY, m_inputNhwc);
        else
            m_resize.run<std::uint8_t>(bgra, m_runtime->bytes(), width, height, m_inputWidth, m_inputHeight,
                transform.scaleX, transform.scaleY, transform.padX, transform.padY, m_inputNhwc);
    }

    vaporlock::image::BgraTensorResize m_resize;
    vaporlock::inference::CpuPlacement m_cpuPlacement;
    std::unique_ptr<vaporlock::inference::Runtime> m_runtime;
    std::string m_inputName;
    std::vector<std::string> m_outputNames;
    int m_inputWidth = 640, m_inputHeight = 640;
    ONNXTensorElementDataType m_inputType = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    std::array<int64_t, 4> m_inputShape{};
    bool m_inputNhwc = false;
    int m_modelClassCount = -1;
    bool m_modelEndToEnd = false;
    bool m_modelNormalizedCoordinates = false;
    std::vector<std::wstring> m_modelLabels;
    std::vector<std::vector<int64_t>> m_outputShapes;
    int m_detectionOutputIndex = -1;
    int m_prototypeOutputIndex = -1;
    int m_semanticOutputIndex = -1;
    int m_maskChannels = 0;

#if VAPORLOCK_FORCE_TENSORRT_RTX
    std::unique_ptr<vaporlock::tensorrt_rtx::Runtime> m_tensorRt;
#endif
#if VAPORLOCK_HAS_CUDA_PREPROCESS
    bool m_cudaPreprocessEnabled = false;
    std::uint8_t* m_cudaBgra = nullptr;
    float* m_cudaChw = nullptr;
    std::size_t m_cudaBgraCapacity = 0;
    std::size_t m_cudaChwCapacity = 0;
#endif
#endif
    std::wstring m_modelPath;
    std::unique_ptr<cloud_native::Detector> m_cloud;
    CaptureFrameIdentity m_cloudIdentity{};
    std::wstring m_providerName;
    std::wstring m_deviceName;
    int m_inferenceDevice = -1;
    bool m_usingCudaDevice = false;
    double m_preprocessMs = 0.0;
    double m_modelMs = 0.0;
    bool m_feedObserved = false;
    double m_postprocessMs = 0.0;
    std::chrono::steady_clock::time_point m_started = std::chrono::steady_clock::now();
};

class IoUTracker {
public:
    std::vector<Track> update(const std::vector<Detection>& input, const AppConfig& config, AimTime now = AimClock::now()) {
        if (now <= m_lastObservation) return snapshot();
        m_lastObservation = now;
        std::vector<Detection> detections;
        for (const auto& detection : input) {
            const auto& b = detection.box;
            if (std::isfinite(detection.confidence) && detection.confidence >= config.confidence
                && std::isfinite(b.x) && std::isfinite(b.y) && std::isfinite(b.w) && std::isfinite(b.h)
                && b.w > 0 && b.h > 0) detections.push_back(detection);
        }
        std::vector<std::tuple<double, int, int>> candidates;
        for (const auto& [id, track] : m_tracks) {
            const double elapsed = std::chrono::duration<double>(now - track.lastUpdate).count();
            if (elapsed * 1000 > legacyDurationMs(config.maxMissed, config.trackRetentionMs)) continue;
            RectF predicted = track.observedBox;
            const double horizon = std::min(elapsed, config.predictionLimitMs / 1000.0);
            predicted.x += track.vx * horizon;
            predicted.y += track.vy * horizon;
            const double diagonal = std::max(1.0, std::hypot(predicted.w, predicted.h));
            for (int index = 0; index < static_cast<int>(detections.size()); ++index) {
                if (track.detection.classId != detections[index].classId) continue;
                const auto& box = detections[index].box;
                const double sizeRatio = std::max(box.area(), predicted.area()) / std::max(1.0, std::min(box.area(), predicted.area()));
                if (sizeRatio > 2.5) continue;
                const double displacement = distanceTo(predicted.cx(), predicted.cy(), box.cx(), box.cy());
                const double overlap = boxIou(predicted, box);
                const double gate = diagonal * 0.6 + std::min(diagonal, config.predictionMaxSpeed * elapsed);
                if (overlap < config.matchIou && displacement > gate) continue;
                const double cost = 0.65 * (1.0 - overlap) + displacement / diagonal + 0.1 * (sizeRatio - 1.0);
                candidates.emplace_back(cost, id, index);
            }
        }
        std::sort(candidates.begin(), candidates.end());
        std::vector<int> usedTracks, usedDetections;
        std::vector<bool> ambiguousDetections(detections.size(), false);
        for (const auto& [cost, id, index] : candidates) {
            if (std::find(usedTracks.begin(), usedTracks.end(), id) != usedTracks.end() || std::find(usedDetections.begin(), usedDetections.end(), index) != usedDetections.end()) continue;
            // A near tie is insufficient identity evidence, particularly at a
            // crossing. Keep both identities available instead of transferring one.
            bool ambiguous = false;
            for (const auto& other : candidates) {
                const auto [otherCost, otherId, otherIndex] = other;
                if (std::find(usedTracks.begin(), usedTracks.end(), otherId) != usedTracks.end()
                    || std::find(usedDetections.begin(), usedDetections.end(), otherIndex) != usedDetections.end()) continue;
                if (((otherId == id && otherIndex != index) || (otherIndex == index && otherId != id))
                    && std::abs(otherCost - cost) < 0.08) {
                    ambiguous = true;
                    ambiguousDetections[index] = ambiguousDetections[otherIndex] = true;
                }
            }
            if (ambiguous) { m_tracks[id].identityCertain = false; continue; }
            auto& track = m_tracks[id];
            const RectF previousBox = track.detection.box;
            Detection updated = detections[index];
            const double elapsed = std::clamp(std::chrono::duration<double>(now - track.lastUpdate).count(), 0.0001, 2.0);
            const bool reacquired = track.missed > 0 || !track.identityCertain;
            const double retained = reacquired ? 0.0 : timeRetention(std::clamp(config.boxSmoothing, 0.0, 0.95), elapsed);
            updated.box.x = previousBox.x * retained + updated.box.x * (1.0 - retained);
            updated.box.y = previousBox.y * retained + updated.box.y * (1.0 - retained);
            updated.box.w = previousBox.w * retained + updated.box.w * (1.0 - retained);
            updated.box.h = previousBox.h * retained + updated.box.h * (1.0 - retained);
            const double dx = detections[index].box.cx() - track.observedBox.cx();
            const double dy = detections[index].box.cy() - track.observedBox.cy();
            if (reacquired) track.observations.clear();
            track.observations.emplace_back(now, detections[index].box.cx(), detections[index].box.cy());
            while (track.observations.size() > 2 && now - std::get<0>(track.observations[1]) >= std::chrono::milliseconds(50))
                track.observations.pop_front();
            const auto [motionTime, originX, originY] = track.observations.front();
            const double motionDt = std::max(0.0001, std::chrono::duration<double>(now - motionTime).count());
            const double motionX = detections[index].box.cx() - originX;
            const double motionY = detections[index].box.cy() - originY;
            const bool noise = std::hypot(motionX, motionY) <= config.predictionNoiseFloor || std::hypot(dx, dy) < 0.0001;
            const double rawVx = noise ? 0.0 : std::clamp(motionX / motionDt, -config.predictionMaxSpeed, config.predictionMaxSpeed);
            const double rawVy = noise ? 0.0 : std::clamp(motionY / motionDt, -config.predictionMaxSpeed, config.predictionMaxSpeed);
            const double previousVx = track.vx;
            const double previousVy = track.vy;
            const bool reversed = dx * previousVx + dy * previousVy < 0.0;
            const double motionRetained = timeRetention(config.smoothing, elapsed);
            if (reacquired || noise || reversed) {
                track.vx = track.vy = 0.0;
                track.ax = track.ay = 0.0;
                track.motionSamples = 0;
                track.motionEvidenceSeconds = 0.0;
                if (reversed) {
                    track.observations.clear();
                    track.observations.emplace_back(now, detections[index].box.cx(), detections[index].box.cy());
                }
            } else {
                track.vx = previousVx * motionRetained + rawVx * (1.0 - motionRetained);
                track.vy = previousVy * motionRetained + rawVy * (1.0 - motionRetained);
                const double rawAx = (track.vx - previousVx) / elapsed;
                const double rawAy = (track.vy - previousVy) / elapsed;
                track.ax = std::clamp(track.ax * motionRetained + rawAx * (1.0 - motionRetained), -config.predictionMaxAcceleration, config.predictionMaxAcceleration);
                track.ay = std::clamp(track.ay * motionRetained + rawAy * (1.0 - motionRetained), -config.predictionMaxAcceleration, config.predictionMaxAcceleration);
                ++track.motionSamples;
                track.motionEvidenceSeconds += elapsed;
            }
            track.motionConfidence = std::clamp(track.motionEvidenceSeconds / 0.05, 0.0, 1.0);
            track.detection = std::move(updated);
            track.observedBox = detections[index].box;
            if (reacquired) track.hasStableAnchor = false;
            updateStableAnchor(track, config, elapsed);
            track.hasObservation = true;
            track.identityCertain = true;
            ++track.age;
            track.missed = 0;
            track.lastUpdate = now;
            usedTracks.push_back(id);
            usedDetections.push_back(index);
        }
        std::vector<int> expired;
        for (auto& [id, track] : m_tracks) {
            if (std::find(usedTracks.begin(), usedTracks.end(), id) == usedTracks.end()) {
                ++track.missed;
                if (config.predictLost) {
                    const double elapsed = std::clamp(std::chrono::duration<double>(now - track.lastUpdate).count(), 0.0, config.predictionLimitMs / 1000.0);
                    track.detection.box = track.observedBox;
                    track.detection.box.x += std::clamp(track.vx * elapsed, -config.predictionMaxDistance, config.predictionMaxDistance);
                    track.detection.box.y += std::clamp(track.vy * elapsed, -config.predictionMaxDistance, config.predictionMaxDistance);
                }
            }
            if (track.missed > 0 && std::chrono::duration<double, std::milli>(now - track.lastUpdate).count()
                > legacyDurationMs(config.maxMissed, config.trackRetentionMs)) expired.push_back(id);
        }
        for (int id : expired) m_tracks.erase(id);
        for (int index = 0; index < static_cast<int>(detections.size()); ++index) {
            if (std::find(usedDetections.begin(), usedDetections.end(), index) != usedDetections.end()) continue;
            // An unresolved association must not regain authority by becoming a
            // new, certain identity in the very same observation.
            if (ambiguousDetections[index]) continue;
            Track track;
            track.id = m_nextId++;
            track.detection = detections[index];
            track.observedBox = track.detection.box;
            track.hasObservation = true;
            updateStableAnchor(track, config);
            track.lastUpdate = now;
            m_tracks.emplace(track.id, track);
            m_tracks.at(track.id).observations.emplace_back(now, track.observedBox.cx(), track.observedBox.cy());
        }
        return snapshot();
    }

private:
    std::vector<Track> snapshot() const {
        std::vector<Track> tracks;
        for (const auto& [id, track] : m_tracks) { (void)id; tracks.push_back(track); }
        std::sort(tracks.begin(), tracks.end(), [](const Track& left, const Track& right) { return left.id < right.id; });
        return tracks;
    }

    std::map<int, Track> m_tracks;
    int m_nextId = 1;
    AimTime m_lastObservation{};
};

double medianValue(std::vector<double> values) {
    if (values.empty()) return 0.0;
    const std::size_t middle = values.size() / 2;
    std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(middle), values.end());
    const double upper = values[middle];
    if (values.size() % 2 != 0) return upper;
    std::nth_element(values.begin(), values.begin() + static_cast<std::ptrdiff_t>(middle - 1), values.end());
    return (upper + values[middle - 1]) * 0.5;
}

// Finds motion that is shared by several visible boxes. Object motion tends to
// disagree; image/camera jitter tends to agree. The result is already the
// inverse correction in capture-coordinate pixels per second.
class MotionCompensationState {
public:
    std::pair<double, double> update(const std::vector<Track>& tracks, const AppConfig& config, AimTime now = AimClock::now()) {
        const double elapsed = m_lastUpdate == AimTime{} ? 1.0 / kLegacyResponseHz
            : std::max(0.0, std::chrono::duration<double>(now - m_lastUpdate).count());
        m_lastUpdate = now;
        m_retained = timeRetention(config.motionCompensationAutoSmoothing, elapsed);
        contributors = 0;
        automaticValid = false;
        // Keep the estimator available for calibration even when correction
        // output itself is disabled. It only contributes to movement after
        // the separate compensation activation gate has approved output.
        if (config.motionCompensationMode != 1 && config.motionCompensationMode != 2) {
            automaticX = automaticY = 0.0;
            return {0.0, 0.0};
        }

        const int required = std::max(2, config.motionCompensationAutoMinTracks);
        std::vector<const Track*> candidates;
        candidates.reserve(tracks.size());
        for (const auto& track : tracks) {
            if (track.missed != 0 || track.age < required || track.detection.confidence < config.confidence
                || !track.identityCertain || !track.hasObservation || now < track.lastUpdate
                || now - track.lastUpdate > std::chrono::milliseconds(config.outputMaxObservationAgeMs)
                || !std::isfinite(track.vx) || !std::isfinite(track.vy)) continue;
            candidates.push_back(&track);
        }
        if (static_cast<int>(candidates.size()) < required) return decay(config);

        std::vector<double> xs, ys;
        xs.reserve(candidates.size());
        ys.reserve(candidates.size());
        for (const Track* track : candidates) {
            xs.push_back(track->vx);
            ys.push_back(track->vy);
        }
        const double medianX = medianValue(xs);
        const double medianY = medianValue(ys);
        const double tolerance = std::max(0.001, config.motionCompensationAutoConsensus);
        xs.clear();
        ys.clear();
        for (const Track* track : candidates) {
            if (std::hypot(track->vx - medianX, track->vy - medianY) <= tolerance) {
                xs.push_back(track->vx);
                ys.push_back(track->vy);
            }
        }
        if (static_cast<int>(xs.size()) < required) return decay(config);

        const double limit = std::max(0.001, config.motionCompensationAutoMaxRate);
        const double desiredX = std::clamp(-medianValue(xs), -limit, limit);
        const double desiredY = std::clamp(-medianValue(ys), -limit, limit);
        const double retained = m_retained;
        if (!hasEstimate) {
            automaticX = desiredX;
            automaticY = desiredY;
            hasEstimate = true;
        } else {
            automaticX = automaticX * retained + desiredX * (1.0 - retained);
            automaticY = automaticY * retained + desiredY * (1.0 - retained);
        }
        contributors = static_cast<int>(xs.size());
        automaticValid = true;
        return {automaticX, automaticY};
    }

    int contributors = 0;
    bool automaticValid = false;
    double automaticHorizontalCorrection() const { return automaticX; }
    double automaticVerticalCorrection() const { return automaticY; }

private:
    std::pair<double, double> decay(const AppConfig& config) {
        (void)config;
        const double retained = m_retained;
        automaticX *= retained;
        automaticY *= retained;
        if (std::abs(automaticX) < 0.001) automaticX = 0.0;
        if (std::abs(automaticY) < 0.001) automaticY = 0.0;
        return {automaticX, automaticY};
    }

    double automaticX = 0.0;
    double automaticY = 0.0;
    bool hasEstimate = false;
    AimTime m_lastUpdate{};
    double m_retained = 0;
};

std::pair<double, double> predictedTargetAnchor(const Track& track, const AppConfig& config, AimTime now = AimClock::now()) {
    auto [x, y] = targetAnchor(track, config);
    if (!config.predictLost || !track.identityCertain || track.missed != 0 || track.motionConfidence <= 0.0) return {x, y};
    const double age = std::max(0.0, std::chrono::duration<double>(now - track.lastUpdate).count());
    const double seconds = std::clamp(age + config.predictionLeadMs / 1000.0, 0.0, config.predictionLimitMs / 1000.0);
    const double certainty = std::clamp(track.motionConfidence, 0.0, 1.0);
    double dx = (std::clamp(track.vx, -config.predictionMaxSpeed, config.predictionMaxSpeed) * seconds
        + 0.5 * std::clamp(track.ax, -config.predictionMaxAcceleration, config.predictionMaxAcceleration) * seconds * seconds) * certainty;
    double dy = (std::clamp(track.vy, -config.predictionMaxSpeed, config.predictionMaxSpeed) * seconds
        + 0.5 * std::clamp(track.ay, -config.predictionMaxAcceleration, config.predictionMaxAcceleration) * seconds * seconds) * certainty;
    const double magnitude = std::hypot(dx, dy);
    if (!std::isfinite(magnitude)) return {x, y};
    if (magnitude > config.predictionMaxDistance && magnitude > 0.0) {
        dx *= config.predictionMaxDistance / magnitude;
        dy *= config.predictionMaxDistance / magnitude;
    }
    return {x + dx, y + dy};
}

double retentionConfidence(const AppConfig& config) {
    return std::max(config.confidence, config.retainConfidence);
}

double acquisitionConfidence(const AppConfig& config) {
    return std::max(retentionConfidence(config), config.acquireConfidence);
}

bool targetObservationFresh(const Track& selected, const AppConfig& config, AimTime now = AimClock::now()) {
    if (!selected.hasObservation || !selected.identityCertain || selected.missed != 0
        || !std::isfinite(selected.detection.confidence) || selected.detection.confidence < config.confidence
        || selected.lastUpdate == AimTime{} || selected.lastUpdate > now) return false;
    const auto [x, y] = targetAnchor(selected, config);
    return std::isfinite(x) && std::isfinite(y)
           && now - selected.lastUpdate <= std::chrono::milliseconds(std::max(1, config.outputMaxObservationAgeMs));
}

bool targetEligibleForOutput(const Track& selected, const AppConfig& config, AimTime now = AimClock::now(), int width=1000000, int height=1000000) {
    return config.frameReference.usable(now) && targetObservationFresh(selected, config, now)
        && selected.detection.confidence >= retentionConfidence(config)
        && !targetExcluded(selected, config,width,height);
}

bool targetEligibleForOutput(const std::optional<Track>& selected, const AppConfig& config, AimTime now = AimClock::now(), int width=1000000, int height=1000000) {
    return selected && targetEligibleForOutput(*selected, config, now,width,height);
}

bool targetInsideSnapRadius(const std::optional<Track>& selected, int width, int height, const AppConfig& config) {
    if (!targetEligibleForOutput(selected, config,AimClock::now(),width,height) || config.snapRadius <= 0 || width <= 0 || height <= 0) return false;
    const auto [anchorX, anchorY] = targetAnchor(*selected, config);
    return anchorDistanceFromFrameCenter(anchorX, anchorY, width, height, config) <= config.snapRadius;
}

bool targetAnchorInsideDeadzone(const Track& selected, int width, int height, const AppConfig& config) {
    if (width <= 0 || height <= 0) return true;
    const auto [anchorX, anchorY] = settledOutputAnchor(selected, width, height, config);
    // Output precision remains a literal circular pixel radius. Selection FOV
    // shape is intentionally independent so changing it never changes where
    // relative movement settles.
    const auto [x, y] = aimingReference(width, height, config);
    return distanceTo(anchorX, anchorY, x, y) <= config.deadzone;
}

LONG quantizeRelativeStep(double requested, double& remainder, int maximumStep) {
    if (!std::isfinite(requested)) {
        remainder = 0.0;
        return 0;
    }
    if (requested == 0.0) {
        remainder = 0.0;
        return 0;
    }
    // Do not let fractional error from the opposite direction delay a new
    // correction. Within one direction, retain the fraction so a 0 px dead
    // zone can still converge instead of repeatedly rounding tiny movement to
    // zero.
    if ((requested > 0.0 && remainder < 0.0) || (requested < 0.0 && remainder > 0.0)) remainder = 0.0;
    const double limit = static_cast<double>(std::max(1, maximumStep));
    if (std::abs(requested) > limit) {
        remainder = 0;
        return static_cast<LONG>(std::copysign(limit, requested));
    }
    // Clamp the new request, not its already-authorized sub-unit remainder.
    // For example, repeated 0.8 requests with a one-unit cap must average 0.8.
    const double accumulated = requested + remainder;
    const LONG step = static_cast<LONG>(std::clamp(std::trunc(accumulated), -limit, limit));
    remainder = accumulated - static_cast<double>(step);
    return step;
}

LONG quantizeBlendedStep(double tracking, double compensation, double& trackingRemainder,
                        double& compensationRemainder, int maximumStep) {
    if (!std::isfinite(tracking) || !std::isfinite(compensation)) {
        trackingRemainder = compensationRemainder = 0;
        return 0;
    }
    const double requested = tracking + compensation;
    const double limit = static_cast<double>(std::max(1, maximumStep));
    if (!std::isfinite(requested) || std::abs(requested) > limit) {
        trackingRemainder = compensationRemainder = 0;
        return std::isfinite(requested) ? static_cast<LONG>(std::copysign(limit, requested)) : 0;
    }
    const auto integrate = [](double requested, double remainder) {
        if (requested == 0) return 0.0;
        if ((requested > 0 && remainder < 0) || (requested < 0 && remainder > 0)) remainder = 0;
        return requested + remainder;
    };
    tracking = integrate(tracking, trackingRemainder);
    compensation = integrate(compensation, compensationRemainder);
    const double total = tracking + compensation;
    trackingRemainder = compensationRemainder = 0;
    if (!std::isfinite(total)) return 0;
    const LONG step = static_cast<LONG>(std::clamp(std::trunc(total), -limit, limit));
    if (total == 0) return step;
    const double remainder = total - step;
    if (std::abs(remainder) >= 1) return step; // Bound cancellation roundoff for extreme inputs.
    if ((tracking > 0 && compensation > 0) || (tracking < 0 && compensation < 0)) {
        // Both sources contribute to one quantized report. Preserve their share
        // of the sub-unit remainder so cancelling either cannot erase the other.
        trackingRemainder = remainder * (tracking / total);
        compensationRemainder = remainder - trackingRemainder;
    } else if (std::abs(tracking) >= std::abs(compensation)) {
        // Opposing requests cancel before quantization; only the net source
        // retains a remainder. Neither side accumulates hidden opposing debt.
        trackingRemainder = remainder;
    } else compensationRemainder = remainder;
    return step;
}

// SendInput exposes no ownership for a pressed mouse button. Track physical
// button transitions separately so the snap action never releases a button a
// person is currently holding.
class PhysicalMouseMonitor {
public:
    PhysicalMouseMonitor() = default;
    PhysicalMouseMonitor(const PhysicalMouseMonitor&) = delete;
    PhysicalMouseMonitor& operator=(const PhysicalMouseMonitor&) = delete;

    ~PhysicalMouseMonitor() { stop(); }

    bool start() {
        if (m_started) return m_available;
        m_started = true;
        for (int button = 0; button < 3; ++button) {
            m_down[button] = (GetAsyncKeyState(virtualKeyFor(button)) & 0x8000) != 0;
        }

        std::promise<bool> ready;
        auto result = ready.get_future();
        m_thread = std::thread([this, ready = std::move(ready)]() mutable {
            MSG message{};
            PeekMessageW(&message, nullptr, 0, 0, PM_NOREMOVE);
            PhysicalMouseMonitor* expected = nullptr;
            if (!s_instance.compare_exchange_strong(expected, this)) { ready.set_value(false); return; }
            m_hook = SetWindowsHookExW(WH_MOUSE_LL, hookProc, GetModuleHandleW(nullptr), 0);
            m_available = m_hook != nullptr;
            ready.set_value(m_available);
            while (m_available && !m_stop.load()) {
                while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                    TranslateMessage(&message); DispatchMessageW(&message);
                }
                MsgWaitForMultipleObjects(0, nullptr, FALSE, 2, QS_ALLINPUT);
            }
            if (m_hook) UnhookWindowsHookEx(m_hook);
            m_hook = nullptr;
            s_instance = nullptr;
            m_available = false;
        });
        return result.get();
    }

    void pump() {
        // Physical transitions are serviced independently of inference/output.
    }

    [[nodiscard]] bool available() const { return m_available; }
    [[nodiscard]] bool physicalButtonDown(int button) const { return m_down[indexFor(button)]; }
    [[nodiscard]] std::uint64_t pressEpoch(int button) const { return m_pressEpoch[indexFor(button)]; }

private:
    static int indexFor(int button) {
        return std::clamp(button, 0, 2);
    }

    static int virtualKeyFor(int button) {
        switch (button) {
        case 1: return VK_RBUTTON;
        case 2: return VK_MBUTTON;
        default: return VK_LBUTTON;
        }
    }

    static int buttonForMessage(WPARAM message) {
        switch (message) {
        case WM_LBUTTONDOWN:
        case WM_LBUTTONUP: return 0;
        case WM_RBUTTONDOWN:
        case WM_RBUTTONUP: return 1;
        case WM_MBUTTONDOWN:
        case WM_MBUTTONUP: return 2;
        default: return -1;
        }
    }

    static bool isDownMessage(WPARAM message) {
        return message == WM_LBUTTONDOWN || message == WM_RBUTTONDOWN || message == WM_MBUTTONDOWN;
    }

    static LRESULT CALLBACK hookProc(int code, WPARAM message, LPARAM data) {
        auto* instance = s_instance.load();
        if (code == HC_ACTION && instance != nullptr) {
            const auto* event = reinterpret_cast<const MSLLHOOKSTRUCT*>(data);
            const int button = buttonForMessage(message);
            if (event != nullptr && button >= 0 && (event->flags & LLMHF_INJECTED) == 0) {
                instance->observePhysicalButton(button, isDownMessage(message));
            }
        }
        return CallNextHookEx(nullptr, code, message, data);
    }

    void observePhysicalButton(int button, bool down) {
        const int index = indexFor(button);
        if (down && !m_down[index]) {
            m_down[index] = true;
            ++m_pressEpoch[index];
        } else if (!down) {
            m_down[index] = false;
        }
    }

    void stop() {
        m_stop = true;
        if (m_thread.joinable()) m_thread.join();
    }

    inline static std::atomic<PhysicalMouseMonitor*> s_instance{nullptr};
    HHOOK m_hook = nullptr;
    std::array<std::atomic<bool>, 3> m_down{};
    std::array<std::atomic<std::uint64_t>, 3> m_pressEpoch{};
    std::thread m_thread;
    std::atomic<bool> m_stop{false};
    bool m_started = false;
    std::atomic<bool> m_available{false};
};

class MouseSnapEventAction {
public:
    struct UpdateResult {
        bool sent = false;
        bool failed = false;
        bool yieldedToPhysical = false;
        bool monitorUnavailable = false;
    };

    explicit MouseSnapEventAction(PhysicalMouseMonitor& monitor) : m_monitor(monitor) {}

    ~MouseSnapEventAction() {
        UpdateResult ignored;
        releaseSafely(ignored);
    }

    UpdateResult update(const AppConfig& config, bool eventActionAuthorized, bool targetInsideSnap, std::chrono::steady_clock::time_point now) {
        UpdateResult result;
        const bool actionEnabled = config.eventActionEnabled && eventActionAuthorized;
        if (actionEnabled && !m_monitor.start()) {
            result.monitorUnavailable = true;
            return result;
        }
        m_monitor.pump();
        if (!m_monitor.available()) return result;
        if (m_held && (m_monitor.pressEpoch(m_heldButton) != m_pressEpochAtAcquire
                       || m_monitor.physicalButtonDown(m_heldButton))) {
            m_yieldedToPhysical = true;
        }

        if (!actionEnabled || !targetInsideSnap) {
            releaseSafely(result);
            if (!m_held) {
                m_yieldedToPhysical = false;
                m_yieldedUntilExit = false;
            }
            return result;
        }

        // Once a person uses the same button, finish any already-injected
        // press only after their physical press has ended. Do not re-arm until
        // the target leaves the snap radius and enters again.
        if (m_held && m_yieldedToPhysical) {
            result.yieldedToPhysical = true;
            releaseSafely(result);
            if (!m_held) m_yieldedUntilExit = true;
            return result;
        }

        // Changing the selected mouse button live must never leave the old
        // button held. Releasing is attempted before the new button is used.
        if (m_held && (m_heldButton != config.eventActionButton || config.eventActionMode != 1)) {
            releaseSafely(result);
            if (m_held) return result;
        }
        if (m_yieldedUntilExit) {
            result.yieldedToPhysical = true;
            return result;
        }
        if (m_monitor.physicalButtonDown(config.eventActionButton)) {
            m_yieldedUntilExit = true;
            result.yieldedToPhysical = true;
            return result;
        }
        if (m_held) return result;

        if (config.eventActionMode == 1) {
            beginPress(config.eventActionButton, result);
            return result;
        }

        const int cooldown = std::max(0, config.eventActionCooldownMs);
        if (m_lastClick.time_since_epoch().count() != 0
            && std::chrono::duration_cast<std::chrono::milliseconds>(now - m_lastClick).count() < cooldown) {
            return result;
        }
        m_lastClick = now;
        if (!beginPress(config.eventActionButton, result)) return result;

        // A physical press that arrives with an automated click gets priority
        // as well. Its release is deferred until the physical hold is over.
        m_monitor.pump();
        if (m_monitor.pressEpoch(m_heldButton) != m_pressEpochAtAcquire
            || m_monitor.physicalButtonDown(m_heldButton)) {
            m_yieldedToPhysical = true;
            result.yieldedToPhysical = true;
            releaseSafely(result);
            if (!m_held) m_yieldedUntilExit = true;
            return result;
        }
        if (send(config.eventActionButton, false)) {
            m_held = false;
            ++m_sent;
            result.sent = true;
            return result;
        }
        ++m_failed;
        result.failed = true;
        return result;
    }

    void releaseSafely(UpdateResult& result) {
        if (!m_held || !m_monitor.available()) return;
        m_monitor.pump();
        if (m_monitor.physicalButtonDown(m_heldButton)) {
            m_yieldedToPhysical = true;
            result.yieldedToPhysical = true;
            return;
        }
        if (send(m_heldButton, false)) {
            m_held = false;
            ++m_sent;
            result.sent = true;
        } else {
            ++m_failed;
            result.failed = true;
        }
    }

    [[nodiscard]] bool held() const { return m_held; }
    [[nodiscard]] std::uint64_t sentTotal() const { return m_sent; }
    [[nodiscard]] std::uint64_t failedTotal() const { return m_failed; }

private:
    bool beginPress(int button, UpdateResult& result) {
        if (!send(button, true)) {
            ++m_failed;
            result.failed = true;
            return false;
        }
        m_held = true;
        m_heldButton = button;
        m_pressEpochAtAcquire = m_monitor.pressEpoch(button);
        ++m_sent;
        result.sent = true;
        return true;
    }

    static DWORD flagFor(int button, bool down) {
        switch (button) {
        case 1: return down ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_RIGHTUP;
        case 2: return down ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_MIDDLEUP;
        default: return down ? MOUSEEVENTF_LEFTDOWN : MOUSEEVENTF_LEFTUP;
        }
    }

    static bool send(int button, bool down) {
        INPUT input{};
        input.type = INPUT_MOUSE;
        input.mi.dwFlags = flagFor(button, down);
        return SendInput(1, &input, sizeof(input)) == 1;
    }

    PhysicalMouseMonitor& m_monitor;
    bool m_held = false;
    int m_heldButton = 0;
    std::uint64_t m_pressEpochAtAcquire = 0;
    bool m_yieldedToPhysical = false;
    bool m_yieldedUntilExit = false;
    std::chrono::steady_clock::time_point m_lastClick{};
    std::uint64_t m_sent = 0;
    std::uint64_t m_failed = 0;
};

struct TargetingVisualSnapshot {
    CaptureFrameIdentity identity{};
    AimTime sampledAt{};
    int trackId = 0;
    bool eligible = false;
    double x = 0, y = 0;
};

std::string overlayMetadata(int width, int height, const std::vector<Track>& tracks, const std::optional<Track>& selected,
                         bool manualLock, const AppConfig& config, int captureLeft, int captureTop, AimTime observedAt,
                         CaptureFrameIdentity identity = {}, TargetingVisualSnapshot guide = {}, bool sourceBoundary = false) {
    // Pair original capture age with a shared Windows clock so pipe/UI delays
    // cannot turn an old observation into a fresh overlay frame.
    const auto emittedTick = GetTickCount64();
    const auto now = AimClock::now();
    std::ostringstream payload;
    payload << std::fixed << std::setprecision(3)
            << "{\"type\":\"overlay\",\"width\":" << width
            << ",\"height\":" << height
            << ",\"schema\":2,\"session\":" << identity.session
            << ",\"source_generation\":" << identity.sourceGeneration
            << ",\"source_boundary\":" << (sourceBoundary ? "true" : "false")
            << ",\"frame_id\":" << identity.frameId
            << ",\"config_revision\":" << identity.configRevision
            << ",\"control_epoch\":" << config.visualEpoch
            << ",\"emitted_tick_ms\":" << emittedTick
            << ",\"observation_age_ms\":";
    if (observedAt != AimTime{} && observedAt <= now)
        payload << std::chrono::duration<double, std::milli>(now - observedAt).count();
    else
        payload << "null";
    payload
            << ",\"capture_left\":" << captureLeft
            << ",\"capture_top\":" << captureTop
            << ",\"reference_x\":" << aimingReference(width, height, config).first
            << ",\"reference_y\":" << aimingReference(width, height, config).second
            << ",\"reference_inside\":" << (!sourceBoundary && config.frameReference.inside ? "true" : "false")
            << ",\"crosshair_detected\":" << (config.frameReference.detected ? "true" : "false")
            << ",\"crosshair_age_ms\":" << (config.frameReference.detected ? std::chrono::duration<double,std::milli>(now-config.frameReference.matched).count() : 0.)
            << ",\"selected_track\":" << (selected ? selected->id : 0)
            << ",\"manual_lock\":" << (manualLock ? "true" : "false")
            << ",\"tracks\":[";
    bool first = true;
    for (const auto& track : tracks) {
        if (!first) payload << ',';
        first = false;
        const auto [anchorX, anchorY] = targetAnchor(track, config);
        const bool isSelected = selected && selected->id == track.id;
        const bool eligible = targetEligibleForOutput(track, config, now,width,height);
        const bool guideCurrent = isSelected && guide.eligible && guide.trackId == track.id
            && guide.identity == identity && guide.sampledAt != AimTime{} && guide.sampledAt <= now;
        payload << "{\"id\":" << track.id
                << ",\"class_id\":" << track.detection.classId
                << ",\"has_observation\":" << (track.hasObservation ? "true" : "false")
                << ",\"observed_x\":" << track.observedBox.x
                << ",\"observed_y\":" << track.observedBox.y
                << ",\"observed_w\":" << track.observedBox.w
                << ",\"observed_h\":" << track.observedBox.h
                << ",\"label\":\"" << wideToUtf8(jsonEscape(track.detection.label)) << "\""
                << ",\"confidence\":" << track.detection.confidence
                << ",\"x\":" << track.detection.box.x
                << ",\"y\":" << track.detection.box.y
                << ",\"w\":" << track.detection.box.w
                << ",\"h\":" << track.detection.box.h
                << ",\"missed\":" << track.missed
                << ",\"selected\":" << (isSelected ? "true" : "false")
                << ",\"eligible\":" << (eligible ? "true" : "false")
                << ",\"observation_age_ms\":";
        if (track.hasObservation && track.lastUpdate != AimTime{} && track.lastUpdate <= now)
            payload << std::chrono::duration<double, std::milli>(now - track.lastUpdate).count();
        else payload << "null";
        payload
                << ",\"anchor_x\":" << anchorX
                << ",\"anchor_y\":" << anchorY
                << ",\"guide_valid\":" << (guideCurrent ? "true" : "false")
                << ",\"predicted_x\":" << (guideCurrent ? guide.x : anchorX)
                << ",\"predicted_y\":" << (guideCurrent ? guide.y : anchorY)
                << '}';
    }
    payload << "]}";
    return payload.str();
}

class TargetSelector {
    struct RetainedLock {
        int trackId = 0, classId = -1;
        double anchorX = 0, anchorY = 0;
        RectF box{};
        AimTime observedAt{};
    };
public:
    bool toggleManualLock() {
        if (!m_locked) return false;
        m_manualLocked = !m_manualLocked;
        return true;
    }
    [[nodiscard]] bool manuallyLocked() const { return m_manualLocked; }

    std::optional<Track> select(std::vector<Track>& tracks, int width, int height, const AppConfig& config,
                                AimTime now = AimClock::now()) {
        std::vector<Track*> visible;
        Track* current = nullptr;
        for (auto& track : tracks) {
            track.selected = false;
            // Keep weaker fresh observations as identity-ambiguity evidence.
            // Acquisition thresholds gate selection, not the evidence set.
            if (targetObservationFresh(track, config, now)) visible.push_back(&track);
            if (track.id == m_locked) current = &track;
        }
        // Crossing an exclusion boundary ends lock ownership immediately, even
        // for manual locks; lost-target grace must not revive the excluded ID.
        if (current && targetExcluded(*current, config,width,height)) {
            m_locked = 0; m_manualLocked = false; m_retained.reset();
            m_nearWaitStarted.reset(); current = nullptr;
        }
        const auto distance = [&](const Track& track) {
            const auto [x, y] = targetAnchor(track, config);
            return anchorDistanceFromFrameCenter(x, y, width, height, config);
        };
        const auto band = [&](const Track& track) {
            const double d = distance(track);
            return d <= std::max(0, config.snapRadius) ? 0 : d <= std::max(config.snapRadius, config.nearRadius) ? 1 : 2;
        };
        const auto confirmed = [&](const Track& track) {
            return track.age >= config.minimumTrackAge || (config.snapBypassesConfirmation && band(track) == 0);
        };
        const auto score = [&](const Track& track) {
            switch (config.strategy) {
            case 1: return 1.0 - track.detection.confidence;
            case 2: return -track.detection.box.area();
            case 3: return track.detection.box.area();
            case 4: return std::hypot(track.vx, track.vy);
            default: return distance(track);
            }
        };
        const auto preferred = [&](const Track& a, const Track& b) {
            const bool watchedA = classListed(config.watchlist, a.detection.classId);
            const bool watchedB = classListed(config.watchlist, b.detection.classId);
            return watchedA != watchedB ? watchedA : classPriority(a, config) > classPriority(b, config);
        };
        const auto better = [&](const Track& a, const Track& b) {
            if (preferred(a, b)) return true;
            if (preferred(b, a)) return false;
            return band(a) != band(b) ? band(a) < band(b) : score(a) < score(b);
        };
        const auto mark = [&](Track* track) -> std::optional<Track> {
            track->selected = true;
            m_locked = track->id;
            m_nearWaitStarted.reset();
            if (track->missed == 0 && track->identityCertain && track->hasObservation) {
                const auto [x, y] = targetAnchor(*track, config);
                m_retained = RetainedLock{track->id, track->detection.classId, x, y,
                                         track->observedBox, track->lastUpdate};
            }
            return *track;
        };
        const bool currentVisible = current && targetEligibleForOutput(*current, config, now,width,height);
        if (currentVisible && distance(*current) <= config.retentionRadius) {
            if (m_manualLocked || now - m_lockedSince < std::chrono::milliseconds(config.lockPersistenceMs)) {
                m_challengerId = 0; return mark(current);
            }
            Track* challenger = nullptr;
            for (auto* candidate : visible) {
                if (targetExcluded(*candidate,config,width,height) || candidate->id == current->id || candidate->detection.confidence < acquisitionConfidence(config)
                    || !confirmed(*candidate) || distance(*candidate) > config.acquisitionRadius) continue;
                if (!challenger || better(*candidate, *challenger)) challenger = candidate;
            }
            if (!challenger || !materiallyBetter(*challenger, *current, config, distance, band)) {
                m_challengerId = 0; return mark(current);
            }
            // A one-frame score/zone fluctuation must not steal a valid lock.
            // Explicit class/watchlist priority still takes effect immediately.
            // Evidence uses distinct capture time, so repeated selector calls or
            // a slow delivery cannot turn a single picture into confirmation.
            if (!preferred(*challenger, *current)) {
                if (m_challengerId != challenger->id) {
                    m_challengerId = challenger->id;
                    m_challengerSince = challenger->lastUpdate;
                    return mark(current);
                }
                if (challenger->lastUpdate <= m_challengerSince
                    || challenger->lastUpdate - m_challengerSince < std::chrono::milliseconds(50)) return mark(current);
            }
            m_challengerId = 0;
            m_lockedSince = now;
            return mark(challenger);
        }
        m_challengerId = 0;
        // Memory may reserve a lock, but missing/uncertain tracks cannot move.
        const double holdMs = legacyDurationMs(config.lostTargetTimeoutFrames, config.lostTargetTimeoutMs);
        const double graceMs = std::min(holdMs, legacyDurationMs(config.occlusionGraceFrames, config.occlusionGraceMs));
        if (m_retained && !currentVisible && now >= m_retained->observedAt
            && std::chrono::duration<double, std::milli>(now - m_retained->observedAt).count() <= holdMs) {
            const double ageMs = std::chrono::duration<double, std::milli>(now - m_retained->observedAt).count();
            if (config.occlusionHandling && !m_manualLocked && ageMs <= graceMs) {
                Track* recovery = nullptr;
                int plausible = 0;
                const double diagonal = std::hypot(m_retained->box.w, m_retained->box.h);
                const double radius = std::min({static_cast<double>(config.retentionRadius),
                    static_cast<double>(config.occlusionReacquireRadius), diagonal * 0.5});
                for (auto* candidate : visible) {
                    if (candidate->detection.classId != m_retained->classId) continue;
                    const auto [x, y] = targetAnchor(*candidate, config);
                    const auto& box = candidate->observedBox;
                    const double sizeRatio = std::max({box.w / std::max(1.0, m_retained->box.w),
                        m_retained->box.w / std::max(1.0, box.w), box.h / std::max(1.0, m_retained->box.h),
                        m_retained->box.h / std::max(1.0, box.h)});
                    if (sizeRatio > 1.5 || std::hypot(x - m_retained->anchorX, y - m_retained->anchorY) > radius) continue;
                    ++plausible;
                    if (candidate->age >= config.minimumTrackAge
                        && candidate->detection.confidence >= acquisitionConfidence(config)
                        && !targetExcluded(*candidate,config,width,height)) recovery = candidate;
                }
                // Preferences cannot substitute for identity evidence.
                if (plausible == 1 && recovery) {
                    m_lockedSince = now;
                    return mark(recovery);
                }
            }
            if (current) { current->selected = true; return *current; }
            return std::nullopt;
        }
        m_locked = 0; m_manualLocked = false; m_retained.reset();
        Track* best = nullptr;
        bool nearContender = false;
        for (auto* candidate : visible) {
            if (targetExcluded(*candidate,config,width,height)) continue;
            if (candidate->detection.confidence < acquisitionConfidence(config)) continue;
            if (distance(*candidate) > config.acquisitionRadius) continue;
            if (!confirmed(*candidate)) { if (band(*candidate) <= 1) nearContender = true; continue; }
            if (!best || better(*candidate, *best)) best = candidate;
        }
        if (nearContender && (!best || band(*best) == 2)) {
            if (!m_nearWaitStarted) m_nearWaitStarted = now;
            if (now - *m_nearWaitStarted < std::chrono::milliseconds(config.nearCandidateWaitMs)) return std::nullopt;
        }
        // Preserve the wait budget across contender replacement/disappearance.
        if (!best) return std::nullopt;
        m_lockedSince = now;
        return mark(best);
    }
private:
    static int classPriority(const Track& track, const AppConfig& config) {
        const auto rule = config.classPolicies.find(track.detection.classId);
        return rule == config.classPolicies.end() ? 0 : rule->second.priority;
    }
    template <typename Distance, typename Band>
    static bool materiallyBetter(const Track& candidate, const Track& current, const AppConfig& config, const Distance& distance, const Band& band) {
        const bool candidateWatched = classListed(config.watchlist, candidate.detection.classId);
        const bool currentWatched = classListed(config.watchlist, current.detection.classId);
        if (candidateWatched != currentWatched) return candidateWatched;
        const int a = classPriority(candidate, config), b = classPriority(current, config);
        if (a != b) return a > b;
        const int candidateBand = band(candidate), currentBand = band(current);
        if (candidateBand != currentBand) return candidateBand < currentBand;
        const double margin = std::clamp(config.switchingThreshold, 0.0, 0.95);
        switch (config.strategy) {
        case 1: return candidate.detection.confidence > current.detection.confidence + margin;
        case 2: return candidate.detection.box.area() > current.detection.box.area() * (1.0 + margin);
        case 3: return candidate.detection.box.area() < current.detection.box.area() * (1.0 - margin);
        case 4: return std::hypot(candidate.vx, candidate.vy) < std::hypot(current.vx, current.vy) * (1.0 - margin);
        default: return distance(candidate) < distance(current) * (1.0 - margin);
        }
    }
    int m_locked = 0;
    int m_challengerId = 0;
    AimTime m_challengerSince{};
    bool m_manualLocked = false;
    std::chrono::steady_clock::time_point m_lockedSince{};
    std::optional<RetainedLock> m_retained;
    std::optional<AimTime> m_nearWaitStarted;
};

int headlessKeyFromName(const std::wstring& value) {
    if (value == L"left") return VK_LBUTTON;
    if (value == L"middle") return VK_MBUTTON;
    if (value == L"shift") return VK_LSHIFT;
    if (value == L"control") return VK_LCONTROL;
    if (value == L"space") return VK_SPACE;
    if (value == L"f") return 'F';
    if (value == L"g") return 'G';
    return VK_RBUTTON;
}

struct XInputGamepad {
    WORD buttons = 0;
    BYTE leftTrigger = 0;
    BYTE rightTrigger = 0;
    SHORT thumbLX = 0;
    SHORT thumbLY = 0;
    SHORT thumbRX = 0;
    SHORT thumbRY = 0;
};

struct XInputState {
    DWORD packetNumber = 0;
    XInputGamepad gamepad{};
};

struct ControllerSnapshot {
    bool connected = false;
    bool bindingDown = false;
    DWORD slot = 0;
    XInputGamepad gamepad{};
};

constexpr WORD kPadDpadUp = 0x0001;
constexpr WORD kPadDpadDown = 0x0002;
constexpr WORD kPadDpadLeft = 0x0004;
constexpr WORD kPadDpadRight = 0x0008;
constexpr WORD kPadStart = 0x0010;
constexpr WORD kPadBack = 0x0020;
constexpr WORD kPadLeftThumb = 0x0040;
constexpr WORD kPadRightThumb = 0x0080;
constexpr WORD kPadLeftShoulder = 0x0100;
constexpr WORD kPadRightShoulder = 0x0200;
constexpr WORD kPadA = 0x1000;
constexpr WORD kPadB = 0x2000;
constexpr WORD kPadX = 0x4000;
constexpr WORD kPadY = 0x8000;

int controllerBindingFromName(const std::wstring& value) {
    if (value == L"a") return 0;
    if (value == L"b") return 1;
    if (value == L"x") return 2;
    if (value == L"y") return 3;
    if (value == L"left_shoulder") return 4;
    if (value == L"right_shoulder") return 5;
    if (value == L"left_thumb") return 6;
    if (value == L"right_thumb") return 7;
    if (value == L"back") return 8;
    if (value == L"start") return 9;
    if (value == L"dpad_up") return 10;
    if (value == L"dpad_down") return 11;
    if (value == L"dpad_left") return 12;
    if (value == L"dpad_right") return 13;
    if (value == L"left_trigger") return 14;
    return 15;  // right trigger
}

bool isKnownControllerBinding(const std::wstring& value) {
    return value == L"a" || value == L"b" || value == L"x" || value == L"y"
           || value == L"left_shoulder" || value == L"right_shoulder"
           || value == L"left_thumb" || value == L"right_thumb"
           || value == L"back" || value == L"start"
           || value == L"dpad_up" || value == L"dpad_down"
           || value == L"dpad_left" || value == L"dpad_right"
           || value == L"left_trigger" || value == L"right_trigger";
}

bool controllerBindingDown(const XInputGamepad& pad, int binding) {
    switch (binding) {
    case 0: return (pad.buttons & kPadA) != 0;
    case 1: return (pad.buttons & kPadB) != 0;
    case 2: return (pad.buttons & kPadX) != 0;
    case 3: return (pad.buttons & kPadY) != 0;
    case 4: return (pad.buttons & kPadLeftShoulder) != 0;
    case 5: return (pad.buttons & kPadRightShoulder) != 0;
    case 6: return (pad.buttons & kPadLeftThumb) != 0;
    case 7: return (pad.buttons & kPadRightThumb) != 0;
    case 8: return (pad.buttons & kPadBack) != 0;
    case 9: return (pad.buttons & kPadStart) != 0;
    case 10: return (pad.buttons & kPadDpadUp) != 0;
    case 11: return (pad.buttons & kPadDpadDown) != 0;
    case 12: return (pad.buttons & kPadDpadLeft) != 0;
    case 13: return (pad.buttons & kPadDpadRight) != 0;
    case 14: return pad.leftTrigger >= 30;
    default: return pad.rightTrigger >= 30;
    }
}

class XInputReader {
public:
    ~XInputReader() {
        if (m_library) FreeLibrary(m_library);
    }

    void resetTrigger() { m_trigger.reset(); }

    ControllerSnapshot sample(int configuredSlot, int binding, std::optional<DWORD> ignoredSlot = std::nullopt, int press = 30, int release = 30, int source = 0) {
        ControllerSnapshot snapshot;
        if (!load()) { m_trigger.reset(); return snapshot; }
        const DWORD first = configuredSlot > 0 ? static_cast<DWORD>(configuredSlot - 1) : 0;
        const DWORD last = configuredSlot > 0 ? first + 1 : 4;
        for (DWORD slot = first; slot < last; ++slot) {
            // A virtual output controller has no user input. Excluding its
            // XInput slot prevents it from displacing a physical controller
            // when this profile uses automatic controller selection.
            if (ignoredSlot && slot == *ignoredSlot) continue;
            XInputState state{};
            if (m_getState(slot, &state) != ERROR_SUCCESS) continue;
            snapshot.connected = true;
            snapshot.slot = slot;
            snapshot.gamepad = state.gamepad;
            if (binding == 14 || binding == 15) {
                const int value = binding == 14 ? state.gamepad.leftTrigger : state.gamepad.rightTrigger;
                snapshot.bindingDown = m_trigger.sample(value, press, release,
                    static_cast<int>(slot) + binding * 4 + source * 128 + configuredSlot * 512);
            } else {
                m_trigger.reset();
                snapshot.bindingDown = controllerBindingDown(state.gamepad, binding);
            }
            return snapshot;
        }
        m_trigger.reset();
        return snapshot;
    }

private:
    vaporlock::ControllerTrigger m_trigger;
    using GetState = DWORD(WINAPI*)(DWORD, XInputState*);

    bool load() {
        if (m_attempted) return m_getState != nullptr;
        m_attempted = true;
        for (const wchar_t* name : {L"xinput1_4.dll", L"xinput1_3.dll", L"xinput9_1_0.dll"}) {
            m_library = LoadLibraryW(name);
            if (!m_library) continue;
            m_getState = reinterpret_cast<GetState>(GetProcAddress(m_library, "XInputGetState"));
            if (m_getState) return true;
            FreeLibrary(m_library);
            m_library = nullptr;
        }
        return false;
    }

    HMODULE m_library = nullptr;
    GetState m_getState = nullptr;
    bool m_attempted = false;
};

bool applyLiveSetting(AppConfig& config, const std::wstring& field, const std::wstring& value) {
    if (field == L"class_policies") return parseClassPolicies(value, config.classPolicies);
    auto integer = [&](int& target, int minimum, int maximum) {
        wchar_t* end = nullptr;
        const long long parsed = std::wcstoll(value.c_str(), &end, 10);
        if (end == value.c_str() || *end != L'\0') return false;
        target = static_cast<int>(std::clamp(parsed, static_cast<long long>(minimum), static_cast<long long>(maximum)));
        return true;
    };
    auto decimal = [&](double& target, double minimum, double maximum) {
        wchar_t* end = nullptr;
        const double parsed = std::wcstod(value.c_str(), &end);
        if (end == value.c_str() || *end != L'\0' || !std::isfinite(parsed)) return false;
        target = std::clamp(parsed, minimum, maximum);
        return true;
    };
    auto boolean = [&](bool& target) {
        if (value == L"1" || value == L"true") { target = true; return true; }
        if (value == L"0" || value == L"false") { target = false; return true; }
        return false;
    };

    // New settings reject out-of-domain values instead of silently clamping.
    auto boundedDecimal = [&](double& target, double maximum) {
        wchar_t* end = nullptr;
        const double parsed = std::wcstod(value.c_str(), &end);
        if (end == value.c_str() || *end != L'\0' || !std::isfinite(parsed)
            || parsed < 0.0 || parsed > maximum) return false;
        target = parsed; return true;
    };
    if (field == L"assistance_x") return boundedDecimal(config.assistanceX, 4.0);
    if (field == L"assistance_y") return boundedDecimal(config.assistanceY, 4.0);
    if (field == L"controller_output_deadzone") return boundedDecimal(config.controllerOutputDeadzone, 0.5);
    if (field == L"acquire_confidence") return boundedDecimal(config.acquireConfidence, 1.0);
    if (field == L"retain_confidence") return boundedDecimal(config.retainConfidence, 1.0);

    if (field == L"confidence") return decimal(config.confidence, 0.01, 1.0);
    if (field == L"nms_iou") return decimal(config.iou, 0.01, 1.0);
    if (field == L"nms_enabled") return boolean(config.nmsEnabled);
    if (field == L"nms_class_agnostic") return boolean(config.nmsClassAgnostic);
    if (field == L"model_coordinate_units") {
        if (value == L"auto") config.modelCoordinateUnits = 0;
        else if (value == L"pixels") config.modelCoordinateUnits = 1;
        else if (value == L"normalized") config.modelCoordinateUnits = 2;
        else return false;
        return true;
    }
    if (field == L"model_output_format") {
        if (value == L"auto") config.modelOutputFormat = 0;
        else if (value == L"yolo-class") config.modelOutputFormat = 1;
        else if (value == L"yolo-objectness") config.modelOutputFormat = 2;
        else if (value == L"end-to-end") config.modelOutputFormat = 3;
        else return false;
        return true;
    }
    if (field == L"max_detections") return integer(config.maxDetections, 1, 5000);
    if (field == L"resize_mode") {
        if (value == L"stretch") { config.letterbox = false; return true; }
        if (value == L"letterbox") { config.letterbox = true; return true; }
        return false;
    }
    if (field == L"class_filter") { config.classFilter = value; return true; }

    if (field == L"monitor") return integer(config.captureMonitor, 0, 32);
    if (field == L"region_x") return integer(config.captureRegion.xOffset, -1000000, 1000000);
    if (field == L"region_y") return integer(config.captureRegion.yOffset, -1000000, 1000000);
    if (field == L"region_width") return integer(config.captureRegion.width, 1, 1000000);
    if (field == L"region_height") return integer(config.captureRegion.height, 1, 1000000);
    if (field == L"capture_fps") return integer(config.captureFps, 1, 1000000);
    if (field == L"inference_fps") return integer(config.inferenceFps, 1, 1000000);
    if (field == L"frame_skip") return integer(config.frameSkip, 0, 600);

    if (field == L"match_iou") return decimal(config.matchIou, 0.01, 1.0);
    if (field == L"max_missed") {
        if (!integer(config.maxMissed, 0, 600)) return false;
        config.trackRetentionMs = static_cast<int>(std::lround(config.maxMissed * 1000.0 / kLegacyResponseHz));
        return true;
    }
    if (field == L"smoothing") return decimal(config.smoothing, 0.0, 0.95);
    if (field == L"box_smoothing") return decimal(config.boxSmoothing, 0.0, 0.95);
    if (field == L"anchor_smoothing") return decimal(config.anchorSmoothing, 0.0, 0.98);
    if (field == L"anchor_settle_radius") return decimal(config.anchorSettleRadius, 0.0, 1000000.0);
    if (field == L"overlay_segmentation_masks") return boolean(config.overlaySegmentationMasks);
    if (field == L"segmentation_mask_limit") return integer(config.segmentationMaskLimit, 0, 128);
    if (field == L"segmentation_mask_threshold") return decimal(config.segmentationMaskThreshold, 0.01, 0.99);
    if (field == L"predict_lost") return boolean(config.predictLost);
    if (field == L"prediction_lead_ms") return integer(config.predictionLeadMs, 0, 1000000);
    if (field == L"prediction_limit_ms") return integer(config.predictionLimitMs, 1, 1000000);
    if (field == L"prediction_max_speed") return decimal(config.predictionMaxSpeed, 0.0, 1000000.0);
    if (field == L"prediction_max_acceleration") return decimal(config.predictionMaxAcceleration, 0.0, 1000000.0);
    if (field == L"prediction_max_distance") return decimal(config.predictionMaxDistance, 0.0, 1000000.0);
    if (field == L"prediction_noise_floor") return decimal(config.predictionNoiseFloor, 0.0, 1000000.0);
    if (field == L"track_retention_ms") return integer(config.trackRetentionMs, 0, 1000000);
    if (field == L"lost_target_timeout_ms") return integer(config.lostTargetTimeoutMs, 0, 1000000);
    if (field == L"occlusion_grace_ms") return integer(config.occlusionGraceMs, 0, 1000000);
    if (field == L"snap_bypasses_confirmation") return boolean(config.snapBypassesConfirmation);
    if (field == L"near_candidate_wait_ms") return integer(config.nearCandidateWaitMs, 0, 1000000);

    if (field == L"strategy") return integer(config.strategy, 0, 4);
    if (field == L"acquisition_radius") return integer(config.acquisitionRadius, 1, 1000000);
    if (field == L"near_radius") return integer(config.nearRadius, 0, 1000000);
    if (field == L"snap_radius") return integer(config.snapRadius, 0, 1000000);
    if (field == L"retention_radius") return integer(config.retentionRadius, 1, 1000000);
    if (field == L"fov_shape") {
        if (value == L"circle") config.fovShape = 0;
        else if (value == L"rectangle") config.fovShape = 1;
        else return false;
        return true;
    }
    if (field == L"switching") return decimal(config.switchingThreshold, 0.0, 0.95);
    if (field == L"lock_persistence_ms") return integer(config.lockPersistenceMs, 0, 1000000);
    if (field == L"lost_target_timeout") {
        if (!integer(config.lostTargetTimeoutFrames, 0, 1000000)) return false;
        config.lostTargetTimeoutMs = static_cast<int>(std::lround(config.lostTargetTimeoutFrames * 1000.0 / kLegacyResponseHz));
        return true;
    }
    if (field == L"minimum_track_age") return integer(config.minimumTrackAge, 1, 1000000);
    if (field == L"anchor_mode") {
        if (value == L"center") config.anchorMode = 0;
        else if (value == L"top") config.anchorMode = 1;
        else if (value == L"bottom") config.anchorMode = 2;
        else if (value == L"upper-quarter") config.anchorMode = 3;
        else return false;
        return true;
    }
    if (field == L"anchor_x") return decimal(config.anchorOffsetX, -1.0, 1.0);
    if (field == L"anchor_y") return decimal(config.anchorOffsetY, -1.0, 1.0);
    if (field == L"watchlist") {
        config.watchlist = value;
        return true;
    }
    if (field == L"occlusion_handling") return boolean(config.occlusionHandling);
    if (field == L"occlusion_grace_frames") {
        if (!integer(config.occlusionGraceFrames, 0, 1000000)) return false;
        config.occlusionGraceMs = static_cast<int>(std::lround(config.occlusionGraceFrames * 1000.0 / kLegacyResponseHz));
        return true;
    }
    if (field == L"occlusion_reacquire_radius") return integer(config.occlusionReacquireRadius, 0, 1000000);

    if (field == L"movement_enabled") {
        bool requested = false;
        if (!boolean(requested)) return false;
        config.movementEnabled = requested && !config.observeOnly;
        return true;
    }
    if (field == L"activation_mode") { config.activationMode = value == L"toggle" ? 1 : 0; return value == L"hold" || value == L"toggle"; }
    if (field == L"activation_key") {
        const int key = headlessKeyFromName(value);
        config.activationKey = key == VK_LSHIFT ? 1 : key == VK_LCONTROL ? 2 : key == VK_SPACE ? 3 : 0;
        return value == L"right" || value == L"shift" || value == L"control" || value == L"space";
    }
    if (field == L"activation_source") {
        config.activationSource = value == L"controller" ? 1 : value == L"either" ? 2 : 0;
        return value == L"keyboard" || value == L"controller" || value == L"either";
    }
    if (field == L"controller_slot") return integer(config.controllerSlot, 0, 4);
    if (field == L"controller_binding") {
        if (!isKnownControllerBinding(value)) return false;
        config.controllerBinding = controllerBindingFromName(value);
        return true;
    }
    if (field == L"gain") return decimal(config.movementGain, 0.0, 1000000.0);
    if (field == L"max_step") return integer(config.maxStep, 1, 1000000);
    if (field == L"deadzone") return integer(config.deadzone, 0, 1000000);
    if (field == L"movement_smoothing") return decimal(config.movementSmoothing, 0.0, 0.95);
    if (field == L"output_rate") return integer(config.outputRate, 1, 1000000);
    if (field == L"output_max_observation_age_ms") return integer(config.outputMaxObservationAgeMs, 1, 2000);
    if (field == L"input_units_per_pixel_x") return decimal(config.inputUnitsPerPixelX, 0.001, 1000.0);
    if (field == L"input_units_per_pixel_y") return decimal(config.inputUnitsPerPixelY, 0.001, 1000.0);
    if (field == L"max_speed") return decimal(config.maxSpeed, 0.0, 1000000.0);
    if (field == L"aim_reference") {
        if (value == L"capture-center") config.aimReference = 0;
        else if (value == L"crosshair-offset") config.aimReference = 1;
        else return false;
        return true;
    }
    if (field == L"aim_reference_x") return decimal(config.aimReferenceX, -1000000.0, 1000000.0);
    if (field == L"aim_reference_y") return decimal(config.aimReferenceY, -1000000.0, 1000000.0);
    if (field == L"motion_compensation_enabled") {
        bool requested = false;
        if (!boolean(requested)) return false;
        config.motionCompensationEnabled = requested && !config.observeOnly;
        return true;
    }
    if (field == L"motion_compensation_activation_mode") {
        config.motionCompensationActivationMode = value == L"click" ? 1 : value == L"constant" ? 2 : value == L"automatic" ? 3 : 0;
        return value == L"hold" || value == L"click" || value == L"constant" || value == L"automatic";
    }
    if (field == L"motion_compensation_activation_key") {
        const int key = headlessKeyFromName(value);
        if (key == 0) return false;
        config.motionCompensationActivationKey = value;
        return true;
    }
    if (field == L"motion_compensation_mode") {
        config.motionCompensationMode = value == L"automatic" ? 1 : value == L"combined" ? 2 : 0;
        return value == L"preset" || value == L"automatic" || value == L"combined";
    }
    if (field == L"motion_compensation_vertical") return decimal(config.motionCompensationVertical, -1000000.0, 1000000.0);
    if (field == L"motion_compensation_horizontal") return decimal(config.motionCompensationHorizontal, -1000000.0, 1000000.0);
    if (field == L"motion_compensation_strength") return decimal(config.motionCompensationStrength, 0.0, 1000000.0);
    if (field == L"motion_compensation_auto_min_tracks") return integer(config.motionCompensationAutoMinTracks, 2, 1000000);
    if (field == L"motion_compensation_auto_consensus") return decimal(config.motionCompensationAutoConsensus, 0.001, 1000000.0);
    if (field == L"motion_compensation_auto_smoothing") return decimal(config.motionCompensationAutoSmoothing, 0.0, 0.999);
    if (field == L"motion_compensation_auto_max_rate") return decimal(config.motionCompensationAutoMaxRate, 0.001, 1000000.0);
    if (field == L"event_action_enabled") {
        bool requested = false;
        if (!boolean(requested)) return false;
        config.eventActionEnabled = requested && !config.observeOnly;
        return true;
    }
    if (field == L"event_action_requires_activation") return boolean(config.eventActionRequiresActivation);
    if (field == L"event_action_mode") {
        config.eventActionMode = value == L"hold" ? 1 : 0;
        return value == L"click" || value == L"hold";
    }
    if (field == L"event_action_button") {
        config.eventActionButton = value == L"right" ? 1 : value == L"middle" ? 2 : 0;
        return value == L"left" || value == L"right" || value == L"middle";
    }
    if (field == L"event_action_cooldown_ms") return integer(config.eventActionCooldownMs, 0, 86400000);
    if (field == L"virtual_controller_manual_priority") return boolean(config.virtualControllerManualPriority);
    if (field == L"virtual_controller_stick") { config.controllerOutputStick = value == L"left" ? 0 : 1; return value == L"left" || value == L"right"; }
    if (field == L"virtual_controller_strength") return decimal(config.controllerOutputStrength, 0.0, 64.0);
    if (field == L"invert_x") return boolean(config.invertX);
    if (field == L"invert_y") return boolean(config.invertY);
    if (field == L"response_curve") { config.responseCurve = value == L"quadratic" ? 1 : 0; return value == L"linear" || value == L"quadratic"; }

    if (field == L"preview_enabled") return boolean(config.previewEnabled);
    if (field == L"preview_fps") return integer(config.previewFps, 1, 120);
    if (field == L"preview_quality") return integer(config.previewQuality, 30, 100);
    if (field == L"overlay_enabled") return boolean(config.overlayEnabled);
    if (field == L"overlay_boxes") return boolean(config.overlayBoxes);
    if (field == L"overlay_motion") return boolean(config.overlayMotion);
    if (field == L"overlay_fov") return boolean(config.overlayFov);
    if (field == L"overlay_metadata") return boolean(config.overlayMetadata);

    if (field == L"dataset_enabled") return boolean(config.datasetEnabled);
    if (field == L"dataset_capture_confidence_enabled") return boolean(config.datasetCaptureConfidenceEnabled);
    if (field == L"dataset_capture_requires_activation") return boolean(config.datasetCaptureRequiresActivation);
    if (field == L"dataset_directory") { config.datasetDirectory = value; return true; }
    if (field == L"dataset_min_confidence") return vaporlock::collection::confidence(value, config.datasetMinConfidence);
    if (field == L"dataset_interval_ms") return vaporlock::collection::integer(value, 100, 1000000, config.datasetIntervalMs);
    if (field == L"dataset_max_images") return vaporlock::collection::integer(value, 0, 1000000, config.datasetMaxImages);
    if (field == L"dataset_max_mib") return vaporlock::collection::integer(value, 0, 1000000, config.datasetMaxMiB);
    if (field == L"dataset_selected_only" || field == L"dataset_require_detections") return true;
    if (field == L"dataset_jpeg_quality") return vaporlock::collection::integer(value, 30, 100, config.datasetJpegQuality);
    return false;
}

class LiveConfigReader {
public:
    std::uint64_t takeRejectedEpoch() { return std::exchange(m_rejectedEpoch, 0); }
    bool applyAvailable(AppConfig& config) {
        bool changed = false;
        const HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
        if (!input || input == INVALID_HANDLE_VALUE) return false;
        for (;;) {
            DWORD available = 0;
            if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr) || available == 0) break;
            std::array<char, 1024> chunk{};
            DWORD read = 0;
            if (!ReadFile(input, chunk.data(), static_cast<DWORD>(std::min<std::size_t>(chunk.size(), available)), &read, nullptr) || read == 0) break;
            // The GUI only emits small one-line commands. Bound incomplete
            // input so a damaged pipe cannot grow the native process forever.
            if (m_buffer.size() + read > kMaximumBufferedBytes) m_buffer.clear();
            m_buffer.append(chunk.data(), read);
        }
        for (;;) {
            const std::size_t newline = m_buffer.find('\n');
            if (newline == std::string::npos) break;
            std::string line = m_buffer.substr(0, newline);
            m_buffer.erase(0, newline + 1);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            const std::size_t tab = line.find('\t');
            if (tab == std::string::npos || tab == 0) continue;
            const auto epochTab = line.find('\t', tab + 1);
            std::uint64_t epoch = config.visualEpoch;
            if (epochTab != std::string::npos) {
                const auto text = line.substr(epochTab + 1);
                if (text.empty() || text.size() > 15 || text.find_first_not_of("0123456789") != std::string::npos) continue;
                epoch = 0;
                for (const char digit : text) epoch = epoch * 10 + (digit - '0');
                if (epoch <= config.visualEpoch) continue;
            }
            // A rejected epoch command stops this GUI session. Do not apply a
            // later queued command and accidentally acknowledge past the failure.
            // Keep this latched until a new process owns a fresh command stream.
            if (m_rejectionLatched) {
                if (epochTab != std::string::npos) m_rejectedEpoch = epoch;
                continue;
            }
            AppConfig proposed = config;
            if (applyLiveSetting(proposed, narrowToWide(line.substr(0, tab)),
                                 narrowToWide(line.substr(tab + 1, epochTab == std::string::npos ? epochTab : epochTab - tab - 1)))) {
                applySessionSafety(proposed);
                proposed.visualEpoch = epoch;
                config = std::move(proposed);
                changed = true;
            } else if (epochTab != std::string::npos) {
                m_rejectionLatched = true;
                m_rejectedEpoch = epoch;
            }
        }
        return changed;
    }

private:
    static constexpr std::size_t kMaximumBufferedBytes = 64 * 1024;
    std::string m_buffer;
    std::uint64_t m_rejectedEpoch = 0;
    bool m_rejectionLatched = false;
};

#ifdef VAPORLOCK_HAS_VIRTUAL_CONTROLLER
std::wstring vigemErrorMessage(VIGEM_ERROR result) {
    switch (result) {
    case VIGEM_ERROR_BUS_NOT_FOUND:
        return L"The optional virtual-controller driver is not installed.";
    case VIGEM_ERROR_NO_FREE_SLOT:
        return L"Windows has no free XInput controller slot. Disconnect an unused controller and try again.";
    case VIGEM_ERROR_BUS_VERSION_MISMATCH:
        return L"The installed virtual-controller driver is not compatible with this build.";
    case VIGEM_ERROR_BUS_ACCESS_FAILED:
        return L"Windows denied access to the virtual-controller driver.";
    case VIGEM_ERROR_NOT_SUPPORTED:
        return L"The installed virtual-controller driver does not support Xbox output.";
    default:
        return L"The virtual-controller driver returned error " + std::to_wstring(static_cast<unsigned long>(result)) + L".";
    }
}

class VirtualXboxOutput {
public:
    ~VirtualXboxOutput() { reset(); }

    bool connect(std::wstring& error) {
        reset();
        m_client = vigem_alloc();
        if (!m_client) {
            error = L"Vapor Lock could not allocate the virtual-controller client.";
            return false;
        }
        const VIGEM_ERROR connection = vigem_connect(m_client);
        if (connection != VIGEM_ERROR_NONE) {
            error = vigemErrorMessage(connection);
            reset();
            return false;
        }
        m_connected = true;
        m_target = vigem_target_x360_alloc();
        if (!m_target) {
            error = L"Vapor Lock could not create an Xbox controller target.";
            reset();
            return false;
        }
        const VIGEM_ERROR added = vigem_target_add(m_client, m_target);
        if (added != VIGEM_ERROR_NONE) {
            error = vigemErrorMessage(added);
            reset();
            return false;
        }
        m_attached = true;
        ULONG slot = 0;
        if (vigem_target_x360_get_user_index(m_client, m_target, &slot) == VIGEM_ERROR_NONE) {
            m_userIndex = static_cast<DWORD>(slot);
        }
        return setStick(0, 0, false, error);
    }

    [[nodiscard]] std::optional<DWORD> userIndex() const { return m_userIndex; }
    [[nodiscard]] bool ready() const { return m_attached; }

    bool setStick(SHORT x, SHORT y, bool leftStick, std::wstring& error) {
        if (!m_attached) {
            error = L"The virtual Xbox controller is not connected.";
            return false;
        }
        XUSB_REPORT report{};
        if (leftStick) {
            report.sThumbLX = x;
            report.sThumbLY = y;
        } else {
            report.sThumbRX = x;
            report.sThumbRY = y;
        }
        const VIGEM_ERROR updated = vigem_target_x360_update(m_client, m_target, report);
        if (updated != VIGEM_ERROR_NONE) {
            error = vigemErrorMessage(updated);
            return false;
        }
        return true;
    }

    bool setPassthroughReport(const XInputGamepad& physical, SHORT trackingX, SHORT trackingY,
                              bool leftStick, bool trackingActive, bool manualPriority,
                              std::wstring& error) {
        if (!m_attached) {
            error = L"The virtual Xbox controller is not connected.";
            return false;
        }
        XUSB_REPORT report{};
        report.wButtons = physical.buttons;
        report.bLeftTrigger = physical.leftTrigger;
        report.bRightTrigger = physical.rightTrigger;
        report.sThumbLX = physical.thumbLX;
        report.sThumbLY = physical.thumbLY;
        report.sThumbRX = physical.thumbRX;
        report.sThumbRY = physical.thumbRY;

        SHORT& outputX = leftStick ? report.sThumbLX : report.sThumbRX;
        SHORT& outputY = leftStick ? report.sThumbLY : report.sThumbRY;
        // A customer moving the chosen camera stick always wins by default.
        // This keeps the compatibility bridge additive instead of fighting a
        // physical input that the user already relies on.
        constexpr int kManualStickThreshold = 6000;
        const bool physicalStickActive = std::abs(static_cast<int>(outputX)) >= kManualStickThreshold
                                         || std::abs(static_cast<int>(outputY)) >= kManualStickThreshold;
        if (trackingActive && (!manualPriority || !physicalStickActive)) {
            outputX = trackingX;
            outputY = trackingY;
        }
        const VIGEM_ERROR updated = vigem_target_x360_update(m_client, m_target, report);
        if (updated != VIGEM_ERROR_NONE) {
            error = vigemErrorMessage(updated);
            return false;
        }
        return true;
    }

    void reset() {
        if (m_client && m_target && m_attached) {
            XUSB_REPORT neutral{};
            (void)vigem_target_x360_update(m_client, m_target, neutral);
            (void)vigem_target_remove(m_client, m_target);
        }
        if (m_target) vigem_target_free(m_target);
        if (m_client && m_connected) vigem_disconnect(m_client);
        if (m_client) vigem_free(m_client);
        m_client = nullptr;
        m_target = nullptr;
        m_connected = false;
        m_attached = false;
        m_userIndex.reset();
    }

private:
    PVIGEM_CLIENT m_client = nullptr;
    PVIGEM_TARGET m_target = nullptr;
    bool m_connected = false;
    bool m_attached = false;
    std::optional<DWORD> m_userIndex;
};
#else
class VirtualXboxOutput {
public:
    bool connect(std::wstring& error) {
        error = L"This Vapor Lock build does not include the virtual Xbox output bridge.";
        return false;
    }
    [[nodiscard]] std::optional<DWORD> userIndex() const { return std::nullopt; }
    [[nodiscard]] bool ready() const { return false; }
    bool setStick(SHORT, SHORT, bool, std::wstring& error) {
        error = L"This Vapor Lock build does not include the virtual Xbox output bridge.";
        return false;
    }
    bool setPassthroughReport(const XInputGamepad&, SHORT, SHORT, bool, bool, bool, std::wstring& error) {
        error = L"This Vapor Lock build does not include the virtual Xbox output bridge.";
        return false;
    }
    void reset() { }
};
#endif

#pragma pack(push, 1)
struct ControllerHostFrame {
    std::uint32_t magic = 0x48434C56; // "VLCH" little-endian
    std::uint16_t version = 2;
    WORD buttons = 0;
    BYTE leftTrigger = 0;
    BYTE rightTrigger = 0;
    SHORT leftX = 0;
    SHORT leftY = 0;
    SHORT rightX = 0;
    SHORT rightY = 0;
    SHORT trackingX = 0;
    SHORT trackingY = 0;
    BYTE flags = 0;
    BYTE reserved = 0;
    std::uint64_t sentAtMilliseconds = 0;
};
#pragma pack(pop)

static_assert(sizeof(ControllerHostFrame) == 32, "Controller bridge wire format changed unexpectedly.");

class ControllerHostOutput {
public:
    explicit ControllerHostOutput(std::wstring pipeName)
        : m_pipePath(L"\\\\.\\pipe\\" + std::move(pipeName)), m_started(GetTickCount64()) { }

    ~ControllerHostOutput() { disconnect(); }
    ControllerHostOutput(const ControllerHostOutput&) = delete;
    ControllerHostOutput& operator=(const ControllerHostOutput&) = delete;

    [[nodiscard]] bool connected() const { return m_pipe != INVALID_HANDLE_VALUE; }
    [[nodiscard]] bool unavailableTooLong() const { return m_failed || (!connected() && GetTickCount64() - m_started > 6000); }
    [[nodiscard]] const std::wstring& lastError() const { return m_lastError; }

    bool send(const ControllerSnapshot& physical, SHORT trackingX, SHORT trackingY,
              bool proxyMode, bool leftStick, bool trackingActive, bool manualPriority) {
        if (!ensureConnected()) return false;

        ControllerHostFrame frame{};
        if (physical.connected) {
            frame.buttons = physical.gamepad.buttons;
            frame.leftTrigger = physical.gamepad.leftTrigger;
            frame.rightTrigger = physical.gamepad.rightTrigger;
            frame.leftX = physical.gamepad.thumbLX;
            frame.leftY = physical.gamepad.thumbLY;
            frame.rightX = physical.gamepad.thumbRX;
            frame.rightY = physical.gamepad.thumbRY;
            frame.flags |= 1 << 0;
        }
        frame.trackingX = trackingX;
        frame.trackingY = trackingY;
        if (proxyMode) frame.flags |= 1 << 1;
        if (leftStick) frame.flags |= 1 << 2;
        if (trackingActive) frame.flags |= 1 << 3;
        if (manualPriority) frame.flags |= 1 << 4;
        frame.sentAtMilliseconds = GetTickCount64();

        const DWORD failure = m_writer.write(m_pipe, &frame, sizeof(frame), 5);
        if (failure == ERROR_SUCCESS) {
            m_lastError.clear();
            return true;
        }
        m_lastError = failure == ERROR_TIMEOUT
            ? L"The controller bridge stopped consuming input. Output was stopped instead of queuing stale movement."
            : L"The Xbox One-compatible controller bridge pipe stopped (Windows error " + std::to_wstring(failure) + L").";
        m_failed = true;
        disconnect();
        return false;
    }

private:
    bool ensureConnected() {
        if (m_failed) return false;
        if (!m_writer.ready()) {
            m_lastError = L"Windows could not prepare asynchronous controller output.";
            m_failed = true;
            return false;
        }
        if (connected()) return true;
        const ULONGLONG now = GetTickCount64();
        if (now < m_nextAttempt) return false;
        m_nextAttempt = now + 150;
        // Startup retries remain paced, but waiting for a busy pipe must not
        // hold the movement scheduler's cancellation lock for 100 ms.
        m_pipe = CreateFileW(m_pipePath.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
        if (m_pipe == INVALID_HANDLE_VALUE) {
            const DWORD failure = GetLastError();
            m_lastError = L"Connecting to the Xbox One-compatible controller bridge failed (Windows error "
                          + std::to_wstring(failure) + L").";
            return false;
        }
        return true;
    }

    void disconnect() {
        if (m_pipe != INVALID_HANDLE_VALUE) CloseHandle(m_pipe);
        m_pipe = INVALID_HANDLE_VALUE;
    }

    std::wstring m_pipePath;
    HANDLE m_pipe = INVALID_HANDLE_VALUE;
    ULONGLONG m_started = 0;
    ULONGLONG m_nextAttempt = 0;
    std::wstring m_lastError;
    vaporlock::io::BoundedWindowsWrite m_writer;
    bool m_failed = false;
};

#include "app/AimOutput.h"

void updateFrameReference(AppConfig& config, const DesktopCapture& capture,
                          vaporlock::crosshair::Matcher& matcher, std::uint64_t generation) {
    int sx=GetSystemMetrics(SM_XVIRTUALSCREEN),sy=GetSystemMetrics(SM_YVIRTUALSCREEN);
    int sw=GetSystemMetrics(SM_CXVIRTUALSCREEN),sh=GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if(config.captureMonitor>0) {
        MONITORINFO info{sizeof(MONITORINFO)};
        if(!GetMonitorInfoW(DesktopCapture::sourceMonitor(config.captureMonitor),&info))sw=sh=0;
        else {sx=info.rcMonitor.left;sy=info.rcMonitor.top;sw=info.rcMonitor.right-sx;sh=info.rcMonitor.bottom-sy;}
    }
    auto ref=vaporlock::crosshair::screenReference(sx,sy,sw,sh,capture.left(),capture.top(),capture.width(),capture.height());
    if(config.captureMonitor==0 && !MonitorFromPoint(POINT{static_cast<LONG>(sx+sw*.5),static_cast<LONG>(sy+sh*.5)},MONITOR_DEFAULTTONULL)) {ref.inside=false;ref.status="screen-gap";}
    if(!config.crosshairEnabled) {
        matcher.reset();
        if(config.aimReference==1) {
            ref.x=capture.width()*.5+config.aimReferenceX;ref.y=capture.height()*.5+config.aimReferenceY;
            ref.inside=ref.x>=0&&ref.y>=0&&ref.x<capture.width()&&ref.y<capture.height();
            ref.status=ref.inside?"manual":"outside-capture";
        }
    } else if(config.crosshairTemplate.samples.empty()||config.captureMonitor<=0) {
        matcher.reset();ref.status="recalibrate";
    } else {
        const auto identity=capture.frameIdentity(generation);
        ref=matcher.update(config.crosshairTemplate,{config.captureMonitor,sx,sy,sw,sh,
            config.captureRegion.xOffset,config.captureRegion.yOffset,config.captureRegion.width,config.captureRegion.height},
            capture.bgra(),capture.width(),capture.height(),capture.left(),capture.top(),
            generation,identity.sourceGeneration,identity.frameId,capture.observedAt(),AimClock::now(),ref);
    }
    config.frameReference=ref;
}

#include "app/PreviewWorker.h"

int runHeadless(int argc, wchar_t* argv[]) {
    // Exact read-only query, with no model/runtime initialization, capture,
    // input request. Extra arguments never reach this path.
    if (argc == 3 && std::wstring_view(argv[1]) == L"--headless" && std::wstring_view(argv[2]) == L"--capabilities") {
        std::cout << "{\"type\":\"capabilities\",\"protocol_version\":1,\"cuda_preprocess\":"
#if VAPORLOCK_HAS_ONNXRUNTIME && (VAPORLOCK_HAS_CUDA || VAPORLOCK_FORCE_TENSORRT_RTX) && VAPORLOCK_HAS_CUDA_PREPROCESS && !VAPORLOCK_FORCE_DML
                  << "true"
#else
                  << "false"
#endif
                  << "}" << std::endl;
        return 0;
    }
    AppConfig config;
    bool inspectModel = false;
    bool running = true;
    for (int index = 1; index < argc; ++index) {
        const std::wstring argument = argv[index];
        auto next = [&]() -> std::wstring { return ++index < argc ? argv[index] : L""; };
        if(argument==L"--cloud-pipe"){config.cloudPipe=next();continue;}
        if(argument==L"--cloud-worker-pid"){config.cloudWorkerPid=static_cast<DWORD>(_wcstoui64(next().c_str(),nullptr,10));continue;}
        if (argument == L"--assistance-x" || argument == L"--assistance-y"
            || argument == L"--controller-output-deadzone" || argument == L"--acquire-confidence"
            || argument == L"--retain-confidence") {
            auto field = argument.substr(2);
            std::replace(field.begin(), field.end(), L'-', L'_');
            if (!applyLiveSetting(config, field, next())) {
                std::cout << "{\"type\":\"error\",\"message\":\"Invalid assistance or confidence setting\"}" << std::endl;
                return 2;
            }
            continue;
        }
        if (argument == L"--model-coordinate-units") {
            if (!applyLiveSetting(config, L"model_coordinate_units", next())) {
                std::cout << "{\"type\":\"error\",\"message\":\"Unsupported model coordinate units\"}" << std::endl;
                return 2;
            }
            continue;
        }
        if (argument == L"--aim-setting") {
            const auto setting = next();
            const auto equal = setting.find(L'=');
            if (equal == std::wstring::npos || !applyLiveSetting(config, setting.substr(0, equal), setting.substr(equal + 1))) {
                std::cout << "{\"type\":\"error\",\"message\":\"Invalid aim setting.\"}" << std::endl;
                return 2;
            }
            continue;
        }
        // Keep this separate from the long compatibility parser below. MSVC
        // otherwise reaches its nested conditional limit as options grow.
        if (argument == L"--motion-compensation") {
            config.motionCompensationEnabled = true;
            continue;
        }
        if (argument == L"--no-motion-compensation") {
            config.motionCompensationEnabled = false;
            continue;
        }
        if (argument == L"--motion-compensation-activation-mode") {
            const auto mode = next();
            config.motionCompensationActivationMode = mode == L"click" ? 1 : mode == L"constant" ? 2 : mode == L"automatic" ? 3 : 0;
            continue;
        }
        if (argument == L"--motion-compensation-activation-key") {
            const auto key = next();
            if (headlessKeyFromName(key) == 0) {
                std::cout << "{\"type\":\"error\",\"message\":\"Unsupported motion compensation activation key\"}" << std::endl;
                return 2;
            }
            config.motionCompensationActivationKey = key;
            continue;
        }
        if (argument == L"--motion-compensation-mode") {
            const auto mode = next();
            config.motionCompensationMode = mode == L"automatic" ? 1 : mode == L"combined" ? 2 : 0;
            continue;
        }
        if (argument == L"--motion-compensation-vertical") {
            config.motionCompensationVertical = std::clamp(wcstod(next().c_str(), nullptr), -1000000.0, 1000000.0);
            continue;
        }
        if (argument == L"--motion-compensation-horizontal") {
            config.motionCompensationHorizontal = std::clamp(wcstod(next().c_str(), nullptr), -1000000.0, 1000000.0);
            continue;
        }
        if (argument == L"--motion-compensation-strength") {
            config.motionCompensationStrength = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 1000000.0);
            continue;
        }
        if (argument == L"--motion-compensation-auto-min-tracks") {
            config.motionCompensationAutoMinTracks = std::clamp(_wtoi(next().c_str()), 2, 1000000);
            continue;
        }
        if (argument == L"--motion-compensation-auto-consensus") {
            config.motionCompensationAutoConsensus = std::clamp(wcstod(next().c_str(), nullptr), 0.001, 1000000.0);
            continue;
        }
        if (argument == L"--motion-compensation-auto-smoothing") {
            config.motionCompensationAutoSmoothing = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 0.999);
            continue;
        }
        if (argument == L"--motion-compensation-auto-max-rate") {
            config.motionCompensationAutoMaxRate = std::clamp(wcstod(next().c_str(), nullptr), 0.001, 1000000.0);
            continue;
        }
        // Collection options form a separate early-handled group. Keeping
        // their validators here avoids MSVC's limit on nested else-if blocks.
        if (argument == L"--dataset" || argument == L"--no-dataset"
                || argument.rfind(L"--dataset-", 0) == 0 || argument == L"--no-dataset-capture-confidence") {
            if (argument == L"--dataset") config.datasetEnabled = true;
            else if (argument == L"--no-dataset") config.datasetEnabled = false;
            else if (argument == L"--dataset-capture-confidence") config.datasetCaptureConfidenceEnabled = true;
            else if (argument == L"--no-dataset-capture-confidence") config.datasetCaptureConfidenceEnabled = false;
            else if (argument == L"--dataset-directory") config.datasetDirectory = next();
            else if (argument == L"--dataset-min-confidence") {
                if (!vaporlock::collection::confidence(next(), config.datasetMinConfidence)) {
                    std::cout << "{\"type\":\"error\",\"message\":\"Invalid capture confidence\"}" << std::endl;
                    return 2;
                }
            }
            else if (argument == L"--dataset-interval-ms") {
                if (!vaporlock::collection::integer(next(), 100, 1000000, config.datasetIntervalMs)) {
                    std::cout << "{\"type\":\"error\",\"message\":\"Invalid capture interval\"}" << std::endl;
                    return 2;
                }
            }
            else if (argument == L"--dataset-requires-activation") config.datasetCaptureRequiresActivation = true;
            else if (argument == L"--dataset-max-images" || argument == L"--dataset-max-mib") {
                auto& limit = argument == L"--dataset-max-images" ? config.datasetMaxImages : config.datasetMaxMiB;
                if (!vaporlock::collection::integer(next(), 0, 1000000, limit)) {
                    std::cout << "{\"type\":\"error\",\"message\":\"Invalid collection limit\"}" << std::endl;
                    return 2;
                }
            }
            else if (argument == L"--dataset-anytime") config.datasetCaptureRequiresActivation = false;
            else if (argument == L"--dataset-jpeg-quality") {
                if (!vaporlock::collection::integer(next(), 30, 100, config.datasetJpegQuality)) {
                    std::cout << "{\"type\":\"error\",\"message\":\"Invalid capture JPEG quality\"}" << std::endl;
                    return 2;
                }
            }
            // Historical label policy is intentionally ignored: raw source
            // capture is now labeled only by the background worker.
            else if (argument != L"--dataset-selected-only" && argument != L"--dataset-all-detections"
                     && argument != L"--dataset-allow-empty" && argument != L"--dataset-require-detections") {
                std::cout << "{\"type\":\"error\",\"message\":\"Unknown dataset option\"}" << std::endl;
                return 2;
            }
            continue;
        }
        if (argument == L"--model") config.modelPath = next();
        else if (argument == L"--provider") {
            const auto provider = next();
            if (provider == L"cpu") config.provider = InferenceProvider::Cpu;
            else if (provider == L"cuda") config.provider = InferenceProvider::Cuda;
            else if (provider == L"dml") config.provider = InferenceProvider::DirectMl;
            else if (provider == L"trt") config.provider = InferenceProvider::TensorRtRtx;
            else { std::cout << "{\"type\":\"error\",\"message\":\"Provider must be cpu, cuda, or dml\"}" << std::endl; return 2; }
        }
        else if (argument == L"--inference-device") {
            const auto text=next();
            if(text.empty() || text.size()>2 || (text!=L"-1" && text.find_first_not_of(L"0123456789")!=std::wstring::npos)) return 2;
            config.inferenceDevice=_wtoi(text.c_str());
            if(config.inferenceDevice < -1 || config.inferenceDevice >= 64) return 2;
        }
        else if (argument == L"--confidence") config.confidence = std::clamp(wcstod(next().c_str(), nullptr), 0.01, 1.0);
        else if (argument == L"--nms-iou") config.iou = std::clamp(wcstod(next().c_str(), nullptr), 0.01, 1.0);
        else if (argument == L"--nms") config.nmsEnabled = true;
        else if (argument == L"--no-nms") config.nmsEnabled = false;
        else if (argument == L"--class-agnostic-nms") config.nmsClassAgnostic = true;
        else if (argument == L"--class-aware-nms") config.nmsClassAgnostic = false;
        else if (argument == L"--model-output-format") {
            const auto format = next();
            if (format == L"auto") config.modelOutputFormat = 0;
            else if (format == L"yolo-class") config.modelOutputFormat = 1;
            else if (format == L"yolo-objectness") config.modelOutputFormat = 2;
            else if (format == L"end-to-end") config.modelOutputFormat = 3;
            else { std::cout << "{\"type\":\"error\",\"message\":\"Unsupported model output format\"}" << std::endl; return 2; }
        }
        else if (argument == L"--max-detections") config.maxDetections = std::clamp(_wtoi(next().c_str()), 1, 5000);
        else if (argument == L"--resize-mode") config.letterbox = next() != L"stretch";
        else if (argument == L"--class-filter") config.classFilter = next();
        else if (argument == L"--primary-display") config.captureMonitor = 1;
        else if (argument == L"--monitor") config.captureMonitor = std::clamp(_wtoi(next().c_str()), 0, 32);
        else if (argument == L"--region") {
            std::wstringstream values(next());
            std::wstring value;
            std::array<int*, 4> targets{&config.captureRegion.xOffset, &config.captureRegion.yOffset, &config.captureRegion.width, &config.captureRegion.height};
            for (auto* target : targets) {
                if (!std::getline(values, value, L',')) { std::cout << "{\"type\":\"error\",\"message\":\"Region must be x,y,width,height\"}" << std::endl; return 2; }
                *target = _wtoi(value.c_str());
            }
            if (config.captureRegion.width <= 0 || config.captureRegion.height <= 0) { std::cout << "{\"type\":\"error\",\"message\":\"Region width and height must be positive\"}" << std::endl; return 2; }
        }
        else if (argument == L"--crosshair-enabled") config.crosshairEnabled=true;
        else if (argument == L"--crosshair-template") {
            if(!vaporlock::crosshair::parse(next(),config.crosshairTemplate)) {
                std::cout << "{\"type\":\"error\",\"message\":\"Invalid crosshair calibration\"}" << std::endl;return 2;
            }
        }
        else if (argument == L"--capture-fps") config.captureFps = std::max(1, _wtoi(next().c_str()));
        else if (argument == L"--inference-fps") config.inferenceFps = std::max(1, _wtoi(next().c_str()));
        else if (argument == L"--frame-skip") config.frameSkip = std::clamp(_wtoi(next().c_str()), 0, 60);
        else if (argument == L"--predict-lost") config.predictLost = true;
        else if (argument == L"--no-predict-lost") config.predictLost = false;
        else if (argument == L"--cpu-sets") { config.cpuSets=next(); if(config.cpuSets.size()>768)return 2; }
        else if (argument == L"--ort-intra-op-threads") config.ortIntraOpThreads = std::clamp(_wtoi(next().c_str()), 0, 64);
        else if (argument == L"--ort-inter-op-threads") config.ortInterOpThreads = std::clamp(_wtoi(next().c_str()), 0, 32);
        else if (argument == L"--cuda-graph-replay") config.cudaGraphReplay = true;
        else if (argument == L"--no-cuda-graph-replay") config.cudaGraphReplay = false;
        else if (argument == L"--cuda-preprocess") config.cudaPreprocess = true;
        else if (argument == L"--no-cuda-preprocess") config.cudaPreprocess = false;
        else if (argument == L"--match-iou") config.matchIou = std::clamp(wcstod(next().c_str(), nullptr), 0.01, 1.0);
        else if (argument == L"--smoothing") config.smoothing = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 0.95);
        else if (argument == L"--box-smoothing" || argument == L"--anchor-smoothing" || argument == L"--anchor-settle-radius") {
            const double value = wcstod(next().c_str(), nullptr);
            if (argument == L"--box-smoothing") config.boxSmoothing = std::clamp(value, 0.0, 0.95);
            else if (argument == L"--anchor-smoothing") config.anchorSmoothing = std::clamp(value, 0.0, 0.98);
            else config.anchorSettleRadius = std::clamp(value, 0.0, 1000000.0);
        }
        else if (argument == L"--max-missed") config.maxMissed = std::clamp(_wtoi(next().c_str()), 0, 600);
        else if (argument == L"--prediction-lead-ms") config.predictionLeadMs = std::clamp(_wtoi(next().c_str()), 0, 1000);
        else if (argument == L"--prediction-limit-ms") config.predictionLimitMs = std::clamp(_wtoi(next().c_str()), 1, 5000);
        else if (argument == L"--strategy") config.strategy = std::clamp(_wtoi(next().c_str()), 0, 4);
        else if (argument == L"--acquire-radius") config.acquisitionRadius = std::clamp(_wtoi(next().c_str()), 1, 10000);
        else if (argument == L"--near-radius") config.nearRadius = std::clamp(_wtoi(next().c_str()), 0, 10000);
        else if (argument == L"--snap-radius") config.snapRadius = std::clamp(_wtoi(next().c_str()), 0, 10000);
        else if (argument == L"--retain-radius") config.retentionRadius = std::clamp(_wtoi(next().c_str()), 1, 10000);
        else if (argument == L"--fov-shape") config.fovShape = next() == L"rectangle" ? 1 : 0;
        else if (argument == L"--switching") config.switchingThreshold = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 0.95);
        else if (argument == L"--lock-persistence-ms") config.lockPersistenceMs = std::clamp(_wtoi(next().c_str()), 0, 30000);
        else if (argument == L"--lost-target-timeout") config.lostTargetTimeoutFrames = std::clamp(_wtoi(next().c_str()), 0, 600);
        else if (argument == L"--min-track-age") config.minimumTrackAge = std::clamp(_wtoi(next().c_str()), 1, 120);
        else if (argument == L"--anchor-mode") {
            const auto mode = next();
            config.anchorMode = mode == L"top" ? 1 : mode == L"bottom" ? 2 : mode == L"upper-quarter" ? 3 : 0;
        }
        else if (argument == L"--exclusion-zones") {
            if (!parseExclusionZones(next(), config.exclusionZones)) {
                std::cout << "{\"type\":\"error\",\"message\":\"Invalid exclusion zones\"}" << std::endl;
                return 2;
            }
        }
        else if (argument == L"--class-policies") {
            if (!parseClassPolicies(next(), config.classPolicies)) {
                std::cout << "{\"type\":\"error\",\"message\":\"Invalid class policy rules\"}" << std::endl;
                return 1;
            }
        }
        else if (argument == L"--anchor-x") config.anchorOffsetX = std::clamp(wcstod(next().c_str(), nullptr), -1.0, 1.0);
        else if (argument == L"--anchor-y") config.anchorOffsetY = std::clamp(wcstod(next().c_str(), nullptr), -1.0, 1.0);
        else if (argument == L"--watchlist") config.watchlist = next();
        else if (argument == L"--occlusion-handling") config.occlusionHandling = true;
        else if (argument == L"--no-occlusion-handling") config.occlusionHandling = false;
        else if (argument == L"--occlusion-grace-frames") config.occlusionGraceFrames = std::clamp(_wtoi(next().c_str()), 0, 1000000);
        else if (argument == L"--occlusion-reacquire-radius") config.occlusionReacquireRadius = std::clamp(_wtoi(next().c_str()), 0, 1000000);
        else if (argument == L"--observe-only") config.observeOnly = true;
        else if (argument == L"--movement") config.movementEnabled = true;
        else if (argument == L"--activation-mode") config.activationMode = next() == L"toggle" ? 1 : 0;
        else if (argument == L"--activation-key") { const int key = headlessKeyFromName(next()); config.activationKey = key == VK_LSHIFT ? 1 : key == VK_LCONTROL ? 2 : key == VK_SPACE ? 3 : 0; }
        else if (argument == L"--activation-source") {
            const auto source = next();
            config.activationSource = source == L"controller" ? 1 : source == L"either" ? 2 : 0;
        }
        else if (argument == L"--controller-slot") config.controllerSlot = std::clamp(_wtoi(next().c_str()), 0, 4);
        else if (argument == L"--controller-binding") config.controllerBinding = controllerBindingFromName(next());
        else if (argument == L"--controller-trigger-press" || argument == L"--controller-trigger-release") {
            const auto raw = next();
            int parsed = 0;
            if (raw.empty() || raw.size() > 3 || raw.find_first_not_of(L"0123456789") != std::wstring::npos
                || (parsed = _wtoi(raw.c_str())) < 1 || parsed > 255) return 2;
            if (argument == L"--controller-trigger-press") config.controllerTriggerPress = parsed;
            else config.controllerTriggerRelease = parsed;
        }
        // Accepted as no-ops so older saved command lines remain compatible.
        else if (argument == L"--controller-manual-override" || argument == L"--no-controller-manual-override") { }
        else if (argument == L"--controller-deadzone") { (void)next(); }
        else if (argument == L"--manual-target-lock") config.manualTargetLockEnabled = true;
        else if (argument == L"--no-manual-target-lock") config.manualTargetLockEnabled = false;
        else if (argument == L"--manual-lock-source") {
            const auto source = next();
            config.manualLockSource = source == L"controller" ? 1 : source == L"either" ? 2 : 0;
        }
        else if (argument == L"--manual-lock-key") config.manualLockKey = next();
        else if (argument == L"--manual-lock-controller-binding") config.manualLockControllerBinding = controllerBindingFromName(next());
        else if (argument == L"--manual-lock-event") config.manualLockEvent = next();
        else if (argument == L"--gain") config.movementGain = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 4.0);
        else if (argument == L"--max-step") config.maxStep = std::clamp(_wtoi(next().c_str()), 1, 500);
        else if (argument == L"--deadzone") config.deadzone = std::clamp(_wtoi(next().c_str()), 0, 500);
        else if (argument == L"--movement-smoothing") config.movementSmoothing = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 0.95);
        else if (argument == L"--output-rate") config.outputRate = std::max(1, _wtoi(next().c_str()));
        else if (argument == L"--event-action") config.eventActionEnabled = true;
        else if (argument == L"--no-event-action") config.eventActionEnabled = false;
        else if (argument == L"--event-action-requires-activation") config.eventActionRequiresActivation = true;
        else if (argument == L"--event-action-independent") config.eventActionRequiresActivation = false;
        else if (argument == L"--event-action-mode") config.eventActionMode = next() == L"hold" ? 1 : 0;
        else if (argument == L"--event-action-button") {
            const auto button = next();
            config.eventActionButton = button == L"right" ? 1 : button == L"middle" ? 2 : 0;
        }
        else if (argument == L"--event-action-cooldown-ms") config.eventActionCooldownMs = std::clamp(_wtoi(next().c_str()), 0, 86400000);
        else if (argument == L"--output-transport") {
            if (!vaporlock::hardware::parseTransport(next(), config.outputTransport)) {
                std::cout << "{\"type\":\"error\",\"message\":\"Unsupported output transport\"}" << std::endl;
                return 2;
            }
        }
        else if (argument == L"--virtual-controller-mode") config.virtualControllerMode = next() == L"slot0-proxy" ? 1 : 0;
        else if (argument == L"--virtual-controller-stick") config.controllerOutputStick = next() == L"left" ? 0 : 1;
        else if (argument == L"--virtual-controller-strength") config.controllerOutputStrength = std::clamp(wcstod(next().c_str(), nullptr), 0.0, 64.0);
        else if (argument == L"--virtual-controller-manual-priority") config.virtualControllerManualPriority = true;
        else if (argument == L"--no-virtual-controller-manual-priority") config.virtualControllerManualPriority = false;
        else if (argument == L"--controller-host-pipe") config.controllerHostPipe = next();
        else if (argument == L"--controller-host-virtual-slot") config.controllerHostVirtualSlot = std::clamp(_wtoi(next().c_str()), 0, 3);
        else if (argument == L"--hardware-serial-port") config.hardwareSerialPort = next();
        else if (argument == L"--hardware-serial-baud") config.hardwareSerialBaud = std::clamp(_wtoi(next().c_str()), 1200, 4000000);
        else if (argument == L"--kmbox-net-address") config.kmboxNetAddress = next();
        else if (argument == L"--kmbox-net-port") config.kmboxNetPort = std::clamp(_wtoi(next().c_str()), 0, 65535);
        else if (argument == L"--kmbox-net-device-code") config.kmboxNetDeviceCode = next();
        else if (argument == L"--kmbox-a-library") config.kmboxALibraryPath = next();
        else if (argument == L"--kmbox-a-vid") config.kmboxAVendorId = static_cast<int>(std::clamp<unsigned long>(wcstoul(next().c_str(), nullptr, 0), 0, 65535));
        else if (argument == L"--kmbox-a-pid") config.kmboxAProductId = static_cast<int>(std::clamp<unsigned long>(wcstoul(next().c_str(), nullptr, 0), 0, 65535));
        else if (argument == L"--virtual-mouse-library") config.virtualMouseLibraryPath = next();
        else if (argument == L"--invert-x") config.invertX = true;
        else if (argument == L"--invert-y") config.invertY = true;
        else if (argument == L"--response-curve") config.responseCurve = next() == L"quadratic" ? 1 : 0;
        else if (argument == L"--preview") config.previewEnabled = true;
        else if (argument == L"--no-preview") config.previewEnabled = false;
        else if (argument == L"--preview-fps") config.previewFps = std::clamp(_wtoi(next().c_str()), 1, 120);
        else if (argument == L"--preview-quality") config.previewQuality = std::clamp(_wtoi(next().c_str()), 30, 100);
        else if (argument == L"--overlay") config.overlayEnabled = true;
        else if (argument == L"--no-overlay") config.overlayEnabled = false;
        else if (argument == L"--overlay-boxes") config.overlayBoxes = true;
        else if (argument == L"--no-overlay-boxes") config.overlayBoxes = false;
        else if (argument == L"--overlay-motion") config.overlayMotion = true;
        else if (argument == L"--no-overlay-motion") config.overlayMotion = false;
        else if (argument == L"--overlay-fov") config.overlayFov = true;
        else if (argument == L"--no-overlay-fov") config.overlayFov = false;
        else if (argument == L"--overlay-metadata" || argument == L"--overlay-segmentation-masks" || argument == L"--no-overlay-segmentation-masks"
                 || argument == L"--segmentation-mask-limit" || argument == L"--segmentation-mask-threshold") {
            if (argument == L"--overlay-metadata") config.overlayMetadata = true;
            else if (argument == L"--overlay-segmentation-masks") config.overlaySegmentationMasks = true;
            else if (argument == L"--no-overlay-segmentation-masks") config.overlaySegmentationMasks = false;
            else if (argument == L"--segmentation-mask-limit") config.segmentationMaskLimit = std::clamp(_wtoi(next().c_str()), 0, 128);
            else config.segmentationMaskThreshold = std::clamp(wcstod(next().c_str(), nullptr), 0.01, 0.99);
        }
        else if (argument == L"--inspect-model") inspectModel = true;
        else if (argument == L"--headless") continue;
        else {
            std::cout << "{\"type\":\"error\",\"message\":\"Unknown option\"}" << std::endl;
            return 2;
        }
    }

    if (!vaporlock::validTriggerThresholds(config.controllerTriggerPress, config.controllerTriggerRelease)) return 2;

    applySessionSafety(config);


    OnnxDetector detector;
    if (!config.modelPath.empty()) {
        std::wstring error;
        if (!detector.loadModel(config.modelPath, config.provider, config.ortIntraOpThreads, config.ortInterOpThreads, config.cudaGraphReplay, config.cudaPreprocess, error, config.inferenceDevice, config.cpuSets, config.cloudPipe, config.cloudWorkerPid)) {
            std::cout << "{\"type\":\"error\",\"message\":\"" << wideToUtf8(jsonEscape(error)) << "\"}" << std::endl;
            return 1;
        }
        if (inspectModel) {
            std::cout << "{\"type\":\"model\",\"message\":\"" << wideToUtf8(jsonEscape(detector.status())) << "\"}" << std::endl;
            return 0;
        }
    } else if (inspectModel) {
        std::cout << "{\"type\":\"error\",\"message\":\"Choose an ONNX model before inspecting it\"}" << std::endl;
        return 2;
    }

    const bool usingVirtualXbox = config.outputTransport == vaporlock::hardware::kVirtualXboxTransport;
    const bool usingHardwareOutput = vaporlock::hardware::usesHardwareTransport(config.outputTransport);
    const bool usingControllerHost = usingVirtualXbox && !config.controllerHostPipe.empty();
    if (usingVirtualXbox && !usingControllerHost) {
        std::cout << "{\"type\":\"output-error\",\"message\":\"Prepare the local Xbox One-compatible controller bridge from the control center before starting this output transport.\"}" << std::endl;
        return 1;
    }
    std::optional<ControllerHostOutput> controllerHostOutput;
    if (usingControllerHost) controllerHostOutput.emplace(config.controllerHostPipe);

    std::optional<vaporlock::hardware::HardwareMovementOutput> hardwareOutput;
    if (usingHardwareOutput) {
        vaporlock::hardware::HardwareOutputConfig hardwareConfig;
        hardwareConfig.serialPort = config.hardwareSerialPort;
        hardwareConfig.serialBaud = config.hardwareSerialBaud;
        hardwareConfig.kmboxNetAddress = config.kmboxNetAddress;
        hardwareConfig.kmboxNetPort = config.kmboxNetPort;
        hardwareConfig.kmboxNetDeviceCode = config.kmboxNetDeviceCode;
        hardwareConfig.kmboxALibraryPath = config.kmboxALibraryPath;
        hardwareConfig.kmboxAVendorId = config.kmboxAVendorId;
        hardwareConfig.kmboxAProductId = config.kmboxAProductId;
        hardwareConfig.virtualMouseLibraryPath = config.virtualMouseLibraryPath;
        hardwareOutput.emplace();
        std::wstring outputError;
        if (!hardwareOutput->prepare(config.outputTransport, hardwareConfig, outputError)) {
            std::cout << "{\"type\":\"output-error\",\"message\":\"Hardware output is unavailable: "
                      << wideToUtf8(jsonEscape(outputError)) << "\"}" << std::endl;
            return 1;
        }
        std::cout << "{\"type\":\"output\",\"transport\":\""
                  << wideToUtf8(vaporlock::hardware::transportWireName(config.outputTransport))
                  << "\",\"message\":\"Hardware movement output is connected.\"}" << std::endl;
    }
    if (usingControllerHost) {
        std::cout << "{\"type\":\"output\",\"transport\":\"virtual-xbox\",\"message\":\"Controller bridge is preparing.\"}" << std::endl;
    }

    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    DesktopCapture capture;
    IoUTracker tracker;
    TargetSelector selector;
    MotionCompensationState motionCompensation;
    std::vector<Track> tracks;
    std::optional<Track> selected;
    vaporlock::InferenceCadence inferenceCadence;
    vaporlock::pipeline::PipelineTiming pipelineTiming;
    vaporlock::pipeline::FrameSchedule frameSchedule;
    vaporlock::pipeline::CloudFreshFrameSchedule cloudCaptureSchedule;
    vaporlock::pipeline::DeadlineWait frameWait;
    AimTime previousLoop{}, previousObservation{}, previousCompletion{};
    const auto started = std::chrono::steady_clock::now();
    auto lastReport = started;
    long long frames = 0;
    long long explicitSkippedFrames = 0, rateLimitedFrames = 0, noFreshFramePolls = 0;
    long long completedInferences = 0;
    long long syntheticInferences = 0;
    long long obsoleteInferences = 0;
    std::uint64_t trackingGeneration = 0, trackingSource = 0;
    long long freshCaptures = 0;
    struct RateSample { AimTime time; long long captures, inferences; std::uint64_t outputs; };
    std::deque<RateSample> rateSamples{{started, 0, 0, 0}};
    double inferenceWorkMs = 0.0, modelWorkMs = 0.0;
    double captureMs = 0.0;
    double inferenceMs = 0.0;
    double preprocessMs = 0.0;
    double modelMs = 0.0;
    double captureAcquireMs=0.,captureWallMs=0.;
    std::uint64_t retiredCaptureAttempts=0;
    double postprocessMs = 0.0;
    double trackingMs = 0.0;
    int detections = 0;
    bool manualLockWasDown = false;
    std::chrono::steady_clock::time_point lastPreview{};
    PhysicalMouseMonitor physicalMouse;
    MouseSnapEventAction eventAction(physicalMouse);
    using Telemetry = vaporlock::io::AsyncTelemetry;
    Telemetry telemetry(GetStdHandle(STD_OUTPUT_HANDLE));
    DatasetWriter datasetWriter(
        {},
        [&](std::string message) {
            telemetry.publish(Telemetry::Kind::Notice, "{\"type\":\"notice\",\"message\":\""
                + wideToUtf8(jsonEscape(narrowToWide(message))) + "\"}");
        }, {}, {}, [&](std::uint64_t count, const std::wstring& path) {
            telemetry.publish(Telemetry::Kind::Dataset, "{\"type\":\"dataset\",\"samples\":" + std::to_string(count)
                + ",\"path\":\"" + wideToUtf8(jsonEscape(path)) + "\"}");
        });
    AimOutputScheduler output(
        [&](const AimReport& report, const AimInputs& input, const AppConfig& settings, AimOutputStatus& status) {
            if (telemetry.failed() && report.active) {
                status.fatalError = L"The control center connection was closed.";
                return false;
            }
            if (usingControllerHost) {
                const bool ok = controllerHostOutput->send(input.controller, report.stickX, report.stickY,
                    settings.virtualControllerMode == 1, settings.controllerOutputStick == 0,
                    report.active && (report.stickX || report.stickY), settings.virtualControllerManualPriority);
                status.controllerReady = controllerHostOutput->connected();
                if (!ok && controllerHostOutput->unavailableTooLong()) status.fatalError = controllerHostOutput->lastError();
                return ok;
            }
            if (usingHardwareOutput && (report.dx || report.dy)) {
                std::wstring error;
                const bool ok = hardwareOutput->sendRelativeMovement(report.dx, report.dy, error);
                if (!ok) status.fatalError = error.empty() ? L"The hardware transport rejected output." : error;
                return ok;
            }
            return usingHardwareOutput ? true : submitWindowsAim(report, input, settings, status);
        }, {},
        [&](const AppConfig& settings, bool authorized, bool insideSnap, AimTime now, AimOutputStatus& status) {
            if (telemetry.failed()) {
                authorized = insideSnap = false;
                status.fatalError = L"The control center connection was closed.";
            }
            const auto result = eventAction.update(settings, authorized, insideSnap, now);
            status.eventHolding = eventAction.held();
            status.eventMonitor = !settings.eventActionEnabled || !authorized || physicalMouse.available();
            status.eventSent = eventAction.sentTotal(); status.eventFailed = eventAction.failedTotal();
            status.eventReason = !settings.eventActionEnabled ? "disabled" : !authorized ? "activation-not-authorized"
                : result.monitorUnavailable ? "physical-input-monitor-unavailable" : result.yieldedToPhysical ? "physical-button-priority"
                : !insideSnap ? "outside-snap-radius" : result.failed ? "windows-rejected"
                : settings.eventActionMode == 1 ? "holding" : result.sent ? "clicked" : "click-cooldown";
        },
        [&](const AppConfig& current, std::uint64_t revision, bool authorized, bool nativeRunning) {
            datasetWriter.observeOutput(current, revision, authorized, nativeRunning && !telemetry.failed());
        });
    LiveConfigPump liveConfig(config, output, [&](std::uint64_t epoch) {
        telemetry.publish(Telemetry::Kind::Control,
            "{\"type\":\"setting-rejected\",\"control_epoch\":" + std::to_string(epoch)
            + ",\"code\":\"SETTING_REJECTED\"}");
    });
    PreviewWorker previewWorker(
        {},
        [&](std::string error) {
            telemetry.publish(Telemetry::Kind::Notice, "{\"type\":\"notice\",\"message\":\""
                + wideToUtf8(jsonEscape(narrowToWide(error))) + "\"}");
        }, {}, [&](std::string line, std::uint64_t revision) {
            liveConfig.withCurrent(revision, [&] {
                telemetry.publish(Telemetry::Kind::Preview, std::move(line));
            });
        });

    output.start();
    const auto fail = [&](std::string line, int code) {
        output.stop();
        if (!datasetWriter.stop()) {
            telemetry.publish(Telemetry::Kind::Notice, "{\"type\":\"notice\",\"message\":\"Collection is still finishing cleanup.\"}");
            while (!datasetWriter.stop()) {} // remain closing; no freed graph/detach
        }
        previewWorker.configure(false);
        telemetry.finish(std::move(line));
        return code;
    };
    XInputReader controllerInput;
    ScopedWinHandle manualLockEvent;
    if (config.manualTargetLockEnabled && !config.manualLockEvent.empty()) {
        manualLockEvent.value = OpenEventW(SYNCHRONIZE, FALSE, config.manualLockEvent.c_str());
    }

    vaporlock::crosshair::Matcher crosshairMatcher;
    vaporlock::crosshair::Reference previousReference;
    while (running) {
        const auto frameStarted = std::chrono::steady_clock::now();
        const auto now = frameStarted;
        const bool timingReady = now - started >= std::chrono::seconds(2);
        if (timingReady && previousLoop >= started + std::chrono::seconds(2))
            pipelineTiming.loopInterval.record(std::chrono::duration<double,std::milli>(now-previousLoop).count());
        previousLoop = now;
        const auto outputGeneration = liveConfig.read(config);
        if(trackingGeneration==outputGeneration)config.frameReference=previousReference;
        const bool cloudCapturePaced=!config.cloudPipe.empty();
        auto frameDeadline = cloudCapturePaced ? frameStarted : frameSchedule.begin(frameStarted,config.captureFps);
        if(cloudCapturePaced && cloudCaptureSchedule.hasFresh()) {
            if(!frameWait.until(cloudCaptureSchedule.next(config.captureFps),liveConfig.changedEvent(),
                               [&]{return telemetry.failed();}))continue;
            if(!liveConfig.current(outputGeneration))continue;
        }
        previewWorker.configure(config.previewEnabled);
        if (!config.previewEnabled) telemetry.clear(Telemetry::Kind::Preview);
        if (!config.overlayMetadata && !config.previewEnabled) telemetry.clear(Telemetry::Kind::Overlay);
        if (telemetry.failed()) return fail("{\"type\":\"error\",\"message\":\"The control center connection was closed.\"}", 1);
        const auto captureStarted = std::chrono::steady_clock::now();
        const auto captureCancelled=[&] {
            return telemetry.failed() || !liveConfig.current(outputGeneration);
        };
        const bool captureOk=cloudCapturePaced ? capture.capture(config,4,captureCancelled) : capture.capture(config);
        if(!captureOk)return fail("{\"type\":\"error\",\"message\":\"Desktop capture failed\"}",1);
        const auto afterCapture=std::chrono::steady_clock::now();
        if(cloudCapturePaced && capture.freshFrame() && !cloudCaptureSchedule.commit(capture.acquiredAt(),config.captureFps))
            return fail("{\"type\":\"error\",\"message\":\"Cloud capture violated its fresh-frame rate boundary.\"}",1);
        if(cloudCapturePaced && captureCancelled()) {++retiredCaptureAttempts;continue;}
        const auto sourceIdentity = capture.frameIdentity(outputGeneration);
        const bool sourceChanged = capture.freshFrame() && (trackingSource != sourceIdentity.sourceGeneration
            || trackingGeneration != outputGeneration);
        if (sourceChanged) {
            // Invalidate queued encoding outside the config lock (the encoder
            // publishes under that lock). Then retire the source before inference.
            previewWorker.configure(false);
            telemetry.clear(Telemetry::Kind::Preview);
            liveConfig.withCurrent(outputGeneration, [&] {
                tracker = IoUTracker{}; selector = TargetSelector{};
                motionCompensation = MotionCompensationState{};
                tracks.clear(); selected.reset();
                trackingGeneration = outputGeneration; trackingSource = sourceIdentity.sourceGeneration;
                output.observeSource(sourceIdentity, outputGeneration);
                if (config.overlayMetadata || config.previewEnabled) telemetry.publish(Telemetry::Kind::Overlay,
                    overlayMetadata(capture.width(), capture.height(), {}, {}, false, config,
                        capture.left(), capture.top(), capture.observedAt(), sourceIdentity, {}, true));
            });
            previewWorker.configure(config.previewEnabled);
        }
        if (capture.freshFrame()) {
            if (timingReady) {
                const double wall=std::chrono::duration<double,std::milli>(afterCapture-captureStarted).count();
                pipelineTiming.capture.record(cloudCapturePaced?capture.workMilliseconds():wall);
                pipelineTiming.captureWall.record(wall);
                pipelineTiming.captureAcquire.record(cloudCapturePaced?capture.acquisitionMilliseconds():0.);
                if (previousObservation >= started + std::chrono::seconds(2))
                    pipelineTiming.observationInterval.record(std::chrono::duration<double,std::milli>(capture.observedAt()-previousObservation).count());
            }
            previousObservation = capture.observedAt();
        }
        if (capture.freshFrame()) datasetWriter.observeSource(capture.frameIdentity(outputGeneration));
        ++frames;
        if (capture.freshFrame()) ++freshCaptures;
        else ++noFreshFramePolls;
        const bool needsController = config.activationSource != 0
                                      || (config.manualTargetLockEnabled && config.manualLockSource != 0)
                                      || (usingVirtualXbox && config.virtualControllerMode == 1);
        const std::optional<DWORD> ignoredControllerSlot = usingControllerHost && config.controllerHostVirtualSlot >= 0
                                                             ? std::optional<DWORD>{static_cast<DWORD>(config.controllerHostVirtualSlot)}
                                                             : std::nullopt;
        const ControllerSnapshot controller = needsController
                                                ? controllerInput.sample(config.controllerSlot, config.controllerBinding, ignoredControllerSlot,
                                                    config.controllerTriggerPress, config.controllerTriggerRelease, config.activationSource)
                                                : ControllerSnapshot{};
        const bool manualKeyDown = (GetAsyncKeyState(headlessKeyFromName(config.manualLockKey)) & 0x8000) != 0;
        const bool manualControllerDown = controller.connected && controllerBindingDown(controller.gamepad, config.manualLockControllerBinding);
        const bool manualLockDown = config.manualTargetLockEnabled
                                    && (config.manualLockSource == 1 ? manualControllerDown
                                        : config.manualLockSource == 2 ? (manualKeyDown || manualControllerDown)
                                                                       : manualKeyDown);
        const bool manualLockRequested = manualLockEvent.value
                                         && WaitForSingleObject(manualLockEvent.value, 0) == WAIT_OBJECT_0;
        if ((manualLockDown && !manualLockWasDown) || manualLockRequested) selector.toggleManualLock();
        manualLockWasDown = manualLockDown;
        const bool inferenceEligible = capture.hasCommittedFrame()
            && (capture.freshFrame() || config.modelPath.empty());
        const auto decision = inferenceCadence.evaluate(afterCapture, inferenceEligible,
            config.captureFps, config.inferenceFps, config.frameSkip, true);
        using InferenceDecision = vaporlock::InferenceCadence::Decision;
        if (decision == InferenceDecision::ExplicitSkip) ++explicitSkippedFrames;
        else if (decision == InferenceDecision::RateLimited) ++rateLimitedFrames;
        if (decision == InferenceDecision::Run) {
            std::vector<Detection> currentDetections;
            DetectionBatch<Detection> detectionBatch;
            const auto captureIdentity = capture.frameIdentity(outputGeneration);
            updateFrameReference(config,capture,crosshairMatcher,outputGeneration);
            output.observeReference(config.frameReference,captureIdentity,outputGeneration);
            try {
                detectionBatch = detector.detectBatch(capture.width(), capture.height(), capture.bgra(), config, captureIdentity);
                currentDetections = std::move(detectionBatch.targeting);
            } catch (const std::exception& exception) {
                return fail("{\"type\":\"error\",\"message\":\"Inference failed: " + wideToUtf8(jsonEscape(narrowToWide(exception.what()))) + "\"}", 1);
            }
            const auto afterInference = std::chrono::steady_clock::now();
            // A long model run must not publish an already expired matched reference.
            if(config.frameReference.detected&&!config.frameReference.usable(afterInference)) {
                AppConfig fallback=config;fallback.crosshairEnabled=false;fallback.aimReference=0;
                vaporlock::crosshair::Matcher unused;updateFrameReference(fallback,capture,unused,outputGeneration);
                config.frameReference=fallback.frameReference;config.frameReference.status="lost";
            }
            // Serialize only the stateful CPU commit with config changes.
            // Session::Run and preview encoding never hold this lock.
            const bool accepted = liveConfig.withCurrent(outputGeneration, [&] {
                if (trackingGeneration != outputGeneration || trackingSource != captureIdentity.sourceGeneration) {
                    tracker = IoUTracker{}; selector = TargetSelector{};
                    motionCompensation = MotionCompensationState{};
                    tracks.clear(); selected.reset();
                    trackingGeneration = outputGeneration;
                    trackingSource = captureIdentity.sourceGeneration;
                }
                const auto& r=config.frameReference;
                if(previousReference.detected!=r.detected||previousReference.inside!=r.inside
                    || std::hypot(previousReference.x-r.x,previousReference.y-r.y)>.25) selector=TargetSelector{};
                previousReference=r;
                tracks = tracker.update(currentDetections, config, capture.observedAt());
                selected = config.frameReference.usable(AimClock::now()) ? selector.select(tracks, capture.width(), capture.height(), config) : std::optional<Track>{};
                output.publish(selected, capture.width(), capture.height(), capture.observedAt(), outputGeneration,
                               motionCompensation.update(tracks, config, capture.observedAt()), captureIdentity, config.frameReference);
            });
            if (!accepted) ++obsoleteInferences;
            const auto afterTracking = std::chrono::steady_clock::now();
            detections = static_cast<int>(currentDetections.size());
            captureWallMs=std::chrono::duration<double,std::milli>(afterCapture-captureStarted).count();
            captureAcquireMs=cloudCapturePaced?capture.acquisitionMilliseconds():0.;
            captureMs=cloudCapturePaced?capture.workMilliseconds():captureWallMs;
            inferenceMs = std::chrono::duration<double, std::milli>(afterInference - afterCapture).count();
            preprocessMs = detector.preprocessMilliseconds();
            modelMs = detector.modelMilliseconds();
            postprocessMs = detector.postprocessMilliseconds();
            ++completedInferences;
            if (config.modelPath.empty()) ++syntheticInferences;
            inferenceWorkMs += inferenceMs;
            modelWorkMs += modelMs;
            trackingMs = std::chrono::duration<double, std::milli>(afterTracking - afterInference).count();
            if (timingReady) {
                pipelineTiming.preprocess.record(preprocessMs);
                pipelineTiming.model.record(modelMs);
                pipelineTiming.postprocess.record(postprocessMs);
                pipelineTiming.tracking.record(trackingMs);
                pipelineTiming.observationAge.record(std::chrono::duration<double,std::milli>(afterTracking-capture.observedAt()).count());
                if (previousCompletion >= started + std::chrono::seconds(2))
                    pipelineTiming.completionInterval.record(std::chrono::duration<double,std::milli>(afterTracking-previousCompletion).count());
            }
            previousCompletion = afterTracking;

            if (accepted && config.overlayMetadata) {
                auto line = overlayMetadata(capture.width(), capture.height(), tracks, selected,
                    selector.manuallyLocked(), config, capture.left(), capture.top(), capture.observedAt(),
                    captureIdentity, output.visualSnapshot(captureIdentity));
                liveConfig.withCurrent(outputGeneration, [&] { telemetry.publish(Telemetry::Kind::Overlay, std::move(line)); });
            }

            if (accepted && config.datasetEnabled && capture.freshFrame())
                datasetWriter.admit(capture.width(), capture.height(), capture.bgra(), captureIdentity,
                    detectionBatch.capture, true, capture.observedAt(), capture.capturedAtMs());
            if (accepted && config.previewEnabled && (lastPreview.time_since_epoch().count() == 0 || std::chrono::duration<double>(now - lastPreview).count() >= 1.0 / std::max(1, config.previewFps))) {
                previewWorker.publish({capture.width(), capture.height(), capture.bgra(), tracks, selected, config, outputGeneration,
                    captureIdentity, capture.observedAt()});
                lastPreview = now;
            }
        }

        const auto outputStatus = output.status();
        if (!outputStatus.fatalError.empty()) {
            return fail("{\"type\":\"output-error\",\"message\":\"" + wideToUtf8(jsonEscape(outputStatus.fatalError)) + "\"}", 1);
        }
        const bool activationAuthorized = outputStatus.authorized;
        const bool outputActive = outputStatus.active;
        const bool compensationActive = outputStatus.compensationActive;
        const bool targetInsideSnap = outputStatus.insideSnap;
        const bool eventActionActivationAuthorized = !config.eventActionRequiresActivation || activationAuthorized;
        const char* outputReason = outputStatus.reason.c_str();
        const char* eventActionReason = outputStatus.eventReason.c_str();

        const auto reportNow = std::chrono::steady_clock::now();
        if (reportNow - lastReport >= std::chrono::milliseconds(250)) {
            const auto collectionStatus = datasetWriter.status();
            const double uptime = std::max(0.001, std::chrono::duration<double>(reportNow - started).count());
            const bool virtualControllerReady = usingVirtualXbox && outputStatus.controllerReady;
            const int virtualControllerSlot = usingVirtualXbox && config.controllerHostVirtualSlot >= 0
                ? config.controllerHostVirtualSlot + 1 : 0;
            const bool proxyReady = config.virtualControllerMode == 1
                                    && virtualControllerReady
                                    && virtualControllerSlot == 1
                                    && controller.connected
                                    && controller.slot != 0;
            std::ostringstream report;
            rateSamples.push_back({reportNow, freshCaptures, completedInferences, outputStatus.sent});
            while (rateSamples.size() > 2 && reportNow - rateSamples[1].time >= std::chrono::seconds(1)) rateSamples.pop_front();
            const auto& previousRate = rateSamples.front();
            const double rateSeconds = std::max(0.001, std::chrono::duration<double>(reportNow - previousRate.time).count());
            report << std::fixed << std::setprecision(2)
                      << "{\"type\":\"stats\",\"fps\":" << (freshCaptures - previousRate.captures) / rateSeconds
                      << ",\"capture_fps\":" << (freshCaptures - previousRate.captures) / rateSeconds
                      << ",\"inference_fps\":" << (completedInferences - previousRate.inferences) / rateSeconds
                      << ",\"control_epoch\":" << config.visualEpoch
                      << ",\"output_fps\":" << (outputStatus.sent - previousRate.outputs) / rateSeconds
                      << ",\"capture_total\":" << freshCaptures
                      << ",\"emitted_tick_ms\":" << GetTickCount64()
                      << ",\"inference_total\":" << completedInferences
                      << ",\"synthetic_inference_total\":" << syntheticInferences
                      << ",\"inference_obsolete_total\":" << obsoleteInferences
                      << ",\"inference_device\":\"" << wideToUtf8(jsonEscape(detector.deviceName())) << "\""
                      << ",\"inference_device_index\":" << detector.deviceIndex()
                      << ",\"cpu_placement_count\":" << detector.cpuPlacementCount()
                      << ",\"high_resolution_pacing\":" << (frameWait.highResolution() ? "true" : "false")
                      << ",\"inference_explicit_skip_total\":" << explicitSkippedFrames
                      << ",\"inference_rate_limited_total\":" << rateLimitedFrames
                      << ",\"capture_no_fresh_poll_total\":" << noFreshFramePolls
                      << ",\"uptime_seconds\":" << uptime
                      << ",\"inference_work_ms\":" << inferenceWorkMs
                      << ",\"model_work_ms\":" << modelWorkMs
                      << ",\"capture_ms\":" << captureMs
                      << ",\"inference_ms\":" << inferenceMs
                      << ",\"capture_wall_ms\":" << captureWallMs
                      << ",\"capture_acquire_ms\":" << captureAcquireMs
                      << ",\"capture_config_retired_total\":" << retiredCaptureAttempts
                      << ",\"cloud_fresh_capture_pacing\":" << (cloudCapturePaced?"true":"false")
                      << ",\"preprocess_ms\":" << preprocessMs
                      << ",\"model_ms\":" << modelMs
                      << ",\"postprocess_ms\":" << postprocessMs
                      << ",\"tracking_ms\":" << trackingMs
                      << ",\"detections\":" << detections
                      << ",\"tracks\":" << tracks.size()
                       << ",\"selected_track\":" << (selected ? selected->id : 0)
                       << ",\"selected_class\":" << (selected ? selected->detection.classId : -1)
                       << ",\"selected_confidence\":" << (selected ? selected->detection.confidence : 0.0f)
                       << ",\"selected_missed\":" << (selected ? selected->missed : 0)
                       << ",\"manual_lock\":" << (selector.manuallyLocked() ? "true" : "false")
                       << ",\"controller_connected\":" << (controller.connected ? "true" : "false")
                       << ",\"physical_controller_slot\":" << (controller.connected ? static_cast<int>(controller.slot + 1) : 0)
                       << ",\"output_transport\":\"" << wideToUtf8(vaporlock::hardware::transportWireName(config.outputTransport)) << "\""
                       << ",\"virtual_controller_ready\":" << (virtualControllerReady ? "true" : "false")
                       << ",\"virtual_controller_slot\":" << virtualControllerSlot
                       << ",\"virtual_controller_mode\":\"" << (config.virtualControllerMode == 1 ? "slot0-proxy" : "separate") << "\""
                       << ",\"virtual_controller_backend\":\"xbox-one-hid\""
                       << ",\"controller_proxy_ready\":" << (proxyReady ? "true" : "false")
                       << ",\"observe_only\":" << (config.observeOnly ? "true" : "false")
                       << ",\"output_active\":" << (outputActive ? "true" : "false")
                       << ",\"activation_authorized\":" << (activationAuthorized ? "true" : "false")
                       << ",\"output_reason\":\"" << outputReason << "\""
                       << ",\"motion_compensation_enabled\":" << (config.motionCompensationEnabled ? "true" : "false")
                       << ",\"motion_compensation_active\":" << (compensationActive ? "true" : "false")
                       << ",\"motion_compensation_mode\":\"" << (config.motionCompensationMode == 1 ? "automatic" : config.motionCompensationMode == 2 ? "combined" : "preset") << "\""
                       << ",\"motion_compensation_auto_valid\":" << (motionCompensation.automaticValid ? "true" : "false")
                       << ",\"motion_compensation_auto_tracks\":" << motionCompensation.contributors
                       << ",\"motion_compensation_auto_correction_horizontal\":" << motionCompensation.automaticHorizontalCorrection()
                       << ",\"motion_compensation_auto_correction_vertical\":" << motionCompensation.automaticVerticalCorrection()
                       << ",\"output_sent_total\":" << outputStatus.sent
                       << ",\"output_observation_age_ms\":" << outputStatus.observationAgeMs
                       << ",\"output_history_entries\":" << outputStatus.acceptedMotionEntries
                       << ",\"output_history_complete\":" << (outputStatus.motionHistoryComplete ? "true" : "false")
                       << ",\"output_failed_total\":" << outputStatus.failed
                       << ",\"event_action_enabled\":" << (config.eventActionEnabled ? "true" : "false")
                       << ",\"event_action_requires_activation\":" << (config.eventActionRequiresActivation ? "true" : "false")
                       << ",\"event_action_activation_authorized\":" << (eventActionActivationAuthorized ? "true" : "false")
                       << ",\"event_action_inside_snap\":" << (targetInsideSnap ? "true" : "false")
                       << ",\"event_action_holding\":" << (outputStatus.eventHolding ? "true" : "false")
                       << ",\"event_action_physical_monitor\":" << (outputStatus.eventMonitor ? "true" : "false")
                       << ",\"event_action_mode\":\"" << (config.eventActionMode == 1 ? "hold" : "click") << "\""
                       << ",\"event_action_reason\":\"" << eventActionReason << "\""
                       << ",\"event_action_sent_total\":" << outputStatus.eventSent
                       << ",\"event_action_failed_total\":" << outputStatus.eventFailed
                       << ",\"collection_status_known\":" << (collectionStatus ? "true" : "false");
            if (collectionStatus) {
                report << ",\"collection_saved\":" << collectionStatus->saved
                       << ",\"collection_saved_bytes\":" << collectionStatus->savedBytes
                       << ",\"collection_limit_reason\":\"" << collectionStatus->limitReason << "\""
                       << ",\"collection_dropped\":" << collectionStatus->dropped
                       << ",\"collection_pending\":" << (collectionStatus->pending ? "true" : "false")
                       << ",\"collection_encoding\":" << (collectionStatus->encoding ? "true" : "false")
                       << ",\"collection_root_ready\":" << (collectionStatus->rootReady ? "true" : "false")
                       << ",\"collection_cleanup_pending\":" << (collectionStatus->cleanupPending ? "true" : "false")
                       << ",\"collection_retirement_pending\":" << (collectionStatus->retirementPending ? "true" : "false")
                       << ",\"collection_qualification_unavailable\":" << (collectionStatus->qualificationUnavailable ? "true" : "false")
                       << ",\"collection_error\":\"" << collectionStatus->error << "\""
                       << ",\"collection_owned_bgra_bytes\":" << collectionStatus->ownedBgraBytes
                       << ",\"collection_highwater_bgra_bytes\":" << collectionStatus->highWaterBgraBytes;
            }
            report << ",\"configured_capture_fps\":" << config.captureFps
                   << ",\"configured_inference_fps\":" << config.inferenceFps
                   << ",\"configured_frame_skip\":" << config.frameSkip
                   << ",\"crosshair_status\":\"" << config.frameReference.status << "\""
                   << ",\"emitted_tick_ms\":" << GetTickCount64()
                   << ",\"crosshair_age_ms\":" << (config.frameReference.detected ? std::chrono::duration<double,std::milli>(AimClock::now()-config.frameReference.matched).count() : 0.)
                   << ",\"crosshair_detected\":" << (config.frameReference.detected ? "true" : "false")
                   << ",\"reference_inside\":" << (config.frameReference.inside ? "true" : "false")
                   << ",\"reference_x\":" << config.frameReference.x << ",\"reference_y\":" << config.frameReference.y
                   << ",\"capture_width\":" << capture.width() << ",\"capture_height\":" << capture.height()
                   << ",\"capture_backend\":\"" << capture.backend() << "\""
                   << ",\"capture_recovery_reason\":\"" << capture.recoveryReason() << "\""
                   << ",\"capture_recovery_probes\":" << capture.recoveryProbes()
                   << ",\"capture_recovery_transitions\":" << capture.recoveryTransitions()
                   << ",\"capture_dxgi_timeouts\":" << capture.dxgiTimeouts()
                   << ",\"capture_dxgi_frames\":" << capture.dxgiFrames()
                   << ",\"timing\":";
            pipelineTiming.json(report);
            report << ",\"model\":\"" << (config.modelPath.empty() ? "test signal" : "loaded") << "\"}";
            telemetry.publish(Telemetry::Kind::Stats, report.str());
            lastReport = reportNow;
        }

        if(cloudCapturePaced) {
            // A zero/pointer-only return must neither consume a fresh-frame
            // rate slot nor cause a spin. A normal 4ms source wait needs no
            // extra delay; a very short unsuccessful call gets a bounded retry.
            if(!capture.freshFrame() && afterCapture-captureStarted<std::chrono::milliseconds(1))
                frameWait.until(AimClock::now()+std::chrono::milliseconds(1),liveConfig.changedEvent(),[&]{return telemetry.failed();});
            continue;
        }
        int pacingFps = config.captureFps;
        while (!frameWait.until(frameDeadline, liveConfig.changedEvent(), [&] { return telemetry.failed(); })) {
            if (telemetry.failed()) break;
            const int updatedFps = liveConfig.captureFps();
            if (updatedFps != pacingFps) {
                // Recompute from this acquisition's start, not the wake time.
                // Same-rate changes cannot create extra acquisitions or move
                // the existing deadline farther into the future.
                frameSchedule.reset();
                frameDeadline = frameSchedule.begin(frameStarted, updatedFps);
                pacingFps = updatedFps;
            }
        }
    }
    output.stop();
    if (!datasetWriter.stop()) {
        telemetry.publish(Telemetry::Kind::Notice, "{\"type\":\"notice\",\"message\":\"Collection is still finishing cleanup.\"}");
        while (!datasetWriter.stop()) {} // owner and callbacks remain alive
    }
    return 0;
}

struct LabelRow { int y; std::wstring label; std::wstring detail; };

class MainWindow {
public:
    int run(HINSTANCE instance, int show) {
        m_instance = instance;
        SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE);
        INITCOMMONCONTROLSEX common{sizeof(INITCOMMONCONTROLSEX), ICC_STANDARD_CLASSES | ICC_BAR_CLASSES};
        InitCommonControlsEx(&common);
        WNDCLASSW windowClass{};
        windowClass.lpfnWndProc = windowProc;
        windowClass.hInstance = instance;
        windowClass.lpszClassName = L"VaporLockMainWindow";
        windowClass.hCursor = LoadCursor(nullptr, IDC_ARROW);
        RegisterClassW(&windowClass);
        m_hwnd = CreateWindowExW(0, windowClass.lpszClassName, L"Vapor Lock", WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
            CW_USEDEFAULT, CW_USEDEFAULT, 1480, 920, nullptr, nullptr, instance, this);
        ShowWindow(m_hwnd, show);
        UpdateWindow(m_hwnd);
        MSG message{};
        while (GetMessageW(&message, nullptr, 0, 0)) { TranslateMessage(&message); DispatchMessageW(&message); }
        return static_cast<int>(message.wParam);
    }

private:
    static LRESULT CALLBACK windowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
        MainWindow* self = nullptr;
        if (message == WM_NCCREATE) {
            self = static_cast<MainWindow*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
            self->m_hwnd = hwnd;
        } else {
            self = reinterpret_cast<MainWindow*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        }
        return self ? self->handle(message, wparam, lparam) : DefWindowProcW(hwnd, message, wparam, lparam);
    }

    LRESULT handle(UINT message, WPARAM wparam, LPARAM lparam) {
        switch (message) {
        case WM_CREATE: createControls(); loadDefaultProfile(); appendLog(L"Vapor Lock is ready"); return 0;
        case WM_SIZE: layout(); return 0;
        case WM_GETMINMAXINFO: reinterpret_cast<MINMAXINFO*>(lparam)->ptMinTrackSize = {1100, 700}; return 0;
        case WM_COMMAND: command(LOWORD(wparam), HIWORD(wparam)); return 0;
        case WM_TIMER: if (wparam == kFrameTimer) step(); return 0;
        case WM_DRAWITEM: drawButton(reinterpret_cast<DRAWITEMSTRUCT*>(lparam)); return TRUE;
        case WM_CTLCOLORSTATIC: return controlColor(reinterpret_cast<HDC>(wparam), false);
        case WM_CTLCOLOREDIT: return controlColor(reinterpret_cast<HDC>(wparam), true);
        case WM_CTLCOLORLISTBOX: return controlColor(reinterpret_cast<HDC>(wparam), true);
        case WM_PAINT: paint(); return 0;
        case WM_DESTROY: KillTimer(m_hwnd, kFrameTimer); destroyResources(); PostQuitMessage(0); return 0;
        default: return DefWindowProcW(m_hwnd, message, wparam, lparam);
        }
    }

    HWND button(const wchar_t* text, int id) {
        HWND result = CreateWindowW(L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_OWNERDRAW, 0, 0, 100, 32, m_hwnd,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), m_instance, nullptr);
        SendMessageW(result, WM_SETFONT, reinterpret_cast<WPARAM>(m_font), TRUE);
        return result;
    }
    HWND edit(const wchar_t* text, int id, bool readOnly = false) {
        DWORD style = WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL;
        if (readOnly) style |= ES_READONLY;
        HWND result = CreateWindowW(L"EDIT", text, style, 0, 0, 100, 28, m_hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), m_instance, nullptr);
        SendMessageW(result, WM_SETFONT, reinterpret_cast<WPARAM>(m_font), TRUE);
        return result;
    }
    HWND combo(int id) {
        HWND result = CreateWindowW(L"COMBOBOX", L"", WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL, 0, 0, 100, 180, m_hwnd,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), m_instance, nullptr);
        SendMessageW(result, WM_SETFONT, reinterpret_cast<WPARAM>(m_font), TRUE);
        return result;
    }
    HWND check(const wchar_t* text, int id) {
        HWND result = CreateWindowW(L"BUTTON", text, WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX, 0, 0, 100, 26, m_hwnd,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), m_instance, nullptr);
        SendMessageW(result, WM_SETFONT, reinterpret_cast<WPARAM>(m_font), TRUE);
        return result;
    }

    void createControls() {
        m_font = CreateFontW(17, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        m_smallFont = CreateFontW(14, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        m_titleFont = CreateFontW(28, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        m_panelBrush = CreateSolidBrush(kPanel);
        m_fieldBrush = CreateSolidBrush(kField);
        m_sidebarBrush = CreateSolidBrush(kSidebar);
        m_run = button(L"Start capture", IdRun);
        m_stop = button(L"Stop", IdStop);
        m_nav = {button(L"Live view", IdNavLive), button(L"Detection", IdNavDetection), button(L"Capture", IdNavCapture), button(L"Tracking", IdNavTracking), button(L"Selection", IdNavSelection), button(L"Output", IdNavOutput), button(L"Overlay", IdNavOverlay), button(L"Profiles", IdNavProfiles), button(L"Diagnostics", IdNavDiagnostics)};
        m_modelPath = edit(L"", IdModelPath, true); m_browseModel = button(L"Browse...", IdBrowseModel); m_unloadModel = button(L"Unload", IdUnloadModel);
        m_confidence = edit(L"0.55", IdConfidence); m_nmsIou = edit(L"0.45", IdNmsIou); m_classFilter = edit(L"", IdClassFilter);
        m_captureSource = combo(IdCaptureSource); SendMessageW(m_captureSource, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"All displays")); SendMessageW(m_captureSource, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Primary display"));
        m_captureFps = edit(L"30", IdCaptureFps); m_inferenceFps = edit(L"30", IdInferenceFps); m_frameSkip = edit(L"0", IdFrameSkip);
        m_trackIou = edit(L"0.30", IdTrackIou); m_smoothing = edit(L"0.35", IdSmoothing); m_maxMissed = edit(L"18", IdMaxMissed); m_predictLost = check(L"Predict tracks between detections", IdPredictLost);
        m_strategy = combo(IdStrategy); for (int i = 0; i < 5; ++i) SendMessageW(m_strategy, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(strategyName(i).c_str()));
        m_acquireRadius = edit(L"420", IdAcquireRadius); m_retainRadius = edit(L"540", IdRetainRadius); m_switching = edit(L"0.18", IdSwitching);
        m_movement = check(L"Enable relative mouse output", IdMovement);
        m_activationMode = combo(IdActivationMode); SendMessageW(m_activationMode, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Hold key")); SendMessageW(m_activationMode, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(L"Toggle key"));
        m_activationKey = combo(IdActivationKey); for (const auto* key : {L"Right mouse button", L"Left Shift", L"Left Control", L"Space"}) SendMessageW(m_activationKey, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(key));
        m_gain = edit(L"0.18", IdGain); m_maxStep = edit(L"24", IdMaxStep); m_deadzone = edit(L"8", IdDeadzone);
        m_overlay = check(L"Show tracking overlay", IdOverlay); m_overlayBoxes = check(L"Bounding boxes", IdOverlayBoxes); m_overlayLabels = check(L"Class labels", IdOverlayLabels); m_overlayConfidence = check(L"Confidence values", IdOverlayConfidence); m_overlayIds = check(L"Track identities", IdOverlayIds); m_overlayMotion = check(L"Prediction vectors", IdOverlayMotion);
        m_profileName = edit(L"Default", IdProfileName); m_loadProfile = button(L"Import profile", IdLoadProfile); m_saveProfile = button(L"Save as...", IdSaveProfile); m_resetProfile = button(L"Reset settings", IdResetProfile);
        m_clearLog = button(L"Clear log", IdClearLog); m_copyDiagnostics = button(L"Copy diagnostics", IdCopyDiagnostics);
        m_log = CreateWindowW(L"EDIT", L"", WS_CHILD | ES_MULTILINE | ES_READONLY | WS_VSCROLL | ES_AUTOVSCROLL, 0, 0, 100, 100, m_hwnd, nullptr, m_instance, nullptr);
        SendMessageW(m_log, WM_SETFONT, reinterpret_cast<WPARAM>(m_smallFont), TRUE);
        pushConfig();
    }

    std::vector<HWND> controls() const {
        return {m_modelPath, m_browseModel, m_unloadModel, m_confidence, m_nmsIou, m_classFilter, m_captureSource, m_captureFps, m_inferenceFps, m_frameSkip, m_trackIou, m_smoothing, m_maxMissed, m_predictLost, m_strategy, m_acquireRadius, m_retainRadius, m_switching, m_movement, m_activationMode, m_activationKey, m_gain, m_maxStep, m_deadzone, m_overlay, m_overlayBoxes, m_overlayLabels, m_overlayConfidence, m_overlayIds, m_overlayMotion, m_profileName, m_loadProfile, m_saveProfile, m_resetProfile, m_clearLog, m_copyDiagnostics, m_log};
    }
    void show(HWND control, bool visible) { ShowWindow(control, visible ? SW_SHOW : SW_HIDE); }
    void hidePageControls() {
        for (HWND control : controls()) show(control, false);
        m_labels.clear();
    }
    void layout() {
        if (!m_hwnd) return;
        RECT window{};
        GetClientRect(m_hwnd, &window);
        const int sidebarWidth = 220;
        const int headerHeight = 78;
        const int statusHeight = 32;
        MoveWindow(m_run, sidebarWidth + 24, 20, 132, 38, TRUE);
        MoveWindow(m_stop, sidebarWidth + 166, 20, 86, 38, TRUE);
        int navigationY = 114;
        for (HWND navigation : m_nav) { MoveWindow(navigation, 14, navigationY, sidebarWidth - 28, 38, TRUE); navigationY += 45; }
        m_content = {sidebarWidth + 24, headerHeight + 24, window.right - 24, window.bottom - statusHeight - 18};
        hidePageControls();
        if (m_page == Page::Live) layoutLive();
        else if (m_page == Page::Detection) layoutDetection();
        else if (m_page == Page::Capture) layoutCapture();
        else if (m_page == Page::Tracking) layoutTracking();
        else if (m_page == Page::Selection) layoutSelection();
        else if (m_page == Page::Output) layoutOutput();
        else if (m_page == Page::Overlay) layoutOverlay();
        else if (m_page == Page::Profiles) layoutProfiles();
        else layoutDiagnostics();
        InvalidateRect(m_hwnd, nullptr, TRUE);
    }
    void title(const std::wstring& heading, const std::wstring& subheading) { m_heading = heading; m_subheading = subheading; }
    void settingsPanel() { m_settingsPanel = {m_content.left, m_content.top + 74, std::min(m_content.left + 820, m_content.right), m_content.bottom}; }
    void row(const std::wstring& label, const std::wstring& detail, HWND control, int& y, int width = 310) {
        m_labels.push_back({y, label, detail});
        MoveWindow(control, m_content.left + 465, y - 4, width, 30, TRUE);
        show(control, true);
        y += 72;
    }
    void checkRow(const std::wstring& detail, HWND control, int& y) {
        MoveWindow(control, m_content.left + 18, y, 360, 28, TRUE);
        show(control, true);
        m_labels.push_back({y + 28, L"", detail});
        y += 76;
    }
    RECT previewSide() const { return {m_content.left + 850, m_content.top + 74, m_content.right, m_content.bottom}; }
    void layoutLive() { title(L"Live monitoring", L"Desktop capture, model inference, and tracked output in one place."); m_preview = {m_content.left, m_content.top + 74, m_content.right, m_content.bottom}; }
    void layoutDetection() {
        title(L"Detection", L"Load a model and control which detections enter the tracking system.");
        settingsPanel(); int y = m_content.top + 100;
        row(L"Model", L"ONNX model used for detection", m_modelPath, y, 145);
        MoveWindow(m_browseModel, m_content.left + 620, y - 76, 100, 30, TRUE); show(m_browseModel, true);
        MoveWindow(m_unloadModel, m_content.left + 730, y - 76, 78, 30, TRUE); show(m_unloadModel, true);
        row(L"Confidence threshold", L"Discard detections below this confidence", m_confidence, y);
        row(L"NMS overlap", L"Remove duplicate boxes above this overlap", m_nmsIou, y);
        row(L"Allowed class IDs", L"Comma-separated IDs; leave blank for all classes", m_classFilter, y);
        m_preview = previewSide();
    }
    void layoutCapture() {
        title(L"Capture", L"Choose the desktop surface and pace the processing loop.");
        settingsPanel(); int y = m_content.top + 100;
        row(L"Desktop surface", L"All displays includes the entire virtual desktop", m_captureSource, y);
        row(L"Capture FPS", L"How often Vapor Lock reads a desktop frame", m_captureFps, y);
        row(L"Inference FPS", L"Maximum model evaluations per second", m_inferenceFps, y);
        row(L"Frame skip", L"Extra captured frames to skip before inference", m_frameSkip, y);
        m_preview = previewSide();
    }
    void layoutTracking() {
        title(L"Tracking", L"Tune identity continuity and motion estimation.");
        settingsPanel(); int y = m_content.top + 100;
        row(L"Match overlap", L"Minimum overlap needed to preserve an identity", m_trackIou, y);
        row(L"Motion smoothing", L"Higher values dampen velocity changes", m_smoothing, y);
        row(L"Lost frame limit", L"Frames retained after a target disappears", m_maxMissed, y);
        checkRow(L"Continue a lost track along its estimated motion", m_predictLost, y);
        m_preview = previewSide();
    }
    void layoutSelection() {
        title(L"Target selection", L"Choose how Vapor Lock acquires and keeps a tracked object.");
        settingsPanel(); int y = m_content.top + 100;
        row(L"Selection method", L"Priority rule applied to visible tracks", m_strategy, y);
        row(L"Acquisition radius", L"Maximum center distance for a new target", m_acquireRadius, y);
        row(L"Retention radius", L"Maximum center distance before losing a lock", m_retainRadius, y);
        row(L"Switching hysteresis", L"Higher values resist switching targets", m_switching, y);
        m_preview = previewSide();
    }
    void layoutOutput() {
        title(L"Output", L"Optional relative mouse movement for compatible camera-control software.");
        settingsPanel(); int y = m_content.top + 100;
        checkRow(L"Disabled by default. Output requires a selected target and activation.", m_movement, y);
        row(L"Activation mode", L"Hold keeps output active while a key is down; Toggle latches it", m_activationMode, y);
        row(L"Activation key", L"Key that authorizes movement output", m_activationKey, y);
        row(L"Movement gain", L"How strongly target offset becomes movement", m_gain, y);
        row(L"Maximum step", L"Safety cap for one output update", m_maxStep, y);
        row(L"Dead zone", L"Ignore target offsets within this many pixels", m_deadzone, y);
        m_preview = previewSide();
    }
    void layoutOverlay() {
        title(L"Overlay", L"Control what the live preview reveals about the tracking state.");
        settingsPanel(); int y = m_content.top + 100;
        checkRow(L"Hide every tracking annotation from the preview", m_overlay, y);
        checkRow(L"Draw a box around each active track", m_overlayBoxes, y);
        checkRow(L"Show the detector class label", m_overlayLabels, y);
        checkRow(L"Show detector confidence", m_overlayConfidence, y);
        checkRow(L"Show persistent track identities", m_overlayIds, y);
        checkRow(L"Show estimated motion direction", m_overlayMotion, y);
        m_preview = previewSide();
    }
    void layoutProfiles() {
        title(L"Profiles", L"Store a complete working configuration as a portable JSON profile.");
        settingsPanel(); int y = m_content.top + 100;
        row(L"Profile name", L"Name saved inside the profile", m_profileName, y);
        MoveWindow(m_loadProfile, m_content.left + 18, y + 4, 150, 34, TRUE);
        MoveWindow(m_saveProfile, m_content.left + 178, y + 4, 130, 34, TRUE);
        MoveWindow(m_resetProfile, m_content.left + 318, y + 4, 150, 34, TRUE);
        show(m_loadProfile, true); show(m_saveProfile, true); show(m_resetProfile, true);
        m_preview = previewSide();
    }
    void layoutDiagnostics() {
        title(L"Diagnostics", L"Live runtime information and a timestamped event log.");
        m_settingsPanel = {m_content.left, m_content.top + 74, m_content.right, m_content.bottom};
        MoveWindow(m_clearLog, m_content.left, m_content.top + 76, 116, 32, TRUE);
        MoveWindow(m_copyDiagnostics, m_content.left + 126, m_content.top + 76, 158, 32, TRUE);
        show(m_clearLog, true); show(m_copyDiagnostics, true);
        MoveWindow(m_log, m_content.left, m_content.top + 122, m_content.right - m_content.left, m_content.bottom - (m_content.top + 122), TRUE);
        show(m_log, true);
        m_preview = {0, 0, 0, 0};
    }

    void command(int id, int event) {
        if (id == IdRun && event == BN_CLICKED) start();
        else if (id == IdStop && event == BN_CLICKED) stop();
        else if (id >= IdNavLive && id <= IdNavDiagnostics && event == BN_CLICKED) { m_page = static_cast<Page>(id - IdNavLive); layout(); }
        else if (id == IdBrowseModel && event == BN_CLICKED) loadModelDialog();
        else if (id == IdUnloadModel && event == BN_CLICKED) { m_detector.clearModel(); m_config.modelPath.clear(); pushConfig(); appendLog(L"Model unloaded; test signal restored"); }
        else if (id == IdLoadProfile && event == BN_CLICKED) loadProfileDialog();
        else if (id == IdSaveProfile && event == BN_CLICKED) saveProfileDialog();
        else if (id == IdResetProfile && event == BN_CLICKED) resetProfile();
        else if (id == IdClearLog && event == BN_CLICKED) { m_logText.clear(); SetWindowTextW(m_log, L""); }
        else if (id == IdCopyDiagnostics && event == BN_CLICKED) copyDiagnostics();
        else if (event == EN_KILLFOCUS || event == CBN_SELCHANGE || event == BN_CLICKED) {
            pullConfig();
            m_outputGeneration = m_output.configure(m_config);
            if (id == IdCaptureFps && m_running) refreshTimer();
            InvalidateRect(m_hwnd, nullptr, FALSE);
        }
    }

    void start() {
        if (m_running) return;
        pullConfig();
        pushConfig();
        m_capture.restartSession();
        m_running = true;
        m_frames = 0;
        m_started = std::chrono::steady_clock::now();
        m_inferenceCadence.reset();
        m_tracker = IoUTracker{};
        m_selector = TargetSelector{};
        m_selected.reset();
        m_outputGeneration = m_output.configure(m_config);
        m_output.start();
        refreshTimer();
        appendLog(L"Capture pipeline started");
        InvalidateRect(m_hwnd, nullptr, FALSE);
    }
    void stop() {
        m_output.stop();
        if (!m_running) return;
        m_running = false;
        KillTimer(m_hwnd, kFrameTimer);
        m_stats.outputActive = false;
        appendLog(L"Capture pipeline stopped");
        InvalidateRect(m_hwnd, nullptr, FALSE);
    }
    void refreshTimer() {
        KillTimer(m_hwnd, kFrameTimer);
        SetTimer(m_hwnd, kFrameTimer, static_cast<UINT>(std::max(1, 1000 / std::max(1, m_config.captureFps))), nullptr);
    }

    std::wstring chooseFile(const wchar_t* title, const wchar_t* filter, bool save) {
        wchar_t path[MAX_PATH]{};
        OPENFILENAMEW dialog{};
        dialog.lStructSize = sizeof(dialog);
        dialog.hwndOwner = m_hwnd;
        dialog.lpstrFile = path;
        dialog.nMaxFile = MAX_PATH;
        dialog.lpstrTitle = title;
        dialog.lpstrFilter = filter;
        dialog.Flags = OFN_PATHMUSTEXIST | (save ? OFN_OVERWRITEPROMPT : OFN_FILEMUSTEXIST);
        return (save ? GetSaveFileNameW(&dialog) : GetOpenFileNameW(&dialog)) ? std::wstring(path) : L"";
    }
    void loadModelDialog() {
        m_output.clear();
        const auto path = chooseFile(L"Load ONNX model", L"ONNX Models\0*.onnx\0All Files\0*.*\0", false);
        if (path.empty()) return;
        std::wstring error;
        if (!m_detector.loadModel(path, m_config.provider, m_config.ortIntraOpThreads, m_config.ortInterOpThreads, m_config.cudaGraphReplay, m_config.cudaPreprocess, error, m_config.inferenceDevice, m_config.cpuSets)) { MessageBoxW(m_hwnd, error.c_str(), L"Could not load model", MB_ICONWARNING); return; }
        m_config.modelPath = path;
        pushConfig();
        appendLog(L"Model loaded: " + std::filesystem::path(path).filename().wstring());
    }
    void loadProfileDialog() {
        const auto path = chooseFile(L"Import profile", L"JSON Profiles\0*.json\0All Files\0*.*\0", false);
        if (!path.empty()) loadProfile(path);
    }
    void saveProfileDialog() {
        pullConfig();
        const auto path = chooseFile(L"Save profile", L"JSON Profiles\0*.json\0All Files\0*.*\0", true);
        if (!path.empty()) saveProfile(path);
    }

    static std::wstring value(HWND control) {
        wchar_t buffer[1024]{};
        GetWindowTextW(control, buffer, 1024);
        return buffer;
    }
    static bool isChecked(HWND control) { return SendMessageW(control, BM_GETCHECK, 0, 0) == BST_CHECKED; }
    static void setChecked(HWND control, bool checked) { SendMessageW(control, BM_SETCHECK, checked ? BST_CHECKED : BST_UNCHECKED, 0); }
    static double number(HWND control, double fallback, double low, double high) {
        wchar_t buffer[64]{};
        GetWindowTextW(control, buffer, 64);
        wchar_t* end = nullptr;
        double result = wcstod(buffer, &end);
        if (end == buffer) result = fallback;
        return std::clamp(result, low, high);
    }
    static int integer(HWND control, int fallback, int low, int high) {
        return static_cast<int>(std::lround(number(control, fallback, low, high)));
    }
    static void setNumber(HWND control, double value, int decimals) {
        wchar_t buffer[64]{};
        if (decimals == 0) swprintf_s(buffer, L"%.0f", value);
        else swprintf_s(buffer, L"%.*f", decimals, value);
        SetWindowTextW(control, buffer);
    }

    void pullConfig() {
        m_config.confidence = number(m_confidence, m_config.confidence, 0.01, 1.0);
        m_config.iou = number(m_nmsIou, m_config.iou, 0.01, 1.0);
        m_config.classFilter = value(m_classFilter);
        m_config.captureMonitor = SendMessageW(m_captureSource, CB_GETCURSEL, 0, 0) == 1 ? 1 : 0;
        m_config.captureFps = integer(m_captureFps, m_config.captureFps, 1, 240);
        m_config.inferenceFps = integer(m_inferenceFps, m_config.inferenceFps, 1, 240);
        m_config.frameSkip = integer(m_frameSkip, m_config.frameSkip, 0, 60);
        m_config.matchIou = number(m_trackIou, m_config.matchIou, 0.01, 1.0);
        m_config.smoothing = number(m_smoothing, m_config.smoothing, 0.0, 0.95);
        m_config.maxMissed = integer(m_maxMissed, m_config.maxMissed, 0, 600);
        m_config.predictLost = isChecked(m_predictLost);
        m_config.strategy = std::clamp(static_cast<int>(SendMessageW(m_strategy, CB_GETCURSEL, 0, 0)), 0, 4);
        m_config.acquisitionRadius = integer(m_acquireRadius, m_config.acquisitionRadius, 1, 10000);
        m_config.retentionRadius = integer(m_retainRadius, m_config.retentionRadius, 1, 10000);
        m_config.switchingThreshold = number(m_switching, m_config.switchingThreshold, 0.0, 0.95);
        m_config.movementEnabled = isChecked(m_movement);
        m_config.activationMode = std::clamp(static_cast<int>(SendMessageW(m_activationMode, CB_GETCURSEL, 0, 0)), 0, 1);
        m_config.activationKey = std::clamp(static_cast<int>(SendMessageW(m_activationKey, CB_GETCURSEL, 0, 0)), 0, 3);
        m_config.movementGain = number(m_gain, m_config.movementGain, 0.0, 4.0);
        m_config.maxStep = integer(m_maxStep, m_config.maxStep, 1, 500);
        m_config.deadzone = integer(m_deadzone, m_config.deadzone, 0, 500);
        m_config.overlayEnabled = isChecked(m_overlay);
        m_config.overlayBoxes = isChecked(m_overlayBoxes);
        m_config.overlayLabels = isChecked(m_overlayLabels);
        m_config.overlayConfidence = isChecked(m_overlayConfidence);
        m_config.overlayIds = isChecked(m_overlayIds);
        m_config.overlayMotion = isChecked(m_overlayMotion);
        m_config.profileName = value(m_profileName);
    }

    void pushConfig() {
        setNumber(m_confidence, m_config.confidence, 2);
        setNumber(m_nmsIou, m_config.iou, 2);
        SetWindowTextW(m_classFilter, m_config.classFilter.c_str());
        SendMessageW(m_captureSource, CB_SETCURSEL, m_config.captureMonitor == 1 ? 1 : 0, 0);
        setNumber(m_captureFps, m_config.captureFps, 0);
        setNumber(m_inferenceFps, m_config.inferenceFps, 0);
        setNumber(m_frameSkip, m_config.frameSkip, 0);
        setNumber(m_trackIou, m_config.matchIou, 2);
        setNumber(m_smoothing, m_config.smoothing, 2);
        setNumber(m_maxMissed, m_config.maxMissed, 0);
        setChecked(m_predictLost, m_config.predictLost);
        SendMessageW(m_strategy, CB_SETCURSEL, m_config.strategy, 0);
        setNumber(m_acquireRadius, m_config.acquisitionRadius, 0);
        setNumber(m_retainRadius, m_config.retentionRadius, 0);
        setNumber(m_switching, m_config.switchingThreshold, 2);
        setChecked(m_movement, m_config.movementEnabled);
        SendMessageW(m_activationMode, CB_SETCURSEL, m_config.activationMode, 0);
        SendMessageW(m_activationKey, CB_SETCURSEL, m_config.activationKey, 0);
        setNumber(m_gain, m_config.movementGain, 2);
        setNumber(m_maxStep, m_config.maxStep, 0);
        setNumber(m_deadzone, m_config.deadzone, 0);
        setChecked(m_overlay, m_config.overlayEnabled);
        setChecked(m_overlayBoxes, m_config.overlayBoxes);
        setChecked(m_overlayLabels, m_config.overlayLabels);
        setChecked(m_overlayConfidence, m_config.overlayConfidence);
        setChecked(m_overlayIds, m_config.overlayIds);
        setChecked(m_overlayMotion, m_config.overlayMotion);
        SetWindowTextW(m_profileName, m_config.profileName.c_str());
        SetWindowTextW(m_modelPath, m_config.modelPath.empty() ? L"No ONNX model loaded" : m_config.modelPath.c_str());
        m_outputGeneration = m_output.configure(m_config);
        layout();
    }

    void step() {
        const auto started = std::chrono::steady_clock::now();
        if (!m_capture.capture(m_config)) { appendLog(L"Desktop capture failed"); return; }
        if (!m_capture.hasCommittedFrame()) return;
        updateFrameReference(m_config,m_capture,m_crosshairMatcher,m_outputGeneration);
        m_output.observeReference(m_config.frameReference,m_capture.frameIdentity(m_outputGeneration),m_outputGeneration);
        const auto identity = m_capture.frameIdentity(m_outputGeneration);
        if (m_capture.freshFrame() && identity.sourceGeneration != m_trackingSource) {
            m_trackingSource = identity.sourceGeneration;
            m_tracker = IoUTracker{}; m_selector = TargetSelector{};
            m_tracks.clear(); m_selected.reset(); m_lastDetections.clear();
            m_output.observeSource(identity, m_outputGeneration);
        }
        const auto afterCapture = std::chrono::steady_clock::now();
        ++m_frames;
        const auto now = std::chrono::steady_clock::now();
        m_stats.inferenceRan = m_inferenceCadence.evaluate(afterCapture, m_capture.freshFrame(),
            m_config.captureFps, m_config.inferenceFps, m_config.frameSkip, false)
            == vaporlock::InferenceCadence::Decision::Run;
        if (m_stats.inferenceRan) {
            try {
                m_lastDetections = m_detector.detect(m_capture.width(), m_capture.height(), m_capture.bgra(), m_config);
            } catch (const std::exception& exception) {
                stop();
                appendLog(L"Inference failed: " + narrowToWide(exception.what()));
                MessageBoxW(m_hwnd, narrowToWide(exception.what()).c_str(), L"Inference failed", MB_ICONWARNING);
                return;
            }
            const auto afterDetect = std::chrono::steady_clock::now();
            m_tracks = m_tracker.update(m_lastDetections, m_config, m_capture.observedAt());
            m_selected = m_config.frameReference.usable(AimClock::now()) ? m_selector.select(m_tracks, m_capture.width(), m_capture.height(), m_config) : std::optional<Track>{};
            m_stats.detectMs = std::chrono::duration<double, std::milli>(afterDetect - afterCapture).count();
            m_stats.trackMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - afterDetect).count();
        } else {
            m_stats.detectMs = 0.0;
            m_stats.trackMs = 0.0;
        }
        emitMovement();
        const double runtime = std::max(0.001, std::chrono::duration<double>(now - m_started).count());
        m_stats.fps = m_frames / runtime;
        m_stats.captureMs = std::chrono::duration<double, std::milli>(afterCapture - started).count();
        m_stats.detections = static_cast<int>(m_lastDetections.size());
        m_stats.tracks = static_cast<int>(m_tracks.size());
        // Only the image surface changes per frame. Repainting the entire window
        // caused visible flashing of navigation and settings controls.
        if (m_preview.right > m_preview.left && m_preview.bottom > m_preview.top) {
            InvalidateRect(m_hwnd, &m_preview, FALSE);
        }
        RECT client{};
        GetClientRect(m_hwnd, &client);
        const RECT status{220, std::max<LONG>(0, client.bottom - 32), client.right, client.bottom};
        InvalidateRect(m_hwnd, &status, FALSE);
    }

    void emitMovement() {
        if (m_stats.inferenceRan) m_output.publish(m_selected, m_capture.width(), m_capture.height(), m_capture.observedAt(),
            m_outputGeneration, {}, m_capture.frameIdentity(m_outputGeneration), m_config.frameReference);
        m_stats.outputActive = m_output.status().active;
    }

    void loadDefaultProfile() {
        const auto path = std::filesystem::path(executableDirectory()) / L"profiles" / L"default.json";
        if (std::filesystem::exists(path)) loadProfile(path.wstring());
    }
    void loadProfile(const std::wstring& path) {
        const auto source = readFile(path);
        if (source.empty()) { MessageBoxW(m_hwnd, L"This profile could not be read.", L"Profile error", MB_ICONWARNING); return; }
        AppConfig loaded = m_config;
        const auto general = jsonObject(source, L"general");
        const auto model = jsonObject(source, L"model");
        const auto capture = jsonObject(source, L"capture");
        const auto tracking = jsonObject(source, L"tracking");
        const auto selection = jsonObject(source, L"selection");
        const auto movement = jsonObject(source, L"movement");
        const auto overlay = jsonObject(source, L"overlay");
        const auto performance = jsonObject(source, L"performance");
        loaded.profileName = jsonString(general, L"profileName", loaded.profileName);
        loaded.modelPath = jsonString(model, L"path", loaded.modelPath);
        loaded.confidence = jsonNumber(model, L"confidenceThreshold", loaded.confidence);
        loaded.iou = jsonNumber(model, L"iouThreshold", loaded.iou);
        loaded.classFilter = jsonString(model, L"classFilter", loaded.classFilter);
        const auto provider = jsonString(model, L"provider", L"cpu");
        loaded.provider = provider == L"cuda" ? InferenceProvider::Cuda : provider == L"dml" ? InferenceProvider::DirectMl
                        : provider == L"trt" ? InferenceProvider::TensorRtRtx : InferenceProvider::Cpu;
        loaded.letterbox = jsonString(model, L"resizeMode", L"letterbox") != L"stretch";
        loaded.maxDetections = static_cast<int>(jsonNumber(jsonObject(source, L"detection"), L"maxDetections", loaded.maxDetections));
        loaded.captureMonitor = static_cast<int>(jsonNumber(capture, L"monitor", jsonString(capture, L"surface", L"primary") == L"all" ? 0 : 1));
        const auto region = jsonString(capture, L"region", L"");
        if (!region.empty()) {
            std::wstringstream values(region);
            std::wstring value;
            std::array<int*, 4> targets{&loaded.captureRegion.xOffset, &loaded.captureRegion.yOffset, &loaded.captureRegion.width, &loaded.captureRegion.height};
            for (auto* target : targets) if (std::getline(values, value, L',')) *target = _wtoi(value.c_str());
        }
        loaded.captureFps = static_cast<int>(jsonNumber(capture, L"fps", loaded.captureFps));
        loaded.inferenceFps = static_cast<int>(jsonNumber(performance, L"inferenceFps", loaded.inferenceFps));
        loaded.frameSkip = static_cast<int>(jsonNumber(performance, L"frameSkipping", loaded.frameSkip));
        loaded.ortIntraOpThreads = static_cast<int>(jsonNumber(performance, L"ortIntraOpThreads", loaded.ortIntraOpThreads));
        loaded.ortInterOpThreads = static_cast<int>(jsonNumber(performance, L"ortInterOpThreads", loaded.ortInterOpThreads));
        loaded.cudaGraphReplay = jsonBool(performance, L"cudaGraphReplay", loaded.cudaGraphReplay);
        loaded.cudaPreprocess = jsonBool(performance, L"cudaPreprocess", loaded.cudaPreprocess);
        loaded.matchIou = jsonNumber(tracking, L"matchIou", loaded.matchIou);
        loaded.maxMissed = static_cast<int>(jsonNumber(tracking, L"maxLostFrames", loaded.maxMissed));
        loaded.smoothing = jsonNumber(tracking, L"smoothing", loaded.smoothing);
        loaded.predictLost = jsonBool(tracking, L"predictLost", loaded.predictLost);
        loaded.strategy = strategyFromName(jsonString(selection, L"strategy", L"center"));
        loaded.acquisitionRadius = static_cast<int>(jsonNumber(selection, L"acquisitionRadius", loaded.acquisitionRadius));
        loaded.retentionRadius = static_cast<int>(jsonNumber(selection, L"retentionRadius", loaded.retentionRadius));
        loaded.switchingThreshold = jsonNumber(selection, L"switchingHysteresis", loaded.switchingThreshold);
        loaded.movementEnabled = jsonBool(movement, L"enabled", loaded.movementEnabled);
        loaded.movementGain = jsonNumber(movement, L"gain", loaded.movementGain);
        loaded.maxStep = static_cast<int>(jsonNumber(movement, L"maxStep", loaded.maxStep));
        loaded.deadzone = static_cast<int>(jsonNumber(movement, L"deadzone", loaded.deadzone));
        loaded.activationMode = jsonString(movement, L"activationMode", L"hold") == L"toggle" ? 1 : 0;
        const auto key = jsonString(movement, L"activationKey", L"RightMouseButton");
        loaded.activationKey = key == L"LeftShift" ? 1 : key == L"LeftControl" ? 2 : key == L"Space" ? 3 : 0;
        loaded.overlayEnabled = jsonBool(overlay, L"enabled", loaded.overlayEnabled);
        loaded.overlayBoxes = jsonBool(overlay, L"boxes", loaded.overlayBoxes);
        loaded.overlayLabels = jsonBool(overlay, L"labels", loaded.overlayLabels);
        loaded.overlayConfidence = jsonBool(overlay, L"confidence", loaded.overlayConfidence);
        loaded.overlayIds = jsonBool(overlay, L"ids", loaded.overlayIds);
        loaded.overlayMotion = jsonBool(overlay, L"motion", loaded.overlayMotion);
        m_config = loaded;
        if (!m_config.modelPath.empty()) {
            std::wstring error;
            if (!m_detector.loadModel(m_config.modelPath, m_config.provider, m_config.ortIntraOpThreads, m_config.ortInterOpThreads, m_config.cudaGraphReplay, m_config.cudaPreprocess, error, m_config.inferenceDevice, m_config.cpuSets)) {
                appendLog(L"Profile model was unavailable: " + error);
                m_config.modelPath.clear();
            }
        }
        m_activeProfile = path;
        pushConfig();
        appendLog(L"Profile loaded: " + std::filesystem::path(path).filename().wstring());
    }
    void saveProfile(const std::wstring& path) {
        std::wofstream file(path);
        if (!file) { MessageBoxW(m_hwnd, L"The profile could not be written.", L"Profile error", MB_ICONWARNING); return; }
        static const std::array<const wchar_t*, 4> keys{L"RightMouseButton", L"LeftShift", L"LeftControl", L"Space"};
        file << L"{\n  \"version\": 2,\n"
            << L"  \"general\": { \"profileName\": \"" << jsonEscape(m_config.profileName) << L"\" },\n"
            << L"  \"model\": { \"path\": \"" << jsonEscape(m_config.modelPath) << L"\", \"provider\": \"" << (m_config.provider == InferenceProvider::Cuda ? L"cuda" : m_config.provider == InferenceProvider::DirectMl ? L"dml" : m_config.provider == InferenceProvider::TensorRtRtx ? L"trt" : L"cpu") << L"\", \"resizeMode\": \"" << (m_config.letterbox ? L"letterbox" : L"stretch") << L"\", \"confidenceThreshold\": " << m_config.confidence << L", \"iouThreshold\": " << m_config.iou << L", \"classFilter\": \"" << jsonEscape(m_config.classFilter) << L"\" },\n"
            << L"  \"detection\": { \"maxDetections\": " << m_config.maxDetections << L" },\n"
            << L"  \"capture\": { \"source\": \"desktop\", \"monitor\": " << m_config.captureMonitor << L", \"region\": \"" << m_config.captureRegion.xOffset << L"," << m_config.captureRegion.yOffset << L"," << m_config.captureRegion.width << L"," << m_config.captureRegion.height << L"\", \"fps\": " << m_config.captureFps << L" },\n"
            << L"  \"tracking\": { \"matchIou\": " << m_config.matchIou << L", \"maxLostFrames\": " << m_config.maxMissed << L", \"smoothing\": " << m_config.smoothing << L", \"predictLost\": " << (m_config.predictLost ? L"true" : L"false") << L" },\n"
            << L"  \"selection\": { \"strategy\": \"" << strategyJsonName(m_config.strategy) << L"\", \"acquisitionRadius\": " << m_config.acquisitionRadius << L", \"retentionRadius\": " << m_config.retentionRadius << L", \"switchingHysteresis\": " << m_config.switchingThreshold << L" },\n"
            << L"  \"movement\": { \"enabled\": " << (m_config.movementEnabled ? L"true" : L"false") << L", \"gain\": " << m_config.movementGain << L", \"maxStep\": " << m_config.maxStep << L", \"deadzone\": " << m_config.deadzone << L", \"activationMode\": \"" << (m_config.activationMode ? L"toggle" : L"hold") << L"\", \"activationKey\": \"" << keys[m_config.activationKey] << L"\" },\n"
            << L"  \"overlay\": { \"enabled\": " << (m_config.overlayEnabled ? L"true" : L"false") << L", \"boxes\": " << (m_config.overlayBoxes ? L"true" : L"false") << L", \"labels\": " << (m_config.overlayLabels ? L"true" : L"false") << L", \"confidence\": " << (m_config.overlayConfidence ? L"true" : L"false") << L", \"ids\": " << (m_config.overlayIds ? L"true" : L"false") << L", \"motion\": " << (m_config.overlayMotion ? L"true" : L"false") << L" },\n"
            << L"  \"performance\": { \"inferenceFps\": " << m_config.inferenceFps << L", \"frameSkipping\": " << m_config.frameSkip << L", \"ortIntraOpThreads\": " << m_config.ortIntraOpThreads << L", \"ortInterOpThreads\": " << m_config.ortInterOpThreads << L", \"cudaGraphReplay\": " << (m_config.cudaGraphReplay ? L"true" : L"false") << L", \"cudaPreprocess\": " << (m_config.cudaPreprocess ? L"true" : L"false") << L" }\n}\n";
        m_activeProfile = path;
        appendLog(L"Profile saved: " + std::filesystem::path(path).filename().wstring());
    }
    void resetProfile() {
        stop();
        m_detector.clearModel();
        m_config = AppConfig{};
        m_activeProfile.clear();
        m_tracks.clear();
        m_selected.reset();
        m_lastDetections.clear();
        pushConfig();
        appendLog(L"Settings restored to factory defaults");
    }

    void copyDiagnostics() {
        const std::wstring report = L"Vapor Lock diagnostics\r\nState: " + std::wstring(m_running ? L"Running" : L"Stopped") + L"\r\n" + m_detector.status() + L"\r\nCapture FPS: " + std::to_wstring(static_cast<int>(m_stats.fps)) + L"\r\nCapture: " + std::to_wstring(m_stats.captureMs) + L" ms\r\nInference: " + std::to_wstring(m_stats.detectMs) + L" ms\r\nTracks: " + std::to_wstring(m_stats.tracks) + L"\r\n\r\n" + m_logText;
        if (!OpenClipboard(m_hwnd)) return;
        EmptyClipboard();
        const auto bytes = (report.size() + 1) * sizeof(wchar_t);
        HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, bytes);
        if (memory) {
            auto* destination = static_cast<wchar_t*>(GlobalLock(memory));
            memcpy(destination, report.c_str(), bytes);
            GlobalUnlock(memory);
            SetClipboardData(CF_UNICODETEXT, memory);
        }
        CloseClipboard();
        appendLog(L"Diagnostics copied to clipboard");
    }
    void appendLog(const std::wstring& message) {
        const auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
        tm local{};
        localtime_s(&local, &now);
        wchar_t timestamp[24]{};
        wcsftime(timestamp, 24, L"%H:%M:%S", &local);
        m_logText += L"[" + std::wstring(timestamp) + L"] " + message + L"\r\n";
        if (m_logText.size() > 24000) m_logText.erase(0, 8000);
        SetWindowTextW(m_log, m_logText.c_str());
        SendMessageW(m_log, EM_SETSEL, m_logText.size(), m_logText.size());
        SendMessageW(m_log, EM_SCROLLCARET, 0, 0);
    }

    LRESULT controlColor(HDC hdc, bool field) {
        SetTextColor(hdc, kText);
        SetBkColor(hdc, field ? kField : kPanel);
        return reinterpret_cast<LRESULT>(field ? m_fieldBrush : m_panelBrush);
    }
    void drawButton(const DRAWITEMSTRUCT* item) {
        if (!item || item->CtlType != ODT_BUTTON) return;
        const int id = item->CtlID;
        const bool selectedNavigation = id >= IdNavLive && id <= IdNavDiagnostics && static_cast<int>(m_page) == id - IdNavLive;
        const bool pressed = (item->itemState & ODS_SELECTED) != 0;
        const bool disabled = (item->itemState & ODS_DISABLED) != 0;
        COLORREF color = selectedNavigation ? RGB(33, 82, 83) : (id == IdRun ? RGB(30, 116, 99) : id == IdStop ? RGB(70, 50, 52) : kPanelRaised);
        if (pressed) color = RGB(43, 61, 82);
        if (disabled) color = RGB(34, 42, 53);
        HBRUSH brush = CreateSolidBrush(color);
        FillRect(item->hDC, &item->rcItem, brush);
        DeleteObject(brush);
        HPEN border = CreatePen(PS_SOLID, 1, selectedNavigation ? kAccent : RGB(55, 71, 91));
        HGDIOBJ old = SelectObject(item->hDC, border);
        Rectangle(item->hDC, item->rcItem.left, item->rcItem.top, item->rcItem.right, item->rcItem.bottom);
        SelectObject(item->hDC, old);
        DeleteObject(border);
        wchar_t caption[128]{};
        GetWindowTextW(item->hwndItem, caption, 128);
        SetBkMode(item->hDC, TRANSPARENT);
        SetTextColor(item->hDC, disabled ? kMuted : kText);
        SelectObject(item->hDC, m_smallFont);
        RECT text = item->rcItem;
        text.left += 12;
        DrawTextW(item->hDC, caption, -1, &text, DT_SINGLELINE | DT_VCENTER | DT_LEFT);
    }
    void textAt(HDC hdc, int x, int y, const std::wstring& text) {
        TextOutW(hdc, x, y, text.c_str(), static_cast<int>(text.size()));
    }
    void paint() {
        PAINTSTRUCT paintState{};
        HDC hdc = BeginPaint(m_hwnd, &paintState);
        RECT client{};
        GetClientRect(m_hwnd, &client);
        HBRUSH background = CreateSolidBrush(kWindow);
        FillRect(hdc, &client, background);
        DeleteObject(background);
        RECT sidebar{0, 0, 220, client.bottom};
        FillRect(hdc, &sidebar, m_sidebarBrush);
        HBRUSH header = CreateSolidBrush(RGB(19, 28, 41));
        RECT top{220, 0, client.right, 78};
        FillRect(hdc, &top, header);
        DeleteObject(header);
        SetBkMode(hdc, TRANSPARENT);
        SelectObject(hdc, m_titleFont);
        SetTextColor(hdc, kText);
        textAt(hdc, 18, 24, L"VAPOR LOCK");
        SelectObject(hdc, m_smallFont);
        SetTextColor(hdc, kMuted);
        textAt(hdc, 18, 60, L"Vision tracking workspace");
        SelectObject(hdc, m_titleFont);
        SetTextColor(hdc, kText);
        textAt(hdc, m_content.left, m_content.top, m_heading);
        SelectObject(hdc, m_smallFont);
        SetTextColor(hdc, kMuted);
        textAt(hdc, m_content.left, m_content.top + 38, m_subheading);
        drawRunStatus(hdc, client);
        if (m_page != Page::Live && m_page != Page::Diagnostics) drawSettings(hdc);
        if (m_preview.right > m_preview.left) drawPreview(hdc);
        drawStatusBar(hdc, client);
        EndPaint(m_hwnd, &paintState);
    }
    void drawRunStatus(HDC hdc, const RECT& client) {
        const std::wstring status = m_running ? L"CAPTURING" : L"READY";
        SetTextColor(hdc, m_running ? kAccent : kWarm);
        SelectObject(hdc, m_smallFont);
        textAt(hdc, client.right - 300, 20, status);
        SetTextColor(hdc, kMuted);
        const std::wstring model = m_config.modelPath.empty() ? L"Test signal" : std::filesystem::path(m_config.modelPath).filename().wstring();
        textAt(hdc, client.right - 300, 42, model);
    }
    void drawSettings(HDC hdc) {
        FillRect(hdc, &m_settingsPanel, m_panelBrush);
        HPEN border = CreatePen(PS_SOLID, 1, RGB(49, 65, 84));
        HGDIOBJ old = SelectObject(hdc, border);
        Rectangle(hdc, m_settingsPanel.left, m_settingsPanel.top, m_settingsPanel.right, m_settingsPanel.bottom);
        SelectObject(hdc, old);
        DeleteObject(border);
        for (const auto& label : m_labels) {
            SelectObject(hdc, m_font);
            SetTextColor(hdc, kText);
            if (!label.label.empty()) textAt(hdc, m_content.left + 18, label.y, label.label);
            SelectObject(hdc, m_smallFont);
            SetTextColor(hdc, kMuted);
            if (!label.detail.empty()) textAt(hdc, m_content.left + 18, label.y + (label.label.empty() ? 0 : 25), label.detail);
        }
    }
    void drawPreview(HDC hdc) {
        HBRUSH panel = CreateSolidBrush(RGB(8, 13, 20));
        FillRect(hdc, &m_preview, panel);
        DeleteObject(panel);
        HPEN border = CreatePen(PS_SOLID, 1, RGB(49, 65, 84));
        HGDIOBJ old = SelectObject(hdc, border);
        Rectangle(hdc, m_preview.left, m_preview.top, m_preview.right, m_preview.bottom);
        SelectObject(hdc, old);
        DeleteObject(border);
        if (m_capture.bgra().empty()) {
            SetTextColor(hdc, kMuted);
            SelectObject(hdc, m_font);
            const std::wstring message = L"Start capture to see the live desktop feed";
            DrawTextW(hdc, message.c_str(), -1, &m_preview, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            return;
        }
        // Only committed pixels may be shown. The GDI DIB can contain an
        // uncommitted recovery probe while detection still owns the old frame.
        BITMAPINFO bitmapInfo{};
        bitmapInfo.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bitmapInfo.bmiHeader.biWidth = m_capture.width();
        bitmapInfo.bmiHeader.biHeight = -m_capture.height();
        bitmapInfo.bmiHeader.biPlanes = 1;
        bitmapInfo.bmiHeader.biBitCount = 32;
        bitmapInfo.bmiHeader.biCompression = BI_RGB;
        const int availableWidth = m_preview.right - m_preview.left - 16;
        const int availableHeight = m_preview.bottom - m_preview.top - 54;
        const double scale = std::min(availableWidth / static_cast<double>(m_capture.width()), availableHeight / static_cast<double>(m_capture.height()));
        const int drawWidth = static_cast<int>(m_capture.width() * scale);
        const int drawHeight = static_cast<int>(m_capture.height() * scale);
        const int x = m_preview.left + (m_preview.right - m_preview.left - drawWidth) / 2;
        const int y = m_preview.top + 34 + (availableHeight - drawHeight) / 2;
        SetStretchBltMode(hdc, HALFTONE);
        StretchDIBits(hdc, x, y, drawWidth, drawHeight, 0, 0, m_capture.width(), m_capture.height(),
            m_capture.bgra().data(), &bitmapInfo, DIB_RGB_COLORS, SRCCOPY);
        SetBkMode(hdc, TRANSPARENT);
        SelectObject(hdc, m_smallFont);
        SetTextColor(hdc, kMuted);
        textAt(hdc, m_preview.left + 12, m_preview.top + 10, L"LIVE PREVIEW");
        if (m_config.overlayEnabled) drawTracks(hdc, x, y, scale);
    }
    void drawTracks(HDC hdc, int x, int y, double scale) {
        HPEN normal = CreatePen(PS_SOLID, 2, kAccent);
        HPEN selected = CreatePen(PS_SOLID, 3, kWarm);
        for (const auto& track : m_tracks) {
            const int left = x + static_cast<int>(track.detection.box.x * scale);
            const int top = y + static_cast<int>(track.detection.box.y * scale);
            const int right = x + static_cast<int>((track.detection.box.x + track.detection.box.w) * scale);
            const int bottom = y + static_cast<int>((track.detection.box.y + track.detection.box.h) * scale);
            if (m_config.overlayBoxes) {
                HGDIOBJ old = SelectObject(hdc, track.selected ? selected : normal);
                Rectangle(hdc, left, top, right, bottom);
                SelectObject(hdc, old);
            }
            std::wstring label;
            if (m_config.overlayIds) label += L"#" + std::to_wstring(track.id) + L" ";
            if (m_config.overlayLabels) label += track.detection.label;
            if (m_config.overlayConfidence) {
                if (!label.empty()) label += L" ";
                wchar_t score[16]{};
                swprintf_s(score, L"%.0f%%", track.detection.confidence * 100.0);
                label += score;
            }
            if (!label.empty()) { SetTextColor(hdc, kText); textAt(hdc, left + 4, std::max(y, top - 20), label); }
            if (m_config.overlayMotion) {
                const int centerX = left + (right - left) / 2;
                const int centerY = top + (bottom - top) / 2;
                MoveToEx(hdc, centerX, centerY, nullptr);
                LineTo(hdc, centerX + static_cast<int>(track.vx * scale * 3.0), centerY + static_cast<int>(track.vy * scale * 3.0));
            }
        }
        DeleteObject(normal);
        DeleteObject(selected);
    }
    void drawStatusBar(HDC hdc, const RECT& client) {
        HBRUSH bar = CreateSolidBrush(RGB(19, 28, 41));
        RECT status{220, client.bottom - 32, client.right, client.bottom};
        FillRect(hdc, &status, bar);
        DeleteObject(bar);
        SelectObject(hdc, m_smallFont);
        SetTextColor(hdc, kMuted);
        const std::wstring value = L"Capture " + std::to_wstring(static_cast<int>(m_stats.fps)) + L" FPS   |   Capture " + std::to_wstring(static_cast<int>(m_stats.captureMs)) + L" ms   |   Inference " + std::to_wstring(static_cast<int>(m_stats.detectMs)) + L" ms   |   Tracks " + std::to_wstring(m_stats.tracks) + L"   |   Output " + (m_stats.outputActive ? L"active" : L"idle");
        textAt(hdc, 238, client.bottom - 23, value);
    }
    void destroyResources() {
        if (m_font) DeleteObject(m_font);
        if (m_smallFont) DeleteObject(m_smallFont);
        if (m_titleFont) DeleteObject(m_titleFont);
        if (m_panelBrush) DeleteObject(m_panelBrush);
        if (m_fieldBrush) DeleteObject(m_fieldBrush);
        if (m_sidebarBrush) DeleteObject(m_sidebarBrush);
    }

    HINSTANCE m_instance = nullptr;
    HWND m_hwnd = nullptr;
    HFONT m_font = nullptr, m_smallFont = nullptr, m_titleFont = nullptr;
    HBRUSH m_panelBrush = nullptr, m_fieldBrush = nullptr, m_sidebarBrush = nullptr;
    HWND m_run = nullptr, m_stop = nullptr, m_modelPath = nullptr, m_browseModel = nullptr, m_unloadModel = nullptr, m_confidence = nullptr, m_nmsIou = nullptr, m_classFilter = nullptr, m_captureSource = nullptr, m_captureFps = nullptr, m_inferenceFps = nullptr, m_frameSkip = nullptr, m_trackIou = nullptr, m_smoothing = nullptr, m_maxMissed = nullptr, m_predictLost = nullptr, m_strategy = nullptr, m_acquireRadius = nullptr, m_retainRadius = nullptr, m_switching = nullptr, m_movement = nullptr, m_activationMode = nullptr, m_activationKey = nullptr, m_gain = nullptr, m_maxStep = nullptr, m_deadzone = nullptr, m_overlay = nullptr, m_overlayBoxes = nullptr, m_overlayLabels = nullptr, m_overlayConfidence = nullptr, m_overlayIds = nullptr, m_overlayMotion = nullptr, m_profileName = nullptr, m_loadProfile = nullptr, m_saveProfile = nullptr, m_resetProfile = nullptr, m_clearLog = nullptr, m_copyDiagnostics = nullptr, m_log = nullptr;
    std::array<HWND, 9> m_nav{};
    Page m_page = Page::Live;
    RECT m_content{}, m_settingsPanel{}, m_preview{};
    std::wstring m_heading = L"Live monitoring", m_subheading, m_logText, m_activeProfile;
    std::vector<LabelRow> m_labels;
    AppConfig m_config;
    DesktopCapture m_capture;
    vaporlock::crosshair::Matcher m_crosshairMatcher;
    OnnxDetector m_detector;
    IoUTracker m_tracker;
    std::uint64_t m_trackingSource = 0;
    TargetSelector m_selector;
    std::vector<Detection> m_lastDetections;
    std::vector<Track> m_tracks;
    std::optional<Track> m_selected;
    RuntimeStats m_stats;
    bool m_running = false;
    AimOutputScheduler m_output{[](const AimReport& report, const AimInputs& input, const AppConfig& settings, AimOutputStatus& status) {
        return submitWindowsAim(report,input,settings,status);
    }};
    std::uint64_t m_outputGeneration = 0;
    long long m_frames = 0;
    vaporlock::InferenceCadence m_inferenceCadence;
    std::chrono::steady_clock::time_point m_started = std::chrono::steady_clock::now();
};

} // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE);
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    SetDllDirectoryW(L"");
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        const bool headless = std::any_of(argv, argv + argc, [](const wchar_t* argument) { return wcscmp(argument, L"--headless") == 0; });
        if (headless) {
            const int result = runHeadless(argc, argv);
            LocalFree(argv);
            return result;
        }
        LocalFree(argv);
    }
    MainWindow window;
    return window.run(instance, show);
}
