#pragma once

#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace vaporlock::video {
using Microsoft::WRL::ComPtr;
using Clock = std::chrono::steady_clock;
constexpr DWORD kVideoStream = static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM);
constexpr std::uint64_t kMaxPixels = 4096ULL * 2160;

class Foundation {
public:
    Foundation() : com_(CoInitializeEx(nullptr, COINIT_MULTITHREADED)),
        started_((SUCCEEDED(com_) || com_ == RPC_E_CHANGED_MODE) && SUCCEEDED(MFStartup(MF_VERSION))) {}
    ~Foundation() {
        if (started_) MFShutdown();
        if (SUCCEEDED(com_)) CoUninitialize();
    }
    Foundation(const Foundation&) = delete;
    Foundation& operator=(const Foundation&) = delete;
    explicit operator bool() const { return started_; }

private:
    HRESULT com_;
    bool started_;
};

struct Device {
    std::wstring id;
    std::wstring name;
};

inline std::wstring selectorString(IMFAttributes* attributes, REFGUID key) {
    WCHAR* data = nullptr;
    UINT32 length = 0;
    if (FAILED(attributes->GetAllocatedString(key, &data, &length))) return {};
    std::wstring result(data, length);
    CoTaskMemFree(data);
    return result;
}

inline bool listDevices(std::vector<Device>& devices) {
    Foundation foundation;
    ComPtr<IMFAttributes> attributes;
    if (!foundation || FAILED(MFCreateAttributes(&attributes, 1))
        || FAILED(attributes->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID)))
        return false;
    IMFActivate** activation = nullptr;
    UINT32 count = 0;
    if (FAILED(MFEnumDeviceSources(attributes.Get(), &activation, &count))) return false;
    for (UINT32 i = 0; i < count; ++i) {
        ComPtr<IMFActivate> device;
        device.Attach(activation[i]);
        auto id = selectorString(device.Get(), MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK);
        auto name = selectorString(device.Get(), MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME);
        if (!id.empty() && id.size() <= 2048 && devices.size() < 64)
            devices.push_back({std::move(id), name.empty() ? L"Video device" : name.substr(0, 256)});
    }
    CoTaskMemFree(activation);
    return true;
}

struct Crop {
    int offsetX, offsetY, width, height;
    bool fullFrame;
};

struct Frame {
    int width = 0, height = 0, left = 0, top = 0, sourceWidth = 0, sourceHeight = 0;
    Clock::time_point observed{};
    std::vector<unsigned char> bgra;
};

inline bool validDimensions(UINT32 width, UINT32 height) {
    return width > 0 && height > 0 && width <= 8192 && height <= 8192
        && static_cast<std::uint64_t>(width) * height <= kMaxPixels;
}

// The source extent includes row padding. scanlineOffset points at the
// top row even when the memory layout is bottom-up.
inline bool copyBgra(const BYTE* data, std::size_t size, std::int64_t scanlineOffset, LONG stride,
                     int width, int height, Crop crop, Frame& frame) {
    if (!data || !validDimensions(width, height) || std::abs(static_cast<std::int64_t>(stride)) < width * 4)
        return false;
    const std::int64_t last = scanlineOffset + (height - 1) * static_cast<std::int64_t>(stride);
    if (std::min(scanlineOffset, last) < 0
        || static_cast<std::uint64_t>(std::max(scanlineOffset, last) + width * 4) > size) return false;
    frame.sourceWidth = width;
    frame.sourceHeight = height;
    frame.width = crop.fullFrame ? width : std::clamp(crop.width, 1, width);
    frame.height = crop.fullFrame ? height : std::clamp(crop.height, 1, height);
    frame.left = std::clamp((width - frame.width) / 2 + crop.offsetX, 0, width - frame.width);
    frame.top = std::clamp((height - frame.height) / 2 + crop.offsetY, 0, height - frame.height);
    const auto rowBytes = static_cast<std::size_t>(frame.width) * 4;
    frame.bgra.resize(rowBytes * frame.height);
    for (int row = 0; row < frame.height; ++row) {
        auto* target = frame.bgra.data() + rowBytes * row;
        const auto offset = scanlineOffset + (frame.top + row) * static_cast<std::int64_t>(stride) + frame.left * 4;
        std::memcpy(target, data + offset, rowBytes);
        for (std::size_t position = 3; position < rowBytes; position += 4) target[position] = 255;
    }
    return true;
}

