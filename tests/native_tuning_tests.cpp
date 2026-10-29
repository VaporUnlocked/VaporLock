// Exercise the actual headless selector and wire parser without capture or ORT.
#include "app/Win32VaporLock.cpp"

static void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

static Track subject(int id, int classId, double x, double confidence = 0.9) {
    Track track;
    track.id = id;
    track.age = 5;
    track.detection.classId = classId;
    track.detection.confidence = confidence;
    track.detection.box = {x, 290, 60, 60};
    track.observedBox = track.detection.box;
    track.hasObservation = true;
    track.lastUpdate = AimClock::now();
    return track;
}

#include "tests/aim_output_tests.h"
#include "tests/compensation_output_tests.h"
#include "tests/controller_pipe_tests.h"
#include "tests/feature_batch3_tests.h"
#include "tests/feature_batch5_tests.h"

#include "tests/crosshair_reference_tests.h"

int main() {
    runCrosshairReferenceTests();
    {
        auto track=subject(17,42,290);
        track.observedBox={12,34,56,78};
        auto wire=overlayMetadata(640,640,{track},track,false,AppConfig{},0,0,AimClock::now());
        require(wire.find("\"class_id\":42")!=std::string::npos,"observation class missing");
        require(wire.find("\"has_observation\":true")!=std::string::npos,"observed identity missing");
        require(wire.find("\"observed_x\":12.000")!=std::string::npos,"raw observed box missing");
        require(wire.find("\"x\":290.000")!=std::string::npos,"display box changed");
    }

    {
        vaporlock::ControllerTrigger latch;
        require(!vaporlock::validTriggerThresholds(0,0) && !vaporlock::validTriggerThresholds(20,30)
            && !vaporlock::validTriggerThresholds(256,30), "invalid trigger thresholds accepted");
        require(!latch.sample(29,30,30,1) && latch.sample(30,30,30,1)
            && !latch.sample(29,30,30,1), "legacy trigger boundary changed");
        latch.reset();
        const int values[] = {0,89,90,70,50,49,70,90};
        const bool expected[] = {false,false,true,true,true,false,false,true};
        for (int i=0;i<8;++i) require(latch.sample(values[i],90,50,1)==expected[i], "trigger hysteresis mismatch");
        require(!latch.sample(70,90,50,2), "controller identity retained held trigger");
        require(latch.sample(100,90,50,2), "trigger press failed");
        require(!latch.sample(-1,90,50,2) && !latch.sample(70,90,50,2), "invalid sample retained trigger");
        require(latch.sample(255,255,1,2) && latch.sample(1,255,1,2)
            && !latch.sample(0,255,1,2), "trigger endpoint mismatch");
        require(latch.sample(100,90,50,2) && !latch.sample(70,90,60,2), "threshold change retained latch");
        XInputGamepad pad{}; pad.leftTrigger=30;
        require(controllerBindingDown(pad,14), "manual-lock trigger legacy path changed");
    }
    try {
        runFeatureBatch3Tests();
        runFeatureBatch5Tests();
        {
            HANDLE reader = nullptr, writer = nullptr;
            require(CreatePipe(&reader, &writer, nullptr, 0), "live command pipe creation failed");
            const HANDLE prior = GetStdHandle(STD_INPUT_HANDLE);
            struct RestoreInput { HANDLE prior, reader, writer; ~RestoreInput() {
                SetStdHandle(STD_INPUT_HANDLE, prior); CloseHandle(reader); CloseHandle(writer);
            } } restore{prior, reader, writer};
            require(SetStdHandle(STD_INPUT_HANDLE, reader), "live command pipe setup failed");
            LiveConfigReader live;
            AppConfig config; config.observeOnly = true;
            auto send = [&](const std::string& line) {
                DWORD written = 0;
                require(WriteFile(writer, line.data(), static_cast<DWORD>(line.size()), &written, nullptr)
                        && written == line.size(), "live command fixture write failed");
                return live.applyAvailable(config);
            };
            require(send("confidence\t0.65\t1\n") && config.visualEpoch == 1 && config.confidence == .65,
                    "display epoch did not follow accepted setting");
            require(!send("confidence\t0.1\t1\n") && config.confidence == .65, "replayed command changed configuration");
            require(!send("confidence\t0.1\t-1\n") && config.visualEpoch == 1, "malformed epoch changed configuration");
            require(send("movement_enabled\t1\t2\n") && !config.movementEnabled && config.observeOnly,
                    "display epoch altered Observe-only guard");
            require(send("confidence\t0.7\n") && config.confidence == .7 && config.visualEpoch == 2,
                    "legacy local command compatibility changed");
            config.eventActionMode = 1;
            require(!send("event_action_mode\tinvalid\t3\n") && config.eventActionMode == 1
                    && config.visualEpoch == 2 && live.takeRejectedEpoch() == 3,
                    "rejected enum changed operative config or lost its rejection");
            require(live.takeRejectedEpoch() == 0, "rejection was delivered more than once");
            require(!send("motion_compensation_strength\tnan\t4\n") && config.visualEpoch == 2
                    && live.takeRejectedEpoch() == 4, "nonfinite command lost rejection");
            require(!send("confidence\t0.8\t5\n") && config.visualEpoch == 2
                    && config.confidence == .7 && live.takeRejectedEpoch() == 5,
                    "newer field falsely acknowledged past a rejected command");
            require(!send("confidence\t0.8\n") && config.confidence == .7,
                    "legacy command escaped the rejected-session latch");
            LiveConfigReader restarted;
            DWORD written = 0;
            const std::string command = "confidence\t0.8\t5\n";
            require(WriteFile(writer, command.data(), static_cast<DWORD>(command.size()), &written, nullptr),
                    "restart command fixture failed");
            require(restarted.applyAvailable(config) && config.visualEpoch == 5 && config.confidence == .8,
                    "new reader retained another session's rejection");
        }
        {
            AppConfig config;
            config.datasetEnabled = true;
            require(config.datasetEnabled, "Community collection configuration was disabled");
        }
        {
            wchar_t engine[] = L"VaporLock.exe", headless[] = L"--headless", capabilities[] = L"--capabilities";
            wchar_t extra[] = L"--movement";
            wchar_t* arguments[] = {engine, headless, capabilities, extra};
            std::ostringstream stream;
            auto* previous = std::cout.rdbuf(stream.rdbuf());
            int result = -1, rejected = -1;
            try {
                result = runHeadless(3, arguments);
                rejected = runHeadless(4, arguments);
            } catch (...) {
                std::cout.rdbuf(previous);
                throw;
            }
            std::cout.rdbuf(previous);
            require(result == 0, "capability query attempted licensing or capture");
            require(rejected == 2, "capability query allowed extra operational arguments");
            require(stream.str().find("{\"type\":\"capabilities\",\"protocol_version\":1,\"cuda_preprocess\":false}") != std::string::npos,
                    "non-CUDA build advertised GPU preprocessing");
        }
        {
            const auto serialize = [](AimTime observedAt) {
                std::ostringstream stream;
                auto* previous = std::cout.rdbuf(stream.rdbuf());
                try {
                    stream << overlayMetadata(640, 640, {subject(1, 0, 290)}, {}, false, AppConfig{}, -320, 220, observedAt);
                } catch (...) {
                    std::cout.rdbuf(previous);
                    throw;
                }
                std::cout.rdbuf(previous);
                return stream.str();
            };
            const auto beforeTick = GetTickCount64();
            const auto observedAt = AimClock::now() - std::chrono::milliseconds(120);
            const auto frame = narrowToWide(serialize(observedAt));
            const auto afterTick = GetTickCount64();
            const double maximumAge = std::chrono::duration<double, std::milli>(AimClock::now() - observedAt).count();
            const double emittedTick = jsonNumber(frame, L"emitted_tick_ms", -1);
            const double observedAge = jsonNumber(frame, L"observation_age_ms", -1);
            require(emittedTick >= beforeTick && emittedTick <= afterTick, "overlay did not serialize shared Windows clock");
            require(observedAge >= 120 && observedAge <= maximumAge + 0.001, "overlay used arrival time instead of original capture age");
            require(jsonNumber(frame, L"capture_left", 0) == -320, "overlay clock changed capture geometry");
            require(serialize(AimTime{}).find("\"observation_age_ms\":null") != std::string::npos,
                    "unknown capture time was presented as a fresh frame");
            require(serialize(AimClock::now() + std::chrono::hours(1)).find("\"observation_age_ms\":null") != std::string::npos,
                    "future capture time was presented as a fresh frame");
        }
        AppConfig config;
        require(parseClassPolicies(L"1 2 10 1 0.25 -0.1", config.classPolicies), "valid rules rejected");
        Track car = subject(2, 2, 290);
        auto anchor = targetAnchor(car, config);
        require(anchor.first == 335 && anchor.second == 284, "class anchor not used");
        require(!parseClassPolicies(L"2 2 1 0 0 0 2 2 0 0 0", config.classPolicies), "duplicate accepted");
        require(config.classPolicies.at(2).priority == 10, "invalid update mutated rules");
        for (const auto* invalid : {L"-1", L"129", L"1 2 1 0 nan 0", L"1 2 1 4 0 0", L"0 trailing", L"1 2 1 0 1.01 0"})
        require(!parseClassPolicies(invalid, config.classPolicies), "invalid policy accepted");
        require(!resolveModelOutputLayout(6, 2, 0).endToEnd, "two-class YOLO class output misread");
        require(resolveModelOutputLayout(6, 1, 0).objectness, "one-class YOLO objectness output misread");
        require(!resolveModelOutputLayout(6, -1, 0).endToEnd, "ambiguous six-column output guessed as end-to-end");
        require(!resolveModelOutputLayout(84, 80, 0).objectness, "YOLOv8 output misread");
        require(resolveModelOutputLayout(85, 80, 0).objectness, "YOLOv5 output misread");
        require(!modelOutputTransposed(1, 6, 1, 0, 0, true), "one final box transposed");
        require(!modelOutputTransposed(300, 38, 1, 32, 0, true), "final segmentation transposed");
        require(modelOutputTransposed(134, 256, 130, 0, 0, false), "130-class raw output transposed incorrectly");
        require(!modelOutputTransposed(256, 134, 130, 0, 0, false), "row-major raw output transposed incorrectly");
        require(modelOutputTransposed(37, 300, 1, 32, 0, false), "raw segmentation transposed incorrectly");
        require(!modelOutputTransposed(1, 6, -1, 0, 0, false), "small unknown final output transposed");
        std::vector<float> endToEndRows(300U * 6U, 0.0F);
        endToEndRows[0] = 100.0F; endToEndRows[1] = 120.0F; endToEndRows[2] = 260.0F;
        endToEndRows[3] = 380.0F; endToEndRows[4] = 0.93F; endToEndRows[5] = 0.0F;
        require(looksLikeEndToEndOutput(endToEndRows.data(), 300, 6, false, 1), "YOLO end-to-end rows were not recognized");
        const std::vector<float> rawTwoClass{
            320.0F, 300.0F, 320.0F, 300.0F, 50.0F, 60.0F,
            60.0F, 70.0F, 0.91F, 0.12F, 0.09F, 0.88F,
        };
        require(!looksLikeEndToEndOutput(rawTwoClass.data(), 2, 6, true, 2), "transposed raw YOLO rows were misread as end-to-end");
        std::vector<unsigned char> circlePixels(640U * 640U * 4U, 0);
        drawFovGuide(circlePixels, 640, 640, 50, false, {1, 2, 3});
        const std::size_t circlePoint = (320U * 640U + 370U) * 4U;
        require(circlePixels[circlePoint] == 3 && circlePixels[circlePoint + 1] == 2 && circlePixels[circlePoint + 2] == 1,
                "circular FOV guide was not rendered");
        std::vector<unsigned char> rectanglePixels(640U * 640U * 4U, 0);
        drawFovGuide(rectanglePixels, 640, 640, 50, true, {4, 5, 6});
        const std::size_t rectangleCorner = (270U * 640U + 270U) * 4U;
        require(rectanglePixels[rectangleCorner] == 6, "rectangular FOV guide was not rendered");
        IoUTracker smoothTracker;
        config.boxSmoothing = 0.5;
        const Detection firstDetection = subject(1, 0, 100).detection;
        Detection secondDetection = firstDetection;
        secondDetection.box.x += 10;
        const auto smoothingStart = AimClock::now();
        smoothTracker.update({firstDetection}, config, smoothingStart);
        const auto smoothed = smoothTracker.update({secondDetection}, config,
            smoothingStart + std::chrono::duration_cast<AimClock::duration>(std::chrono::duration<double>(1.0 / 60)));
        require(smoothed.size() == 1 && std::abs(smoothed.front().detection.box.x - 105.0) < 0.001,
                "box smoothing did not stabilize the tracked position");
        {
            AppConfig association;
            association.trackRetentionMs = 300;
            IoUTracker duplicateTracker;
            auto original = subject(1, 0, 290).detection;
            auto duplicate = original;
            duplicate.box.x += 1;
            const auto start = AimClock::now();
            const auto advance = [&](int frame) { return start + std::chrono::milliseconds(frame * 17); };
            const auto initial = duplicateTracker.update({original}, association, advance(0));
            const auto conflicted = duplicateTracker.update({original, duplicate}, association, advance(1));
            require(conflicted.size() == 1 && !conflicted.front().identityCertain, "ambiguous detection spawned an authoritative identity");
            for (int frame = 2; frame < 180; ++frame) {
                const auto recovered = duplicateTracker.update({original}, association, advance(frame));
                require(recovered.size() == 1 && recovered.front().id == initial.front().id && recovered.front().identityCertain,
                        "one duplicate caused sustained identity churn");
            }
            IoUTracker overlapTracker;
            auto left = original, right = original;
            left.box.x -= 10; right.box.x += 10;
            overlapTracker.update({left, right}, association, advance(0));
            const auto overlap = overlapTracker.update({original}, association, advance(1));
            require(overlap.size() == 2 && std::all_of(overlap.begin(), overlap.end(), [](const Track& track) { return !track.identityCertain; }),
                    "ambiguous overlap created or authorized a new target");
            const auto separated = overlapTracker.update({left, right}, association, advance(2));
            require(separated.size() == 2 && std::all_of(separated.begin(), separated.end(), [](const Track& track) { return track.identityCertain; }),
                    "separated targets failed to regain their original identities");
        }
        config.classPolicies.clear();
        config.fovShape = 0;
        config.deadzone = 0;
        config.anchorSettleRadius = 0.0;
        Track centeredAnchor = subject(6, 0, 290);
        require(targetAnchorInsideDeadzone(centeredAnchor, 640, 640, config), "zero dead zone rejected an exactly centered anchor");
        centeredAnchor.detection.box.x += 0.25;
        require(!targetAnchorInsideDeadzone(centeredAnchor, 640, 640, config), "zero dead zone accepted an off-center anchor");
        config.anchorSettleRadius = 2.0;
        centeredAnchor.detection.box.x = 290.75;
        require(targetAnchorInsideDeadzone(centeredAnchor, 640, 640, config), "center settle radius did not snap a micro-jitter anchor");
        double remainder = 0.0;
        const LONG firstPrecisionStep = quantizeRelativeStep(0.42, remainder, 10);
        const LONG secondPrecisionStep = quantizeRelativeStep(0.42, remainder, 10);
        const LONG thirdPrecisionStep = quantizeRelativeStep(0.42, remainder, 10);
        require(firstPrecisionStep == 0 && secondPrecisionStep == 0 && thirdPrecisionStep == 1,
                "fractional anchor correction was discarded instead of converging");
        config.motionCompensationEnabled = true;
        config.motionCompensationMode = 1;
        config.motionCompensationAutoMinTracks = 3;
        config.motionCompensationAutoConsensus = 3.0;
        config.motionCompensationAutoSmoothing = 0.0;
        config.motionCompensationAutoMaxRate = 100.0;
        std::vector<Track> sharedDrift{subject(80, 0, 100), subject(81, 0, 200), subject(82, 0, 300)};
        for (auto& track : sharedDrift) { track.vx = 12.0; track.vy = -7.0; }
        MotionCompensationState compensation;
        const auto [compensationX, compensationY] = compensation.update(sharedDrift, config);
        require(std::abs(compensationX + 12.0) < 0.001 && std::abs(compensationY - 7.0) < 0.001,
                "automatic motion compensation did not oppose shared drift");
        require(compensation.automaticValid && compensation.contributors == 3,
                "automatic motion compensation did not report its consensus");
        sharedDrift[2].vx = 90.0;
        sharedDrift[2].vy = 90.0;
        compensation.update(sharedDrift, config);
        require(!compensation.automaticValid, "automatic motion compensation accepted a non-consensus track set");
        require(applyLiveSetting(config, L"motion_compensation_mode", L"combined"), "compensation mode rejected");
        require(applyLiveSetting(config, L"motion_compensation_activation_mode", L"hold") && config.motionCompensationActivationMode == 0,
                "hold compensation activation rejected");
        require(applyLiveSetting(config, L"motion_compensation_activation_mode", L"click") && config.motionCompensationActivationMode == 1,
                "one-shot click compensation activation rejected");
        require(applyLiveSetting(config, L"motion_compensation_activation_mode", L"constant") && config.motionCompensationActivationMode == 2,
                "constant compensation activation rejected");
        require(applyLiveSetting(config, L"motion_compensation_activation_mode", L"automatic") && config.motionCompensationActivationMode == 3,
                "automatic compensation activation rejected");
        require(applyLiveSetting(config, L"motion_compensation_activation_key", L"left")
                    && config.motionCompensationActivationKey == L"left",
                "left mouse compensation activation key rejected");
        require(applyLiveSetting(config, L"motion_compensation_vertical", L"0.001"), "precise vertical compensation rejected");
        require(applyLiveSetting(config, L"motion_compensation_horizontal", L"-18.25"), "horizontal compensation rejected");
        require(std::abs(config.motionCompensationVertical - 0.001) < 0.000001
                    && std::abs(config.motionCompensationHorizontal + 18.25) < 0.000001,
                "live motion compensation values were not retained");
        require(applyLiveSetting(config, L"class_policies", L"1 2 10 0 0 0"), "live rules rejected");
        config.lockPersistenceMs = 0;
        std::vector<Track> tracks{subject(1, 0, 290, 0.99), subject(2, 2, 400, 0.60)};
        TargetSelector selector;
        require(selector.select(tracks, 640, 640, config)->id == 2, "class priority ignored");
        tracks.erase(tracks.begin() + 1);
        config.lostTargetTimeoutMs = 0;
        require(selector.select(tracks, 640, 640, config)->id == 1, "fallback failed");
        tracks.push_back(car);
        config.lostTargetTimeoutMs = -1;
        require(selector.select(tracks, 640, 640, config)->id == 2, "higher priority did not replace fallback");
        tracks[1].missed = 1;
        require(selector.select(tracks, 640, 640, config)->id == 2, "short gap lost lock");
        tracks[1].missed = config.lostTargetTimeoutFrames + 1;
        tracks[1].lastUpdate -= std::chrono::seconds(1);
        // Expiry is elapsed time, independent of the number of missing updates.
        config.lostTargetTimeoutMs = 0;
        require(selector.select(tracks, 640, 640, config)->id == 1, "lost target did not expire");
        tracks[1].missed = 0;
        tracks[1].lastUpdate = AimClock::now();
        config.lostTargetTimeoutMs = -1;
        selector.toggleManualLock();
        require(selector.select(tracks, 640, 640, config)->id == 1, "priority overrode manual pin");
        TargetSelector durationSelector;
        config.lockPersistenceMs = 30000;
        std::vector<Track> durationTracks{subject(1, 0, 290)};
        durationSelector.select(durationTracks, 640, 640, config);
        durationTracks.push_back(car);
        require(durationSelector.select(durationTracks, 640, 640, config)->id == 1, "priority overrode minimum duration");
        TargetSelector radiusSelector;
        config.lockPersistenceMs = 0;
        config.acquisitionRadius = 100;
        tracks[1].detection.box.x = 550;
        require(radiusSelector.select(tracks, 640, 640, config)->id == 1, "priority bypassed acquisition radius");
        require(radiusSelector.select(tracks, 640, 640, config)->id == 1, "challenger bypassed acquisition radius");
        config.classPolicies.clear();
        config.strategy = 1;
        config.snapRadius = 50;
        config.nearRadius = 150;
        config.acquisitionRadius = 300;
        std::vector<Track> zoneTracks{subject(10, 0, 290, 0.60), subject(11, 0, 500, 0.99)};
        TargetSelector zoneSelector;
        require(zoneSelector.select(zoneTracks, 640, 640, config)->id == 10, "snap zone did not outrank outer challenger");
        require(zoneSelector.select(zoneTracks, 640, 640, config)->id == 10, "outer challenger stole snap lock");
        config.minimumTrackAge = 3;
        std::vector<Track> immediateSnap{subject(12, 0, 290, 0.70)};
        immediateSnap.front().age = 1;
        TargetSelector immediateSnapSelector;
        config.snapBypassesConfirmation = true;
        require(immediateSnapSelector.select(immediateSnap, 640, 640, config)->id == 12,
                "a visible snap target did not acquire immediately");
        std::vector<Track> nearHolding{subject(13, 0, 350, 0.70), subject(14, 0, 500, 0.99)};
        nearHolding.front().age = 1;
        TargetSelector nearHoldingSelector;
        require(!nearHoldingSelector.select(nearHolding, 640, 640, config),
                "outer candidate bypassed a near contender still being evaluated");
        nearHolding.front().age = config.minimumTrackAge;
        require(nearHoldingSelector.select(nearHolding, 640, 640, config)->id == 13,
                "mature near contender was not acquired before outer candidate");
        std::vector<Track> corner{subject(20, 0, 390)};
        corner.front().detection.box.y = 390;
        config.acquisitionRadius = 110;
        config.fovShape = 0;
        TargetSelector circleSelector;
        require(!circleSelector.select(corner, 640, 640, config), "circular FOV accepted outside corner");
        config.fovShape = 1;
        TargetSelector rectangleSelector;
        require(rectangleSelector.select(corner, 640, 640, config).has_value(), "rectangular FOV rejected inside corner");
        auto outputTarget = std::optional<Track>{tracks.front()};
        config.confidence = 0.80;
        require(targetEligibleForOutput(outputTarget, config), "visible confident target blocked");
        outputTarget->missed = 1;
        require(!targetEligibleForOutput(outputTarget, config), "predicted missing target permitted output");
        outputTarget->missed = 0;
        outputTarget->detection.confidence = 0.79;
        require(!targetEligibleForOutput(outputTarget, config), "below-confidence target permitted output");
        config.confidence = 0.55;
        config.classPolicies.clear();
        config.snapRadius = 25;
        std::optional<Track> snapTarget{subject(61, 0, 290, 0.90)};
        require(targetInsideSnapRadius(snapTarget, 640, 640, config), "centered target missed snap event radius");
        snapTarget->detection.box.x += 40;
        require(!targetInsideSnapRadius(snapTarget, 640, 640, config), "outer target triggered snap event radius");
        require(applyLiveSetting(config, L"event_action_enabled", L"true"), "snap event toggle rejected");
        require(config.eventActionRequiresActivation, "snap event activation gate was not enabled by default");
        require(applyLiveSetting(config, L"event_action_requires_activation", L"false"), "snap event activation gate rejected");
        require(applyLiveSetting(config, L"event_action_mode", L"hold"), "snap event hold mode rejected");
        require(applyLiveSetting(config, L"event_action_button", L"middle"), "snap event button rejected");
        require(applyLiveSetting(config, L"event_action_cooldown_ms", L"250"), "snap event cooldown rejected");
        require(config.eventActionEnabled && !config.eventActionRequiresActivation && config.eventActionMode == 1 && config.eventActionButton == 2 && config.eventActionCooldownMs == 250,
                "snap event live settings were not retained");
        int transport = vaporlock::hardware::kMouseTransport;
        require(vaporlock::hardware::parseTransport(L"makcu-v1", transport) && transport == vaporlock::hardware::kMakcuLegacyTransport,
                "MAKCU legacy transport was not parsed");
        require(vaporlock::hardware::parseTransport(L"kmbox-net", transport) && transport == vaporlock::hardware::kKmboxNetTransport,
                "KMBox Net transport was not parsed");
        require(vaporlock::hardware::parseTransport(L"virtual-mouse", transport) && transport == vaporlock::hardware::kVirtualMouseTransport,
                "virtual mouse transport was not parsed");
        require(!vaporlock::hardware::parseTransport(L"unknown-device", transport), "unknown hardware transport was accepted");
        AppConfig observeConfig;
        observeConfig.observeOnly = true;
        observeConfig.movementEnabled = true;
        observeConfig.motionCompensationEnabled = true;
        observeConfig.eventActionEnabled = true;
        observeConfig.outputTransport = vaporlock::hardware::kKmboxNetTransport;
        observeConfig.controllerHostPipe = L"VaporLockController-test";
        observeConfig.controllerHostVirtualSlot = 0;
        applySessionSafety(observeConfig);
        require(!observeConfig.movementEnabled && !observeConfig.motionCompensationEnabled && !observeConfig.eventActionEnabled,
                "Observe-only did not disable every output path");
        require(observeConfig.outputTransport == vaporlock::hardware::kMouseTransport
                    && observeConfig.controllerHostPipe.empty() && observeConfig.controllerHostVirtualSlot == -1,
                "Observe-only still prepared an external output transport");
        require(applyLiveSetting(observeConfig, L"movement_enabled", L"true") && !observeConfig.movementEnabled,
                "Live settings re-enabled movement in Observe-only");
        require(applyLiveSetting(observeConfig, L"motion_compensation_enabled", L"true") && !observeConfig.motionCompensationEnabled,
                "Live settings re-enabled compensation in Observe-only");
        require(applyLiveSetting(observeConfig, L"event_action_enabled", L"true") && !observeConfig.eventActionEnabled,
                "Live settings re-enabled snap events in Observe-only");
        require(vaporlock::hardware::makeMakcuLegacyMoveCommand(12, -7) == ".move(12,-7)\r\n",
                "MAKCU legacy movement command changed");
        const auto makcuFrame = vaporlock::hardware::makeMakcuBinaryMoveFrame(12, -7);
        require(makcuFrame.size() == 11 && makcuFrame[0] == 0x50 && makcuFrame[1] == 0x0d
                    && makcuFrame[2] == 0x07 && makcuFrame[3] == 0x00
                    && makcuFrame[4] == 12 && makcuFrame[5] == 0
                    && makcuFrame[6] == 0xf9 && makcuFrame[7] == 0xff,
                "MAKCU binary movement frame changed");
        require(vaporlock::hardware::makeVaporLockBridgeMoveCommand(12, -7) == "VLMB 1 12 -7\n",
                "serial HID bridge command changed");
        std::uint32_t deviceCode = 0;
        require(vaporlock::hardware::parseKmboxDeviceCode(L"A1B2C3D4", deviceCode) && deviceCode == 0xA1B2C3D4U,
                "KMBox device code was not parsed");
        require(!vaporlock::hardware::parseKmboxDeviceCode(L"A1B2", deviceCode), "short KMBox device code was accepted");
        config.outputTransport = vaporlock::hardware::kKmboxNetTransport;
        config.activationSource = 1;
        config.virtualControllerMode = 1;
        config.controllerHostPipe = L"VaporLockController-test";
        config.controllerHostVirtualSlot = 0;
        config.hardwareSerialPort = L"COM7";
        config.kmboxNetAddress = L"192.168.1.44";
        config.kmboxNetDeviceCode = L"A1B2C3D4";
        config.kmboxALibraryPath = L"C:\\Vendor\\KMBoxA.dll";
        config.classPolicies.clear();
        config.switchingThreshold = 0.18;
        tracks[1].detection.box.x = 295;
        TargetSelector centerSelector;
        require(centerSelector.select(tracks, 640, 640, config)->id == 1, "default center selection changed");
        require(centerSelector.select(tracks, 640, 640, config)->id == 1, "center lock oscillated");
        config.strategy = 1;
        config.watchlist = L"2";
        TargetSelector watchlistSelector;
        std::vector<Track> watchedTracks{subject(30, 0, 290, 0.99), subject(31, 2, 400, 0.60)};
        require(watchlistSelector.select(watchedTracks, 640, 640, config)->id == 31, "watchlist preference was ignored");
        config.watchlist.clear();
        config.occlusionHandling = true;
        config.occlusionGraceFrames = 4;
        config.occlusionReacquireRadius = 80;
        config.lostTargetTimeoutFrames = 12;
        TargetSelector occlusionSelector;
        std::vector<Track> occluded{subject(40, 0, 290, 0.90)};
        require(occlusionSelector.select(occluded, 640, 640, config)->id == 40, "occlusion lock was not acquired");
        occluded.front().missed = 1;
        occluded.push_back(subject(41, 0, 300, 0.89));
        require(occlusionSelector.select(occluded, 640, 640, config)->id == 41, "nearby target was not reacquired after occlusion");
        config.classPolicies.clear();
        config.strategy = 0;
        config.minimumTrackAge = 2;
        config.retentionRadius = 200;
        config.occlusionReacquireRadius = 60;
        config.lostTargetTimeoutFrames = 4;
        TargetSelector retainedAnchorSelector;
        std::vector<Track> retained{subject(70, 0, 290, 0.90)};
        require(retainedAnchorSelector.select(retained, 640, 640, config)->id == 70, "retention anchor was not acquired");
        retained.front().missed = 1;
        require(retainedAnchorSelector.select(retained, 640, 640, config)->id == 70, "lost target did not keep its retention reservation");
        std::vector<Track> unrelatedDuringHold{subject(71, 0, 450, 0.99)};
        require(!retainedAnchorSelector.select(unrelatedDuringHold, 640, 640, config),
                "unrelated candidate displaced a retained lost anchor");
        std::vector<Track> reappearedNearAnchor{subject(72, 0, 300, 0.89)};
        reappearedNearAnchor.front().age = 1;
        require(!retainedAnchorSelector.select(reappearedNearAnchor, 640, 640, config),
                "one unconfirmed detection substituted for lost identity");
        reappearedNearAnchor.front().age = config.minimumTrackAge;
        require(retainedAnchorSelector.select(reappearedNearAnchor, 640, 640, config)->id == 72,
                "same-class target did not recover from the retained anchor after tracker expiry");
        parseClassPolicies(L"1 2 10 0 0 0", config.classPolicies);
        require(!config.classPolicies.empty(), "Community class policies were removed");
        require(config.outputTransport == vaporlock::hardware::kKmboxNetTransport
                    && config.activationSource == 1 && config.virtualControllerMode == 1
                    && config.controllerHostPipe == L"VaporLockController-test" && config.controllerHostVirtualSlot == 0
                    && config.hardwareSerialPort == L"COM7" && config.kmboxNetAddress == L"192.168.1.44"
                    && config.kmboxNetDeviceCode == L"A1B2C3D4" && config.kmboxALibraryPath == L"C:\\Vendor\\KMBoxA.dll",
                "Universal controller or hardware output settings were removed");
        runAimOutputTests();
        runCompensationOutputTests();
        runControllerPipeTests();
        std::cout << "Native tuning tests passed" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
