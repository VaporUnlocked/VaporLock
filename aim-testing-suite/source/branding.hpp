#pragma once
#include "raylib.h"
namespace suite {
// Palette observed on vaporlock.top; native layout and geometry are suite code.
inline constexpr const char* BrandName="VAPOR LOCK";
inline constexpr const char* BrandSite="https://github.com/VaporUnlocked/VaporLock";
inline constexpr Color BrandInk{3,6,11,255},BrandPanel{5,10,18,255},BrandCard{12,20,34,255};
inline constexpr Color BrandText{242,246,255,255},BrandMuted{154,170,192,255};
inline constexpr Color BrandIce{155,216,255,255},BrandBlue{79,145,232,255},BrandViolet{168,121,255,255};
inline Rectangle pauseCard(float w,float h){return {w/2-330,h/2-340,660,680};}
inline Rectangle websiteButton(float w,float h){auto c=pauseCard(w,h);return {c.x+32,c.y+580,596,44};}
inline Rectangle qualityField(float w,float h){auto c=pauseCard(w,h);return {c.x+230,c.y+320,398,48};}
inline Rectangle qualityOption(float w,float h,int option){auto f=qualityField(w,h);return {f.x,f.y+f.height+4+44*option,f.width,44};}
inline int nextMenuFocus(int current,bool reverse){return current<0?(reverse?5:0):(current+(reverse?5:1))%6;}
inline void brandMark(float x,float y,float size){
    DrawRectangleRounded({x,y,size,size},.16f,6,BrandCard);
    DrawRectangleRoundedLinesEx({x,y,size,size},.16f,6,1,BrandBlue);
    const auto p=[&](float a,float b){return Vector2{x+size*a,y+size*b};};
    DrawLineEx(p(.18f,.30f),p(.34f,.73f),size*.065f,BrandIce);
    DrawLineEx(p(.34f,.73f),p(.50f,.30f),size*.065f,BrandIce);
    DrawLineEx(p(.64f,.30f),p(.64f,.73f),size*.065f,BrandIce);
    DrawLineEx(p(.64f,.73f),p(.86f,.73f),size*.065f,BrandIce);
}
}
