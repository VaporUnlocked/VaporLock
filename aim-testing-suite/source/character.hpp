#pragma once
#include "pose.hpp"
#include "mesh_query.hpp"
#include "rlgl.h"
#include <set>
using namespace suite;
const char* vertexShader=R"(#version 330
in vec3 vertexPosition;
in vec2 vertexTexCoord;
in vec3 vertexNormal;
in vec4 vertexColor;
uniform mat4 mvp;
uniform mat4 matModel;
uniform mat4 matNormal;
out vec3 world;
out vec3 normal;
out vec2 uv;
out vec4 color;
void main(){world=vec3(matModel*vec4(vertexPosition,1.0));normal=normalize(vec3(matNormal*vec4(vertexNormal,0.0)));uv=vertexTexCoord;color=vertexColor;gl_Position=mvp*vec4(vertexPosition,1.0);}
)";
const char* fragmentShader=R"(#version 330
in vec3 world;in vec3 normal;in vec2 uv;in vec4 color;
uniform sampler2D texture0;uniform vec4 colDiffuse;uniform vec3 cameraPosition;uniform vec3 fogColor;uniform float exposure;uniform vec3 lightTint;
uniform sampler2D shadowTexture;uniform mat4 shadowMatrix;uniform float shadowEnabled;uniform float surfaceDetail;uniform float surfaceColor;
uniform float shadowTexel;uniform int shadowRadius;
out vec4 finalColor;
vec3 decodeSrgb(vec3 c){return mix(c/12.92,pow((c+.055)/1.055,vec3(2.4)),step(vec3(.04045),c));}
vec3 encodeSrgb(vec3 c){return mix(c*12.92,1.055*pow(c,vec3(1.0/2.4))-.055,step(vec3(.0031308),c));}
float hashNoise(vec3 p){return fract(sin(dot(p,vec3(127.1,311.7,74.7)))*43758.5453);}
float softNoise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
 return mix(mix(mix(hashNoise(i),hashNoise(i+vec3(1,0,0)),f.x),mix(hashNoise(i+vec3(0,1,0)),hashNoise(i+vec3(1,1,0)),f.x),f.y),
 mix(mix(hashNoise(i+vec3(0,0,1)),hashNoise(i+vec3(1,0,1)),f.x),mix(hashNoise(i+vec3(0,1,1)),hashNoise(i+vec3(1,1,1)),f.x),f.y),f.z);}
float visibility(vec3 n){
 if(shadowEnabled<0.5)return 1.0;vec4 clip=shadowMatrix*vec4(world,1);vec3 p=clip.xyz/clip.w*.5+.5;
 if(any(lessThan(p,vec3(0)))||any(greaterThan(p,vec3(1))))return 1.0;
 float bias=max(.00007*(1.0-dot(n,normalize(vec3(-.35,.9,-.2)))),.00002)*(2048.0*shadowTexel),lit=0.0;
 for(int x=-shadowRadius;x<=shadowRadius;x++)for(int y=-shadowRadius;y<=shadowRadius;y++){float z=texture(shadowTexture,p.xy+vec2(x,y)*shadowTexel).r;lit+=p.z-bias<=z?1.0:0.0;}
 float count=float((shadowRadius*2+1)*(shadowRadius*2+1));return .25+.75*lit/count;
}
void main(){
 vec4 texel=texture(texture0,uv);
 // glTF base-color textures are sRGB; material/vertex factors are linear.
 // Procedural range colors arrive as sRGB UI colors instead.
 vec3 factor=colDiffuse.rgb*color.rgb;if(surfaceColor>.5)factor=decodeSrgb(factor);
 vec4 base=vec4(decodeSrgb(texel.rgb)*factor,1.0);
 vec3 n=normalize(gl_FrontFacing?normal:-normal);float diffuse=max(dot(n,normalize(vec3(-.35,.9,-.2))),0.0);
 if(surfaceDetail>.5){float grain=softNoise(world*5.0);base.rgb*=.87+.20*grain;}
 float shade=visibility(n);
 vec3 lit=base.rgb*(.43+diffuse*.88*shade)*exposure*lightTint;
 if(surfaceDetail>.5){float rim=pow(1.0-max(dot(n,normalize(cameraPosition-world)),0.0),5.0);lit+=vec3(.007,.010,.017)*rim;}
 float fog=clamp((length(world-cameraPosition)-20.0)/100.0,0.0,0.6);
 finalColor=vec4(mix(encodeSrgb(max(lit,vec3(0.0))),fogColor,fog),base.a);
}
)";

