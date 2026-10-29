#pragma once
#include <windows.h>
#include <algorithm>
#include <array>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

// Worker-only filesystem work. Every opened ancestor remains held without
// FILE_SHARE_WRITE or FILE_SHARE_DELETE until the owner retires. List access
// participates in ordinary sharing checks; attribute/reparse exclusion is NOT
// proved by this amendment. A failed close keeps its HANDLE.
// Network/device roots and ambiguous Win32 names are unavailable, never replaced
// by a fixture or another directory. The native empty setting alone chooses the
// existing defaultDatasetRoot(). R7 owned-file checks passed; complete reparse
// confinement and actual production capture remain separate release gates.
class CaptureRoot {
public:
    static constexpr std::size_t maximumHandles = 64;
    static std::shared_ptr<CaptureRoot> prepare(const std::wstring& selected) {
        auto result = std::shared_ptr<CaptureRoot>(new CaptureRoot());
        try {
            const auto native = selected.empty() ? defaultDatasetRoot().wstring() : selected;
            if (native.empty() || native.size() > 32700 || native.find(L'\0') != std::wstring::npos)
                throw std::runtime_error("invalid-root");
            auto path = native;
            std::replace(path.begin(), path.end(), L'/', L'\\');
            // Deliberately bounded local drive roots; no UNC, device prefix,
            // ADS, drive-relative path, traversal or hidden Win32 normalization.
            if (path.size() < 3 || !((path[0] >= L'A' && path[0] <= L'Z') || (path[0] >= L'a' && path[0] <= L'z'))
                || path[1] != L':' || path[2] != L'\\') throw std::runtime_error("unsupported-root");
            // Validate the entire selection before creating any directory.
            std::vector<std::wstring> parts;
            parts.reserve(maximumHandles - 2);
            std::size_t start = 3;
            while (start < path.size()) {
                const auto end = path.find(L'\\', start);
                const auto part = path.substr(start, end == std::wstring::npos ? end : end - start);
                validatePart(part);
                if (parts.size() == maximumHandles - 2) throw std::runtime_error("root-depth-limit");
                parts.push_back(part);
                if (end == std::wstring::npos) break;
                start = end + 1;
                if (start == path.size()) throw std::runtime_error("invalid-root");
            }
            const auto drive = path.substr(0, 3);
            requireLocalDrive(drive); // mapped remote letters fail before effects
            result->hold(drive, false);
            // The held root supplies the volume identity. Do not use the drive
            // letter again: remapping that alias cannot redirect child effects.
            std::wstring current = result->m_volumeRoot;
            for (const auto& part : parts) {
                if (current.back() != L'\\') current += L'\\';
                current += part;
                result->hold(current, true);
            }
            if (current.back() != L'\\') current += L'\\';
            current += L"images";
            result->hold(current, true);
            result->m_images = current;
            result->m_displayImages = path + (path.back() == L'\\' ? L"" : L"\\") + L"images";
            result->m_ready = true;
        } catch (...) {
            result->m_ready = false;
            result->close(); // failed close stays owned in the returned object
        }
        return result;
    }
    bool ready() const { return m_ready; }
    const std::wstring& images() const { return m_images; }
    const std::wstring& displayImages() const { return m_displayImages; }
    bool close() {
        m_ready = false;
        bool complete = true;
        for (auto it = m_handles.rbegin(); it != m_handles.rend(); ++it) {
            if (*it != INVALID_HANDLE_VALUE) {
                if (CloseHandle(*it)) *it = INVALID_HANDLE_VALUE;
                else complete = false;
            }
        }
        if (complete) m_handles.clear();
        return complete;
    }
    bool hasHandles() const { return !m_handles.empty(); }
    ~CaptureRoot() {
        // Owners call and confirm close before release. The fallback preserves
        // unknown handles even on allocation/exception paths; never a fake ack.
        if (!close()) {
            auto* retained = new std::vector<HANDLE>();
            retained->swap(m_handles);
        }
    }
private:
    static void requireLocalDrive(const std::wstring& root) {
        const auto type = GetDriveTypeW(root.c_str());
        if (type != DRIVE_FIXED && type != DRIVE_REMOVABLE)
            throw std::runtime_error("unsupported-root-volume");
    }
    static std::wstring finalName(HANDLE handle) {
        std::array<wchar_t, 32768> buffer{};
        const auto length = GetFinalPathNameByHandleW(handle, buffer.data(),
            static_cast<DWORD>(buffer.size()), FILE_NAME_NORMALIZED | VOLUME_NAME_GUID);
        if (!length || length >= buffer.size() || buffer[length] != L'\0')
            throw std::runtime_error("root-volume-query-failed");
        return std::wstring(buffer.data(), length);
    }
    static bool volumeRoot(const std::wstring& path) {
        // Exactly \\?\Volume{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}\ . A SUBST
        // suffix, remote path, non-Mount-Manager device, or malformed query
        // has no accepted root identity and cannot reach creation.
        if (path.size() != 49 || path.compare(0, 11, L"\\\\?\\Volume{") != 0
            || path[47] != L'}' || path[48] != L'\\') return false;
        for (std::size_t i = 11; i < 47; ++i) {
            if (i == 19 || i == 24 || i == 29 || i == 34) {
                if (path[i] != L'-') return false;
            } else if (!((path[i] >= L'0' && path[i] <= L'9')
                || (path[i] >= L'a' && path[i] <= L'f')
                || (path[i] >= L'A' && path[i] <= L'F'))) return false;
        }
        return true;
    }
    void verifyHeld(HANDLE handle, const std::wstring& expected) const {
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(handle, &info)
            || !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
            || info.dwVolumeSerialNumber != m_volumeSerial)
            throw std::runtime_error("root-volume-or-reparse-mismatch");
        const auto actual = finalName(handle);
        if (CompareStringOrdinal(actual.c_str(), static_cast<int>(actual.size()),
            expected.c_str(), static_cast<int>(expected.size()), TRUE) != CSTR_EQUAL)
            throw std::runtime_error("root-canonical-path-mismatch");
    }
    static void validatePart(const std::wstring& part) {
        if (part.empty() || part == L"." || part == L".." || part.size() > 255
            || part.back() == L'.' || part.back() == L' ') throw std::runtime_error("invalid-root");
        for (const auto c : part)
            if (c < 32 || c == L':' || c == L'<' || c == L'>' || c == L'"' || c == L'|' || c == L'?' || c == L'*')
                throw std::runtime_error("invalid-root");
        auto base = part.substr(0, part.find(L'.'));
        for (auto& c : base) if (c >= L'a' && c <= L'z') c -= L'a' - L'A';
        if (base == L"CON" || base == L"PRN" || base == L"AUX" || base == L"NUL"
            || (base.size() == 4 && (base.substr(0, 3) == L"COM" || base.substr(0, 3) == L"LPT")
                && base[3] >= L'0' && base[3] <= L'9')) throw std::runtime_error("invalid-root");
    }
    void hold(const std::wstring& path, bool create) {
        if (m_handles.size() == maximumHandles) throw std::runtime_error("root-depth-limit");
        // Parents have already been opened/validated/held before creating any
        // child. Open with OPEN_REPARSE_POINT even when CreateDirectory races.
        if (create) {
            if (m_volumeRoot.empty() || m_handles.empty()) throw std::runtime_error("root-identity-missing");
            requireLocalDrive(m_volumeRoot);
            verifyHeld(m_handles.back(), m_previousPath);
            if (!CreateDirectoryW(path.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)
                throw std::runtime_error("root-create-failed");
        }
        const auto handle = CreateFileW(path.c_str(), FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle == INVALID_HANDLE_VALUE) throw std::runtime_error("root-open-failed");
        // Reserve occurs before acquiring native ownership; push cannot throw
        // after the HANDLE has been opened.
        m_handles.push_back(handle);
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(handle, &info) || !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) throw std::runtime_error("root-reparse-or-invalid");
        if (m_volumeRoot.empty()) {
            const auto canonical = finalName(handle);
            if (!volumeRoot(canonical)) throw std::runtime_error("unsupported-root-volume");
            requireLocalDrive(canonical);
            m_volumeRoot = canonical;
            m_volumeSerial = info.dwVolumeSerialNumber;
            m_previousPath = canonical;
        } else {
            verifyHeld(handle, path);
            m_previousPath = path;
        }
    }
    CaptureRoot() { m_handles.reserve(maximumHandles); }
    bool m_ready = false;
    std::wstring m_images;
    std::wstring m_displayImages;
    std::wstring m_volumeRoot;
    std::wstring m_previousPath;
    DWORD m_volumeSerial = 0;
    std::vector<HANDLE> m_handles;
};
