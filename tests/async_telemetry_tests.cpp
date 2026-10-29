#include "app/AsyncTelemetry.h"
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <vector>

using Writer = vaporlock::io::AsyncTelemetry;
using Clock = std::chrono::steady_clock;

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Pipe {
    HANDLE read = nullptr, write = nullptr;
    Pipe() { if (!CreatePipe(&read, &write, nullptr, 4096)) throw std::runtime_error("Cannot create test pipe"); }
    ~Pipe() { if (read) CloseHandle(read); closeWrite(); }
    void closeWrite() { if (write) { CloseHandle(write); write = nullptr; } }
};

template<class Predicate>
static bool waitFor(Predicate ready) {
    const auto deadline = Clock::now() + std::chrono::seconds(2);
    while (!ready() && Clock::now() < deadline) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    return ready();
}

static bool hasBytes(HANDLE pipe) {
    DWORD count = 0;
    return PeekNamedPipe(pipe, nullptr, 0, nullptr, &count, nullptr) && count;
}

static void verifyCoalescing() {
    Pipe pipe;
    Writer writer(pipe.write);
    const std::string first(200000, 'A');
    writer.publish(Writer::Kind::Preview, first);
    require(waitFor([&] { return hasBytes(pipe.read); }), "Writer did not reach the pipe");
    const auto before = Clock::now();
    for (int index = 0; index < 2000; ++index) {
        for (auto kind : {Writer::Kind::Stats, Writer::Kind::Overlay, Writer::Kind::Preview, Writer::Kind::Dataset, Writer::Kind::Notice, Writer::Kind::Control})
            writer.publish(kind, std::to_string(static_cast<int>(kind)) + ":" + std::to_string(index));
    }
    const auto publishMs = std::chrono::duration<double, std::milli>(Clock::now() - before).count();
    std::atomic<int> lines{0};
    auto reader = std::async(std::launch::async, [&] {
        std::vector<std::string> received;
        std::string pending;
        char buffer[4096];
        DWORD count;
        while (ReadFile(pipe.read, buffer, sizeof(buffer), &count, nullptr) && count) {
            pending.append(buffer, count);
            for (auto end = pending.find('\n'); end != std::string::npos; end = pending.find('\n')) {
                received.push_back(pending.substr(0, end)); pending.erase(0, end + 1);
                ++lines;
            }
        }
        return std::pair{received, pending};
    });
    const bool drained = waitFor([&] { return lines >= 7; });
    writer.finish("terminal", 1000);
    pipe.closeWrite();
    auto [received, trailing] = reader.get();
    require(drained && received.size() == 8 && trailing.empty(), "Lost or interleaved line framing");
    require(received.front() == first && received.back() == "terminal", "Partial-write or terminal framing failed");
    for (int kind = 0; kind < 6; ++kind)
        require(std::count(received.begin(), received.end(), std::to_string(kind) + ":1999") == 1, "Queued stale states or lost latest state");
    require(publishMs < 1000, "Capture-side telemetry publication waited on the reader");
    require(!writer.publish(Writer::Kind::Stats, "after-stop"), "Stopped writer accepted work");
    std::cout << "Unread pipe: 12000 state publications in " << publishMs << " ms; newest states delivered\n";
}

static void verifyCancellation() {
    Pipe pipe;
    Writer writer(pipe.write);
    writer.publish(Writer::Kind::Preview, std::string(200000, 'X'));
    require(waitFor([&] { return hasBytes(pipe.read); }), "Writer did not block on test pipe");
    const auto before = Clock::now();
    writer.stop();
    const auto ms = std::chrono::duration<double, std::milli>(Clock::now() - before).count();
    require(ms < 1000, "Cancellation waited for the absent GUI reader");
    std::cout << "Cancel blocked pipe ms=" << ms << '\n';
    for (int iteration = 0; iteration < 50; ++iteration) {
        Writer racing(pipe.write);
        racing.publish(Writer::Kind::Preview, std::string(10000, 'Y'));
        racing.stop();
    }
}

static void verifyBrokenPipe() {
    Pipe pipe;
    Writer writer(pipe.write);
    CloseHandle(pipe.read); pipe.read = nullptr;
    writer.publish(Writer::Kind::Stats, "state");
    require(waitFor([&] { return writer.failed(); }), "Broken connection was not reported");
    require(!writer.publish(Writer::Kind::Stats, "retry"), "Broken writer accepted more output");
    writer.stop();
}

static void verifyFinishDropsPending() {
    Pipe pipe;
    Writer writer(pipe.write);
    writer.publish(Writer::Kind::Preview, std::string(200000, 'X'));
    require(waitFor([&] { return hasBytes(pipe.read); }), "Writer did not start");
    writer.publish(Writer::Kind::Stats, "should-not-follow-error");
    const auto before = Clock::now();
    writer.finish("error", 25);
    const auto ms = std::chrono::duration<double, std::milli>(Clock::now() - before).count();
    require(ms < 1000, "Terminal error waited indefinitely for a blocked consumer");
    require(!writer.publish(Writer::Kind::Stats, "late"), "Finishing writer was reopened");
    std::cout << "Unresponsive consumer terminal shutdown ms=" << ms << '\n';
}

int main() {
    try {
        verifyCoalescing();
        verifyCancellation();
        verifyBrokenPipe();
        verifyFinishDropsPending();
        std::cout << "Asynchronous telemetry tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
