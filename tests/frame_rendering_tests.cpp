// Synthetic pixels only: no engine entry point, capture, input, or licensing.
#include "app/Win32VaporLock.cpp"

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
#include "capture_recovery_tests.h"

static void referenceLine(std::vector<unsigned char>& pixels, int width, int height, int x0, int y0, int x1, int y1) {
    const int dx = std::abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    const int dy = -std::abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int error = dx + dy;
    for (;;) {
        if (x0 >= 0 && y0 >= 0 && x0 < width && y0 < height) {
            const auto at = (static_cast<std::size_t>(y0) * width + x0) * 4;
            pixels[at] = 200; pixels[at + 1] = 160; pixels[at + 2] = 120;
        }
        if (x0 == x1 && y0 == y1) break;
        const int twice = error * 2;
        if (twice >= dy) { error += dy; x0 += sx; }
        if (twice <= dx) { error += dx; y0 += sy; }
    }
}

static void verifyLines() {
    int cases = 0;
    for (int x0 : {-50, -17, -1, 0, 1, 5, 12, 23, 24, 25, 40})
    for (int y0 : {-50, -17, -1, 0, 1, 5, 12, 23, 24, 25, 40})
    for (int x1 : {-50, -17, -1, 0, 1, 5, 12, 23, 24, 25, 40})
    for (int y1 : {-50, -17, -1, 0, 1, 5, 12, 23, 24, 25, 40}) {
        std::vector<unsigned char> reference(24 * 24 * 4), actual(reference.size());
        referenceLine(reference, 24, 24, x0, y0, x1, y1);
        drawLine(actual, 24, 24, x0, y0, x1, y1, {120, 160, 200});
        require(reference == actual, "Clipped raster changed the visible pixels");
        ++cases;
    }
    std::vector<unsigned char> extreme(24 * 24 * 4);
    drawLine(extreme, 24, 24, INT_MIN, INT_MIN, INT_MAX, INT_MAX, {120, 160, 200});
    for (int y = 0; y < 24; ++y) for (int x = 0; x < 24; ++x)
        require(extreme[(y * 24 + x) * 4] == (x == y ? 200 : 0), "Extreme diagonal overflowed");
    std::cout << "Visible-pixel parity cases: " << cases << "; extreme endpoints passed\n";
}

static void verifyPreviewQueue(bool cancel) {
    std::promise<void> started, release;
    auto released = release.get_future();
    auto beginning = started.get_future();
    std::atomic<int> calls{0};
    std::mutex mutex;
    std::condition_variable wake;
    std::vector<std::string> values;
    PreviewWorker worker([&](std::string value) {
        std::lock_guard lock(mutex); values.push_back(std::move(value)); wake.notify_all();
    }, [](std::string) {}, [&](PreviewWorker::Frame frame) {
        if (calls++ == 0) { started.set_value(); released.wait(); }
        return std::to_string(frame.width);
    });
    worker.configure(true);
    worker.publish({1});
    const bool began = beginning.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
    if (cancel) { worker.configure(false); worker.configure(true); }
    const auto before = AimClock::now();
    for (int i = 2; i <= 2000; ++i) worker.publish({i});
    const auto ms = std::chrono::duration<double, std::milli>(AimClock::now() - before).count();
    release.set_value();
    bool delivered;
    {
        std::unique_lock lock(mutex);
        delivered = wake.wait_for(lock, std::chrono::seconds(2), [&] { return values.size() >= (cancel ? 1U : 2U); });
    }
    worker.stop();
    require(began && delivered, "Preview worker failed to complete");
    require(values == (cancel ? std::vector<std::string>{"2000"} : std::vector<std::string>{"1", "2000"}),
            "Preview worker replayed a backlog or a disabled generation");
    require(!worker.publish({2001}), "Stopped preview worker accepted more work");
    require(ms < 1000, "Publishing previews waited for the encoder");
    std::cout << "Preview latest-only queue cancel=" << cancel << "; 1999 submissions ms=" << ms << '\n';
}

static void verifyThreadedEncoding() {
    std::promise<std::pair<bool, std::string>> result;
    auto future = result.get_future();
    PreviewWorker worker([&](std::string data) { result.set_value({true, std::move(data)}); },
                         [&](std::string error) { result.set_value({false, std::move(error)}); });
    worker.configure(true);
    PreviewWorker::Frame frame;
    frame.width = 32; frame.height = 16;
    frame.pixels.resize(32 * 16 * 4, 255);
    frame.config.overlayEnabled = false;
    worker.publish(std::move(frame));
    const bool completed = future.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
    worker.stop();
    require(completed, "Actual preview encoding stalled");
    const auto [success, text] = future.get();
    require(success && text.starts_with("{\"type\":\"preview\",\"width\":32,\"height\":16,")
            && text.find("\"jpeg\":\"/9j/") != std::string::npos && text.find("\"schema\":2") != std::string::npos,
            "Preview worker did not initialize its own image-encoding context");
}

