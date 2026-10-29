#pragma once
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>
#include "app/CaptureQualification.h"
#include "app/CaptureJpeg.h"
#include "app/CaptureRoot.h"
#include "app/CaptureFile.h"

// Native output/config guard -> collection try_lock only; never the reverse.
// A retirement request blocks admission immediately. Completed retirement and
// final rename serialize through the collection guard. Blocked IO stays owned.
class DatasetWriter {
public:
    using Clock = std::chrono::steady_clock;
    enum class Admission { Accepted, Replaced, Disabled, Stale, Cooldown,
        Unqualified, Unavailable, OverLimit, RootPending, Busy };
    struct Status {
        std::uint64_t saved = 0, dropped = 0, savedBytes = 0;
        const char* limitReason = "none";
        std::size_t ownedBgraBytes = 0, highWaterBgraBytes = 0;
        bool pending = false, encoding = false, rootReady = false;
        bool cleanupPending = false, retirementPending = false, closing = false, complete = false;
        bool qualificationUnavailable = false;
        const char* error = "none";
        Clock::time_point lastAdmission{}, lastCommit{}; // private native test receipt, never renderer data
    };
    using Saved = std::function<void(std::uint64_t)>;
    using Receipt = std::function<void(std::uint64_t, const std::wstring&)>;
    using Notice = std::function<void(std::string)>;
    using BeforeEncode = std::function<void()>; // deterministic blocking test seam, never writes
    using BeforePrepare = std::function<void()>; // source retirement test seam, no filesystem effects
    using Authorize = std::function<bool()>;
    DatasetWriter(Saved saved, Notice notice, BeforeEncode beforeEncode = {}, BeforePrepare beforePrepare = {}, Receipt receipt = {}, Authorize authorize = {})
        : m_saved(std::move(saved)), m_notice(std::move(notice)), m_beforeEncode(std::move(beforeEncode)),
          m_beforePrepare(std::move(beforePrepare)), m_receipt(std::move(receipt)), m_authorize(std::move(authorize)) {
        m_thread = std::thread([this] {
            try { run(); }
            catch (...) {
                // An unexpected worker failure cannot acknowledge root/file/
                // callback cleanup. Keep the entire graph closing until its
                // existing process owner resolves it; no destructor detach.
                m_stopping = true; m_workerFailed = true; requestRetirement();
            }
        });
    }
    ~DatasetWriter() {
        // A failed 2s ack retains this complete graph; no detach or freeing a
        // joinable worker/callback/root/file. Existing engine process owner may
        // terminate a closing process. Destruction waits honestly if it cannot.
        while (!stop()) {}
    }
    DatasetWriter(const DatasetWriter&) = delete;
    DatasetWriter& operator=(const DatasetWriter&) = delete;

