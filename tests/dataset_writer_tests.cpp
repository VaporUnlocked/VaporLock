// SOURCE ONLY: synthetic clean frames, recording input, exclusive temporary roots.
// No compiler/test execution authorized yet. Native candidate-flow tests are
// separate: these writer tests do not stand in for the 15 actual C014 cases.
#include "app/Win32VaporLock.cpp"
using Microsoft::WRL::ComPtr;

static void require(bool value,const char* reason) { if (!value) throw std::runtime_error(reason); }
using Clock = DatasetWriter::Clock;
using Admission = DatasetWriter::Admission;

struct TemporaryDataset {
    std::filesystem::path parent = std::filesystem::canonical(std::filesystem::temp_directory_path()), root;
    TemporaryDataset() {
        for (int attempt=0;attempt<100;++attempt) {
            const auto path=parent/(L"vaporlock-c014-writer-"+std::to_wstring(GetCurrentProcessId())+L"-"
                +std::to_wstring(GetTickCount64())+L"-"+std::to_wstring(attempt));
            if (std::filesystem::create_directory(path)) { root=path; return; }
        }
        throw std::runtime_error("exclusive temporary dataset unavailable");
    }
    ~TemporaryDataset() {
        std::error_code error;
        const auto resolved=std::filesystem::weakly_canonical(root,error);
        if (error || resolved.parent_path()!=parent || !resolved.filename().wstring().starts_with(L"vaporlock-c014-writer-")) return;
        // Refuse recursive cleanup if an unexpected reparse entry appears.
        for (const auto& item:std::filesystem::recursive_directory_iterator(resolved,error)) {
            const auto attributes=GetFileAttributesW(item.path().c_str());
            if (attributes==INVALID_FILE_ATTRIBUTES || (attributes&FILE_ATTRIBUTE_REPARSE_POINT)) return;
        }
        if (!error) std::filesystem::remove_all(resolved,error);
    }
};

struct Gate {
    std::promise<void> entered,release;
    std::future<void> beginning=entered.get_future(),released=release.get_future();
    std::atomic<unsigned> calls{0};
    bool opened=false;
    void blockFirst() { if (calls++==0) { entered.set_value(); released.wait(); } }
    void open() { if (!opened) { opened=true; release.set_value(); } }
    ~Gate() { open(); }
};
struct OpenGateOnReturn { Gate& gate; ~OpenGateOnReturn() { gate.open(); } };

template<class Predicate> static void until(Predicate predicate,const char* reason) {
    const auto limit=Clock::now()+std::chrono::seconds(5);
    while (!predicate()) { require(Clock::now()<limit,reason); std::this_thread::sleep_for(std::chrono::milliseconds(2)); }
}
static AppConfig configuration(const TemporaryDataset& folder) {
    AppConfig config;config.datasetEnabled=true;config.datasetCaptureRequiresActivation=false;
    config.datasetIntervalMs=100;config.datasetJpegQuality=83;config.datasetDirectory=folder.root.wstring();
    return config;
}
static void ready(DatasetWriter& writer,const AppConfig& config,std::uint64_t revision=1) {
    until([&] { return writer.observeOutput(config,revision,true,true); },"native policy not observed");
    until([&] { const auto s=writer.status();return s && s->rootReady && !s->cleanupPending; },"held fixture root not ready");
}
static std::vector<unsigned char> cleanFrame() {
    std::vector<unsigned char> bytes(32*16*4);
    for (int y=0;y<16;++y) for(int x=0;x<32;++x) {
        const auto at=static_cast<std::size_t>(y*32+x)*4;
        bytes[at]=static_cast<unsigned char>(x*8);bytes[at+1]=static_cast<unsigned char>(y*16);bytes[at+2]=64;bytes[at+3]=255;
    }
    return bytes;
}
static Admission submit(DatasetWriter& writer,const std::vector<unsigned char>& bytes,std::uint64_t frameId,
                        Clock::time_point observed,std::uint64_t source=1,std::uint64_t revision=1,
                        bool qualified=true,bool fresh=true) {
    CaptureFrameIdentity identity{1,source,frameId,revision};
    if (!writer.observeSource(identity)) return Admission::Stale;
    CaptureQualification qualification{identity,true,qualified};
    return writer.admit(32,16,bytes,identity,qualification,fresh,observed,1000+frameId);
}
static std::vector<std::filesystem::path> images(const TemporaryDataset& folder) {
    std::vector<std::filesystem::path> values;
    const auto directory=folder.root/L"images";
    if (!std::filesystem::is_directory(directory)) return values;
    for (const auto& item:std::filesystem::directory_iterator(directory))
        if (item.path().extension()==L".jpg") values.push_back(item.path());
    return values;
}
static void checkCleanDimensions(const std::filesystem::path& path) {
    ComPtr<IWICImagingFactory> factory;ComPtr<IWICBitmapDecoder> decoder;ComPtr<IWICBitmapFrameDecode> frame;
    require(SUCCEEDED(CoCreateInstance(CLSID_WICImagingFactory,nullptr,CLSCTX_INPROC_SERVER,IID_PPV_ARGS(factory.GetAddressOf()))),"fixture WIC factory");
    require(SUCCEEDED(factory->CreateDecoderFromFilename(path.c_str(),nullptr,GENERIC_READ,WICDecodeMetadataCacheOnLoad,decoder.GetAddressOf())),"committed JPEG decode");
    require(SUCCEEDED(decoder->GetFrame(0,frame.GetAddressOf())),"committed JPEG frame");
    UINT width=0,height=0;require(SUCCEEDED(frame->GetSize(&width,&height)) && width==32 && height==16,"whole source dimensions changed/cropped");
}

