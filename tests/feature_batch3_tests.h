// Actual selector/scheduler with synthetic clocks and sinks: no device output.
static void runFeatureBatch3Tests() {
    {
        AppConfig c;
        for (const auto* field : {L"assistance_x", L"assistance_y", L"controller_output_deadzone", L"acquire_confidence", L"retain_confidence"}) {
            for (const auto* bad : {L"nan", L"inf", L"-0.01", L"5", L"", L"0.2junk"})
                require(!applyLiveSetting(c, field, bad), "Batch3 invalid live setting accepted");
            require(applyLiveSetting(c, field, L"0.125"), "Batch3 valid live setting rejected");
            require(applyLiveSetting(c, field, L"0"), "Batch3 zero boundary rejected");
        }
        require(!applyLiveSetting(c,L"controller_output_deadzone",L"0.500001"), "stick compensation range exceeded");
        require(!applyLiveSetting(c,L"retain_confidence",L"1.000001"), "retention range exceeded");
        require(applyLiveSetting(c,L"assistance_x",L"4") && c.assistanceX==4, "axis maximum rejected");
        // Invalid CLI must fail before capture/model/provider initialization.
        for (const auto* field : {L"--assistance-x", L"--assistance-y", L"--controller-output-deadzone", L"--acquire-confidence", L"--retain-confidence"}) {
            wchar_t executable[]=L"test", headless[]=L"--headless", bad[]=L"nan";
            std::wstring option(field);
            wchar_t* args[]{executable,headless,option.data(),bad};
            require(runHeadless(4,args)==2,"invalid Batch3 CLI escaped preflight");
            require(runHeadless(3,args)==2,"missing Batch3 CLI value escaped preflight");
        }
    }
    {
        AppConfig c; c.confidence=.2;c.acquireConfidence=.8;c.retainConfidence=.4;
        c.minimumTrackAge=1;c.lockPersistenceMs=0;c.occlusionHandling=false;c.lostTargetTimeoutMs=0;
        TargetSelector selector;
        auto now=aimTime(0);
        std::vector<Track> tracks{timedSubject(1,310,now)};
        tracks[0].detection.confidence=.7;
        require(!selector.select(tracks,640,640,c,now),"weak new target acquired");
        tracks[0].detection.confidence=.8;
        require(selector.select(tracks,640,640,c,now)->id==1,"acquisition threshold boundary failed");
        tracks[0].detection.confidence=.4;
        tracks.push_back(timedSubject(2,290,now));tracks[1].detection.confidence=.7;
        require(selector.select(tracks,640,640,c,now)->id==1,"weak challenger displaced retained identity");
        require(targetEligibleForOutput(tracks[0],c,now),"retention boundary blocked");
        tracks[0].detection.confidence=.399;
        require(!targetEligibleForOutput(tracks[0],c,now),"weak retained target authorized output");
        tracks[0].detection.confidence=.5;tracks[0].missed=1;
        require(!targetEligibleForOutput(tracks[0],c,now),"missed retention authorized output");
        tracks[0].missed=0;tracks[0].identityCertain=false;
        require(!targetEligibleForOutput(tracks[0],c,now),"uncertain retention authorized output");
        tracks[0].identityCertain=true;
        require(!targetEligibleForOutput(tracks[0],c,aimTime(3)),"stale retention authorized output");
        tracks.clear();selector.select(tracks,640,640,c,aimTime(3));
        tracks={timedSubject(1,310,aimTime(4))};tracks[0].detection.confidence=.5;
        require(!selector.select(tracks,640,640,c,aimTime(4)),"cleared identity kept retention privilege");
        c.retainConfidence=.9;
        require(acquisitionConfidence(c)==.9,"acquisition bypassed stronger retention floor");
        c.retainConfidence=0;c.acquireConfidence=0;c.confidence=.6;
        require(acquisitionConfidence(c)==.6 && retentionConfidence(c)==.6,"legacy confidence defaults changed");
        c.retainConfidence=.3;c.acquireConfidence=.4;
        require(acquisitionConfidence(c)==.6 && retentionConfidence(c)==.6,"detector floor bypassed");
    }
    {
        AppConfig c;c.confidence=.2;c.acquireConfidence=.8;c.retainConfidence=.4;
        c.minimumTrackAge=1;c.occlusionHandling=true;c.occlusionGraceMs=100;c.lostTargetTimeoutMs=200;
        TargetSelector selector;
        std::vector<Track> tracks{timedSubject(1,310,aimTime(0))};
        selector.select(tracks,640,640,c,aimTime(0));
        tracks={timedSubject(2,310,aimTime(.01))};tracks[0].detection.confidence=.6;
        require(!selector.select(tracks,640,640,c,aimTime(.01)),"new recovery ID used retention threshold");
        tracks[0].detection.confidence=.8;
        require(selector.select(tracks,640,640,c,aimTime(.01))->id==2,"qualified recovery ID failed");
        for(double weak:{.3,.6,.799}) {
            TargetSelector ambiguous;
            tracks={timedSubject(1,310,aimTime(1))};
            ambiguous.select(tracks,640,640,c,aimTime(1));
            tracks={timedSubject(2,309,aimTime(1.01)),timedSubject(3,311,aimTime(1.01))};
            tracks[1].detection.confidence=weak;
            require(!ambiguous.select(tracks,640,640,c,aimTime(1.01)),
                "weak plausible neighbor disappeared from recovery ambiguity evidence");
        }
    }
    auto base=[] {
        AppConfig c;c.movementEnabled=true;c.deadzone=0;c.anchorSettleRadius=0;c.movementSmoothing=0;
        c.movementGain=.2;c.outputRate=60;c.maxStep=10000;c.maxSpeed=100000;
        c.predictLost=false;c.anchorMode=0;c.anchorOffsetX=0;c.anchorOffsetY=0;return c;
    };
    auto sample=[&](AppConfig c) {
        AimReport latest;
        AimOutputScheduler worker([&](const AimReport& r,const AimInputs&,const AppConfig&,AimOutputStatus&){if(r.active)latest=r;return true;},
            [](const AppConfig&){AimInputs i;i.down=true;return i;});
        auto generation=worker.configure(c);
        worker.publish(timedSubject(1,390,aimTime(0),390),640,640,aimTime(0),generation);
        worker.pollOnce(aimTime(0));
        const auto emitted = latest; // Keep the active sample; teardown sends neutral.
        worker.stop();
        return emitted;
    };
    {
        auto c=base();auto original=sample(c);
        std::cout << "Batch3 baseline axes " << original.dx << "," << original.dy << std::endl;
        require(original.dx==20&&original.dy==20,"legacy axis output changed");
        c.assistanceX=0;auto vertical=sample(c);
        require(vertical.dx==0&&vertical.dy==original.dy,"horizontal zero altered vertical output");
        c.assistanceX=1;c.assistanceY=0;auto horizontal=sample(c);
        require(horizontal.dx==original.dx&&horizontal.dy==0,"vertical zero altered horizontal output");
        c.assistanceX=2;auto stronger=sample(c);
        require(stronger.dx>horizontal.dx&&stronger.dx<=100&&stronger.dy==0,"axis multiplier failed error cap");
        c.assistanceX=c.assistanceY=4;c.maxStep=3;auto capped=sample(c);
        require(std::abs(capped.dx)<=3&&std::abs(capped.dy)<=3,"axis multiplier bypassed step cap");
        c.maxStep=1000;c.maxSpeed=60;auto speed=sample(c);
        require(std::hypot(speed.dx,speed.dy)<=1,"axis multiplier bypassed radial speed cap");
        c=base();c.controllerOutputDeadzone=.5;auto mouse=sample(c);
        require(mouse.dx==original.dx&&mouse.dy==original.dy,"controller compensation altered mouse output");
    }
    {
        auto c=base();c.outputTransport=vaporlock::hardware::kVirtualXboxTransport;
        c.maxSpeed=6000;c.controllerOutputStrength=1;
        auto legacy=sample(c);c.controllerOutputDeadzone=.2;auto boosted=sample(c);
        require(boosted.stickX>legacy.stickX&&boosted.stickY<legacy.stickY,"controller compensation did not boost");
        require(std::abs(boosted.stickX+boosted.stickY)<=1,"controller compensation altered direction");
        require(std::hypot(boosted.stickX,boosted.stickY)<=32768,"controller compensation exceeded radial bound");
        c.assistanceX=c.assistanceY=0;auto neutral=sample(c);
        require(neutral.stickX==0&&neutral.stickY==0,"zero output became nonneutral");
        c.assistanceX=c.assistanceY=1e-310;auto tiny=sample(c);
        require(tiny.stickX>0&&tiny.stickY<0&&std::hypot(tiny.stickX,tiny.stickY)<=32768,"tiny vector overflowed compensation");
        c=base();c.outputTransport=vaporlock::hardware::kVirtualXboxTransport;c.controllerOutputStrength=64;c.maxSpeed=6000;
        auto diagonal=sample(c);
        require(diagonal.stickX==32767&&diagonal.stickY==-32767,"default mapping lost independent-axis legacy clamp");
        c.controllerOutputDeadzone=.5;auto radial=sample(c);
        require(std::hypot(radial.stickX,radial.stickY)<=32768,"enabled compensation failed radial saturation");
        c.controllerOutputStrength=0;auto off=sample(c);
        require(off.stickX==0&&off.stickY==0,"zero controller strength bypassed");
    }
    {
        auto trajectory=[&](int rate) {
            auto c=base();c.assistanceX=.5;c.assistanceY=1.5;c.outputRate=rate;
            LONG x=0,y=0;
            AimOutputScheduler worker([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){x+=r.dx;y+=r.dy;return true;},
                [](const AppConfig&){AimInputs i;i.down=true;return i;});
            const auto gen=worker.configure(c);
            worker.publish(timedSubject(1,390,aimTime(0),390),640,640,aimTime(0),gen);
            for(int tick=1;tick<=rate/10;++tick)worker.pollOnce(aimTime(static_cast<double>(tick)/rate));
            return std::pair<LONG,LONG>{x,y};
        };
        const auto reference=trajectory(60);
        for(int rate:{120,240}) {
            const auto actual=trajectory(rate);
            require(std::abs(actual.first-reference.first)<=2&&std::abs(actual.second-reference.second)<=2,
                "axis multipliers lost elapsed-time normalization");
        }
    }
#ifndef VAPORLOCK_FREE_EDITION
    {
        auto c=base();c.assistanceX=c.assistanceY=0;c.motionCompensationEnabled=true;
        c.motionCompensationMode=0;c.motionCompensationActivationMode=2;c.motionCompensationHorizontal=-600;
        auto output=sample(c);
        require(output.dx>0&&output.dy==0,"zero assistance unexpectedly disabled independent compensation");
    }
#endif
    {
        auto c=base();c.outputTransport=vaporlock::hardware::kVirtualXboxTransport;c.controllerOutputDeadzone=.2;
        bool held=true;AimReport last;
        AimOutputScheduler worker([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){last=r;return true;},
            [&](const AppConfig&){AimInputs i;i.down=held;return i;});
        auto gen=worker.configure(c);
        worker.publish(timedSubject(1,390,aimTime(0),390),640,640,aimTime(0),gen);worker.pollOnce(aimTime(0));
        require(last.stickX>0,"controller release fixture failed");
        held=false;worker.pollOnce(aimTime(.001));
        require(last.stickX==0&&last.stickY==0,"release compensation failed neutral");
        held=true;worker.pollOnce(aimTime(.03));worker.clear();
        require(last.stickX==0&&last.stickY==0,"Stop compensation failed neutral");
        gen=worker.configure(c);worker.publish(timedSubject(1,390,aimTime(1)),640,640,aimTime(1),gen);
        worker.pollOnce(aimTime(1));worker.pollOnce(aimTime(2));
        require(last.stickX==0&&last.stickY==0,"expiry compensation failed neutral");
        c.assistanceX=0;gen=worker.configure(c);
        worker.publish(timedSubject(1,390,aimTime(3),390),640,640,aimTime(3),gen);worker.pollOnce(aimTime(3));
        require(last.stickX==0&&last.stickY<0,"live zero-axis left horizontal output");
        c.retainConfidence=.95;gen=worker.configure(c);worker.pollOnce(aimTime(3.01));
        require(last.stickX==0&&last.stickY==0,"raised retention kept published output");
        worker.publish(timedSubject(1,390,aimTime(3.02)),640,640,aimTime(3.02),gen);worker.pollOnce(aimTime(3.02));
        require(last.stickX==0&&last.stickY==0,"final output gate bypassed retention");
    }
    std::cout << "Batch3 native contracts passed" << std::endl;
}
