#pragma once
#include <windows.h>
#include <algorithm>
#include <chrono>
#include <cmath>

namespace vaporlock::pipeline {

// Maintain a phase while work fits the budget. An overrun resumes with one new
// acquisition immediately; it never schedules a catch-up burst of old frames.
class FrameSchedule {
public:
    using Clock = std::chrono::steady_clock;
    Clock::time_point begin(Clock::time_point now, int fps) {
        fps = std::clamp(fps, 1, 1000000);
        const auto period = std::max(Clock::duration{1},
            std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1.0/fps)));
        if (fps != m_fps || !m_valid || now-m_deadline >= period) {
            m_deadline=now+period;
            m_fps=fps;
            m_valid=true;
        } else m_deadline+=period;
        return m_deadline;
    }
    void reset() noexcept { m_valid=false; }
private:
    Clock::time_point m_deadline{};
    int m_fps=0;
    bool m_valid=false;
};

class DeadlineWait {
public:
    using Clock=FrameSchedule::Clock;
    DeadlineWait():m_timer(CreateWaitableTimerExW(nullptr,nullptr,
        CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,TIMER_MODIFY_STATE|SYNCHRONIZE)) {}
    ~DeadlineWait() { if(m_timer) CloseHandle(m_timer); }
    DeadlineWait(const DeadlineWait&)=delete;
    DeadlineWait& operator=(const DeadlineWait&)=delete;
    bool highResolution() const noexcept { return m_timer!=nullptr && !m_failed; }
    template<class Cancelled>
    bool until(Clock::time_point deadline,HANDLE changed,Cancelled cancelled) {
        while(!cancelled()) {
            const auto remaining=deadline-Clock::now();
            if(remaining<=Clock::duration::zero()) return true;
            const auto ms=std::chrono::duration<double,std::milli>(remaining).count();
            const DWORD bound=static_cast<DWORD>(std::clamp(std::ceil(ms),1.0,8.0));
            LARGE_INTEGER due{};
            due.QuadPart=-std::max<LONGLONG>(1,static_cast<LONGLONG>(std::ceil(ms*10000.0)));
            if(m_timer && !m_failed && SetWaitableTimer(m_timer,&due,0,nullptr,nullptr,FALSE)) {
                const HANDLE handles[]{changed,m_timer};
                const DWORD status=changed?WaitForMultipleObjects(2,handles,FALSE,bound)
                    :WaitForSingleObject(m_timer,bound);
                if(changed && status==WAIT_OBJECT_0) return false;
                if(status==WAIT_OBJECT_0+(changed?1:0)) continue; // Recheck deadline after an early wake.
                if(status==WAIT_TIMEOUT) continue;
                m_failed=true;
            } else if(m_timer) m_failed=true;
            // A failed timer must not turn into a spin or an unbounded sleep.
            if(changed) {
                const auto status=WaitForSingleObject(changed,bound);
                if(status==WAIT_OBJECT_0) return false;
                if(status==WAIT_FAILED) Sleep(bound);
            } else Sleep(bound);
        }
        return false;
    }
private:
    HANDLE m_timer=nullptr;
    bool m_failed=false;
};
} // namespace vaporlock::pipeline