static void intervalAndLatestQueue() {
    TemporaryDataset folder;auto config=configuration(folder);Gate gate;
    std::mutex mutex;std::vector<Clock::time_point> commits;std::vector<std::wstring> receipts;
    DatasetWriter* owner=nullptr;
    DatasetWriter writer([&](std::uint64_t) {
        const auto s=owner->status();require(s.has_value(),"commit receipt unavailable");
        std::lock_guard lock(mutex);commits.push_back(s->lastCommit);
    },[](std::string){},[&]{gate.blockFirst();},{},[&](std::uint64_t,const std::wstring& path) {
        std::lock_guard lock(mutex);receipts.push_back(path);
    });owner=&writer;
    OpenGateOnReturn unblock{gate};
    ready(writer,config);const auto observed=Clock::now()-std::chrono::seconds(10);auto bytes=cleanFrame();
    require(submit(writer,bytes,1,observed)==Admission::Accepted,"interval-only first frame admission");
    require(gate.beginning.wait_for(std::chrono::seconds(5))==std::future_status::ready,"worker never encoded");
    require(submit(writer,bytes,2,observed+std::chrono::milliseconds(99))==Admission::Cooldown,"admission interval bypass");
    require(submit(writer,bytes,3,observed+std::chrono::milliseconds(100))==Admission::Accepted,"one pending frame admission");
    require(submit(writer,bytes,4,observed+std::chrono::milliseconds(200))==Admission::Replaced,"latest pending replacement");
    const auto status=writer.status();require(status && status->pending && status->encoding,"queue ownership not observed");
    require(status->ownedBgraBytes==2*bytes.size() && status->highWaterBgraBytes<=128U*1024U*1024U,"owned BGRA exceeds one pending + one encoding");
    gate.open();until([&] {const auto s=writer.status();return s && s->saved==2;},"two actual commits missing");
    require(writer.stop(),"writer close ack failed");
    require(receipts.size()==2,"actual committed destination receipts missing");
    for(const auto& receipt:receipts) {
        const std::filesystem::path path(receipt);
        require(path.parent_path()==folder.root/L"images" && std::filesystem::is_regular_file(path),"receipt does not identify committed image");
    }
    { std::lock_guard lock(mutex);require(commits.size()==2 && commits[1]-commits[0]>=std::chrono::milliseconds(100),"actual commit interval bypass"); }
    const auto paths=images(folder);require(paths.size()==2,"unexpected number of committed JPEGs");
    bool first=false,last=false;
    for(const auto& path:paths) {const auto name=path.filename().wstring();first|=name.find(L"vaporlock_1001_")==0;last|=name.find(L"vaporlock_1004_")==0;checkCleanDimensions(path);}
    require(first && last,"writer substituted another frame or retained superseded pending frame");
    require(!std::filesystem::exists(folder.root/L"labels"),"capture created annotation labels");
}

