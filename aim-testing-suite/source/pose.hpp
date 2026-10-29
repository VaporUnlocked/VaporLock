#pragma once
#include "storage.hpp"
#include "raymath.h"
#include "external/cgltf.h"
#include <array>
#include <cstring>
#include <stdexcept>

namespace suite {
inline Matrix gltfMatrix(const float* f) {
    return {f[0],f[4],f[8],f[12],f[1],f[5],f[9],f[13],f[2],f[6],f[10],f[14],f[3],f[7],f[11],f[15]};
}
// Bake glTF joint transforms once into static world-space vertex buffers.
// Keeping this small importer separate avoids per-frame skeletal work and makes
// rendering, ray tests and exported bounds use exactly the same posed geometry.
struct FrozenPose {
    std::vector<unsigned char> bytes;
    cgltf_data* data=nullptr;
    struct Rest {std::array<float,3> t,s;std::array<float,4> r;bool ht,hs,hr,hm;};
    std::vector<Rest> rest;
    FrozenPose()=default;
    FrozenPose(const FrozenPose&)=delete;
    FrozenPose& operator=(const FrozenPose&)=delete;
    ~FrozenPose(){clear();}
    void clear(){if(data)cgltf_free(data);data=nullptr;rest.clear();bytes.clear();}
    void load(const fs::path& path) {
        clear();bytes=readBytes(path);cgltf_options options{};
        if(cgltf_parse(&options,bytes.data(),bytes.size(),&data)!=cgltf_result_success ||
           cgltf_load_buffers(&options,data,nullptr)!=cgltf_result_success)throw std::runtime_error("Cannot read the character pose.");
        for(size_t i=0;i<data->nodes_count;i++){
            auto& n=data->nodes[i];Rest r{};
            std::copy(n.translation,n.translation+3,r.t.begin());std::copy(n.scale,n.scale+3,r.s.begin());std::copy(n.rotation,n.rotation+4,r.r.begin());
            r.ht=n.has_translation!=0;r.hs=n.has_scale!=0;r.hr=n.has_rotation!=0;r.hm=n.has_matrix!=0;rest.push_back(r);
        }
    }
    std::vector<std::string> names()const {
        std::vector<std::string> result{"Original pose"};
        if(data)for(size_t i=0;i<data->animations_count;i++){
            std::string n=data->animations[i].name?data->animations[i].name:"Pose "+std::to_string(i+1);
            auto split=n.find_last_of('|');if(split!=std::string::npos)n=n.substr(split+1);result.push_back(n);
        }
        return result;
    }
    void bake(Model& model,int animation,float fraction) {
        for(size_t i=0;i<data->nodes_count;i++) {
            auto& n=data->nodes[i];auto& r=rest[i];
            std::copy(r.t.begin(),r.t.end(),n.translation);std::copy(r.s.begin(),r.s.end(),n.scale);std::copy(r.r.begin(),r.r.end(),n.rotation);
            n.has_translation=r.ht;n.has_scale=r.hs;n.has_rotation=r.hr;n.has_matrix=r.hm;
        }
        if(animation>=0 && size_t(animation)<data->animations_count) {
            auto& a=data->animations[animation];float end=0;
            for(size_t i=0;i<a.samplers_count;i++){
                auto* input=a.samplers[i].input;float t=0;
                if(input->count)cgltf_accessor_read_float(input,input->count-1,&t,1);end=std::max(end,t);
            }
            float time=std::clamp(fraction,0.0f,1.0f)*end;
            for(size_t i=0;i<a.channels_count;i++) {
                auto& channel=a.channels[i];auto* n=channel.target_node;auto* s=channel.sampler;
                if(!n||!s||!s->input->count)continue;
                size_t left=0,right=s->input->count-1;float lt=0,rt=0;
                // A binary search keeps imported clips with many samples cheap.
                while(left<right){size_t mid=(left+right+1)/2;float t=0;cgltf_accessor_read_float(s->input,mid,&t,1);if(t<=time)left=mid;else right=mid-1;}
                right=std::min(left+1,s->input->count-1);
                cgltf_accessor_read_float(s->input,left,&lt,1);cgltf_accessor_read_float(s->input,right,&rt,1);
                float blend=rt>lt?std::clamp((time-lt)/(rt-lt),0.0f,1.0f):0;
                if(s->interpolation==cgltf_interpolation_type_step)blend=0;
                size_t components=channel.target_path==cgltf_animation_path_type_rotation?4:3;
                float v0[4]{},v1[4]{},v[4]{};
                cgltf_accessor_read_float(s->output,left,v0,components);cgltf_accessor_read_float(s->output,right,v1,components);
                for(size_t j=0;j<components;j++)v[j]=v0[j]+(v1[j]-v0[j])*blend;
                if(channel.target_path==cgltf_animation_path_type_rotation){
                    auto q=QuaternionNormalize(QuaternionSlerp({v0[0],v0[1],v0[2],v0[3]},{v1[0],v1[1],v1[2],v1[3]},blend));
                    n->rotation[0]=q.x;n->rotation[1]=q.y;n->rotation[2]=q.z;n->rotation[3]=q.w;n->has_rotation=1;n->has_matrix=0;
                }else if(channel.target_path==cgltf_animation_path_type_translation){std::copy(v,v+3,n->translation);n->has_translation=1;n->has_matrix=0;}
                else if(channel.target_path==cgltf_animation_path_type_scale){std::copy(v,v+3,n->scale);n->has_scale=1;n->has_matrix=0;}
            }
        }
        int meshIndex=0;
        for(size_t ni=0;ni<data->nodes_count;ni++){
            auto& node=data->nodes[ni];if(!node.mesh)continue;
            float f[16];cgltf_node_transform_world(&node,f);Matrix world=gltfMatrix(f);
            std::vector<Matrix> skin,skinNormal;
            if(node.skin)for(size_t j=0;j<node.skin->joints_count;j++){
                cgltf_node_transform_world(node.skin->joints[j],f);auto joint=gltfMatrix(f);auto inverse=MatrixIdentity();
                if(node.skin->inverse_bind_matrices){cgltf_accessor_read_float(node.skin->inverse_bind_matrices,j,f,16);inverse=gltfMatrix(f);}
                auto matrix=MatrixMultiply(inverse,joint);skin.push_back(matrix);skinNormal.push_back(MatrixTranspose(MatrixInvert(matrix)));
            }
            for(size_t pi=0;pi<node.mesh->primitives_count;pi++){
                auto& prim=node.mesh->primitives[pi];
                if(prim.type!=cgltf_primitive_type_triangles)continue;
                if(meshIndex>=model.meshCount)throw std::runtime_error("Character mesh order mismatch.");
                auto& mesh=model.meshes[meshIndex++];
                auto* positions=cgltf_find_accessor(&prim,cgltf_attribute_type_position,0);
                auto* normals=cgltf_find_accessor(&prim,cgltf_attribute_type_normal,0);
                auto* joints=cgltf_find_accessor(&prim,cgltf_attribute_type_joints,0);
                auto* weights=cgltf_find_accessor(&prim,cgltf_attribute_type_weights,0);
                if(!positions||positions->count!=size_t(mesh.vertexCount))throw std::runtime_error("Character vertex count mismatch.");
                if(!mesh.normals)mesh.normals=static_cast<float*>(MemAlloc(unsigned(mesh.vertexCount*3*sizeof(float))));
                for(int i=0;i<mesh.vertexCount;i++){
                    float p[3]{},n[3]{0,1,0};cgltf_accessor_read_float(positions,i,p,3);if(normals)cgltf_accessor_read_float(normals,i,n,3);
                    Vector3 source{p[0],p[1],p[2]},normal{n[0],n[1],n[2]},position{},direction{};
                    if(joints&&weights&&!skin.empty()){
                        cgltf_uint ids[4]{};float w[4]{};cgltf_accessor_read_uint(joints,i,ids,4);cgltf_accessor_read_float(weights,i,w,4);
                        float sum=0;
                        for(int k=0;k<4;k++)if(w[k]>0&&ids[k]<skin.size()){
                            position=Vector3Add(position,Vector3Scale(Vector3Transform(source,skin[ids[k]]),w[k]));
                            direction=Vector3Add(direction,Vector3Scale(Vector3Transform(normal,skinNormal[ids[k]]),w[k]));sum+=w[k];
                        }
                        if(sum>1e-7f){position=Vector3Scale(position,1/sum);direction=Vector3Normalize(direction);}
                        else{position=Vector3Transform(source,world);direction=normal;}
                    }else{position=Vector3Transform(source,world);direction=Vector3Normalize(Vector3Transform(normal,MatrixTranspose(MatrixInvert(world))));}
                    for(int k=0;k<3;k++){float v=k==0?position.x:k==1?position.y:position.z;
                        float norm=k==0?direction.x:k==1?direction.y:direction.z;
                        mesh.vertices[i*3+k]=v;mesh.normals[i*3+k]=norm;
                        if(mesh.animVertices)mesh.animVertices[i*3+k]=v;if(mesh.animNormals)mesh.animNormals[i*3+k]=norm;}
                }
                UpdateMeshBuffer(mesh,0,mesh.vertices,mesh.vertexCount*3*sizeof(float),0);
                UpdateMeshBuffer(mesh,2,mesh.normals,mesh.vertexCount*3*sizeof(float),0);
            }
        }
    }
};
}
