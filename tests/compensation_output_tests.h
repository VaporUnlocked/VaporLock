// Recording sinks only. These cases never sample or send desktop input.
static void runCompensationOutputTests() {
    for (int limit : {1, 24, 10000}) {
        double legacy = 0, tracking = 0, compensation = 0;
        for (int index = 0; index < 4000; ++index) {
            const double request = index % 19 == 0 ? 0.0 : ((index * 73 % 283) - 141) / 37.0;
            const auto expected = quantizeRelativeStep(request, legacy, limit);
            require(quantizeBlendedStep(request, 0, tracking, compensation, limit) == expected,
                    "tracking-only quantization changed");
            require(tracking == legacy && compensation == 0, "tracking-only remainder changed");
        }
        for (double target : {-9.25, -0.4, 0.0, 0.25, 0.8, 9.25})
        for (double correction : {-9.25, -0.8, -0.25, 0.0, 0.4, 9.25}) {
            tracking = compensation = 0;
            long long total = 0;
            for (int index = 0; index < 1000; ++index) {
                const auto step = quantizeBlendedStep(target, correction, tracking, compensation, limit);
                total += step;
                require(std::abs(step) <= limit, "combined report exceeded max step");
                require(std::isfinite(tracking) && std::isfinite(compensation)
                            && std::abs(tracking) < 1 && std::abs(compensation) < 1
                            && std::abs(tracking + compensation) < 1,
                        "combined quantization accumulated hidden movement debt");
            }
            const double expected = std::clamp(target + correction, -static_cast<double>(limit),
                                               static_cast<double>(limit)) * 1000;
            require(std::abs(total - expected) <= 1.000001, "constant combined movement lost precision");
        }
    }
    double tracking = 0, compensation = 0;
    require(quantizeBlendedStep(.3, .4, tracking, compensation, 24) == 0, "fraction split emitted early");
    tracking = 0; // Tracking cancelled while compensation remains authorized.
    require(quantizeBlendedStep(0, .4, tracking, compensation, 24) == 0, "cancelled tracking fraction leaked");
    require(quantizeBlendedStep(0, .4, tracking, compensation, 24) == 1, "compensation fraction was cancelled by tracking");
    tracking = compensation = 0;
    quantizeBlendedStep(.4, .4, tracking, compensation, 24);
    require(quantizeBlendedStep(-.2, .4, tracking, compensation, 24) == 0, "direction reversal moved early");
    require(quantizeBlendedStep(0, .4, tracking, compensation, 24) == 1, "tracking reversal erased compensation fraction");
    require(quantizeBlendedStep(1000, -900, tracking, compensation, 24) == 24,
            "individually clipping sources cancelled valid combined motion");
    require(tracking == 0 && compensation == 0, "step clipping retained a backlog");
    AppConfig cadenceConfig;
    cadenceConfig.movementEnabled = true;
    cadenceConfig.motionCompensationEnabled = false;
    cadenceConfig.movementSmoothing = 0;
    cadenceConfig.outputRate = 60;
    cadenceConfig.maxSpeed = 100000;
    cadenceConfig.maxStep = 10000;
    bool unrelatedClick = false;
    int reports = 0;
    AimOutputScheduler cadence([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
        if (report.dx || report.dy) ++reports;
        return true;
    }, [&](const AppConfig&) {
        AimInputs input; input.down = true; input.compensationDown = unrelatedClick; return input;
    });
    const auto cadenceGeneration = cadence.configure(cadenceConfig);
    cadence.publish(timedSubject(1, 2000, aimTime(0)), 640, 640, aimTime(0), cadenceGeneration);
    cadence.pollOnce(aimTime(0));
    require(reports == 1, "output cadence fixture did not start");
    unrelatedClick = true;
    cadence.pollOnce(aimTime(.001));
    require(reports == 1, "disabled compensation click bypassed tracking output rate");
    cadence.pollOnce(aimTime(.02));
    require(reports == 2, "ordinary tracking did not resume at its configured output cadence");
#ifndef VAPORLOCK_FREE_EDITION
    int scenarios = 0;
    for (int observationRate : {15, 60, 120}) for (int outputRate : {60, 240, 1000})
    for (int targetMode = 0; targetMode < 6; ++targetMode) for (int sourceMode = 0; sourceMode < 3; ++sourceMode)
    for (int activationMode : {0, 2}) {
        AppConfig config;
        config.movementEnabled = true;
        config.movementGain = 0; // Isolate compensation; still exercise target/deadzone state changes.
        config.motionCompensationEnabled = true;
        config.motionCompensationMode = sourceMode;
        config.motionCompensationActivationMode = activationMode;
        config.motionCompensationHorizontal = sourceMode == 2 ? 5 : 10;
        config.motionCompensationVertical = sourceMode == 2 ? -20 : -40;
        config.inputUnitsPerPixelX = 1.0;
        config.inputUnitsPerPixelY = 1.0;
        config.motionCompensationStrength = 1;
        config.outputRate = outputRate;
        config.outputMaxObservationAgeMs = 150;
        config.maxSpeed = 100000;
        config.maxStep = 10000;
        config.anchorSettleRadius = config.deadzone = 0;
        config.predictLost = false;
        long long x = 0, y = 0;
        AimOutputScheduler scheduler([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
            x += report.dx; y += report.dy;
            return true;
        }, [](const AppConfig&) { AimInputs input; input.down = input.compensationDown = true; return input; });
        const auto generation = scheduler.configure(config);
        double nextObservation = 0;
        int observation = 0;
        for (int tick = 0; tick < outputRate * 3; ++tick) {
            const double t = tick / static_cast<double>(outputRate);
            if (t + 1e-9 >= nextObservation) {
                ++observation;
                auto target = timedSubject(targetMode == 2 ? observation : 1, 290, aimTime(t));
                if (targetMode == 4) target.identityCertain = false;
                if (targetMode == 5) target.missed = 1;
                const bool absent = targetMode == 0 || (targetMode == 3 && observation % 2);
                scheduler.publish(absent ? std::nullopt : std::optional<Track>(target), 640, 640, aimTime(t), generation,
                                  sourceMode == 2 ? std::pair{-5.0, 20.0} : std::pair{-10.0, 40.0});
                nextObservation += 1.0 / observationRate;
            }
            scheduler.pollOnce(aimTime(t));
        }
        if (std::abs(x + 30) > 1 || std::abs(y - 120) > 1) {
            std::ostringstream failure;
            failure << "Compensation lost fractions: obs=" << observationRate << " output=" << outputRate
                    << " target=" << targetMode << " source=" << sourceMode << " activation=" << activationMode
                    << " actual=" << x << ',' << y;
            throw std::runtime_error(failure.str());
        }
        ++scenarios;
    }
    std::cout << "Compensation integration: " << scenarios << " target/rate/source/activation cases passed" << std::endl;

    AppConfig config;
    config.movementEnabled = true;
    config.movementGain = 0.9;
    config.movementSmoothing = config.anchorSettleRadius = config.deadzone = 0;
    config.predictLost = false;
    config.motionCompensationEnabled = true;
    config.motionCompensationActivationMode = 0;
    config.motionCompensationMode = 1;
    config.outputRate = 100;
    config.maxSpeed = 100000;
    config.maxStep = 10000;
    bool held = true;
    int dx = 0;
    AimOutputScheduler loss([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
        dx += report.dx; return true;
    }, [&](const AppConfig&) { AimInputs input; input.down = true; input.compensationDown = held; return input; });
    auto generation = loss.configure(config);
    loss.publish(timedSubject(1, 291, aimTime(0)), 640, 640, aimTime(0), generation);
    loss.pollOnce(aimTime(0));
    require(dx == 0, "tracking fixture did not accumulate a fractional step");
    loss.publish(std::nullopt, 640, 640, aimTime(0.01), generation, {50, 0});
    loss.pollOnce(aimTime(0.01));
    require(dx == 0, "lost-target fraction leaked into independent compensation");
    loss.pollOnce(aimTime(0.02));
    require(dx == 1, "compensation did not retain its own fraction after target loss");

    config.motionCompensationMode = 0;
    config.outputRate = 60;
    config.motionCompensationHorizontal = -24;
    config.motionCompensationVertical = 0;
    generation = loss.configure(config);
    dx = 0;
    const auto tick = [&](int index) {
        const auto time = aimTime(1 + index / 60.0);
        loss.publish(timedSubject(1, 290, time), 640, 640, time, generation);
        loss.pollOnce(time);
    };
    tick(0); // +0.4 held compensation, centered tracking
    held = false; tick(1);
    held = true; tick(2); tick(3);
    require(dx == 0, "released compensation retained an old fractional step");
    tick(4);
    require(dx == 1, "re-armed compensation did not integrate fresh input");

    config.observeOnly = true;
    generation = loss.configure(config);
    dx = 0;
    for (int index = 0; index < 120; ++index) tick(index);
    require(dx == 0, "Observe-only emitted compensation");

    config.observeOnly = false;
    config.movementEnabled = false;
    int index = 0;
    std::vector<int> attempted;
    AimOutputScheduler rejected([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
        if (report.dx) attempted.push_back(index);
        return attempted.size() != 1;
    }, [](const AppConfig&) { AimInputs input; input.compensationDown = true; return input; });
    rejected.configure(config);
    for (index = 0; index < 6; ++index) rejected.pollOnce(aimTime(index / 60.0));
    require(attempted == std::vector<int>{2, 5}, "rejected transport write retained a compensation remainder");

    config.motionCompensationHorizontal = -12;
    config.motionCompensationActivationMode = 1;
    held = false;
    generation = loss.configure(config);
    dx = 0;
    tick(0);
    held = true;
    for (int n = 1; n < 30; ++n) tick(n);
    require(dx == 12, "click compensation repeated while held");
    held = false; tick(30);
    held = true; tick(31);
    require(dx == 24, "second click did not produce one compensation event");
#endif
}
