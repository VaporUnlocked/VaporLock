#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include "app/HardwareOutput.h"
#include "app/InterceptionAbi.h"
#include "app/BoundedWindowsWrite.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstring>
#include <cwctype>
#include <limits>
#include <memory>
#include <string_view>

namespace vaporlock::hardware {
namespace {

constexpr std::uint32_t kKmboxConnectCommand = 0xaf3c2828U;
constexpr std::uint32_t kKmboxMoveCommand = 0xaede7345U;
constexpr DWORD kSerialWriteAllowanceMs = 20;

#pragma pack(push, 1)
struct KmboxPacketHeader {
    std::uint32_t deviceCode = 0;
    std::uint32_t nonce = 0;
    std::uint32_t sequence = 0;
    std::uint32_t command = 0;
};

struct KmboxMousePacket {
    std::int32_t buttons = 0;
    std::int32_t x = 0;
    std::int32_t y = 0;
    std::int32_t wheel = 0;
    std::array<std::int32_t, 10> points{};
};

struct KmboxMovePacket {
    KmboxPacketHeader header;
    KmboxMousePacket mouse;
};

#pragma pack(pop)

static_assert(sizeof(KmboxPacketHeader) == 16, "KMBox Net header layout changed unexpectedly.");
static_assert(sizeof(KmboxMousePacket) == 56, "KMBox Net movement layout changed unexpectedly.");
static_assert(sizeof(KmboxMovePacket) == 72, "KMBox Net packet layout changed unexpectedly.");

std::wstring trim(std::wstring value) {
    const auto first = value.find_first_not_of(L" \t\r\n");
    if (first == std::wstring::npos) return L"";
    const auto last = value.find_last_not_of(L" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::wstring uppercase(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t character) {
        return static_cast<wchar_t>(std::towupper(character));
    });
    return value;
}

int clampI16(int value) {
    return std::clamp(value, static_cast<int>(std::numeric_limits<std::int16_t>::min()),
                      static_cast<int>(std::numeric_limits<std::int16_t>::max()));
}

InterceptionMouseStroke virtualMouseMove(int x, int y) {
    InterceptionMouseStroke stroke{};
    stroke.x = clampI16(x);
    stroke.y = clampI16(y);
    return stroke;
}

bool serialPortPath(const std::wstring& configuredPort, std::wstring& path) {
    const std::wstring port = uppercase(trim(configuredPort));
    if (port.size() <= 3 || port.rfind(L"COM", 0) != 0) return false;
    for (std::size_t index = 3; index < port.size(); ++index) {
        if (port[index] < L'0' || port[index] > L'9') return false;
    }
    const int portNumber = _wtoi(port.c_str() + 3);
    if (portNumber < 1 || portNumber > 256) return false;
    path = L"\\\\.\\" + port;
    return true;
}

bool configureSerial(HANDLE handle, int baud, std::wstring& error) {
    DCB settings{};
    settings.DCBlength = sizeof(settings);
    if (!GetCommState(handle, &settings)) {
        error = L"Windows could not read the selected serial device settings.";
        return false;
    }
    settings.BaudRate = static_cast<DWORD>(std::clamp(baud, 1200, 4000000));
    settings.ByteSize = 8;
    settings.Parity = NOPARITY;
    settings.StopBits = ONESTOPBIT;
    settings.fBinary = TRUE;
    settings.fParity = FALSE;
    settings.fOutxCtsFlow = FALSE;
    settings.fOutxDsrFlow = FALSE;
    settings.fDtrControl = DTR_CONTROL_ENABLE;
    settings.fRtsControl = RTS_CONTROL_ENABLE;
    settings.fOutX = FALSE;
    settings.fInX = FALSE;
    if (!SetCommState(handle, &settings)) {
        error = L"Windows could not configure the selected serial device.";
        return false;
    }
    COMMTIMEOUTS timeouts{};
    // 8N1 uses ten wire bits per byte. Keep a short allowance beyond the
    // configured line time instead of delaying every stalled device by 120 ms.
    timeouts.WriteTotalTimeoutMultiplier = (10000U + settings.BaudRate - 1U) / settings.BaudRate;
    timeouts.WriteTotalTimeoutConstant = kSerialWriteAllowanceMs;
    timeouts.ReadIntervalTimeout = MAXDWORD;
    if (!SetCommTimeouts(handle, &timeouts)) {
        error = L"Windows could not set serial-device timeouts.";
        return false;
    }
    SetupComm(handle, 4096, 4096);
    PurgeComm(handle, PURGE_RXABORT | PURGE_RXCLEAR | PURGE_TXABORT | PURGE_TXCLEAR);
    return true;
}

DWORD serialWriteTimeoutMilliseconds(DWORD size, int baud) {
    const auto rate = static_cast<std::uint64_t>(std::clamp(baud, 1200, 4000000));
    const auto wireTime = (static_cast<std::uint64_t>(size) * 10000U + rate - 1U) / rate;
    return static_cast<DWORD>(std::min<std::uint64_t>(MAXDWORD - 1U, wireTime + kSerialWriteAllowanceMs));
}

bool writeExact(io::BoundedWindowsWrite& writer, HANDLE handle, const void* data, DWORD size,
                int baud, std::wstring& error) {
    const DWORD failure = writer.write(handle, data, size, serialWriteTimeoutMilliseconds(size, baud));
    if (failure != ERROR_SUCCESS) {
        error = failure == ERROR_TIMEOUT
            ? L"The serial device did not finish its movement command in time. Output was stopped to avoid queued stale movement."
            : L"The serial device could not complete its movement command (Windows error " + std::to_wstring(failure) + L").";
        return false;
    }
    error.clear();
    return true;
}

FARPROC resolveExport(HMODULE library, const char* undecorated, const char* decorated) {
    if (!library) return nullptr;
    if (auto* result = GetProcAddress(library, undecorated)) return result;
    return GetProcAddress(library, decorated);
}

}  // namespace