struct Sample {
    ComPtr<IMFSample> pixels;
    Clock::time_point received{};
    LONGLONG timestamp = 0;
    DWORD flags = 0;
    bool failed = false;
};

class ReaderCallback final : public IMFSourceReaderCallback {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (iid != IID_IUnknown && iid != __uuidof(IMFSourceReaderCallback)) return E_NOINTERFACE;
        *result = static_cast<IMFSourceReaderCallback*>(this);
        AddRef();
        return S_OK;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = --refs_;
        if (!remaining) delete this;
        return remaining;
    }

    HRESULT STDMETHODCALLTYPE OnReadSample(HRESULT result, DWORD, DWORD flags, LONGLONG time, IMFSample* sample) override {
        const bool failed = FAILED(result) || (flags & MF_SOURCE_READERF_ERROR) != 0;
        const DWORD terminal = MF_SOURCE_READERF_ENDOFSTREAM | MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED;
        bool readNext = false;
        {
            std::lock_guard lock(mutex_);
            pending_ = false;
            if (!reader_) return S_OK;
            if (sample || failed || (flags & terminal))
                latest_ = Sample{sample, Clock::now(), time, flags, failed};
            // Devices keep one newest sample. Pause at format boundaries
            // until the owner has loaded the updated RGB layout.
            readNext = continuous_ && !failed && !(flags & terminal);
        }
        if (readNext) request(true);
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE OnFlush(DWORD) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE OnEvent(DWORD, IMFMediaEvent*) override { return S_OK; }

    void start(IMFSourceReader* reader, bool continuous) {
        {
            std::lock_guard lock(mutex_);
            reader_ = reader;
            continuous_ = continuous;
        }
        request();
    }

    void stop() {
        std::unique_lock lock(mutex_);
        reader_ = nullptr;
        requestsDrained_.wait(lock, [this] { return submitting_ == 0; });
        latest_.reset();
    }

    void request(bool replaceLatest = false) {
        ComPtr<IMFSourceReader> reader;
        {
            std::lock_guard lock(mutex_);
            if (!reader_ || pending_ || (!replaceLatest && latest_)) return;
            reader = reader_;
            pending_ = true;
            ++submitting_;
        }
        const HRESULT result = reader->ReadSample(kVideoStream, 0, nullptr, nullptr, nullptr, nullptr);
        reader.Reset();
        std::lock_guard lock(mutex_);
        if (FAILED(result) && reader_) {
            pending_ = false;
            latest_ = Sample{};
            latest_->failed = true;
        }
        if (--submitting_ == 0) requestsDrained_.notify_all();
    }

    std::optional<Sample> take() {
        std::lock_guard lock(mutex_);
        return std::exchange(latest_, std::nullopt);
    }

private:
    std::atomic<ULONG> refs_{1};
    std::mutex mutex_;
    std::condition_variable requestsDrained_;
    unsigned submitting_ = 0;
    IMFSourceReader* reader_ = nullptr; // Owned by Input until stop().
    bool continuous_ = false, pending_ = false;
    std::optional<Sample> latest_;
};

class Input {
public:
    enum class Result { Pending, Ready, Finished, Failed };
    ~Input() {
        if (callback_) callback_->stop();
        reader_.Reset(); // Also shuts down its media source.
    }

