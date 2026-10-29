// Included by the native engine after its capture, tracking and transport types.
// The injected sampler and sink keep deterministic tests entirely off the desktop.
struct AimInputs {
    bool valid = true;
    bool down = false;
    bool compensationDown = false;
    ControllerSnapshot controller{};
};

struct AimReport {
    LONG dx = 0, dy = 0;
    SHORT stickX = 0, stickY = 0;
    bool active = false;
};

struct AimOutputStatus {
    bool authorized = false;
    bool active = false;
    bool compensationActive = false;
    bool insideSnap = false;
    bool eventHolding = false;
    bool eventMonitor = true;
    bool controllerReady = false;
    ControllerSnapshot controller{};
    std::uint64_t sent = 0, failed = 0, eventSent = 0, eventFailed = 0;
    std::size_t acceptedMotionEntries = 0;
    bool motionHistoryComplete = true;
    double observationAgeMs = -1.0;
    std::string reason = "idle";
    std::string eventReason = "disabled";
    std::wstring fatalError;
};

class AimOutputScheduler {
public:
    using Sampler = std::function<AimInputs(const AppConfig&)>;
    using Sink = std::function<bool(const AimReport&, const AimInputs&, const AppConfig&, AimOutputStatus&)>;
    using Events = std::function<void(const AppConfig&, bool, bool, AimTime, AimOutputStatus&)>;
    using CollectionObserver = std::function<void(const AppConfig&, std::uint64_t, bool, bool)>;

    explicit AimOutputScheduler(Sink sink, Sampler sampler = {}, Events events = {}, CollectionObserver collection = {})
        : m_sink(std::move(sink)), m_sampler(std::move(sampler)), m_events(std::move(events)), m_collection(std::move(collection)) {}
    ~AimOutputScheduler() { stop(); if (m_stopEvent) CloseHandle(m_stopEvent); }
    AimOutputScheduler(const AimOutputScheduler&) = delete;
    AimOutputScheduler& operator=(const AimOutputScheduler&) = delete;

    std::uint64_t configure(const AppConfig& config) {
        std::lock_guard lock(m_mutex);
        cancelLocked();
        m_controller.resetTrigger();
        m_config = config;
        m_config.frameReference = {};
        applySessionSafety(m_config);
        m_toggle = false;
        // Changing bindings while held must not accidentally arm toggle mode.
        m_inputInitialized = false;
        observeCollectionLocked();
        return m_generation;
    }

    std::uint64_t generation() const { std::lock_guard lock(m_mutex); return m_generation; }
    AimOutputStatus status() const { std::lock_guard lock(m_mutex); return m_status; }

    TargetingVisualSnapshot visualSnapshot(CaptureFrameIdentity identity, AimTime now = AimClock::now()) const {
        std::lock_guard lock(m_mutex);
        TargetingVisualSnapshot view{identity, now};
        if (m_stopped || identity != m_identity || identity.configRevision != m_generation
                || !m_status.fatalError.empty() || !targetEligibleForOutput(m_target, m_config, now,m_width,m_height)
                || (m_historyFloor != AimTime{} && m_target->lastUpdate <= m_historyFloor)) return view;
        view.trackId = m_target->id;
        auto [x, y] = predictedTargetAnchor(*m_target, m_config, now);
        if (pointExcluded(x,y,m_config,m_width,m_height)) return view;
        for (const auto& step : m_history) if (step.when >= m_target->lastUpdate) {
            x -= step.x; y -= step.y;
        }
        if (pointExcluded(x,y,m_config,m_width,m_height)) return view;
        view.eligible = std::isfinite(x) && std::isfinite(y);
        view.x = view.eligible ? x : 0; view.y = view.eligible ? y : 0;
        return view;
    }