struct HardwareMovementOutput::Impl {
    int transport = kMouseTransport;
    HANDLE serial = INVALID_HANDLE_VALUE;
    io::BoundedWindowsWrite serialWriter;
    int serialBaud = 115200;
    SOCKET socket = INVALID_SOCKET;
    bool winsockStarted = false;
    sockaddr_in kmboxAddress{};
    std::uint32_t kmboxDeviceCode = 0;
    std::uint32_t kmboxSequence = 0;
    HMODULE kmboxALibrary = nullptr;
    using KmboxAInit = int(__cdecl*)(int, int);
    using KmboxAMove = int(__cdecl*)(int, int);
    KmboxAInit kmboxAInit = nullptr;
    KmboxAMove kmboxAMove = nullptr;
    HMODULE virtualMouseLibrary = nullptr;
    void* virtualMouseContext = nullptr;
    int virtualMouseDevice = 0;
    using VirtualMouseCreate = void*(__cdecl*)();
    using VirtualMouseDestroy = void(__cdecl*)(void*);
    using VirtualMouseSend = int(__cdecl*)(void*, int, const InterceptionMouseStroke*, unsigned int);
    VirtualMouseDestroy virtualMouseDestroy = nullptr;
    VirtualMouseSend virtualMouseSend = nullptr;
};

bool parseTransport(const std::wstring& value, int& transport) {
    if (value == L"mouse") transport = kMouseTransport;
    else if (value == L"virtual-xbox") transport = kVirtualXboxTransport;
    else if (value == L"makcu-v1") transport = kMakcuLegacyTransport;
    else if (value == L"makcu-v2") transport = kMakcuBinaryTransport;
    else if (value == L"serial-hid-bridge") transport = kSerialHidBridgeTransport;
    else if (value == L"kmbox-net") transport = kKmboxNetTransport;
    else if (value == L"kmbox-a") transport = kKmboxATransport;
    else if (value == L"virtual-mouse") transport = kVirtualMouseTransport;
    else return false;
    return true;
}

const wchar_t* transportWireName(int transport) {
    switch (transport) {
    case kVirtualXboxTransport: return L"virtual-xbox";
    case kMakcuLegacyTransport: return L"makcu-v1";
    case kMakcuBinaryTransport: return L"makcu-v2";
    case kSerialHidBridgeTransport: return L"serial-hid-bridge";
    case kKmboxNetTransport: return L"kmbox-net";
    case kKmboxATransport: return L"kmbox-a";
    case kVirtualMouseTransport: return L"virtual-mouse";
    default: return L"mouse";
    }
}

bool usesHardwareTransport(int transport) {
    return transport == kMakcuLegacyTransport || transport == kMakcuBinaryTransport
           || transport == kSerialHidBridgeTransport || transport == kKmboxNetTransport
           || transport == kKmboxATransport || transport == kVirtualMouseTransport;
}

