#pragma once

#include <windows.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <utility>

namespace vaporlock::io {

// One writer owns the inherited synchronous stdout handle. Producers replace
// pending state messages; they never wait for the GUI to consume pipe bytes.
class AsyncTelemetry {
public:
    enum class Kind : std::size_t { Stats, Overlay, Preview, Dataset, Notice, Control, Count };
    static constexpr std::size_t maxLineBytes = 64U * 1024U * 1024U;

    explicit AsyncTelemetry(HANDLE destination) : m_destination(destination) {
        m_thread = std::thread([this] { run(); });
    }
    ~AsyncTelemetry() { stop(); }
    AsyncTelemetry(const AsyncTelemetry&) = delete;
    AsyncTelemetry& operator=(const AsyncTelemetry&) = delete;

    bool publish(Kind kind, std::string line) {
        if (line.size() >= maxLineBytes || kind >= Kind::Count) return false;
        line.push_back('\n');
        {
            std::lock_guard lock(m_mutex);
            if (m_stopping || m_finishing || m_failed) return false;
            m_pending[static_cast<std::size_t>(kind)].swap(line);
        }
        m_wake.notify_one();
        return true;
    }

    void clear(Kind kind) {
        std::string discarded;
        {
            std::lock_guard lock(m_mutex);
            if (kind < Kind::Count) m_pending[static_cast<std::size_t>(kind)].swap(discarded);
        }
    }

    bool failed() const { return m_failed.load(); }

    // Called only after movement has stopped. Allow a short terminal-message
    // drain, then cancel outstanding writes and wait for OS completion.
    void finish(std::string terminal, DWORD drainMs = 250) {
        {
            std::lock_guard lock(m_mutex);
            m_finishing = true;
            for (auto& message : m_pending) message.clear();
            if (terminal.size() < maxLineBytes) m_terminal = std::move(terminal) + '\n';
        }
        m_wake.notify_one();
        if (m_thread.joinable()) WaitForSingleObject(m_thread.native_handle(), drainMs);
        stop();
    }

    void stop() {
        {
            std::lock_guard lock(m_mutex);
            m_stopping = true;
            for (auto& message : m_pending) message.clear();
        }
        m_wake.notify_one();
        if (!m_thread.joinable()) return;
        // Cancellation can race the beginning of WriteFile. Recheck until the
        // dedicated writer exits; never detach it or free a live write buffer.
        while (WaitForSingleObject(m_thread.native_handle(), 5) == WAIT_TIMEOUT)
            CancelSynchronousIo(m_thread.native_handle());
        m_thread.join();
    }

private:
    void run() {
        for (;;) {
            std::string line;
            bool terminal = false;
            {
                std::unique_lock lock(m_mutex);
                m_wake.wait(lock, [this] {
                    if (m_stopping || m_finishing) return true;
                    for (const auto& message : m_pending) if (!message.empty()) return true;
                    return false;
                });
                if (m_stopping) return;
                if (m_finishing) {
                    line.swap(m_terminal);
                    terminal = true;
                } else {
                    // Rotate priorities so frequent overlays cannot starve
                    // statistics, previews, or cumulative dataset counts.
                    for (std::size_t offset = 0; offset < m_pending.size(); ++offset) {
                        const auto index = (m_next + offset) % m_pending.size();
                        if (m_pending[index].empty()) continue;
                        line.swap(m_pending[index]);
                        m_next = (index + 1) % m_pending.size();
                        break;
                    }
                }
            }
            std::size_t position = 0;
            while (position < line.size()) {
                if (m_stopping) return;
                DWORD written = 0;
                const auto count = static_cast<DWORD>((std::min)(line.size() - position, std::size_t{65536}));
                if (!WriteFile(m_destination, line.data() + position, count, &written, nullptr) || !written || written > count) {
                    if (!m_stopping) m_failed = true;
                    return;
                }
                position += written;
            }
            if (terminal) return;
        }
    }

    HANDLE m_destination;
    std::array<std::string, static_cast<std::size_t>(Kind::Count)> m_pending;
    std::string m_terminal;
    std::size_t m_next = 0;
    std::atomic<bool> m_stopping{false}, m_failed{false};
    bool m_finishing = false;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::thread m_thread;
};

} // namespace vaporlock::io
