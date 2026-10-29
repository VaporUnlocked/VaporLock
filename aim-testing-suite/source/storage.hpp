#pragma once
#include "simulation.hpp"
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>
namespace suite {
namespace fs=std::filesystem;
inline fs::path pathFromUtf8(std::string_view value){return fs::path(std::u8string(reinterpret_cast<const char8_t*>(value.data()),value.size()));}
fs::path executableFolder();
bool isReparsePoint(const fs::path& path);
unsigned long ownProcessId();
void showStartupWindow(void* nativeHandle,bool activate);
bool ownWindowIsVisibleOnScreen(void* nativeHandle);
bool openBrandWebsite(void* nativeHandle);
void showError(const std::string& text);
void replaceFile(const fs::path& temporary,const fs::path& destination);
void establishRoot(fs::path root);
const fs::path& root();
bool confined(const fs::path& path);
fs::path local(const fs::path& path);
std::string utf8(const fs::path& path);
std::vector<unsigned char> readBytes(const fs::path& path);
void writeText(const fs::path& path,const std::string& text);
void installFileCallbacks();
Settings loadSettings(const fs::path& name="settings.ini");
void saveSettings(const Settings& settings,const fs::path& name="settings.ini");
std::vector<fs::path> findModels();
fs::path findModel(const std::string& filename);
bool validateGlb(const fs::path& path,std::string& reason);
std::string timestamp();
std::string jsonEscape(const std::string& input);
}
