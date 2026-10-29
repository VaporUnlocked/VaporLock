#include "app/InferenceRuntime.h"
#include <filesystem>
#include <iostream>

void require(bool ok,const char* message) { if(!ok) throw std::runtime_error(message); }
int wmain(int argc,wchar_t** argv) {
    try {
        require(argc==2,"Fixture path missing");
        auto env=std::make_unique<Ort::Env>(ORT_LOGGING_LEVEL_ERROR,"metadata-test");
        Ort::SessionOptions options;
        options.SetIntraOpNumThreads(1); options.SetInterOpNumThreads(1);
        auto session=std::make_unique<Ort::Session>(*env,argv[1],options);
        vaporlock::inference::Runtime runtime(std::move(env),std::move(session),"image",{"output"},
            std::array<std::int64_t,4>{1,3,2,2},ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT);
        for(std::size_t i=0;i<runtime.floats().size();++i) runtime.floats()[i]=static_cast<float>(i);
        const auto& outputs=runtime.run();
        require(outputs.size()==1 && outputs[0].GetTensorData<float>()[11]==11,"CPU fixture did not run correctly");
        bool foreignRejected=false;
        std::thread foreign([&]{try{runtime.retireMetadataSession();}catch(const std::runtime_error&){foreignRejected=true;}});
        foreign.join();require(foreignRejected,"Foreign owner retired session");
        require(runtime.run()[0].GetTensorData<float>()[5]==5,"Rejected retirement damaged CPU session");
        auto* storage=runtime.floats().data();
        runtime.retireMetadataSession();
        require(runtime.floats().data()==storage && runtime.floats()[11]==11,"Retirement invalidated input storage");
        bool fallbackRejected=false;
        try{runtime.run();}catch(const std::runtime_error& e){fallbackRejected=std::string(e.what()).find("cannot execute CPU inference")!=std::string::npos;}
        require(fallbackRejected,"Retired metadata session allowed CPU fallback");
        runtime.cancel();
        std::cout << "Actual ORT CPU inference, ownership, metadata retirement and fallback rejection passed.\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
