// Native production selector and output scheduler; synthetic clock and no device.
static void runFeatureBatch5Tests() {
    AppConfig c; c.anchorOffsetY=0;c.anchorMode=0;c.minimumTrackAge=1;
    require(parseExclusionZones(L"1 300 300 21 21",c.exclusionZones),"zone parser valid");
    for(const auto* bad:{L"",L"17",L"1 0 0 0 1",L"1 -1 0 2 2",L"1 999999 0 2 1",L"1 0 0 2.5 3",L"0 junk",L"1 0 0 2147483648 1"}) {
        require(!parseExclusionZones(bad,c.exclusionZones),"invalid exclusion wire accepted");
        require(c.exclusionZones.size()==1&&c.exclusionZones[0].x==300,"failed parse changed zone list");
        wchar_t exe[]=L"test",headless[]=L"--headless",option[]=L"--exclusion-zones";
        std::wstring value(bad);wchar_t* args[]{exe,headless,option,value.data()};
        require(runHeadless(4,args)==2,"invalid zone CLI escaped preflight");
    }
    for(auto point:std::vector<std::pair<double,double>>{{300,300},{320,320},{310.5,310.5}})
        require(pointExcluded(point.first,point.second,c,640,640),"inclusive edge mismatch");
    require(!pointExcluded(320.001,310,c,640,640),"right edge exceeds pixel width");
    require(!pointExcluded(310,320.001,c,640,640),"bottom edge exceeds pixel height");
    require(!pointExcluded(310,310,c,300,300),"outside zone active after capture resize");
    require(pointExcluded(310,310,c,311,311),"partial zone lost inside pixels");
    auto now=aimTime(0);auto target=timedSubject(1,280,now,280);
    require(targetExcluded(target,c,640,640),"observed aiming point not excluded");
    target.hasStableAnchor=true;target.stableAnchorX=390;target.stableAnchorY=390;
    require(targetExcluded(target,c,640,640),"smoothing bypassed observation zone");
    target.observedBox={360,360,60,60};target.stableAnchorX=310;target.stableAnchorY=310;
    require(targetExcluded(target,c,640,640),"stable anchor bypassed zone");
    target.hasStableAnchor=false;target.detection.box=target.observedBox={290,290,60,60};
    c.classPolicies[0]={0,1,0,0};
    require(!targetExcluded(target,c,640,640),"class-aware top anchor not respected");c.classPolicies.clear();
    {
        TargetSelector selector;std::vector<Track> tracks{timedSubject(1,270,now,270)};
        AppConfig empty=c;empty.exclusionZones.clear();
        require(selector.select(tracks,640,640,empty,now)->id==1,"legacy selection failed");
        require(selector.toggleManualLock(),"manual lock fixture failed");
        require(!selector.select(tracks,640,640,c,now),"excluded manual/retained lock survived");
        require(!selector.manuallyLocked()&&!tracks[0].selected,"excluded indicators retained");
        tracks={timedSubject(2,350,now,270)};
        require(selector.select(tracks,640,640,c,now)->id==2,"valid target blocked after exclusion");
    }
    {
        // An excluded neighboring observation is still identity ambiguity evidence.
        AppConfig x=c;x.occlusionHandling=true;x.occlusionGraceMs=100;x.lostTargetTimeoutMs=200;
        x.exclusionZones={{340,0,2,640}};
        TargetSelector selector;std::vector<Track> tracks{timedSubject(1,310,aimTime(0))};
        require(!selector.select(tracks,640,640,x,aimTime(0)),"boundary selection allowed");
        tracks={timedSubject(1,309,aimTime(0))};
        require(selector.select(tracks,640,640,x,aimTime(0))->id==1,"recovery fixture acquisition");
        tracks={timedSubject(2,308,aimTime(.01)),timedSubject(3,310,aimTime(.01))};
        require(!selector.select(tracks,640,640,x,aimTime(.01)),"excluded neighbor removed from ambiguity evidence");
    }
    for(bool predicted:{false,true}) {
        AppConfig x=c;x.movementEnabled=true;x.motionCompensationEnabled=false;x.deadzone=0;x.anchorSettleRadius=0;
        x.predictLost=predicted;x.predictionLeadMs=10;x.predictionLimitMs=100;x.predictionMaxDistance=80;
        x.exclusionZones=predicted?std::vector<ExclusionZone>{{430,300,10,40}}:std::vector<ExclusionZone>{{400,300,50,40}};
        int moved=0;bool snap=false;
        AimOutputScheduler scheduler([&](const AimReport&r,const AimInputs&,const AppConfig&,AimOutputStatus&){if(r.dx||r.dy)++moved;return true;},
            [](const AppConfig&){AimInputs i;i.down=true;return i;},
            [&](const AppConfig&,bool,bool inside,AimTime,AimOutputStatus&){snap=inside;});
        auto gen=scheduler.configure(x);CaptureFrameIdentity identity{1,1,1,gen};
        auto t=timedSubject(1,390,now);t.vx=1000;t.motionConfidence=1;
        scheduler.publish(t,640,640,now,gen,{},identity);scheduler.pollOnce(now);
#ifdef VAPORLOCK_FREE_EDITION
        if(predicted) {
            require(scheduler.visualSnapshot(identity,now).eligible,"Free prediction gate changed existing entitlement");
            scheduler.stop(); continue;
        }
#endif
        require(!scheduler.visualSnapshot(identity,now).eligible,"excluded output visual remained active");
        require(moved==0&&!snap&&!scheduler.status().active,"excluded target caused output/event");
        scheduler.stop();
    }
    c.exclusionZones.clear();
    require(!targetExcluded(target,c,640,640),"empty list changed default");
    require(parseExclusionZones(L"0",c.exclusionZones)&&c.exclusionZones.empty(),"empty wire rejected");
}
