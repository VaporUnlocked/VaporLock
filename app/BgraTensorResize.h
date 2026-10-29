#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <tuple>
#include <type_traits>
#include <vector>

namespace vaporlock::image {
// Cache only geometry, never pixels. Preserve the original half-pixel sampling,
// padding, operation order and normalization for every provider and CPU type.
class BgraTensorResize {
    struct Axis { int lo=0, hi=0; float weight=0; bool outside=false; };
    std::vector<Axis> m_x, m_y;
    std::tuple<int,int,int,int,float,float,float,float> m_key{};
    static std::vector<Axis> axis(int input, int output, float scale, float pad) {
        std::vector<Axis> result(output);
        for (int i=0;i<output;++i) {
            float p=(static_cast<float>(i)-pad+0.5F)/scale-0.5F;
            auto& a=result[i];
            a.outside=p < -0.5F || p >= input-0.5F;
            if(a.outside) continue;
            p=std::clamp(p,0.0F,input-1.0F);
            a.lo=static_cast<int>(p); a.hi=std::min(a.lo+1,input-1); a.weight=p-a.lo;
        }
        return result;
    }
public:
    template<class T>
    void run(std::span<const std::uint8_t> pixels, std::span<T> output, int width, int height,
             int targetWidth, int targetHeight, float scaleX, float scaleY, float padX, float padY, bool nhwc) {
        static_assert(std::is_same_v<T,float> || std::is_same_v<T,std::uint8_t>);
        if(width<=0 || height<=0 || targetWidth<=0 || targetHeight<=0
            || width>32768 || height>32768 || targetWidth>32768 || targetHeight>32768
            || !std::isfinite(scaleX) || !std::isfinite(scaleY) || scaleX<=0 || scaleY<=0
            || !std::isfinite(padX) || !std::isfinite(padY)
            || static_cast<std::size_t>(width)*height*4 != pixels.size()
            || static_cast<std::size_t>(targetWidth)*targetHeight*3 != output.size())
            throw std::invalid_argument("Invalid resize preprocessing buffers or geometry.");
        const auto key=std::make_tuple(width,height,targetWidth,targetHeight,scaleX,scaleY,padX,padY);
        if(m_x.empty() || key!=m_key) {
            auto x=axis(width,targetWidth,scaleX,padX), y=axis(height,targetHeight,scaleY,padY);
            m_x=std::move(x); m_y=std::move(y); m_key=key;
        }
        const std::size_t plane=static_cast<std::size_t>(targetWidth)*targetHeight;
        const auto convert=[](float value)->T {
            if constexpr(std::is_same_v<T,float>) return value/255.0F;
            else return static_cast<std::uint8_t>(std::clamp(value,0.0F,255.0F));
        };
        for(int y=0;y<targetHeight;++y) {
            const auto& ay=m_y[y];
            const auto* upper=pixels.data()+static_cast<std::size_t>(ay.lo)*width*4;
            const auto* lower=pixels.data()+static_cast<std::size_t>(ay.hi)*width*4;
            const float invY=1.0F-ay.weight;
            for(int x=0;x<targetWidth;++x) {
                const auto& ax=m_x[x];
                const std::size_t at=static_cast<std::size_t>(y)*targetWidth+x;
                const float invX=1.0F-ax.weight;
                for(int c=0;c<3;++c) {
                    float value=114.0F;
                    if(!ax.outside && !ay.outside) {
                        const int channel=2-c;
                        const float a=upper[ax.lo*4+channel]*invX+upper[ax.hi*4+channel]*ax.weight;
                        const float b=lower[ax.lo*4+channel]*invX+lower[ax.hi*4+channel]*ax.weight;
                        value=a*invY+b*ay.weight;
                    }
                    output[nhwc ? at*3+c : plane*c+at]=convert(value);
                }
            }
        }
    }
};
}
