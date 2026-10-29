#pragma once
#include "simulation.hpp"
#include <array>
#include <sstream>
#include <iomanip>
#include <string_view>
namespace suite {
inline double cmPerTurn(float sensitivity,int dpi){return 360.0*2.54/(std::max(1,dpi)*std::max(.0001f,sensitivity));}
inline float sensitivityForCm(double cm,int dpi){return float(360.0*2.54/(std::max(1,dpi)*std::max(.01,cm)));}
// Return only the part of a live frame left after the preparation countdown.
inline double consumeLeadIn(double& remaining,double dt){
    if(!std::isfinite(dt)||dt<=0)return 0;double used=std::min(remaining,dt);remaining=std::max(0.0,remaining-used);return dt-used;
}
inline void advancePractice(Clock& clock,double dt,bool timed,double duration){
    clock.advance(dt,true);
    if(timed){
        const auto end=uint64_t(std::ceil(duration/TickSeconds-1e-8));
        if(clock.ticks>=end){clock.ticks=end;clock.remainder=0;}
    }
}
struct Acquisition {
    std::array<double,12> appeared{};
    std::array<bool,12> visible{},claimed{};
    std::vector<float> delays;int streak=0,bestStreak=0,switches=0,lastTarget=-1;
    Acquisition(){appeared.fill(-1);}
    void observe(int id,bool now,double time){
        if(now&&!visible[id]){appeared[id]=time;claimed[id]=false;}
        if(!now)appeared[id]=-1;
        visible[id]=now;
    }
    void hit(int id,double time){
        ++streak;bestStreak=std::max(bestStreak,streak);
        if(lastTarget>=0&&lastTarget!=id)++switches;lastTarget=id;
        if(visible[id]&&!claimed[id]&&appeared[id]>=0&&time>=appeared[id]){
            delays.push_back(float((time-appeared[id])*1000));claimed[id]=true;
        }
    }
    void miss(){streak=0;}
    float median()const{if(delays.empty())return -1;auto sorted=delays;std::sort(sorted.begin(),sorted.end());size_t n=sorted.size();return n%2?sorted[n/2]:(sorted[n/2-1]+sorted[n/2])*.5f;}
};
// Stable local comparison ID. This is a grouping key, not a security checksum.
inline std::string comparisonId(std::string_view context){
    uint64_t value=14695981039346656037ull;for(unsigned char c:context){value^=c;value*=1099511628211ull;}
    std::ostringstream out;out<<std::hex<<std::setw(16)<<std::setfill('0')<<value;return out.str();
}
}
