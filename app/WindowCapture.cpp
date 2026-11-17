#include "WindowCapture.h"

#include <d3d11.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>

namespace vaporlock::video {
namespace {
using namespace winrt::Windows::Graphics;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;

bool ownsWindow(HWND window, DWORD pid) {
    DWORD owner = 0;
    return IsWindow(window) && GetWindowThreadProcessId(window, &owner) && owner == pid;
}

Clock::time_point observationTime(Direct3D11CaptureFrame const& frame) {
    LARGE_INTEGER frequency{}, count{};
    const auto now = Clock::now();
    if (!QueryPerformanceCounter(&count) || !QueryPerformanceFrequency(&frequency)) return now;
    const auto ticks = frame.SystemRelativeTime().count();
    const double age = static_cast<double>(count.QuadPart) / frequency.QuadPart - ticks / 10000000.0;
    return age > 0.0 ? now - std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(age)) : now;
}
}

struct WindowInput::State {
    HWND window = nullptr;
    DWORD pid = 0;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11Texture2D> staging;
    Direct3D11::IDirect3DDevice compositor{nullptr};
    GraphicsCaptureItem item{nullptr};
    Direct3D11CaptureFramePool pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    GraphicsCaptureItem::Closed_revoker closedEvent;
    std::shared_ptr<std::atomic<bool>> closed = std::make_shared<std::atomic<bool>>(false);
    SizeInt32 size{};

    void startSession() {
        pool = Direct3D11CaptureFramePool::CreateFreeThreaded(compositor,
            DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
        session = pool.CreateCaptureSession(item);
        try { session.IsCursorCaptureEnabled(false); } catch (const winrt::hresult_error&) {}
        session.StartCapture();
    }

    ~State() {
        closedEvent.revoke();
        try {
            if (session) session.Close();
            if (pool) pool.Close();
        } catch (const winrt::hresult_error&) {}
    }
};

WindowInput::WindowInput() = default;
WindowInput::~WindowInput() = default;

bool WindowInput::open(HWND window, DWORD pid) {
    state_.reset();
    try {
        if (!ownsWindow(window, pid) || !GraphicsCaptureSession::IsSupported()) return false;
        auto state = std::make_unique<State>();
        state->window = window;
        state->pid = pid;
        winrt::check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
            state->device.GetAddressOf(), nullptr, state->context.GetAddressOf()));
        ComPtr<IDXGIDevice> dxgi;
        winrt::check_hresult(state->device.As(&dxgi));
        winrt::com_ptr<IInspectable> inspectable;
        winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), inspectable.put()));
        state->compositor = inspectable.as<Direct3D11::IDirect3DDevice>();
        const auto interop = winrt::get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        winrt::check_hresult(interop->CreateForWindow(window, winrt::guid_of<GraphicsCaptureItem>(),
                                                     winrt::put_abi(state->item)));
        state->size = state->item.Size();
        if (!ownsWindow(window, pid) || !validDimensions(state->size.Width, state->size.Height)) return false;
        state->closedEvent = state->item.Closed(winrt::auto_revoke, [closed = state->closed](auto&&, auto&&) {
            closed->store(true);
        });
        state->startSession();
        state_ = std::move(state);
        return true;
    } catch (const winrt::hresult_error&) {
        return false;
    }
}

Input::Result WindowInput::poll(Crop crop, Frame& output) {
    using Result = Input::Result;
    if (!state_) return Result::Failed;
    auto& state = *state_;
    if (state.closed->load() || !ownsWindow(state.window, state.pid)) return Result::Finished;
    try {
        Direct3D11CaptureFrame frame{nullptr};
        for (int index = 0; index < 2; ++index) {
            auto next = state.pool.TryGetNextFrame();
            if (!next) break;
            if (frame) frame.Close();
            frame = std::move(next);
        }
        if (!frame) return Result::Pending;
        struct CloseFrame {
            Direct3D11CaptureFrame& frame;
            ~CloseFrame() {
                try { if (frame) frame.Close(); } catch (const winrt::hresult_error&) {}
            }
        } closeFrame{frame};
        if (IsIconic(state.window)) return Result::Pending;
        const auto size = frame.ContentSize();
        if (!validDimensions(size.Width, size.Height)) return Result::Failed;
        if (size.Width != state.size.Width || size.Height != state.size.Height) {
            frame.Close();
            frame = nullptr;
            state.staging.Reset();
            // Restarting the session requests the first frame at the new size,
            // including when the window's contents stop changing after resize.
            state.session.Close();
            state.size = size;
            state.startSession();
            return Result::Pending;
        }
        const auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
        ComPtr<ID3D11Texture2D> source;
        winrt::check_hresult(access->GetInterface(IID_PPV_ARGS(source.GetAddressOf())));
        D3D11_TEXTURE2D_DESC description{};
        source->GetDesc(&description);
        if (description.Width < static_cast<UINT>(size.Width) || description.Height < static_cast<UINT>(size.Height))
            return Result::Pending;
        if (!state.staging) {
            description.Width = size.Width;
            description.Height = size.Height;
            description.Usage = D3D11_USAGE_STAGING;
            description.BindFlags = 0;
            description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            description.MiscFlags = 0;
            winrt::check_hresult(state.device->CreateTexture2D(&description, nullptr, state.staging.GetAddressOf()));
        }
        D3D11_BOX box{0, 0, 0, static_cast<UINT>(size.Width), static_cast<UINT>(size.Height), 1};
        state.context->CopySubresourceRegion(state.staging.Get(), 0, 0, 0, 0, source.Get(), 0, &box);
        D3D11_MAPPED_SUBRESOURCE mapping{};
        winrt::check_hresult(state.context->Map(state.staging.Get(), 0, D3D11_MAP_READ, 0, &mapping));
        struct Unmap {
            State& state;
            ~Unmap() { state.context->Unmap(state.staging.Get(), 0); }
        } unmap{state};
        if (!copyBgra(static_cast<const BYTE*>(mapping.pData), static_cast<std::size_t>(mapping.RowPitch) * size.Height,
                      0, static_cast<LONG>(mapping.RowPitch), size.Width, size.Height, crop, output))
            return Result::Failed;
        output.observed = observationTime(frame);
        return Result::Ready;
    } catch (const winrt::hresult_error&) {
        return state.closed->load() || !ownsWindow(state.window, state.pid) ? Result::Finished : Result::Failed;
    }
}

}