    void publish(const std::optional<Track>& target, int width, int height, AimTime observedAt,
                 std::uint64_t generation, std::pair<double, double> automatic = {}, CaptureFrameIdentity identity = {}, vaporlock::crosshair::Reference reference = {}) {
        std::lock_guard lock(m_mutex);
        if (generation != m_generation || m_stopped || observedAt < m_lastPublished) return;
        if (m_identity.session && identity.session && (identity.session != m_identity.session
                || identity.sourceGeneration < m_identity.sourceGeneration
                || identity.frameId < m_identity.frameId)) return;
        // Neither a duplicate frame nor a retained prediction renews the lease.
        if (observedAt == m_lastPublished) return;
        // Capture geometry changes define a new coordinate system. Never apply
        // displacement measured in the old pixels to the new image.
        if ((m_width > 0 && m_height > 0 && (m_width != width || m_height != height))
                || (m_identity.session && identity.session &&
                    (m_identity.session != identity.session || m_identity.sourceGeneration != identity.sourceGeneration)))
            resetResponseLocked();
        if(m_config.frameReference.detected!=reference.detected||m_config.frameReference.inside!=reference.inside
            || std::hypot(m_config.frameReference.x-reference.x,m_config.frameReference.y-reference.y)>.25)
            resetTrackingFilterLocked(); // Keep accepted physical displacement history.
        m_config.frameReference=reference;
        m_lastPublished = observedAt;
        m_identity = identity;
        m_width = width; m_height = height;
        m_automatic = automatic;
        if (!target || target->missed || !target->identityCertain) {
            m_target.reset(); resetTrackingFilterLocked(); return;
        }
        // Changing subjects resets response lag, not movement already applied
        // to the entire view after this image was captured.
        if (!m_target || m_target->id != target->id) resetTrackingFilterLocked();
        // Do not copy prototype masks into the high-frequency movement thread.
        Track light;
        light.id = target->id; light.age = target->age; light.missed = target->missed;
        light.detection.box = target->detection.box;
        light.detection.classId = target->detection.classId;
        light.detection.confidence = target->detection.confidence;
        light.vx = target->vx; light.vy = target->vy; light.ax = target->ax; light.ay = target->ay;
        light.stableAnchorX = target->stableAnchorX; light.stableAnchorY = target->stableAnchorY;
        light.hasStableAnchor = target->hasStableAnchor;
        light.hasObservation = target->hasObservation; light.identityCertain = target->identityCertain;
        light.lastUpdate = target->lastUpdate; light.motionConfidence = target->motionConfidence;
        light.observedBox = target->observedBox;
        m_target = std::move(light);
    }

    void clear() { std::lock_guard lock(m_mutex); cancelLocked(); }

    // A changed/lost reference invalidates the old selection before inference.
    // Do not run old target decisions against a new centre while the model runs.
    void observeReference(const vaporlock::crosshair::Reference& reference,
                          CaptureFrameIdentity identity, std::uint64_t generation) {
        std::lock_guard lock(m_mutex);
        if(m_stopped||generation!=m_generation||identity.configRevision!=generation
            ||(m_identity.session && (identity.session!=m_identity.session
                ||identity.sourceGeneration<m_identity.sourceGeneration||identity.frameId<m_identity.frameId)))return;
        const auto& old=m_config.frameReference;
        if(old.resolved==reference.resolved&&old.detected==reference.detected&&old.inside==reference.inside
            &&std::hypot(old.x-reference.x,old.y-reference.y)<=.25)return;
        m_target.reset();resetTrackingFilterLocked();m_status.insideSnap=false;
        if(m_events)m_events(m_config,false,false,AimClock::now(),m_status);
    }

