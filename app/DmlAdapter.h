#pragma once
#include <d3d12.h>
#include <dxgi1_6.h>
#include <wrl/client.h>
#include <stdexcept>
#include <string>
#include <vector>

namespace vaporlock::inference {

struct DmlAdapter {
    unsigned legacyIndex;
    LUID luid;
    std::wstring name;
};

inline bool sameAdapter(LUID a,LUID b) noexcept {
    return a.LowPart==b.LowPart && a.HighPart==b.HighPart;
}

// ORT accepts the original EnumAdapters ordinal, never the ordinal in the
// high-performance list. Keep that index attached to the validated adapter.
inline DmlAdapter chooseDmlAdapter(int requested = -1) {
    if(requested < -1 || requested >= 64) throw std::runtime_error("Invalid DirectML adapter index.");
    using Microsoft::WRL::ComPtr;
    ComPtr<IDXGIFactory1> factory;
    if(FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))))
        throw std::runtime_error("DirectML could not enumerate graphics adapters.");
    std::vector<DmlAdapter> capable;
    for(unsigned index=0;index<64;++index) {
        ComPtr<IDXGIAdapter1> adapter;
        const HRESULT found=factory->EnumAdapters1(index,&adapter);
        if(found==DXGI_ERROR_NOT_FOUND) break;
        if(FAILED(found)) throw std::runtime_error("Graphics adapter enumeration changed or failed.");
        DXGI_ADAPTER_DESC1 desc{};
        if(FAILED(adapter->GetDesc1(&desc)))
            throw std::runtime_error("Graphics adapter identity could not be verified.");
        if(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) continue;
        if(FAILED(D3D12CreateDevice(adapter.Get(),D3D_FEATURE_LEVEL_11_0,__uuidof(ID3D12Device),nullptr))) continue;
        capable.push_back({index,desc.AdapterLuid,desc.Description});
    }
    if(capable.empty()) throw std::runtime_error("No hardware adapter supports DirectML's DirectX 12 requirement.");
    if(requested >= 0) {
        for(const auto& candidate:capable) if(candidate.legacyIndex==static_cast<unsigned>(requested)) {
            ComPtr<IDXGIAdapter1> verify; DXGI_ADAPTER_DESC1 current{};
            if(!factory->IsCurrent() || FAILED(factory->EnumAdapters1(candidate.legacyIndex,&verify))
                || FAILED(verify->GetDesc1(&current)) || !sameAdapter(current.AdapterLuid,candidate.luid))
                throw std::runtime_error("Graphics adapters changed; refresh the inference GPU selection.");
            return candidate;
        }
        throw std::runtime_error("The selected DirectML adapter is unavailable or incompatible. Choose another inference GPU.");
    }
    ComPtr<IDXGIFactory6> preferred;
    if(SUCCEEDED(factory.As(&preferred))) {
        for(unsigned rank=0;rank<64;++rank) {
            ComPtr<IDXGIAdapter1> adapter;
            const HRESULT found=preferred->EnumAdapterByGpuPreference(rank,DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                                                                    IID_PPV_ARGS(&adapter));
            if(found==DXGI_ERROR_NOT_FOUND) break;
            if(FAILED(found)) throw std::runtime_error("Preferred graphics adapter enumeration failed.");
            DXGI_ADAPTER_DESC1 desc{};
            if(FAILED(adapter->GetDesc1(&desc))) throw std::runtime_error("Preferred graphics adapter identity is unavailable.");
            for(const auto& candidate:capable) {
                if(!sameAdapter(candidate.luid,desc.AdapterLuid)) continue;
                // Verify the ordinal again rather than silently substituting 0
                // if a hot-plug changed the factory topology.
                ComPtr<IDXGIAdapter1> verify;
                DXGI_ADAPTER_DESC1 current{};
                if(!factory->IsCurrent() || FAILED(factory->EnumAdapters1(candidate.legacyIndex,&verify))
                    || FAILED(verify->GetDesc1(&current)) || !sameAdapter(current.AdapterLuid,candidate.luid))
                    throw std::runtime_error("Graphics adapters changed; restart the inference session.");
                return candidate;
            }
        }
        throw std::runtime_error("No preferred graphics adapter matched the validated DirectML adapters.");
    }
    if(!factory->IsCurrent()) throw std::runtime_error("Graphics adapters changed; restart the inference session.");
    return capable.front(); // Older Windows: first capability-checked hardware adapter.
}
} // namespace vaporlock::inference