std::string makeMakcuLegacyMoveCommand(int x, int y) {
    return ".move(" + std::to_string(clampI16(x)) + "," + std::to_string(clampI16(y)) + ")\r\n";
}

std::vector<std::uint8_t> makeMakcuBinaryMoveFrame(int x, int y) {
    const auto appendI16 = [](std::vector<std::uint8_t>& target, int value) {
        const auto signedValue = static_cast<std::int16_t>(clampI16(value));
        const auto raw = static_cast<std::uint16_t>(signedValue);
        target.push_back(static_cast<std::uint8_t>(raw & 0xffU));
        target.push_back(static_cast<std::uint8_t>((raw >> 8U) & 0xffU));
    };
    std::vector<std::uint8_t> frame{0x50, 0x0d, 0x07, 0x00};
    appendI16(frame, x);
    appendI16(frame, y);
    frame.push_back(1);   // one direct segment
    frame.push_back(0);   // no curve control point
    frame.push_back(0);
    return frame;
}

std::string makeVaporLockBridgeMoveCommand(int x, int y) {
    return "VLMB 1 " + std::to_string(clampI16(x)) + " " + std::to_string(clampI16(y)) + "\n";
}

bool parseKmboxDeviceCode(const std::wstring& value, std::uint32_t& code) {
    std::wstring text = uppercase(trim(value));
    if (text.rfind(L"0X", 0) == 0) text.erase(0, 2);
    if (text.size() != 8) return false;
    std::uint32_t result = 0;
    for (const wchar_t character : text) {
        int digit = -1;
        if (character >= L'0' && character <= L'9') digit = character - L'0';
        else if (character >= L'A' && character <= L'F') digit = character - L'A' + 10;
        if (digit < 0) return false;
        result = (result << 4U) | static_cast<std::uint32_t>(digit);
    }
    code = result;
    return true;
}

HardwareMovementOutput::HardwareMovementOutput() = default;
HardwareMovementOutput::~HardwareMovementOutput() { close(); }
HardwareMovementOutput::HardwareMovementOutput(HardwareMovementOutput&&) noexcept = default;
HardwareMovementOutput& HardwareMovementOutput::operator=(HardwareMovementOutput&&) noexcept = default;

