#pragma once
#include <Windows.h>
#include <onnxruntime_cxx_api.h>
#include <vector>
#include <string>
#include <algorithm>
#include <memory>
#include <stdexcept>

namespace vaporlock::inference {
// Supported scope is intentionally one processor group. No process-wide
// scheduling policy changes; caller placement is restored after each Run.
class CpuPlacement {
public:
    std::vector<ULONG> ids;
    DWORD_PTR mask=0;
    void configure(const std::wstring& wire) {
        ids.clear();mask=0;if(wire.empty())return;
        if(wire.size()>768 || GetActiveProcessorGroupCount()!=1)throw std::runtime_error("CPU placement is unsupported on this system.");
        std::size_t offset=0;
        while(offset<wire.size()) {
            auto end=wire.find(L',',offset);if(end==std::wstring::npos)end=wire.size();
            auto token=wire.substr(offset,end-offset);
            if(token.empty() || token.size()>10 || token.find_first_not_of(L"0123456789")!=std::wstring::npos)throw std::runtime_error("Invalid CPU set.");
            const auto value=std::stoull(token);if(value>0xffffffffULL || ids.size()>=64 || std::find(ids.begin(),ids.end(),value)!=ids.end())throw std::runtime_error("Invalid CPU sets.");
            ids.push_back(static_cast<ULONG>(value));offset=end+1;
        }
        if(wire.back()==L',')throw std::runtime_error("Invalid CPU sets.");
        DWORD_PTR processMask=0,systemMask=0;
        if(!GetProcessAffinityMask(GetCurrentProcess(),&processMask,&systemMask))throw std::runtime_error("CPU affinity is unavailable.");
        ULONG length=0;GetSystemCpuSetInformation(nullptr,0,&length,GetCurrentProcess(),0);
        if(!length || length>1024*1024)throw std::runtime_error("CPU inventory is unavailable.");
        std::vector<unsigned char> data(length);
        if(!GetSystemCpuSetInformation(reinterpret_cast<PSYSTEM_CPU_SET_INFORMATION>(data.data()),length,&length,GetCurrentProcess(),0))throw std::runtime_error("CPU inventory changed.");
        std::size_t matched=0;
        for(std::size_t at=0;at<length;) {
            auto* item=reinterpret_cast<PSYSTEM_CPU_SET_INFORMATION>(data.data()+at);
            if(length-at<8 || item->Size<8 || item->Size>length-at)throw std::runtime_error("Invalid CPU inventory.");
            if(item->Type==CpuSetInformation) {
                if(item->Size<sizeof(SYSTEM_CPU_SET_INFORMATION))throw std::runtime_error("Invalid CPU inventory.");
                const auto& cpu=item->CpuSet;
                if(std::find(ids.begin(),ids.end(),cpu.Id)!=ids.end()) {
                    if(cpu.Group!=0 || cpu.LogicalProcessorIndex>=sizeof(DWORD_PTR)*8 || (cpu.Allocated && !cpu.AllocatedToTargetProcess)
                            || !(processMask&(DWORD_PTR(1)<<cpu.LogicalProcessorIndex)))throw std::runtime_error("Selected CPU set is unavailable.");
                    mask|=DWORD_PTR(1)<<cpu.LogicalProcessorIndex;++matched;
                }
            }
            at+=item->Size;
        }
        if(matched!=ids.size() || !mask)throw std::runtime_error("Selected CPU set is unavailable.");
    }
    void apply(HANDLE thread) const {
        if(ids.empty())return;
        if(!SetThreadAffinityMask(thread,mask) || !SetThreadSelectedCpuSets(thread,ids.data(),static_cast<ULONG>(ids.size())))
            throw std::runtime_error("CPU thread placement failed.");
        GROUP_AFFINITY actual{};
        if(!GetThreadGroupAffinity(thread,&actual) || actual.Group!=0 || actual.Mask!=mask)
            throw std::runtime_error("CPU thread affinity could not be verified.");
        ULONG count=0;GetThreadSelectedCpuSets(thread,nullptr,0,&count);
        std::vector<ULONG> selected(count);
        if(count!=ids.size() || !GetThreadSelectedCpuSets(thread,selected.data(),count,&count))throw std::runtime_error("CPU set request could not be verified.");
        auto expected=ids;std::sort(expected.begin(),expected.end());std::sort(selected.begin(),selected.end());
        if(selected!=expected)throw std::runtime_error("CPU set request differs from selection.");
    }
    struct Scope {
        GROUP_AFFINITY before{};std::vector<ULONG> sets;bool active=false;
        explicit Scope(const CpuPlacement* placement) {
            if(!placement || placement->ids.empty())return;
            ULONG count=0;auto thread=GetCurrentThread();
            if(!GetThreadGroupAffinity(thread,&before))throw std::runtime_error("CPU thread state is unavailable.");
            GetThreadSelectedCpuSets(thread,nullptr,0,&count);if(count>4096)throw std::runtime_error("Invalid CPU thread state.");
            sets.resize(count);if(!GetThreadSelectedCpuSets(thread,sets.data(),count,&count))throw std::runtime_error("CPU thread state is unavailable.");
            active=true;
            try{placement->apply(thread);}catch(...){restore();throw;}
        }
        void restore() noexcept {
            if(!active)return;active=false;auto thread=GetCurrentThread();
            // A failed restore must not let capture/output continue with altered
            // scheduling. Terminate only this owned engine, never the host.
            if(!SetThreadGroupAffinity(thread,&before,nullptr) || !SetThreadSelectedCpuSets(thread,sets.data(),static_cast<ULONG>(sets.size())))std::terminate();
        }
        ~Scope(){restore();}
    };
    struct Work { OrtThreadWorkerFn function;void* parameter; };
    static DWORD WINAPI entry(void* raw) {
        std::unique_ptr<Work> work(static_cast<Work*>(raw));work->function(work->parameter);return 0;
    }
    static OrtCustomThreadHandle create(void* raw,OrtThreadWorkerFn function,void* parameter) {
        auto work=std::make_unique<Work>(Work{function,parameter});
        HANDLE thread=CreateThread(nullptr,0,entry,work.get(),CREATE_SUSPENDED,nullptr);
        if(!thread)return nullptr;
        try{static_cast<CpuPlacement*>(raw)->apply(thread);}catch(...){TerminateThread(thread,1);CloseHandle(thread);return nullptr;}
        if(ResumeThread(thread)==DWORD(-1)){TerminateThread(thread,1);CloseHandle(thread);return nullptr;}
        work.release();return reinterpret_cast<OrtCustomThreadHandle>(thread);
    }
    static void join(OrtCustomThreadHandle handle) {
        HANDLE thread=reinterpret_cast<HANDLE>(const_cast<void*>(reinterpret_cast<const void*>(handle)));
        WaitForSingleObject(thread,INFINITE);CloseHandle(thread);
    }
    void options(Ort::SessionOptions& options,int requestedThreads) {
        if(ids.empty())return;
        options.SetIntraOpNumThreads(requestedThreads>0?requestedThreads:static_cast<int>(ids.size()));
        options.SetCustomCreateThreadFn(create);options.SetCustomThreadCreationOptions(this);options.SetCustomJoinThreadFn(join);
    }
};
}
