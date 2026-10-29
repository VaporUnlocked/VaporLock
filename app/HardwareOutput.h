#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace vaporlock::hardware {

// Keep the numeric values stable because profiles and the native command line
// use the corresponding wire names below.
constexpr int kMouseTransport = 0;
constexpr int kVirtualXboxTransport = 1;
constexpr int kMakcuLegacyTransport = 2;
constexpr int kMakcuBinaryTransport = 3;
constexpr int kSerialHidBridgeTransport = 4;
constexpr int kKmboxNetTransport = 5;
constexpr int kKmboxATransport = 6;
constexpr int kVirtualMouseTransport = 7;

struct HardwareOutputConfig {
    std::wstring serialPort;
    int serialBaud = 115200;
    std::wstring kmboxNetAddress;
    int kmboxNetPort = 0;
    std::wstring kmboxNetDeviceCode;
    std::wstring kmboxALibraryPath;
    int kmboxAVendorId = 0x6688;
    int kmboxAProductId = 0x2021;
    std::wstring virtualMouseLibraryPath;
};

[[nodiscard]] bool parseTransport(const std::wstring& value, int& transport);
[[nodiscard]] const wchar_t* transportWireName(int transport);
[[nodiscard]] bool usesHardwareTransport(int transport);

// Exposed separately so the native tests can lock down the small, bounded
// movement-only protocol frames without needing a connected device.
[[nodiscard]] std::string makeMakcuLegacyMoveCommand(int x, int y);
[[nodiscard]] std::vector<std::uint8_t> makeMakcuBinaryMoveFrame(int x, int y);
[[nodiscard]] std::string makeVaporLockBridgeMoveCommand(int x, int y);
[[nodiscard]] bool parseKmboxDeviceCode(const std::wstring& value, std::uint32_t& code);

class HardwareMovementOutput {
public:
    HardwareMovementOutput();
    ~HardwareMovementOutput();
    HardwareMovementOutput(HardwareMovementOutput&&) noexcept;
    HardwareMovementOutput& operator=(HardwareMovementOutput&&) noexcept;
    HardwareMovementOutput(const HardwareMovementOutput&) = delete;
    HardwareMovementOutput& operator=(const HardwareMovementOutput&) = delete;

    // Opens and validates the selected transport before capture begins. The
    // adapter only accepts relative movement commands; it has no keyboard,
    // click, masking, remapping, device-identity, or descriptor operations.
    [[nodiscard]] bool prepare(int transport, const HardwareOutputConfig& config, std::wstring& error);
    [[nodiscard]] bool sendRelativeMovement(int x, int y, std::wstring& error);
    [[nodiscard]] bool ready() const;
    void close() noexcept;

private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};

}  // namespace vaporlock::hardware
