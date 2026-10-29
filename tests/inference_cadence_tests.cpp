#include "app/InferenceCadence.h"
#include <iostream>
#include <stdexcept>
#include <string>

using Cadence = vaporlock::InferenceCadence;
using D = Cadence::Decision;
static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
static Cadence::Clock::time_point at(double seconds) {
    return Cadence::Clock::time_point{} + std::chrono::duration_cast<Cadence::Clock::duration>(
        std::chrono::duration<double>(seconds));
}
int main() {
    try {
        for (int rate : {30, 60, 120, 144, 240, 256, 360, 1000}) {
            for (int seed = 0; seed < 5; ++seed) {
                Cadence cadence;
                for (int i = 0; i < rate * 10; ++i) {
                    const double jitter = ((i * 17 + seed * 11) % 19) * .000002;
                    require(cadence.evaluate(at(i / double(rate) + jitter), true, rate, rate, 0, true) == D::Run,
                            "Equal paced limits discarded a fresh frame with skip=0");
                }
            }
        }
        int oldRuns = 0, repairedRuns = 0;
        double oldLast = -1;
        Cadence cadence;
        for (int i = 0; i < 1200; ++i) {
            const double t = i / 120.0 + (i % 2 == 0 ? .00008 : 0);
            if (oldLast < 0 || t - oldLast >= 1.0 / 60) { ++oldRuns; oldLast = t; }
            if (cadence.evaluate(at(t), true, 120, 60, 0, true) == D::Run) ++repairedRuns;
        }
        require(oldRuns < 500, "Regression stimulus did not reproduce the old loss");
        require(repairedRuns >= 599 && repairedRuns <= 601, "Deadline cadence failed 60 Hz target");
        for (int seed = 0; seed < 5; ++seed) {
            Cadence c; int runs = 0;
            for (int i = 0; i < 2560; ++i) {
                double t = i / 256.0 + ((i * 13 + seed) % 23) * .000004;
                if (c.evaluate(at(t), true, 256, 120, 0, true) == D::Run) ++runs;
            }
            require(runs >= 1198 && runs <= 1201, "Mixed limits lost cadence or exceeded rate");
        }
        Cadence skipped;
        for (int seed = 0; seed < 5; ++seed) {
            Cadence sparse, explicitHalf, unpaced;
            int sparseRuns = 0, skippedRuns = 0, unpacedRuns = 0;
            for (int i = 0; i < 1200; ++i) {
                const auto t = at(i / 120.0 + ((17 * i + 11 + seed) % 19) * .000002);
                if (sparse.evaluate(t, i % 2 == 0, 120, 60, 0, true) == D::Run) ++sparseRuns;
                if (explicitHalf.evaluate(t, true, 120, 60, 1, true) == D::Run) ++skippedRuns;
                if (i % 2 == 0 && unpaced.evaluate(t, true, 60, 60, 0, false) == D::Run) ++unpacedRuns;
            }
            require(sparseRuns == 600, "Sparse fresh frames lost to jitter");
            require(skippedRuns == 600, "Explicit skip plus rate gate lost to jitter");
            require(unpacedRuns == 600, "GUI equal-rate jitter lost frames");
        }
        // Repeated near-deadline calls cannot accumulate unlimited rate credit.
        Cadence bounded; int boundedRuns = 0;
        for (int i = 0; i < 100000; ++i) {
            const double seconds = i * .0001;
            if (bounded.evaluate(at(seconds), true, 10000, 60, 0, true) == D::Run) ++boundedRuns;
            require(boundedRuns <= int((seconds + .00025) * 60) + 1, "Timing allowance exceeded bounded credit");
        }
        for (int i = 0; i < 300; ++i) {
            require(skipped.evaluate(at(i / 60.0), false, 60, 60, 2, true) == D::NoFreshFrame,
                    "Cached image admitted");
            require(skipped.evaluate(at(i / 60.0), true, 60, 60, 2, true) ==
                    (i % 3 == 2 ? D::Run : D::ExplicitSkip), "Explicit skip counted cached frames");
        }
        Cadence stalled;
        require(stalled.evaluate(at(0), true, 120, 60, 0, true) == D::Run, "First frame lost");
        require(stalled.evaluate(at(8), true, 120, 60, 0, true) == D::Run, "Stall recovery lost fresh frame");
        require(stalled.evaluate(at(8.000001), true, 120, 60, 0, true) == D::RateLimited, "Catch-up burst after stall");
        require(stalled.evaluate(at(8.000002), true, 120, 120, 0, true) == D::Run, "Live limit change retained old deadline");
        // GUI timer callbacks are not guaranteed capture-paced; no bypass there.
        Cadence gui;
        require(gui.evaluate(at(0), true, 60, 60, 0, false) == D::Run, "GUI first frame");
        require(gui.evaluate(at(.001), true, 60, 60, 0, false) == D::RateLimited, "Unpaced callback bypassed cap");
        gui.reset();
        require(gui.evaluate(at(.001), true, 60, 60, 0, false) == D::Run, "Reset retained deadline");
        std::cout << "PASS: 40 equal-limit jitter runs, 5 mixed-limit runs, stale/skip/stall/reset checks.\n"
                  << "Regression 10s: old=" << oldRuns << " repaired=" << repairedRuns << " target=600\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
