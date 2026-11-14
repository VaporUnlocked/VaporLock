// Included after DesktopCapture. The D3D immediate context and duplication
// interface stay on this thread. Only completed CPU snapshots cross the handoff.
template<class Capture>
class DesktopCaptureWorkerFor {
public:
    struct Request {
        int monitor;
        CaptureRegion region;
        int fps;
        std::uint64_t revision;
    };
    struct Progress {
        std::uint64_t noFreshPolls = 0, retiredAttempts = 0, dxgiTimeouts = 0;
        double capturePacingWaitMs = 0.0, capturePacingOvershootMs = 0.0;
        bool highResolutionPacing = false;
    };
    enum class Result { Pending, Frame, Failed, RateViolation };
    using Cancelled = std::function<bool(std::uint64_t)>;
    using SourceChanged = std::function<void(CaptureFrameIdentity)>;

    explicit DesktopCaptureWorkerFor(Cancelled cancelled, SourceChanged sourceChanged = {})
        : m_cancelled(std::move(cancelled)), m_sourceChanged(std::move(sourceChanged)),
          m_thread([this] { run(); }) {}
    ~DesktopCaptureWorkerFor() { stop(); }
    DesktopCaptureWorkerFor(const DesktopCaptureWorkerFor&) = delete;
    DesktopCaptureWorkerFor& operator=(const DesktopCaptureWorkerFor&) = delete;

    // Repeated requests for the same outstanding revision are harmless. A
    // settings change replaces queued work and retires any old in-flight result.
    void submit(Request request) {
        std::lock_guard lock(m_mutex);
        if (m_stopping || (m_request && m_request->revision == request.revision)) return;
        m_request = request;
        m_result = Result::Pending;
        ++m_serial;
        m_wake.notify_all();
    }

    // Pending leaves the caller's previously accepted pixels untouched.
    Result take(typename Capture::Frame& frame, std::uint64_t revision, std::chrono::milliseconds wait,
                Progress* progress = nullptr) {
        std::unique_lock lock(m_mutex);
        const auto current = [&] { return m_request && m_request->revision == revision; };
        m_wake.wait_for(lock, wait, [&] {
            return m_stopping || !current() || m_result != Result::Pending;
        });
        if (progress) *progress = m_progress;
        if (m_stopping || !current() || m_result == Result::Pending) return Result::Pending;
        const auto result = m_result;
        if (result == Result::Frame) std::swap(frame, m_frame);
        m_request.reset();
        m_result = Result::Pending;
        return result;
    }

    // Hold across the short tracking/output commit. A newer frame from the
    // same source is fine; a recovered source invalidates the in-flight result.
    template<class Action>
    bool withCurrentSource(CaptureFrameIdentity identity, Action action) {
        std::lock_guard lock(m_mutex);
        if (m_stopping || (m_request && m_request->revision == identity.configRevision
                && (m_result == Result::Failed || m_result == Result::RateViolation))
                || m_latestSource.session != identity.session
                || m_latestSource.sourceGeneration != identity.sourceGeneration
                || m_latestSource.configRevision != identity.configRevision) return false;
        action();
        return true;
    }

    void stop() {
        {
            std::lock_guard lock(m_mutex);
            m_stopping = true;
            m_wake.notify_all();
        }
        if (m_thread.joinable()) m_thread.join();
    }

private:
    void run() {
        struct ComApartment {
            HRESULT result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
            ~ComApartment() { if (SUCCEEDED(result)) CoUninitialize(); }
        } apartment;
        Capture capture;
        AppConfig config;
        using Schedule = vaporlock::pipeline::FreshFrameSchedule;
        Schedule schedule(Schedule::Pacing::KeepPhase);
        vaporlock::pipeline::DeadlineWait wait;
        AimTime lastFresh = AimClock::now();
        std::uint64_t handled = 0;
        bool desktopPaced = false;
        for (;;) {
            Request request{};
            {
                std::unique_lock lock(m_mutex);
                m_wake.wait(lock, [&] { return m_stopping || (m_request && m_serial != handled); });
                if (m_stopping) return;
                request = *m_request;
                handled = m_serial;
            }
            const auto cancelled = [&] {
                return m_stopping || m_serial != handled || (m_cancelled && m_cancelled(request.revision));
            };
            const bool paced = request.monitor > 0;
            if (desktopPaced != paced) {
                schedule.reset();
                lastFresh = AimClock::now();
                desktopPaced = paced;
            }
            double pacingWaitMs = 0.0, pacingOvershootMs = 0.0;
            if (paced && schedule.hasFresh()) {
                const auto deadline = schedule.next(request.fps);
                const auto before = AimClock::now();
                if (!wait.until(deadline, nullptr, cancelled)) continue;
                if (before < deadline) {
                    const auto woke = AimClock::now();
                    pacingWaitMs = std::chrono::duration<double, std::milli>(woke - before).count();
                    pacingOvershootMs = std::chrono::duration<double, std::milli>(woke - deadline).count();
                }
            }
            config.captureMonitor = request.monitor;
            config.captureRegion = request.region;
            while (!cancelled()) {
                const auto started = AimClock::now();
                const bool captured = capture.capture(config, 0, cancelled);
                const auto after = AimClock::now();
                const bool fresh = capture.freshFrame();
                const bool inBudget = !paced || !fresh || schedule.commit(capture.acquiredAt(), request.fps);
                if (fresh) lastFresh = capture.observedAt();
                const bool retired = cancelled();
                {
                    std::unique_lock lock(m_mutex);
                    m_progress.dxgiTimeouts = capture.dxgiTimeouts();
                    m_progress.highResolutionPacing = wait.highResolution();
                    if (retired || m_stopping || m_serial != handled) {
                        ++m_progress.retiredAttempts;
                        break;
                    }
                    if (!captured || !inBudget || fresh) {
                        m_progress.capturePacingWaitMs = pacingWaitMs;
                        m_progress.capturePacingOvershootMs = pacingOvershootMs;
                        if (!captured) m_result = Result::Failed;
                        else if (!inBudget) m_result = Result::RateViolation;
                        else {
                            capture.takeFrame(m_frame, request.revision);
                            const bool changed = m_latestSource.session != m_frame.identity.session
                                || m_latestSource.sourceGeneration != m_frame.identity.sourceGeneration;
                            m_latestSource = m_frame.identity;
                            // Retire old output immediately. Never call into
                            // config or output while holding the mailbox lock.
                            if (changed && m_sourceChanged) {
                                const auto identity = m_latestSource;
                                lock.unlock();
                                m_sourceChanged(identity);
                                lock.lock();
                                if (m_stopping || m_serial != handled) break;
                            }
                            m_result = Result::Frame;
                        }
                        m_wake.notify_all();
                        break;
                    }
                    ++m_progress.noFreshPolls;
                }
                wait.until(started + vaporlock::pipeline::desktopPollDelay(after - lastFresh), nullptr, cancelled);
            }
        }
    }

    Cancelled m_cancelled;
    SourceChanged m_sourceChanged;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::optional<Request> m_request;
    std::atomic<bool> m_stopping{false};
    std::atomic<std::uint64_t> m_serial{0};
    Result m_result = Result::Pending;
    typename Capture::Frame m_frame;
    CaptureFrameIdentity m_latestSource{};
    Progress m_progress;
    std::thread m_thread;
};

using DesktopCaptureWorker = DesktopCaptureWorkerFor<DesktopCapture>;
