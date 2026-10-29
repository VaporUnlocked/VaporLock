#pragma once
#include <windows.h>
#include <string>
#include <stdexcept>
namespace vaporlock::inference {
inline std::string cudaDeviceName(int index) {
    struct Library { HMODULE value; ~Library(){if(value)FreeLibrary(value);} } lib{LoadLibraryExW(L"nvcuda.dll",nullptr,LOAD_LIBRARY_SEARCH_SYSTEM32)};
    if(!lib.value || index<0 || index>=64) throw std::runtime_error("Selected CUDA GPU is unavailable.");
    const auto init=reinterpret_cast<int(WINAPI*)(unsigned)>(GetProcAddress(lib.value,"cuInit"));
    const auto countFn=reinterpret_cast<int(WINAPI*)(int*)>(GetProcAddress(lib.value,"cuDeviceGetCount"));
    const auto get=reinterpret_cast<int(WINAPI*)(int*,int)>(GetProcAddress(lib.value,"cuDeviceGet"));
    const auto nameFn=reinterpret_cast<int(WINAPI*)(char*,int,int)>(GetProcAddress(lib.value,"cuDeviceGetName"));
    int count=0,device=0;char name[256]{};
    if(!init || !countFn || !get || !nameFn || init(0) || countFn(&count) || index>=count
        || get(&device,index) || nameFn(name,256,device)) throw std::runtime_error("Selected CUDA GPU is unavailable.");
    return name;
}
}
