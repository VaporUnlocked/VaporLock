#include "storage.hpp"
#include "range.hpp"
#include "mesh_query.hpp"
#include "training.hpp"
#include "branding.hpp"
#include "bundled_assets.hpp"
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <cstring>
using namespace suite;
static int checks=0;
void check(bool v,const char* message){++checks;if(!v)throw std::runtime_error(message);}
bool near(double a,double b,double epsilon=1e-5){return std::abs(a-b)<epsilon;}
std::string glb(const std::string& input) {
    auto json=input;while(json.size()%4)json+=" ";
    std::string out(20,'\0');
    uint32_t header[]={0x46546c67,2,uint32_t(20+json.size()),uint32_t(json.size()),0x4e4f534a};
    std::memcpy(out.data(),header,20);return out+json;
}
int main(int argc,char** argv) {
    try {
        if(argc!=2)throw std::runtime_error("Provide the suite root.");
        establishRoot(pathFromUtf8(argv[1]));installFileCallbacks();
        Settings s;
        check(near(verticalFov(90,16.0f/9),58.7155,0.001),"Horizontal to vertical FOV conversion");
        check(near(angularError({0,0,0},{0,0,1},{0,0,10}),0),"Centered aim error");
        check(near(angularError({0,0,0},{0,0,1},{10,0,10}),45,0.001),"Off-axis angular error");
        float distance=0;
        check(rayBox({0,1,0},{0,0,1},{{-1,0,5},{1,2,6}},distance)&&near(distance,5),"Ray box front hit");
        check(!rayBox({2,1,0},{0,0,1},{{-1,0,5},{1,2,6}},distance),"Parallel ray outside box");
        check(!rayBox({0,1,0},{0,0,-1},{{-1,0,5},{1,2,6}},distance),"Box behind camera");
        check(covered({0,1,0},{0,1,10},{{{-1,0,5},{1,2,6}}}),"Cover occludes aimpoint");
        check(!covered({0,1,0},{0,1,3},{{{-1,0,5},{1,2,6}}}),"Cover behind target does not occlude");
        Clock sixty,twoForty;
        for(int i=0;i<600;i++)sixty.advance(1.0/60,true);
        for(int i=0;i<2400;i++)twoForty.advance(1.0/240,true);
        check(sixty.ticks==1200&&twoForty.ticks==1200,"Frame-rate independent fixed simulation");
        sixty.advance(2,false);check(sixty.ticks==1200,"Pause freezes simulation");
        sixty.advance(2,true);check(sixty.ticks==1230&&near(sixty.discarded,1.75),"Large stalls capped and accounted for");
        sixty.advance(std::numeric_limits<double>::quiet_NaN(),true);check(sixty.ticks==1230,"Non-finite clock input ignored");
        Clock boundary;boundary.ticks=1799;advancePractice(boundary,.2,true,15);
        check(boundary.ticks==1800&&near(boundary.time(),15),"Long finishing frame cannot extend a timed session beyond its final tick");
        Clock stalledClock;advancePractice(stalledClock,4,true,60);
        check(stalledClock.ticks==30&&near(stalledClock.discarded,3.75),"Full stall duration is accounted for without a hidden two-second cap");
        Clock fractional;fractional.ticks=1800;advancePractice(fractional,.2,true,15.001);
        check(fractional.ticks==1801,"Fractional duration ends at the first fixed tick at or after its deadline");
        check(nextMenuFocus(-1,false)==0&&nextMenuFocus(-1,true)==5&&nextMenuFocus(5,false)==0&&nextMenuFocus(0,true)==5,"Keyboard menu traversal enters and wraps in both directions");
        for(auto size:std::array<Vector2,3>{{{1040,720},{1440,900},{1920,1080}}}){
            auto card=pauseCard(size.x,size.y),link=websiteButton(size.x,size.y);
            check(card.x>=0&&card.y>=0&&card.x+card.width<=size.x&&card.y+card.height<=size.y,"Menu fits the supported viewport");
            check(link.x>=card.x&&link.y>=card.y&&link.x+link.width<=card.x+card.width&&link.y+link.height<=card.y+card.height,"Website control stays inside the visible menu");
            for(int i=0;i<QualityCount;i++){auto row=qualityOption(size.x,size.y,i);
                check(row.x>=card.x&&row.y>=card.y&&row.x+row.width<=card.x+card.width&&row.y+row.height<=card.y+card.height,"All dropdown choices stay inside the visible menu");}
        }
        check(near(cmPerTurn(.075f,800),15.24,.00001),"Raw counts and declared DPI convert to physical turn distance");
        for(int dpi:{400,800,1600,3200})for(double cm:{10.0,25.0,50.0,100.0})
            check(near(cmPerTurn(sensitivityForCm(cm,dpi),dpi),cm,.00001),"Physical sensitivity inverse preserves requested distance");
        double ready=.3;check(near(consumeLeadIn(ready,.1),0)&&near(ready,.2),"Preparation consumes frame time without simulation");
        check(near(consumeLeadIn(ready,.25),.05)&&near(ready,0),"Countdown boundary preserves only leftover frame time");
        check(consumeLeadIn(ready,std::numeric_limits<double>::quiet_NaN())==0,"Countdown ignores non-finite input");
        Clock lead60,lead240;double r60=3,r240=3;
        for(int i=0;i<600;i++)lead60.advance(consumeLeadIn(r60,1.0/60),true);
        for(int i=0;i<2400;i++)lead240.advance(consumeLeadIn(r240,1.0/240),true);
        check(lead60.ticks==840&&lead240.ticks==840,"Preparation leaves equal seven-second drills at 60 and 240 Hz");
        Acquisition acquisition;check(acquisition.median()<0,"No acquisition observations remain distinguishable from zero milliseconds");
        acquisition.observe(0,true,1);acquisition.hit(0,1.25);acquisition.hit(0,1.5);
        check(acquisition.delays.size()==1&&near(acquisition.median(),250),"Repeated hits do not duplicate a visibility acquisition sample");
        acquisition.observe(1,true,1);acquisition.hit(1,1.75);
        check(acquisition.switches==1&&acquisition.bestStreak==3&&near(acquisition.median(),500),"Target switches and even median use actual successful hits");
        acquisition.miss();check(acquisition.streak==0&&acquisition.bestStreak==3,"Miss breaks current streak and preserves best streak");
        acquisition.observe(0,false,2);acquisition.observe(0,true,3);acquisition.hit(0,3.125);
        check(acquisition.delays.size()==3&&near(acquisition.median(),250)&&acquisition.switches==2,"Reappearance starts a new acquisition sample and odd median is correct");
        acquisition.observe(2,false,4);acquisition.hit(2,4.1);
        check(acquisition.delays.size()==3,"A hit without a sampled visible reference does not invent a reaction measurement");
        for(int mode=0;mode<8;mode++)for(int id=0;id<32;id++)for(int tick=0;tick<100;tick++){
            s.count=32;s.motion=mode;auto e=entityAt(s,id,tick*.123);auto again=entityAt(s,id,tick*.123);
            check(e.position.x==again.position.x&&e.position.z==again.position.z&&e.active==again.active,"Repeatable seed/time");
            check(std::isfinite(e.position.x)&&std::abs(e.position.x)<=s.spread+.0001f&&e.position.z>=3,"Motion remains in bounds");
        }
        s.motion=0;auto a=entityAt(s,1,0),b=entityAt(s,1,100);check(a.position.x==b.position.x&&a.position.z==b.position.z,"Stationary targets stay still");
        s.motion=1;s.speed=0;a=entityAt(s,1,0);b=entityAt(s,1,100);check(a.position.x==b.position.x,"Zero speed freezes strafe");
        s.motion=7;s.mixedStill=true;a=entityAt(s,0,0);b=entityAt(s,0,100);check(a.position.x==b.position.x&&a.position.z==b.position.z,"Mixed mode stationary subset");
        s.speed=std::numeric_limits<float>::quiet_NaN();s.count=999;s.fov=-1;s.pose=999;s.sanitize();
        check(s.count==32&&near(s.speed,2.2)&&s.fov==45&&s.pose==255,"Settings bounds and nonfinite sanitization");
        check(confined("reports/a.json"),"Local file permitted");
        check(!confined("../outside.json"),"Parent traversal rejected");
        check(!confined(root().parent_path()/"outside.json"),"Absolute outside file rejected");
        check(!confined(fs::path(utf8(root())+"-other")/"data.json"),"Prefix sibling path rejected");
        check(!confined("settings.ini:extra"),"Alternate stream rejected");
        check(!confined("\\\\not-a-server\\share\\model.glb"),"Network path rejected without opening it");
        writeText("build-temp/test-settings.ini","count=-10\nspeed=nan\nfov=999\nmodel=../outside.glb\nsensitivity=invalid\nmotion=abc\n");
        auto parsed=loadSettings("build-temp/test-settings.ini");
        check(parsed.quality==DefaultQuality,"Existing preferences without a quality field default to Mid");
        for(int quality=0;quality<QualityCount;quality++){Settings preset; preset.quality=quality;saveSettings(preset,"build-temp/quality-roundtrip.ini");
            check(loadSettings("build-temp/quality-roundtrip.ini").quality==quality,"All quality choices persist across reloads");}
        writeText("build-temp/quality-invalid.ini","quality=999\n");check(loadSettings("build-temp/quality-invalid.ini").quality==DefaultQuality,"Invalid saved quality safely defaults to Mid");
        check(parsed.count==1&&parsed.fov==120&&near(parsed.speed,2.2),"Invalid values safely clamped");
        check(parsed.model=="SWAT.glb","Model path traversal rejected in settings");
        saveSettings(parsed,"build-temp/roundtrip.ini");auto roundtrip=loadSettings("build-temp/roundtrip.ini");
        check(roundtrip.count==parsed.count&&roundtrip.fov==parsed.fov&&roundtrip.model==parsed.model,"Settings round trip");
        parsed.mouseDpi=1600;parsed.readySeconds=4;parsed.hitResponse=1;parsed.respawnDelay=.7f;parsed.antialias=false;
        saveSettings(parsed,"build-temp/training-roundtrip.ini");auto trainingSettings=loadSettings("build-temp/training-roundtrip.ini");
        check(trainingSettings.mouseDpi==1600&&trainingSettings.readySeconds==4&&trainingSettings.hitResponse==1&&near(trainingSettings.respawnDelay,.7)&&!trainingSettings.antialias,"Calibration and drill response settings persist together");
        parsed.mouseDpi=0;parsed.readySeconds=99;parsed.hitResponse=99;parsed.respawnDelay=std::numeric_limits<float>::quiet_NaN();parsed.sanitize();
        check(parsed.mouseDpi==100&&parsed.readySeconds==5&&parsed.hitResponse==1&&near(parsed.respawnDelay,.45),"Calibration and respawn bounds sanitize malformed preferences");
        std::string reason;bool valid=validateGlb(local("assets/characters/SWAT.glb"),reason);
        check(valid,("Included model validates: "+reason).c_str());
        writeText("build-temp/external.glb",glb(R"({"asset":{"version":"2.0"},"meshes":[{"primitives":[]}],"buffers":[{"uri":"../outside.bin","byteLength":4}]})"));
        check(!validateGlb(local("build-temp/external.glb"),reason)&&reason.find("Embed")!=std::string::npos,"External buffer reference rejected before loading");
        writeText("build-temp/broken.glb","bad file");
        check(!validateGlb(local("build-temp/broken.glb"),reason),"Malformed model rejected");
        writeText("build-temp/transparent.glb",glb(R"({"asset":{"version":"2.0"},"materials":[{"alphaMode":"MASK"}],"meshes":[{"primitives":[]}]})"));
        check(!validateGlb(local("build-temp/transparent.glb"),reason)&&reason.find("opaque")!=std::string::npos,"Unsupported transparent hit surfaces are rejected explicitly");
        auto unicodePath=pathFromUtf8("build-temp/unicode-\xc3\xa9.txt");writeText(unicodePath,"local UTF-8 round trip");
        auto unicodeBytes=readBytes(unicodePath);
        check(std::string(unicodeBytes.begin(),unicodeBytes.end())=="local UTF-8 round trip","Confined UTF-8 file round trip");
        for(float boundary:{-16.0f,0.0f,16.0f}){
            check(occupiedBay({boundary-.001f,1.62f,0})!=occupiedBay({boundary+.001f,1.62f,0}),"Crossing a bay boundary switches residency");
            check(occupiedBay({boundary,1.62f,0})==occupiedBay({boundary+.001f,1.62f,0}),"Exact boundary belongs to a single deterministic bay");
        }
        for(Vector3 outside:std::array<Vector3,6>{{{33,1,0},{-33,1,0},{0,1,-10},{0,1,43},{0,-1,10},{0,7,10}}})
            check(occupiedBay(outside)==-1,"Outside all bay bounds unloads target instances");
// Range collision: diagonal sliding, stalled frames, panel front/reach and bay limits.
        auto walls=architecture();
        auto blocked=movePlayer({.6f,1.62f,8},{-2,0,1},walls);
        check(blocked.x>=.4f&&blocked.z>8.9f,"Wall collision slides along the unobstructed axis");
        auto free=movePlayer({24,1.62f,-6},{-1,0,1},walls);
        check(near(free.x,23,.001)&&near(free.z,-5,.001),"Free walkway movement");
        auto stalled=movePlayer({24,1.62f,-6},{0,0,100},walls);
        check(stalled.z<=-3,"Movement bounds a stalled frame");
        for(int bay=0;bay<4;bay++){
            check(occupiedBay({BayCenters[bay],1.62f,-4.5f})==bay,"Each firing approach loads exactly its own bay");
            check(occupiedBay({BayCenters[bay],1.05f,35})==bay,"Crouching inside the lane preserves bay residency");
            check(closestBay({BayCenters[bay],1,-4})==bay,"Bay selection matches lane center");
            check(!blocksPlayer({BayCenters[bay],1.62f,-6.5f},walls),"Every unstuck position is clear");
            auto center=boardCenter(bay);Ray ray{{center.x,center.y,center.z-4},{0,0,1}};
            auto hit=intersectPanel(ray,bay);check(hit.hit&&near(hit.distance,4)&&near(hit.pixel.x,512,.001)&&near(hit.pixel.y,512,.001),"Physical panel center maps to texture center");
            check(!intersectPanel({{center.x,center.y,center.z+1},{0,0,-1}},bay).hit,"Panel cannot be activated through its back");
            check(!intersectPanel({{center.x,center.y,center.z-13},{0,0,1}},bay).hit,"Panel interaction has bounded reach");
            Settings baySettings=bayDefaults(bay);baySettings.count=500;baySettings.spread=100;baySettings.distance=100;sanitizeBay(baySettings);
            check(baySettings.count==12&&baySettings.spread==5.5f&&baySettings.distance==30,"Per-bay limits keep targets inside the range");
            for(int mode=0;mode<8;mode++)for(int id=0;id<12;id++){
                baySettings.motion=mode;auto target=rangeEntity(baySettings,bay,id,53.2);
                check(std::abs(target.position.x-BayCenters[bay])<=5.501f&&target.position.z<=38.351f,"All motions remain inside their bay");
            }
        }
        check(visibleBox({{-1,0,4},{1,2,6}},{0,1,0},{0,0,1},90,1.6f),"Centered target survives frustum culling");
        check(!visibleBox({{-1,0,-6},{1,2,-4}},{0,1,0},{0,0,1},90,1.6f),"Target behind camera is culled");
        check(!visibleBox({{20,0,4},{22,2,6}},{0,1,0},{0,0,1},90,1.6f),"Off-screen target is culled");
        check(visibleBox({{4,0,4},{7,2,6}},{0,1,0},{0,0,1},90,1.6f),"Target crossing viewport edge is retained");
        Camera3D projectionCamera{{0,1,0},{0,1,1},{0,1,0},60,CAMERA_PERSPECTIVE};
        auto bounds=projectBounds({{-1,0,4},{1,2,6}},projectionCamera,1280,720);
        check(bounds.visible&&bounds.rectangle.x<640&&bounds.rectangle.x+bounds.rectangle.width>640,"Capture bounds contain a visible centered character");
        bounds=projectBounds({{-.1f,.5f,-.5f},{.1f,1.5f,1}},projectionCamera,1280,720);
        check(bounds.visible&&bounds.rectangle.width>0&&bounds.rectangle.height>0,"Partly behind-camera bounds are near-clipped rather than omitted");
        check(!projectBounds({{-1,0,-6},{1,2,-4}},projectionCamera,1280,720).visible,"Capture rejects fully behind-camera geometry");
        check(!projectBounds({{50,0,4},{52,2,6}},projectionCamera,1280,720).visible,"Capture rejects offscreen geometry");
        check(panelControl({940,312}).kind==ControlKind::Increase&&panelControl({940,312}).index==1,"Rendered increase tile picking");
        check(panelControl({810,312}).kind==ControlKind::Decrease,"Rendered decrease tile picking");
        check(panelControl({30,815}).kind==ControlKind::None,"Panel padding does not activate a control");
        check(panelControl({690,876}).kind==ControlKind::None,"Gaps between action tiles are inactive");
        check(panelControl({640,155}).index==2&&panelControl({640,155}).kind==ControlKind::Page,"Optics tab picking");
        check(panelControl({870,155}).index==3&&panelControl({870,155}).kind==ControlKind::Page,"Input calibration tab picking");
        check(panelControl({265,155}).kind==ControlKind::None,"Tab spacing does not activate a tab");
        // BVH comparisons use an independent raylib triangle routine on a generated grid.
        MeshQuery query;
        for(int x=-6;x<=6;x++)for(int y=-6;y<=6;y++){
            float z=4+float((x*x+y*y)%5);
            query.add({float(x),float(y),z},{float(x)+.8f,float(y),z},{float(x),float(y)+.8f,z});
        }
        query.build();
        for(int k=0;k<500;k++){
            Ray ray{{0,0,0},Vector3Normalize({(unitHash(172,k)*2-1)*7,(unitHash(311,k)*2-1)*7,6})};
            float reference=1e30f;for(auto& triangle:query.triangles){auto h=GetRayCollisionTriangle(ray,triangle.a,triangle.b,triangle.c);if(h.hit)reference=std::min(reference,h.distance);}
            float hit=query.hit(ray);check((hit==1e30f&&reference==1e30f)||near(hit,reference,.0001),"BVH nearest hit matches independent triangle intersection");
        }
        MeshQuery simple;simple.add({-1,-1,0},{1,-1,0},{0,1,0});simple.build();
        auto transformed=MatrixMultiply(MatrixScale(2,3,4),MatrixMultiply(MatrixRotateY(27*DEG2RAD),MatrixTranslate(1,2,7)));
        check(near(simple.worldHit({{1,2,0},{0,0,1}},transformed),7,.0001),"Inverse-transform BVH preserves world distance under rotation and nonuniform scale");
        check(near(simple.worldHit({{1,2,0},{0,0,1}},transformed,3),3),"BVH respects a closer solid occluder");
        check(simple.hit({{0,0,1},{0,0,1}})==1e30f,"BVH rejects intersections behind the ray");
        // Test the portable layout with no loose runtime assets and a Unicode path.
        auto originalRoot=root();
        auto isolated=local(pathFromUtf8("build-temp/portable-core-\xc3\xa9-"+std::to_string(ownProcessId())));
        fs::create_directories(isolated);establishRoot(isolated);preparePortableLayout();
        check(fs::is_directory(local("models"))&&fs::is_directory(local("reports")),"Portable first launch creates local model and report folders");
        check(!fs::exists(local("assets")),"Bundled models and font do not require an extracted asset folder");
        check(fs::exists(local("LICENSES.txt"))&&fs::exists(local("PORTABLE-README.txt"))&&fs::exists(local("models/README.txt")),"Portable first launch includes credits and model instructions");
        check(findModels().size()==1,"Clean portable setup discovers the cleared embedded character");
        for(const char* name:{"SWAT.glb"}){
            auto path=findModel(name);check(!fs::exists(path),"Built-in character needs no loose model file");
            check(validateGlb(path,reason),"Embedded GLB passes the real model validator");
        }
        auto font=readBytes("assets/fonts/Interface.ttf");
        check(font.size()==202460&&font[0]==0&&font[1]==1,"Font bytes load directly from executable resources");
        writeText("settings.ini","model=Ghost-MW2019.glb\n");
        check(loadSettings().model=="SWAT.glb","Removed built-in saved selection migrates to SWAT");
        writeText("models/Ghost-MW2019.glb",std::string(reinterpret_cast<const char*>(font.data()),font.size()));
        check(loadSettings().model=="Ghost-MW2019.glb","Actual custom model with a legacy name retains normal validation");
        check(!validateGlb(findModel("Ghost-MW2019.glb"),reason),"Invalid custom legacy-name data is not silently replaced");
        fs::remove(local("models/Ghost-MW2019.glb"));
        writeText("settings.ini","model=missing-custom.glb\n");
        check(loadSettings().model=="missing-custom.glb","Missing custom selection is not rewritten by built-in migration");
        bool missingCustomRejected=false;
        try{findModel("missing-custom.glb");}catch(const std::runtime_error&){missingCustomRejected=true;}
        check(missingCustomRejected,"Missing explicit custom model retains its error");
        int loadedSize=0;auto* loadedBytes=LoadFileData(utf8(local("assets/characters/SWAT.glb")).c_str(),&loadedSize);
        check(loadedBytes&&loadedSize==1560900&&std::memcmp(loadedBytes,"glTF",4)==0,"Renderer file callback can read the embedded model");UnloadFileData(loadedBytes);
        writeText("models/custom.GLB",std::string(reinterpret_cast<const char*>(font.data()),font.size()));
        writeText("models/detector.onnx","ignored detector network");
        writeText("models/nested/hidden.glb","ignored nested model");
        check(findModels().size()==2&&findModel("custom.GLB").parent_path()==local("models"),"Model discovery accepts GLB case variations and ignores ONNX and subfolders");
        check(!validateGlb(findModel("custom.GLB"),reason),"Invalid custom character is rejected");
        auto swat=readBytes("assets/characters/SWAT.glb");
        writeText("models/SWAT.glb",std::string(reinterpret_cast<const char*>(swat.data()),swat.size()));
        check(findModels().size()==2&&findModel("SWAT.glb").parent_path()==local("models"),"Custom names take priority without duplicate character entries");
        check(validateGlb(findModel("SWAT.glb"),reason),"Custom GLB loads from the new models location");
        writeText("settings.ini","quality=3\nmodel=custom.GLB\n");writeText("models/README.txt","user notes");
        preparePortableLayout();std::ifstream preserved(local("models/README.txt"));std::string notes;std::getline(preserved,notes);
        check(notes=="user notes"&&loadSettings().quality==3&&loadSettings().model=="custom.GLB","Repeated launches preserve local notes and saved preferences");
        check(readBytes("models/SWAT.glb")==swat,"Repeated launches preserve custom model bytes");
        check(!confined("../outside.glb"),"Portable relocation retains the same file boundary");
        establishRoot(originalRoot);
        writeText("reports/core-checks.json","{\"result\":\"passed\",\"assertions\":"+std::to_string(checks)+"}\n");
        std::cout<<checks<<" checks passed.\n";return 0;
    }catch(const std::exception& e){std::cerr<<"FAILED after "<<checks<<" checks: "<<e.what()<<"\n";return 1;}
}
