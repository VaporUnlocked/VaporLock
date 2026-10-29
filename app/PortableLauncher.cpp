#include <windows.h>

#include "ProductVersion.h"

#include <filesystem>
#include <string>

namespace {

std::wstring quote(const std::filesystem::path& value) {
    return L"\"" + value.wstring() + L"\"";
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    SetDllDirectoryW(L"");
    wchar_t executablePath[MAX_PATH]{};
    if (!GetModuleFileNameW(nullptr, executablePath, MAX_PATH)) return 1;
    const auto productRoot = std::filesystem::path(executablePath).parent_path();
    const auto applicationRoot = productRoot / L"app";
    const auto controlCenter = applicationRoot / L"VaporLockControlCenter.exe";
    if (!std::filesystem::exists(controlCenter)) {
        MessageBoxW(nullptr, L"Vapor Lock's control center is incomplete. Keep every file in the Vapor Lock folder together.", L"Vapor Lock", MB_ICONERROR);
        return 1;
    }

    SetCurrentDirectoryW(applicationRoot.c_str());
    std::wstring command = quote(controlCenter);
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(controlCenter.c_str(), command.data(), nullptr, nullptr, FALSE, 0, nullptr, applicationRoot.c_str(), &startup, &process)) {
        MessageBoxW(nullptr, L"Vapor Lock could not start its control center.", L"Vapor Lock", MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exitCode = 1;
    GetExitCodeProcess(process.hProcess, &exitCode);
    CloseHandle(process.hProcess);
    return static_cast<int>(exitCode);
}
