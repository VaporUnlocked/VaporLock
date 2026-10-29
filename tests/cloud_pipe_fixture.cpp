#define NOMINMAX
#include <windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "app/BoundedWindowsWrite.h"
#include "app/CaptureQualification.h"
struct RectF {double x,y,w,h;};
struct Detection {
 RectF box{};double confidence{};int classId{};std::wstring label;
 std::vector<std::uint8_t> segmentationMask;
 int segmentationMaskWidth{},segmentationMaskHeight{},segmentationInputWidth{},segmentationInputHeight{};
 float segmentationScaleX{},segmentationScaleY{},segmentationPadX{},segmentationPadY{};
};
struct AppConfig {
 double confidence=.3,iou=.45;bool nmsEnabled=true;int maxDetections=4;
 int outputMaxObservationAgeMs=120;bool overlaySegmentationMasks=true;int segmentationMaskLimit=4;
 std::wstring classFilter;
};
bool classAllowed(const std::wstring&,int){return true;}
#include "app/CloudPipeDetector.h"
int wmain(int argc,wchar_t** argv){
 if(argc==2 && std::wstring(argv[1])==L"--policy-only") {
  using namespace cloud_native;
  if(validOperation(static_cast<Operation>(123)))return 8;
  if(!validOperation(Operation::Inspect)||!validOperation(Operation::Infer))return 9;
  std::cout<<"{\"policy_matched\":true}";return 0;
 }
 if(argc==2 && std::wstring(argv[1])==L"--early-exit")return 0;
 if(argc<3 || argc>4)return 3;
 try {
  cloud_native::Detector detector;detector.connect(argv[1],static_cast<DWORD>(wcstoul(argv[2],nullptr,10)));
  auto result=detector.detect(64,64,std::vector<unsigned char>(64*64*4,50),AppConfig{}, {11,12,13,14});
  if(result.size()!=1 || result[0].classId!=0 || result[0].box.x!=8 || result[0].box.y!=10
     || result[0].segmentationPadX!=-8 || result[0].segmentationPadY!=-10) return 4;
  const bool polygon=argc==4;
  if(polygon){if(result[0].segmentationMask!=std::vector<std::uint8_t>(9,1))return 5;}
  else if(result[0].segmentationMask!=std::vector<std::uint8_t>{1,0,0,1})return 5;
  std::cout<<"{\"matched\":true}";return 0;
 }catch(const std::exception& error){std::cout<<"{\"rejected\":true,\"diagnostic\":\""<<error.what()<<"\"}";return 2;}
}
