// Included after App; uses the shipping renderer and real frame/simulation clock.
void runFpsBenchmark(App& app,int quality){
    app.selectQuality(quality);app.settings.frameCap=5;app.notifyUntil=0;
    glfwSwapInterval(0);SetTargetFPS(0);SetWindowTitle("Vapor Lock | 1920 x 1080 FPS measurement");
    using Finish=void(__stdcall*)();auto gpuFinish=reinterpret_cast<Finish>(rlGetProcAddress("glFinish"));
    app.frame(); // Prepare and show this process's window without foreground capture.
    PollInputEvents();app.frame(); // Settle the size callbacks before validating the first sample.
    auto require1080p=[&]{
        if(GetScreenWidth()!=1920||GetScreenHeight()!=1080||GetRenderWidth()!=1920||GetRenderHeight()!=1080||
            (app.viewport.id&&(app.viewport.texture.width!=1920||app.viewport.texture.height!=1080))||
            app.presentation.texture.width!=1920||app.presentation.texture.height!=1080)
            throw std::runtime_error("FPS size mismatch: client "+std::to_string(GetScreenWidth())+"x"+std::to_string(GetScreenHeight())+
                ", framebuffer "+std::to_string(GetRenderWidth())+"x"+std::to_string(GetRenderHeight())+
                ", scene "+std::to_string(app.presentation.texture.width)+"x"+std::to_string(app.presentation.texture.height));
        if(WindowShouldClose()||IsWindowMinimized())throw std::runtime_error("FPS window closed or minimized; measurement cancelled.");
    };
    require1080p();const auto& preset=graphicsPreset(quality);
    std::ostringstream report;report<<std::setprecision(8);
    report<<"{\n\"quality\":\""<<preset.name<<"\",\"model\":\""<<jsonEscape(app.character.filename)<<"\",\"model_content_id\":\""<<app.modelKey<<"\","
        <<"\n\"resolution\":[1920,1080],\"render_scale\":1,\"app_limiter\":0,\"requested_swap_interval\":0,\"warmup_seconds_per_bay\":2,\"measurement_seconds_per_bay\":8,"
        <<"\n\"requested_msaa\":"<<preset.samples<<",\"actual_msaa\":"<<app.sceneAA.samples<<",\"shadow_size\":"<<app.shadows.requested<<",\"shadow_ready\":"<<(app.shadows.ready?"true":"false")
        <<",\"shadow_filter_taps\":"<<(preset.shadowSize?(preset.shadowRadius*2+1)*(preset.shadowRadius*2+1):0)<<",\"surface_detail\":"<<(preset.detail?"true":"false")<<",\"world_glow\":"<<(preset.glow?"true":"false")
        <<",\"fov\":"<<app.settings.fov<<",\"weapon\":"<<(app.settings.weapon?"true":"false")<<",\"bays\":[";
    size_t totalFrames=0;double totalSeconds=0;std::vector<double> allFrames;std::array<double,6> stages{};double boxes=0,gpuMs=0;unsigned long long gpuFrames=0;
    for(int bay=0;bay<BayCount;bay++){
        app.active=bay;app.position={BayCenters[bay],1.62f,-4.5f};app.pitch=-1.2f;
        if(occupiedBay(app.position)!=bay)throw std::runtime_error("Benchmark must occupy the measured bay with its targets loaded.");
        for(auto& b:app.bays)b.reset();double phaseStart=GetTime();
        auto draw=[&]{app.yaw=9*std::sin(float(GetTime()-phaseStart)*.7f);app.frame();require1080p();};
        while(GetTime()-phaseStart<2)draw();
        if(!ownWindowIsVisibleOnScreen(GetWindowHandle()))throw std::runtime_error("FPS measurement window is not visible.");
        if(gpuFinish)gpuFinish();app.profile.collect();app.profile.gpuSum=0;app.profile.gpuCount=0;std::vector<double> frames;frames.reserve(20000);double start=GetTime();
        do{double before=GetTime();draw();frames.push_back((GetTime()-before)*1000);if(app.profile.enabled){for(size_t i=0;i<stages.size();i++)stages[i]+=app.profile.cpu[i];boxes+=app.profile.boxes;}}while(GetTime()-start<8);
        if(gpuFinish)gpuFinish();double seconds=GetTime()-start;
        app.profile.collect();gpuMs+=app.profile.gpuSum;gpuFrames+=app.profile.gpuCount;
        totalFrames+=frames.size();totalSeconds+=seconds;allFrames.insert(allFrames.end(),frames.begin(),frames.end());
        std::sort(frames.begin(),frames.end());auto pct=[&](double p){return frames[size_t((frames.size()-1)*p)];};
        if(bay)report<<",";report<<"\n{\"name\":\""<<BayNames[bay]<<"\",\"targets\":"<<app.bays[bay].settings.count<<",\"frames\":"<<frames.size()<<",\"seconds\":"<<seconds<<",\"average_fps\":"<<frames.size()/seconds<<",\"p50_frame_ms\":"<<pct(.5)<<",\"p99_frame_ms\":"<<pct(.99)<<"}";
        appLog<<"BENCHMARK "<<preset.name<<" / "<<BayNames[bay]<<": "<<frames.size()/seconds<<" FPS"<<std::endl;
    }
    std::sort(allFrames.begin(),allFrames.end());
    report<<"\n],\"active_bay_targets_only\":true,\"range_character_assets\":1,\"average_fps\":"<<totalFrames/totalSeconds<<",\"total_frames\":"<<totalFrames<<",\"measured_seconds\":"<<totalSeconds<<",\"p99_frame_ms\":"<<allFrames[size_t((allFrames.size()-1)*.99)];
    if(app.profile.enabled){report<<",\"host_stage_ms\":{\"update\":"<<stages[0]/totalFrames<<",\"displays\":"<<stages[1]/totalFrames<<",\"shadow\":"<<stages[2]/totalFrames<<",\"world\":"<<stages[3]/totalFrames<<",\"compose\":"<<stages[4]/totalFrames<<",\"present\":"<<stages[5]/totalFrames<<"},\"box_draws_per_frame\":"<<boxes/totalFrames<<",\"character_meshes\":"<<app.character.model.meshCount<<",\"gpu_scope_elapsed_ms\":"<<(gpuFrames?gpuMs/gpuFrames:0)<<",\"gpu_query_frames\":"<<gpuFrames;}
    report
        <<",\"screenshots_during_test\":false,\"note\":\"Shipping Release renderer, moving targets and camera sweeps. GPU drained at boundaries. No OS input, screenshots or per-frame disk writes. Driver/compositor policy is not overridden.\"}\n";
    std::string name=preset.name;std::transform(name.begin(),name.end(),name.begin(),[](unsigned char c){return char(std::tolower(c));});
    writeText(std::string("reports/")+(app.profile.enabled?"profile":"fps")+"-1080p-"+name+".json",report.str());
}
