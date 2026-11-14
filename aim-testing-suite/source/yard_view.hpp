#pragma once

#include "yard.hpp"
#include "world_batch.hpp"
#include "rlgl.h"

namespace suite {

inline constexpr Color YardAmber{255,179,88,255}, YardTeal{110,204,191,255};

inline void drawRain(Vector3 position, double elapsed) {
    const float t=static_cast<float>(elapsed);
    rlBegin(RL_LINES);
    for(int i=0;i<340;++i){
        const float x=position.x+(unitHash(827,i,1)-.5f)*44;
        const float z=position.z+(unitHash(827,i,2)-.5f)*44;
        const float phase=std::fmod(unitHash(827,i,3)*8+t*(8+unitHash(827,i,4)*3),8.f);
        const float y=8-phase;
        rlColor4ub(154,184,205,59);
        rlVertex3f(x+y*.16f,y,z);
        rlColor4ub(168,202,222,8);
        rlVertex3f(x+(y+.35f)*.16f,y+.35f,z-.05f);
    }
    rlEnd();
}

inline void buildYard(WorldBatch& world, WorldBatch& lights, const Mesh& cube, Shader solid, Shader emissive) {
    auto box = [&](Vector3 center, Vector3 size, Color color) { world.box(cube,center,size,color); };
    auto lit = [&](Vector3 center, Vector3 size, Color color) { lights.box(cube,center,size,color); };
    box({0,-.15f,15},{48,.30f,60},{57,68,73,255});
    for (const auto& part : YardStructures) {
        auto center = Vector3Scale(Vector3Add(part.bounds.min,part.bounds.max),.5f);
        auto size = Vector3Subtract(part.bounds.max,part.bounds.min);
        box(center,size,part.color);
    }
    for (float z = -12; z < 45; z += 2.3f) {
        box({11.9f,.009f,z},{.11f,.012f,1.06f},{207,159,88,255});
        box({-9.8f,.009f,z},{.085f,.012f,1.4f},{163,177,173,255});
    }
    for (float x = -21; x < 22; x += 1.05f)
        box({x,.009f,4.45f},{.55f,.013f,.16f},{208,167,91,255});
    for (float z = -13; z <= 44; z += 4)
        box({0,.004f,z},{46,.007f,.022f},{43,55,60,255});
    for (float x = -22; x < 23; x += 6)
        box({x,.005f,15},{.025f,.009f,58},{44,56,61,255});
    for (int i : {4,5,8,9}) {
        const auto b = YardStructures[i].bounds;
        auto metal = YardStructures[i].color;
        for (float x = b.min.x+.22f; x < b.max.x-.1f; x += .32f) {
            box({x,b.max.y*.53f,b.min.z-.036f},{.11f,b.max.y*.85f,.072f},
                ColorBrightness(metal,.16f));
            box({x,b.max.y*.53f,b.max.z+.036f},{.11f,b.max.y*.85f,.072f},
                ColorBrightness(metal,-.16f));
        }
        for (float z = b.min.z+.28f; z < b.max.z-.1f; z += .35f) {
            box({b.min.x-.036f,b.max.y*.55f,z},{.072f,b.max.y*.83f,.095f},ColorBrightness(metal,.10f));
            box({b.max.x+.036f,b.max.y*.55f,z},{.072f,b.max.y*.83f,.095f},ColorBrightness(metal,-.10f));
        }
        for (float x : {b.min.x,b.max.x}) for (float z : {b.min.z,b.max.z})
            box({x,b.max.y*.51f,z},{.18f,b.max.y,.18f},{48,56,61,255});
        box({(b.min.x+b.max.x)*.5f,b.max.y-.07f,b.min.z-.045f},{b.max.x-b.min.x,.13f,.13f},{43,48,51,255});
    }
    for (float z : {-8.f, 6.f, 20.f, 38.f}) for (float side : {-21.8f,21.8f}) {
        const float inward = side < 0 ? 1.f : -1.f;
        box({side,3.8f,z},{.20f,7.6f,.20f},{61,72,78,255});
        box({side+inward*.62f,7.42f,z},{1.4f,.18f,.48f},{88,94,91,255});
        lit({side+inward*.69f,7.30f,z},{1.19f,.08f,.37f},side<0?YardTeal:YardAmber);
        for (int n=0;n<3;++n)
            box({side+inward*(.7f+n*.3f),.010f,z+.22f*n},{.25f,.013f,2.9f-.4f*n},
                side<0?Color{61,92,96,255}:Color{110,83,61,255});
    }
    for (float x : {-20.1f,-10.4f}) for (float z : {29.2f,34.5f,40.5f})
        box({x,2.52f,z},{.35f,5.05f,.33f},{44,53,63,255});
    for (float z : {30.2f,35.2f,39.5f}) {
        box({-15.2f,4.37f,z},{9.4f,.20f,.24f},{43,52,60,255});
        lit({-15.2f,4.24f,z},{5.1f,.048f,.12f},YardTeal);
    }
    for (float z=26; z<41; z+=2.3f) {
        for (float x : {14.2f,21.8f}) box({x,3.1f,z},{.085f,1.03f,.085f},{95,104,106,255});
        box({18,.94f,z},{.15f,1.88f,.22f},{56,68,74,255});
    }
    for (float x : {14.2f,21.8f})
        box({x,3.60f,33.5f},{.075f,.085f,15},{105,115,118,255});
    for (int i=0;i<12;++i) {
        const float z = 20.8f + i * .45f;
        const float height = (i + 1) * (2.6f/12);
        box({18,height*.5f,z},{2.1f,height,.46f},{71,82,88,255});
        box({18,height+.005f,z-.21f},{2.09f,.014f,.055f},{209,170,92,255});
    }
    for (float z : {10.f,32.f}) {
        box({0,7.25f,z},{43,.38f,.32f},{67,79,86,255});
        for (float x : {-14.f,0.f,14.f}) lit({x,7.01f,z},{2.1f,.08f,.18f},YardAmber);
    }
    for (int i=0;i<20;++i) {
        const float x = -22.f + i*2.3f;
        const float h = 7.f + unitHash(193,i)*8.f;
        box({x,h*.5f,49.f+float(i%3)},{2.25f,h,4},{39,52,64,255});
        if (i%4==0) lit({x,h-.8f,46.9f+float(i%3)},{.25f,.18f,.02f},YardTeal);
    }
    for(int i=0;i<58;++i){
        const float x=-22+unitHash(713,i,1)*44,z=-13+unitHash(713,i,2)*56;
        const float length=.34f+unitHash(713,i,3)*1.55f;
        const auto color=i%3==0?Color{64,82,92,255}:Color{47,60,67,255};
        box({x,.006f,z},{length,.009f,.38f+unitHash(713,i,4)*1.2f},color);
    }
    world.upload(solid);
    lights.upload(emissive);
}

inline float yardFloor(Vector3 position) {
    if (position.x >= 16.90f && position.x <= 19.10f && position.z >= 20.575f && position.z < 26)
        return std::clamp(std::floor((position.z - 20.575f)/.45f)+1.f,1.f,12.f)*(2.6f/12);
    if (position.x >= 14 && position.x <= 22 && position.z >= 26 && position.z <= 41)
        return 2.6f;
    return 0;
}

inline bool yardBlocked(Vector3 feet) {
    constexpr float radius = .28f;
    for (const auto& bounds : yardSolids()) {
        if (bounds.max.y <= feet.y+.06f || bounds.min.y >= feet.y+1.65f) continue;
        const float dx = feet.x - std::clamp(feet.x,bounds.min.x,bounds.max.x);
        const float dz = feet.z - std::clamp(feet.z,bounds.min.z,bounds.max.z);
        if (dx*dx+dz*dz < radius*radius) return true;
    }
    return false;
}

inline Vector3 moveYardPlayer(Vector3 feet, Vector3 delta) {
    const int steps = std::max(1,int(std::ceil(Vector3Length(delta)/.09f)));
    delta = Vector3Scale(delta,1.f/steps);
    for (int i=0;i<steps;++i) for (int axis : {0,1}) {
        Vector3 candidate{feet.x+(axis==0?delta.x:0),0,feet.z+(axis==1?delta.z:0)};
        candidate.y = yardFloor(candidate);
        if (candidate.y <= feet.y+.28f && !yardBlocked(candidate)) feet = candidate;
    }
    return feet;
}

inline Sound yardShotSound(const YardWeapon& weapon) {
    constexpr unsigned int rate = 24000, length = 6400;
    std::vector<short> samples(length);
    unsigned int random = 293721;
    float low = 0;
    for (unsigned int i=0;i<length;++i) {
        const float t = float(i)/rate;
        random = random*1664525U+1013904223U;
        const float noise = float((random >> 16)&65535)/32767.5f-1;
        low += (noise-low)*.18f;
        const float body = std::sin(t*6.2831853f*97.f*weapon.soundPitch) * std::exp(-t*45.f);
        const float snap = noise*std::exp(-t*112.f)*.58f + low*std::exp(-t*25.f)*.35f;
        const float action = t>.105f ? noise*std::exp(-(t-.105f)*135.f)*.10f : 0.f;
        const float value = (body*.42f + snap + action) * std::min(1.f,t*5500);
        samples[i]=static_cast<short>(std::clamp(value,-1.f,1.f)*27000.f);
    }
    return LoadSoundFromWave({length,rate,16,1,samples.data()});
}

} // namespace suite
