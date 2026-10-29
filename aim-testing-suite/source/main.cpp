#include "character.hpp"
#include "range.hpp"
#include "range_weapon.hpp"
#include "quality.hpp"
#include "shadows.hpp"
#include "training.hpp"
#include "branding.hpp"
#include "neon.hpp"
#include "render_profile.hpp"
#include "world_batch.hpp"
#include "bundled_assets.hpp"
#define GLFW_INCLUDE_NONE
#include "GLFW/glfw3.h"
#include <cstdarg>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <chrono>
using namespace suite;
namespace {
constexpr Color Ink=BrandInk,Panel=BrandPanel,Card=BrandCard,Line{43,61,82,255};
constexpr Color Text=BrandText,Muted=BrandMuted,Violet=BrandViolet,Cyan=BrandIce,Amber{244,211,94,255};
constexpr Color BayColors[]={BrandIce,BrandBlue,BrandViolet,Color{235,128,198,255}};
Font uiFont{};std::ofstream appLog;
void logger(int level,const char* message,va_list args){if(level<LOG_INFO)return;char buffer[2048];vsnprintf(buffer,sizeof(buffer),message,args);if(appLog)appLog<<buffer<<std::endl;}
std::string num(double v,int decimals=1){std::ostringstream o;o<<std::fixed<<std::setprecision(decimals)<<v;return o.str();}
void text(const std::string& s,float x,float y,float size=20,Color c=Text){DrawTextEx(uiFont,s.c_str(),{x,y},size,.5f,c);}
void centered(const std::string& s,Rectangle r,float size,Color c=Text){auto m=MeasureTextEx(uiFont,s.c_str(),size,.5f);text(s,r.x+(r.width-m.x)/2,r.y+(r.height-m.y)/2,size,c);}
void fitText(std::string s,float x,float y,float width,float size,Color c=Text){while(s.size()>3&&MeasureTextEx(uiFont,s.c_str(),size,.5f).x>width)s.pop_back();text(s,x,y,size,c);}
bool button(Rectangle r,const std::string& title,Color accent=Cyan){
    bool hover=CheckCollisionPointRec(GetMousePosition(),r);if(hover)neonBorder(r,accent,.8f,11);
    DrawRectangleRounded(r,.12f,6,hover?Color{23,44,69,255}:Card);
    DrawRectangleLinesEx(r,1,hover?accent:Line);centered(title,r,19,hover?accent:Text);return hover&&IsMouseButtonPressed(MOUSE_BUTTON_LEFT);
}
struct Stats {
    int shots=0,hits=0;double time=0,visible=0,onTarget=0,errorSum=0;float error=-1;int hover=-1;std::vector<float> frames;
    Acquisition acquisition;
    void clear(){*this={};}
    float accuracy()const{return shots?100.0f*hits/shots:0;}
    float percentile(float q)const{if(frames.empty())return 0;auto copy=frames;std::sort(copy.begin(),copy.end());return copy[size_t((copy.size()-1)*q)];}
};
struct Bay {
    Settings settings,runSettings;Clock clock;Stats stats;int page=0;bool running=false,finished=false,dirty=true;
    std::array<double,12> respawn{};std::array<double,12> flashed{};std::array<int,12> generations{};
    std::string csv,stem,key,events;double nextSample=0,readyLeft=0;int reportNumber=0,runWidth=0,runHeight=0;
    void reset(){clock={};stats.clear();respawn.fill(0);flashed.fill(-10);generations.fill(0);readyLeft=0;finished=false;nextSample=0;dirty=true;}
};
struct ShotHit {enum Kind{Empty,World,Board,Target}kind=Empty;float distance=200;int bay=-1,id=-1;Vector3 point{};Vector2 pixel{};};
struct Impact{Vector3 point;double until;bool target;};
struct App {
    Settings settings;std::array<Bay,BayCount>bays;Character character;RangeWeapon rifle;Shader shader{},environmentShader{};Model cube{};SceneAA sceneAA;RangeShadow shadows;RangeNeon neon;RenderProfile profile;std::array<WorldBatch,BayCount+1> environment,emissive;bool buildingWorld=false;int buildGroup=0;
    std::array<RenderTexture2D,BayCount>boards{},signs{};std::array<RenderTexture2D,3> distancePlates{};RenderTexture2D viewport{},presentation{};
    std::vector<fs::path>models;int modelIndex=0,active=0;Camera3D camera{};Vector3 position{24,1.62f,-6.5f};float yaw=0,pitch=-1.2f;
    bool captured=false,menu=false,quit=false,testing=false,startupCheck=false,benchmarking=false,shown=false,captureNext=false,wasFocused=false,qualityOpen=false,menuActionsBlocked=false;int qualityCursor=DefaultQuality;
    inline static App* inputOwner=nullptr;GLFWmousebuttonfun previousButtonHandler=nullptr;int pendingClicks=0,fireCount=0;
    int testFrame=0,startupFrames=0,shaderCamera=0,shaderFog=0,shaderExposure=0,shaderTint=0,shotSequence=0,hoveredBay=-1,appliedCap=-1;Control hoveredControl;
    std::string notification,testModel="SWAT.glb",modelKey;double notifyUntil=0,lastBoards=-100,lastShot=-100,hitMarker=-100,elapsed=0,frameAverage=8.3;float kick=0;
    std::vector<BoundingBox>solids;std::vector<Impact>impacts;
    uint64_t pausedTestTicks=0;int websiteRequests=0,menuFocus=-1;
    void notify(const std::string& message){notification=message;notifyUntil=GetTime()+4;appLog<<"NOTICE: "<<message<<std::endl;}
    void release(){if(captured){EnableCursor();captured=false;}}
    void queueClick(int button,int action,bool focused){if(button==GLFW_MOUSE_BUTTON_LEFT&&action==GLFW_PRESS&&captured&&!menu&&focused)pendingClicks=std::min(64,pendingClicks+1);}
    void capture(){if(!testing&&!startupCheck&&!benchmarking&&IsWindowFocused()){DisableCursor();captured=true;GetMouseDelta();applyMouse();}}
    void visitWebsite(){
        release();menu=true;++websiteRequests;
        if(testing||startupCheck||benchmarking){notify("Project source link validated without opening a browser.");return;}
        notify(openBrandWebsite(GetWindowHandle())?"GitHub project opened in your browser. The range remains paused.":"Could not open the browser. Visit github.com/VaporUnlocked/VaporLock manually.");
    }
    bool menuButton(Rectangle rect,const std::string& label,int id,Color accent=Cyan){
        if(menuFocus==id)neonBorder(rect,Cyan,.8f,11);
        bool clicked=button(rect,label,accent);
        if(menuFocus==id)DrawRectangleLinesEx({rect.x-3,rect.y-3,rect.width+6,rect.height+6},2,Cyan);
        return !menuActionsBlocked&&(clicked||(menuFocus==id&&IsWindowFocused()&&(IsKeyPressed(KEY_ENTER)||IsKeyPressed(KEY_SPACE))));
    }
    void applyMouse(){if(captured&&glfwRawMouseMotionSupported())glfwSetInputMode(glfwGetCurrentContext(),GLFW_RAW_MOUSE_MOTION,settings.rawMouse?GLFW_TRUE:GLFW_FALSE);}
    void rebuildSolids(){solids=architecture();for(int i=0;i<BayCount;i++){auto cover=bayCover(bays[i].settings,i);solids.insert(solids.end(),cover.begin(),cover.end());}}
    void refreshModels(){models=findModels();modelIndex=0;for(size_t i=0;i<models.size();i++)if(utf8(models[i].filename())==settings.model)modelIndex=int(i);}
    void updateModelKey(){modelKey=comparisonId(std::string_view(reinterpret_cast<const char*>(character.poseData.bytes.data()),character.poseData.bytes.size()));}
    std::string contextKey(int index)const{
        const auto& s=bays[index].settings;std::ostringstream out;out<<std::setprecision(9)<<"range-v7-active-batched,"<<modelKey<<","<<index<<","<<s.motion<<","<<s.count<<","<<s.speed<<","<<s.distance<<","<<s.spread<<","<<s.seed<<","<<s.height<<","<<s.facing<<","<<s.duration<<","<<s.cover<<","<<s.hitResponse<<","<<s.respawnDelay<<","<<s.appearanceInterval<<","<<s.mixedStill<<","<<s.aimHeight<<","<<s.markers<<","<<settings.pose<<","<<settings.poseFrame<<","<<settings.fov<<","<<settings.sensitivity<<","<<settings.rawMouse<<","<<settings.mouseDpi<<","<<settings.light<<","<<settings.frameCap<<","<<settings.antialias<<","<<settings.quality<<","<<shadows.requested<<","<<shadows.radius<<","<<s.readySeconds<<","<<settings.crosshair<<","<<settings.weapon<<","<<settings.hud<<","<<sceneAA.samples<<","<<shadows.ready<<","<<glfwRawMouseMotionSupported()<<","<<GetScreenWidth()<<","<<GetScreenHeight();return comparisonId(out.str());
    }
    void save(){
        if(testing||startupCheck||benchmarking)return;
        try{saveSettings(settings);for(int i=0;i<BayCount;i++)saveSettings(bays[i].settings,"range-bay-"+std::to_string(i+1)+".ini");}
        catch(const std::exception& e){notify(e.what());}
    }
    void updateCamera(){camera={position,Vector3Add(position,forward(yaw,pitch)),{0,1,0},verticalFov(settings.fov,float(std::max(1,GetScreenWidth()))/std::max(1,GetScreenHeight())),CAMERA_PERSPECTIVE};}
    void setup(bool check,bool startup,const std::string& requested){
        testing=check;startupCheck=startup;settings=(testing||startup)?Settings{}:loadSettings();
        if(!requested.empty()){
            auto ext=utf8(pathFromUtf8(requested).extension());std::transform(ext.begin(),ext.end(),ext.begin(),[](unsigned char c){return char(std::tolower(c));});
            if(requested.find_first_of("/\\:")!=std::string::npos||ext!=".glb")throw std::runtime_error("Select a character using only its GLB filename.");
            settings.model=requested;settings.pose=-1;settings.poseFrame=0;
        }else if(testing)settings.model=testModel;
        testModel=settings.model;
        if(!fs::exists(local("range-bay-1.ini"))){settings.hud=true;settings.crosshair=true;settings.weapon=true;}
        for(int i=0;i<BayCount;i++){
            auto name="range-bay-"+std::to_string(i+1)+".ini";
            bays[i].settings=(!testing&&!startup&&fs::exists(local(name)))?loadSettings(name):bayDefaults(i);
            sanitizeBay(bays[i].settings);bays[i].reset();
        }
        SetTraceLogCallback(logger);SetConfigFlags(FLAG_WINDOW_RESIZABLE|FLAG_MSAA_4X_HINT|FLAG_WINDOW_HIDDEN);
        InitWindow((testing||startup||benchmarking)?1920:1440,(testing||startup||benchmarking)?1080:900,"Vapor Lock | Aim Testing Suite");SetWindowMinSize(1040,720);SetExitKey(KEY_NULL);
        if(!IsWindowReady())throw std::runtime_error("Could not create the range window.");
        // Windows otherwise constrains a resizable window to the current monitor's width.
        // Test windows alone use a fixed client size; normal play remains freely resizable.
        if(testing||startup||benchmarking)glfwSetWindowSizeLimits(glfwGetCurrentContext(),1920,1080,1920,1080);
        inputOwner=this;previousButtonHandler=glfwSetMouseButtonCallback(glfwGetCurrentContext(),[](GLFWwindow* window,int button,int action,int mods){
            auto* app=inputOwner;if(!app)return;if(app->previousButtonHandler)app->previousButtonHandler(window,button,action,mods);
            app->queueClick(button,action,glfwGetWindowAttrib(window,GLFW_FOCUSED)!=0);
        });
        if(!glfwRawMouseMotionSupported())settings.rawMouse=false;
        if(startup||benchmarking)glfwSetWindowAttrib(glfwGetCurrentContext(),GLFW_FOCUS_ON_SHOW,GLFW_FALSE);
        SetTargetFPS(benchmarking?0:testing?60:FrameCaps[settings.frameCap]);
        uiFont=LoadFontEx(utf8(local("assets/fonts/Interface.ttf")).c_str(),96,nullptr,0);
        if(!uiFont.texture.id)uiFont=GetFontDefault();SetTextureFilter(uiFont.texture,TEXTURE_FILTER_BILINEAR);
        shader=LoadShaderFromMemory(vertexShader,fragmentShader);
        if(shader.id==rlGetShaderIdDefault())throw std::runtime_error("The range lighting shader could not be loaded.");
        shaderCamera=GetShaderLocation(shader,"cameraPosition");shaderFog=GetShaderLocation(shader,"fogColor");shaderExposure=GetShaderLocation(shader,"exposure");shaderTint=GetShaderLocation(shader,"lightTint");
        environmentShader=LoadShaderFromMemory(vertexShader,fragmentShader);
        float detail=1;SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"surfaceColor"),&detail,SHADER_UNIFORM_FLOAT);
        cube=LoadModelFromMesh(GenMeshCube(1,1,1));cube.materials[0].shader=environmentShader;sceneAA.init();shadows.init();neon.init();if(profile.enabled)profile.init();
        refreshModels();if(models.empty())throw std::runtime_error("No local GLB characters were found.");
        auto chosen=std::find_if(models.begin(),models.end(),[&](const auto& p){return utf8(p.filename())==settings.model;});
        if(chosen==models.end()){if(!requested.empty()||testing)throw std::runtime_error("The requested local character is missing.");chosen=models.begin();}
        if(!character.load(*chosen,shader,settings))throw std::runtime_error(character.status);refreshModels();updateModelKey();
        for(int i=0;i<BayCount;i++)if(testing||!fs::exists(local("range-bay-"+std::to_string(i+1)+".ini")))
            bays[i].settings.facing=180.0f;
        for(int i=0;i<BayCount;i++){
            boards[i]=LoadRenderTexture(1024,1024);signs[i]=LoadRenderTexture(1536,320);
            if(!IsRenderTextureValid(boards[i])||!IsRenderTextureValid(signs[i]))throw std::runtime_error("Could not allocate range control screens.");
            SetTextureFilter(boards[i].texture,TEXTURE_FILTER_BILINEAR);SetTextureFilter(signs[i].texture,TEXTURE_FILTER_BILINEAR);
        }
        for(int i=0;i<3;i++){
            distancePlates[i]=LoadRenderTexture(512,256);if(!IsRenderTextureValid(distancePlates[i]))throw std::runtime_error("Could not allocate distance plates.");
            BeginTextureMode(distancePlates[i]);ClearBackground(Color{38,43,54,255});DrawRectangleLinesEx({5,5,502,246},6,Muted);
            centered(std::to_string((i+1)*10)+" M",{0,25,512,150},110);centered("FROM FIRING LINE",{0,174,512,52},24,Muted);EndTextureMode();
            SetTextureFilter(distancePlates[i].texture,TEXTURE_FILTER_BILINEAR);
        }
        rifle.load(shader);buildWorld();rebuildSolids();updateCamera();wasFocused=IsWindowFocused();
        appLog<<"STARTUP: range ready; "<<character.filename<<"; "<<character.triangles<<" triangles; "<<character.query.nodes.size()<<" BVH nodes"<<std::endl;
    }
    Entity entity(int bay,int id)const{
        if(bay!=occupiedBay(position))return {id,{},false};auto& b=bays[bay];auto e=rangeEntity(b.settings,bay,id,b.clock.time());
        if(b.settings.hitResponse&&b.generations[id]>0&&b.settings.motion==0)e.position.x=BayCenters[bay]+(unitHash(b.settings.seed,id,100+b.generations[id])*2-1)*b.settings.spread;
        if(b.clock.time()<b.respawn[id])e.active=false;return e;
    }
    ShotHit cast(Ray ray,bool onlyActive=false)const{
        ShotHit result;float d;
        for(auto& box:solids)if(rayBox(ray.position,ray.direction,box,d)&&d<result.distance){result.kind=ShotHit::World;result.distance=d;}
        if(ray.direction.y<-.000001f){d=-ray.position.y/ray.direction.y;if(d>=0&&d<result.distance){result.kind=ShotHit::World;result.distance=d;}}
        if(ray.direction.y>.000001f){d=(6-ray.position.y)/ray.direction.y;if(d>=0&&d<result.distance){result.kind=ShotHit::World;result.distance=d;}}
        for(int i=0;i<BayCount;i++){auto p=intersectPanel(ray,i);if(p.hit&&p.distance<=result.distance+.002f){result.kind=ShotHit::Board;result.distance=p.distance;result.bay=i;result.pixel=p.pixel;}}
        for(int i=0;i<BayCount;i++){
            if(i!=occupiedBay(position)||(onlyActive&&i!=active))continue;const auto& s=bays[i].settings;
            for(int id=0;id<s.count;id++){
                auto e=entity(i,id);if(!e.active)continue;
                if(!rayBox(ray.position,ray.direction,character.bounds(e,s),d)||d>=result.distance)continue;
                d=character.query.worldHit(ray,character.transform(e,s),result.distance);
                if(d<result.distance){result.kind=ShotHit::Target;result.distance=d;result.bay=i;result.id=id;}
            }
        }
        result.point=Vector3Add(ray.position,Vector3Scale(ray.direction,result.distance));return result;
    }
    void finish(int index,const std::string& reason){
        auto& b=bays[index];if(!b.running)return;b.running=false;b.finished=true;b.readyLeft=0;b.dirty=true;lastBoards=-100;std::ostringstream report;auto& m=b.stats;
        report<<"{\n \"schema\":4,\n \"comparison_id\":\""<<b.key<<"\",\n \"model_content_id\":\""<<modelKey<<"\",\n \"resolution\":["<<b.runWidth<<","<<b.runHeight<<"],\n \"bay\":\""<<BayNames[index]<<"\",\n \"model\":\""<<jsonEscape(character.filename)
            <<"\",\n \"end_reason\":\""<<reason<<"\",\n \"seconds\":"<<m.time<<",\n \"shots\":"<<m.shots<<",\n \"hits\":"<<m.hits
            <<",\n \"accuracy_percent\":"<<(m.shots?num(m.accuracy(),4):"null")<<",\n \"visible_aimpoint_seconds\":"<<m.visible
            <<",\n \"hits_per_second\":"<<(m.time?m.hits/m.time:0)<<",\n \"best_hit_streak\":"<<m.acquisition.bestStreak<<",\n \"successful_target_switches\":"<<m.acquisition.switches
            <<",\n \"acquisition_samples\":"<<m.acquisition.delays.size()<<",\n \"median_acquisition_ms\":"<<(m.acquisition.delays.empty()?"null":num(m.acquisition.median(),3))
            <<",\n \"mean_visible_aimpoint_error_degrees\":"<<(m.visible?num(m.errorSum/m.visible,6):"null")
            <<",\n \"time_on_target_percent\":"<<(m.time?num(100*m.onTarget/m.time,4):"null")
            <<",\n \"frame_ms_p50\":"<<(m.frames.empty()?"null":num(m.percentile(.5f),4))<<",\n \"frame_ms_p95\":"<<(m.frames.empty()?"null":num(m.percentile(.95f),4))
            <<",\n \"discarded_stall_seconds\":"<<b.clock.discarded
            <<",\n \"measurement_sampling\":\"render frames\",\n \"acquisition_definition\":\"Scene time from an in-view unobstructed reference point becoming visible to its first successful click; not biological reaction time.\",\n \"notes\":\"Frame time is not input or display latency. Control/countdown shots excluded. Paused in the menu, without focus, or outside the active bay. No detector connected. Comparison ID groups equal configurations; it is not a security checksum.\"\n}\n";
        try{writeText(b.stem+".json",report.str());writeText(b.stem+".csv",b.csv);writeText(b.stem+"-shots.csv",b.events);saveSettings(b.runSettings,b.stem+".ini");notify(std::string(BayNames[index])+" report saved locally.");}
        catch(const std::exception& e){notify(std::string("Report save failed: ")+e.what());}
    }
    void finishAll(const std::string& why){for(int i=0;i<BayCount;i++)finish(i,why);}
    void start(int index,bool leadIn=true){
        auto& b=bays[index];if(b.running){finish(index,"stopped");return;}b.reset();b.running=true;b.runSettings=b.settings;
        b.runSettings.model=settings.model;b.runSettings.pose=settings.pose;b.runSettings.poseFrame=settings.poseFrame;
        b.runSettings.fov=settings.fov;b.runSettings.sensitivity=settings.sensitivity;b.runSettings.rawMouse=settings.rawMouse;
        b.runSettings.quality=settings.quality;b.runSettings.mouseDpi=settings.mouseDpi;b.runSettings.antialias=settings.antialias;b.runSettings.frameCap=settings.frameCap;b.runSettings.light=settings.light;
        b.runSettings.crosshair=settings.crosshair;b.runSettings.weapon=settings.weapon;b.runSettings.hud=settings.hud;
        b.readyLeft=leadIn?b.settings.readySeconds:0;b.runWidth=GetScreenWidth();b.runHeight=GetScreenHeight();b.key=contextKey(index);lastBoards=-100;
        b.stem="reports/range-"+timestamp()+"-"+std::to_string(ownProcessId())+"-bay"+std::to_string(index+1)+"-"+std::to_string(++b.reportNumber);
        b.csv="seconds,camera_x,camera_y,camera_z,yaw,pitch,visible_aimpoint_error_degrees,crosshair_target,shots,hits,frame_ms\n";
        b.events="seconds,target_id,hit,x,y,z\n";notify(b.readyLeft>0?"Get ready. The timer begins after the countdown.":std::string(BayNames[index])+" timed drill started.");
    }
    void unstuck(){active=closestBay(position);position={BayCenters[active],1.62f,-6.5f};yaw=0;pitch=-1.2f;updateCamera();}
    void chooseModel(int delta){
        refreshModels();if(models.empty()){notify("No local GLB characters are available.");return;}
        int next=(modelIndex+delta+int(models.size()))%int(models.size());if(utf8(models[next].filename())==settings.model)return;
        finishAll("model changed");Settings candidate=settings;candidate.pose=utf8(models[next].filename())=="SWAT.glb"?5:-1;candidate.poseFrame=0;
        try{if(character.load(models[next],shader,candidate)){settings=candidate;modelIndex=next;updateModelKey();for(auto& b:bays)b.reset();save();notify(character.filename+" selected.");}else notify(character.status);}
        catch(const std::exception& e){notify(e.what());}for(auto& b:bays)b.dirty=true;
    }
    void control(int index,Control c){
        auto& b=bays[index];auto& s=b.settings;if(c.kind==ControlKind::None)return;
        if(c.kind==ControlKind::Page){b.page=c.index;b.dirty=true;return;}
        if(b.page==3&&c.index==7&&(c.kind==ControlKind::Increase||c.kind==ControlKind::Decrease)){b.page=0;b.dirty=true;return;}
        if(c.kind==ControlKind::Start){start(index);return;}
        if(c.kind==ControlKind::Reset){finish(index,"reset");b.reset();return;}
        if(c.kind==ControlKind::Defaults){finish(index,"defaults");s=bayDefaults(index);b.reset();rebuildSolids();save();return;}
        int sign=c.kind==ControlKind::Increase?1:-1;bool poseChanged=false;
        if(b.page==2||(b.page==3&&(c.index<3||c.index==6))||(b.page==1&&(c.index==2||c.index==3)))finishAll("settings changed");else finish(index,"settings changed");
        if(b.page==0)switch(c.index){
            case 0:s.motion=(s.motion+sign+8)%8;break;case 1:s.count+=sign;break;case 2:s.speed+=sign*.5f;break;
            case 3:s.distance+=sign*2;break;case 4:s.spread+=sign*.5f;break;case 5:s.duration+=sign*15;break;
            case 6:s.seed=std::clamp(s.seed+sign,1,999999);break;case 7:s.cover=!s.cover;break;
        }
        if(b.page==1)switch(c.index){
            case 0:s.height+=sign*.05f;break;case 1:s.facing+=sign*15;if(s.facing>180)s.facing=-180;if(s.facing< -180)s.facing=180;break;
            case 2:{int n=character.animationCount+1;settings.pose=(settings.pose+1+sign+n)%n-1;poseChanged=true;break;}
            case 3:settings.poseFrame=std::clamp(settings.poseFrame+sign*.05f,0.0f,1.0f);poseChanged=true;break;
            case 4:s.aimHeight+=sign*.05f;break;case 5:s.appearanceInterval+=sign*.25f;break;
            case 6:s.mixedStill=!s.mixedStill;break;case 7:s.markers=!s.markers;break;
        }
        if(b.page==2)switch(c.index){
            case 0:settings.fov+=sign*5;break;case 1:settings.sensitivity+=sign*.005f;break;
            case 2:settings.frameCap=(settings.frameCap+sign+6)%6;break;case 3:settings.rawMouse=glfwRawMouseMotionSupported()&&!settings.rawMouse;break;
            case 4:settings.crosshair=!settings.crosshair;break;case 5:settings.weapon=!settings.weapon;break;
            case 6:settings.light=(settings.light+sign+3)%3;break;case 7:settings.hud=!settings.hud;break;
        }
        if(b.page==3)switch(c.index){
            case 0:settings.mouseDpi+=sign*100;break;
            case 1:if(glfwRawMouseMotionSupported()){settings.sensitivity=sensitivityForCm(std::clamp(cmPerTurn(settings.sensitivity,settings.mouseDpi)+sign,1.0,200.0),settings.mouseDpi);settings.rawMouse=true;}else notify("Raw mouse input is unavailable on this system.");break;
            case 2:settings.rawMouse=glfwRawMouseMotionSupported()&&!settings.rawMouse;break;
            case 3:s.readySeconds+=sign;break;case 4:s.hitResponse=1-s.hitResponse;break;
            case 5:s.respawnDelay+=sign*.05f;break;case 6:settings.antialias=!settings.antialias;break;
            case 7:b.page=0;break;
        }
        settings.sanitize();sanitizeBay(s);if(poseChanged)character.setPose(settings);if(b.page!=2)b.reset();
        for(auto& bay:bays)bay.dirty=true;rebuildSolids();lastBoards=-100;if(blocksPlayer(position,solids))unstuck();
        applyMouse();SetTargetFPS(benchmarking?0:testing?60:FrameCaps[settings.frameCap]);save();
    }
    void shoot(){
        auto hit=cast({position,forward(yaw,pitch)});lastShot=elapsed;kick=1;shotSequence++;
        if(hit.kind==ShotHit::Board){control(hit.bay,panelControl(hit.pixel));return;}
        int scoringBay=hit.kind==ShotHit::Target?hit.bay:active;
        if(scoringBay!=active){notify("Move into that bay to score hits there.");return;}
        auto& b=bays[scoringBay];if(b.finished&&!b.running)b.reset();b.stats.shots++;b.dirty=true;
        if(b.readyLeft>0){--b.stats.shots;return;}
        if(hit.kind==ShotHit::Target){b.stats.hits++;b.flashed[hit.id]=b.clock.time();hitMarker=elapsed;b.stats.acquisition.hit(hit.id,b.clock.time());
            if(b.settings.hitResponse){b.respawn[hit.id]=b.clock.time()+b.settings.respawnDelay;++b.generations[hit.id];b.stats.acquisition.observe(hit.id,false,b.clock.time());}}
        else b.stats.acquisition.miss();
        if(b.running){std::ostringstream event;event<<num(b.clock.time(),5)<<","<<hit.id<<","<<(hit.kind==ShotHit::Target?1:0)<<","<<hit.point.x<<","<<hit.point.y<<","<<hit.point.z<<"\n";b.events+=event.str();}
        impacts.push_back({hit.point,elapsed+.35,hit.kind==ShotHit::Target});if(impacts.size()>24)impacts.erase(impacts.begin());
    }
    void input(double dt){
        fireCount=0;int clicks=pendingClicks;pendingClicks=0;if(testing||startupCheck||benchmarking)return;bool focused=IsWindowFocused();
        if(!focused){if(wasFocused&&captured){release();menu=true;}wasFocused=false;return;}wasFocused=true;
        if(IsKeyPressed(KEY_ESCAPE)){if(qualityOpen){qualityOpen=false;return;}menu=!menu;if(menu)release();else {capture();return;}}
        if(IsKeyPressed(KEY_F11)){release();ToggleBorderlessWindowed();if(!menu)capture();}
        if(IsKeyPressed(KEY_F12))captureNext=true;if(menu)return;
        if(!captured){if(IsMouseButtonPressed(MOUSE_BUTTON_LEFT))capture();return;}
        auto delta=GetMouseDelta();yaw=std::remainder(yaw-delta.x*settings.sensitivity,360.0f);pitch=std::clamp(pitch-delta.y*settings.sensitivity,-85.0f,85.0f);
        Vector3 f=forward(yaw,0),r={-f.z,0,f.x},move{};
        if(IsKeyDown(KEY_W))move=Vector3Add(move,f);if(IsKeyDown(KEY_S))move=Vector3Subtract(move,f);
        if(IsKeyDown(KEY_D))move=Vector3Add(move,r);if(IsKeyDown(KEY_A))move=Vector3Subtract(move,r);
        if(Vector3LengthSqr(move)>0){float speed=IsKeyDown(KEY_LEFT_SHIFT)?5.5f:3.2f;if(IsKeyDown(KEY_LEFT_CONTROL))speed=1.6f;move=Vector3Scale(Vector3Normalize(move),speed*float(std::min(dt,.2)));position=movePlayer(position,move,solids);}
        float eye=IsKeyDown(KEY_LEFT_CONTROL)?1.05f:1.62f;position.y+=(eye-position.y)*std::min(1.0f,float(dt)*15);active=closestBay(position);fireCount=clicks;
    }
    void measure(double dt,float frameMs){
        auto& b=bays[active];auto& m=b.stats;if(b.finished&&!b.running)return;m.time+=dt;m.error=-1;m.hover=-1;Vector3 dir=forward(yaw,pitch);
        auto right=Vector3Normalize(Vector3CrossProduct(dir,{0,1,0})),up=Vector3CrossProduct(right,dir);
        float tangent=std::tan(settings.fov*DEG2RAD/2),aspect=float(GetScreenWidth())/GetScreenHeight();
        for(int id=0;id<b.settings.count;id++){
            auto e=entity(active,id);auto point=aimPoint(e,b.settings),toPoint=Vector3Subtract(point,position);
            float z=Vector3DotProduct(toPoint,dir),pointDistance=Vector3Length(toPoint);bool visible=e.active&&z>.001f;
            visible=visible&&std::abs(Vector3DotProduct(toPoint,right))<=z*tangent&&std::abs(Vector3DotProduct(toPoint,up))<=z*tangent/aspect;
            if(visible&&covered(position,point,solids))visible=false;
            if(visible&&pointDistance>.001f){auto first=cast({position,Vector3Scale(toPoint,1/pointDistance)},true);
                if(first.distance<pointDistance-.01f&&!(first.kind==ShotHit::Target&&first.bay==active&&first.id==id))visible=false;}
            m.acquisition.observe(id,visible,b.clock.time());
            if(visible){float error=angularError(position,dir,point);if(m.error<0||error<m.error)m.error=error;}
        }
        if(m.error>=0){m.visible+=dt;m.errorSum+=m.error*dt;}auto hit=cast({position,dir},true);if(hit.kind==ShotHit::Target){m.hover=hit.id;m.onTarget+=dt;}
        if(m.frames.size()<120000)m.frames.push_back(frameMs);
        if(b.running&&b.clock.time()>=b.nextSample){
            b.nextSample=b.clock.time()+1.0/30;std::ostringstream row;row<<num(b.clock.time(),4)<<","<<position.x<<","<<position.y<<","<<position.z<<","<<yaw<<","<<pitch<<","<<m.error<<","<<m.hover<<","<<m.shots<<","<<m.hits<<","<<frameMs<<"\n";b.csv+=row.str();
        }
        if(b.running&&b.clock.time()>=b.settings.duration)finish(active,"completed");
    }
    void renderPanel(int index){
        auto& b=bays[index];auto& s=b.settings;Color accent=BayColors[index];BeginTextureMode(boards[index]);ClearBackground(Ink);
        neonBorder({0,0,1024,6},accent,.9f,22);DrawRectangle(0,0,1024,6,accent);text("VAPOR LOCK  /  RANGE 0"+std::to_string(index+1),32,31,24,accent);
        text(BayNames[index],32,64,48);text("SHOOT A TILE TO ADJUST",571,84,23,Muted);
        const char* pages[]={"DRILL","TARGET","OPTICS","INPUT"};
        for(int p=0;p<4;p++){Rectangle r{32.0f+240*p,132,224,52};if(p==b.page)neonBorder(r,accent,.65f,12);DrawRectangleRec(r,p==b.page?accent:Card);centered(pages[p],r,30,p==b.page?Ink:Text);}
        std::array<std::string,8>names,values;
        if(b.page==0){
            names={"Motion","Targets","Speed","Depth past firing line","Spread","Timed duration","Repeatable seed","Cover blocks"};
            values={MotionNames[s.motion],std::to_string(s.count),num(s.speed)+" m/s",num(s.distance,0)+" m",num(s.spread)+" m",num(s.duration,0)+" s",std::to_string(s.seed),s.cover?"ON":"OFF"};
        }else if(b.page==1){
            names={"Model height","Facing","Pose (all bays)","Pose frame (all bays)","Aim point height","Appearance period","Mixed still targets","Target markers"};
            auto poses=character.poseNames();
            values={num(s.height,2)+" m",num(s.facing,0)+" deg",poses.at(size_t(settings.pose+1)),num(settings.poseFrame*100,0)+" %",num(s.aimHeight*100,0)+" %",num(s.appearanceInterval,2)+" s",s.mixedStill?"ON":"OFF",s.markers?"ON":"OFF"};
        }else if(b.page==2){
            names={"Horizontal FOV","Mouse sensitivity","Frame limit","Raw mouse input","Crosshair","View weapon","Lighting","Minimal HUD"};
            values={num(settings.fov,0)+" deg",num(settings.sensitivity,3),settings.frameCap==5?"UNLIMITED":std::to_string(FrameCaps[settings.frameCap])+" FPS",
                settings.rawMouse?"ON":"OFF",settings.crosshair?"ON":"OFF",settings.weapon?"ON":"OFF",settings.light==0?"NEUTRAL":settings.light==1?"WARM":"DIM",settings.hud?"ON":"OFF"};
        }else{
            names={"DPI (set yours)","cm per full turn","Raw mouse input","Ready countdown","After a hit","Respawn delay","Anti-aliasing","Return to drill"};
            values={std::to_string(settings.mouseDpi),settings.rawMouse?num(cmPerTurn(settings.sensitivity,settings.mouseDpi),2)+" cm":"ENABLE RAW",settings.rawMouse?"ON":"OFF",std::to_string(s.readySeconds)+" s",s.hitResponse?"RESPAWN":"KEEP TARGET",num(s.respawnDelay,2)+" s",settings.antialias&&graphicsPreset(settings.quality).samples?std::to_string(graphicsPreset(settings.quality).samples)+"x MSAA":"OFF","DRILL"};
        }
        for(int row=0;row<8;row++){
            float y=208.0f+row*74;DrawRectangleRec({32,y,960,62},Panel);fitText(names[row],52,y+17,360,31,Text);fitText(values[row],425,y+16,330,32,accent);
            for(int side=0;side<2;side++){Rectangle r{side?886.0f:770.0f,y,92,62};DrawRectangleRec(r,Card);centered(side?"+":"-",r,40,accent);}
        }
        Rectangle startRect{32,840,376,76};neonBorder(startRect,accent,.65f,14);DrawRectangleRec(startRect,accent);centered(b.running?"STOP + SAVE":"START TIMED",startRect,28,Ink);
        DrawRectangleRec({424,840,258,76},Card);centered("RESET",{424,840,258,76},26);
        DrawRectangleRec({698,840,294,76},Card);centered("DEFAULTS",{698,840,294,76},26);
        text(b.page==3?"cm/turn assumes your stated DPI and raw input.":b.page==2?"OPTICS APPLY TO EVERY BAY":"DRILL + TARGET VALUES STAY IN THIS BAY",32,941,25,Muted);
        text("Approach to read. Shoot a highlighted tile.",32,979,24,Muted);
        if(index==hoveredBay){
            Rectangle rect{};auto c=hoveredControl;
            if(c.kind==ControlKind::Page)rect={32.0f+c.index*240,132,224,52};
            if(c.kind==ControlKind::Increase||c.kind==ControlKind::Decrease)rect={c.kind==ControlKind::Increase?886.0f:770.0f,208.0f+c.index*74,92,62};
            if(c.kind==ControlKind::Start)rect={32,840,376,76};if(c.kind==ControlKind::Reset)rect={424,840,258,76};if(c.kind==ControlKind::Defaults)rect={698,840,294,76};
            if(rect.width>0){DrawRectangleRec(rect,Fade(accent,.16f));DrawRectangleLinesEx(rect,5,WHITE);}
        }
        EndTextureMode();b.dirty=false;
    }
    void renderSign(int index){
        auto& b=bays[index];auto& m=b.stats;auto accent=BayColors[index];BeginTextureMode(signs[index]);ClearBackground(Panel);
        neonBorder({0,0,12,320},accent,.7f,20);DrawRectangle(0,0,12,320,accent);text("0"+std::to_string(index+1),45,29,84,accent);
        text(BayNames[index],184,38,68);text(BayDescriptions[index],188,125,25,Muted);
        text("VAPOR LOCK  /  TRAINING RANGE",188,167,21,Cyan);
        std::string state=b.running?(active==index&&!menu?(b.readyLeft>0?"GET READY":"TIMED DRILL"):"PAUSED"):b.finished?"RESULT SAVED":"FREE PRACTICE";
        text(state,44,231,27,accent);std::string clock=b.running?num(std::max(0.0,double(b.settings.duration)-b.clock.time()),1)+"s":num(m.time,1)+"s";text(clock,425,224,39,Text);
        text("HITS",802,51,22,Muted);text(std::to_string(m.hits),802,88,66);text("ACCURACY",1055,51,22,Muted);text(m.shots?num(m.accuracy(),0)+"%":"--",1055,88,66);
        text("ON TARGET",800,187,26,Muted);text(m.time?num(100*m.onTarget/m.time,1)+"%":"--",1055,184,34,accent);
        std::string extra=index==1?"BEST STREAK  "+std::to_string(m.acquisition.bestStreak):"ACQUIRE  "+(m.acquisition.delays.empty()?std::string("--"):num(m.acquisition.median(),0)+" ms");
        text(extra,800,252,26,Muted);EndTextureMode();
    }
    void block(Vector3 center,Vector3 size,Color color){if(buildingWorld){environment[buildGroup].box(cube.meshes[0],center,size,color);return;}if(profile.enabled)++profile.boxes;DrawModelEx(cube,center,{0,1,0},0,size,color);}
    void neonBlock(Vector3 center,Vector3 size,Color color){if(buildingWorld){emissive[buildGroup].box(cube.meshes[0],center,size,color);return;}auto original=cube.materials[0].shader;cube.materials[0].shader=neon.core;block(center,size,color);cube.materials[0].shader=original;}
    void box(BoundingBox b,Color color){block(Vector3Scale(Vector3Add(b.min,b.max),.5f),Vector3Subtract(b.max,b.min),color);}
    void screen(Texture2D texture,Vector3 center,float width,float height){
        float left=center.x+width/2,right=center.x-width/2,top=center.y+height/2,bottom=center.y-height/2;
        rlSetTexture(texture.id);rlBegin(RL_QUADS);rlColor4ub(255,255,255,255);rlNormal3f(0,0,-1);
        rlTexCoord2f(0,0);rlVertex3f(left,bottom,center.z);rlTexCoord2f(1,0);rlVertex3f(right,bottom,center.z);
        rlTexCoord2f(1,1);rlVertex3f(right,top,center.z);rlTexCoord2f(0,1);rlVertex3f(left,top,center.z);rlEnd();rlSetTexture(0);
    }
    void buildWorld(){buildingWorld=true;buildGroup=0;
        Color concrete{58,63,73,255},wall{46,49,60,255},dark{25,28,37,255};
        block({0,-.16f,16.5f},{64,.3f,51},concrete);block({0,6.15f,16.5f},{64,.3f,51},Color{35,38,48,255});
        for(auto& b:architecture())box(b,wall);
        for(int x=-32;x<=32;x+=2)block({float(x),.003f,-4.5f},{.018f,.006f,9},Color{83,86,96,255});
        for(float z:{-8.0f,-6.0f,-4.0f,-2.0f})block({0,.004f,z},{64,.008f,.018f},Color{79,82,93,255});
        neonBlock({0,.012f,-7.8f},{63,.012f,.045f},NeonPink);
        for(int i=0;i<BayCount;i++){
            buildGroup=i+1;float x=BayCenters[i];auto accent=NeonBayColors[i];
            block({x,-.005f,21.5f},{15.55f,.06f,40.5f},Color{40,45,53,255});
            for(float side:{-7.5f,7.5f}){block({x+side,.05f,21.5f},{.15f,.10f,40.5f},dark);neonBlock({x+side,.108f,21.5f},{.04f,.015f,40.5f},accent);}
            block({x,2.45f,41.75f},{15.1f,4.8f,.2f},Color{96,105,118,255});
            for(float a=-7;a<=7;a+=1)block({x+a,2.4f,41.57f},{.025f,4.6f,.045f},Color{63,73,88,255});
            for(float z=8;z<=40;z+=8){block({x,5.55f,z},{15.6f,.45f,.65f},dark);neonBlock({x,5.28f,z-.19f},{10.5f,.045f,.08f},int(z/8)%2?NeonViolet:NeonCyan);}
            for(int d=0;d<3;d++){
                float z=FiringLine+(d+1)*10;
                block({x,.04f,z},{14.5f,.03f,.04f},Color{105,114,129,255});

            }
            neonBlock({x,.05f,1.35f},{15,.025f,.12f},accent);
            for(int a=0;a<18;a++)block({x-7.2f+a*.85f,.05f,1.1f},{.4f,.03f,.2f},Color{93,100,114,255});
            block({x,4.8f,4.2f},{14,2.15f,.25f},Ink);
            for(float side:{-5.8f,5.8f})neonBlock({x+side,4.8f,4.05f},{.08f,1.6f,.03f},accent);
            block({x+7.5f,2.3f,2.2f},{.18f,4.6f,.24f},dark);block({x-7.5f,2.3f,2.2f},{.18f,4.6f,.24f},dark);
            auto c=boardCenter(i);block({c.x,c.y,c.z+.13f},{PanelWidth+.15f,PanelHeight+.15f,.22f},Ink);
            neonBlock({c.x,c.y,c.z+.1f},{PanelWidth+.08f,PanelHeight+.08f,.2f},accent);
            for(float side:{-7.78f,7.78f}){
                block({x+side,.20f,22},{.08f,.40f,39},dark);
                block({x+side,1.1f,22},{.06f,.045f,39},Color{94,100,118,255});
                for(float z=7;z<42;z+=4){
                    block({x+side,2.9f,z},{.06f,2.55f,3.84f},Color{38,42,54,255});
                    block({x+side*.996f,2.9f,z},{.025f,2.25f,3.55f},Color{44,48,61,255});
                }
            }
        }
        buildingWorld=false;for(int i=0;i<=BayCount;i++){environment[i].upload(environmentShader);emissive[i].upload(neon.core);}
    }
    void world(){
        environment[0].draw();emissive[0].draw();
        for(int i=0;i<BayCount;i++){
            if(!visibleBox(environment[i+1].bounds,position,forward(yaw,pitch),settings.fov,float(GetScreenWidth())/GetScreenHeight()))continue;
            environment[i+1].draw();emissive[i+1].draw();
            float x=BayCenters[i];auto accent=NeonBayColors[i];auto c=boardCenter(i);
            screen(signs[i].texture,{x,4.8f,4.06f},9.6f,2.0f);screen(boards[i].texture,{c.x,c.y,c.z-.012f},PanelWidth,PanelHeight);
            for(int d=0;d<3;d++){
                float z=FiringLine+(d+1)*10,a=x-6.1f;
                rlSetTexture(distancePlates[d].texture.id);rlBegin(RL_QUADS);rlColor4ub(255,255,255,255);rlNormal3f(0,1,0);
                rlTexCoord2f(0,0);rlVertex3f(a+.9f,.058f,z-.8f);rlTexCoord2f(1,0);rlVertex3f(a-.9f,.058f,z-.8f);
                rlTexCoord2f(1,1);rlVertex3f(a-.9f,.058f,z+.1f);rlTexCoord2f(0,1);rlVertex3f(a+.9f,.058f,z+.1f);rlEnd();rlSetTexture(0);
            }
            for(auto b:bayCover(bays[i].settings,i)){box(b,Color{72,80,91,255});neonBlock({(b.min.x+b.max.x)/2,b.max.y-.08f,b.min.z-.015f},{b.max.x-b.min.x,.09f,.03f},accent);DrawCubeWiresV(Vector3Scale(Vector3Add(b.min,b.max),.5f),Vector3Subtract(b.max,b.min),Color{114,124,138,255});}
            if(i!=occupiedBay(position))continue;const auto& s=bays[i].settings;
            for(int id=0;id<s.count;id++){
                auto e=entity(i,id);if(!e.active||!visibleBox(character.bounds(e,s),position,forward(yaw,pitch),settings.fov,float(GetScreenWidth())/GetScreenHeight()))continue;
                DrawCylinder({e.position.x,.045f,e.position.z},.32f,.32f,.015f,24,Color{21,24,32,255});
                bool flash=bays[i].clock.time()-bays[i].flashed[id]<.10;character.draw(e,s,flash?Color{255,198,192,255}:WHITE);
                if(s.markers){DrawSphere(aimPoint(e,s),.024f,accent);DrawBoundingBox(character.bounds(e,s),Fade(accent,.4f));}
            }
        }
        for(auto& effect:impacts)if(elapsed<effect.until){float f=float((effect.until-elapsed)/.35);DrawSphere(effect.point,.025f+.025f*f,effect.target?Cyan:Amber);}
    }
    void glowAccents(){
        neon.begin();
        neon.strip({0,.026f,-7.8f},{31.5f,0,0},{0,0,.45f},NeonPink,.28f);
        for(int i=0;i<BayCount;i++){
            float x=BayCenters[i];auto accent=NeonBayColors[i];auto c=boardCenter(i);
            for(float side:{-7.5f,7.5f})neon.strip({x+side,.038f,21.5f},{0,0,20.3f},{.42f,0,0},accent,.28f);
            neon.strip({x,.038f,1.35f},{7.5f,0,0},{0,0,.50f},accent,.22f);
            for(float z=8;z<=40;z+=8)neon.strip({x,5.255f,z-.19f},{5.35f,0,0},{0,0,.34f},int(z/8)%2?NeonViolet:NeonCyan,.27f);
            for(float side:{-5.8f,5.8f})neon.strip({x+side,4.8f,4.022f},{0,.87f,0},{.27f,0,0},accent,.33f);
            // Halos sit behind the opaque screens, so their text stays untouched.
            for(float side:{-1.0f,1.0f}){
                neon.strip({c.x+side*(PanelWidth+.08f)/2,c.y,c.z+.08f},{0,PanelHeight/2+.10f,0},{.30f,0,0},accent,.36f);
                neon.strip({c.x,c.y+side*(PanelHeight+.08f)/2,c.z+.08f},{PanelWidth/2+.10f,0,0},{0,.30f,0},accent,.36f);
            }
            for(auto cover:bayCover(bays[i].settings,i))neon.strip({(cover.min.x+cover.max.x)/2,cover.max.y-.08f,cover.min.z-.032f},{(cover.max.x-cover.min.x)/2,0,0},{0,.25f,0},accent,.25f);
        }
        neon.end();
    }
    void weapon(){
        if(!settings.weapon)return;
        auto f=forward(yaw,pitch),r=Vector3Normalize(Vector3CrossProduct(f,{0,1,0})),u=Vector3CrossProduct(r,f);
        if(rifle.ready){
            auto origin=Vector3Add(position,Vector3Add(Vector3Scale(r,.19f),Vector3Add(Vector3Scale(u,-.23f+kick*.012f),Vector3Scale(f,.13f-kick*.04f))));
            auto toward=Vector3Normalize(Vector3Add(f,Vector3Scale(r,-.12f)));
            auto right=Vector3Normalize(Vector3CrossProduct(toward,u)),up=Vector3CrossProduct(right,toward);
            rlDrawRenderBatchActive();sceneAA.clearDepth();rlEnableDepthTest();shadows.bind(shader,false);rifle.draw(origin,right,up,toward);
            if(elapsed-lastShot<.035)DrawSphere(Vector3Add(origin,Vector3Scale(toward,.75f)),.024f,Amber);
            rlDrawRenderBatchActive();rlEnableDepthTest();return;
        }
        float sway=captured?std::sin(float(elapsed)*2.3f)*.002f:0;
        auto origin=Vector3Add(position,Vector3Add(Vector3Scale(f,.48f-kick*.055f),Vector3Add(Vector3Scale(r,.20f+sway),Vector3Scale(u,-.20f+kick*.015f))));
        Matrix basis={r.x,u.x,f.x,0,r.y,u.y,f.y,0,r.z,u.z,f.z,0,0,0,0,1},original=cube.transform;
        auto part=[&](Vector3 offset,Vector3 size,Color color){
            auto point=Vector3Add(origin,Vector3Add(Vector3Scale(r,offset.x),Vector3Add(Vector3Scale(u,offset.y),Vector3Scale(f,offset.z))));
            cube.transform=MatrixMultiply(MatrixScale(size.x,size.y,size.z),basis);DrawModelEx(cube,point,{0,1,0},0,{1,1,1},color);
        };
        rlDrawRenderBatchActive();sceneAA.clearDepth();rlEnableDepthTest();
        part({0,0,.10f},{.09f,.105f,.36f},Color{41,46,58,255});part({0,.048f,.16f},{.076f,.024f,.43f},Color{76,84,102,255});
        part({0,0,.36f},{.037f,.041f,.21f},Color{27,31,41,255});part({0,-.11f,0},{.065f,.15f,.075f},Color{50,53,66,255});
        part({0,-.10f,.19f},{.067f,.16f,.065f},Color{36,40,52,255});part({0,.09f,.08f},{.054f,.058f,.042f},Color{24,27,34,255});
        part({0,.13f,.08f},{.010f,.014f,.020f},Cyan);part({.047f,.021f,.12f},{.008f,.013f,.16f},Violet);
        part({.015f,-.135f,-.025f},{.11f,.10f,.14f},Color{50,60,73,255});
        if(elapsed-lastShot<.055){part({0,0,.515f},{.072f,.065f,.075f},Amber);part({0,0,.56f},{.025f,.035f,.07f},Color{255,235,187,255});}
        cube.transform=original;rlDrawRenderBatchActive();rlEnableDepthTest();
    }
    void renderScene(){
        int width=GetScreenWidth(),height=GetScreenHeight();
        if(!presentation.id||presentation.texture.width!=width||presentation.texture.height!=height){
            if(viewport.id)UnloadRenderTexture(viewport);if(presentation.id)UnloadRenderTexture(presentation);
            viewport={};presentation=LoadRenderTexture(width,height);
            if(!IsRenderTextureValid(presentation))throw std::runtime_error("Could not allocate the range view.");
        }
        if(captureNext&&!viewport.id){viewport=LoadRenderTexture(width,height);if(!IsRenderTextureValid(viewport))throw std::runtime_error("Could not allocate clean capture.");}const auto& graphics=graphicsPreset(settings.quality);sceneAA.resize(width,height,settings.antialias?graphics.samples:0);shadows.configure(graphics.shadowSize,graphics.shadowRadius);float detail=graphics.detail?1.0f:0.0f;SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"surfaceDetail"),&detail,SHADER_UNIFORM_FLOAT);
        if(shadows.ready&&occupiedBay(position)>=0){
            shadows.begin({BayCenters[active],0,bays[active].settings.distance+FiringLine+2});
            cube.materials[0].shader=shadows.shader;
            for(auto& cover:bayCover(bays[active].settings,active))box(cover,WHITE);
            for(int m=0;m<character.model.materialCount;m++)character.model.materials[m].shader=shadows.shader;
            for(int id=0;id<bays[active].settings.count;id++){auto e=entity(active,id);if(e.active)character.draw(e,bays[active].settings);}
            for(int m=0;m<character.model.materialCount;m++)character.model.materials[m].shader=shader;
            cube.materials[0].shader=environmentShader;shadows.end();
        }
        profile.mark(2);float cameraValue[]={position.x,position.y,position.z},fog[]={.09f,.10f,.14f};float exposure=settings.light==0?1.05f:settings.light==1?1.20f:.55f;
        float tint[3]={1,1,1};if(settings.light==1){tint[0]=1.12f;tint[1]=1.01f;tint[2]=.84f;}if(settings.light==2){tint[0]=.82f;tint[1]=.91f;tint[2]=1.10f;}
        SetShaderValue(shader,shaderCamera,cameraValue,SHADER_UNIFORM_VEC3);SetShaderValue(shader,shaderFog,fog,SHADER_UNIFORM_VEC3);SetShaderValue(shader,shaderExposure,&exposure,SHADER_UNIFORM_FLOAT);SetShaderValue(shader,shaderTint,tint,SHADER_UNIFORM_VEC3);
        SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"cameraPosition"),cameraValue,SHADER_UNIFORM_VEC3);
        SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"fogColor"),fog,SHADER_UNIFORM_VEC3);
        SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"exposure"),&exposure,SHADER_UNIFORM_FLOAT);
        SetShaderValue(environmentShader,GetShaderLocation(environmentShader,"lightTint"),tint,SHADER_UNIFORM_VEC3);
        shadows.bind(shader,true);shadows.bind(environmentShader,true);
        BeginTextureMode(presentation);sceneAA.begin();ClearBackground(Ink);BeginMode3D(camera);world();if(graphics.glow)glowAccents();EndMode3D();
        if(captureNext){sceneAA.copyColor(sceneAA.fbo?sceneAA.fbo:presentation.id,viewport.id);exportCapture();captureNext=false;}profile.mark(3);if(!menu){BeginMode3D(camera);weapon();EndMode3D();}sceneAA.resolve(presentation.id);EndTextureMode();
    }
    void exportCapture(){
        std::string stem="reports/capture-"+timestamp()+"-"+std::to_string(ownProcessId())+"-"+std::to_string(++shotSequence);
        Image shot=LoadImageFromTexture(viewport.texture);ImageFlipVertical(&shot);bool ok=ExportImage(shot,utf8(local(stem+".png")).c_str());UnloadImage(shot);
        std::ostringstream out;out<<"{\"capture_schema\":2,\"target_population\":\"occupied_bay_only\",\"loaded_bay\":"<<occupiedBay(position)+1<<",\"model_content_id\":\""<<modelKey<<"\",\"pose\":"<<settings.pose<<",\"pose_frame\":"<<settings.poseFrame<<",\"light\":"<<settings.light<<",\"quality\":\""<<graphicsPreset(settings.quality).name<<"\",\"shadow_size\":"<<shadows.requested<<",\"msaa_samples\":"<<sceneAA.samples<<",\"model\":\""<<jsonEscape(character.filename)<<"\",\"horizontal_fov\":"<<settings.fov<<",\"width\":"<<viewport.texture.width<<",\"height\":"<<viewport.texture.height
            <<",\"camera\":["<<position.x<<","<<position.y<<","<<position.z<<"],\"yaw\":"<<yaw<<",\"pitch\":"<<pitch
            <<",\"bounds_note\":\"Projected full geometry bounds; clipping and occlusion are not segmentation.\",\"targets\":[";
        bool first=true;
        for(int i=0;i<BayCount;i++)for(int id=0;id<bays[i].settings.count;id++){
            auto e=entity(i,id);if(!e.active)continue;
            auto projected=projectBounds(character.bounds(e,bays[i].settings),camera,viewport.texture.width,viewport.texture.height);
            if(!projected.visible)continue;auto rect=projected.rectangle;Vector2 min{rect.x,rect.y},max{rect.x+rect.width,rect.y+rect.height};
            if(!first)out<<",";first=false;
            out<<"{\"bay\":"<<i+1<<",\"id\":"<<id<<",\"position\":["<<e.position.x<<","<<e.position.y<<","<<e.position.z<<"],\"simulation_seconds\":"<<bays[i].clock.time()<<",\"height\":"<<bays[i].settings.height<<",\"facing\":"<<bays[i].settings.facing
                <<",\"aimpoint_unoccluded_by_range\":"<<(!covered(position,aimPoint(e,bays[i].settings),solids)?"true":"false")<<",\"bbox\":["<<std::clamp(min.x,0.0f,float(viewport.texture.width))<<","<<std::clamp(min.y,0.0f,float(viewport.texture.height))<<","<<std::clamp(max.x,0.0f,float(viewport.texture.width))<<","<<std::clamp(max.y,0.0f,float(viewport.texture.height))<<"]}";
        }
        out<<"]}\n";writeText(stem+".json",out.str());notify(ok?"Clean range image + target bounds saved locally.":"The range image could not be saved.");
    }
    void hud(){
        float w=float(GetScreenWidth()),h=float(GetScreenHeight());auto& b=bays[active];auto accent=BayColors[active];
        if(settings.hud){
            DrawRectangleRec({w/2-130,16,260,72},Fade(Ink,.94f));brandMark(w/2-110,31,38);
            text(BrandName,w/2-58,28,20,Text);text("TRAINING RANGE",w/2-58,55,13,Cyan);
            DrawRectangleRec({18,16,342,72},Fade(Ink,.94f));DrawRectangleRec({w-322,16,304,72},Fade(Ink,.94f));
            neonBorder({24,24,4,48},accent,.65f,10);DrawRectangle(24,24,4,48,accent);text("0"+std::to_string(active+1)+"  /  "+BayNames[active],42,23,24);
            text(b.running?(b.readyLeft>0?"GET READY":"TIMED  "+num(std::max(0.0,double(b.settings.duration)-b.clock.time()),1)+"s"):b.finished?"RESULT SAVED":"FREE PRACTICE",42,53,19,accent);
            text(std::to_string(b.stats.hits)+" HITS   /   "+(b.stats.shots?num(b.stats.accuracy(),0)+"%":"--")+" ACC",w-302,27,21,Text);
            text("FRAME "+num(frameAverage,1)+" ms   /   "+std::to_string(GetFPS())+" FPS",w-302,57,17,Muted);
            DrawRectangle(0,int(h-81),int(w),81,Fade(Ink,.92f));
            text("WASD move   Shift sprint   Ctrl crouch   LMB fire",26,h-67,18,Text);text("Shoot the physical panels to change settings",26,h-36,18,accent);
            text("Esc menu   F12 capture   F11 window",w-343,h-36,17,Text);
        }
        if(b.readyLeft>0&&!menu){DrawRectangleRec({w/2-145,h*.32f,290,162},Fade(Ink,.94f));centered("GET READY",{w/2-145,h*.32f+16,290,32},24,accent);centered(std::to_string(int(std::ceil(b.readyLeft))),{w/2-145,h*.32f+48,290,93},78,Text);}
        if(settings.crosshair&&!menu){
            float x=w/2,y=h/2;Color c=(b.stats.hover>=0||hoveredBay>=0)?Cyan:Text;
            DrawLineEx({x-9,y},{x+9,y},3,Ink);DrawLineEx({x,y-9},{x,y+9},3,Ink);
            DrawLineEx({x-8,y},{x-4,y},1,c);DrawLineEx({x+4,y},{x+8,y},1,c);DrawLineEx({x,y-8},{x,y-4},1,c);DrawLineEx({x,y+4},{x,y+8},1,c);DrawCircleV({x,y},1.3f,c);
        }
        if(elapsed-hitMarker<.16&&!menu){auto c=Fade(Cyan,float(1-(elapsed-hitMarker)/.16));Vector2 center{w/2,h/2};for(int x:{-1,1})for(int y:{-1,1})DrawLineEx({center.x+x*10,center.y+y*10},{center.x+x*17,center.y+y*17},2,c);}
        if(!captured&&!menu&&!testing&&!startupCheck&&!benchmarking){
            Rectangle r{w/2-260,h*.70f,520,90};DrawRectangleRec(r,Fade(Ink,.9f));centered("CLICK TO ENTER THE RANGE",{r.x,r.y+13,r.width,30},23);
            centered("Shoot the physical panels. Esc opens the main menu.",{r.x,r.y+49,r.width,22},15,Muted);
        }
        if(GetTime()<notifyUntil&&!notification.empty()){
            float size=std::min(w-80,MeasureTextEx(uiFont,notification.c_str(),16,.5f).x+40);DrawRectangleRec({w/2-size/2,h-106,size,37},Fade(Ink,.94f));centered(notification,{w/2-size/2,h-106,size,37},16,Cyan);
        }
    }
    void selectQuality(int selected){
        if(selected<0||selected>=QualityCount)return;
        const auto& preset=graphicsPreset(selected);
        if(settings.quality!=selected||settings.antialias!=(preset.samples>0)){
            finishAll("graphics quality changed");settings.quality=selected;settings.antialias=preset.samples>0;
            for(auto& b:bays)b.dirty=true;save();notify(std::string("Quality: ")+preset.name);
        }
        qualityCursor=selected;qualityOpen=false;menuFocus=2;
    }
    void qualityClick(Vector2 mouse){
        for(int i=0;i<QualityCount;i++)if(CheckCollisionPointRec(mouse,qualityOption(float(GetScreenWidth()),float(GetScreenHeight()),i))){selectQuality(i);return;}
        qualityOpen=false;
    }
    void pauseMenu(){
        if(!menu)return;float w=float(GetScreenWidth()),h=float(GetScreenHeight());DrawRectangle(0,0,int(w),int(h),Fade(Ink,.80f));
        bool focused=IsWindowFocused()&&!testing&&!benchmarking;
        menuActionsBlocked=qualityOpen;
        if(focused&&IsKeyPressed(KEY_TAB)){qualityOpen=false;menuFocus=nextMenuFocus(menuFocus,IsKeyDown(KEY_LEFT_SHIFT));}
        Rectangle card=pauseCard(w,h);neonBorder(card,NeonViolet,.85f,24);DrawRectangleRec(card,Panel);DrawRectangleLinesEx(card,1,Line);
        neonBorder({card.x,card.y,660,3},NeonCyan,.7f,16);
        DrawRectangleGradientH(int(card.x),int(card.y),660,3,Cyan,Violet);
        brandMark(card.x+32,card.y+28,44);text(BrandName,card.x+92,card.y+28,24);text("AIM TESTING SUITE",card.x+92,card.y+59,14,Cyan);
        text("LOCAL RANGE",card.x+510,card.y+43,14,Muted);
        text("Range paused",card.x+32,card.y+92,38);text("Esc to return. Tab and Enter also select menu actions.",card.x+32,card.y+144,18,Muted);
        DrawLine(int(card.x+32),int(card.y+176),int(card.x+628),int(card.y+176),Line);text("01  CHARACTER",card.x+32,card.y+191,18,Cyan);
        Rectangle field{card.x+32,card.y+220,596,62};DrawRectangleRec(field,Ink);
        if(menuButton({field.x,field.y,54,62},"<",0))chooseModel(-1);if(menuButton({field.x+542,field.y,54,62},">",1))chooseModel(1);
        fitText(character.filename,field.x+70,field.y+14,455,23);text(std::to_string(modelIndex+1)+" / "+std::to_string(models.size())+" LOCAL CHARACTERS",field.x+70,field.y+45,15,Muted);
        text("Add .glb files to models beside the EXE. Reopen Esc to refresh.",card.x+32,card.y+293,16,Muted);
        text("02  QUALITY",card.x+32,card.y+335,18,Cyan);
        text(graphicsPreset(settings.quality).description,card.x+32,card.y+384,17,Muted);
        if(menuButton({card.x+32,card.y+424,596,52},"03  UNSTUCK PLAYER",3)){unstuck();menu=false;capture();}
        if(menuButton({card.x+32,card.y+492,596,52},"04  EXIT RANGE",4,Muted))quit=true;
        DrawLine(int(card.x+32),int(card.y+564),int(card.x+628),int(card.y+564),Line);
        auto link=websiteButton(w,h);
        if(menuButton(link,"GitHub / Project source",5))visitWebsite();
        centered("Opens your browser only when selected.",{card.x+32,card.y+642,596,20},16,Muted);
        // Draw the popup last. Underlying menu actions stay blocked for this frame.
        auto quality=qualityField(w,h);bool wasOpen=qualityOpen;
        if(menuFocus==2||qualityOpen)neonBorder(quality,Cyan,.7f,12);
        DrawRectangleRec(quality,Card);DrawRectangleLinesEx(quality,1,menuFocus==2?Cyan:Line);
        text(graphicsPreset(settings.quality).name,quality.x+16,quality.y+12,23);
        centered(qualityOpen?"^":"v",{quality.x+quality.width-40,quality.y,32,quality.height},21,Cyan);
        bool click=focused&&IsMouseButtonPressed(MOUSE_BUTTON_LEFT),enter=focused&&(IsKeyPressed(KEY_ENTER)||IsKeyPressed(KEY_SPACE));
        if((click&&CheckCollisionPointRec(GetMousePosition(),quality))||(!qualityOpen&&menuFocus==2&&enter)){
            qualityOpen=!qualityOpen;qualityCursor=settings.quality;menuFocus=2;click=false;enter=false;
        }
        if(qualityOpen){
            if(focused&&IsKeyPressed(KEY_UP))qualityCursor=(qualityCursor+QualityCount-1)%QualityCount;
            if(focused&&IsKeyPressed(KEY_DOWN))qualityCursor=(qualityCursor+1)%QualityCount;
            auto top=qualityOption(w,h,0);Rectangle list{top.x,top.y,top.width,44.0f*QualityCount};
            neonBorder(list,NeonViolet,.8f,14);DrawRectangleRec(list,Panel);DrawRectangleLinesEx(list,1,Cyan);
            for(int i=0;i<QualityCount;i++){
                auto row=qualityOption(w,h,i);bool hover=CheckCollisionPointRec(GetMousePosition(),row)&&focused;
                if(hover||qualityCursor==i)DrawRectangleRec({row.x+2,row.y+1,row.width-4,row.height-2},Card);
                text(graphicsPreset(i).name,row.x+16,row.y+11,22,i==settings.quality?Cyan:Text);
                if(i==DefaultQuality)text("DEFAULT",row.x+row.width-100,row.y+14,14,Muted);
            }
            if(enter&&wasOpen)selectQuality(qualityCursor);
            else if(click&&wasOpen)qualityClick(GetMousePosition());
        }
    }
    void screenshot(const std::string& filename){
        Image image=LoadImageFromTexture(presentation.texture);ImageFlipVertical(&image);
        if(!ExportImage(image,utf8(local("reports/"+filename)).c_str()))throw std::runtime_error("Cannot write preview image.");UnloadImage(image);
    }
    void aimAt(Vector3 point){auto d=Vector3Subtract(point,position);yaw=std::atan2(d.x,d.z)*RAD2DEG;pitch=std::atan2(d.y,std::sqrt(d.x*d.x+d.z*d.z))*RAD2DEG;updateCamera();}
    void aimTestTarget(){
        auto e=entity(0,0);bool found=false;
        for(float y=.35f;y<.9f&&!found;y+=.035f)for(float x=-.18f;x<=.18f&&!found;x+=.06f){auto p=e.position;p.y=bays[0].settings.height*y;p.x+=x;aimAt(p);found=cast({position,forward(yaw,pitch)}).kind==ShotHit::Target;}
        verify(found,"test target is reachable by a mesh ray");
    }
    void verify(bool condition,const char* message){if(!condition)throw std::runtime_error(std::string("Range check: ")+message);}
    void checkFrame(){
        ++testFrame;
        if(testFrame==3){verify(!viewport.id,"clean capture buffer is not allocated during ordinary play");screenshot("range-overview.png");verify(character.loaded&&character.triangles>0,"character loaded");
            verify(uiFont.texture.id&&uiFont.texture.id!=GetFontDefault().texture.id,"embedded interface font loaded");verify(rifle.ready,"procedural view weapon loaded");verify(fs::is_directory(local("models")),"portable models folder ready");}
        if(testFrame==4){
            auto center=boardCenter(0);position={center.x,1.8f,-4.8f};settings.fov=75;aimAt(center);
            int previous=bays[0].settings.count,other=bays[1].settings.count,shots=bays[0].stats.shots;
            aimAt(panelPoint(0,{940,208+74+30}));shoot();
            verify(bays[0].settings.count==previous+1&&bays[1].settings.count==other,"per-bay target controls");
            verify(bays[0].stats.shots==shots,"control shots excluded from score");aimAt(center);
        }
        if(testFrame==5)lastShot=-100;
        if(testFrame==6){screenshot("range-panel.png");}
        if(testFrame==7){menu=true;refreshModels();}
        if(testFrame==9)screenshot("range-menu.png");
        if(testFrame==10){
            menu=false;unstuck();settings.fov=90;bays[0].settings.count=1;bays[0].settings.motion=0;bays[0].settings.distance=8;bays[0].settings.cover=false;
            bays[0].settings.hitResponse=0;rebuildSolids();start(0,false);active=0;auto e=entity(0,0);bool found=false;
            for(float y=.35f;y<.9f&&!found;y+=.035f)for(float x=-.18f;x<=.18f&&!found;x+=.06f){auto p=e.position;p.y=bays[0].settings.height*y;p.x+=x;aimAt(p);found=cast({position,forward(yaw,pitch)}).kind==ShotHit::Target;}
            verify(found,"character can be shot");Ray ray{position,forward(yaw,pitch)};auto transform=character.transform(e,bays[0].settings);
            float fast=character.query.worldHit(ray,transform),reference=1e30f;
            for(int i=0;i<character.model.meshCount;i++){auto hit=GetRayCollisionMesh(ray,character.model.meshes[i],transform);if(hit.hit)reference=std::min(reference,hit.distance);}
            verify(std::abs(fast-reference)<.002f,"BVH agrees with triangle reference");shoot();
            verify(bays[0].stats.shots==1&&bays[0].stats.hits==1,"manual shot hits visible character");
            auto point=Vector3Add(position,Vector3Scale(ray.direction,4));solids.push_back({{point.x-.5f,0,point.z-.1f},{point.x+.5f,3,point.z+.1f}});
            verify(cast(ray).kind==ShotHit::World,"solid cover blocks character hits");rebuildSolids();
        }
        if(testFrame==72){verify(bays[0].stats.onTarget>.95&&bays[0].stats.time>.95,"fixed simulation and on-target measurement");finish(0,"automated validation");verify(fs::exists(local(bays[0].stem+".csv")),"CSV written");captureNext=true;}
        if(testFrame==74){screenshot("range-firing.png");active=1;position={BayCenters[1],1.62f,-4.5f};yaw=0;pitch=-1.2f;}
        if(testFrame==77){screenshot("range-tracking.png");active=2;position={BayCenters[2],1.62f,-4.5f};}
        if(testFrame==80){screenshot("range-reflex.png");active=3;position={BayCenters[3],1.62f,-4.5f};}
        if(testFrame==83){screenshot("range-cover.png");position={27,1.62f,-6.5f};active=0;yaw=-70;pitch=0;}
        if(testFrame==86){screenshot("range-walkway.png");menu=true;SetWindowSize(1920,1080);auto time=bays[3].clock.time();bays[3].clock.advance(1,false);verify(time==bays[3].clock.time(),"pause freezes targets");}
        if(testFrame==89){
            screenshot("range-compact-menu.png");menu=false;unstuck();auto original=settings.model;
            chooseModel(1);verify(character.loaded,"model change loads");chooseModel(-1);verify(settings.model==original,"model change restores");
            verify(character.load(findModel(testModel),shader,settings),"model reload");
        }
        if(testFrame==93){
            position={0,1.62f,5};unstuck();verify(!blocksPlayer(position,solids),"unstuck returns to walkable position");
            auto stopped=movePlayer({.55f,1.62f,8},{-2,0,0},solids);verify(stopped.x>=.4f,"wall collision prevents tunnelling");
            auto originalName=character.filename;auto originalCount=character.query.triangles.size();Settings trial=settings;
            verify(!character.load(local("build-temp/broken.glb"),shader,trial)&&character.loaded&&character.filename==originalName&&character.query.triangles.size()==originalCount,"invalid replacement preserves the current model");
            active=0;bays[0].page=1;bays[0].dirty=true;settings.fov=75;SetWindowSize(1920,1080);
            auto center=boardCenter(0);position={center.x,1.8f,-4.8f};aimAt(center);
            float height=bays[0].settings.height;control(0,{ControlKind::Increase,0});verify(bays[0].settings.height>height,"target height control");
            control(0,{ControlKind::Increase,2});verify(settings.pose>=-1&&settings.pose<character.animationCount,"shared frozen pose selection");
        }
        if(testFrame==96){screenshot("range-target-panel.png");bays[0].page=2;bays[0].dirty=true;}
        if(testFrame==99){screenshot("range-optics-panel.png");float fov=settings.fov;control(0,{ControlKind::Increase,0});verify(settings.fov==fov+5,"optics apply to the camera");}
        if(testFrame==100){
            bays[0].settings.duration=15;start(0,false);bays[0].clock.ticks=1798;bays[0].stats.time=bays[0].clock.time();
        }
        if(testFrame==103){verify(!bays[0].running&&bays[0].finished&&bays[0].stats.time>=15,"timed drill completes automatically");bays[0].reset();menu=true;pausedTestTicks=bays[0].clock.ticks;}
        if(testFrame==108){verify(bays[0].clock.ticks==pausedTestTicks,"menu freezes simulation across rendered frames");menu=false;}
        if(testFrame==110){verify(bays[0].clock.ticks>pausedTestTicks,"simulation resumes after the menu");}
        if(testFrame==113){
            active=0;unstuck();auto& b=bays[0];b.settings.count=1;b.settings.motion=0;b.settings.readySeconds=3;b.settings.hitResponse=1;b.settings.respawnDelay=.15f;
            start(0);aimTestTarget();shoot();verify(b.stats.shots==0&&b.stats.hits==0&&b.clock.ticks==0,"countdown excludes shots and freezes target time");menu=true;
        }
        if(testFrame==116){verify(bays[0].readyLeft==3&&bays[0].clock.ticks==0,"menu pauses preparation countdown");menu=false;lastShot=-100;}
        if(testFrame==117){screenshot("range-countdown.png");bays[0].readyLeft=.02;}
        if(testFrame==120){
            auto& b=bays[0];verify(b.readyLeft==0&&b.clock.ticks>0,"countdown transitions to a live drill");aimTestTarget();shoot();
            verify(b.stats.hits==1&&!entity(0,0).active&&b.generations[0]==1,"clicking hit removes target until respawn");
            verify(b.stats.acquisition.delays.size()==1&&b.stats.acquisition.median()>0,"visibility acquisition sample recorded");
        }
        if(testFrame==131){
            auto& b=bays[0];auto e=entity(0,0);verify(e.active&&std::abs(e.position.x-BayCenters[0])>.001f&&std::abs(e.position.x-BayCenters[0])<=b.settings.spread,"static clicking target respawns at a seeded new position");
            finish(0,"countdown and respawn validation");verify(fs::exists(local(b.stem+"-shots.csv")),"per-shot event report saved");
            b.page=3;b.dirty=true;auto center=boardCenter(0);position={center.x,1.8f,-4.8f};aimAt(center);
            int dpi=settings.mouseDpi;std::string previousKey=contextKey(0);aimAt(panelPoint(0,{940,238}));shoot();
            verify(settings.mouseDpi==dpi+100&&contextKey(0)!=previousKey,"physical calibration control updates DPI and comparison key");
            double cm=cmPerTurn(settings.sensitivity,settings.mouseDpi);control(0,{ControlKind::Increase,1});
            verify(!glfwRawMouseMotionSupported()||(settings.rawMouse&&cmPerTurn(settings.sensitivity,settings.mouseDpi)>cm+.99),"physical turn distance calibration applies raw sensitivity");
            aimAt(center);control(0,{ControlKind::Increase,6});lastShot=-100;
        }
        if(testFrame==134){verify(!settings.antialias&&sceneAA.samples==0&&sceneAA.fbo==0,"MSAA can be disabled and its framebuffer released");control(0,{ControlKind::Increase,6});}
        if(testFrame==137){
            verify(sceneAA.samples>=2&&sceneAA.fbo&&shadows.ready,"multisample and shadow framebuffers initialized on this GPU");screenshot("range-input-panel.png");
            start(0,false);control(0,{ControlKind::Increase,7});verify(bays[0].running&&bays[0].page==0,"page navigation preserves the timed run");bays[0].runWidth=1919; // Exercise resize detection while rendering only at 1080p.
        }
        if(testFrame==140){verify(!bays[0].running&&bays[0].finished,"viewport resizing ends and saves the run before mixing configurations");}
        if(testFrame==145){
            active=0;unstuck();aimTestTarget();active=1;int previous=bays[0].stats.shots,here=bays[1].stats.shots;
            shoot();verify(bays[0].stats.shots==previous&&bays[1].stats.shots==here,"shooting a different bay cannot alter paused scores");active=0;
            verify(websiteRequests==0,"startup and ordinary rendering never open the website");
            visitWebsite();verify(menu&&!captured&&websiteRequests==1,"explicit website action keeps the range paused and releases capture");pausedTestTicks=bays[0].clock.ticks;
        }
        if(testFrame==148){
            verify(bays[0].clock.ticks==pausedTestTicks,"website action does not resume simulation");screenshot("vaporlock-menu.png");menu=false;
            auto center=boardCenter(0);position={center.x,1.8f,-4.8f};aimAt(center);int other=bays[1].settings.count,controls=0;
            for(int page=0;page<4;page++){
                aimAt(panelPoint(0,{144.0f+240*page,155}));shoot();verify(bays[0].page==page,"every rendered tab maps to its page");
                for(int row=0;row<8;row++){
                    if(page==3&&row==7)continue;
                    for(float x:{940.0f,810.0f}){
                        aimAt(panelPoint(0,{x,238.0f+74*row}));auto hit=cast({position,forward(yaw,pitch)});
                        verify(hit.kind==ShotHit::Board&&panelControl(hit.pixel).index==row,"rendered adjustment reaches the intended row");shoot();++controls;
                    }
                }
            }
            verify(controls==62&&bays[1].settings.count==other,"all numeric and toggle controls preserve other bay target settings");
            aimAt(panelPoint(0,{850,875}));shoot();verify(bays[0].settings.count==bayDefaults(0).count,"physical defaults control restores the bay");
            aimAt(panelPoint(0,{185,875}));shoot();verify(bays[0].running,"physical start control begins preparation");
            aimAt(panelPoint(0,{550,875}));shoot();verify(!bays[0].running&&!bays[0].finished&&bays[0].clock.ticks==0,"physical reset clears the run");
            aimAt(center);lastShot=-100;
            writeText("reports/website-link-check.json","{\"result\":\"passed\",\"url\":\"https://github.com/VaporUnlocked/VaporLock\",\"automatic_requests\":0,\"explicit_requests\":1,\"browser_opened_during_test\":false,\"method\":\"Menu action exercised with external launch suppressed in validation mode\"}\n");
        }
        if(testFrame==151){screenshot("vaporlock-controls.png");menu=true;}
        if(testFrame==152){
            pendingClicks=0;captured=true;menu=false;
            queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_PRESS,true);queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_RELEASE,true);queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_PRESS,true);
            verify(pendingClicks==2,"separate short clicks survive within one render frame");
            menu=true;queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_PRESS,true);menu=false;captured=false;queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_PRESS,true);
            captured=true;queueClick(GLFW_MOUSE_BUTTON_LEFT,GLFW_PRESS,false);queueClick(GLFW_MOUSE_BUTTON_RIGHT,GLFW_PRESS,true);
            verify(pendingClicks==2,"menu unfocused uncaptured and other-button events cannot queue shots");pendingClicks=0;captured=false;menu=true;
        }
        if(testFrame==112){
            Settings s=bays[0].settings;s.count=1;s.motion=0;auto e=rangeEntity(s,0,0,0);Matrix transform=character.transform(e,s);
            Ray ray{{e.position.x,1.2f,e.position.z-5},{0,0,1}};double sink=0;
            auto begin=std::chrono::steady_clock::now();for(int i=0;i<2000;i++)sink+=std::min(100.0f,character.query.worldHit(ray,transform));
            double fastUs=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count()/2000;
            begin=std::chrono::steady_clock::now();for(int i=0;i<40;i++)for(int m=0;m<character.model.meshCount;m++){auto hit=GetRayCollisionMesh(ray,character.model.meshes[m],transform);if(hit.hit)sink+=hit.distance;}
            double referenceUs=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count()/40;
            writeText("reports/range-query-benchmark.json","{\"model\":\""+jsonEscape(character.filename)+"\",\"bvh_us_per_query\":"+num(fastUs,3)+",\"reference_us_per_query\":"+num(referenceUs,3)+",\"checksum\":"+num(sink,3)+",\"note\":\"Local synthetic ray benchmark; not an FPS guarantee.\"}\n");
        }
        if(testFrame==154){
            verify(settings.quality==DefaultQuality,"quality defaults to Mid");start(0,false);auto oldKey=contextKey(0);selectQuality(0);
            verify(!bays[0].running&&contextKey(0)!=oldKey,"quality change saves the timed run and changes comparison context");menu=false;
        }
        if(testFrame==157||testFrame==161||testFrame==165||testFrame==169){
            const auto& p=graphicsPreset(settings.quality);
            verify(sceneAA.requested==p.samples&&((p.samples==0&&sceneAA.fbo==0)||(sceneAA.samples>=2&&sceneAA.samples<=p.samples)),"preset applies its MSAA allocation");
            verify(shadows.requested==p.shadowSize&&(p.shadowSize==0?!shadows.ready&&shadows.target.id==0:shadows.ready&&shadows.target.depth.width==p.shadowSize),"preset applies or releases its shadow texture");
            screenshot(std::string("quality-")+p.name+".png");
        }
        if(testFrame==158)selectQuality(1);
        if(testFrame==162)selectQuality(2);
        if(testFrame==166)selectQuality(3);
        if(testFrame==170){selectQuality(DefaultQuality);menu=true;qualityOpen=true;qualityCursor=DefaultQuality;}
        if(testFrame==172){
            screenshot("quality-dropdown.png");auto option=qualityOption(float(GetScreenWidth()),float(GetScreenHeight()),1);
            qualityClick({option.x+10,option.y+10});verify(settings.quality==1&&!qualityOpen&&menu,"dropdown row selects High and keeps the menu paused");
            qualityOpen=true;qualityClick({0,0});verify(settings.quality==1&&!qualityOpen&&menu,"outside click dismisses without changing quality or resuming");selectQuality(DefaultQuality);
        }
        if(testFrame==181){
            auto oldPosition=position;int oldActive=active;auto oldCamera=camera;
            std::array<uint64_t,BayCount> ticks{};std::array<int,BayCount> hits{};
            for(int i=0;i<BayCount;i++){ticks[i]=bays[i].clock.ticks;hits[i]=bays[i].stats.hits;}
            for(int visit=0;visit<BayCount;visit++){
                position={BayCenters[visit],1.62f,-4.5f};active=visit;updateCamera();
                verify(occupiedBay(position)==visit&&entity(visit,0).position.z>=FiringLine,"entering a bay creates its target instances");
                for(int other=0;other<BayCount;other++)if(other!=visit)for(int id=0;id<bays[other].settings.count;id++)
                    verify(!entity(other,id).active,"all unoccupied bays have no live target instances");
            }
            position={0,1.62f,-10};for(int i=0;i<BayCount;i++)verify(!entity(i,0).active,"leaving every bay removes every target instance");
            for(int i=0;i<BayCount;i++)verify(bays[i].clock.ticks==ticks[i]&&bays[i].stats.hits==hits[i],"unloading a bay preserves its paused timer and score");
            position=oldPosition;active=oldActive;camera=oldCamera;
            verify(environment[0].ready&&environment[1].ready&&emissive[1].ready,"static range meshes remain resident");
            settings.antialias=false;captureNext=true;
        }
        if(testFrame==184){verify(viewport.id&&viewport.texture.width==1920&&viewport.texture.height==1080,"single-sample clean capture remains 1080p");settings.antialias=true;}
        if(testFrame==192){
            writeText("reports/range-check.json","{\"result\":\"passed\",\"frames\":192,\"msaa_samples\":"+std::to_string(sceneAA.samples)+",\"shadow_map_ready\":true,\"quality\":\"Mid\",\"model\":\""+jsonEscape(character.filename)+"\",\"triangles\":"+std::to_string(character.triangles)+",\"bvh_nodes\":"+std::to_string(character.query.nodes.size())+",\"checks\":[\"four rendered bays\",\"physical control picking\",\"independent bay settings\",\"control shots excluded\",\"triangle-accurate hits\",\"BVH reference comparison\",\"solid occlusion\",\"timed metrics\",\"local CSV and JSON\",\"clean scene export\",\"Esc menu\",\"compact layout\",\"model replacement and reload\",\"invalid replacement preserves character\",\"all panel pages\",\"pose and height controls\",\"optics control\",\"timer completion\",\"menu pause and resume\",\"wall collision\",\"unstuck\",\"countdown freeze and pause\",\"respawn after hit\",\"acquisition measurement\",\"per-shot export\",\"DPI and physical sensitivity\",\"MSAA toggle\",\"shadow framebuffer\",\"navigation retains run\",\"resize ends run\",\"inactive bay scoring isolation\",\"website action without external launch\",\"all 62 adjustment buttons\",\"physical defaults and reset\",\"multiple click events per frame\",\"focus and menu shot gates\",\"all graphics presets\",\"quality dropdown picking\",\"quality ends timed run\",\"occupied bay targets only\",\"bay transitions preserve state\",\"single scene composition\",\"capture allocation deferred\"]}\n");quit=true;
        }
    }
    void frame(){profile.begin();
        double dt=testing?1.0/60:double(GetFrameTime());if(!std::isfinite(dt)||dt<0)dt=0;elapsed+=std::min(dt,.25);
        bool previousMenu=menu;input(dt);if(menu&&!previousMenu){refreshModels();for(auto& b:bays)b.dirty=true;}
        for(int i=0;i<BayCount;i++)if(bays[i].running&&(bays[i].runWidth!=GetScreenWidth()||bays[i].runHeight!=GetScreenHeight()))finish(i,"viewport resized");
        int cap=benchmarking?0:testing?60:(!captured||menu||!IsWindowFocused())?30:FrameCaps[settings.frameCap];
        if(cap!=appliedCap){SetTargetFPS(cap);appliedCap=cap;}
        if(IsWindowMinimized()&&!testing&&!benchmarking){PollInputEvents();WaitTime(.03);return;}
        frameAverage=frameAverage*.95+dt*1000*.05;kick=std::max(0.0f,kick-float(dt)*7);
        bool live=!menu&&occupiedBay(position)==active&&(testing||startupCheck||benchmarking||(captured&&IsWindowFocused()));double before=bays[active].clock.time();
        if(live&&!bays[active].finished){auto& b=bays[active];double simulationDt=consumeLeadIn(b.readyLeft,dt);advancePractice(b.clock,simulationDt,b.running,b.settings.duration);}
        updateCamera();if(live)for(int click=0;click<fireCount;click++)shoot();
        if(live&&bays[active].readyLeft<=0)measure(std::max(0.0,bays[active].clock.time()-before),float(dt*1000));
        int panel=-1;Control controlHover;
        if(!menu){auto focus=cast({position,forward(yaw,pitch)},true);if(focus.kind==ShotHit::Board){panel=focus.bay;controlHover=panelControl(focus.pixel);}}
        if(panel!=hoveredBay||controlHover.kind!=hoveredControl.kind||controlHover.index!=hoveredControl.index){
            if(hoveredBay>=0)bays[hoveredBay].dirty=true;if(panel>=0)bays[panel].dirty=true;hoveredBay=panel;hoveredControl=controlHover;
        }
        profile.mark(0);profile.gpuBegin();if(elapsed-lastBoards>.2){renderSign(active);lastBoards=elapsed;}
        for(int i=0;i<BayCount;i++)if(bays[i].dirty){renderSign(i);renderPanel(i);}profile.mark(1);renderScene();
        BeginTextureMode(presentation);hud();pauseMenu();EndTextureMode();profile.mark(4);
        BeginDrawing();ClearBackground(Ink);DrawTexturePro(presentation.texture,{0,0,float(presentation.texture.width),-float(presentation.texture.height)},{0,0,float(GetScreenWidth()),float(GetScreenHeight())},{0,0},0,WHITE);if(profile.enabled){rlDrawRenderBatchActive();profile.gpuEnd();}EndDrawing();profile.mark(5);
        if(!testing&&!shown){ClearWindowState(FLAG_WINDOW_HIDDEN);showStartupWindow(GetWindowHandle(),!startupCheck&&!benchmarking);if(startupCheck||benchmarking)SetWindowSize(1920,1080);shown=true;appLog<<"STARTUP: visible_on_screen="<<ownWindowIsVisibleOnScreen(GetWindowHandle())<<std::endl;}
        if(startupCheck&&++startupFrames==12){
            bool visible=ownWindowIsVisibleOnScreen(GetWindowHandle());writeText("reports/startup-check.json",std::string("{\"result\":\"")+(visible?"passed":"failed")+"\",\"visible_on_screen\":"+(visible?"true":"false")+"}\n");verify(visible,"startup window visible");quit=true;
        }
        if(testing)checkFrame();
    }
    void close(){
        glfwSetMouseButtonCallback(glfwGetCurrentContext(),previousButtonHandler);inputOwner=nullptr;
        finishAll("exited");release();save();for(auto& batch:environment)batch.unload();for(auto& batch:emissive)batch.unload();character.unload();rifle.unload();UnloadModel(cube);UnloadShader(shader);UnloadShader(environmentShader);sceneAA.unload();shadows.unload();neon.unload();profile.unload();
        for(auto& b:boards)if(b.id)UnloadRenderTexture(b);for(auto& s:signs)if(s.id)UnloadRenderTexture(s);
        for(auto& plate:distancePlates)if(plate.id)UnloadRenderTexture(plate);
        if(viewport.id)UnloadRenderTexture(viewport);if(presentation.id)UnloadRenderTexture(presentation);if(uiFont.texture.id!=GetFontDefault().texture.id)UnloadFont(uiFont);CloseWindow();
    }
};
#include "fps_benchmark.hpp"
}
int main(int argc,char** argv){
    bool test=argc>1&&(std::string(argv[1])=="--render-check"||std::string(argv[1])=="--range-check");
    bool startup=argc>1&&std::string(argv[1])=="--startup-check";
    bool profiling=argc>1&&std::string(argv[1])=="--profile-check";bool benchmark=profiling||(argc>1&&std::string(argv[1])=="--fps-check");
    try{
        establishRoot(executableFolder());preparePortableLayout();installFileCallbacks();
        appLog.open(local("reports/runtime-"+timestamp()+"-"+std::to_string(ownProcessId())+".log"),std::ios::trunc);appLog<<std::unitbuf<<"STARTUP: process "<<ownProcessId()<<std::endl;
        std::string model;if(argc>2&&(test||std::string(argv[1])=="--model"))model=argv[2];
        int quality=DefaultQuality;
        if(benchmark&&argc>2){std::string wanted=argv[2];std::transform(wanted.begin(),wanted.end(),wanted.begin(),[](unsigned char c){return char(std::tolower(c));});quality=-1;
            for(int i=0;i<QualityCount;i++){std::string name=graphicsPreset(i).name;std::transform(name.begin(),name.end(),name.begin(),[](unsigned char c){return char(std::tolower(c));});if(wanted==name)quality=i;}
            if(quality<0)throw std::runtime_error("FPS quality must be max, high, mid or potato.");}
        App app;app.profile.enabled=profiling;app.benchmarking=benchmark;app.setup(test,startup,model);
        if(benchmark)runFpsBenchmark(app,quality);else while(!app.quit&&!WindowShouldClose())app.frame();
        app.close();appLog<<"SHUTDOWN: clean exit"<<std::endl;return 0;
    }catch(const std::exception& e){if(appLog)appLog<<"FATAL: "<<e.what()<<std::endl;if(!test&&!startup&&!benchmark)showError(e.what());return 1;}
}

