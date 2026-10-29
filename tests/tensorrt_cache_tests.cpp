#include "app/TensorRtCache.h"
#include <atomic>
#include <iostream>
#include <thread>

namespace cache = vaporlock::tensorrt_cache;
void require(bool ok, const char* detail) { if (!ok) throw std::runtime_error(detail); }
int main() {
    auto root = std::filesystem::temp_directory_path() / (L"VaporLock-cache-test-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
    try {
        require(cache::digest("abc", 3) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "SHA256 known vector");
        require(cache::elements({1,3,640,640}) == 1228800, "Normal image shape");
        for (const auto& shape : std::vector<std::vector<std::int64_t>>{{}, {0}, {-1}, {INT64_MAX,2}, {1,1,1,1,1,1,1,1,1}, {128*1024*1024,2}})
            require(cache::elements(shape) == 0, "Invalid shape accepted");
        require(std::filesystem::create_directory(root), "Unique scratch directory");
        auto path = root / L"engine.vlcache";
        const std::string identity = "fixture-model-gpu-driver-shape";
        auto key = cache::digest(identity.data(), identity.size());
        std::vector<char> a(32768,'a'), b(32768,'b');
        require(cache::save(path,key,a.data(),a.size()), "Save cache");
        require(cache::load(path,key)==a, "Roundtrip cache");
        require(cache::load(path,std::string(64,'0')).empty(), "Foreign compatibility key accepted");
        require(cache::readBounded(path,16).empty(), "Bounded read accepted oversized file");
        { std::fstream f(path,std::ios::binary|std::ios::in|std::ios::out); f.seekp(140); f.put('x'); }
        require(cache::load(path,key).empty(), "Corrupt payload accepted");
        { std::ofstream f(path,std::ios::binary|std::ios::trunc); f << "VLTRTC02"; }
        require(cache::load(path,key).empty(), "Truncated envelope accepted");
        std::atomic<int> successes{0};
        auto writer = [&](const std::vector<char>& payload) { for(int i=0;i<15;++i) if(cache::save(path,key,payload.data(),payload.size())) ++successes; };
        std::thread first(writer,std::cref(a)), second(writer,std::cref(b));
        first.join(); second.join();
        auto final = cache::load(path,key);
        require(successes>0 && (final==a || final==b), "Concurrent write mixed payloads");
        for(const auto& entry:std::filesystem::directory_iterator(root)) require(entry.path()==path,"Partial file leaked");
        auto linked = root / L"linked.vlcache";
        if(CreateSymbolicLinkW(linked.c_str(),path.c_str(),SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE)) {
            require(cache::load(linked,key).empty(),"Symlink cache read accepted");
            require(!cache::save(linked,key,b.data(),b.size()),"Symlink cache write accepted");
            std::filesystem::remove(linked);
        } else std::cout << "Symlink fixture unavailable on this host; not counted as passed.\n";
        // Remove only the two exact owned fixture paths; no recursive cleanup.
        std::filesystem::remove(path); std::filesystem::remove(root);
        std::cout << "TensorRT cache integrity, bounds, atomic concurrency and shape tests passed.\n";
        return 0;
    } catch(const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
