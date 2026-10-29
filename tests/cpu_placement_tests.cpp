#include "app/CpuPlacement.h"
#include <iostream>
#include <chrono>
#include <cmath>
#include <array>

using vaporlock::inference::CpuPlacement;
void require(bool value,const char* text){if(!value)throw std::runtime_error(text);}
struct Probe {DWORD_PTR mask;bool correct=false;};
void probe(void* raw){auto& p=*static_cast<Probe*>(raw);p.correct=(p.mask&(DWORD_PTR(1)<<GetCurrentProcessorNumber()))!=0;}
int wmain(int argc,wchar_t** argv){
    try{
        require(argc==3,"Expected CPU-set IDs and owned ONNX fixture");
        CpuPlacement placement;placement.configure(argv[1]);require(!placement.ids.empty(),"No CPUs selected");
        GROUP_AFFINITY before{},after{};require(GetThreadGroupAffinity(GetCurrentThread(),&before),"Missing initial affinity");
        {CpuPlacement::Scope scope(&placement);require((placement.mask&(DWORD_PTR(1)<<GetCurrentProcessorNumber()))!=0,"Caller placement");}
        require(GetThreadGroupAffinity(GetCurrentThread(),&after) && before.Mask==after.Mask && before.Group==after.Group,"Caller not restored");
        try{CpuPlacement::Scope scope(&placement);throw std::runtime_error("fixture");}catch(...){}
        require(GetThreadGroupAffinity(GetCurrentThread(),&after) && before.Mask==after.Mask,"Exception did not restore affinity");
        Probe observed{placement.mask};auto thread=CpuPlacement::create(&placement,probe,&observed);require(thread!=nullptr,"Worker creation failed");CpuPlacement::join(thread);require(observed.correct,"Worker ran outside selected CPUs");
        for(const auto* invalid:{L"4294967296",L"1,1",L"1,",L"-1",L"bogus"}){
            bool rejected=false;try{CpuPlacement bad;bad.configure(invalid);}catch(...){rejected=true;}require(rejected,"Malformed placement accepted");
        }
        Ort::Env env(ORT_LOGGING_LEVEL_WARNING,"owned-cpu-placement-test");
        auto measure=[&](bool explicitPlacement){
            Ort::SessionOptions options;options.SetIntraOpNumThreads(2);options.SetInterOpNumThreads(1);
            if(explicitPlacement)placement.options(options,2);
            Ort::Session session(env,argv[2],options);
            std::vector<float> data(256*256,1.f);std::array<int64_t,2> shape{256,256};
            auto memory=Ort::MemoryInfo::CreateCpu(OrtArenaAllocator,OrtMemTypeDefault);
            auto tensor=Ort::Value::CreateTensor<float>(memory,data.data(),data.size(),shape.data(),shape.size());
            const char* input="x";const char* output="y";auto started=std::chrono::steady_clock::now();
            for(int repeat=0;repeat<12;++repeat){
                CpuPlacement::Scope scope(explicitPlacement?&placement:nullptr);
                auto values=session.Run(Ort::RunOptions{nullptr},&input,&tensor,1,&output,1);
                auto* result=values[0].GetTensorData<float>();require(std::abs(result[0]-256.f)<.001f && std::abs(result[65535]-256.f)<.001f,"CPU output mismatch");
            }
            return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count()/12.;
        };
        auto baseline=measure(false),selected=measure(true);
        require(GetThreadGroupAffinity(GetCurrentThread(),&after) && before.Mask==after.Mask,"ORT caller not restored");
        std::cout<<"{\"passed\":true,\"default_ms\":"<<baseline<<",\"selected_ms\":"<<selected<<",\"selected_cpus\":"<<placement.ids.size()<<"}\n";
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