    // Retire a capture source before its replacement inference starts. This is
    // not a configuration change: keep the LiveConfig/scheduler revision aligned.
    void observeSource(CaptureFrameIdentity identity, std::uint64_t generation) {
        std::lock_guard lock(m_mutex);
        if (m_stopped || generation != m_generation || identity.configRevision != generation) return;
        if (m_identity.session == identity.session && m_identity.sourceGeneration == identity.sourceGeneration) return;
        if (m_identity.session && (m_identity.session != identity.session
                || identity.sourceGeneration < m_identity.sourceGeneration)) return;
        m_target.reset(); m_lastPublished = {}; m_identity = identity;
        resetResponseLocked(); resetCompensationLocked(); m_automatic = {};
        m_status.active = m_status.compensationActive = false;
        m_status.observationAgeMs = -1; m_status.reason = "capture-source-changed";
        if (m_events) m_events(m_config, false, false, AimClock::now(), m_status);
        if (m_lastActive) submitLocked({}, m_lastInputs);
        m_lastActive = false;
        observeCollectionLocked();
    }

    void start() {
        std::lock_guard lock(m_mutex);
        if (m_thread.joinable()) return;
        m_stopped = false;
        observeCollectionLocked();
        if (m_stopEvent) ResetEvent(m_stopEvent);
        m_thread = std::thread([this] {
            const HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
            for (;;) {
                {
                    std::unique_lock lock(m_mutex);
                    if (m_stopped) break;
                }
                pollOnce(AimClock::now(), true);
                std::unique_lock lock(m_mutex);
                // Wait to the existing output deadline, not a fresh period
                // after polling/sink work. Activation and events still sample
                // at least every 2 ms (subject to OS scheduling), even at low
                // output rates. Never spin or replay missed output ticks.
                double waitSeconds = 0.002;
                if (m_lastActive && m_lastTick != AimTime{}) {
                    const double untilOutput = 1.0 / std::max(1, m_config.outputRate)
                        - std::chrono::duration<double>(AimClock::now() - m_lastTick).count();
                    waitSeconds = std::clamp(untilOutput, 0.0, waitSeconds);
                }
                const auto period = std::chrono::duration<double>(waitSeconds);
                LARGE_INTEGER due{};
                due.QuadPart = -std::max<LONGLONG>(1, static_cast<LONGLONG>(std::ceil(period.count() * 10000000)));
                if (timer && m_stopEvent && SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE)) {
                    const HANDLE handles[]{m_stopEvent, timer};
                    lock.unlock();
                    WaitForMultipleObjects(2, handles, FALSE, INFINITE);
                    lock.lock();
                } else m_wake.wait_for(lock, period, [this] { return m_stopped; });
                if (m_stopped) break;
            }
            if (timer) CloseHandle(timer);
        });
    }

    void stop() {
        {
            std::lock_guard lock(m_mutex);
            if (!m_stopped) cancelLocked();
            m_stopped = true;
            observeCollectionLocked();
        }
        m_wake.notify_all();
        if (m_stopEvent) SetEvent(m_stopEvent);
        if (m_thread.joinable()) m_thread.join();
    }

    // Test seam: the hook runs unlocked immediately before final validation.
    // Cancellation can finish here; generation validation then rejects the work.
    std::function<void()> beforeSubmission;
    void pollOnce(AimTime now, bool realtime = false) {
        std::uint64_t generation;
        {
            std::lock_guard lock(m_mutex);
            if (m_stopped || !m_status.fatalError.empty()) { observeCollectionLocked(); return; }
            generation = m_generation;
        }
        if (beforeSubmission) beforeSubmission();
        std::lock_guard lock(m_mutex);
        if (m_stopped || generation != m_generation) return;
        struct CollectionOnReturn {
            AimOutputScheduler& owner;
            ~CollectionOnReturn() { owner.observeCollectionLocked(); }
        } collectionOnReturn{*this};
        // Sampling, authorization, calculation and submission share cancellation's
        // lock. clear()/stop() cannot complete then leak an old local command.
        const AimInputs input = m_sampler ? m_sampler(m_config) : sampleInputLocked();
        if (realtime) now = AimClock::now();
        pruneHistoryLocked(now);
        m_lastInputs = input;
        m_status.controller = input.controller;
        if (!m_inputInitialized) {
            m_wasDown = input.down;
            m_compensationWasDown = input.compensationDown;
            m_inputInitialized = true;
        }
        if (!input.valid || !m_config.movementEnabled || m_config.observeOnly) m_toggle = false;
        else if (m_config.activationMode == 1 && input.down && !m_wasDown) m_toggle = !m_toggle;
        m_wasDown = input.down;
        const bool click = input.compensationDown && !m_compensationWasDown;
        m_compensationWasDown = input.compensationDown;
        m_status.authorized = input.valid && !m_config.observeOnly
            && (m_config.activationMode == 0 ? input.down : m_toggle);
        // If the bounded history discarded commands relevant to an unusually
        // old frame, abstain until a fully covered fresh observation arrives.
        const bool historyCovered = !m_target || m_historyFloor == AimTime{}
            || m_target->lastUpdate > m_historyFloor;
        m_status.motionHistoryComplete = historyCovered;
        m_status.observationAgeMs = m_target
            ? std::chrono::duration<double, std::milli>(now - m_target->lastUpdate).count() : -1.0;
        const bool checkZones = !m_config.exclusionZones.empty();
        const auto predicted = checkZones && m_target ? predictedTargetAnchor(*m_target,m_config,now) : std::pair<double,double>{};
        double correctedX = 0, correctedY = 0;
        if (checkZones && m_target) for (const auto& step : m_history) if (step.when >= m_target->lastUpdate) {
            correctedX += step.x; correctedY += step.y;
        }
        const bool eligible = historyCovered && targetEligibleForOutput(m_target, m_config, now,m_width,m_height)
            && !pointExcluded(predicted.first,predicted.second,m_config,m_width,m_height)
            && !pointExcluded(predicted.first-correctedX,predicted.second-correctedY,m_config,m_width,m_height);
        const bool leaseFresh = m_lastPublished != AimTime{} && now >= m_lastPublished
            && now - m_lastPublished <= std::chrono::milliseconds(m_config.outputMaxObservationAgeMs);
        const bool tracking = m_config.movementEnabled && m_status.authorized && eligible;
        const bool automaticReady = leaseFresh && (eligible || m_config.motionCompensationMode != 0);
        m_status.compensationActive = input.valid && !m_config.observeOnly && m_config.motionCompensationEnabled
            && (m_config.motionCompensationActivationMode == 0 ? input.compensationDown
                : m_config.motionCompensationActivationMode == 1 ? click
                : m_config.motionCompensationActivationMode == 2 ? true : automaticReady);
        if (!tracking) resetTrackingFilterLocked();
        if (!m_status.compensationActive || (m_config.motionCompensationMode == 1 && !leaseFresh))
            resetCompensationLocked();
        m_status.insideSnap = eligible && anchorDistanceFromFrameCenter(
            targetAnchor(*m_target, m_config).first, targetAnchor(*m_target, m_config).second,
            m_width, m_height, m_config) <= std::max(0, m_config.snapRadius);
        if (m_events) m_events(m_config, input.valid && !m_config.observeOnly
            && (!m_config.eventActionRequiresActivation || m_status.authorized), m_status.insideSnap, now, m_status);
        // Fatal events can occur without a nonzero report (for example a closed
        // control-center pipe while centered). Retire authority and scheduling
        // immediately rather than waiting for a transport submission to fail.
        if (!m_status.fatalError.empty()) { cancelLocked(); return; }
        m_status.active = tracking || m_status.compensationActive;
        m_status.reason = m_config.observeOnly ? "observe-only" : !input.valid ? "input-unavailable"
            : !m_config.movementEnabled ? "movement-disabled" : !m_status.authorized ? "activation-not-authorized"
            : !m_target ? "no-target" : !historyCovered ? "movement-history-incomplete"
            : !eligible ? "target-stale-missing-or-uncertain" : "rate-limited";
        if (!m_status.active) {
            // Reset fractions and filter lag on release, not just integer output.
            // Absence/release ends authority and discards fractional/filter
            // state. Accepted view movement still belongs to later old images.
            resetTrackingFilterLocked();
            resetCompensationLocked();
            m_lastTick = now;
            if (m_lastActive || m_config.outputTransport == vaporlock::hardware::kVirtualXboxTransport)
                submitLocked({}, input);
            m_lastActive = false;
            return;
        }
        const double elapsed = m_lastTick == AimTime{} ? 1.0 / std::max(1, m_config.outputRate)
            : std::chrono::duration<double>(now - m_lastTick).count();
        const bool immediateCompensationClick = m_status.compensationActive && m_config.motionCompensationActivationMode == 1;
        if (!immediateCompensationClick && elapsed + 1e-9 < 1.0 / std::max(1, m_config.outputRate)) return;
        // A delayed transport/thread must not catch up with a large burst.
        const double dt = std::clamp(elapsed, 0.0, 0.05);
        m_lastTick = now;
        m_lastActive = true;
        double x = 0.0, y = 0.0;
        if (tracking) {
            auto [anchorX, anchorY] = checkZones ? predicted : predictedTargetAnchor(*m_target, m_config, now);
            const auto [refX, refY] = aimingReference(m_width, m_height, m_config);
            if (!checkZones) for (const auto& step : m_history) if (step.when >= m_target->lastUpdate) {
                correctedX += step.x; correctedY += step.y;
            }
            // Output between observations consumes the measured error budget.
            // It cannot replay one correction indefinitely during slow inference.
            double errorX = anchorX - refX - correctedX;
            double errorY = anchorY - refY - correctedY;
            const auto actual = rawTargetAnchor(*m_target, m_config, true);
            const double actualX = actual.first - refX - correctedX, actualY = actual.second - refY - correctedY;
            if (std::hypot(actualX, actualY) <= std::max(static_cast<double>(m_config.deadzone), m_config.anchorSettleRadius)) {
                m_filteredX = m_filteredY = m_remainderX = m_remainderY = 0;
                m_status.reason = "inside-deadzone";
            } else {
                if (m_config.responseCurve == 1) {
                    const double scale = std::max(1.0, std::hypot(m_width * 0.5, m_height * 0.5));
                    errorX = std::copysign(errorX * errorX / scale, errorX);
                    errorY = std::copysign(errorY * errorY / scale, errorY);
                }
                const double retained = timeRetention(m_config.movementSmoothing, dt);
                m_filteredX = retained * m_filteredX + (1 - retained) * errorX;
                m_filteredY = retained * m_filteredY + (1 - retained) * errorY;
                auto bounded = [&](double filtered, double error, double multiplier) {
                    const double axisGain = m_config.movementGain * multiplier;
                    const double rate = -std::log1p(-std::clamp(axisGain, 0.0, 0.9999)) * kLegacyResponseHz
                        + std::max(0.0, axisGain - 1.0) * kLegacyResponseHz;
                    const double gain = m_config.outputTransport == vaporlock::hardware::kVirtualXboxTransport
                        ? rate * dt : -std::expm1(-rate * dt);
                    if (filtered * error <= 0) return 0.0;
                    return std::copysign(std::min(std::abs(filtered * gain), std::abs(error)), error);
                };
                x = bounded(m_filteredX, errorX, m_config.assistanceX) * m_config.inputUnitsPerPixelX;
                y = bounded(m_filteredY, errorY, m_config.assistanceY) * m_config.inputUnitsPerPixelY;
                if (!m_config.predictLost || m_target->motionConfidence <= 0) {
                    const auto measuredBound = [](double requested, double remaining) {
                        return requested * remaining <= 0 ? 0.0 : std::copysign(std::min(std::abs(requested), std::abs(remaining)), requested);
                    };
                    x = measuredBound(x, actualX * m_config.inputUnitsPerPixelX);
                    y = measuredBound(y, actualY * m_config.inputUnitsPerPixelY);
                }
            }
        }
        double compensationX = 0, compensationY = 0;
        if (m_status.compensationActive) {
            const double interval = m_config.motionCompensationActivationMode == 1 ? 1.0 : dt;
            const double strength = m_config.motionCompensationStrength * interval;
            if (m_config.motionCompensationMode != 1) {
                compensationX -= m_config.motionCompensationHorizontal * strength * m_config.inputUnitsPerPixelX;
                compensationY -= m_config.motionCompensationVertical * strength * m_config.inputUnitsPerPixelY;
            }
            if (m_config.motionCompensationMode != 0 && leaseFresh) {
                compensationX += m_automatic.first * strength * m_config.inputUnitsPerPixelX;
                compensationY += m_automatic.second * strength * m_config.inputUnitsPerPixelY;
            }
        }
        const double magnitude = std::hypot(x + compensationX, y + compensationY);
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(compensationX)
            || !std::isfinite(compensationY) || !std::isfinite(magnitude)) { cancelLocked(); return; }
        const double limit = std::max(0.0, m_config.maxSpeed) * dt;
        if (magnitude > limit && magnitude > 0) {
            const double scale = limit / magnitude;
            x *= scale; y *= scale;
            compensationX *= scale; compensationY *= scale;
        }
        AimReport report;
        report.active = true;
        if (m_config.outputTransport == vaporlock::hardware::kVirtualXboxTransport) {
            // Stick output is normalized velocity, not pixels or per-frame deltas.
            const double scale = 32767.0 * m_config.controllerOutputStrength / std::max(1e-6, dt * m_config.maxSpeed);
            double stickX = (x + compensationX) * scale * (m_config.invertX ? -1 : 1);
            double stickY = (y + compensationY) * scale * (m_config.invertY ? 1 : -1);
            if (m_config.controllerOutputDeadzone > 0) {
                const double radius = std::hypot(stickX, stickY);
                if (radius > 0) {
                    const double d = m_config.controllerOutputDeadzone;
                    const double compensated = 32767.0 * (d + (1.0-d) * std::min(radius / 32767.0, 1.0));
                    stickX = (stickX / radius) * compensated;
                    stickY = (stickY / radius) * compensated;
                }
            }
            report.stickX = static_cast<SHORT>(std::clamp(std::round(stickX), -32767.0, 32767.0));
            report.stickY = static_cast<SHORT>(std::clamp(std::round(stickY), -32767.0, 32767.0));
        } else {
            const double signX = m_config.invertX ? -1 : 1, signY = m_config.invertY ? -1 : 1;
            // Hardware adapters transmit signed 16-bit deltas. Quantize within
            // that bound so the accepted-history record matches the wire report.
            const int stepLimit = vaporlock::hardware::usesHardwareTransport(m_config.outputTransport)
                ? std::min(32767, m_config.maxStep) : m_config.maxStep;
            report.dx = quantizeBlendedStep(x * signX, compensationX * signX, m_remainderX, m_compensationRemainderX, stepLimit);
            report.dy = quantizeBlendedStep(y * signY, compensationY * signY, m_remainderY, m_compensationRemainderY, stepLimit);
        }
        if (report.dx || report.dy || m_config.outputTransport == vaporlock::hardware::kVirtualXboxTransport) {
            if (!submitLocked(report, input)) {
                resetTrackingFilterLocked();
                resetCompensationLocked();
                if (!m_status.fatalError.empty()) cancelLocked();
            } else {
                // A stick-to-pixels calibration is environment dependent; don't
                // pretend a normalized stick value is an observed displacement.
                if (m_config.outputTransport != vaporlock::hardware::kVirtualXboxTransport) {
                    m_history.push_back({now, report.dx * (m_config.invertX ? -1.0 : 1.0) / m_config.inputUnitsPerPixelX,
                                            report.dy * (m_config.invertY ? -1.0 : 1.0) / m_config.inputUnitsPerPixelY});
                    pruneHistoryLocked(now);
                }
            }
        }
    }

