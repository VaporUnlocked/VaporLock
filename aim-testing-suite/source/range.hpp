#pragma once
#include "simulation.hpp"
#include "raymath.h"
#include <array>
namespace suite {
constexpr int BayCount=4;
constexpr float BayCenters[]={24,8,-8,-24};
constexpr const char* BayNames[]={"PRECISION","TRACKING","REFLEX","COVER"};
constexpr const char* BayDescriptions[]={"PLACE EVERY SHOT","STAY ON THE TARGET","FIND. ACQUIRE. REPEAT.","WORK THE SIGHTLINES"};
constexpr float PanelWidth=3.8f,PanelHeight=3.35f;
constexpr float FiringLine=1.35f;
constexpr int PanelPixels=1024;
inline Vector3 boardCenter(int bay){return {BayCenters[bay]+4.85f,2.03f,.7f};}
inline BoundingBox boardBox(int bay){auto c=boardCenter(bay);return {{c.x-PanelWidth/2,c.y-PanelHeight/2,c.z},{c.x+PanelWidth/2,c.y+PanelHeight/2,c.z+.16f}};}
inline Settings bayDefaults(int bay) {
    Settings s;s.count=bay==2?3:bay==1?4:5;s.motion=bay==0?0:bay==1?1:bay==2?6:3;
    s.distance=bay==0?12.0f:bay==1?14.0f:bay==2?10.0f:15.0f;s.spread=4;s.speed=bay==1?3:2.5f;
    s.cover=bay==3;s.height=1.85f;s.facing=180;s.duration=60;s.appearanceInterval=1.8f;
    s.seed=407+bay*137;s.markers=false;s.hitResponse=(bay==0||bay==2)?1:0;return s;
}
inline void sanitizeBay(Settings& s) {
    s.sanitize();s.count=std::min(s.count,12);s.distance=std::clamp(s.distance,6.0f,30.0f);
    s.spread=std::clamp(s.spread,1.0f,5.5f);
}
inline int closestBay(Vector3 p){int index=0;for(int i=1;i<BayCount;i++)if(std::abs(p.x-BayCenters[i])<std::abs(p.x-BayCenters[index]))index=i;return index;}
// Each bay includes its marked approach in the shared corridor, where users aim and use panels.
// Half-open X boundaries select exactly one bay; no targets exist outside these zones.
inline int occupiedBay(Vector3 p){
    if(!std::isfinite(p.x)||!std::isfinite(p.y)||!std::isfinite(p.z)||p.z< -9||p.z>42||p.y<0||p.y>6)return -1;
    for(int i=0;i<BayCount;i++)if(p.x>=BayCenters[i]-8&&(p.x<BayCenters[i]+8||(i==0&&p.x==32)))return i;
    return -1;
}
inline Entity rangeEntity(const Settings& s,int bay,int id,double time) {
    auto e=entityAt(s,id,time);e.position.x+=BayCenters[bay];e.position.z=FiringLine+std::clamp(e.position.z,5.0f,37.0f);return e;
}
inline std::vector<BoundingBox> architecture() {
    std::vector<BoundingBox> result{
        {{-32.3f,0,-9.3f},{32.3f,6,-9}},
        {{-32.3f,0,-9},{-32,6,43}},{{32,0,-9},{32.3f,6,43}},
        {{-32,0,42},{32,6,43}}
    };
    for(float x:{-16.0f,0.0f,16.0f})result.push_back({{x-.16f,0,1.7f},{x+.16f,5,42}});
    for(int i=0;i<BayCount;i++)result.push_back(boardBox(i));
    return result;
}
inline std::vector<BoundingBox> bayCover(const Settings& s,int bay) {
    auto boxes=obstacles(s);for(auto& b:boxes){b.min.x+=BayCenters[bay];b.max.x+=BayCenters[bay];b.min.z+=FiringLine;b.max.z+=FiringLine;}
    return boxes;
}
inline bool blocksPlayer(Vector3 p,const std::vector<BoundingBox>& boxes) {
    constexpr float radius=.26f;
    for(auto& b:boxes) {
        if(b.max.y<=.08f||b.min.y>1.75f)continue;
        float x=std::clamp(p.x,b.min.x,b.max.x),z=std::clamp(p.z,b.min.z,b.max.z);
        float dx=p.x-x,dz=p.z-z;if(dx*dx+dz*dz<radius*radius)return true;
    }
    return false;
}
inline Vector3 movePlayer(Vector3 start,Vector3 delta,const std::vector<BoundingBox>& boxes) {
    float distance=std::sqrt(delta.x*delta.x+delta.z*delta.z);
    if(!std::isfinite(distance))return start;
    // Bound even a stalled-frame displacement, then substep to prevent tunnelling.
    if(distance>3){delta=Vector3Scale(delta,3/distance);distance=3;}
    int steps=std::max(1,int(std::ceil(distance/.10f)));delta=Vector3Scale(delta,1.0f/steps);
    for(int i=0;i<steps;i++){
        auto next=start;next.x+=delta.x;if(!blocksPlayer(next,boxes))start.x=next.x;
        next=start;next.z+=delta.z;if(!blocksPlayer(next,boxes))start.z=next.z;
    }
    return start;
}
struct PanelHit {bool hit=false;float distance=1e30f;Vector2 pixel{};};
inline bool visibleBox(BoundingBox box,Vector3 camera,Vector3 direction,float horizontalFov,float aspect) {
    auto right=Vector3Normalize(Vector3CrossProduct(direction,{0,1,0})),up=Vector3CrossProduct(right,direction);
    float horizontal=std::tan(horizontalFov*DEG2RAD/2),vertical=horizontal/aspect;
    bool outside[6]={true,true,true,true,true,true};
    for(int i=0;i<8;i++){
        Vector3 v{(i&1)?box.max.x:box.min.x,(i&2)?box.max.y:box.min.y,(i&4)?box.max.z:box.min.z};v=Vector3Subtract(v,camera);
        float z=Vector3DotProduct(v,direction),x=Vector3DotProduct(v,right),y=Vector3DotProduct(v,up);
        outside[0]&=x < -z*horizontal;outside[1]&=x > z*horizontal;outside[2]&=y < -z*vertical;outside[3]&=y > z*vertical;
        outside[4]&=z < .01f;outside[5]&=z > 150;
    }
    for(bool out:outside)if(out)return false;return true;
}
inline PanelHit intersectPanel(Ray ray,int bay,float reach=12) {
    auto c=boardCenter(bay);
    if(ray.direction.z<=1e-6f||ray.position.z>=c.z)return {};
    float distance=(c.z-ray.position.z)/ray.direction.z;if(distance<0||distance>reach)return {};
    auto point=Vector3Add(ray.position,Vector3Scale(ray.direction,distance));
    float u=(c.x+PanelWidth/2-point.x)/PanelWidth,v=(c.y+PanelHeight/2-point.y)/PanelHeight;
    if(u<0||u>1||v<0||v>1)return {};
    return {true,distance,{u*PanelPixels,v*PanelPixels}};
}
struct ProjectedBounds {bool visible=false;Rectangle rectangle{};};
inline ProjectedBounds projectBounds(BoundingBox box,Camera3D camera,int width,int height){
    auto dir=Vector3Normalize(Vector3Subtract(camera.target,camera.position));
    std::array<Vector3,8> corners{};std::array<float,8> depths{};
    Vector2 lo{1e20f,1e20f},hi{-1e20f,-1e20f};bool any=false;
    auto include=[&](Vector3 point){auto p=GetWorldToScreenEx(point,camera,width,height);if(!std::isfinite(p.x)||!std::isfinite(p.y))return;lo=Vector2Min(lo,p);hi=Vector2Max(hi,p);any=true;};
    constexpr float near=.01f;
    for(int i=0;i<8;i++){
        corners[i]={(i&1)?box.max.x:box.min.x,(i&2)?box.max.y:box.min.y,(i&4)?box.max.z:box.min.z};
        depths[i]=Vector3DotProduct(Vector3Subtract(corners[i],camera.position),dir);
        if(depths[i]>=near)include(corners[i]);
    }
    for(int i=0;i<8;i++)for(int bit:{1,2,4})if(!(i&bit)){
        int j=i|bit;if((depths[i]>=near)==(depths[j]>=near))continue;
        float t=(near-depths[i])/(depths[j]-depths[i]);include(Vector3Lerp(corners[i],corners[j],t));
    }
    if(!any||hi.x<0||hi.y<0||lo.x>width||lo.y>height)return {};
    lo.x=std::clamp(lo.x,0.0f,float(width));lo.y=std::clamp(lo.y,0.0f,float(height));
    hi.x=std::clamp(hi.x,0.0f,float(width));hi.y=std::clamp(hi.y,0.0f,float(height));
    return {hi.x>lo.x&&hi.y>lo.y,{lo.x,lo.y,hi.x-lo.x,hi.y-lo.y}};
}
enum class ControlKind {None,Page,Decrease,Increase,Start,Reset,Defaults};
struct Control {ControlKind kind=ControlKind::None;int index=0;};
inline Control panelControl(Vector2 p) {
    if(p.y>=132&&p.y<184&&p.x>=32&&p.x<992){int tab=std::min(3,int((p.x-32)/240));if(p.x<32+tab*240+224)return {ControlKind::Page,tab};}
    if(p.y>=208&&p.y<800) {
        int row=int((p.y-208)/74);float rowY=208.0f+74*row;
        if(p.y<rowY+62) {
            if(p.x>=770&&p.x<862)return {ControlKind::Decrease,row};
            if(p.x>=886&&p.x<978)return {ControlKind::Increase,row};
        }
    }
    if(p.y>=840&&p.y<916) {
        if(p.x>=32&&p.x<408)return {ControlKind::Start,0};
        if(p.x>=424&&p.x<682)return {ControlKind::Reset,0};
        if(p.x>=698&&p.x<992)return {ControlKind::Defaults,0};
    }
    return {};
}
inline Vector3 panelPoint(int bay,Vector2 pixel) {
    auto c=boardCenter(bay);return {c.x+PanelWidth/2-PanelWidth*pixel.x/PanelPixels,c.y+PanelHeight/2-PanelHeight*pixel.y/PanelPixels,c.z};
}
}
