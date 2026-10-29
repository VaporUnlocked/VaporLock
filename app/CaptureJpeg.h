#pragma once
#include <windows.h>
#include <objidl.h>
#include <wincodec.h>
#include <wrl/client.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include "app/CaptureQualification.h"

// Collection-specific sink. The existing preview encoder is untouched. This
// fixed allocation bounds owned JPEG bytes, not opaque WIC or whole-engine RSS.
class CaptureJpegStream final : public IStream {
public:
    static constexpr std::size_t capacity = 32U * 1024U * 1024U;
    CaptureJpegStream() : m_bytes(std::make_unique<unsigned char[]>(capacity)) {}
    const unsigned char* data() const { return m_bytes.get(); }
    std::size_t size() const { return m_length; } // read after WIC commits/releases

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (!value) return E_POINTER;
        *value = nullptr;
        if (iid != IID_IUnknown && iid != IID_ISequentialStream && iid != IID_IStream) return E_NOINTERFACE;
        *value = static_cast<IStream*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++m_references; }
    ULONG STDMETHODCALLTYPE Release() override {
        const auto count = --m_references;
        if (!count) delete this;
        return count;
    }
    HRESULT STDMETHODCALLTYPE Read(void* value, ULONG bytes, ULONG* read) override {
        if (read) *read = 0;
        if (bytes && !value) return E_POINTER;
        std::lock_guard lock(m_mutex);
        const auto count = (std::min)(static_cast<std::size_t>(bytes), m_position < m_length ? m_length - m_position : 0);
        if (count) std::memcpy(value, m_bytes.get() + m_position, count);
        m_position += count;
        if (read) *read = static_cast<ULONG>(count);
        return count == bytes ? S_OK : S_FALSE;
    }
    HRESULT STDMETHODCALLTYPE Write(const void* value, ULONG bytes, ULONG* written) override {
        if (written) *written = 0;
        if (bytes && !value) return E_POINTER;
        std::lock_guard lock(m_mutex);
        if (bytes > capacity - m_position) return STG_E_MEDIUMFULL;
        if (m_position > m_length) std::memset(m_bytes.get() + m_length, 0, m_position - m_length);
        if (bytes) std::memcpy(m_bytes.get() + m_position, value, bytes);
        m_position += bytes;
        m_length = (std::max)(m_length, m_position);
        if (written) *written = bytes;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Seek(LARGE_INTEGER offset, DWORD origin, ULARGE_INTEGER* result) override {
        std::lock_guard lock(m_mutex);
        std::size_t base;
        if (origin == STREAM_SEEK_SET) base = 0;
        else if (origin == STREAM_SEEK_CUR) base = m_position;
        else if (origin == STREAM_SEEK_END) base = m_length;
        else return STG_E_INVALIDFUNCTION;
        if (offset.QuadPart < 0) {
            const auto magnitude = static_cast<std::uint64_t>(-(offset.QuadPart + 1)) + 1;
            if (magnitude > base) return STG_E_INVALIDFUNCTION;
            m_position = base - static_cast<std::size_t>(magnitude);
        } else {
            const auto magnitude = static_cast<std::uint64_t>(offset.QuadPart);
            if (magnitude > capacity - base) return STG_E_MEDIUMFULL;
            m_position = base + static_cast<std::size_t>(magnitude);
        }
        if (result) result->QuadPart = m_position;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetSize(ULARGE_INTEGER size) override {
        std::lock_guard lock(m_mutex);
        if (size.QuadPart > capacity) return STG_E_MEDIUMFULL;
        const auto length = static_cast<std::size_t>(size.QuadPart);
        if (length > m_length) std::memset(m_bytes.get() + m_length, 0, length - m_length);
        m_length = length; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Stat(STATSTG* value, DWORD) override {
        if (!value) return E_POINTER;
        std::lock_guard lock(m_mutex);
        *value = {}; value->type = STGTY_STREAM; value->cbSize.QuadPart = m_length;
        value->grfMode = STGM_READWRITE; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Commit(DWORD) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE Revert() override { return STG_E_INVALIDFUNCTION; }
    HRESULT STDMETHODCALLTYPE CopyTo(IStream*, ULARGE_INTEGER, ULARGE_INTEGER*, ULARGE_INTEGER*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE LockRegion(ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override { return STG_E_INVALIDFUNCTION; }
    HRESULT STDMETHODCALLTYPE UnlockRegion(ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override { return STG_E_INVALIDFUNCTION; }
    HRESULT STDMETHODCALLTYPE Clone(IStream** value) override { if (value) *value = nullptr; return E_NOTIMPL; }
private:
    ~CaptureJpegStream() = default;
    std::atomic<ULONG> m_references{1};
    std::unique_ptr<unsigned char[]> m_bytes;
    std::size_t m_position = 0, m_length = 0;
    std::mutex m_mutex;
};

inline bool encodeCaptureJpeg(int width, int height, const unsigned char* pixels, std::size_t pixelBytes,
                             int quality, const std::function<bool()>& current,
                             Microsoft::WRL::ComPtr<CaptureJpegStream>& encoded) {
    std::size_t checked = 0;
    if (!pixels || quality < 30 || quality > 100 || !captureBgraBytes(width, height, pixelBytes, checked) || !current()) return false;
    Microsoft::WRL::ComPtr<IWICImagingFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(factory.GetAddressOf())))) return false;
    Microsoft::WRL::ComPtr<CaptureJpegStream> stream;
    stream.Attach(new CaptureJpegStream());
    Microsoft::WRL::ComPtr<IWICBitmapEncoder> encoder;
    if (FAILED(factory->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, encoder.GetAddressOf()))
        || FAILED(encoder->Initialize(stream.Get(), WICBitmapEncoderNoCache))) return false;
    Microsoft::WRL::ComPtr<IWICBitmapFrameEncode> frame;
    Microsoft::WRL::ComPtr<IPropertyBag2> properties;
    if (FAILED(encoder->CreateNewFrame(frame.GetAddressOf(), properties.GetAddressOf()))) return false;
    PROPBAG2 option{}; option.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT value; VariantInit(&value); value.vt = VT_R4; value.fltVal = static_cast<float>(quality) / 100.0F;
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    if (FAILED(properties->Write(1, &option, &value)) || FAILED(frame->Initialize(properties.Get()))
        || FAILED(frame->SetSize(width, height)) || FAILED(frame->SetPixelFormat(&format))
        || format != GUID_WICPixelFormat24bppBGR) return false;
    std::array<unsigned char, 16384U * 3U> row;
    const auto stride = static_cast<UINT>(width * 3);
    for (int y = 0; y < height; ++y) {
        if (!current()) return false;
        const auto* source = pixels + static_cast<std::size_t>(y) * width * 4U;
        for (int x = 0; x < width; ++x) std::memcpy(row.data() + static_cast<std::size_t>(x) * 3U, source + static_cast<std::size_t>(x) * 4U, 3U);
        if (FAILED(frame->WritePixels(1, stride, stride, row.data()))) return false;
    }
    if (!current() || FAILED(frame->Commit()) || FAILED(encoder->Commit()) || !stream->size()) return false;
    frame.Reset(); encoder.Reset(); // retire codec references before reading sink
    encoded = std::move(stream); return true;
}
