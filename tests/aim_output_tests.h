// Synthetic capture clock, activation source and output sink: never SendInput.
static AimTime aimTime(double seconds) {
    return AimTime{} + std::chrono::seconds(10)
        + std::chrono::duration_cast<AimClock::duration>(std::chrono::duration<double>(seconds));
}

static Track timedSubject(int id, double x, AimTime time, double y = 290) {
    auto track = subject(id, 0, x);
    track.detection.box.y = y;
    track.observedBox = track.detection.box;
    track.lastUpdate = time;
    return track;
}

static void runAimOutputTests() {
    {
        AppConfig c; c.movementEnabled = true; c.predictLost = false; c.deadzone = 0;
        c.anchorSettleRadius = 0; c.movementSmoothing = 0; c.maxStep = 10000;
        c.maxSpeed = 100000; c.movementGain = .5;
        long moved = 0;
        AimOutputScheduler s([&](const AimReport& r, const AimInputs&, const AppConfig&, AimOutputStatus&) {
            moved += r.dx; return true;
        }, [](const AppConfig&) { AimInputs i; i.down = true; return i; });
        const auto gen = s.configure(c);
        const CaptureFrameIdentity identity{1, 1, 1, gen};
        auto track = timedSubject(7, 390, aimTime(0));
        s.publish(track, 640, 640, aimTime(0), gen, {}, identity);
        const auto before = s.visualSnapshot(identity, aimTime(0));
        require(before.eligible && before.trackId == 7, "visual snapshot lost accepted selection");
        s.pollOnce(aimTime(.001));
        const auto after = s.visualSnapshot(identity, aimTime(.002));
        require(after.eligible && std::abs(after.x - (before.x - moved / c.inputUnitsPerPixelX)) < 1e-6,
                "visual guide disagrees with accepted output displacement");
        require(!s.visualSnapshot({1, 1, 2, gen}, aimTime(.002)).eligible, "visual guide crossed frame identity");
        require(!s.visualSnapshot(identity, aimTime(3)).eligible, "expired visual guide stayed live");
        require(s.status().acceptedMotionEntries > 0, "source reset test did not produce accepted history");
        const CaptureFrameIdentity replacement{1, 2, 2, gen};
        s.observeSource(replacement, gen);
        require(s.generation() == gen, "source retirement desynchronized live configuration");
        require(!s.visualSnapshot(identity, aimTime(.003)).eligible && !s.visualSnapshot(replacement, aimTime(.003)).eligible,
                "source retirement retained the old target while inference was pending");
        require(s.status().acceptedMotionEntries == 0, "source retirement retained old displacement history");
        s.publish(track, 640, 640, aimTime(.003), gen, {}, identity);
        require(!s.visualSnapshot(identity, aimTime(.003)).eligible, "delayed retired-source inference revived old target");
        track.lastUpdate = aimTime(.004);
        s.publish(track, 640, 640, aimTime(.004), gen, {}, replacement);
        const auto replaced = s.visualSnapshot(replacement, aimTime(.004));
        require(replaced.eligible && replaced.trackId == 7 && std::abs(replaced.x-before.x)<1e-6,
                "new source reused old movement for a recycled track ID");
        s.clear();
        require(!s.visualSnapshot(identity, aimTime(.003)).eligible, "cancelled visual guide revived");
    }
    {
        AppConfig c;c.movementEnabled=true;c.deadzone=8;
        bool fatal=false;int moved=0;
        AimOutputScheduler s([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){if(r.dx||r.dy)++moved;return true;},
            [](const AppConfig&){AimInputs i;i.down=true;return i;},
            [&](const AppConfig&,bool,bool,AimTime,AimOutputStatus&status){if(fatal)status.fatalError=L"synthetic callback failure";});
        const auto gen=s.configure(c);
        s.publish(timedSubject(1,290,aimTime(0)),640,640,aimTime(0),gen);
        s.pollOnce(aimTime(0));fatal=true;s.pollOnce(aimTime(.001));
        require(s.generation()!=gen&&!s.status().active&&!s.status().authorized,"zero-output fatal callback retained active scheduling");
        require(s.status().observationAgeMs==-1,"cancelled telemetry retained observation age");
        for(int n=2;n<100;++n)s.pollOnce(aimTime(n*.001));
        require(moved==0,"fatal callback allowed subsequent relative movement");
    }
    {
        AppConfig c;c.lockPersistenceMs=0;c.minimumTrackAge=1;
        TargetSelector selection;
        std::vector<Track> visible{timedSubject(1,390,aimTime(0))};
        require(selection.select(visible,640,640,c,aimTime(0))->id==1,"initial acquisition gained unnecessary delay");
        visible={timedSubject(1,390,aimTime(.010)),timedSubject(2,310,aimTime(.010))};
        require(selection.select(visible,640,640,c,aimTime(.010))->id==1,"single-frame challenger stole valid lock");
        require(selection.select(visible,640,640,c,aimTime(.100))->id==1,"repeated old frame confirmed challenger");
        visible={timedSubject(1,390,aimTime(.060)),timedSubject(2,310,aimTime(.060))};
        require(selection.select(visible,640,640,c,aimTime(.100))->id==2,"sustained better target failed confirmed handoff");
        TargetSelector transient;
        visible={timedSubject(1,390,aimTime(1))};transient.select(visible,640,640,c,aimTime(1));
        for(int n=1;n<40;++n){
            const auto now=aimTime(1+n*.017);visible={timedSubject(1,390,now)};
            if(n%2)visible.push_back(timedSubject(2,310,now));
            require(transient.select(visible,640,640,c,now)->id==1,"intermittent contender accumulated false confirmation");
        }
        for(int boundary:{49,50,51}){
            TargetSelector exact;
            visible={timedSubject(1,390,aimTime(2))};exact.select(visible,640,640,c,aimTime(2));
            visible={timedSubject(1,390,aimTime(2.010)),timedSubject(2,310,aimTime(2.010))};
            exact.select(visible,640,640,c,aimTime(2.010));
            const auto now=aimTime(2.010)+std::chrono::milliseconds(boundary);
            visible={timedSubject(1,390,now),timedSubject(2,310,now)};
            require(exact.select(visible,640,640,c,now)->id==(boundary<50?1:2),"challenger time boundary changed");
        }
        for(bool pinned:{false,true}){
            TargetSelector priority;
            visible={timedSubject(1,390,aimTime(3))};priority.select(visible,640,640,c,aimTime(3));
            if(pinned)require(priority.toggleManualLock(),"manual pin fixture failed");
            visible={timedSubject(1,390,aimTime(3.010)),timedSubject(2,310,aimTime(3.010))};
            visible[1].detection.classId=2;c.watchlist=L"2";
            require(priority.select(visible,640,640,c,aimTime(3.010))->id==(pinned?1:2),"confirmation changed manual or explicit priority");
            c.watchlist.clear();
        }
    }
    // An old image may return after a miss, uncertainty or input release.
    // None of those events undoes accepted movement of the entire view.
    for (int interruption = 0; interruption < 5; ++interruption) {
        AppConfig c; c.movementEnabled=true;c.movementGain=.8;c.movementSmoothing=0;
        c.anchorSettleRadius=0;c.deadzone=0;c.predictLost=false;c.outputRate=1000;
        c.maxSpeed=100000;c.maxStep=10000;
        long accepted=0;bool held=true;
        AimOutputScheduler s([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){accepted+=r.dx;return true;},
            [&](const AppConfig&){AimInputs i;i.down=held;return i;});
        const auto gen=s.configure(c);
        s.publish(timedSubject(1,390,aimTime(0)),640,640,aimTime(0),gen);
        s.pollOnce(aimTime(.030));s.pollOnce(aimTime(.040));
        require(accepted>20,"continuity fixture did not send movement after capture");
        if(interruption<3) {
            std::optional<Track> missing;
            if(interruption){missing=timedSubject(1,390,aimTime(.010));
                if(interruption==1)missing->missed=1;else missing->identityCertain=false;}
            s.publish(missing,640,640,aimTime(.010),gen);
        } else if(interruption==3) held=false;
        const auto before=accepted;
        if(interruption!=4) {
            for(int n=41;n<46;++n)s.pollOnce(aimTime(n/1000.));
            require(accepted==before,"gap or release allowed tracking output");
        }
        held=true;
        s.publish(timedSubject(interruption==4?2:1,310,aimTime(.020)),640,640,aimTime(.020),gen);
        s.pollOnce(aimTime(.050));
        require(accepted<before,"gap/release/switch forgot accepted view displacement");
    }
    // Compensation-only output also affects a later tracking image.
#ifndef VAPORLOCK_FREE_EDITION
    {
        AppConfig c;c.movementEnabled=true;c.movementGain=.8;c.movementSmoothing=0;c.predictLost=false;
        c.anchorSettleRadius=0;c.deadzone=0;c.outputRate=1000;c.maxSpeed=100000;c.maxStep=10000;
        c.motionCompensationEnabled=true;c.motionCompensationActivationMode=0;c.motionCompensationHorizontal=-6000;
        bool tracking=false;long accepted=0;
        AimOutputScheduler s([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){accepted+=r.dx;return true;},
            [&](const AppConfig&){AimInputs i;i.down=tracking;i.compensationDown=!tracking;return i;});
        const auto gen=s.configure(c);
        s.pollOnce(aimTime(.03));s.pollOnce(aimTime(.04));
        const auto before=accepted;require(before>20,"compensation history fixture did not move");
        tracking=true;
        // Releasing compensation and arming tracking changes authority, not
        // the displacement already sent after this image was captured.
        s.publish(timedSubject(1,310,aimTime(.02)),640,640,aimTime(.02),gen);
        s.pollOnce(aimTime(.041));
        require(accepted<=before,"compensation-only movement was absent from view history");
    }
#endif
    {
        AppConfig c;c.movementEnabled=true;c.movementGain=.8;c.movementSmoothing=0;
        c.anchorSettleRadius=0;c.deadzone=0;c.predictLost=false;c.outputRate=5000;
        c.maxSpeed=1e8;c.maxStep=10000;c.outputMaxObservationAgeMs=10000;
        bool held=true,reject=false;long accepted=0;
        AimOutputScheduler s([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){
            if(reject)return false;accepted+=r.dx;return true;},
            [&](const AppConfig&){AimInputs i;i.down=held;return i;});
        auto gen=s.configure(c);
        s.publish(timedSubject(1,1e9,aimTime(0)),640,640,aimTime(0),gen);
        s.pollOnce(aimTime(0));
        require(s.status().acceptedMotionEntries==1,"accepted report not recorded exactly once");
        reject=true;s.pollOnce(aimTime(.001));reject=false;
        require(s.status().acceptedMotionEntries==1,"rejected write changed accepted history");
        for(int n=1;n<=5000;++n)s.pollOnce(aimTime(.001+n*.00025));
        require(s.status().acceptedMotionEntries<=4096,"accepted history exceeded count bound");
        require(!s.status().motionHistoryComplete,"truncated history was treated as complete");
        auto before=accepted;s.pollOnce(aimTime(1.8));
        require(accepted==before,"partially covered old image authorized output");
        s.publish(timedSubject(1,390,aimTime(1.9)),640,640,aimTime(1.9),gen);
        s.pollOnce(aimTime(1.9));
        require(s.status().motionHistoryComplete&&accepted>before,"fresh frame failed to recover history coverage");
        held=false;s.pollOnce(aimTime(4.0));
        require(s.status().acceptedMotionEntries==0,"idle polling retained expired history");
        s.clear();require(s.status().acceptedMotionEntries==0,"clear retained a motion epoch");
    }
    // Hardware clipping must occur before quantization/history, using the same
    // report accepted by the recording sink, including the virtual mouse path.
    for(int transport=2;transport<=7;++transport) {
        AppConfig c;c.outputTransport=transport;c.movementEnabled=true;c.movementGain=.8;
        c.movementSmoothing=0;c.predictLost=false;c.outputRate=60;c.maxSpeed=1e9;c.maxStep=1000000;
        LONG delta=0;
        AimOutputScheduler s([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){delta=r.dx;return true;},
            [](const AppConfig&){AimInputs i;i.down=true;return i;});
        auto gen=s.configure(c);s.publish(timedSubject(1,1e9,aimTime(0)),640,640,aimTime(0),gen);s.pollOnce(aimTime(0));
        require(delta==32767,"hardware report exceeded its accepted wire range");
    }
    {
        AppConfig delayed;
        delayed.movementEnabled = true;
        delayed.movementSmoothing = 0;
        delayed.anchorSettleRadius = 0;
        delayed.deadzone = 0;
        delayed.predictLost = false;
        delayed.outputRate = 240;
        delayed.maxSpeed = 100000;
        delayed.maxStep = 10000;
        long accepted = 0;
        AimOutputScheduler changing([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
            accepted += report.dx;
            return true;
        }, [](const AppConfig&) { AimInputs input; input.down = true; return input; });
        const auto generation = changing.configure(delayed);
        changing.publish(timedSubject(1, 490, aimTime(0)), 640, 640, aimTime(0), generation);
        changing.pollOnce(aimTime(0));
        const auto beforeCapture = accepted;
        for (int tick = 1; tick <= 4; ++tick) changing.pollOnce(aimTime(tick * .01));
        require(accepted - beforeCapture > 50, "delayed switch fixture did not cross the new target");
        const auto beforeSwitch = accepted;
        // This image was captured while the old target was still in control.
        changing.publish(timedSubject(2, 340, aimTime(.005)), 640, 640, aimTime(.005), generation);
        changing.pollOnce(aimTime(.05));
        require(accepted < beforeSwitch, "target switch discarded movement since the new image was captured");
    }
    AppConfig config;
    config.movementEnabled = true;
    config.movementSmoothing = 0;
    config.anchorSmoothing = 0;
    config.anchorSettleRadius = 0;
    config.deadzone = 0;
    config.predictLost = false;
    config.outputRate = 240;
    config.maxSpeed = 100000;
    config.maxStep = 10000;
    config.outputMaxObservationAgeMs = 120;
    bool held = true, valid = true;
    std::vector<AimReport> reports;
    const auto sink = [&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
        if (report.dx || report.dy || report.stickX || report.stickY) reports.push_back(report);
        return true;
    };
    const auto sampler = [&](const AppConfig&) { AimInputs input; input.down = held; input.valid = valid; return input; };
    AimOutputScheduler worker(sink, sampler);
    auto generation = worker.configure(config);
    auto target = timedSubject(1, 490, aimTime(0));
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    worker.pollOnce(aimTime(0));
    require(!reports.empty(), "fresh authorized target did not submit");
    held = false;
    const auto beforeRelease = reports.size();
    worker.pollOnce(aimTime(0.001));
    require(reports.size() == beforeRelease && !worker.status().authorized, "hold release waited for output rate or inference");
    held = true;
    worker.pollOnce(aimTime(0.02));
    require(reports.size() > beforeRelease, "hold did not resume with a still-fresh observation");
    worker.pollOnce(aimTime(0.121));
    const auto expiredCount = reports.size();
    for (int n = 1; n < 50; ++n) {
        worker.publish(target, 640, 640, target.lastUpdate, generation);
        worker.pollOnce(aimTime(0.121 + n * 0.01));
    }
    require(reports.size() == expiredCount, "republishing old high-confidence observations renewed output");
    target.lastUpdate = aimTime(0.7);
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    worker.pollOnce(aimTime(0.7));
    require(reports.size() > expiredCount, "fresh processing could not resume after a stall");
    valid = false;
    const auto invalidCount = reports.size();
    worker.pollOnce(aimTime(0.71));
    require(reports.size() == invalidCount, "invalid input authorized movement");
    valid = true;
    worker.clear();
    worker.publish(target, 640, 640, aimTime(0.72), generation);
    worker.pollOnce(aimTime(0.73));
    require(reports.size() == invalidCount, "old configuration generation reactivated output");

    // Deterministic copy/cancel/submit interleaving, with no timing luck needed.
    generation = worker.configure(config);
    target.lastUpdate = AimClock::now();
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    std::promise<void> paused, resume;
    auto ready = paused.get_future();
    auto resumeFuture = resume.get_future();
    worker.beforeSubmission = [&] { paused.set_value(); resumeFuture.wait(); };
    std::thread pending([&] { worker.pollOnce(AimClock::now()); });
    ready.wait();
    worker.clear();
    resume.set_value();
    pending.join();
    require(reports.size() == invalidCount, "cancelled command submitted after clear completed");
    worker.beforeSubmission = {};

    config.activationMode = 1;
    held = false;
    generation = worker.configure(config);
    target.lastUpdate = aimTime(2);
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    worker.pollOnce(aimTime(2));
    held = true; worker.pollOnce(aimTime(2.01));
    held = false; worker.pollOnce(aimTime(2.02));
    require(worker.status().authorized, "toggle was treated as hold");
    held = true; worker.pollOnce(aimTime(2.03));
    require(!worker.status().authorized, "second press did not release toggle");
    config.activationMode = 0;

    // Observation age includes inference latency, not time since publication.
    generation = worker.configure(config);
    target.lastUpdate = aimTime(3);
    worker.publish(target, 640, 640, aimTime(3), generation);
    const auto delayedCount = reports.size();
    worker.pollOnce(aimTime(3.3));
    require(reports.size() == delayedCount, "late inference was treated as a fresh capture");
    config.movementEnabled = false;
    generation = worker.configure(config);
    target.lastUpdate = aimTime(3.4);
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    worker.pollOnce(aimTime(3.4));
    require(reports.size() == delayedCount, "disabled assistance still submitted");
    config.movementEnabled = true;

    config.aimReference = 1; config.aimReferenceX = 25; config.aimReferenceY = -10;
    config.inputUnitsPerPixelX = 2; config.inputUnitsPerPixelY = 3;
    config.movementGain = 0.18; config.outputRate = 60;
    generation = worker.configure(config);
    target = timedSubject(2, 355, aimTime(4), 300); // anchor 385,330; reference 345,310
    worker.publish(target, 640, 640, target.lastUpdate, generation);
    worker.pollOnce(aimTime(4));
    require(reports.back().dx == 14 && reports.back().dy == 10, "calibration or crosshair offset produced wrong correction");
    require(aimingReference(640, 640, config) == std::pair<double, double>{345, 310}, "aiming reference not capture relative");
    const auto controllerValue = [&](int rate) {
        AppConfig settings = config;
        settings.outputTransport = vaporlock::hardware::kVirtualXboxTransport;
        settings.outputRate = rate;
        AimReport last;
        AimOutputScheduler controller([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) { last = report; return true; }, sampler);
        const auto gen = controller.configure(settings);
        controller.publish(target, 640, 640, target.lastUpdate, gen);
        controller.pollOnce(aimTime(4));
        const auto active = last;
        held = false; controller.pollOnce(aimTime(4.001)); held = true;
        require(last.stickX == 0 && last.stickY == 0, "controller release failed to submit neutral assistance");
        require(controller.status().acceptedMotionEntries==0,"controller velocity entered relative mouse history");
        require(active.stickX > 0 && active.stickY < 0, "controller axis convention changed");
        return active;
    };
    const auto controller60 = controllerValue(60), controller240 = controllerValue(240);
    require(controller60.stickX == controller240.stickX && controller60.stickY == controller240.stickY,
            "normalized controller strength changed with report rate");

    // Equal trajectories with varied observation and output clocks. A tiny
    // simulated plant converts one input unit to one capture pixel.
    struct Response { double at100, finalError, overshoot; };
    const auto response = [&](int observationsHz, int outputHz, bool irregular, bool useTracker = false) {
        AppConfig settings;
        settings.movementEnabled = true; settings.predictLost = false;
        settings.anchorSettleRadius = 0; settings.deadzone = 0;
        settings.movementSmoothing = 0.35; settings.maxStep = 10000;
        settings.maxSpeed = 1440; settings.outputRate = outputHz;
        settings.outputMaxObservationAgeMs = 150;
        double position = 0, at100 = 0, overshoot = 0;
        AimOutputScheduler simulation([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
            position += report.dx; overshoot = std::max(overshoot, position - 200); return true;
        }, [](const AppConfig&) { AimInputs input; input.down = true; return input; });
        const auto gen = simulation.configure(settings);
        IoUTracker tracker;
        double nextObservation = 0;
        for (int ms = 0; ms <= 1000; ++ms) {
            const double t = ms / 1000.0;
            if (t >= nextObservation) {
                auto observation = timedSubject(1, 490 - position, aimTime(t));
                if (useTracker) observation = tracker.update({observation.detection}, settings, aimTime(t)).front();
                simulation.publish(observation, 640, 640, observation.lastUpdate, gen);
                nextObservation = t + (irregular ? (ms % 2 ? 0.7 : 1.3) : 1.0) / observationsHz;
            }
            simulation.pollOnce(aimTime(t));
            if (ms == 100) at100 = position;
        }
        return Response{at100, 200 - position, overshoot};
    };
    double minProgress = 1e9, maxProgress = 0;
    for (int observationRate : {30, 60, 120}) for (int outputRate : {60, 120, 240}) for (bool irregular : {false, true}) {
        const auto result = response(observationRate, outputRate, irregular);
        minProgress = std::min(minProgress, result.at100); maxProgress = std::max(maxProgress, result.at100);
        require(std::abs(result.finalError) <= 2 && result.overshoot <= 1, "trajectory failed to settle or overshot");
    }
    require(maxProgress - minProgress <= 20, "assistance strength changed materially with processing rate");
    std::cout << "Trajectory matrix: 18 scenarios; 100ms progress " << minProgress << ".." << maxProgress << " input units" << std::endl;
    double filteredMin = 1e9, filteredMax = 0, filteredOvershoot = 0;
    for (int observations : {30, 60, 120}) for (int outputs : {60, 120, 240}) for (bool irregular : {false, true}) {
        const auto result = response(observations, outputs, irregular, true);
        filteredMin = std::min(filteredMin, result.at100); filteredMax = std::max(filteredMax, result.at100);
        filteredOvershoot = std::max(filteredOvershoot, result.overshoot);
        require(std::abs(result.finalError) <= 2, "combined filters failed to settle");
    }
    std::cout << "Combined-filter matrix: 18 scenarios; 100ms progress " << filteredMin << ".." << filteredMax
              << "; maximum overshoot " << filteredOvershoot << " units" << std::endl;
    require(filteredOvershoot <= 1, "stacked box/anchor filters caused overshoot");

    // Real worker: no new observations while a simulated slow inference runs.
    std::atomic<bool> realHeld{true};
    std::mutex timesMutex;
    std::vector<AimTime> times;
    config = AppConfig{};
    config.movementEnabled = true; config.outputRate = 240; config.deadzone = 0;
    config.outputMaxObservationAgeMs = 500; config.maxSpeed = 500;
    AimOutputScheduler asynchronous([&](const AimReport& report, const AimInputs&, const AppConfig&, AimOutputStatus&) {
        if (report.dx || report.dy) { std::lock_guard lock(timesMutex); times.push_back(AimClock::now()); }
        return true;
    }, [&](const AppConfig&) { AimInputs input; input.down = realHeld.load(); return input; });
    generation = asynchronous.configure(config);
    target = timedSubject(1, 600, AimClock::now());
    asynchronous.publish(target, 1000, 640, target.lastUpdate, generation);
    asynchronous.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    const auto releasedAt = AimClock::now();
    realHeld = false;
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    asynchronous.stop();
    require(!times.empty(), "threaded recording sink received no test output");
    const double releaseLag = std::max(0.0, std::chrono::duration<double, std::milli>(times.back() - releasedAt).count());
    require(releaseLag < 30, "release remained tied to the simulated slow inference");
    std::cout << "Release during 200ms inference stall: final post-release submission delay " << releaseLag << " ms" << std::endl;

    // Explicit confirmation, bounded waiting and non-substituting manual pin.
    config = AppConfig{}; config.minimumTrackAge = 3; config.lockPersistenceMs = 0;
    config.outputMaxObservationAgeMs = 1000; config.nearCandidateWaitMs = 100;
    std::vector<Track> candidates{timedSubject(1, 290, aimTime(0))};
    candidates[0].age = 1;
    TargetSelector confirm;
    require(!confirm.select(candidates, 640, 640, config, aimTime(0)), "snap bypassed confirmation without opt-in");
    config.snapBypassesConfirmation = true;
    require(confirm.select(candidates, 640, 640, config, aimTime(0))->id == 1, "explicit snap bypass did not work");
    config.snapBypassesConfirmation = false;
    TargetSelector wait;
    candidates = {timedSubject(2, 350, aimTime(0)), timedSubject(3, 500, aimTime(0))};
    candidates[0].age = 1;
    require(!wait.select(candidates, 640, 640, config, aimTime(0)), "near wait was not applied");
    candidates[0].id = 4;
    require(wait.select(candidates, 640, 640, config, aimTime(0.11))->id == 3, "replaced near contender starved confirmed outer target");
    TargetSelector pinned;
    candidates = {timedSubject(5, 290, aimTime(1))};
    pinned.select(candidates, 640, 640, config, aimTime(1));
    pinned.toggleManualLock();
    candidates[0].missed = 1;
    candidates.push_back(timedSubject(6, 292, aimTime(1.02)));
    require(pinned.select(candidates, 640, 640, config, aimTime(1.02))->id == 5, "manual pin silently switched identity");
    require(!targetEligibleForOutput(candidates[0], config, aimTime(1.02)), "retained manual identity authorized output");
    TargetSelector uncertain;
    candidates = {timedSubject(7, 290, aimTime(2))};
    uncertain.select(candidates, 640, 640, config, aimTime(2));
    candidates[0].missed = 1;
    candidates.push_back(timedSubject(8, 292, aimTime(2.02)));
    candidates.push_back(timedSubject(9, 288, aimTime(2.02)));
    require(uncertain.select(candidates, 640, 640, config, aimTime(2.02))->id == 7, "ambiguous recovery guessed an identity");

    config.trackRetentionMs = 200;
    for (int rate : {30, 60, 120}) {
        IoUTracker tracker;
        const auto first = timedSubject(1, 100, aimTime(0)).detection;
        tracker.update({first}, config, aimTime(0));
        for (int n = 1; n <= rate / 5; ++n)
            require(!tracker.update({}, config, aimTime(n / static_cast<double>(rate))).empty(), "retention changed with inference rate");
        require(tracker.update({}, config, aimTime(0.201)).empty(), "time-based track retention did not expire");
    }
    config.boxSmoothing = 0; config.anchorSmoothing = 0; config.smoothing = 0.35;
    config.predictLost = true; config.predictionLeadMs = 200; config.predictionMaxDistance = 25;
    IoUTracker motion;
    Track moving;
    for (int n = 0; n < 20; ++n) {
        auto detection = timedSubject(1, 100 + n * 3, aimTime(n / 60.0)).detection;
        moving = motion.update({detection}, config, aimTime(n / 60.0)).front();
    }
    const auto anchor = targetAnchor(moving, config);
    const auto prediction = predictedTargetAnchor(moving, config, moving.lastUpdate + std::chrono::milliseconds(80));
    require(prediction.first > anchor.first && std::hypot(prediction.first - anchor.first, prediction.second - anchor.second) <= 25.001,
            "latency-aware prediction failed its bound");
    auto stopped = motion.update({moving.detection}, config, moving.lastUpdate + std::chrono::milliseconds(17)).front();
    require(stopped.vx == 0 && stopped.ax == 0 && stopped.motionConfidence == 0, "abrupt stop retained old prediction");
    config.predictionLeadMs = 0; config.predictionMaxDistance = 100;
    const auto noDelay = predictedTargetAnchor(moving, config, moving.lastUpdate);
    const auto delayed = predictedTargetAnchor(moving, config, moving.lastUpdate + std::chrono::milliseconds(80));
    require(delayed.first > noDelay.first + 5, "prediction ignored observation latency");
    for (int rate : {30, 60, 120}) {
        IoUTracker steady;
        Track last;
        for (int n = 0; n <= rate; ++n) {
            const double t = n / static_cast<double>(rate);
            last = steady.update({timedSubject(1, 100 + 90 * t, aimTime(t)).detection}, config, aimTime(t)).front();
        }
        require(std::abs(last.vx - 90) < 1, "motion estimate changed with observation rate");
        auto reversedDetection = last.detection; reversedDetection.box.x -= 4;
        auto reversed = steady.update({reversedDetection}, config, aimTime(1.04)).front();
        require(reversed.motionConfidence == 0 && reversed.ax == 0, "reversal retained old motion certainty");
    }
    IoUTracker jitter;
    for (int n = 0; n < 100; ++n) {
        const auto detection = timedSubject(1, 100 + (n % 2 ? 0.3 : -0.3), aimTime(n / 120.0)).detection;
        const auto track = jitter.update({detection}, config, aimTime(n / 120.0)).front();
        require(track.motionConfidence == 0, "stationary box jitter became prediction");
    }
    IoUTracker crossing;
    crossing.update({timedSubject(1, 100, aimTime(0)).detection, timedSubject(2, 120, aimTime(0)).detection}, config, aimTime(0));
    const auto cross = crossing.update({timedSubject(3, 110, aimTime(0.02)).detection}, config, aimTime(0.02));
    require(!cross[0].identityCertain && !cross[1].identityCertain, "ambiguous crossing silently transferred numeric IDs");
    std::cout << "Aim freshness, activation, cancellation, calibration, timing, prediction and identity tests passed" << std::endl;
}