bool HardwareMovementOutput::prepare(int transport, const HardwareOutputConfig& config, std::wstring& error) {
    close();
    if (!usesHardwareTransport(transport)) {
        error = L"The selected output transport is not a hardware movement adapter.";
        return false;
    }
    auto state = std::make_unique<Impl>();
    state->transport = transport;

    if (transport == kVirtualMouseTransport) {
        const std::wstring libraryPath = trim(config.virtualMouseLibraryPath);
        if (libraryPath.empty()) {
            error = L"The bundled virtual mouse component is unavailable in this Vapor Lock copy.";
            return false;
        }
        state->virtualMouseLibrary = LoadLibraryExW(libraryPath.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!state->virtualMouseLibrary) state->virtualMouseLibrary = LoadLibraryW(libraryPath.c_str());
        if (!state->virtualMouseLibrary) {
            error = L"Windows could not load the virtual mouse component.";
            return false;
        }
        const auto create = reinterpret_cast<Impl::VirtualMouseCreate>(resolveExport(state->virtualMouseLibrary, "interception_create_context", ""));
        state->virtualMouseDestroy = reinterpret_cast<Impl::VirtualMouseDestroy>(resolveExport(state->virtualMouseLibrary, "interception_destroy_context", ""));
        state->virtualMouseSend = reinterpret_cast<Impl::VirtualMouseSend>(resolveExport(state->virtualMouseLibrary, "interception_send", ""));
        if (!create || !state->virtualMouseDestroy || !state->virtualMouseSend) {
            FreeLibrary(state->virtualMouseLibrary);
            error = L"The bundled virtual mouse component has an incompatible API.";
            return false;
        }
        state->virtualMouseContext = create();
        if (!state->virtualMouseContext) {
            FreeLibrary(state->virtualMouseLibrary);
            error = L"Windows could not create the virtual mouse input context.";
            return false;
        }
        const InterceptionMouseStroke probe{};
        for (int device = 11; device <= 20; ++device) {
            if (state->virtualMouseSend(state->virtualMouseContext, device, &probe, 1) == 1) {
                state->virtualMouseDevice = device;
                break;
            }
        }
        if (!state->virtualMouseDevice) {
            state->virtualMouseDestroy(state->virtualMouseContext);
            FreeLibrary(state->virtualMouseLibrary);
            error = L"The virtual mouse driver is not active. Install it from Movement, then restart Windows before using this transport.";
            return false;
        }
        m_impl = std::move(state);
        return true;
    }

    if (transport == kMakcuLegacyTransport || transport == kMakcuBinaryTransport || transport == kSerialHidBridgeTransport) {
        std::wstring path;
        if (!serialPortPath(config.serialPort, path)) {
            error = L"Choose a serial port in the form COM3 before starting this hardware output.";
            return false;
        }
        if (!state->serialWriter.ready()) {
            error = L"Windows could not prepare asynchronous serial output.";
            return false;
        }
        state->serialBaud = std::clamp(config.serialBaud, 1200, 4000000);
        state->serial = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
        if (state->serial == INVALID_HANDLE_VALUE) {
            error = L"Windows could not open the selected serial device. Check its port and close any other program using it.";
            return false;
        }
        if (!configureSerial(state->serial, config.serialBaud, error)) {
            CloseHandle(state->serial);
            return false;
        }
        m_impl = std::move(state);
        return true;
    }

    if (transport == kKmboxNetTransport) {
        std::uint32_t deviceCode = 0;
        if (trim(config.kmboxNetAddress).empty() || config.kmboxNetPort < 1 || config.kmboxNetPort > 65535
            || !parseKmboxDeviceCode(config.kmboxNetDeviceCode, deviceCode)) {
            error = L"Enter the KMBox Net address, port, and eight-character device code before starting output.";
            return false;
        }
        WSADATA winsock{};
        if (WSAStartup(MAKEWORD(2, 2), &winsock) != 0) {
            error = L"Windows networking could not start the KMBox Net adapter.";
            return false;
        }
        state->winsockStarted = true;
        state->socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        if (state->socket == INVALID_SOCKET) {
            WSACleanup();
            error = L"Windows could not create the KMBox Net connection.";
            return false;
        }
        state->kmboxAddress.sin_family = AF_INET;
        state->kmboxAddress.sin_port = htons(static_cast<u_short>(config.kmboxNetPort));
        if (InetPtonW(AF_INET, trim(config.kmboxNetAddress).c_str(), &state->kmboxAddress.sin_addr) != 1) {
            closesocket(state->socket);
            WSACleanup();
            error = L"The KMBox Net address must be a valid IPv4 address.";
            return false;
        }
        state->kmboxDeviceCode = deviceCode;
        state->kmboxSequence = 1;
        KmboxPacketHeader connect{};
        connect.deviceCode = state->kmboxDeviceCode;
        connect.nonce = static_cast<std::uint32_t>(GetTickCount64());
        connect.sequence = state->kmboxSequence;
        connect.command = kKmboxConnectCommand;
        const int sent = sendto(state->socket, reinterpret_cast<const char*>(&connect), sizeof(connect), 0,
                                reinterpret_cast<const sockaddr*>(&state->kmboxAddress), sizeof(state->kmboxAddress));
        if (sent != sizeof(connect)) {
            closesocket(state->socket);
            WSACleanup();
            error = L"The KMBox Net adapter could not send its connection request.";
            return false;
        }
        fd_set readable{};
        FD_ZERO(&readable);
        FD_SET(state->socket, &readable);
        timeval timeout{0, 250000};
        const int selected = select(0, &readable, nullptr, nullptr, &timeout);
        KmboxPacketHeader response{};
        const int received = selected > 0
            ? recvfrom(state->socket, reinterpret_cast<char*>(&response), sizeof(response), 0, nullptr, nullptr)
            : SOCKET_ERROR;
        if (received < static_cast<int>(sizeof(response)) || response.command != connect.command || response.sequence != connect.sequence) {
            closesocket(state->socket);
            WSACleanup();
            error = L"The KMBox Net device did not acknowledge its connection request.";
            return false;
        }
        m_impl = std::move(state);
        return true;
    }

    if (transport == kKmboxATransport) {
        const std::wstring libraryPath = trim(config.kmboxALibraryPath);
        if (libraryPath.empty()) {
            error = L"Choose the official KMBox A DLL before starting KMBox A output.";
            return false;
        }
        state->kmboxALibrary = LoadLibraryExW(libraryPath.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!state->kmboxALibrary) state->kmboxALibrary = LoadLibraryW(libraryPath.c_str());
        if (!state->kmboxALibrary) {
            error = L"Windows could not load the selected KMBox A DLL.";
            return false;
        }
        state->kmboxAInit = reinterpret_cast<Impl::KmboxAInit>(resolveExport(state->kmboxALibrary, "KM_init", "_KM_init@8"));
        state->kmboxAMove = reinterpret_cast<Impl::KmboxAMove>(resolveExport(state->kmboxALibrary, "KM_move", "_KM_move@8"));
        if (!state->kmboxAInit || !state->kmboxAMove) {
            FreeLibrary(state->kmboxALibrary);
            error = L"The selected KMBox A DLL does not expose the official movement API.";
            return false;
        }
        if (state->kmboxAInit(std::clamp(config.kmboxAVendorId, 0, 65535), std::clamp(config.kmboxAProductId, 0, 65535)) != 0) {
            FreeLibrary(state->kmboxALibrary);
            error = L"The KMBox A device did not accept the selected vendor and product IDs.";
            return false;
        }
        m_impl = std::move(state);
        return true;
    }

    error = L"The selected hardware output is not implemented in this build.";
    return false;
}

