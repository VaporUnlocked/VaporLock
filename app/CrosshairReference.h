#pragma once
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

namespace vaporlock::crosshair {
using Clock = std::chrono::steady_clock;
struct Sample { int x=0,y=0,r=0,g=0,b=0,foreground=1; };
struct Template {
    // Monitor index, physical desktop bounds and requested crop geometry.
    std::array<int,9> geometry{};
    int x=0,y=0; // Aim point relative to physical display origin.
    std::vector<Sample> samples;
};
inline bool parse(const std::wstring& wire, Template& result) {
    if (wire.size()>8192) return false;
    std::wistringstream in(wire); Template t; int version=0,count=0;
    if (!(in>>version) || version!=1) return false;
    for (auto& n:t.geometry) if (!(in>>n) || n < -1000000 || n > 1000000) return false;
    if (t.geometry[0]<1 || t.geometry[0]>32 || t.geometry[3]<1 || t.geometry[4]<1
        || t.geometry[7]<1 || t.geometry[8]<1) return false;
    if (!(in>>t.x>>t.y>>count) || t.x<0 || t.y<0 || t.x>=t.geometry[3] || t.y>=t.geometry[4]
        || count<5 || count>128) return false;
    int positive=0,negative=0;
    for(int i=0;i<count;++i) {
        Sample s;
        if (!(in>>s.x>>s.y>>s.r>>s.g>>s.b>>s.foreground) || s.x < -31 || s.x > 31 || s.y < -31 || s.y > 31
            || std::min({s.r,s.g,s.b})<0 || std::max({s.r,s.g,s.b})>255 || (s.foreground!=0 && s.foreground!=1)) return false;
        if (std::any_of(t.samples.begin(),t.samples.end(),[&](const Sample& a){return a.x==s.x&&a.y==s.y;})) return false;
        positive+=s.foreground;negative+=!s.foreground;t.samples.push_back(s);
    }
    std::wstring extra; if(in>>extra || positive<1 || positive>64 || negative<4 || negative>64)return false;
    result=std::move(t);return true;
}
struct Reference {
    bool resolved=false, inside=true, detected=false;
    double x=0,y=0;
    Clock::time_point matched{};
    const char* status="off";
    bool usable(Clock::time_point now) const {
        return !resolved || (inside && (!detected || (now>=matched && now-matched<std::chrono::milliseconds(100))));
    }
};
inline Reference screenReference(int screenLeft,int screenTop,int screenWidth,int screenHeight,
                                 int cropLeft,int cropTop,int cropWidth,int cropHeight) {
    Reference r; r.resolved=true;
    r.x=screenLeft+screenWidth*.5-cropLeft;r.y=screenTop+screenHeight*.5-cropTop;
    r.inside=screenWidth>0&&screenHeight>0&&r.x>=0&&r.y>=0&&r.x<cropWidth&&r.y<cropHeight;
    if(!r.inside)r.status="outside-capture";return r;
}
class Matcher {
    Clock::time_point scanned_{},matched_{};
    std::uint64_t generation_=0,source_=0,frame_=0;
    int streak_=0,lastX_=0,lastY_=0;
    bool locked_=false;
public:
    void reset() { *this=Matcher{}; }
    Reference update(const Template& t,const std::array<int,9>& geometry,
                     const std::vector<std::uint8_t>& bgra,int width,int height,int left,int top,
                     std::uint64_t generation,std::uint64_t source,std::uint64_t frame,
                     Clock::time_point observed,Clock::time_point now,Reference fallback) {
        fallback.status=fallback.inside?"searching":"outside-capture";
        if(generation!=generation_||source!=source_) { reset();generation_=generation;source_=source; }
        if(geometry!=t.geometry||t.samples.empty()) { reset();fallback.status="recalibrate";return fallback; }
        if(frame<=frame_||observed>now||now-observed>std::chrono::milliseconds(100)) {
            locked_=false;streak_=0;fallback.status="lost";return fallback;
        }
        frame_=frame;
        auto retained=[&]() {
            if(locked_&&now>=matched_&&now-matched_<std::chrono::milliseconds(100)) {
                auto r=fallback;r.x=lastX_;r.y=lastY_;r.detected=true;r.matched=matched_;r.status="tracking";
                r.inside=r.x>=0&&r.y>=0&&r.x<width&&r.y<height;return r;
            }return fallback;
        };
        if(scanned_!=Clock::time_point{} && now-scanned_<std::chrono::milliseconds(33))return retained();
        scanned_=now;
        if(matched_!=Clock::time_point{} && observed-matched_>=std::chrono::milliseconds(100)) {streak_=0;locked_=false;}
        if(width<=0||height<=0||static_cast<std::uint64_t>(width)*height*4!=bgra.size()) {
            locked_=false;streak_=0;fallback.status="lost";return fallback;
        }
        const int baseX=geometry[1]+t.x-left,baseY=geometry[2]+t.y-top;
        for(const auto& s:t.samples)if(baseX+s.x-16<0||baseY+s.y-16<0||baseX+s.x+16>=width||baseY+s.y+16>=height) {
            locked_=false;streak_=0;fallback.status="search-outside-capture";return fallback;
        }
        struct Candidate {float score;int x,y;}; std::vector<Candidate> candidates;candidates.reserve(1089);
        for(int dy=-16;dy<=16;++dy)for(int dx=-16;dx<=16;++dx) {
            const int x=baseX+dx,y=baseY+dy;
            float sum=0;bool valid=x>=0&&y>=0&&x<width&&y<height;
            for(const auto& s:t.samples) {
                const int px=x+s.x,py=y+s.y;
                if(px<0||py<0||px>=width||py>=height){valid=false;break;}
                const auto offset=(static_cast<std::size_t>(py)*width+px)*4;
                const int delta=std::max({std::abs(int(bgra[offset+2])-s.r),std::abs(int(bgra[offset+1])-s.g),std::abs(int(bgra[offset])-s.b)});
                sum+=s.foreground?std::max(0.f,1.f-delta/64.f):std::min(1.f,delta/64.f);
            }
            if(valid)candidates.push_back({sum/static_cast<float>(t.samples.size()),x,y});
        }
        if(candidates.empty()){locked_=false;streak_=0;fallback.status="lost";return fallback;}
        const auto best=*std::max_element(candidates.begin(),candidates.end(),[](auto a,auto b){return a.score<b.score;});
        float second=0;
        for(const auto& c:candidates)if(std::abs(c.x-best.x)>2||std::abs(c.y-best.y)>2)second=std::max(second,c.score);
        if(best.score<.86f||best.score-second<.07f){locked_=false;streak_=0;fallback.status="lost";return fallback;}
        if(streak_&&std::hypot(best.x-lastX_,best.y-lastY_)<=3)streak_=std::min(3,streak_+1);else {streak_=1;locked_=false;}
        lastX_=best.x;lastY_=best.y;matched_=observed;locked_=streak_>=3;
        return retained();
    }
};
}