    bool open(const std::wstring& device, const std::wstring& file, int captureFps) {
        if (!foundation_ || device.empty() == file.empty()) return false;
        file_ = !file.empty();
        ComPtr<IMFAttributes> attributes;
        callback_.Attach(new ReaderCallback);
        if (FAILED(MFCreateAttributes(&attributes, 3))
            || FAILED(attributes->SetUnknown(MF_SOURCE_READER_ASYNC_CALLBACK, callback_.Get()))
            || FAILED(attributes->SetUINT32(MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, TRUE))
            || (!file_ && FAILED(attributes->SetUINT32(MF_LOW_LATENCY, TRUE)))) return false;
        if (file_) {
            std::error_code error;
            if (!std::filesystem::is_regular_file(file, error)) return false;
            ComPtr<IMFByteStream> stream;
            if (FAILED(MFCreateFile(MF_ACCESSMODE_READ, MF_OPENMODE_FAIL_IF_NOT_EXIST, MF_FILEFLAGS_NONE,
                                    file.c_str(), &stream))
                || FAILED(MFCreateSourceReaderFromByteStream(stream.Get(), attributes.Get(), &reader_))) return false;
        } else {
            ComPtr<IMFAttributes> selection;
            ComPtr<IMFMediaSource> source;
            if (FAILED(MFCreateAttributes(&selection, 2))
                || FAILED(selection->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID))
                || FAILED(selection->SetString(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK, device.c_str()))
                || FAILED(MFCreateDeviceSource(selection.Get(), &source))) return false;
            if (FAILED(MFCreateSourceReaderFromMediaSource(source.Get(), attributes.Get(), &reader_))) {
                source->Shutdown();
                return false;
            }
        }
        if (FAILED(reader_->SetStreamSelection(static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS), FALSE))
            || FAILED(reader_->SetStreamSelection(kVideoStream, TRUE))) return false;
        if (!file_) selectDeviceMode(captureFps);
        ComPtr<IMFMediaType> output;
        if (FAILED(MFCreateMediaType(&output))
            || FAILED(output->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video))
            || FAILED(output->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32))
            || FAILED(reader_->SetCurrentMediaType(kVideoStream, nullptr, output.Get()))
            || !loadFormat()) return false;
        callback_->start(reader_.Get(), !file_);
        return true;
    }

    Result poll(Crop crop, int captureFps, Frame& frame) {
        const auto now = Clock::now();
        const int fps = std::max(1, captureFps);
        const auto period = std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1.0 / fps));
        if (!file_ || !sample_) {
            if (auto incoming = callback_->take()) sample_ = std::move(incoming);
        }
        if (!sample_) {
            if (file_) callback_->request();
            return Result::Pending;
        }
        if (sample_->failed) return Result::Failed;
        if (sample_->flags & MF_SOURCE_READERF_ENDOFSTREAM) return file_ ? Result::Finished : Result::Failed;
        if (sample_->flags & MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED) {
            if (!loadFormat()) return Result::Failed;
            sample_->flags &= ~MF_SOURCE_READERF_CURRENTMEDIATYPECHANGED;
            if (!file_) callback_->request();
        }
        if (!sample_->pixels) {
            sample_.reset();
            if (file_) callback_->request();
            return Result::Pending;
        }
        bool keep = true;
        double elapsed = 0;
        if (file_) {
            if (!firstTimestamp_) {
                firstTimestamp_ = sample_->timestamp;
                playbackStarted_ = now;
            }
            elapsed = std::max(0.0, (static_cast<double>(sample_->timestamp) - *firstTimestamp_) / 10000000.0);
            if (now - playbackStarted_ < std::chrono::duration<double>(elapsed)) return Result::Pending;
            const auto slot = [fps](double seconds) { return std::floor(seconds * fps + .0001); };
            keep = !lastVideoTime_ || slot(elapsed) > slot(*lastVideoTime_);
        } else if (lastRate_ == fps && now < nextFrame_) {
            return Result::Pending;
        }
        Sample sample = std::move(*sample_);
        sample_.reset();
        if (file_) callback_->request();
        if (!keep) return Result::Pending;
        if (!copySample(sample.pixels.Get(), crop, frame)) return Result::Failed;
        frame.observed = file_ ? now : sample.received;
        if (file_) lastVideoTime_ = elapsed;
        else {
            nextFrame_ = lastRate_ == fps && now - nextFrame_ < period ? nextFrame_ + period : now + period;
            lastRate_ = fps;
        }
        return Result::Ready;
    }