bool HardwareMovementOutput::sendRelativeMovement(int x, int y, std::wstring& error) {
    if (!m_impl) {
        error = L"The selected hardware movement adapter is not prepared.";
        return false;
    }
    if (m_impl->serial != INVALID_HANDLE_VALUE) {
        const auto send = [&](const auto& frame) {
            return writeExact(m_impl->serialWriter, m_impl->serial, frame.data(),
                              static_cast<DWORD>(frame.size()), m_impl->serialBaud, error);
        };
        const bool accepted = m_impl->transport == kMakcuBinaryTransport
            ? send(makeMakcuBinaryMoveFrame(x, y))
            : send(m_impl->transport == kMakcuLegacyTransport
                ? makeMakcuLegacyMoveCommand(x, y) : makeVaporLockBridgeMoveCommand(x, y));
        if (!accepted) close();
        return accepted;
    }
    if (m_impl->transport == kKmboxNetTransport) {
        KmboxMovePacket packet{};
        packet.header.deviceCode = m_impl->kmboxDeviceCode;
        packet.header.nonce = static_cast<std::uint32_t>(GetTickCount64());
        packet.header.sequence = ++m_impl->kmboxSequence;
        packet.header.command = kKmboxMoveCommand;
        packet.mouse.x = clampI16(x);
        packet.mouse.y = clampI16(y);
        const int sent = sendto(m_impl->socket, reinterpret_cast<const char*>(&packet), sizeof(packet), 0,
                                reinterpret_cast<const sockaddr*>(&m_impl->kmboxAddress), sizeof(m_impl->kmboxAddress));
        if (sent != sizeof(packet)) {
            error = L"The KMBox Net device did not accept the movement command.";
            return false;
        }
        return true;
    }
    if (m_impl->transport == kKmboxATransport) {
        if (m_impl->kmboxAMove(clampI16(x), clampI16(y)) != 0) {
            error = L"The KMBox A device did not accept the movement command.";
            return false;
        }
        return true;
    }
    if (m_impl->transport == kVirtualMouseTransport) {
        const auto stroke = virtualMouseMove(x, y);
        if (m_impl->virtualMouseSend(m_impl->virtualMouseContext, m_impl->virtualMouseDevice, &stroke, 1) != 1) {
            error = L"The virtual mouse driver rejected the movement command.";
            return false;
        }
        return true;
    }
    error = L"The selected hardware movement adapter is not available.";
    return false;
}

bool HardwareMovementOutput::ready() const {
    return m_impl != nullptr;
}

void HardwareMovementOutput::close() noexcept {
    if (!m_impl) return;
    if (m_impl->serial != INVALID_HANDLE_VALUE) {
        PurgeComm(m_impl->serial, PURGE_TXABORT | PURGE_TXCLEAR);
        CloseHandle(m_impl->serial);
    }
    if (m_impl->socket != INVALID_SOCKET) closesocket(m_impl->socket);
    if (m_impl->winsockStarted) WSACleanup();
    if (m_impl->kmboxALibrary) FreeLibrary(m_impl->kmboxALibrary);
    if (m_impl->virtualMouseDestroy && m_impl->virtualMouseContext) m_impl->virtualMouseDestroy(m_impl->virtualMouseContext);
    if (m_impl->virtualMouseLibrary) FreeLibrary(m_impl->virtualMouseLibrary);
    m_impl.reset();
}

}  // namespace vaporlock::hardware
