#pragma once
#include <cstdint>
#include <string_view>

namespace vaporlock::tensorrt_cache {
// Cache eligibility only, not an ONNX validator or a model loader. Unknown,
// malformed, overly nested or externally stored tensors disable disk caching.
// NVIDIA still validates/builds the model normally. Field numbers follow the
// ONNX Model/Graph/Node/Attribute/Tensor/SparseTensor/Function/TrainingInfo schema.
namespace onnx_cache_detail {
enum class Kind { Model, Graph, Node, Attribute, Tensor, Sparse, Function, Training, Leaf, Unknown };
inline Kind child(Kind kind, unsigned f) {
    switch(kind) {
    case Kind::Model:
        if(f==7) return Kind::Graph;
        if(f==20) return Kind::Training;
        if(f==25) return Kind::Function;
        if((f>=1&&f<=6)||f==8||f==14||f==26) return Kind::Leaf;
        break;
    case Kind::Graph:
        if(f==1) return Kind::Node;
        if(f==5) return Kind::Tensor;
        if(f==15) return Kind::Sparse;
        if(f==2||(f>=10&&f<=14)||f==16) return Kind::Leaf;
        break;
    case Kind::Node:
        if(f==5) return Kind::Attribute;
        if(f>=1&&f<=10) return Kind::Leaf;
        break;
    case Kind::Attribute:
        if(f==5||f==10) return Kind::Tensor;
        if(f==6||f==11) return Kind::Graph;
        if(f==22||f==23) return Kind::Sparse;
        if((f>=1&&f<=4)||(f>=7&&f<=9)||(f>=13&&f<=15)||f==20||f==21) return Kind::Leaf;
        break;
    case Kind::Tensor:
        if((f>=1&&f<=12)||f==14||f==16) return Kind::Leaf;
        break; // external_data (13) always denies caching, even if incomplete.
    case Kind::Sparse:
        if(f==1||f==2) return Kind::Tensor;
        if(f==3) return Kind::Leaf;
        break;
    case Kind::Function:
        if(f==7) return Kind::Node;
        if(f==11) return Kind::Attribute;
        if(f==1||(f>=4&&f<=6)||(f>=8&&f<=10)||(f>=12&&f<=14)) return Kind::Leaf;
        break;
    case Kind::Training:
        if(f==1||f==2) return Kind::Graph;
        if(f==3||f==4) return Kind::Leaf;
        break;
    default: break;
    }
    return Kind::Unknown;
}
inline bool varint(std::string_view& data, std::uint64_t& value) {
    value=0;
    for(unsigned i=0;i<10;++i) {
        if(data.empty()) return false;
        auto b=static_cast<unsigned char>(data.front());data.remove_prefix(1);
        if(i==9 && b>1) return false;
        value|=static_cast<std::uint64_t>(b&127)<<(7*i);
        if(!(b&128)) return true;
    }
    return false;
}
inline bool inspect(std::string_view data, Kind kind, unsigned depth, std::size_t& budget) {
    if(depth>64) return false;
    bool graph=false;
    while(!data.empty()) {
        if(!budget--) return false;
        std::uint64_t tag=0,value=0;
        if(!varint(data,tag)||!(tag>>3)||tag>>3>0x1fffffff) return false;
        auto f=static_cast<unsigned>(tag>>3);auto wire=tag&7;
        auto next=child(kind,f);
        if(next==Kind::Unknown) return false;
        if(kind==Kind::Model && f==7) graph=true;
        if(wire==0) {
            if(next!=Kind::Leaf||!varint(data,value)) return false;
            if(kind==Kind::Tensor && f==14 && value!=0) return false;
        } else if(wire==2) {
            if(!varint(data,value)||value>data.size()) return false;
            if(kind==Kind::Tensor && f==14) return false;
            auto nested=data.substr(0,static_cast<std::size_t>(value));data.remove_prefix(static_cast<std::size_t>(value));
            if(next!=Kind::Leaf && !inspect(nested,next,depth+1,budget)) return false;
        } else if(wire==1||wire==5) {
            const std::size_t bytes=wire==1?8:4;
            if(next!=Kind::Leaf||data.size()<bytes||(kind==Kind::Tensor&&f==14)) return false;
            data.remove_prefix(bytes);
        } else return false;
    }
    return kind!=Kind::Model||graph;
}
} // namespace onnx_cache_detail
inline bool isSelfContainedOnnx(std::string_view bytes) {
    std::size_t budget=2000000;
    return !bytes.empty() && bytes.size()<=512U*1024U*1024U
        && onnx_cache_detail::inspect(bytes,onnx_cache_detail::Kind::Model,0,budget);
}
} // namespace vaporlock::tensorrt_cache
