#include "app/OnnxCacheEligibility.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>

std::string encoded(std::uint64_t n) {
    std::string value;
    do { auto b=static_cast<unsigned char>(n&127);n>>=7;value.push_back(static_cast<char>(b|(n?128:0))); } while(n);
    return value;
}
std::string message(unsigned field,const std::string& contents) {
    return encoded((field<<3)|2)+encoded(contents.size())+contents;
}

int wmain(int argc,wchar_t** argv) {
    try {
        if(argc!=2) throw std::runtime_error("Fixture directory required");
        int checked=0;
        for(const auto& entry:std::filesystem::directory_iterator(argv[1])) {
            if(entry.path().extension()!=L".onnx") continue;
            std::ifstream f(entry.path(),std::ios::binary);
            std::string bytes((std::istreambuf_iterator<char>(f)),{});
            bool expected=entry.path().filename().wstring().starts_with(L"inline-");
            if(vaporlock::tensorrt_cache::isSelfContainedOnnx(bytes)!=expected)
                throw std::runtime_error("Incorrect eligibility: "+entry.path().filename().string());
            for(std::size_t cut=0;cut<bytes.size();++cut) {
                // A truncated valid prefix can itself be a protobuf model;
                // corruption must remain bounded, never crash or overread.
                vaporlock::tensorrt_cache::isSelfContainedOnnx(std::string_view(bytes).substr(0,cut));
            }
            ++checked;
        }
        if(checked!=12) throw std::runtime_error("Missing ONNX eligibility fixtures");
        const auto basic=message(7,"");
        if(!vaporlock::tensorrt_cache::isSelfContainedOnnx(basic)) throw std::runtime_error("Empty structural graph fixture rejected");
        if(vaporlock::tensorrt_cache::isSelfContainedOnnx(basic+message(999,"future-field")))
            throw std::runtime_error("Unknown model field accepted for caching");
        std::string nested;
        for(int depth=0;depth<30;++depth) nested=message(1,message(5,message(6,nested)));
        if(vaporlock::tensorrt_cache::isSelfContainedOnnx(message(7,nested))) throw std::runtime_error("Depth bound ignored");
        std::string many;many.reserve(4000002);
        for(int i=0;i<2000001;++i) many+=message(2,"");
        if(vaporlock::tensorrt_cache::isSelfContainedOnnx(message(7,many))) throw std::runtime_error("Field budget ignored");
        for(const auto& invalid:std::vector<std::string>{"",std::string(11,'\xff'),std::string("\x3a\xff",2),std::string("\x3b",1)})
            if(vaporlock::tensorrt_cache::isSelfContainedOnnx(invalid)) throw std::runtime_error("Malformed model cached");
        std::cout<<checked<<" inline/external initializer, attribute, nested graph, function, sparse and training fixtures passed.\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
