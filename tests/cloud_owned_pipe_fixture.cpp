// Reuse the fixed test-only native types and peer protocol fixture.
#define wmain original_cloud_fixture_entry
#include "cloud_pipe_fixture.cpp"
#undef wmain

int wmain(int argc,wchar_t** argv) {
 if(argc!=3)return 3;
 try {
  cloud_native::Detector detector;
  detector.connect(argv[1],static_cast<DWORD>(wcstoul(argv[2],nullptr,10)));
  AppConfig config{};config.maxDetections=1;
  auto result=detector.detect(64,64,std::vector<unsigned char>(64*64*4,50),config,{11,12,13,14});
  if(result.size()!=1)return 4;
  const auto& value=result[0];
  if(value.classId!=0 || value.confidence!=.5 || value.box.x!=0 || value.box.y!=0
      || value.box.w!=32 || value.box.h!=32 || value.segmentationMaskWidth!=32
      || value.segmentationMaskHeight!=32 || value.segmentationPadX!=0 || value.segmentationPadY!=0
      || value.segmentationMask!=std::vector<std::uint8_t>(32*32,1))return 5;
  std::cout<<"{\"matched\":true,\"owned_frame\":true}";return 0;
 }catch(const std::exception& error) {
  std::cout<<"{\"rejected\":true,\"diagnostic\":\""<<error.what()<<"\"}";return 2;
 }
}
