#include "app/BgraTensorResize.h"
#include <chrono>
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <random>
#include <vector>

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

// Independent scalar reference retains the original bilinear sampling path.
// Deliberately retains interpolation and indexing, independent of the new copy.
template<class T>
static void reference(const std::vector<std::uint8_t>& bgra, int width, int height, bool nhwc, int tw, int th, float scaleX, float scaleY, float padX, float padY, std::vector<T>& result) {
    const std::size_t plane = static_cast<std::size_t>(tw) * th;
    result.resize(plane * 3);
    for (int dy = 0; dy < th; ++dy) for (int dx = 0; dx < tw; ++dx) {
        float sx = (static_cast<float>(dx) - padX + 0.5F) / scaleX - 0.5F;
        float sy = (static_cast<float>(dy) - padY + 0.5F) / scaleY - 0.5F;
        bool outside=sx < -0.5F || sy < -0.5F || sx >= width-0.5F || sy >= height-0.5F;
        sx = std::clamp(sx, 0.0F, width - 1.0F);
        sy = std::clamp(sy, 0.0F, height - 1.0F);
        const int x0 = static_cast<int>(sx), y0 = static_cast<int>(sy);
        const int x1 = std::min(x0 + 1, width - 1), y1 = std::min(y0 + 1, height - 1);
        const float wx = sx - x0, wy = sy - y0;
        const auto sample = [&](int x, int y, int channel) { return static_cast<float>(bgra[(static_cast<std::size_t>(y)*width+x)*4+channel]); };
        const auto bilinear = [&](int channel) {
            const float upper = sample(x0,y0,channel)*(1.0F-wx)+sample(x1,y0,channel)*wx;
            const float lower = sample(x0,y1,channel)*(1.0F-wx)+sample(x1,y1,channel)*wx;
            return upper*(1.0F-wy)+lower*wy;
        };
        const std::size_t at = static_cast<std::size_t>(dy)*tw+dx;
        for (int channel=0;channel<3;++channel) {
            const float value = outside ? 114.0F : bilinear(2-channel);
            const auto index = nhwc ? at*3+channel : plane*channel+at;
            if constexpr (std::is_same_v<T,float>) result[index]=value/255.0F;
            else result[index]=static_cast<std::uint8_t>(std::clamp(value,0.0F,255.0F));
        }
    }

}

template<class T>
static void compare(vaporlock::image::BgraTensorResize& plan,int w,int h,int tw,int th,bool nhwc,bool letterbox){
 std::mt19937 rng(w+h+tw+th);std::vector<std::uint8_t> pixels(static_cast<size_t>(w)*h*4);
 for(auto& v:pixels)v=static_cast<uint8_t>(rng());
 float sx=float(tw)/w,sy=float(th)/h,px=0,py=0;
 if(letterbox){sx=sy=std::min(sx,sy);px=std::floor((tw-w*sx)*.5F);py=std::floor((th-h*sy)*.5F);}
 std::vector<T> expected;reference<T>(pixels,w,h,nhwc,tw,th,sx,sy,px,py,expected);
 std::vector<T> out(expected.size()+2,static_cast<T>(42));
 for(int retry=0;retry<3;++retry){
 plan.run<T>(pixels,std::span<T>(out).subspan(1,expected.size()),w,h,tw,th,sx,sy,px,py,nhwc);
 require(out.front()==42&&out.back()==42,"Output bounds changed");
 require(!memcmp(out.data()+1,expected.data(),expected.size()*sizeof(T)),"Resize not bit-exact");
 for(size_t i=3;i<pixels.size();i+=4)pixels[i]^=255;
 if(retry==1){for(size_t i=0;i<pixels.size();i+=4)pixels[i]^=127;
 reference<T>(pixels,w,h,nhwc,tw,th,sx,sy,px,py,expected);}
 }
}
int main(){try{
 vaporlock::image::BgraTensorResize plan;int cases=0;
 for(auto size:std::array<std::array<int,4>,9>{{{1000,1000,640,640},{640,480,640,640},{1,7,11,3},{17,11,23,31},{1920,1080,640,640},{640,640,320,320},{320,640,641,479},{1,1,4,3},{641,479,640,640}}})
 for(bool nhwc:{false,true})for(bool letterbox:{false,true}){
 compare<float>(plan,size[0],size[1],size[2],size[3],nhwc,letterbox);
 compare<uint8_t>(plan,size[0],size[1],size[2],size[3],nhwc,letterbox);cases+=2;
 }
 std::vector<uint8_t> pixels(1000*1000*4);std::mt19937 rng(42);for(auto& v:pixels)v=uint8_t(rng());
 std::vector<float> out(640*640*3), old(out.size());double checksum=0;
 for(int repeat=0;repeat<3;++repeat){
 auto start=std::chrono::steady_clock::now();
 for(int i=0;i<30;++i){reference<float>(pixels,1000,1000,false,640,640,.64F,.64F,0,0,old);checksum+=old[(i*101)%old.size()];}
 auto middle=std::chrono::steady_clock::now();
 for(int i=0;i<30;++i){plan.run<float>(pixels,out,1000,1000,640,640,.64F,.64F,0,0,false);checksum+=out[(i*101)%out.size()];}
 auto end=std::chrono::steady_clock::now();
 std::cout<<"resize benchmark "<<repeat<<": original "<<std::chrono::duration<double,std::milli>(middle-start).count()/30<<" ms, candidate "<<std::chrono::duration<double,std::milli>(end-middle).count()/30<<" ms\n";
 }
 bool rejected=false;std::vector<float> bad(2,42);try{plan.run<float>(pixels,bad,1000,1000,640,640,0,1,0,0,false);}catch(const std::invalid_argument&){rejected=true;}
 require(rejected&&bad==std::vector<float>(2,42),"Invalid geometry accepted");
 std::cout<<cases<<" bit-exact resize cases; float/byte NCHW/NHWC, crop/padding/edges, cache changes/reuse, alpha and guards passed; checksum "<<checksum<<"\n";
 return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
