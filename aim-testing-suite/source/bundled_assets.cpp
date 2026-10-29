#include "bundled_assets.hpp"
#include "storage.hpp"
#include "resource_ids.h"
#include <array>
#include <cwctype>
#include <stdexcept>

namespace suite {
std::span<const unsigned char> bundledAsset(const fs::path& relative) {
    auto key=relative.lexically_normal().generic_wstring();
    std::transform(key.begin(),key.end(),key.begin(),::towlower);
    struct Entry {const wchar_t* path;int id;};
    constexpr std::array entries{
        Entry{L"assets/characters/swat.glb",IDR_SWAT},
        Entry{L"assets/fonts/interface.ttf",IDR_FONT},
        Entry{L"licenses.txt",IDR_LICENSES},
        Entry{L"models/readme.txt",IDR_MODEL_GUIDE},
        Entry{L"portable-readme.txt",IDR_PORTABLE_GUIDE}
    };
    for(const auto& entry:entries)if(key==entry.path){
        return bundledResource(entry.id);
    }
    return {};
}
void preparePortableLayout() {
    try {
        fs::create_directories(local("models"));
        fs::create_directories(local("reports"));
        for(const char* name:{"LICENSES.txt","PORTABLE-README.txt","models/README.txt"}){
            auto destination=local(name);
            if(fs::exists(destination))continue; // Preserve the user's existing files.
            auto bytes=bundledAsset(pathFromUtf8(name));
            writeText(destination,std::string(reinterpret_cast<const char*>(bytes.data()),bytes.size()));
        }
    }catch(const std::exception&){
        throw std::runtime_error("The suite needs to create its models folder and local files beside the EXE. Move it to a writable folder, then reopen it. Linked folders are not supported.");
    }
}
}
