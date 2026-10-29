// Exercise the production serial writer on local recording pipes, never a device.
#include "app/HardwareOutput.cpp"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <utility>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};

void interceptionAbiMoves() {
    // Independent DLL-side reader: official fixed offsets, no production type.
    for (const auto [x, y] : {std::pair{0, 0}, {0, 1}, {1, 0}, {-1, 0},
             {0, -1}, {15, -23}, {-32768, 32767}, {1000000, -1000000}}) {
        const auto stroke = vaporlock::hardware::virtualMouseMove(x, y);
        std::array<unsigned char, 20> bytes{};
        std::memcpy(bytes.data(), &stroke, bytes.size());
        std::int32_t decodedX = 0, decodedY = 0;
        std::uint32_t information = 1;
        std::uint16_t state = 1, flags = 1;
        std::int16_t rolling = 1;
        std::memcpy(&state, bytes.data(), 2);
        std::memcpy(&flags, bytes.data() + 2, 2);
        std::memcpy(&rolling, bytes.data() + 4, 2);
        std::memcpy(&decodedX, bytes.data() + 8, 4);
        std::memcpy(&decodedY, bytes.data() + 12, 4);
        std::memcpy(&information, bytes.data() + 16, 4);
        require(decodedX == std::clamp(x, -32768, 32767), "driver ABI corrupted X movement");
        require(decodedY == std::clamp(y, -32768, 32767), "driver ABI corrupted Y movement");
        require(state == 0 && flags == 0 && rolling == 0 && information == 0,
                "movement emitted unintended mouse state or metadata");
    }
}

void unreadSerialWrite() {
    const auto path = L"\\\\.\\pipe\\VaporLock-serial-test-" + std::to_wstring(GetCurrentProcessId());
    const HANDLE server = CreateNamedPipeW(path.c_str(), PIPE_ACCESS_INBOUND | FILE_FLAG_OVERLAPPED,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, 32, 0, nullptr);
    require(server != INVALID_HANDLE_VALUE, "could not create unread pipe");
    std::jthread rescue([server] { Sleep(600); CloseHandle(server); });
    Handle writer{CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr)};
    require(writer.value != INVALID_HANDLE_VALUE, "could not open unread pipe");
    vaporlock::io::BoundedWindowsWrite operation;
    const auto command = vaporlock::hardware::makeVaporLockBridgeMoveCommand(123, -456);
    std::wstring error;
    bool rejected = false;
    double longest = 0;
    for (int index = 0; index < 8192; ++index) {
        const auto before = std::chrono::steady_clock::now();
        const bool accepted = vaporlock::hardware::writeExact(
            operation, writer.value, command.data(), static_cast<DWORD>(command.size()), 115200, error);
        longest = std::max(longest, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - before).count());
        if (!accepted) { rejected = true; break; }
    }
    std::cout << "Unread serial writer maximum call: " << longest << " ms\n";
    require(rejected && !error.empty(), "unread serial writer did not report failure");
    require(longest < 200, "serial writer blocked behind unread receiver");
}

void healthySerialWrites() {
    const auto path = L"\\\\.\\pipe\\VaporLock-serial-healthy-" + std::to_wstring(GetCurrentProcessId());
    Handle server{CreateNamedPipeW(path.c_str(), PIPE_ACCESS_INBOUND,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, 4096, 0, nullptr)};
    require(server.value != INVALID_HANDLE_VALUE, "could not create recording pipe");
    Handle writer{CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr)};
    require(writer.value != INVALID_HANDLE_VALUE, "could not open recording pipe");
    vaporlock::io::BoundedWindowsWrite operation;
    const auto check = [&](const auto& frame, int baud) {
        std::wstring error = L"old error";
        require(vaporlock::hardware::writeExact(operation, writer.value, frame.data(),
                    static_cast<DWORD>(frame.size()), baud, error), "healthy movement command was rejected");
        require(error.empty(), "successful write retained an old error");
        std::vector<char> received(frame.size());
        DWORD count = 0;
        require(ReadFile(server.value, received.data(), static_cast<DWORD>(received.size()), &count, nullptr)
                    && count == frame.size(), "recording pipe received a partial movement command");
        require(std::memcmp(frame.data(), received.data(), frame.size()) == 0, "movement wire bytes changed");
    };
    for (int baud : {1200, 9600, 115200, 1000000, 4000000}) {
        for (const auto [x, y] : {std::pair{0, 0}, std::pair{-32768, 32767}, std::pair{32767, -32768}}) {
            check(vaporlock::hardware::makeMakcuLegacyMoveCommand(x, y), baud);
            check(vaporlock::hardware::makeMakcuBinaryMoveFrame(x, y), baud);
            check(vaporlock::hardware::makeVaporLockBridgeMoveCommand(x, y), baud);
        }
    }
    require(DisconnectNamedPipe(server.value), "could not disconnect recording pipe");
    const auto frame = vaporlock::hardware::makeMakcuBinaryMoveFrame(1, 2);
    std::wstring error;
    require(!vaporlock::hardware::writeExact(operation, writer.value, frame.data(),
                static_cast<DWORD>(frame.size()), 115200, error) && !error.empty(),
            "broken pipe was reported as successful movement");
}

