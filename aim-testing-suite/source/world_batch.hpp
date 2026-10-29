#pragma once
#include "raylib.h"
#include "raymath.h"
#include <vector>
#include <cstring>
#include <stdexcept>
#include <type_traits>
namespace suite {
// Immutable boxes become one GPU mesh per bay/material. Geometry and colors are unchanged.
struct WorldBatch {
    std::vector<float> vertices,normals,uv;std::vector<unsigned char> colors;
    Mesh mesh{};Material material{};bool ready=false;
    BoundingBox bounds{{1e20f,1e20f,1e20f},{-1e20f,-1e20f,-1e20f}};
    void box(const Mesh& cube,Vector3 center,Vector3 size,Color color){
        for(int i=0;i<cube.triangleCount*3;i++){
            int v=cube.indices?cube.indices[i]:i;
            Vector3 p{center.x+cube.vertices[v*3]*size.x,center.y+cube.vertices[v*3+1]*size.y,center.z+cube.vertices[v*3+2]*size.z};
            bounds.min=Vector3Min(bounds.min,p);bounds.max=Vector3Max(bounds.max,p);
            vertices.insert(vertices.end(),{p.x,p.y,p.z});normals.insert(normals.end(),{cube.normals[v*3],cube.normals[v*3+1],cube.normals[v*3+2]});
            uv.insert(uv.end(),{cube.texcoords[v*2],cube.texcoords[v*2+1]});colors.insert(colors.end(),{color.r,color.g,color.b,color.a});
        }
    }
    void upload(Shader shader){
        if(vertices.empty())return;mesh.vertexCount=int(vertices.size()/3);mesh.triangleCount=mesh.vertexCount/3;
        const auto copy=[](const auto& source){using T=typename std::decay_t<decltype(source)>::value_type;auto* p=static_cast<T*>(MemAlloc(unsigned(source.size()*sizeof(T))));if(!p)throw std::runtime_error("Could not allocate static range geometry.");std::memcpy(p,source.data(),source.size()*sizeof(T));return p;};
        mesh.vertices=copy(vertices);mesh.normals=copy(normals);mesh.texcoords=copy(uv);mesh.colors=copy(colors);
        UploadMesh(&mesh,false);material=LoadMaterialDefault();material.shader=shader;ready=true;
        vertices.clear();vertices.shrink_to_fit();normals.clear();normals.shrink_to_fit();uv.clear();uv.shrink_to_fit();colors.clear();colors.shrink_to_fit();
    }
    void draw()const{if(ready)DrawMesh(mesh,material,MatrixIdentity());}
    void unload(){if(ready){UnloadMesh(mesh);MemFree(material.maps);}mesh={};material={};ready=false;}
};
}
