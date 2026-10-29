static void verifyCaptureRecovery() {
    using Recovery = vaporlock::CaptureRecovery;
    using namespace std::chrono_literals;
    const auto start = Recovery::Time{} + 10s;
    Recovery r; r.reset(start);
    require(!r.probeDue(start+99ms), "capture recovery probed a healthy delivery interval");
    require(r.probeDue(start+100ms), "capture starvation did not become probe eligible");
    r.probing(start+100ms);
    std::vector<unsigned char> first{10,20,30,0}, alpha{10,20,30,255}, changed{11,20,30,0};
    require(!r.progressing(first,1,1), "single probe falsely established image progress");
    require(!r.probeDue(start+124ms) && r.probeDue(start+125ms), "confirmation probe timing incorrect");
    r.probing(start+125ms);
    require(!r.progressing(alpha,1,1), "unused alpha was treated as desktop motion");
    require(!r.probeDue(start+374ms) && r.probeDue(start+375ms), "unchanged pair did not back off");
    r.probing(start+375ms);
    require(!r.progressing(first,1,1), "unchanged pair retained its old comparison");
    r.probing(start+400ms);
    require(r.progressing(changed,1,1), "BGR image progress was missed");
    r.failedProbe();
    require(!r.probeDue(start+649ms) && r.probeDue(start+650ms), "failed pair did not back off");
    require(!r.progressing(first,1,1), "failed probe retained old comparison state");
    require(!r.progressing({},1,1), "incomplete capture was accepted");
    require(!r.progressing(changed,1,1), "invalid geometry retained comparison state");
    require(!r.acceptPresentation({},start+1s,start), "pointer-only presentation accepted");
    require(!r.acceptPresentation(start+10ms,start+1s,start), "stale presentation accepted");
    require(!r.acceptPresentation(start+1s,start+1s,start+1s), "older than accepted GDI snapshot accepted");
    require(!r.acceptPresentation(start+2s,start+1s,start), "future presentation accepted");
    require(r.acceptPresentation(start+1000ms,start+1001ms,start+999ms), "healthy fresh presentation rejected");
    r.recover();
    require(r.recovering && r.transitions==1 && r.probeDue(start+251ms), "GDI recovery did not stay live");
    for (int i=0;i<300;++i) {
        const auto time=start+1s+i*20ms;
        require(!r.acceptPresentation(time,time+1ms,time-1ms), "DXGI burst broke the established GDI latch");
        r.recover();
        require(r.transitions==1 && r.probeDue(time), "GDI latch churned or stopped delivering");
    }
    r.reset(start+2s);
    require(!r.recovering && r.acceptPresentation(start+2010ms,start+2011ms,start+2009ms), "explicit reset did not retry DXGI");
    r.presented(start+2011ms);
    require(!r.probeDue(start+2050ms), "healthy presentation did not reset stall clock");
    r.probing(start+2100ms);
    r.presented(start+2110ms);
    require(r.probeDue(start+2210ms), "fresh DXGI retained the startup probe backoff clock");
    require(!r.progressing(first,1,1), "new geometry retained old source probes");
    std::cout << "Capture recovery freshness, probe, session latch and explicit reset checks passed\n";
}
