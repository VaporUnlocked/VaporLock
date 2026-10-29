// Source-only staged tests. No compiler/test execution is authorized yet.
#include "app/CaptureQualification.h"
#include "app/CaptureSettingParse.h"
#ifdef NDEBUG
#undef NDEBUG // qualification/parser checks remain active in future Release builds
#endif
#include <cassert>
#include <limits>

int main() {
    CaptureQualification capture{{1, 2, 3, 4}, true, false};
    // A valid decoded 30% candidate qualifies capture at a 20% cutoff.
    capture.observe(.30, .20, true, 0, 0, 100, 100);
    assert(capture.qualifies);
    assert(.30 < .55);
    capture.qualifies = false;
    capture.observe(.98, .99, true, 0, 0, 100, 100);
    assert(!capture.qualifies);
    capture.observe(.99, .99, true, 0, 0, 100, 100);
    assert(capture.qualifies);
    capture.qualifies = false;
    capture.observe(1, 1, true, 0, 0, 100, 100);
    assert(capture.qualifies);
    capture.qualifies = false;
    for (const auto score : {-1., 1.0001, std::numeric_limits<double>::infinity(),
                             std::numeric_limits<double>::quiet_NaN()}) {
        capture.observe(score, .20, true, 0, 0, 100, 100);
        assert(!capture.qualifies);
    }
    capture.observe(.30, .20, false, 0, 0, 100, 100);
    capture.observe(.30, .20, true, 5, 5, 5, 100);
    capture.observe(.30, .20, true, 0, 0, std::numeric_limits<double>::infinity(), 100);
    assert(!capture.qualifies);
    capture.observed = false;
    capture.observe(.30, .20, true, 0, 0, 100, 100);
    assert(!capture.qualifies);
    assert(!(capture.identity == CaptureFrameIdentity{1, 2, 4, 4}));
    assert(!(capture.identity == CaptureFrameIdentity{1, 3, 3, 4}));
    assert(!(capture.identity == CaptureFrameIdentity{1, 2, 3, 5}));
    assert(!(capture.identity == CaptureFrameIdentity{2, 2, 3, 4}));
    struct Box { double x=0, y=0, w=100, h=100; };
    struct Detection { Box box; double confidence; };
    const std::vector<Detection> feed{{{}, .4}, {{}, .99}};
    capture.observeFeed(feed, .99, true); assert(capture.observed && capture.qualifies);
    capture.observeFeed(feed, 1.0, true); assert(capture.observed && !capture.qualifies);
    capture.observeFeed(feed, .2, false); assert(!capture.observed && !capture.qualifies);
    capture.observeFeed(std::vector<Detection>{}, .2, true); assert(capture.observed && !capture.qualifies);
    std::size_t bytes = 0;
    assert(captureBgraBytes(4096, 4096, 64U * 1024U * 1024U, bytes));
    assert(bytes == 64U * 1024U * 1024U);
    assert(!captureBgraBytes(4096, 4097, 0, bytes));
    assert(!captureBgraBytes(16385, 1, 65540, bytes));
    assert(!captureBgraBytes(1, 1, 3, bytes));
    assert(!captureBgraBytes(-1, 1, 4, bytes));
    assert(captureTensorElements({1, 8400, 84}, 705600));
    assert(captureTensorElements({8400, 84}, 705600, false));
    assert(!captureTensorElements({8400, 84}, 705600));
    assert(!captureTensorElements({2, 8400, 84}, 1411200));
    assert(!captureTensorElements({1, 8400, 84}, 705599));
    assert(!captureTensorElements({1, -1, 84}, 705600));
    assert(!captureTensorElements({1, 9223372036854775807LL, 9223372036854775807LL}, 1));
    double confidence=.5;
    for(const auto token : {L"0.01",L".20",L"0.99",L"1",L".20123456789",L"2e-1"})
        assert(vaporlock::collection::confidence(token,confidence));
    for(const auto token : {L"nan",L"inf",L"-inf",L"-0.2",L"+0.2",L"0",L"1.01",L"0.2x",L" 0.2",L"0x1",L"1e9999",L"1e",L""}) {
        confidence=.5;assert(!vaporlock::collection::confidence(token,confidence) && confidence==.5);
    }
    int interval=1000;
    assert(vaporlock::collection::integer(L"100",100,1000000,interval) && interval==100);
    assert(vaporlock::collection::integer(L"1000000",100,1000000,interval) && interval==1000000);
    for(const auto token : {L"99",L"1000001",L"-100",L"+100",L"100.0",L"100x",L" 100",L"9999999999999999999999999999",L""}) {
        interval=1000;assert(!vaporlock::collection::integer(token,100,1000000,interval) && interval==1000);
    }
}
