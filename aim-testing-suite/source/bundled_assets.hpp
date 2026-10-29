#pragma once
#include <filesystem>
#include <span>
namespace suite {
// These virtual paths are served from this executable's read-only resources.
std::span<const unsigned char> bundledAsset(const std::filesystem::path& relative);
std::span<const unsigned char> bundledResource(int id);
void preparePortableLayout();
}
