#pragma once
#include "raylib.h"
#include "graphics_presets.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

namespace suite {
constexpr double TickSeconds = 1.0/120.0;
constexpr const char* MotionNames[] = {"Stationary", "Lateral strafe", "Smooth sweep", "Stop and reverse", "Crossing lanes", "Approach / retreat", "Appear / disappear", "Mixed motion"};
constexpr const char* SceneNames[] = {"Concrete yard", "Indoor range", "Neutral studio"};
constexpr const char* LightNames[] = {"Daylight", "Warm afternoon", "Low light"};
constexpr const char* PresetNames[] = {"Steady tracking", "Fast reversals", "Crossing targets", "Behind cover", "Static lineup", "Mixed motion"};
constexpr int FrameCaps[] = {30, 60, 120, 144, 240, 0};

struct Settings {
    int count=5, motion=1, scene=0, light=0, seed=407, frameCap=2;
    float speed=2.2f, distance=10.0f, spread=5.0f, height=1.8f;
    float fov=90.0f, sensitivity=0.075f, facing=180.0f, poseFrame=0.0f;
    float duration=60.0f, aimHeight=0.76f, appearanceInterval=2.5f;
    bool cover=false, markers=false, hud=true, crosshair=true, mixedStill=true, rawMouse=false, weapon=true;
    std::string model="SWAT.glb";
    int pose=5;
    int mouseDpi=800,hitResponse=0,readySeconds=3;
    float respawnDelay=.45f;
    bool antialias=true;
    int quality=DefaultQuality;
    void sanitize() {
        auto finiteClamp=[](float& v,float a,float b,float fallback) {v=std::isfinite(v)?std::clamp(v,a,b):fallback;};
        count=std::clamp(count,1,32); motion=std::clamp(motion,0,7); scene=std::clamp(scene,0,2);
        light=std::clamp(light,0,2); seed=std::clamp(seed,1,999999); frameCap=std::clamp(frameCap,0,5);
        pose=std::clamp(pose,-1,255);
        if(quality<0||quality>=QualityCount)quality=DefaultQuality;
        mouseDpi=std::clamp(mouseDpi,100,32000);hitResponse=std::clamp(hitResponse,0,1);readySeconds=std::clamp(readySeconds,0,5);
        finiteClamp(respawnDelay,.15f,3.0f,.45f);
        finiteClamp(speed,0,12,2.2f); finiteClamp(distance,4,45,10); finiteClamp(spread,1,12,5);
        finiteClamp(height,1,2.4f,1.8f); finiteClamp(fov,45,120,90);
        finiteClamp(sensitivity,0.005f,0.3f,0.075f); finiteClamp(facing,-180,180,180);
        finiteClamp(poseFrame,0,1,0); finiteClamp(duration,15,600,60); finiteClamp(aimHeight,0.1f,0.98f,0.76f);
        finiteClamp(appearanceInterval,0.5f,10,2.5f);
    }
    void preset(int index) {
        count=5; speed=2.2f; distance=10; spread=5; cover=false; mixedStill=true;
        switch(index) {
        case 0: motion=2; break;
        case 1: motion=3; speed=5; count=3; break;
        case 2: motion=4; count=8; speed=2.8f; break;
        case 3: motion=1; cover=true; count=4; break;
        case 4: motion=0; count=7; distance=12; break;
        default: motion=7; count=8; speed=2.5f; break;
        }
    }
};
inline uint32_t hash(uint32_t x) {x^=x>>16; x*=0x7feb352du; x^=x>>15; x*=0x846ca68bu; return x^(x>>16);}
inline float unitHash(int seed,int id,int salt=0) {return (hash(uint32_t(seed)^uint32_t(id*7919+salt*101)) & 0xffffffu)/float(0x1000000u);}
inline float triangle(double x) {double t=x-std::floor(x);return float(1.0-4.0*std::abs(t-0.5));}
struct Entity {int id; Vector3 position; bool active;};
inline Entity entityAt(const Settings& s,int id,double time) {
    const float phase=unitHash(s.seed,id);
    const float lane=s.count==1?0.0f:((float(id)/float(s.count-1))*2.0f-1.0f)*s.spread;
    const float depth=float(id%3)*1.8f;
    const double cycle=time*double(s.speed)/std::max(4.0,4.0*double(s.spread));
    Entity e{id,{lane,0,s.distance+depth},true};
    int mode=s.motion==7?(id%6+1):s.motion;
    if(s.motion==7 && s.mixedStill && id%3==0) mode=0;
    switch(mode) {
    case 1: e.position.x=s.spread*triangle(cycle+phase); break;
    case 2: e.position.x=s.spread*float(std::sin(time*s.speed/s.spread+phase*6.283185307)); break;
    case 3: {
        double t=std::fmod(cycle*0.7+phase,1.0);
        float p=t<0.35?float(t/0.35):t<0.5?1.0f:t<0.85?float(1-(t-0.5)/0.35):0.0f;
        e.position.x=(2*p-1)*s.spread; break;
    }
    case 4: e.position.x=s.spread*triangle(cycle*(id%2?-1:1)+phase); e.position.z=s.distance+float(id%4)*1.3f;break;
    case 5: e.position.z=s.distance+depth+3.0f*triangle(time*s.speed/12.0+phase); break;
    case 6: { double phaseTime=time/double(s.appearanceInterval)+phase;
              const int epoch=int(std::floor(phaseTime));e.active=(phaseTime-std::floor(phaseTime))<0.72;
              e.position.x=(unitHash(s.seed,id,epoch+3)*2-1)*s.spread;break;}
    default: break;
    }
    e.position.z=std::max(3.0f,e.position.z);
    return e;
}
struct Clock {
    uint64_t ticks=0; double remainder=0, discarded=0;
    void advance(double dt,bool running) {
        if(!running || !std::isfinite(dt) || dt<0) return;
        if(dt>0.25) {discarded+=dt-0.25;dt=0.25;}
        remainder+=dt;
        auto steps=uint64_t(std::floor((remainder+1e-10)/TickSeconds));
        ticks+=steps; remainder-=steps*TickSeconds;
        if(remainder<0) remainder=0;
    }
    double time()const {return ticks*TickSeconds;}
};
inline Vector3 forward(float yaw,float pitch) {
    const float y=yaw*DEG2RAD,p=pitch*DEG2RAD;
    return {std::sin(y)*std::cos(p),std::sin(p),std::cos(y)*std::cos(p)};
}
inline float verticalFov(float horizontal,float aspect) {return 2.0f*std::atan(std::tan(horizontal*DEG2RAD/2)/aspect)*RAD2DEG;}
inline Vector3 aimPoint(const Entity& e,const Settings& s) {return {e.position.x,e.position.y+s.height*s.aimHeight,e.position.z};}
inline float angularError(Vector3 camera,Vector3 dir,Vector3 point) {
    float x=point.x-camera.x,y=point.y-camera.y,z=point.z-camera.z;
    float d=std::sqrt(x*x+y*y+z*z);
    if(d<1e-6f) return 0;
    return std::acos(std::clamp((x*dir.x+y*dir.y+z*dir.z)/d,-1.0f,1.0f))*RAD2DEG;
}
inline bool rayBox(Vector3 origin,Vector3 direction,BoundingBox box,float& distance) {
    float lo=0,hi=1e8f;
    const float o[]={origin.x,origin.y,origin.z},d[]={direction.x,direction.y,direction.z};
    const float mn[]={box.min.x,box.min.y,box.min.z},mx[]={box.max.x,box.max.y,box.max.z};
    for(int a=0;a<3;++a) {
        if(std::abs(d[a])<1e-8f) {if(o[a]<mn[a]||o[a]>mx[a]) return false;continue;}
        float near=(mn[a]-o[a])/d[a],far=(mx[a]-o[a])/d[a];
        if(near>far) std::swap(near,far);
        lo=std::max(lo,near);hi=std::min(hi,far);if(lo>hi)return false;
    }
    distance=lo;return true;
}
inline std::vector<BoundingBox> obstacles(const Settings& s) {
    if(!s.cover)return {};
    // Cover sits before the motion lanes, not through the target models.
    const float z=std::max(2.5f,s.distance-2.7f);
    return {{{-2.1f,0,z},{-0.9f,2.4f,z+0.7f}},{{2.0f,0,z+0.3f},{3.6f,1.15f,z+1.0f}}};
}
inline bool covered(Vector3 camera,Vector3 point,const std::vector<BoundingBox>& boxes) {
    Vector3 delta={point.x-camera.x,point.y-camera.y,point.z-camera.z};
    float distance=std::sqrt(delta.x*delta.x+delta.y*delta.y+delta.z*delta.z);
    if(distance<1e-6f)return false;
    Vector3 d={delta.x/distance,delta.y/distance,delta.z/distance};
    for(auto& b:boxes){float hit=0;if(rayBox(camera,d,b,hit)&&hit<distance)return true;}
    return false;
}
}
