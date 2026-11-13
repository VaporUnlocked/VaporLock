#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

namespace vaporlock::image {
// Cache only geometry, never pixels. Preserve the original half-pixel sampling,
// padding, operation order and normalization for every provider and CPU type.
class BgraTensorResize {
    struct Axis { int lo=0, hi=0; float weight=0; bool outside=false; };
    std::vector<Axis> m_x, m_y;
    std::tuple<int,int,int,int,float,float,float,float> m_key{};
    int m_firstColumn = 0, m_lastColumn = 0;
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

    template<class T>
    static T convert(float value) {
        if constexpr (std::is_same_v<T, float>) return value / 255.0F;
        else return static_cast<std::uint8_t>(std::clamp(value, 0.0F, 255.0F));
    }

    template<bool Nhwc, class T>
    void writeRows(std::span<const std::uint8_t> pixels, std::span<T> output,
                   int width, int targetWidth, int targetHeight) const {
        constexpr int stride = Nhwc ? 3 : 1;
        const auto padding = convert<T>(114.0F);
        const std::size_t plane = static_cast<std::size_t>(targetWidth) * targetHeight;
        for (int y = 0; y < targetHeight; ++y) {
            T* red = output.data() + static_cast<std::size_t>(y) * targetWidth * stride;
            T* green = red + (Nhwc ? 1 : plane);
            T* blue = red + (Nhwc ? 2 : plane * 2);
            const auto fillColumns = [&](int begin, int end) {
                if constexpr (Nhwc) {
                    std::fill(red + begin * stride, red + end * stride, padding);
                } else {
                    std::fill(red + begin, red + end, padding);
                    std::fill(green + begin, green + end, padding);
                    std::fill(blue + begin, blue + end, padding);
                }
            };
            const auto& ay = m_y[y];
            if (ay.outside) {
                fillColumns(0, targetWidth);
                continue;
            }
            fillColumns(0, m_firstColumn);
            fillColumns(m_lastColumn, targetWidth);
            const auto* upper = pixels.data() + static_cast<std::size_t>(ay.lo) * width * 4;
            const auto* lower = pixels.data() + static_cast<std::size_t>(ay.hi) * width * 4;
            const float invY = 1.0F - ay.weight;
            for (int x = m_firstColumn; x < m_lastColumn; ++x) {
                const auto& ax = m_x[x];
                const auto left = ax.lo * 4;
                const auto right = ax.hi * 4;
                const float invX = 1.0F - ax.weight;
                const auto channel = [&](int component) {
                    const float a = upper[left + component] * invX + upper[right + component] * ax.weight;
                    const float b = lower[left + component] * invX + lower[right + component] * ax.weight;
                    return convert<T>(a * invY + b * ay.weight);
                };
                red[x * stride] = channel(2);
                green[x * stride] = channel(1);
                blue[x * stride] = channel(0);
            }
        }
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
            // With positive scale, in-bounds samples form one contiguous range.
            const auto inBounds = [](const Axis& sample) { return !sample.outside; };
            const auto first = std::find_if(x.begin(), x.end(), inBounds);
            const auto last = std::find_if(x.rbegin(), x.rend(), inBounds).base();
            m_firstColumn = static_cast<int>(first - x.begin());
            m_lastColumn = first == x.end() ? targetWidth : static_cast<int>(last - x.begin());
            m_x=std::move(x); m_y=std::move(y); m_key=key;
        }
        if (nhwc) writeRows<true>(pixels, output, width, targetWidth, targetHeight);
        else writeRows<false>(pixels, output, width, targetWidth, targetHeight);
    }
};
}
