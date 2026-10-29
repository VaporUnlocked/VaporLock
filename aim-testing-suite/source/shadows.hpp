#pragma once
#include "raylib.h"
#include "rlgl.h"
namespace suite {
struct RangeShadow {
    RenderTexture2D target{};Shader shader{};Matrix matrix{};bool ready=false;int requested=-1,radius=1;
    void init(){
        const char* vs="#version 330\nin vec3 vertexPosition;uniform mat4 mvp;void main(){gl_Position=mvp*vec4(vertexPosition,1.0);}";
        const char* fs="#version 330\nout vec4 finalColor;void main(){finalColor=vec4(1);}";
        shader=LoadShaderFromMemory(vs,fs);
    }
    void configure(int size,int filterRadius){
        radius=filterRadius;if(requested==size)return;
        if(target.id)rlUnloadFramebuffer(target.id);target={};ready=false;requested=size;
        if(size<=0)return;
        target.id=rlLoadFramebuffer();target.texture.width=target.texture.height=size;
        rlEnableFramebuffer(target.id);
        target.depth={rlLoadTextureDepth(size,size,false),size,size,1,19};
        rlFramebufferAttach(target.id,target.depth.id,RL_ATTACHMENT_DEPTH,RL_ATTACHMENT_TEXTURE2D,0);
        ready=rlFramebufferComplete(target.id)&&shader.id!=rlGetShaderIdDefault();
        rlDisableFramebuffer();if(ready)SetTextureWrap(target.depth,TEXTURE_WRAP_CLAMP);
    }
    void begin(Vector3 center){
        Vector3 direction=Vector3Normalize({-.35f,.9f,-.2f});
        Camera3D light{Vector3Add(center,Vector3Scale(direction,20)),center,{0,1,0},18,CAMERA_ORTHOGRAPHIC};
        BeginTextureMode(target);ClearBackground(WHITE);BeginMode3D(light);
        matrix=MatrixMultiply(rlGetMatrixModelview(),rlGetMatrixProjection());
    }
    void end(){EndMode3D();EndTextureMode();}
    void bind(Shader receiver,bool enabled){
        float use=ready&&enabled?1.0f:0.0f;
        SetShaderValue(receiver,GetShaderLocation(receiver,"shadowEnabled"),&use,SHADER_UNIFORM_FLOAT);
        if(use>0){
            float texel=1.0f/target.depth.width;
            SetShaderValue(receiver,GetShaderLocation(receiver,"shadowTexel"),&texel,SHADER_UNIFORM_FLOAT);
            SetShaderValue(receiver,GetShaderLocation(receiver,"shadowRadius"),&radius,SHADER_UNIFORM_INT);
            SetShaderValueMatrix(receiver,GetShaderLocation(receiver,"shadowMatrix"),matrix);
            int slot=10;rlEnableShader(receiver.id);rlActiveTextureSlot(slot);rlEnableTexture(target.depth.id);
            rlSetUniform(GetShaderLocation(receiver,"shadowTexture"),&slot,SHADER_UNIFORM_INT,1);rlActiveTextureSlot(0);rlDisableShader();
        }
    }
    void unload(){if(target.id)rlUnloadFramebuffer(target.id);if(shader.id)UnloadShader(shader);target={};shader={};ready=false;requested=-1;}
};
}