static void verifyOutputDuringBackpressure() {
    HANDLE read = nullptr, write = nullptr;
    require(CreatePipe(&read, &write, nullptr, 4096) != 0, "Test pipe creation failed");
    std::atomic<int> reports{0};
    {
        vaporlock::io::AsyncTelemetry telemetry(write);
        telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Preview, std::string(200000, 'X'));
        AimOutputScheduler output([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
            if (report.dx || report.dy) ++reports;
            return true; // Record only; never send a real input event.
        }, [](const AppConfig&) { AimInputs input; input.down = true; return input; });
        AppConfig config;
        config.movementEnabled = true;
        config.outputRate = 120;
        const auto generation = output.configure(config);
        output.start();
        for (int iteration = 0; iteration < 20; ++iteration) {
            Track target;
            target.id = 1; target.age = 5; target.hasObservation = true;
            target.detection.confidence = .95;
            target.detection.box = {340, 300, 40, 40};
            target.observedBox = target.detection.box;
            target.lastUpdate = AimClock::now();
            output.publish(target, 640, 640, target.lastUpdate, generation);
            telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Stats, "latest-state");
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        output.stop();
        telemetry.stop();
    }
    CloseHandle(write); CloseHandle(read);
    require(reports > 0, "Blocked visualization pipe stopped the independent output scheduler");
    std::cout << "Output reports during unread visual pipe: " << reports << " (recording sink only)\n";
}

static void verifyPreviewRecovery() {
    std::mutex mutex;
    std::condition_variable wake;
    int errors = 0, successes = 0, encoded = 0;
    PreviewWorker worker([&](std::string) {
        std::lock_guard lock(mutex); ++successes; wake.notify_all();
    }, [&](std::string message) {
        require(message == "test encoder failure", "Unexpected preview failure");
        std::lock_guard lock(mutex); ++errors; wake.notify_all();
    }, [&](PreviewWorker::Frame frame) -> std::string {
        { std::lock_guard lock(mutex); ++encoded; wake.notify_all(); }
        if (frame.width < 3) throw std::runtime_error("test encoder failure");
        return "recovered";
    });
    const auto wait = [&](auto ready) {
        std::unique_lock lock(mutex);
        return wake.wait_for(lock, std::chrono::seconds(2), ready);
    };
    require(!worker.publish({1}), "Disabled worker accepted a preview");
    worker.configure(true);
    worker.publish({1});
    require(wait([&] { return errors == 1; }), "Preview failure was not reported");
    worker.publish({2});
    require(wait([&] { return encoded == 2; }), "Second encoding did not execute");
    worker.publish({3});
    require(wait([&] { return successes == 1; }), "Encoder failure permanently disabled previews");
    require(errors == 1, "Repeated encoder failures flooded notices");
    worker.configure(false);
    worker.configure(true);
    worker.publish({1});
    require(wait([&] { return errors == 2; }), "Re-enabled preview suppressed the new failure");
    worker.stop();
    std::cout << "Preview encoder recovery and once-per-generation failure reporting passed\n";
}

static int wireFixture(const std::wstring& datasetDirectory) {
    const auto wait = [](auto predicate) {
        const auto deadline = AimClock::now() + std::chrono::seconds(3);
        while (!predicate()) {
            if (AimClock::now() >= deadline) return false;
            Sleep(1);
        }
        return true;
    };
    vaporlock::io::AsyncTelemetry telemetry(GetStdHandle(STD_OUTPUT_HANDLE));
    DatasetWriter dataset([&](std::uint64_t count) {
        telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Dataset,
            "{\"type\":\"dataset\",\"samples\":" + std::to_string(count) + "}");
    }, [&](std::string message) {
        telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Notice,
            "{\"type\":\"notice\",\"message\":\"" + wideToUtf8(jsonEscape(narrowToWide(message))) + "\"}");
    });
    telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Stats, "{\"type\":\"stats\",\"fps\":60,\"inference_total\":1}");
    telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Overlay,
        overlayMetadata(32, 16, {}, {}, false, AppConfig{}, 0, 0, AimClock::now() - std::chrono::milliseconds(100), {1, 1, 1, 2}));
    PreviewWorker worker([&](std::string line) { telemetry.publish(vaporlock::io::AsyncTelemetry::Kind::Preview, std::move(line)); },
                         [](std::string) {});
    worker.configure(true);
    PreviewWorker::Frame frame;
    frame.width = 32; frame.height = 16; frame.pixels.resize(32 * 16 * 4, 255); frame.config.overlayEnabled = false;
    AppConfig collection;
    collection.datasetEnabled = true;
    collection.datasetCaptureRequiresActivation = false;
    collection.datasetDirectory = datasetDirectory;
    collection.datasetJpegQuality = 83;
    require(wait([&] { return dataset.observeOutput(collection, 2, true, true); }), "wire collection configuration failed");
    require(wait([&] { const auto status = dataset.status(); return status && status->rootReady; }), "wire collection root unavailable");
    const CaptureFrameIdentity identity{1, 1, 1, 2};
    frame.identity = identity; frame.observedAt = AimClock::now();
    require(wait([&] { return dataset.observeSource(identity); }), "wire collection source unavailable");
    require(dataset.admit(32, 16, frame.pixels, identity, {identity, true, true}, true,
                          AimClock::now(), 12345) == DatasetWriter::Admission::Accepted,
            "wire collection frame rejected");
    worker.publish(std::move(frame));
    std::cin.get(); // The Qt regression reader acknowledges all four records.
    dataset.stop();
    worker.stop();
    telemetry.stop();
    return 0;
}

