#pragma once
#include "character.hpp"

// Original view-only geometry. It follows the existing camera/recoil placement,
// never participates in target hit tests and remains absent from clean captures.
// One shared unit mesh is instanced for each part in the existing scene pass.
struct RangeWeapon {
    struct Part { Vector3 center,size; Color color; };
    inline static constexpr std::array<Part,9> parts{{
        {{0,0,.08f},{.07f,.09f,.16f},{33,43,55,255}},
        {{0,.01f,.25f},{.065f,.08f,.20f},{52,65,79,255}},
        {{0,.005f,.42f},{.052f,.065f,.14f},{36,48,62,255}},
        {{0,.015f,.58f},{.018f,.018f,.20f},{66,81,96,255}},
        {{0,.015f,.70f},{.025f,.025f,.04f},{28,39,50,255}},
        {{0,-.065f,.23f},{.038f,.09f,.055f},{27,38,50,255}},
        {{0,-.055f,.33f},{.045f,.08f,.06f},{40,53,68,255}},
        {{0,.065f,.27f},{.018f,.035f,.04f},{77,123,151,255}},
        {{0,.055f,.46f},{.012f,.035f,.02f},{77,123,151,255}},
    }};
    Model model{};
    bool ready=false;
    void load(Shader shader) {
        unload();
        model=LoadModelFromMesh(GenMeshCube(1,1,1));
        ready=IsModelValid(model);
        if(ready)model.materials[0].shader=shader;
    }
    void draw(Vector3 origin,Vector3 right,Vector3 up,Vector3 forward) {
        if(!ready)return;
        Matrix basis={right.x,up.x,forward.x,0,right.y,up.y,forward.y,0,right.z,up.z,forward.z,0,0,0,0,1};
        const auto view=MatrixMultiply(basis,MatrixTranslate(origin.x,origin.y,origin.z));
        for(const auto& part:parts){
            const auto local=MatrixMultiply(MatrixScale(part.size.x,part.size.y,part.size.z),
                MatrixTranslate(part.center.x,part.center.y,part.center.z));
            model.materials[0].maps[MATERIAL_MAP_ALBEDO].color=part.color;
            DrawMesh(model.meshes[0],model.materials[0],MatrixMultiply(local,view));
        }
    }
    void unload(){if(model.meshCount)UnloadModel(model);model={};ready=false;}
};
