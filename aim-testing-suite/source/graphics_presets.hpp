#pragma once
#include <algorithm>
namespace suite {
inline constexpr int DefaultQuality=2,QualityCount=4;
struct GraphicsPreset {const char* name;const char* description;int samples,shadowSize,shadowRadius;bool detail,glow;};
inline constexpr GraphicsPreset GraphicsPresets[]={
    {"Max","8x AA / ultra shadows / full effects",8,4096,1,true,true},
    {"High","4x AA / high shadows / full effects",4,2048,1,true,true},
    {"Mid","2x AA / light shadows / neon glow",2,1024,0,true,true},
    {"Potato","No AA or shadows / simple surfaces",0,0,0,false,false}
};
inline const GraphicsPreset& graphicsPreset(int quality){return GraphicsPresets[std::clamp(quality,0,QualityCount-1)];}
}