int main(int argc, char** argv) {
  try {
    if (argc == 3 && std::string_view(argv[1]) == "--wire-fixture") return wireFixture(narrowToWide(argv[2]));
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    verifyCaptureRecovery();
    require(captureSourcePoint(0, 0, 4, 3, DXGI_MODE_ROTATION_ROTATE90) == std::pair{0, 2}, "90 degree corner mapping");
    require(captureSourcePoint(2, 3, 4, 3, DXGI_MODE_ROTATION_ROTATE90) == std::pair{3, 0}, "90 degree opposite corner");
    require(captureSourcePoint(0, 0, 4, 3, DXGI_MODE_ROTATION_ROTATE180) == std::pair{3, 2}, "180 degree corner mapping");
    require(captureSourcePoint(0, 0, 4, 3, DXGI_MODE_ROTATION_ROTATE270) == std::pair{3, 0}, "270 degree corner mapping");
    for (auto rotation : {DXGI_MODE_ROTATION_IDENTITY, DXGI_MODE_ROTATION_ROTATE90, DXGI_MODE_ROTATION_ROTATE180, DXGI_MODE_ROTATION_ROTATE270}) {
        const bool swap = rotation == DXGI_MODE_ROTATION_ROTATE90 || rotation == DXGI_MODE_ROTATION_ROTATE270;
        std::array<bool, 12> visited{};
        for (int y = 0; y < (swap ? 4 : 3); ++y) for (int x = 0; x < (swap ? 3 : 4); ++x) {
            const auto [sx, sy] = captureSourcePoint(x, y, 4, 3, rotation);
            require(sx >= 0 && sx < 4 && sy >= 0 && sy < 3, "Rotated pixel out of bounds");
            require(!visited[sy * 4 + sx], "Rotation duplicated a pixel");
            visited[sy * 4 + sx] = true;
        }
    }
    verifyLines();
    verifyPreviewQueue(false);
    verifyPreviewQueue(true);
    verifyThreadedEncoding();
    verifyPreviewRecovery();
    verifyOutputDuringBackpressure();
    for (const auto [width, height] : {std::pair{640, 640}, std::pair{1024, 1024}, std::pair{1920, 1080}}) {
        std::vector<unsigned char> pixels(static_cast<std::size_t>(width) * height * 4);
        for (std::size_t index = 0; index < pixels.size(); ++index)
            pixels[index] = static_cast<unsigned char>((index * 31 + index / 7) % 256);
        for (int radius : {64, 420, 10000, 1000000}) {
            const auto before = AimClock::now();
            for (int pass = 0; pass < 5; ++pass)
                drawFovGuide(pixels, width, height, radius, false, {120, 160, 200});
            const double ms = std::chrono::duration<double, std::milli>(AimClock::now() - before).count() / 5;
            std::cout << "FOV " << width << 'x' << height << " radius=" << radius << " mean_ms=" << ms << '\n';
        }
        const auto before = AimClock::now();
        std::size_t bytes = 0;
        for (int pass = 0; pass < 5; ++pass) {
            std::vector<unsigned char> jpeg;
            if (!encodeJpeg(width, height, pixels, 65, jpeg)) return 2;
            bytes = base64Encode(jpeg).size();
        }
        std::cout << "JPEG_BASE64 " << width << 'x' << height << " bytes=" << bytes << " mean_ms="
                  << std::chrono::duration<double, std::milli>(AimClock::now() - before).count() / 5 << '\n';
    }
    CoUninitialize();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
