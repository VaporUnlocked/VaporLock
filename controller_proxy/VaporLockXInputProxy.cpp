#include <windows.h>

#include "../app/XInputProxyProtocol.h"

#include <algorithm>
#include <cstdint>

namespace {
struct Gamepad {
    WORD buttons;
    BYTE left_trigger;
    BYTE right_trigger;
    SHORT thumb_lx;
    SHORT thumb_ly;
    SHORT thumb_rx;
    SHORT thumb_ry;
};
struct State {
    DWORD packet_number;
    Gamepad gamepad;
};
using GetStateFn = DWORD(WINAPI*)(DWORD, State*);
using SetStateFn = DWORD(WINAPI*)(DWORD, void*);
using GetCapabilitiesFn = DWORD(WINAPI*)(DWORD, DWORD, void*);
using GetBatteryFn = DWORD(WINAPI*)(DWORD, BYTE, void*);
using GetKeystrokeFn = DWORD(WINAPI*)(DWORD, DWORD, void*);
using GetAudioIdsFn = DWORD(WINAPI*)(DWORD, LPWSTR, UINT*, LPWSTR, UINT*);
using EnableFn = void(WINAPI*)(BOOL);

HMODULE g_system = nullptr;
GetStateFn g_get_state = nullptr;
SetStateFn g_set_state = nullptr;
GetCapabilitiesFn g_get_capabilities = nullptr;
GetBatteryFn g_get_battery = nullptr;
GetKeystrokeFn g_get_keystroke = nullptr;
GetAudioIdsFn g_get_audio_ids = nullptr;
EnableFn g_enable = nullptr;
HANDLE g_mapping = nullptr;
const vaporlock::xinput_proxy::SharedState* g_shared = nullptr;

template <typename T>
T load_export(const char* name) {
    return reinterpret_cast<T>(GetProcAddress(g_system, name));
}

void initialize() {
    if (g_system) return;

    wchar_t path[MAX_PATH]{};
    const UINT length = GetSystemDirectoryW(path, MAX_PATH);
    if (length == 0 || length >= MAX_PATH - 16) return;
    lstrcatW(path, L"\\xinput1_4.dll");
    g_system = LoadLibraryExW(path, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!g_system) return;

    g_get_state = load_export<GetStateFn>("XInputGetState");
    g_set_state = load_export<SetStateFn>("XInputSetState");
    g_get_capabilities = load_export<GetCapabilitiesFn>("XInputGetCapabilities");
    g_get_battery = load_export<GetBatteryFn>("XInputGetBatteryInformation");
    g_get_keystroke = load_export<GetKeystrokeFn>("XInputGetKeystroke");
    g_get_audio_ids = load_export<GetAudioIdsFn>("XInputGetAudioDeviceIds");
    g_enable = load_export<EnableFn>("XInputEnable");

    g_mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, vaporlock::xinput_proxy::kMappingName);
    if (g_mapping)
        g_shared = static_cast<const vaporlock::xinput_proxy::SharedState*>(
            MapViewOfFile(g_mapping, FILE_MAP_READ, 0, 0, sizeof(vaporlock::xinput_proxy::SharedState)));
}

SHORT add_axis(SHORT physical, std::int16_t delta) {
    const int value = static_cast<int>(physical) + static_cast<int>(delta);
    return static_cast<SHORT>(std::clamp(value, -32768, 32767));
}
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputGetState(DWORD user, State* state) WIN_NOEXCEPT {
    initialize();
    if (!g_get_state) return ERROR_DEVICE_NOT_CONNECTED;
    const DWORD result = g_get_state(user, state);
    if (result != ERROR_SUCCESS || !state || !g_shared) return result;

    const auto& shared = *g_shared;
    if (shared.magic != vaporlock::xinput_proxy::kMagic ||
        shared.version != vaporlock::xinput_proxy::kVersion)
        return result;

    if (shared.right_enabled) {
        state->gamepad.thumb_rx = add_axis(state->gamepad.thumb_rx, shared.right_x);
        state->gamepad.thumb_ry = add_axis(state->gamepad.thumb_ry, shared.right_y);
    }
    if (shared.left_enabled) {
        state->gamepad.thumb_lx = add_axis(state->gamepad.thumb_lx, shared.left_x);
        state->gamepad.thumb_ly = add_axis(state->gamepad.thumb_ly, shared.left_y);
    }
    return result;
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputSetState(DWORD user, void* vibration) WIN_NOEXCEPT {
    initialize();
    return g_set_state ? g_set_state(user, vibration) : ERROR_DEVICE_NOT_CONNECTED;
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputGetCapabilities(DWORD user, DWORD flags, void* caps) WIN_NOEXCEPT {
    initialize();
    return g_get_capabilities ? g_get_capabilities(user, flags, caps) : ERROR_DEVICE_NOT_CONNECTED;
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputGetBatteryInformation(DWORD user, BYTE type, void* battery) WIN_NOEXCEPT {
    initialize();
    return g_get_battery ? g_get_battery(user, type, battery) : ERROR_DEVICE_NOT_CONNECTED;
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputGetKeystroke(DWORD user, DWORD reserved, void* stroke) WIN_NOEXCEPT {
    initialize();
    return g_get_keystroke ? g_get_keystroke(user, reserved, stroke) : ERROR_EMPTY;
}

extern "C" __declspec(dllexport) void WINAPI XInputEnable(BOOL enable) WIN_NOEXCEPT {
    initialize();
    if (g_enable) g_enable(enable);
}

extern "C" __declspec(dllexport) DWORD WINAPI XInputGetAudioDeviceIds(
    DWORD user, LPWSTR render, UINT* render_count, LPWSTR capture, UINT* capture_count) WIN_NOEXCEPT {
    initialize();
    return g_get_audio_ids ? g_get_audio_ids(user, render, render_count, capture, capture_count)
                           : ERROR_DEVICE_NOT_CONNECTED;
}
