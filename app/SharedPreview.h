#pragma once

#include <windows.h>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string>
#include <vector>

namespace vaporlock::io {

// Windows pagefile-backed preview transport. The control center creates the
// objects under a per-launch name. Stdout carries only the frame identity.
class SharedPreview {
public:
    static constexpr std::size_t maxPixels = 3840U * 2160U;
    static constexpr std::size_t slotCount = 3;
    static constexpr std::size_t slotBytes = 16 + maxPixels * 4;

    explicit SharedPreview(const std::wstring& token) {
        if (token.size() != 32 || token.find_first_not_of(L"0123456789abcdef") != token.npos) return;
        const auto name = L"Local\\VaporLockPreview-" + token;
        m_mapping = OpenFileMappingW(FILE_MAP_WRITE, FALSE, name.c_str());
        m_mutex = OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE, FALSE, (name + L"-lock").c_str());
        if (m_mapping && m_mutex)
            m_data = static_cast<unsigned char*>(MapViewOfFile(m_mapping, FILE_MAP_WRITE, 0, 0, slotBytes * slotCount));
    }

    ~SharedPreview() {
        if (m_data) UnmapViewOfFile(m_data);
        if (m_mutex) CloseHandle(m_mutex);
        if (m_mapping) CloseHandle(m_mapping);
    }
    SharedPreview(const SharedPreview&) = delete;
    SharedPreview& operator=(const SharedPreview&) = delete;

    bool available() const { return m_data != nullptr; }

    std::optional<std::size_t> write(std::uint64_t frame, int width, int height,
                                     std::vector<unsigned char>& pixels) {
        if (!m_data || !frame || width <= 0 || height <= 0 || pixels.size() > maxPixels * 4
            || pixels.size() != static_cast<std::size_t>(width) * height * 4) return std::nullopt;
        // Normalize undefined desktop alpha while preserving every RGB byte.
        for (std::size_t i = 3; i < pixels.size(); i += 4) pixels[i] = 255;
        const DWORD state = WaitForSingleObject(m_mutex, 0);
        if (state != WAIT_OBJECT_0 && state != WAIT_ABANDONED) return std::nullopt;
        const std::size_t slot = m_next;
        auto* data = m_data + slot * slotBytes;
        const Header header{frame, static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height)};
        std::memcpy(data, &header, sizeof(header));
        std::memcpy(data + sizeof(header), pixels.data(), pixels.size());
        m_next = (slot + 1) % slotCount;
        ReleaseMutex(m_mutex);
        return slot;
    }

private:
    struct Header { std::uint64_t frame; std::uint32_t width, height; };
    static_assert(sizeof(Header) == 16);
    HANDLE m_mapping = nullptr, m_mutex = nullptr;
    unsigned char* m_data = nullptr;
    std::size_t m_next = 0;
};

} // namespace vaporlock::io