void serialTimeouts() {
    using vaporlock::hardware::serialWriteTimeoutMilliseconds;
    require(serialWriteTimeoutMilliseconds(22, 115200) == 22, "normal serial deadline did not account for wire time");
    require(serialWriteTimeoutMilliseconds(22, 1200) == 204, "slow serial line was assigned an impossible deadline");
    require(serialWriteTimeoutMilliseconds(22, 4000000) == 21, "fast serial line inherited a slow deadline");
    require(serialWriteTimeoutMilliseconds(22, -1) == 204, "invalid baud was not clamped like serial configuration");
    require(serialWriteTimeoutMilliseconds(MAXDWORD, 1200) < INFINITE, "serial deadline overflowed into an infinite wait");
}

void partialWriteIsNotSuccess() {
    const auto path = L"\\\\.\\pipe\\VaporLock-serial-partial-" + std::to_wstring(GetCurrentProcessId());
    Handle server{CreateNamedPipeW(path.c_str(), PIPE_ACCESS_INBOUND,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, 32, 0, nullptr)};
    require(server.value != INVALID_HANDLE_VALUE, "could not create short-write pipe");
    Handle client{CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr)};
    require(client.value != INVALID_HANDLE_VALUE, "could not open short-write pipe");
    DWORD mode = PIPE_READMODE_BYTE | PIPE_NOWAIT;
    require(SetNamedPipeHandleState(client.value, &mode, nullptr, nullptr), "could not set short-write pipe mode");
    vaporlock::io::BoundedWindowsWrite writer;
    const std::vector<std::uint8_t> frame(1024 * 64, 42);
    require(writer.write(client.value, frame.data(), static_cast<DWORD>(frame.size()), 50) == ERROR_WRITE_FAULT,
            "partial successful Windows write was reported as a complete movement command");
}

void asynchronousCompletion() {
    const auto path = L"\\\\.\\pipe\\VaporLock-serial-pending-" + std::to_wstring(GetCurrentProcessId());
    Handle server{CreateNamedPipeW(path.c_str(), PIPE_ACCESS_INBOUND,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, 32, 0, nullptr)};
    require(server.value != INVALID_HANDLE_VALUE, "could not create delayed-reader pipe");
    Handle client{CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr)};
    require(client.value != INVALID_HANDLE_VALUE, "could not open delayed-reader pipe");
    std::vector<std::uint8_t> frame(1024 * 64), received(frame.size());
    for (std::size_t index = 0; index < frame.size(); ++index) frame[index] = static_cast<std::uint8_t>(index);
    bool complete = false;
    std::jthread reader([&] {
        Sleep(20);
        std::size_t offset = 0;
        while (offset < received.size()) {
            DWORD count = 0;
            if (!ReadFile(server.value, received.data() + offset, static_cast<DWORD>(received.size() - offset),
                          &count, nullptr) || !count) break;
            offset += count;
        }
        complete = offset == frame.size();
    });
    vaporlock::io::BoundedWindowsWrite writer;
    const DWORD outcome = writer.write(client.value, frame.data(), static_cast<DWORD>(frame.size()), 150);
    if (outcome != ERROR_SUCCESS) DisconnectNamedPipe(server.value);
    reader.join();
    require(outcome == ERROR_SUCCESS && complete && received == frame,
            "overlapped completion did not preserve the full command buffer");
}

void rejectedWriteInputs() {
    vaporlock::io::BoundedWindowsWrite writer;
    const std::uint8_t byte = 0;
    require(writer.write(INVALID_HANDLE_VALUE, &byte, 1, 5) == ERROR_INVALID_HANDLE,
            "invalid handle did not fail immediately");
    require(writer.write(INVALID_HANDLE_VALUE, &byte, 0, 5) == ERROR_INVALID_PARAMETER,
            "empty frame was accepted");
    require(writer.write(INVALID_HANDLE_VALUE, nullptr, 1, 5) == ERROR_INVALID_PARAMETER,
            "missing frame was accepted");
    require(writer.write(INVALID_HANDLE_VALUE, &byte, 1, INFINITE) == ERROR_INVALID_PARAMETER,
            "unbounded write timeout was accepted");
}
}

int main() {
    try {
        interceptionAbiMoves();
        serialTimeouts();
        rejectedWriteInputs();
        healthySerialWrites();
        partialWriteIsNotSuccess();
        asynchronousCompletion();
        unreadSerialWrite();
        std::cout << "Hardware output tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