struct Character {
    Model model{};FrozenPose poseData;MeshQuery query;int animationCount=0;
    BoundingBox raw{};bool loaded=false;int triangles=0;
    std::string filename,status;
    std::vector<bool> doubleSided;
    void unload() {
        poseData.clear();query.clear();animationCount=0;
        if(loaded) {
            std::set<unsigned int> textures;
            for(int i=0;i<model.materialCount;i++)for(int j=0;j<=MATERIAL_MAP_BRDF;j++) {
                auto t=model.materials[i].maps[j].texture;
                if(t.id && t.id!=rlGetTextureIdDefault() && textures.insert(t.id).second)UnloadTexture(t);
            }
            UnloadModel(model);
        }
        model={};loaded=false;doubleSided.clear();
    }
    bool load(const fs::path& path,Shader shader,Settings& settings) {
        Character candidate;Settings next=settings;
        try {
            if(!candidate.loadPrepared(path,shader,next)){status=candidate.status;candidate.unload();return false;}
            using std::swap;
            swap(model,candidate.model);swap(raw,candidate.raw);swap(loaded,candidate.loaded);
            swap(triangles,candidate.triangles);swap(animationCount,candidate.animationCount);
            swap(filename,candidate.filename);swap(status,candidate.status);swap(query,candidate.query);
            swap(doubleSided,candidate.doubleSided);
            swap(poseData.bytes,candidate.poseData.bytes);swap(poseData.data,candidate.poseData.data);swap(poseData.rest,candidate.poseData.rest);
            settings=next;candidate.unload();return true;
        } catch(const std::exception& e){candidate.unload();status=e.what();return false;}
    }
    bool loadPrepared(const fs::path& path,Shader shader,Settings& settings) {
        std::string reason;if(!validateGlb(path,reason)){status=reason;return false;}
        Model next=LoadModel(utf8(path).c_str());
        // raylib 6.0 IsModelValid also requires GPU bone buffers when CPU skinning
        // is selected. Validate the actual buffers used by this frozen-pose renderer.
        bool usable=next.meshCount>0&&next.materialCount>0&&next.materials&&next.meshMaterial;
        for(int i=0;i<next.meshCount;i++){
            auto& m=next.meshes[i];
            usable=usable&&m.vertices&&m.vertexCount>0&&m.vboId&&m.vboId[0];
            if(m.indices)usable=usable&&m.vboId[6];
        }
        if(!usable){if(next.meshes)UnloadModel(next);status="The GLB could not be rendered.";return false;}
        unload();model=next;loaded=true;filename=utf8(path.filename());settings.model=filename;
        poseData.load(path);animationCount=int(poseData.data->animations_count);
        doubleSided.assign(model.materialCount,false);
        for(size_t i=0;i<poseData.data->materials_count&&i+1<doubleSided.size();i++)doubleSided[i+1]=poseData.data->materials[i].double_sided!=0;
        for(int i=0;i<model.materialCount;i++)model.materials[i].shader=shader;
        std::set<unsigned int> filteredTextures;
        for(int i=0;i<model.materialCount;i++) {
            auto& texture=model.materials[i].maps[MATERIAL_MAP_ALBEDO].texture;
            if(texture.id&&texture.id!=rlGetTextureIdDefault()&&filteredTextures.insert(texture.id).second) {
                GenTextureMipmaps(&texture);
                SetTextureFilter(texture,TEXTURE_FILTER_TRILINEAR);
            }
        }
        triangles=0;for(int i=0;i<model.meshCount;i++)triangles+=model.meshes[i].triangleCount;
        setPose(settings);
        status=filename+" loaded";return true;
    }
    void setPose(Settings& settings) {
        if(!loaded)return;
        // Skinning runs only when selecting a pose. Every entity then shares this frozen mesh.
        settings.pose=std::clamp(settings.pose,-1,animationCount-1);
        poseData.bake(model,settings.pose,settings.poseFrame);
        raw={{1e8f,1e8f,1e8f},{-1e8f,-1e8f,-1e8f}};
        for(int i=0;i<model.meshCount;i++){
            auto& m=model.meshes[i];float* v=m.animVertices?m.animVertices:m.vertices;
            for(int j=0;j<m.vertexCount;j++){
                Vector3 p{v[j*3],v[j*3+1],v[j*3+2]};
                raw.min=Vector3Min(raw.min,p);raw.max=Vector3Max(raw.max,p);
            }
        }
        const auto size=Vector3Subtract(raw.max,raw.min);
        if(!std::isfinite(size.x)||!std::isfinite(size.y)||!std::isfinite(size.z)||size.y<0.00001f)
            throw std::runtime_error("Invalid character bounds.");
        model.transform=MatrixTranslate(-(raw.min.x+raw.max.x)/2,-raw.min.y,-(raw.min.z+raw.max.z)/2);
        query.rebuild(model);
    }
    float instanceScale(const Settings& s)const {return s.height/(raw.max.y-raw.min.y);}
    Matrix transform(const Entity& e,const Settings& s)const {
        const float scale=instanceScale(s);
        auto m=MatrixMultiply(MatrixScale(scale,scale,scale),MatrixRotateY(s.facing*DEG2RAD));
        return MatrixMultiply(model.transform,MatrixMultiply(m,MatrixTranslate(e.position.x,e.position.y,e.position.z)));
    }
    BoundingBox bounds(const Entity& e,const Settings& s)const {
        auto matrix=transform(e,s);BoundingBox b{{1e8f,1e8f,1e8f},{-1e8f,-1e8f,-1e8f}};
        for(int i=0;i<8;i++){Vector3 v{(i&1)?raw.max.x:raw.min.x,(i&2)?raw.max.y:raw.min.y,(i&4)?raw.max.z:raw.min.z};
            v=Vector3Transform(v,matrix);b.min=Vector3Min(b.min,v);b.max=Vector3Max(b.max,v);}
        return b;
    }
    void draw(const Entity& e,const Settings& s,Color tint=WHITE)const {
        if(!loaded)return;auto matrix=transform(e,s);
        for(int i=0;i<model.meshCount;i++)drawPart(i,matrix,tint);
    }
    void drawPart(int mesh,Matrix matrix,Color tint=WHITE)const {
        int materialIndex=model.meshMaterial[mesh];auto& material=model.materials[materialIndex];
        auto& color=material.maps[MATERIAL_MAP_ALBEDO].color;Color original=color;
        color={static_cast<unsigned char>(color.r*tint.r/255),static_cast<unsigned char>(color.g*tint.g/255),static_cast<unsigned char>(color.b*tint.b/255),255};
        if(materialIndex<int(doubleSided.size())&&doubleSided[materialIndex])rlDisableBackfaceCulling();
        DrawMesh(model.meshes[mesh],material,matrix);rlEnableBackfaceCulling();color=original;
    }
    std::vector<std::string> poseNames()const {
        return poseData.names();
    }
};


