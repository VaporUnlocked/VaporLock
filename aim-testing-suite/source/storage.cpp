#include "storage.hpp"
#include "bundled_assets.hpp"
#include "external/cgltf.h"
#include <fstream>
#include <sstream>
#include <iomanip>
#include <chrono>
#include <cwctype>
#include <stdexcept>
#include <cstring>
#include <set>
namespace suite {
static fs::path projectRoot;
std::string utf8(const fs::path& path){auto v=path.u8string();return std::string(v.begin(),v.end());}
void establishRoot(fs::path path){projectRoot=fs::canonical(path);}
const fs::path& root(){return projectRoot;}
bool confined(const fs::path& path) {
    try {
        auto inside=[](const fs::path& candidate) {
            auto a=projectRoot.begin(),b=candidate.begin();
            for(;a!=projectRoot.end();++a,++b) {
                if(b==candidate.end())return false;
                auto x=a->wstring(),y=b->wstring();
                std::transform(x.begin(),x.end(),x.begin(),::towlower);
                std::transform(y.begin(),y.end(),y.begin(),::towlower);
                if(x!=y)return false;
            }
            return true;
        };
        auto lexical=(path.is_absolute()?path:projectRoot/path).lexically_normal();
        // Reject outside and network paths without first probing the filesystem.
        if(!inside(lexical))return false;
        auto b=lexical.begin();
        for(auto a=projectRoot.begin();a!=projectRoot.end();++a)++b;
        auto current=projectRoot;
        for(;b!=lexical.end();++b){
            if(b->wstring().find(L':')!=std::wstring::npos)return false;
            current/=*b;
            if(isReparsePoint(current))return false;
        }
        return inside(fs::weakly_canonical(lexical));
    } catch(...) {return false;}
}
fs::path local(const fs::path& path) {
    auto full=path.is_absolute()?path:projectRoot/path;
    if(!confined(full))throw std::runtime_error("File must stay inside this program's folder.");
    return full;
}
std::vector<unsigned char> readBytes(const fs::path& path) {
    auto full=local(path);
    auto bundled=bundledAsset(full.lexically_normal().lexically_relative(projectRoot));
    if(!bundled.empty())return {bundled.begin(),bundled.end()};
    std::ifstream in(full,std::ios::binary|std::ios::ate);
    if(!in)throw std::runtime_error("Cannot read "+utf8(path.filename()));
    auto n=in.tellg();
    if(n<0 || n>256*1024*1024)throw std::runtime_error("File exceeds the 256 MB limit.");
    std::vector<unsigned char> bytes(static_cast<size_t>(n));in.seekg(0);
    if(!bytes.empty() && !in.read(reinterpret_cast<char*>(bytes.data()),n))throw std::runtime_error("Incomplete file read.");
    return bytes;
}
void writeText(const fs::path& path,const std::string& text) {
    auto p=local(path);fs::create_directories(p.parent_path());
    std::ofstream out(p,std::ios::binary|std::ios::trunc);
    if(!out || !out.write(text.data(),std::streamsize(text.size())))throw std::runtime_error("Cannot save "+utf8(path.filename()));
    out.flush();out.close();
    if(!out)throw std::runtime_error("Could not finish saving "+utf8(path.filename()));
}
static unsigned char* readCallback(const char* name,int* count) {
    *count=0;
    try{auto bytes=readBytes(pathFromUtf8(name));auto* p=static_cast<unsigned char*>(MemAlloc(unsigned(bytes.size()+1)));
        if(!p)return nullptr;std::memcpy(p,bytes.data(),bytes.size());p[bytes.size()]=0;*count=int(bytes.size());return p;
    }catch(...){return nullptr;}
}
static char* textCallback(const char* name){int n=0;return reinterpret_cast<char*>(readCallback(name,&n));}
static bool saveCallback(const char* name,void* data,int count) {
    try{if(count<0)return false;writeText(pathFromUtf8(name),std::string(static_cast<char*>(data),size_t(count)));return true;}catch(...){return false;}
}
static bool saveTextCallback(const char* name,const char* text){return saveCallback(name,const_cast<char*>(text),int(std::strlen(text)));}
void installFileCallbacks(){SetLoadFileDataCallback(readCallback);SetLoadFileTextCallback(textCallback);SetSaveFileDataCallback(saveCallback);SetSaveFileTextCallback(saveTextCallback);}
Settings loadSettings(const fs::path& name) {
    Settings s;
    std::ifstream in(local(name)); std::string line;
    while(std::getline(in,line)) {
        auto eq=line.find('=');if(eq==std::string::npos)continue;
        auto k=line.substr(0,eq),v=line.substr(eq+1);
        try{
#define INT_FIELD(field) if(k==#field) s.field=std::stoi(v);
#define FLOAT_FIELD(field) if(k==#field) s.field=std::stof(v);
#define BOOL_FIELD(field) if(k==#field) s.field=(v=="1");
            INT_FIELD(count) INT_FIELD(motion) INT_FIELD(scene) INT_FIELD(light) INT_FIELD(seed) INT_FIELD(frameCap) INT_FIELD(pose)
            INT_FIELD(mouseDpi) INT_FIELD(hitResponse) INT_FIELD(readySeconds) INT_FIELD(quality)
            FLOAT_FIELD(speed) FLOAT_FIELD(distance) FLOAT_FIELD(spread) FLOAT_FIELD(height) FLOAT_FIELD(fov)
            FLOAT_FIELD(sensitivity) FLOAT_FIELD(facing) FLOAT_FIELD(poseFrame) FLOAT_FIELD(duration) FLOAT_FIELD(aimHeight)
            FLOAT_FIELD(appearanceInterval)
            FLOAT_FIELD(respawnDelay)
            BOOL_FIELD(cover) BOOL_FIELD(markers) BOOL_FIELD(hud) BOOL_FIELD(crosshair) BOOL_FIELD(mixedStill)
            BOOL_FIELD(rawMouse) BOOL_FIELD(weapon)
            BOOL_FIELD(antialias)
            if(k=="model" && !v.empty() && v.find_first_of("/\\:")==std::string::npos)s.model=v;
#undef INT_FIELD
#undef FLOAT_FIELD
#undef BOOL_FIELD
        }catch(...){}
    }
    s.sanitize();
    // Migrate only the removed built-in selection. An actual user-provided
    // file with that name remains a custom model and keeps its normal errors.
    if(s.model=="Ghost-MW2019.glb" && !fs::exists(local("models/Ghost-MW2019.glb"))
       && !fs::exists(local("assets/characters/Ghost-MW2019.glb")))s.model="SWAT.glb";
    return s;
}
void saveSettings(const Settings& s,const fs::path& name) {
    std::ostringstream o;o<<std::setprecision(9);
#define FIELD(field) o<<#field<<"="<<s.field<<"\n";
    FIELD(count) FIELD(motion) FIELD(scene) FIELD(light) FIELD(seed) FIELD(frameCap) FIELD(pose)
    FIELD(speed) FIELD(distance) FIELD(spread) FIELD(height) FIELD(fov) FIELD(sensitivity)
    FIELD(facing) FIELD(poseFrame) FIELD(duration) FIELD(aimHeight)
    FIELD(appearanceInterval)
    FIELD(cover) FIELD(markers) FIELD(hud) FIELD(crosshair) FIELD(mixedStill) FIELD(model)
    FIELD(rawMouse) FIELD(weapon)
    FIELD(mouseDpi) FIELD(hitResponse) FIELD(readySeconds) FIELD(respawnDelay) FIELD(antialias) FIELD(quality)
#undef FIELD
    // Settings are replaced only after a complete temporary write.
    auto temp=name;temp+=".tmp";writeText(temp,o.str());
    replaceFile(local(temp),local(name));
}
std::vector<fs::path> findModels() {
    std::vector<fs::path> results;
    std::set<std::wstring> names;
    auto add=[&](const fs::path& path){auto key=path.filename().wstring();std::transform(key.begin(),key.end(),key.begin(),::towlower);if(names.insert(key).second)results.push_back(path);};
    // Custom files take precedence. The legacy location is still recognized for existing users.
    for(const char* folder:{"models","assets/characters"}){
        const auto directory=local(folder);
        if(!fs::exists(directory))continue;
        for(const auto& entry:fs::directory_iterator(directory)){
            auto ext=entry.path().extension().wstring();std::transform(ext.begin(),ext.end(),ext.begin(),::towlower);
            if(ext==L".glb"&&confined(entry.path())&&entry.is_regular_file())add(entry.path());
        }
    }
    add(local("assets/characters/SWAT.glb"));
    std::sort(results.begin(),results.end(),[](const auto& a,const auto& b){return a.filename().wstring()<b.filename().wstring();});return results;
}
fs::path findModel(const std::string& filename){
    auto choices=findModels();
    for(const auto& path:choices)if(utf8(path.filename())==filename)return path;
    throw std::runtime_error("The requested local character is missing.");
}
bool validateGlb(const fs::path& path,std::string& reason) {
    cgltf_data* data=nullptr;
    try {
        auto bytes=readBytes(path);
        if(bytes.size()<20 || std::memcmp(bytes.data(),"glTF",4)!=0)throw std::runtime_error("Choose a binary glTF 2.0 (.glb) file.");
        cgltf_options options{};
        if(cgltf_parse(&options,bytes.data(),bytes.size(),&data)!=cgltf_result_success)throw std::runtime_error("The GLB could not be parsed.");
        if(data->meshes_count==0 || data->nodes_count>4096 || data->meshes_count>256 || data->skins_count>16)
            throw std::runtime_error("Use one character, with at most one skeleton, in a GLB file.");
        if(data->extensions_required_count>0)throw std::runtime_error("Export a plain GLB without required compression/material extensions.");
        for(size_t i=0;i<data->materials_count;i++){
            if(data->materials[i].alpha_mode!=cgltf_alpha_mode_opaque)
                throw std::runtime_error("Use opaque materials: transparent cutouts are not supported by geometric hit testing.");
            auto& texture=data->materials[i].pbr_metallic_roughness.base_color_texture;
            if(texture.texture&&(texture.texcoord!=0||texture.has_transform))
                throw std::runtime_error("Bake the base-color texture into the first UV set without texture-transform extensions.");
        }
        for(size_t i=0;i<data->buffers_count;i++)if(data->buffers[i].uri)throw std::runtime_error("Embed all geometry inside the GLB.");
        for(size_t i=0;i<data->images_count;i++) {
            if(data->images[i].uri)throw std::runtime_error("Embed all textures inside the GLB.");
            auto* mime=data->images[i].mime_type;
            if(mime && std::strcmp(mime,"image/png")!=0 && std::strcmp(mime,"image/jpeg")!=0)
                throw std::runtime_error("Use embedded PNG or JPEG textures.");
        }
        for(size_t i=0;i<data->skins_count;i++) {
            if(data->skins[i].joints_count>256)throw std::runtime_error("The skeleton exceeds 256 joints.");
            // Modular characters may repeat the same armature for each clothing mesh.
            if(i>0) {
                if(data->skins[i].joints_count!=data->skins[0].joints_count)
                    throw std::runtime_error("Use one shared skeleton for every character part.");
                for(size_t j=0;j<data->skins[i].joints_count;j++)
                    if(data->skins[i].joints[j]!=data->skins[0].joints[j])
                        throw std::runtime_error("Use one shared skeleton with matching joint order.");
            }
        }
        for(size_t i=0;i<data->meshes_count;i++)for(size_t j=0;j<data->meshes[i].primitives_count;j++) {
            auto& p=data->meshes[i].primitives[j];
            if(p.type!=cgltf_primitive_type_triangles || p.has_draco_mesh_compression || p.targets_count)
                throw std::runtime_error("Use triangulated geometry without compression or morph targets.");
            auto* positions=cgltf_find_accessor(&p,cgltf_attribute_type_position,0);
            if(!positions || positions->count==0 || positions->count>65535)
                throw std::runtime_error("Split the model into mesh parts below 65,536 vertices each.");
            if(!cgltf_find_accessor(&p,cgltf_attribute_type_normal,0))
                throw std::runtime_error("Export vertex normals with the character.");
            if(cgltf_find_accessor(&p,cgltf_attribute_type_joints,1)||cgltf_find_accessor(&p,cgltf_attribute_type_weights,1))
                throw std::runtime_error("Export at most four bone influences per vertex.");
        }
        for(size_t i=0;i<data->animations_count;i++)for(size_t j=0;j<data->animations[i].samplers_count;j++)
            if(data->animations[i].samplers[j].interpolation==cgltf_interpolation_type_cubic_spline)
                throw std::runtime_error("Resample animations using linear or step interpolation.");
        if(cgltf_load_buffers(&options,data,nullptr)!=cgltf_result_success || cgltf_validate(data)!=cgltf_result_success)
            throw std::runtime_error("The GLB contains invalid or missing geometry.");
        cgltf_free(data);return true;
    }catch(const std::exception& e){if(data)cgltf_free(data);reason=e.what();return false;}
}
std::string timestamp() {
    const auto now=std::chrono::system_clock::now();auto t=std::chrono::system_clock::to_time_t(now);
    std::tm tm{};localtime_s(&tm,&t);std::ostringstream o;
    o<<std::put_time(&tm,"%Y%m%d-%H%M%S")<<"-"<<std::setfill('0')<<std::setw(3)<<(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count()%1000);
    return o.str();
}
std::string jsonEscape(const std::string& s) {
    std::ostringstream out;
    for(unsigned char c:s){if(c=='"'||c=='\\')out<<'\\'<<c;else if(c<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c)<<std::dec;else out<<c;}return out.str();
}
}
