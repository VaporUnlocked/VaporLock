#pragma once
#include "raylib.h"
#include "raymath.h"
#include "rlgl.h"
#include <stdexcept>
namespace suite {
inline constexpr Color NeonCyan{91,235,255,255},NeonBlue{108,159,255,255},NeonViolet{182,127,255,255},NeonPink{255,111,206,255};
inline constexpr Color NeonBayColors[]={NeonCyan,NeonBlue,NeonViolet,NeonPink};

// Draw only outside an accent's shape, before its solid fill and typography.
// No text texture or scene brightness is ever used as a glow source.
inline void neonBorder(Rectangle shape,Color color,float strength=.7f,float spread=15){
    for(int i=12;i>=1;i--){
        float t=float(i)/13,offset=spread*t;
        DrawRectangleLinesEx({shape.x-offset,shape.y-offset,shape.width+2*offset,shape.height+2*offset},spread/12+1,Fade(color,strength*.12f*(1-t)*(1-t)));
    }
}
struct RangeNeon {
    Shader core{},halo{};bool ready=false;
    void init(){
        core=LoadShaderFromMemory(nullptr,R"(#version 330
in vec4 fragColor;uniform vec4 colDiffuse;out vec4 finalColor;
void main(){finalColor=vec4(colDiffuse.rgb*fragColor.rgb,1.0);}
)");
        halo=LoadShaderFromMemory(nullptr,R"(#version 330
in vec2 fragTexCoord;in vec4 fragColor;out vec4 finalColor;
void main(){
 vec2 p=fragTexCoord*2.0-1.0;
 float softEdge=exp(-p.y*p.y*7.0)*(1.0-smoothstep(.80,1.0,abs(p.x)));
 finalColor=vec4(fragColor.rgb,fragColor.a*softEdge);
}
)");
        ready=core.id!=rlGetShaderIdDefault()&&halo.id!=rlGetShaderIdDefault();
        if(!ready)throw std::runtime_error("Could not initialize the range accent lighting.");
    }
    void begin(){
        rlDrawRenderBatchActive();BeginShaderMode(halo);BeginBlendMode(BLEND_ADDITIVE);
        rlEnableDepthTest();rlDisableDepthMask();rlDisableBackfaceCulling();
    }
    // Depth-tested surface glow: it cannot shine through a wall or a character.
    // halfLength and halfWidth orient the quad in the scene. No new render target.
    void strip(Vector3 center,Vector3 halfLength,Vector3 halfWidth,Color color,float opacity){
        Vector3 p[]={Vector3Subtract(Vector3Subtract(center,halfLength),halfWidth),Vector3Add(Vector3Subtract(center,halfWidth),halfLength),
            Vector3Add(Vector3Add(center,halfLength),halfWidth),Vector3Add(Vector3Subtract(center,halfLength),halfWidth)};
        rlSetTexture(rlGetTextureIdDefault());rlBegin(RL_QUADS);rlColor4ub(color.r,color.g,color.b,static_cast<unsigned char>(255*opacity));
        for(int i=0;i<4;i++){rlTexCoord2f(i==1||i==2?1.0f:0.0f,i>=2?1.0f:0.0f);rlVertex3f(p[i].x,p[i].y,p[i].z);}rlEnd();
    }
    void end(){rlDrawRenderBatchActive();rlEnableDepthMask();rlEnableBackfaceCulling();EndBlendMode();EndShaderMode();}
    void unload(){if(core.id)UnloadShader(core);if(halo.id)UnloadShader(halo);core=halo={};ready=false;}
};
}