private:
    void selectDeviceMode(int captureFps) {
        ComPtr<IMFSourceReaderEx> readerEx;
        if (FAILED(reader_.As(&readerEx))) return;
        ComPtr<IMFMediaType> best;
        std::tuple<bool, double, std::uint64_t> bestScore{};
        for (DWORD index = 0; index < 512; ++index) {
            ComPtr<IMFMediaType> native;
            if (FAILED(reader_->GetNativeMediaType(kVideoStream, index, &native))) break;
            UINT32 width = 0, height = 0, numerator = 0, denominator = 1;
            if (FAILED(MFGetAttributeSize(native.Get(), MF_MT_FRAME_SIZE, &width, &height))
                || !validDimensions(width, height)) continue;
            const bool hasRate = SUCCEEDED(MFGetAttributeRatio(native.Get(), MF_MT_FRAME_RATE, &numerator, &denominator)) && denominator;
            const double fps = hasRate ? static_cast<double>(numerator) / denominator : 0;
            const bool reachesRate = fps + .1 >= captureFps;
            const auto score = std::make_tuple(reachesRate, reachesRate ? static_cast<double>(captureFps) : fps,
                                              static_cast<std::uint64_t>(width) * height);
            if (!best || score > bestScore) {
                best = native;
                bestScore = score;
            }
        }
        DWORD flags = 0;
        if (best) readerEx->SetNativeMediaType(kVideoStream, best.Get(), &flags);
    }

    bool loadFormat() {
        ComPtr<IMFMediaType> type;
        GUID subtype{};
        UINT32 width = 0, height = 0, stride = 0;
        if (FAILED(reader_->GetCurrentMediaType(kVideoStream, &type))
            || FAILED(type->GetGUID(MF_MT_SUBTYPE, &subtype)) || subtype != MFVideoFormat_RGB32
            || FAILED(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height))
            || !validDimensions(width, height)) return false;
        width_ = static_cast<int>(width);
        height_ = static_cast<int>(height);
        if (SUCCEEDED(type->GetUINT32(MF_MT_DEFAULT_STRIDE, &stride))) stride_ = static_cast<LONG>(stride);
        else if (FAILED(MFGetStrideForBitmapInfoHeader(MFVideoFormat_RGB32.Data1, width, &stride_))) return false;
        return std::abs(static_cast<std::int64_t>(stride_)) >= width_ * 4;
    }

    bool copySample(IMFSample* sample, Crop crop, Frame& output) {
        ComPtr<IMFMediaBuffer> buffer;
        if (FAILED(sample->ConvertToContiguousBuffer(&buffer))) return false;
        ComPtr<IMF2DBuffer2> surface;
        if (SUCCEEDED(buffer.As(&surface))) {
            BYTE* scanline = nullptr;
            BYTE* start = nullptr;
            LONG pitch = 0;
            DWORD length = 0;
            if (SUCCEEDED(surface->Lock2DSize(MF2DBuffer_LockFlags_Read, &scanline, &pitch, &start, &length))) {
                const bool copied = copyBgra(start, length, scanline - start, pitch, width_, height_, crop, output);
                surface->Unlock2D();
                return copied;
            }
        }
        BYTE* start = nullptr;
        DWORD length = 0;
        if (FAILED(buffer->Lock(&start, nullptr, &length))) return false;
        const auto first = stride_ < 0 ? -(height_ - 1) * static_cast<std::int64_t>(stride_) : 0;
        const bool copied = copyBgra(start, length, first, stride_, width_, height_, crop, output);
        buffer->Unlock();
        return copied;
    }

    Foundation foundation_;
    ComPtr<ReaderCallback> callback_;
    ComPtr<IMFSourceReader> reader_;
    std::optional<Sample> sample_;
    bool file_ = false;
    int width_ = 0, height_ = 0;
    LONG stride_ = 0;
    std::optional<LONGLONG> firstTimestamp_;
    std::optional<double> lastVideoTime_;
    Clock::time_point playbackStarted_{}, nextFrame_{};
    int lastRate_ = 0;
};
} // namespace vaporlock::video