static void retireBlockedFrame(int kind) {
    TemporaryDataset folder;auto config=configuration(folder);config.datasetCaptureRequiresActivation=true;
    Gate gate;DatasetWriter writer([](std::uint64_t){},[](std::string){},[&]{gate.blockFirst();});
    OpenGateOnReturn unblock{gate};
    ready(writer,config);const auto observed=Clock::now()-std::chrono::seconds(10);auto bytes=cleanFrame();
    require(submit(writer,bytes,1,observed)==Admission::Accepted,"retirement fixture admission");
    require(gate.beginning.wait_for(std::chrono::seconds(5))==std::future_status::ready,"retirement worker entry missing");
    if (kind==0) {config.datasetEnabled=false;require(writer.observeOutput(config,2,true,true),"disable retirement request");}
    else if (kind==1) require(writer.observeOutput(config,1,false,true),"activation release retirement request");
    else if (kind==2) require(writer.observeSource({1,2,2,1}),"source change retirement request");
    else if (kind==3) {config.datasetJpegQuality=80;require(writer.observeOutput(config,2,true,true),"config revision retirement request");}
    else {config.datasetMaxImages=1;require(writer.observeOutput(config,1,true,true),"limit change retirement request");}
    gate.open();until([&] {const auto s=writer.status();return s && !s->encoding;},"retired encoding still owned");
    const auto s=writer.status();require(s && s->saved==0 && s->lastAdmission==observed,"retired sample committed/reset admission clock");
    require(writer.stop(),"retired fixture close ack");
    require(images(folder).empty(),"retired sample published JPEG");
}

static void qualifyAndStopBudget() {
    TemporaryDataset folder;auto config=configuration(folder);config.datasetCaptureConfidenceEnabled=true;
    Gate gate;DatasetWriter writer([](std::uint64_t){},[](std::string){},[&]{gate.blockFirst();});
    OpenGateOnReturn unblock{gate};
    ready(writer,config);const auto observed=Clock::now()-std::chrono::seconds(10);auto bytes=cleanFrame();
    require(submit(writer,bytes,1,observed,1,1,false)==Admission::Unqualified,"below-cutoff frame queued");
    require(submit(writer,bytes,2,observed,1,1,true,false)==Admission::Stale,"stale frame queued");
    require(submit(writer,bytes,3,observed)==Admission::Accepted,"current qualification rejected");
    require(gate.beginning.wait_for(std::chrono::seconds(5))==std::future_status::ready,"blocked owner entry");
    const auto before=Clock::now();const bool complete=writer.stop();const auto elapsed=Clock::now()-before;
    require(!complete && elapsed>=std::chrono::milliseconds(1700) && elapsed<std::chrono::seconds(4),"2s Stop ack budget falsely completed or unbounded");
    const auto status=writer.status();require(status && status->closing && !status->complete && status->encoding,"blocked owner graph falsely released");
    gate.open();require(writer.stop(),"released closing worker did not acknowledge real exit/join");
    require(images(folder).empty(),"Stop-retired sample committed");
}