    bool observeOutput(const AppConfig& config, std::uint64_t revision, bool authorized, bool running) {
        std::unique_lock lock(m_mutex,std::try_to_lock);
        if (!lock.owns_lock()) { requestRetirement(); return false; }
        const bool valid = config.datasetDirectory.size() <= 32700
            && config.datasetIntervalMs >= 100 && config.datasetIntervalMs <= 1000000
            && config.datasetMaxImages >= 0 && config.datasetMaxImages <= 1000000
            && config.datasetMaxMiB >= 0 && config.datasetMaxMiB <= 1000000
            && config.datasetJpegQuality >= 30 && config.datasetJpegQuality <= 100
            && std::isfinite(config.datasetMinConfidence)
            && config.datasetMinConfidence >= .01 && config.datasetMinConfidence <= 1.;
        const bool allowed = valid && running && config.datasetEnabled
            && (!config.datasetCaptureRequiresActivation || authorized);
        const bool rootDesired = valid && running && config.datasetEnabled;
        const bool rootChanged = !m_initialized || config.datasetDirectory != m_directory;
        const bool changed = !m_initialized || revision != m_revision || allowed != m_allowed
            || rootChanged || rootDesired != m_rootDesired || config.datasetIntervalMs != m_interval || config.datasetJpegQuality != m_quality
            || config.datasetCaptureConfidenceEnabled != m_gate || config.datasetMinConfidence != m_cutoff
            || config.datasetMaxImages != m_maxImages || config.datasetMaxMiB != m_maxMiB;
        if (changed || m_appliedEpoch != m_epoch.load()) {
            // Copy bounded configuration before changing authority; no filesystem
            // calls or live/output callback reentry inside this guard.
            std::wstring directory;
            try { if (rootChanged && valid) directory = config.datasetDirectory; }
            catch (...) { requestRetirement(); return false; }
            if (changed) ++m_epoch;
            discardPendingLocked(); m_appliedEpoch = m_epoch.load();
            m_initialized = true; m_revision = revision; m_allowed = allowed;
            m_interval = config.datasetIntervalMs; m_quality = config.datasetJpegQuality;
            const bool limitsChanged = config.datasetMaxImages != m_maxImages || config.datasetMaxMiB != m_maxMiB;
            m_maxImages = config.datasetMaxImages; m_maxMiB = config.datasetMaxMiB;
            if (limitsChanged) { m_limitBlocked = false; m_status.limitReason = "none"; }
            m_gate = config.datasetCaptureConfidenceEnabled; m_cutoff = config.datasetMinConfidence;
            if (rootChanged && valid) { m_directory.swap(directory); m_prepareRoot = true; }
            if (rootDesired != m_rootDesired) m_prepareRoot = true;
            if (rootDesired && !m_root) m_prepareRoot = true;
            m_rootDesired = rootDesired;
            if (!valid) { m_allowed = false; m_status.error = "invalid-configuration"; }
            m_wake.notify_all();
        }
        return true;
    }
    bool observeSource(CaptureFrameIdentity identity) {
        std::unique_lock lock(m_mutex,std::try_to_lock);
        if (!lock.owns_lock()) { requestRetirement(); return false; }
        if (!identity.session || !identity.sourceGeneration || !identity.frameId || identity.configRevision != m_revision) return false;
        if (identity.session != m_source.session || identity.sourceGeneration != m_source.sourceGeneration) {
            ++m_epoch; discardPendingLocked(); m_appliedEpoch = m_epoch.load(); m_wake.notify_all();
        }
        m_source = identity; // a later frame never substitutes an already-owned sample
        return true;
    }
    Admission admit(int width, int height, const std::vector<unsigned char>& clean,
                    CaptureFrameIdentity identity, const CaptureQualification& qualification,
                    bool fresh, Clock::time_point observedAt, std::int64_t capturedAtMs) {
        std::size_t bytes = 0;
        if (!captureBgraBytes(width,height,clean.size(),bytes)) {
            std::unique_lock rejected(m_mutex,std::try_to_lock);
            if (rejected.owns_lock()) { ++m_status.dropped; m_status.error = "frame-limit"; }
            return Admission::OverLimit;
        }
        std::unique_lock lock(m_mutex,std::try_to_lock);
        if (!lock.owns_lock()) return Admission::Busy;
        if (!m_allowed || m_stopping.load()) return Admission::Disabled;
        if (m_limitBlocked || !budgetAllowsLocked(0)) return Admission::OverLimit;
        if (!fresh || identity != m_source || identity.configRevision != m_revision
            || m_appliedEpoch != m_epoch.load() || observedAt > Clock::now()) return Admission::Stale;
        if (m_gate && (!qualification.observed || qualification.identity != identity)) {
            m_status.qualificationUnavailable = true; return Admission::Unavailable;
        }
        m_status.qualificationUnavailable = false;
        if (m_gate && !qualification.qualifies) return Admission::Unqualified;
        if (m_lastAdmission != Clock::time_point{} && (observedAt < m_lastAdmission
            || observedAt-m_lastAdmission < std::chrono::milliseconds(m_interval))) return Admission::Cooldown;
        if (!m_root || !m_status.rootReady || m_prepareRoot || cleanupPendingLocked()) return Admission::RootPending;
        if (m_reservedBytes) return Admission::Busy;
        const bool replaced = m_pending.has_value();
        discardPendingLocked(); // old owned pending freed BEFORE new reservation/allocation
        const auto epoch = m_appliedEpoch;
        Frame frame{width,height,{},bytes,identity,capturedAtMs,m_interval,m_quality,epoch,m_root};
        m_reservedBytes = bytes; updateBytesLocked();
        lock.unlock();
        try {
            frame.pixels = std::make_unique<unsigned char[]>(bytes);
            std::memcpy(frame.pixels.get(),clean.data(),bytes);
        } catch (...) {
            lock.lock(); m_reservedBytes = 0; updateBytesLocked(); ++m_status.dropped;
            return Admission::OverLimit;
        }
        lock.lock(); m_reservedBytes = 0;
        if (!current(frame) || !m_allowed || identity != m_source || m_appliedEpoch != epoch) {
            frame.pixels.reset(); updateBytesLocked(); ++m_status.dropped; return Admission::Stale;
        }
        m_pending = std::move(frame); m_lastAdmission = observedAt; m_status.lastAdmission = observedAt;
        m_status.pending = true; updateBytesLocked(); m_wake.notify_one();
        return replaced ? Admission::Replaced : Admission::Accepted;
    }
    std::optional<Status> status() const {
        std::unique_lock lock(m_mutex,std::try_to_lock);
        if (!lock.owns_lock()) return std::nullopt; // unknown is not fabricated zeros
        auto value = m_status;
        value.rootReady = value.rootReady && !m_prepareRoot && m_appliedEpoch == m_epoch.load() && !m_stopping.load();
        value.retirementPending = m_appliedEpoch != m_epoch.load();
        value.cleanupPending = cleanupPendingLocked();
        value.closing = m_stopping.load(); value.complete = m_complete.load();
        if (m_workerFailed.load()) { value.cleanupPending = true; value.error = "writer-unavailable"; }
        return value;
    }
    bool stop() {
        if (!m_thread.joinable()) return true;
        m_stopping = true; requestRetirement();
        // Wait only this owned worker, bounded ack budget; then real join.
        const auto wait = WaitForSingleObject(reinterpret_cast<HANDLE>(m_thread.native_handle()),2000);
        if (wait != WAIT_OBJECT_0 || !m_complete.load()) return false;
        m_thread.join(); return true;
    }

private:
    // Lifetime of this writer is one engine session. Configuration/source/root
    // changes never reset committed accounting. The single writer owns commits.
    bool budgetAllowsLocked(std::size_t encodedBytes) {
        const auto maximumBytes = static_cast<std::uint64_t>(m_maxMiB) * 1048576ULL;
        const bool imagesFull = m_maxImages && m_status.saved >= static_cast<std::uint64_t>(m_maxImages);
        const bool bytesFull = m_maxMiB && (m_status.savedBytes >= maximumBytes
            || static_cast<std::uint64_t>(encodedBytes) > maximumBytes - m_status.savedBytes);
        if (imagesFull || bytesFull) {
            m_limitBlocked = true;
            m_status.limitReason = imagesFull ? "images" : "storage";
            discardPendingLocked();
            return false;
        }
        return true;
    }
    Authorize m_authorize;
    struct Frame {
        int width,height;
        std::unique_ptr<unsigned char[]> pixels;
        std::size_t bytes;
        CaptureFrameIdentity identity;
        std::int64_t capturedAtMs;
        int interval,quality;
        std::uint64_t epoch;
        std::shared_ptr<CaptureRoot> root;
    };
    void requestRetirement() { ++m_epoch; m_wake.notify_all(); }
    bool current(const Frame& frame) const { return !m_stopping.load() && frame.epoch == m_epoch.load(); }
    bool cleanupPendingLocked() const { return m_fileCleanupPending || m_rootCleanupPending; }
    void discardPendingLocked() {
        if (m_pending) { m_pending.reset(); ++m_status.dropped; }
        m_status.pending = false; updateBytesLocked();
    }
    void updateBytesLocked() {
        m_status.ownedBgraBytes = m_reservedBytes + m_encodingBytes + (m_pending ? m_pending->bytes : 0);
        m_status.highWaterBgraBytes = (std::max)(m_status.highWaterBgraBytes,m_status.ownedBgraBytes);
    }
    bool releaseFile() {
        if (!m_file) return true;
        const bool released = m_file->cleanup();
        std::lock_guard lock(m_mutex);
        m_fileCleanupPending = !released;
        if (!released) { m_status.error = "file-cleanup-pending"; return false; }
        m_file.reset(); m_fileRoot.reset(); return true;
    }
    bool save(Frame& frame, std::wstring& receipt) {
        if (m_beforeEncode) m_beforeEncode();
        Microsoft::WRL::ComPtr<CaptureJpegStream> jpeg;
        if (!encodeCaptureJpeg(frame.width,frame.height,frame.pixels.get(),frame.bytes,frame.quality,
            [this,&frame] { return current(frame); },jpeg) || !current(frame)) return false;
        {
            std::lock_guard lock(m_mutex);
            if (!current(frame) || !budgetAllowsLocked(jpeg->size())) return false;
        }
        const auto destination = frame.root->images() + L"\\vaporlock_" + std::to_wstring(frame.capturedAtMs)
            + L"_" + std::to_wstring(frame.identity.session) + L"_" + std::to_wstring(GetCurrentProcessId())
            + L"_" + std::to_wstring(++m_sequence) + L".jpg";
        auto displayReceipt = frame.root->displayImages() + destination.substr(frame.root->images().size());
        m_file = std::make_unique<CaptureFile>(); m_fileRoot = frame.root;
        if (!m_file->create(destination + L".pending")) return false;
        for (std::size_t offset = 0; offset < jpeg->size();) {
            if (!current(frame)) return false;
            DWORD written = 0;
            const auto count = static_cast<DWORD>((std::min)(jpeg->size()-offset,std::size_t{1024*1024}));
            if (!WriteFile(m_file->writer,jpeg->data()+offset,count,&written,nullptr) || written==0 || written>count) return false;
            offset += written;
        }
        if (!current(frame) || !FlushFileBuffers(m_file->writer) || !m_file->closeWriter()) return false;
        std::unique_lock lock(m_mutex);
        while (current(frame) && m_lastCommit != Clock::time_point{}) {
            const auto due = m_lastCommit + std::chrono::milliseconds((std::max)(frame.interval,m_interval));
            if (Clock::now() >= due) break;
            m_wake.wait_until(lock,due,[this,&frame] { return !current(frame); });
        }
        if (!current(frame) || !m_allowed || frame.identity.configRevision != m_revision
            || frame.identity.session != m_source.session || frame.identity.sourceGeneration != m_source.sourceGeneration
            || m_prepareRoot || !frame.root->ready()) return false;
        // Retirement REQUEST during IO can be pending, never ACKed until this
        // same guard releases. Commit and completed retirement are ordered here.
        if (!budgetAllowsLocked(jpeg->size()) || (m_authorize && !m_authorize()) || !m_file->rename(destination)) return false;
        m_lastCommit = Clock::now(); m_status.lastCommit = m_lastCommit;
        ++m_status.saved; // actual final rename only
        m_status.savedBytes += jpeg->size();
        budgetAllowsLocked(0); // expose exact count/byte limit immediately
        receipt = std::move(displayReceipt);
        return true;
    }
    void run() {
        SetThreadPriority(GetCurrentThread(),THREAD_PRIORITY_BELOW_NORMAL);
        const auto com = CoInitializeEx(nullptr,COINIT_MULTITHREADED);
        for (;;) {
            if (!releaseFile()) { std::this_thread::sleep_for(std::chrono::milliseconds(100)); continue; }
            std::optional<Frame> frame;
            std::wstring directory;
            std::shared_ptr<CaptureRoot> retiringRoot;
            bool prepare = false, rootAction = false;
            std::uint64_t rootEpoch = 0;
            {
                std::unique_lock lock(m_mutex);
                if (m_stopping.load() || m_appliedEpoch != m_epoch.load()) discardPendingLocked();
                if (m_stopping.load() || m_prepareRoot || m_rootCleanupPending) {
                    rootAction = true;
                    retiringRoot = std::move(m_root); m_status.rootReady = false;
                    prepare = !m_stopping.load() && m_rootDesired && m_appliedEpoch == m_epoch.load();
                    directory = m_directory; rootEpoch = m_appliedEpoch;
                } else if (m_pending) {
                    frame.swap(m_pending); m_encodingBytes = frame->bytes;
                    m_status.pending = false; m_status.encoding = true; updateBytesLocked();
                } else {
                    m_wake.wait_for(lock,std::chrono::milliseconds(100),
                        [this] { return m_stopping.load() || m_prepareRoot || m_pending.has_value(); });
                    continue;
                }
            }
            if (retiringRoot && !retiringRoot->close()) {
                { std::lock_guard lock(m_mutex); m_root = std::move(retiringRoot);
                  m_rootCleanupPending = true; m_status.error = "root-cleanup-pending"; }
                std::this_thread::sleep_for(std::chrono::milliseconds(100)); continue;
            }
            retiringRoot.reset();
            if (m_stopping.load()) {
                std::lock_guard lock(m_mutex);
                if (m_reservedBytes) continue;
                m_rootCleanupPending = false; m_complete = true; break;
            }
            if (rootAction && !prepare) {
                std::lock_guard lock(m_mutex); m_prepareRoot = false; m_rootCleanupPending = false;
                continue;
            }
            if (prepare) {
                if (m_beforePrepare) m_beforePrepare();
                auto root = CaptureRoot::prepare(directory);
                std::lock_guard lock(m_mutex);
                // A blocking prepare cannot overwrite a newer disable, Stop,
                // root/config change or retirement request. Keep the acquired
                // handles owned but unavailable, then close on the next pass.
                const bool requestCurrent = !m_stopping.load() && m_rootDesired && directory == m_directory
                    && rootEpoch == m_appliedEpoch && rootEpoch == m_epoch.load();
                m_root = std::move(root); m_status.rootReady = requestCurrent && m_root->ready();
                m_rootCleanupPending = !m_root->ready() && m_root->hasHandles();
                m_prepareRoot = !requestCurrent;
                m_status.error = !requestCurrent || m_root->ready() ? "none" : "root-unavailable";
                continue;
            }
            bool saved = false;
            std::wstring receipt;
            try { if (frame && current(*frame)) saved = save(*frame, receipt); } catch (...) { saved = false; }
            const bool fileReleased = releaseFile();
            frame.reset(); // release the one encoding copy before another frame
            std::uint64_t count = 0;
            {
                std::lock_guard lock(m_mutex); m_encodingBytes = 0; m_status.encoding = false; updateBytesLocked();
                if (!saved) { ++m_status.dropped; if (fileReleased) m_status.error = m_limitBlocked ? "none" : "sample-not-committed"; }
                else if (fileReleased) m_status.error = "none";
                count = m_status.saved;
            }
            if (saved && m_saved) { try { m_saved(count); } catch (...) {} }
            if (saved && m_receipt) { try { m_receipt(count, receipt); } catch (...) {} }
            bool limited = false;
            { std::lock_guard lock(m_mutex); limited = m_limitBlocked; }
            if (!saved && !limited && m_notice) { try { m_notice("Collection sample was not committed. Tracking continues; review collection status."); } catch (...) {} }
        }
        if (SUCCEEDED(com)) CoUninitialize();
    }
    Saved m_saved; Notice m_notice; BeforeEncode m_beforeEncode;
    Receipt m_receipt;
    BeforePrepare m_beforePrepare;
    mutable std::mutex m_mutex;
    std::condition_variable m_wake;
    std::thread m_thread;
    std::atomic<bool> m_stopping{false},m_complete{false},m_workerFailed{false};
    std::atomic<std::uint64_t> m_epoch{1};
    std::uint64_t m_appliedEpoch = 1,m_revision = 0,m_sequence = 0;
    bool m_initialized = false,m_allowed = false,m_gate = false,m_prepareRoot = false,m_rootDesired = false;
    bool m_fileCleanupPending = false,m_rootCleanupPending = false;
    double m_cutoff = .5;
    int m_interval = 1000,m_quality = 92;
    int m_maxImages = 0,m_maxMiB = 0;
    bool m_limitBlocked = false;
    std::wstring m_directory;
    CaptureFrameIdentity m_source;
    std::optional<Frame> m_pending;
    std::size_t m_reservedBytes = 0,m_encodingBytes = 0;
    std::shared_ptr<CaptureRoot> m_root,m_fileRoot;
    std::unique_ptr<CaptureFile> m_file;
    Clock::time_point m_lastAdmission{},m_lastCommit{};
    Status m_status;
};