private:
    AimInputs sampleInputLocked() {
        AimInputs input;
        const HDESK desktop = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
        input.valid = desktop != nullptr && GetForegroundWindow() != nullptr;
        if (desktop) {
            wchar_t inputName[256]{}, threadName[256]{};
            DWORD needed = 0;
            input.valid = input.valid
                && GetUserObjectInformationW(desktop, UOI_NAME, inputName, sizeof(inputName), &needed)
                && GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()), UOI_NAME, threadName, sizeof(threadName), &needed)
                && wcscmp(inputName, threadName) == 0;
        }
        if (desktop) CloseDesktop(desktop);
        static constexpr std::array<int, 4> keys{VK_RBUTTON, VK_LSHIFT, VK_LCONTROL, VK_SPACE};
        const bool key = (GetAsyncKeyState(keys[std::clamp(m_config.activationKey, 0, 3)]) & 0x8000) != 0;
        const auto ignored = m_config.controllerHostVirtualSlot >= 0
            ? std::optional<DWORD>(m_config.controllerHostVirtualSlot) : std::nullopt;
        if (m_config.activationSource != 0 || m_config.outputTransport == vaporlock::hardware::kVirtualXboxTransport)
            input.controller = m_controller.sample(m_config.controllerSlot, m_config.controllerBinding, ignored,
                m_config.controllerTriggerPress, m_config.controllerTriggerRelease, m_config.activationSource);
        input.down = m_config.activationSource == 1 ? input.controller.bindingDown
            : m_config.activationSource == 2 ? key || input.controller.bindingDown : key;
        if (m_config.activationSource == 1 && !input.controller.connected) input.valid = false;
        if (!input.valid) m_controller.resetTrigger();
        input.compensationDown = (GetAsyncKeyState(headlessKeyFromName(m_config.motionCompensationActivationKey)) & 0x8000) != 0;
        return input;
    }
    bool submitLocked(const AimReport& report, const AimInputs& input) {
        if (!m_sink) return false;
        const bool ok = m_sink(report, input, m_config, m_status);
        if (report.dx || report.dy || report.stickX || report.stickY) {
            if (ok) { ++m_status.sent; m_status.reason = "output-accepted"; }
            else { ++m_status.failed; m_status.reason = "output-rejected"; }
        }
        return ok;
    }
    void resetTrackingFilterLocked() {
        m_filteredX = m_filteredY = m_remainderX = m_remainderY = 0;
    }
    void resetCompensationLocked() {
        m_compensationRemainderX = m_compensationRemainderY = 0;
    }
    void resetResponseLocked() {
        resetTrackingFilterLocked();
        m_history.clear();
        m_historyFloor = {};
        m_status.acceptedMotionEntries = 0;
        m_status.motionHistoryComplete = true;
    }
    void pruneHistoryLocked(AimTime now) {
        // Time and count bounds apply while idle too. The floor prevents a
        // partial sum from becoming false evidence of unconsumed error.
        while (!m_history.empty() && (m_history.size() > 4096
            || now - m_history.front().when > std::chrono::seconds(2))) {
            m_historyFloor = m_history.front().when;
            m_history.pop_front();
        }
        m_status.acceptedMotionEntries = m_history.size();
        m_status.motionHistoryComplete = !m_target || m_historyFloor == AimTime{}
            || m_target->lastUpdate > m_historyFloor;
    }
    void cancelLocked() {
        ++m_generation;
        m_target.reset(); m_lastPublished = {}; m_lastTick = {};
        m_identity = {};
        resetResponseLocked();
        resetCompensationLocked();
        m_toggle = false;
        m_status.active = m_status.authorized = m_status.compensationActive = false;
        m_status.observationAgeMs = -1.0;
        m_status.reason = "cancelled";
        if (m_events) m_events(m_config, false, false, AimClock::now(), m_status);
        if (m_lastActive) submitLocked({}, m_lastInputs);
        m_lastActive = false;
        observeCollectionLocked();
    }
    void observeCollectionLocked() {
        // Collection callback is try_lock/atomic retirement only, never IO or
        // output.status/live.read reentry. A contended request remains pending;
        // the collection owner reports completion separately.
        if (m_collection) m_collection(m_config, m_generation,
            m_status.authorized && m_status.fatalError.empty(), !m_stopped && m_status.fatalError.empty());
    }
    struct Emitted { AimTime when; double x, y; };
    mutable std::mutex m_mutex;
    std::condition_variable m_wake;
    std::thread m_thread;
    HANDLE m_stopEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    bool m_stopped = false, m_toggle = false, m_wasDown = false, m_inputInitialized = false;
    bool m_compensationWasDown = false, m_lastActive = false;
    std::uint64_t m_generation = 1;
    AppConfig m_config;
    std::optional<Track> m_target;
    CaptureFrameIdentity m_identity{};
    int m_width = 0, m_height = 0;
    AimTime m_lastPublished{}, m_lastTick{};
    AimTime m_historyFloor{};
    double m_filteredX = 0, m_filteredY = 0, m_remainderX = 0, m_remainderY = 0;
    double m_compensationRemainderX = 0, m_compensationRemainderY = 0;
    std::deque<Emitted> m_history;
    std::pair<double, double> m_automatic{};
    AimInputs m_lastInputs;
    AimOutputStatus m_status;
    XInputReader m_controller;
    Sink m_sink;
    Sampler m_sampler;
    Events m_events;
    CollectionObserver m_collection;
};