static void ownedFileCollision() {
    TemporaryDataset folder;const auto root=CaptureRoot::prepare(folder.root.wstring());
    require(root->ready(),"collision fixture held root");
    const auto final=root->images()+L"\\existing.jpg";const auto staging=final+L".pending";
    HANDLE existing=CreateFileW(final.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
    require(existing!=INVALID_HANDLE_VALUE && CloseHandle(existing),"exclusive collision setup");
    CaptureFile owner;require(owner.create(staging),"exclusive owned pending create");
    const unsigned char data[3]{1,2,3};DWORD written=0;
    require(WriteFile(owner.writer,data,3,&written,nullptr) && written==3 && owner.closeWriter(),"owned write close");
    require(!owner.rename(final) && !owner.committed,"final collision overwritten");
    require(owner.cleanup() && owner.released(),"owned collision cleanup did not acknowledge");
    require(std::filesystem::file_size(final)==0 && !std::filesystem::exists(staging),"collision changed unowned final or kept pending");
    require(root->close(),"held collision root close");
}

static void outputPollContinuesDuringBlockedWriter() {
    TemporaryDataset folder;auto config=configuration(folder);Gate gate;std::atomic<int> reports{0};
    DatasetWriter writer([](std::uint64_t){},[](std::string){},[&]{gate.blockFirst();});
    AimOutputScheduler output([&](const AimReport& report,const AimInputs&,const AppConfig&,AimOutputStatus&) {
        if(report.dx || report.dy)++reports;return true; // recording only
    },[](const AppConfig&) {AimInputs input;input.down=true;return input;},{},
      [&](const AppConfig& current,std::uint64_t revision,bool authorized,bool running) {writer.observeOutput(current,revision,authorized,running);});
    OpenGateOnReturn unblock{gate};
    config.movementEnabled=true;const auto generation=output.configure(config);output.start();
    ready(writer,config,generation);
    const auto observed=Clock::now()-std::chrono::seconds(10);auto bytes=cleanFrame();
    require(submit(writer,bytes,1,observed,1,generation)==Admission::Accepted,"blocked output fixture admission");
    require(gate.beginning.wait_for(std::chrono::seconds(5))==std::future_status::ready,"blocked output fixture worker entry");
    for(int i=0;i<20;++i) {
        Track target;target.id=1;target.age=5;target.hasObservation=true;target.detection.confidence=.95;
        target.detection.box={340,300,40,40};target.observedBox=target.detection.box;target.lastUpdate=AimClock::now();
        output.publish(target,640,640,target.lastUpdate,generation);std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    output.stop();gate.open();require(writer.stop(),"blocked output writer close");
    require(reports>0 && images(folder).empty(),"collection blocked recording output or committed after Stop");
}
static void retireBlockedRootPreparation(bool stop) {
    TemporaryDataset folder;auto config=configuration(folder);Gate gate;
    DatasetWriter writer([](std::uint64_t){},[](std::string){},{},[&]{gate.blockFirst();});
    OpenGateOnReturn unblock{gate};
    require(writer.observeOutput(config,1,true,true),"root preparation policy request");
    require(gate.beginning.wait_for(std::chrono::seconds(5))==std::future_status::ready,"root preparation block missing");
    if (stop) require(!writer.stop(),"blocked root preparation falsely acknowledged Stop");
    else {config.datasetEnabled=false;require(writer.observeOutput(config,2,true,true),"disable during root preparation");}
    gate.open();
    // Real worker must release any root acquired by the now-retired request.
    // Real Stop/join completes only after its filesystem handles are closed.
    require(writer.stop(),"retired root preparation did not release/join");
    const auto status=writer.status();
    require(status && status->complete && !status->rootReady && !status->cleanupPending,"retired root preparation falsely remained ready/owned");
    require(images(folder).empty(),"root preparation retirement wrote a sample");
}
static void collectionSessionLimits() {
    TemporaryDataset first, second; auto config=configuration(first);
    config.datasetMaxImages=1;
    DatasetWriter writer([](std::uint64_t){},[](std::string){});
    ready(writer,config); auto bytes=cleanFrame();
    auto observed=Clock::now()-std::chrono::seconds(10);
    require(submit(writer,bytes,1,observed)==Admission::Accepted,"first budget admission");
    until([&]{auto s=writer.status();return s && s->saved==1 && !s->encoding;},"first budget commit");
    auto status=writer.status();
    require(status && status->savedBytes==std::filesystem::file_size(images(first).at(0))
        && std::string(status->limitReason)=="images","committed bytes/count reason");
    config.datasetDirectory=second.root.wstring(); ready(writer,config,2);
    require(submit(writer,bytes,2,observed+std::chrono::seconds(1),2,2)==Admission::OverLimit,
        "folder/source/revision reset collection budget");
    require(images(second).empty(),"limited destination received image");
    config.datasetMaxImages=2; ready(writer,config,3);
    require(submit(writer,bytes,3,observed+std::chrono::seconds(2),2,3)==Admission::Accepted,"raised limit did not resume");
    until([&]{auto s=writer.status();return s && s->saved==2 && !s->encoding;},"raised budget commit");
    config.datasetMaxImages=1; ready(writer,config,4);
    require(submit(writer,bytes,4,observed+std::chrono::seconds(3),2,4)==Admission::OverLimit,"lowered limit ignored");
    require(writer.stop(),"budget writer close");
    // A fresh writer is a new engine session, even at the same destination.
    DatasetWriter fresh([](std::uint64_t){},[](std::string){});
    ready(fresh,config);
    require(submit(fresh,bytes,9,observed)==Admission::Accepted,"new session retained previous budget");
    until([&]{auto s=fresh.status();return s && s->saved==1;},"new session commit");
    require(fresh.stop(),"new session close");
}
static void collectionStorageLimitBeforeFile() {
    TemporaryDataset folder; auto config=configuration(folder);
    config.datasetMaxMiB=1; config.datasetJpegQuality=100;
    DatasetWriter writer([](std::uint64_t){},[](std::string){});
    ready(writer,config);
    std::vector<unsigned char> pixels(1024*1024*4);
    std::uint32_t seed=12345;
    for(auto& byte:pixels) {seed=1664525*seed+1013904223;byte=static_cast<unsigned char>(seed>>24);}
    CaptureFrameIdentity identity{1,1,1,1}; require(writer.observeSource(identity),"large frame source");
    CaptureQualification qualification{identity,true,true};
    require(writer.admit(1024,1024,pixels,identity,qualification,true,Clock::now(),1000)==Admission::Accepted,"large frame admission");
    until([&]{auto s=writer.status();return s && std::string(s->limitReason)=="storage" && !s->encoding;},"encoded-byte budget not enforced");
    auto status=writer.status(); require(status && status->saved==0 && status->savedBytes==0,"rejected image consumed budget");
    require(images(folder).empty(),"oversized image published");
    for(const auto& entry:std::filesystem::directory_iterator(folder.root/L"images"))
        require(entry.path().extension()!=L".pending","over-budget pending file created");
    config.datasetMaxMiB=8;ready(writer,config,2);identity.frameId=2;identity.configRevision=2;
    std::this_thread::sleep_for(std::chrono::milliseconds(110)); // existing capture interval still applies
    require(writer.observeSource(identity),"raised storage source");qualification.identity=identity;
    require(writer.admit(1024,1024,pixels,identity,qualification,true,Clock::now(),2000)==Admission::Accepted,"raised storage admission");
    until([&]{auto s=writer.status();return s && s->saved==1 && !s->encoding;},"raised storage commit");
    status=writer.status();require(status && status->savedBytes>1048576 && status->savedBytes<=8*1048576,"encoded storage count");
    require(writer.stop(),"storage writer close");
}
int main(int argc, char**) {
    try {
        const auto com=CoInitializeEx(nullptr,COINIT_MULTITHREADED);require(SUCCEEDED(com),"test COM initialization");
        if (argc > 1) {
            collectionSessionLimits();collectionStorageLimitBeforeFile();
            CoUninitialize();std::cout<<"Collection session and storage limit checks passed\n";return 0;
        }
        std::cout<<"existing writer cases\n"<<std::flush;
        std::cout<<"interval\n"<<std::flush;intervalAndLatestQueue();
        for(int kind=0;kind<5;++kind){std::cout<<"retire "<<kind<<'\n'<<std::flush;retireBlockedFrame(kind);}
        std::cout<<"qualify\n"<<std::flush;qualifyAndStopBudget();
        std::cout<<"collision\n"<<std::flush;ownedFileCollision();
        std::cout<<"output\n"<<std::flush;outputPollContinuesDuringBlockedWriter();
        std::cout<<"prepare\n"<<std::flush;retireBlockedRootPreparation(false);retireBlockedRootPreparation(true);
        std::cout<<"session limits\n"<<std::flush;collectionSessionLimits();
        std::cout<<"storage limits\n"<<std::flush;collectionStorageLimitBeforeFile();
        CoUninitialize();return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
