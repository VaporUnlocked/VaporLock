#pragma once
#include "CloudAccess.h"
// Included after the engine's Detection/AppConfig and UTF conversion helpers.
// Only inference crosses this private pipe. Native capture/identity/tracking,
// activation, output authorization, overlay and Stop retain their normal owners.
namespace cloud_native {
#pragma pack(push,1)
struct Frame {
    char magic[8]; std::uint64_t sequence, captured;
    std::uint32_t width,height,stride,bytes; float confidence,iou; std::uint32_t maximum;
};
struct Result {
    char magic[8]; std::uint64_t sequence,captured;
    std::uint32_t width,height,count,bytes; float preprocess,model,postprocess,total,maximum;
    CaptureFrameIdentity identity;
};
struct Item {
    float x1,y1,x2,y2,score;
    std::uint32_t ox,oy,width,height,bytes,vertices;
};
#pragma pack(pop)
static_assert(sizeof(Frame)==52 && sizeof(Result)==92 && sizeof(Item)==44);

class Detector {
public:
    ~Detector(){close();}
    Detector()=default; Detector(const Detector&)=delete; Detector& operator=(const Detector&)=delete;
    void close(){if(pipe!=INVALID_HANDLE_VALUE)CloseHandle(pipe);pipe=INVALID_HANDLE_VALUE;if(peer)CloseHandle(peer);peer=nullptr;}
    void connect(const std::wstring& name, DWORD pid) {
        close();
        if(!validOperation(Operation::Inspect))throw std::runtime_error("Invalid Cloud model operation.");
        const std::wstring prefix=pipePrefix();
        if(name.size()!=prefix.size()+32 || name.substr(0,prefix.size())!=prefix || !pid
           || !std::all_of(name.begin()+prefix.size(),name.end(),[](wchar_t c){return (c>=L'0'&&c<=L'9')||(c>=L'a'&&c<=L'f');}))
            throw std::runtime_error("Invalid private .cloud endpoint.");
        peer=OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);
        if(!peer || WaitForSingleObject(peer,0)!=WAIT_TIMEOUT)throw std::runtime_error("The .cloud runtime is unavailable.");
        const auto path=L"\\\\.\\pipe\\"+name;
        pipe=CreateFileW(path.c_str(),0x0012019b,0,nullptr,OPEN_EXISTING,
                         FILE_FLAG_OVERLAPPED|SECURITY_SQOS_PRESENT|SECURITY_IDENTIFICATION,nullptr);
        if(pipe==INVALID_HANDLE_VALUE)throw std::runtime_error("Could not connect to the private .cloud runtime.");
        ULONG server=0;
        if(!GetNamedPipeServerProcessId(pipe,&server)||server!=pid||WaitForSingleObject(peer,0)!=WAIT_TIMEOUT) {
            close(); throw std::runtime_error("The .cloud endpoint does not belong to its admitted runtime.");
        }
    }
    std::vector<Detection> detect(int width,int height,const std::vector<unsigned char>& bgra,const AppConfig& config,
                                  CaptureFrameIdentity identity) {
        if(!validOperation(Operation::Infer))throw std::runtime_error("Invalid Cloud model operation.");
        if(pipe==INVALID_HANDLE_VALUE||WaitForSingleObject(peer,0)!=WAIT_TIMEOUT)
            throw std::runtime_error("The .cloud runtime stopped. Start again to reload the model.");
        constexpr std::size_t maxPixels=4096U*2160U,maxBody=8U*1024U*1024U;
        if(width<=0||height<=0||width>4096||height>4096||static_cast<std::size_t>(width)*height>maxPixels
           ||bgra.size()!=static_cast<std::size_t>(width)*height*4)throw std::runtime_error("Unsupported .cloud capture dimensions.");
        Frame frame{};std::memcpy(frame.magic,"VLCFRM01",8);frame.sequence=++sequence;
        frame.captured=static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
        frame.width=width;frame.height=height;frame.stride=width*4;frame.bytes=static_cast<std::uint32_t>(bgra.size());
        frame.confidence=static_cast<float>(config.confidence);frame.iou=config.nmsEnabled?static_cast<float>(config.iou):1.0F;
        frame.maximum=std::clamp(config.maxDetections,1,256);
        const ULONGLONG deadline=GetTickCount64()+std::clamp(config.outputMaxObservationAgeMs,1,2000);
        write(&frame,sizeof(frame),deadline);write(&identity,sizeof(identity),deadline);write(bgra.data(),bgra.size(),deadline);
        Result result{};read(&result,sizeof(result),deadline);
        if(std::memcmp(result.magic,"VLCRES01",8)||result.sequence!=frame.sequence||result.captured!=frame.captured
           ||result.width!=frame.width||result.height!=frame.height||!(result.identity==identity)||result.count>frame.maximum
           ||result.bytes>maxBody||result.bytes<result.count*sizeof(Item))throw std::runtime_error("Invalid .cloud observation identity or result size.");
        for(float value:{result.preprocess,result.model,result.postprocess,result.total})
            if(!std::isfinite(value)||value<0||value>60000)throw std::runtime_error("Invalid .cloud stage timing.");
        if(!std::isfinite(result.maximum)||result.maximum < -1||result.maximum>1)throw std::runtime_error("Invalid .cloud source confidence.");
        std::vector<unsigned char> body(result.bytes);if(!body.empty())read(body.data(),body.size(),deadline);
        std::size_t offset=0,maskPixels=0;std::vector<Detection> detections;detections.reserve(result.count);
        for(std::uint32_t index=0;index<result.count;++index) {
            if(offset+sizeof(Item)>body.size())throw std::runtime_error("Truncated .cloud detection.");
            Item item{};std::memcpy(&item,body.data()+offset,sizeof(item));offset+=sizeof(item);
            if(!std::isfinite(item.x1)||!std::isfinite(item.y1)||!std::isfinite(item.x2)||!std::isfinite(item.y2)
               ||!std::isfinite(item.score)||item.x1<0||item.y1<0||item.x2>width||item.y2>height
               ||item.x2<=item.x1||item.y2<=item.y1||item.score<0||item.score>1
               ||item.ox>frame.width||item.oy>frame.height||item.width>frame.width-item.ox||item.height>frame.height-item.oy
               ||item.vertices>2048)throw std::runtime_error("Invalid .cloud detection geometry.");
            const auto pixels=static_cast<std::size_t>(item.width)*item.height;
            if((item.width==0)!=(item.height==0)||item.bytes!=(pixels+7)/8||maskPixels+pixels>maxPixels*4
               ||offset+item.bytes+static_cast<std::size_t>(item.vertices)*8>body.size())throw std::runtime_error("Invalid .cloud mask budget.");
            maskPixels+=pixels;
            Detection detection;detection.box={item.x1,item.y1,item.x2-item.x1,item.y2-item.y1};
            detection.confidence=item.score;detection.classId=0;detection.label=L"player";
            if(pixels && config.overlaySegmentationMasks && index<static_cast<std::uint32_t>(config.segmentationMaskLimit)) {
                detection.segmentationMask.resize(pixels);
                for(std::size_t p=0;p<pixels;++p)detection.segmentationMask[p]=(body[offset+p/8]>>(p%8))&1;
                detection.segmentationMaskWidth=item.width;detection.segmentationMaskHeight=item.height;
                detection.segmentationInputWidth=item.width;detection.segmentationInputHeight=item.height;
                detection.segmentationScaleX=1;detection.segmentationScaleY=1;
                detection.segmentationPadX=-static_cast<float>(item.ox);detection.segmentationPadY=-static_cast<float>(item.oy);
            }
            offset+=item.bytes;
            for(std::uint32_t p=0;p<item.vertices;++p) {
                float xy[2];std::memcpy(xy,body.data()+offset,8);offset+=8;
                if(!std::isfinite(xy[0])||!std::isfinite(xy[1])||xy[0]<0||xy[0]>width||xy[1]<0||xy[1]>height)
                    throw std::runtime_error("Invalid .cloud contour coordinate.");
            }
            if(classAllowed(config.classFilter,0))detections.push_back(std::move(detection));
        }
        if(offset!=body.size())throw std::runtime_error("Trailing .cloud result data.");
        if(GetTickCount64()>=deadline)throw std::runtime_error("The .cloud observation expired; output was stopped.");
        if(!validOperation(Operation::Infer))throw std::runtime_error("Invalid Cloud model operation.");
        last=result;return detections;
    }
    Result last{};
private:
    [[noreturn]] void transportFailure(const char* phase,DWORD error,bool expired=false,bool eof=false) const {
        // error is captured at the failing operation, before peer queries or
        // handle cleanup. A timeout alone is not evidence of a worker crash.
        std::string reason;
        if(expired || error==ERROR_TIMEOUT)reason="The .cloud observation deadline expired during ";
        else if(eof)reason="The .cloud result stream ended before a complete observation during ";
        else if(error==ERROR_BROKEN_PIPE || error==ERROR_PIPE_NOT_CONNECTED || error==ERROR_NO_DATA)
            reason="The .cloud transport disconnected during ";
        else reason="The .cloud transport failed during ";
        reason+=phase;reason+=" (Windows error "+std::to_string(error)+"); output was stopped.";
        if(peer && WaitForSingleObject(peer,0)==WAIT_OBJECT_0)reason+=" Worker exit was also observed.";
        throw std::runtime_error(reason);
    }
    void read(void* destination,std::size_t size,ULONGLONG deadline) {
        auto* cursor=static_cast<unsigned char*>(destination);
        while(size) {
            if(GetTickCount64()>=deadline)transportFailure("result read",ERROR_TIMEOUT,true);
            OVERLAPPED operation{};operation.hEvent=CreateEventW(nullptr,TRUE,FALSE,nullptr);
            if(!operation.hEvent)throw std::runtime_error("Could not prepare .cloud result read.");
            DWORD count=0;BOOL ok=ReadFile(pipe,cursor,static_cast<DWORD>(std::min<std::size_t>(size,65536)),&count,&operation);
            DWORD error=ok?ERROR_SUCCESS:GetLastError();bool expired=false;
            if(!ok && error==ERROR_IO_PENDING) {
                auto now=GetTickCount64();auto wait=now<deadline?static_cast<DWORD>(deadline-now):0;
                const auto waited=WaitForSingleObject(operation.hEvent,wait);
                if(waited==WAIT_OBJECT_0) {
                    ok=GetOverlappedResult(pipe,&operation,&count,FALSE);error=ok?ERROR_SUCCESS:GetLastError();
                    if(!ok && error==ERROR_IO_INCOMPLETE) {
                        CancelIoEx(pipe,&operation);DWORD discarded=0;GetOverlappedResult(pipe,&operation,&discarded,TRUE);
                    }
                } else {
                    error=waited==WAIT_TIMEOUT?ERROR_TIMEOUT:waited==WAIT_FAILED?GetLastError():ERROR_GEN_FAILURE;
                    expired=waited==WAIT_TIMEOUT;
                    CancelIoEx(pipe,&operation);DWORD discarded=0;GetOverlappedResult(pipe,&operation,&discarded,TRUE);ok=FALSE;
                }
            }
            CloseHandle(operation.hEvent);
            if(!ok||!count)transportFailure("result read",error,expired,ok&&!count);
            cursor+=count;size-=count;
        }
    }
    void write(const void* data,std::size_t bytes,ULONGLONG deadline) {
        const auto now=GetTickCount64();
        if(now>=deadline)transportFailure("frame write",ERROR_TIMEOUT,true);
        const DWORD error=writer.write(pipe,data,static_cast<DWORD>(bytes),static_cast<DWORD>(deadline-now));
        if(error!=ERROR_SUCCESS)transportFailure("frame write",error,error==ERROR_TIMEOUT);
    }
    HANDLE pipe=INVALID_HANDLE_VALUE,peer=nullptr;std::uint64_t sequence=0;
    vaporlock::io::BoundedWindowsWrite writer;
};
}
