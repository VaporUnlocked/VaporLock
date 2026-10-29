#pragma once
#include "simulation.hpp"
#include "raymath.h"
#include <numeric>
#include <limits>
namespace suite {
// One immutable acceleration structure shared by every instance of the posed mesh.
struct MeshQuery {
    struct Triangle {Vector3 a,b,c,center;BoundingBox box;};
    struct Node {BoundingBox box;int first=0,count=0,left=-1,right=-1;};
    std::vector<Triangle> triangles;std::vector<int> order;std::vector<Node> nodes;
    void clear(){triangles.clear();order.clear();nodes.clear();}
    void add(Vector3 a,Vector3 b,Vector3 c) {
        triangles.push_back({a,b,c,Vector3Scale(Vector3Add(Vector3Add(a,b),c),1.0f/3),
            {Vector3Min(a,Vector3Min(b,c)),Vector3Max(a,Vector3Max(b,c))}});
    }
    int split(int first,int count) {
        BoundingBox box{{1e30f,1e30f,1e30f},{-1e30f,-1e30f,-1e30f}};
        for(int i=first;i<first+count;i++){auto& b=triangles[order[i]].box;box.min=Vector3Min(box.min,b.min);box.max=Vector3Max(box.max,b.max);}
        int index=int(nodes.size());nodes.push_back({box,first,count});
        if(count<=8)return index;
        Vector3 size=Vector3Subtract(box.max,box.min);int axis=size.y>size.x?1:0;if(size.z>(axis==0?size.x:size.y))axis=2;
        int mid=first+count/2;
        auto component=[axis](Vector3 v){return axis==0?v.x:axis==1?v.y:v.z;};
        std::nth_element(order.begin()+first,order.begin()+mid,order.begin()+first+count,
            [&](int a,int b){return component(triangles[a].center)<component(triangles[b].center);});
        int left=split(first,mid-first),right=split(mid,first+count-mid);
        nodes[index].left=left;nodes[index].right=right;nodes[index].count=0;return index;
    }
    void build(){nodes.clear();order.resize(triangles.size());std::iota(order.begin(),order.end(),0);if(!order.empty())split(0,int(order.size()));}
    void rebuild(const Model& model) {
        clear();
        for(int i=0;i<model.meshCount;i++) {
            auto& mesh=model.meshes[i];const float* data=mesh.animVertices?mesh.animVertices:mesh.vertices;
            for(int j=0;j<mesh.triangleCount;j++) {
                Vector3 v[3];
                for(int k=0;k<3;k++){int n=mesh.indices?mesh.indices[j*3+k]:j*3+k;v[k]={data[n*3],data[n*3+1],data[n*3+2]};}
                add(v[0],v[1],v[2]);
            }
        }
        build();
    }
    // Direction need not be normalized: inverse transforms preserve the original world-ray parameter.
    float hit(Ray ray,float maximum=1e30f)const {
        if(nodes.empty())return maximum;
        int stack[128],top=0;stack[top++]=0;float nearest=maximum;
        while(top) {
            auto& node=nodes[stack[--top]];float near=0;
            if(!rayBox(ray.position,ray.direction,node.box,near)||near>nearest)continue;
            if(node.count==0){stack[top++]=node.left;stack[top++]=node.right;continue;}
            for(int j=node.first;j<node.first+node.count;j++) {
                const auto& t=triangles[order[j]];
                auto e1=Vector3Subtract(t.b,t.a),e2=Vector3Subtract(t.c,t.a);
                auto p=Vector3CrossProduct(ray.direction,e2);float det=Vector3DotProduct(e1,p);
                if(std::abs(det)<1e-10f)continue;
                float inv=1/det;auto s=Vector3Subtract(ray.position,t.a);
                float u=Vector3DotProduct(s,p)*inv;if(u<0||u>1)continue;
                auto q=Vector3CrossProduct(s,e1);float v=Vector3DotProduct(ray.direction,q)*inv;if(v<0||u+v>1)continue;
                float d=Vector3DotProduct(e2,q)*inv;if(d>=0&&d<nearest)nearest=d;
            }
        }
        return nearest;
    }
    float worldHit(Ray ray,Matrix transform,float maximum=1e30f)const {
        Matrix inverse=MatrixInvert(transform);
        auto point=Vector3Transform(ray.position,inverse);
        auto next=Vector3Transform(Vector3Add(ray.position,ray.direction),inverse);
        return hit({point,Vector3Subtract(next,point)},maximum);
    }
};
}

