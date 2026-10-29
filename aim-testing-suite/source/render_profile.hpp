#pragma once
#include "rlgl.h"
#include "raylib.h"
#include <array>
namespace suite {
// Optional diagnostics only. GPU query reads never block the render thread.
struct RenderProfile {
    using Gen=void(__stdcall*)(int,unsigned*);using Begin=void(__stdcall*)(unsigned,unsigned);using End=void(__stdcall*)(unsigned);
    using Available=void(__stdcall*)(unsigned,unsigned,int*);using Result=void(__stdcall*)(unsigned,unsigned,unsigned long long*);
    Gen gen{},remove{};Begin beginQuery{};End endQuery{};Available available{};Result result{};
    std::array<unsigned,16>queries{};std::array<bool,16>pending{};size_t cursor=0;bool enabled=false,recording=false;
    std::array<double,6>cpu{};double last=0,gpuSum=0;unsigned long long gpuCount=0;int boxes=0;
    void init(){
        gen=reinterpret_cast<Gen>(rlGetProcAddress("glGenQueries"));remove=reinterpret_cast<Gen>(rlGetProcAddress("glDeleteQueries"));
        beginQuery=reinterpret_cast<Begin>(rlGetProcAddress("glBeginQuery"));endQuery=reinterpret_cast<End>(rlGetProcAddress("glEndQuery"));
        available=reinterpret_cast<Available>(rlGetProcAddress("glGetQueryObjectiv"));result=reinterpret_cast<Result>(rlGetProcAddress("glGetQueryObjectui64v"));
        if(gen&&remove&&beginQuery&&endQuery&&available&&result)gen(int(queries.size()),queries.data());
    }
    void begin(){if(!enabled)return;cpu={};boxes=0;last=GetTime();}
    void mark(int stage){if(!enabled)return;double now=GetTime();cpu[stage]+=(now-last)*1000;last=now;}
    void collect(){
        if(!enabled||!queries[0])return;
        for(size_t i=0;i<queries.size();i++)if(pending[i]){int ready=0;available(queries[i],0x8867,&ready);if(ready){unsigned long long ns=0;result(queries[i],0x8866,&ns);gpuSum+=double(ns)/1e6;++gpuCount;pending[i]=false;}}
    }
    void gpuBegin(){
        if(!enabled||!queries[0])return;collect();
        if(!pending[cursor]){beginQuery(0x88BF,queries[cursor]);recording=true;}
    }
    void gpuEnd(){if(recording){endQuery(0x88BF);pending[cursor]=true;cursor=(cursor+1)%queries.size();recording=false;}}
    void unload(){if(queries[0]&&remove)remove(int(queries.size()),queries.data());queries={};}
};
}
