#include <set>
static void runCrosshairReferenceTests() {
    using namespace vaporlock::crosshair;
    const auto r=screenReference(0,0,1920,1080,740,220,640,640);
    require(r.x==220&&r.y==320&&r.inside,"screen to offset crop conversion");
    const auto negative=screenReference(-1920,-1080,1921,1081,-1180,-860,640,640);
    require(negative.x==220.5&&negative.y==320.5,"negative and odd geometry");
    require(!screenReference(0,0,1920,1080,0,0,100,100).inside,"outside reference clamped");
    Template t;t.geometry={1,0,0,128,128,0,0,128,128};t.x=t.y=64;
    std::vector<std::pair<int,int>> patterns[3];
    patterns[0]={{0,0}};
    for(int n=-3;n<=3;++n){patterns[1].push_back({n,n});if(n)patterns[1].push_back({n,-n});}
    patterns[2]={{-3,-1},{-2,-1},{-1,-1},{-1,0},{-1,1},{0,1},{1,1},{2,1},{2,2}};
    int checks=0;double worst=0;
    for(const auto& pattern:patterns) {
        t.samples.clear();
        std::set<std::pair<int,int>> ink(pattern.begin(),pattern.end()),border;
        for(auto [x,y]:ink){t.samples.push_back({x,y,240,240,240,1});for(auto [dx,dy]:std::array<std::pair<int,int>,4>{{{-1,0},{1,0},{0,-1},{0,1}}})if(!ink.count({x+dx,y+dy}))border.insert({x+dx,y+dy});}
        for(auto [x,y]:border)t.samples.push_back({x,y,240,240,240,0});
        auto frame=[&](bool draw,int dx=0,bool duplicate=false) {
            std::vector<std::uint8_t> pixels(128*128*4,20);
            auto stamp=[&](int at){for(auto [x,y]:ink){auto offset=((64+y)*128+at+x)*4;pixels[offset]=pixels[offset+1]=pixels[offset+2]=240;}};
            if(draw)stamp(64+dx);if(duplicate)stamp(76);return pixels;
        };
        Matcher matcher;Reference found;auto fallback=screenReference(0,0,128,128,0,0,128,128);
        for(int n=1;n<=3;++n) {
            auto now=aimTime(n*.04);auto pixels=frame(true,2);
            const auto start=Clock::now();found=matcher.update(t,t.geometry,pixels,128,128,0,0,1,1,n,now,now,fallback);
            worst=std::max(worst,std::chrono::duration<double,std::milli>(Clock::now()-start).count());
            require(found.detected==(n==3),"three distinct-frame acquisition");++checks;
        }
        require(found.x==66&&found.y==64,"calibrated custom aim point");
        require(found.usable(aimTime(.219))&&!found.usable(aimTime(.22)),"reference age expiry");
        auto duplicate=matcher.update(t,t.geometry,frame(true,2),128,128,0,0,1,1,3,aimTime(.12),aimTime(.16),fallback);
        require(!duplicate.detected,"duplicate frame renewed lock");
        matcher.reset();
        for(int n=1;n<=3;++n){auto now=aimTime(n*.04);found=matcher.update(t,t.geometry,frame(true,0,true),128,128,0,0,1,1,n,now,now,fallback);require(!found.detected,"ambiguous replica accepted");}
        matcher.reset();
        for(int n=1;n<=3;++n){auto now=aimTime(n*.04);found=matcher.update(t,t.geometry,frame(true),128,128,0,0,1,1,n,now,now,fallback);}
        auto now=aimTime(.16);found=matcher.update(t,t.geometry,frame(false),128,128,0,0,1,1,4,now,now,fallback);
        require(!found.detected&&found.x==64,"disappearance retained offset");
        auto geometry=t.geometry;geometry[3]++;
        found=matcher.update(t,geometry,frame(true),128,128,0,0,1,1,5,now,now,fallback);
        require(!found.detected&&std::string(found.status)=="recalibrate","geometry mismatch accepted");
    }
    Template parsed;
    require(!parse(L"1 1 0 0 128 128 0 0 128 128 64 64 129",parsed),"oversize count accepted");
    require(!parse(std::wstring(9000,L'1'),parsed),"oversize wire accepted");
    AppConfig config;config.movementEnabled=true;config.outputMaxObservationAgeMs=500;config.deadzone=0;config.anchorSettleRadius=0;
    long moved=0;int events=0;
    AimOutputScheduler output([&](const AimReport& report,const AimInputs&,const AppConfig&,AimOutputStatus&){moved+=std::abs(report.dx)+std::abs(report.dy);return true;},
        [](const AppConfig&){AimInputs i;i.down=true;return i;},
        [&](const AppConfig&,bool,bool inside,AimTime,AimOutputStatus&){if(inside)++events;});
    auto generation=output.configure(config);auto track=timedSubject(1,390,aimTime(0));
    auto ref=screenReference(0,0,640,640,0,0,640,640);ref.detected=true;ref.x=200;ref.matched=aimTime(0);ref.status="tracking";
    output.publish(track,640,640,aimTime(0),generation,{}, {1,1,1,generation},ref);
    output.pollOnce(aimTime(.01));require(moved>0,"fresh reference denied output");
    auto previous=moved;output.pollOnce(aimTime(.101));require(moved==previous&&!output.status().insideSnap,"expired reference allowed output");
    require(!output.visualSnapshot({1,1,1,generation},aimTime(.101)).eligible,"expired reference allowed guide");
    track.lastUpdate=aimTime(.11);output.publish(track,640,640,aimTime(.11),generation,{}, {1,1,2,generation},ref);
    output.pollOnce(aimTime(.12));require(moved==previous,"fresh target renewed old reference");
    ref.detected=false;ref.inside=false;track.lastUpdate=aimTime(.13);
    output.publish(track,640,640,aimTime(.13),generation,{}, {1,1,3,generation},ref);output.pollOnce(aimTime(.14));
    require(moved==previous&&!output.status().insideSnap,"outside reference allowed target output");
    ref.inside=true;ref.x=320;track.lastUpdate=aimTime(.15);
    output.publish(track,640,640,aimTime(.15),generation,{}, {1,1,4,generation},ref);
    require(output.visualSnapshot({1,1,4,generation},aimTime(.151)).eligible,"fresh fallback missing");
    ref.x=200;output.observeReference(ref,{1,1,5,generation},generation);
    require(!output.visualSnapshot({1,1,4,generation},aimTime(.152)).eligible,"changed reference retained old decision during inference");
    config.frameReference=r;
    require(aimingReference(640,640,config)==std::pair<double,double>{220,320},"native reference diverges");
    auto overlay=overlayMetadata(640,640,{}, {},false,config,740,220,AimClock::now());
    require(overlay.find("\"reference_x\":220.000")!=std::string::npos,"overlay reference diverges");
    std::cout<<"Crosshair patterns dot/X/custom, freshness, geometry, scheduler and overlay PASS; max match ms="<<worst<<std::endl;
}
