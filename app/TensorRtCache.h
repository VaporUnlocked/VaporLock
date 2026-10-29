#pragma once
#include <windows.h>
#include <bcrypt.h>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace vaporlock::tensorrt_cache {
constexpr std::size_t maximumBytes = 512U * 1024U * 1024U;
constexpr std::size_t maximumElements = 128U * 1024U * 1024U;

inline std::size_t elements(const std::vector<std::int64_t>& shape) {
    if (shape.empty() || shape.size() > 8) return 0;
    std::size_t n = 1;
    for (auto d : shape) {
        if (d <= 0 || static_cast<std::uint64_t>(d) > maximumElements / n) return 0;
        n *= static_cast<std::size_t>(d);
    }
    return n;
}

inline bool plain(const std::filesystem::path& path) {
    for (auto p = path; !p.empty(); p = p.parent_path()) {
        auto attributes = GetFileAttributesW(p.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_REPARSE_POINT)) return false;
        if (p == p.parent_path()) break;
    }
    return true;
}

inline std::string digest(const void* data, std::size_t size) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (size > maximumBytes || BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        throw std::runtime_error("TensorRT cache hashing failed.");
    std::array<unsigned char, 32> bytes{};
    auto result = BCryptHash(algorithm, nullptr, 0, reinterpret_cast<PUCHAR>(const_cast<void*>(data)),
                            static_cast<ULONG>(size), bytes.data(), static_cast<ULONG>(bytes.size()));
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (result < 0) throw std::runtime_error("TensorRT cache hashing failed.");
    std::string text; text.reserve(64);
    constexpr char hex[] = "0123456789abcdef";
    for (auto b : bytes) { text += hex[b >> 4]; text += hex[b & 15]; }
    return text;
}

inline std::vector<char> readBounded(const std::filesystem::path& path, std::size_t limit = maximumBytes) {
    if (!plain(path)) return {};
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    auto length = f ? f.tellg() : std::streampos(-1);
    if (length <= std::streampos(0) || static_cast<std::uint64_t>(length) > limit) return {};
    std::vector<char> bytes(static_cast<std::size_t>(length));
    f.seekg(0); f.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (!f || f.peek() != std::char_traits<char>::eof()) return {};
    return bytes;
}

// One atomically replaced envelope binds payload integrity and compatibility.
// No separate metadata file can become paired with an engine from another job.
inline std::vector<char> load(const std::filesystem::path& path, const std::string& identity) {
    auto bytes = readBounded(path);
    constexpr std::size_t header = 8 + 64 + 64;
    if (identity.size() != 64 || bytes.size() <= header || std::string(bytes.data(), 8) != "VLTRTC02"
        || std::string(bytes.data() + 8, 64) != identity) return {};
    if (digest(bytes.data() + header, bytes.size() - header) != std::string(bytes.data() + 72, 64)) return {};
    return {bytes.begin() + header, bytes.end()};
}

inline bool save(const std::filesystem::path& path, const std::string& identity, const void* data, std::size_t size) {
    if (identity.size() != 64 || !size || size > maximumBytes - 136 || !plain(path)) return false;
    std::error_code ec; std::filesystem::create_directories(path.parent_path(), ec);
    if (ec || !plain(path)) return false;
    std::array<unsigned char, 16> nonce{};
    if (BCryptGenRandom(nullptr, nonce.data(), static_cast<ULONG>(nonce.size()), BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) return false;
    auto temporary = path.wstring() + L".";
    constexpr wchar_t hex[] = L"0123456789abcdef";
    for (auto b : nonce) { temporary += hex[b >> 4]; temporary += hex[b & 15]; }
    temporary += L".partial";
    std::string header = "VLTRTC02" + identity + digest(data, size);
    HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    auto write = [&](const void* p, std::size_t n) {
        DWORD written = 0;
        return WriteFile(file, p, static_cast<DWORD>(n), &written, nullptr) && written == n;
    };
    bool ok = write(header.data(), header.size()) && write(data, size) && FlushFileBuffers(file);
    CloseHandle(file);
    ok = ok && plain(path) && MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileW(temporary.c_str());
    return ok;
}
} // namespace vaporlock::tensorrt_cache