bool submitWindowsAim(const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
    if (!report.dx && !report.dy) return true;
    INPUT input{};
    input.type = INPUT_MOUSE;
    input.mi.dx = report.dx; input.mi.dy = report.dy;
    input.mi.dwFlags = MOUSEEVENTF_MOVE;
    return SendInput(1, &input, sizeof(input)) == 1;
}

class LiveConfigPump {
public:
    LiveConfigPump(const AppConfig& initial, AimOutputScheduler& output,
                   std::function<void(std::uint64_t)> rejected = {})
        : m_config(initial), m_output(output), m_rejected(std::move(rejected)) {
        m_changed = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        m_generation = m_output.configure(m_config);
        m_thread = std::thread([this] {
            LiveConfigReader reader;
            while (!m_stop.load()) {
                std::uint64_t rejectedEpoch = 0;
                {
                    std::lock_guard lock(m_mutex);
                    if (reader.applyAvailable(m_config)) {
                        m_generation = m_output.configure(m_config);
                        if (m_changed) SetEvent(m_changed);
                    }
                    rejectedEpoch = reader.takeRejectedEpoch();
                }
                // No telemetry serialization or callback under config/output locks.
                if (rejectedEpoch && m_rejected) m_rejected(rejectedEpoch);
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
        });
    }
    ~LiveConfigPump() { m_stop = true; if (m_thread.joinable()) m_thread.join(); if (m_changed) CloseHandle(m_changed); }
    std::uint64_t read(AppConfig& config) {
        std::lock_guard lock(m_mutex);
        if (m_changed) ResetEvent(m_changed);
        config = m_config; return m_generation;
    }
    bool current(std::uint64_t generation) { std::lock_guard lock(m_mutex); return generation == m_generation; }
    template<class Action>
    bool withCurrent(std::uint64_t generation, Action action) {
        std::lock_guard lock(m_mutex);
        if (generation != m_generation) return false;
        action();
        return true;
    }
    int captureFps() { std::lock_guard lock(m_mutex); return m_config.captureFps; }
    HANDLE changedEvent() const noexcept { return m_changed; }
private:
    HANDLE m_changed = nullptr;
    std::mutex m_mutex;
    AppConfig m_config;
    AimOutputScheduler& m_output;
    std::function<void(std::uint64_t)> m_rejected;
    std::uint64_t m_generation = 0;
    std::atomic<bool> m_stop{false};
    std::thread m_thread;
};
