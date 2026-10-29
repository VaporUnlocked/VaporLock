// Included after the engine's frame-rendering and configuration types.
class PreviewWorker {
public:
    struct Frame {
        int width = 0, height = 0;
        std::vector<unsigned char> pixels;
        std::vector<Track> tracks;
        std::optional<Track> selected;
        AppConfig config;
        std::uint64_t revision = 0;
        CaptureFrameIdentity identity{};
        AimTime observedAt{};
    };
    using Sink = std::function<void(std::string)>;
    using Encoder = std::function<std::string(Frame)>;
    using RevisionSink = std::function<void(std::string, std::uint64_t)>;

    PreviewWorker(Sink sink, Sink failure, Encoder encoder = {}, RevisionSink revisionSink = {})
        : m_sink(std::move(sink)), m_failure(std::move(failure)), m_encoder(std::move(encoder)),
          m_revisionSink(std::move(revisionSink)) {
        m_thread = std::thread([this] { run(); });
    }
    ~PreviewWorker() { stop(); }
    PreviewWorker(const PreviewWorker&) = delete;
    PreviewWorker& operator=(const PreviewWorker&) = delete;

    void configure(bool enabled) {
        std::optional<Frame> discarded;
        std::lock_guard lock(m_mutex);
        if (enabled == m_enabled) return;
        m_enabled = enabled;
        ++m_generation;
        m_reportedFailure = false;
        discarded.swap(m_pending);
    }

    bool publish(Frame frame) {
        std::optional<Frame> previous(std::move(frame));
        {
            std::lock_guard lock(m_mutex);
            if (!m_enabled || m_stopping) return false;
            m_pending.swap(previous);
        }
        m_wake.notify_one();
        return true;
    }

    void stop() {
        {
            std::lock_guard lock(m_mutex);
            m_stopping = true;
            m_pending.reset();
        }
        m_wake.notify_one();
        if (m_thread.joinable()) m_thread.join();
    }

    static std::string encode(Frame frame) {
        std::vector<unsigned char> jpeg;
        const auto pixels = visualizedFrame(frame.width, frame.height, std::move(frame.pixels), frame.tracks, frame.selected, frame.config);
        if (!encodeJpeg(frame.width, frame.height, pixels, frame.config.previewQuality, jpeg))
            throw std::runtime_error("Live preview could not be encoded. Capture and inference are still running.");
        return "{\"type\":\"preview\",\"width\":" + std::to_string(frame.width)
            + ",\"height\":" + std::to_string(frame.height)
            + ",\"schema\":2,\"session\":" + std::to_string(frame.identity.session)
            + ",\"source_generation\":" + std::to_string(frame.identity.sourceGeneration)
            + ",\"frame_id\":" + std::to_string(frame.identity.frameId)
            + ",\"config_revision\":" + std::to_string(frame.identity.configRevision)
            + ",\"control_epoch\":" + std::to_string(frame.config.visualEpoch)
            + ",\"emitted_tick_ms\":" + std::to_string(GetTickCount64())
            + ",\"observation_age_ms\":" + std::to_string(std::chrono::duration<double,std::milli>(AimClock::now()-frame.observedAt).count())
            + ",\"reference_inside\":" + (frame.config.frameReference.inside ? "true" : "false")
            + ",\"crosshair_detected\":" + (frame.config.frameReference.detected ? "true" : "false")
            + ",\"crosshair_age_ms\":" + std::to_string(frame.config.frameReference.detected ? std::chrono::duration<double,std::milli>(AimClock::now()-frame.config.frameReference.matched).count() : 0.)
            + ",\"jpeg\":\"" + base64Encode(jpeg) + "\"}";
    }

private:
    void run() {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
        const auto com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        for (;;) {
            std::optional<Frame> frame;
            std::uint64_t generation;
            {
                std::unique_lock lock(m_mutex);
                m_wake.wait(lock, [this] { return m_stopping || m_pending.has_value(); });
                if (m_stopping) break;
                generation = m_generation;
                frame.swap(m_pending);
            }
            try {
                const auto revision = frame->revision;
                auto line = m_encoder ? m_encoder(std::move(*frame)) : encode(std::move(*frame));
                std::lock_guard lock(m_mutex);
                if (!m_stopping && m_enabled && generation == m_generation) {
                    if (m_revisionSink) m_revisionSink(std::move(line), revision);
                    else m_sink(std::move(line));
                }
            } catch (const std::exception& error) {
                std::lock_guard lock(m_mutex);
                if (!m_stopping && m_enabled && generation == m_generation && !m_reportedFailure) {
                    m_reportedFailure = true;
                    m_failure(error.what());
                }
            }
        }
        if (SUCCEEDED(com)) CoUninitialize();
    }

    Sink m_sink, m_failure;
    Encoder m_encoder;
    RevisionSink m_revisionSink;
    std::optional<Frame> m_pending;
    std::uint64_t m_generation = 0;
    bool m_enabled = false, m_stopping = false, m_reportedFailure = false;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::thread m_thread;
};
