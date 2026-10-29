#pragma once
#include "raylib.h"
#include "rlgl.h"
#include <algorithm>
#include <stdexcept>
namespace suite {
// Multisampling belongs to the offscreen scene FBO, not just the window.
// Resolve once into the existing single-sample texture for presentation/capture.
struct SceneAA {
    using Gen=void(__stdcall*)(int,unsigned*);
    using Bind=void(__stdcall*)(unsigned,unsigned);
    using Storage=void(__stdcall*)(unsigned,int,unsigned,int,int);
    using Attach=void(__stdcall*)(unsigned,unsigned,unsigned,unsigned);
    using Delete=void(__stdcall*)(int,const unsigned*);
    using Integer=void(__stdcall*)(unsigned,int*);
    using Clear=void(__stdcall*)(unsigned);
    Gen gen{};Bind bind{};Storage storage{};Attach attach{};Delete remove{};Integer integer{};Clear clear{};
    unsigned fbo=0,color=0,depth=0;int width=0,height=0,samples=0,requested=0;
    void init(){
        gen=reinterpret_cast<Gen>(rlGetProcAddress("glGenRenderbuffers"));
        bind=reinterpret_cast<Bind>(rlGetProcAddress("glBindRenderbuffer"));
        storage=reinterpret_cast<Storage>(rlGetProcAddress("glRenderbufferStorageMultisample"));
        attach=reinterpret_cast<Attach>(rlGetProcAddress("glFramebufferRenderbuffer"));
        remove=reinterpret_cast<Delete>(rlGetProcAddress("glDeleteRenderbuffers"));
        integer=reinterpret_cast<Integer>(rlGetProcAddress("glGetIntegerv"));
        clear=reinterpret_cast<Clear>(rlGetProcAddress("glClear"));
    }
    void unload(){
        if(fbo){rlUnloadFramebuffer(fbo);fbo=0;depth=0;}
        if(color&&remove)remove(1,&color);if(depth&&remove)remove(1,&depth);
        color=depth=0;width=height=samples=0;
    }
    void resize(int w,int h,int requestedSamples){
        if(width==w&&height==h&&requested==requestedSamples)return;
        unload();width=w;height=h;requested=requestedSamples;
        if(requestedSamples<2||!gen||!bind||!storage||!attach||!remove||!integer)return;
        int maximum=0;integer(0x8D57,&maximum);samples=std::min(requestedSamples,maximum);if(samples<2){samples=0;return;}
        fbo=rlLoadFramebuffer();rlEnableFramebuffer(fbo);
        gen(1,&color);bind(0x8D41,color);storage(0x8D41,samples,0x8058,w,h);attach(0x8D40,0x8CE0,0x8D41,color);
        gen(1,&depth);bind(0x8D41,depth);storage(0x8D41,samples,0x81A6,w,h);attach(0x8D40,0x8D00,0x8D41,depth);
        bind(0x8D41,0);bool valid=rlFramebufferComplete(fbo);rlDisableFramebuffer();
        if(!valid){unload();width=w;height=h;}
    }
    void begin(){if(fbo){rlDrawRenderBatchActive();rlEnableFramebuffer(fbo);}}
    void copyColor(unsigned source,unsigned destination){
        rlDrawRenderBatchActive();rlBindFramebuffer(0x8CA8,source);rlBindFramebuffer(0x8CA9,destination);
        rlBlitFramebuffer(0,0,width,height,0,0,width,height,0x00004000);rlEnableFramebuffer(source);
    }
    void resolve(unsigned destination){
        if(!fbo)return;rlDrawRenderBatchActive();
        rlBindFramebuffer(0x8CA8,fbo);rlBindFramebuffer(0x8CA9,destination);
        rlBlitFramebuffer(0,0,width,height,0,0,width,height,0x00004000);
        rlEnableFramebuffer(destination);
    }
    void clearDepth(){rlDrawRenderBatchActive();rlEnableDepthMask();if(clear)clear(0x00000100);}
};
}
