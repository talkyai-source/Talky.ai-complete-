#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "voice_gateway/session.h"

namespace voice_gateway {

enum class StartSessionResult {
    Started,
    AlreadyExists,
    InvalidConfig,
    InternalError,
};

struct ProcessStatsSnapshot {
    uint64_t sessions_started_total{0};
    uint64_t sessions_stopped_total{0};
    uint64_t sessions_reaped_total{0};
    uint64_t active_sessions{0};
    uint64_t stopped_sessions{0};
    uint64_t packets_in{0};
    uint64_t packets_out{0};
    uint64_t bytes_in{0};
    uint64_t bytes_out{0};
    uint64_t invalid_packets{0};
    uint64_t dropped_packets{0};
    uint64_t jitter_buffer_overflow_drops{0};
    uint64_t jitter_buffer_late_drops{0};
    uint64_t duplicate_packets{0};
    uint64_t out_of_order_packets{0};
    uint64_t timeout_events_total{0};
    uint64_t tts_segments_started_total{0};
    uint64_t tts_segments_completed_total{0};
    uint64_t tts_segments_interrupted_total{0};
    uint64_t tts_frames_enqueued_total{0};
    uint64_t tts_frames_sent_total{0};
    uint64_t tts_frames_dropped_total{0};
    uint64_t tts_queue_depth_frames{0};
    // STT-tap observability (Batch A review).
    uint64_t stt_frames_emitted_total{0};
    uint64_t stt_floor_dropped_total{0};
    uint64_t stt_probation_dropped_total{0};
    uint64_t stt_restarts_committed_total{0};
};

class SessionRegistry {
public:
    SessionRegistry();
    ~SessionRegistry();

    SessionRegistry(const SessionRegistry&) = delete;
    SessionRegistry& operator=(const SessionRegistry&) = delete;

    // audio_cb, when set, is installed on the session BEFORE its receiver thread
    // starts, so no early caller RTP is processed before the STT sink exists
    // (VG-11). Defaulted so existing 2-arg callers are unaffected.
    StartSessionResult start_session(const SessionConfig& config, std::string& error, RtpSession::AudioCallback audio_cb = {});
    bool stop_session(const std::string& session_id, const std::string& reason, bool& already_stopped);

    [[nodiscard]] RtpSessionPtr get_session(const std::string& session_id) const;
    [[nodiscard]] std::vector<SessionStatsSnapshot> list_sessions() const;
    [[nodiscard]] bool all_sessions_healthy() const;
    [[nodiscard]] ProcessStatsSnapshot snapshot() const;

    // Erase self-stopped sessions (watchdog timeout / socket_error) that the
    // backend never explicitly stopped, once they have been stopped longer than
    // the grace period. Runs periodically on reaper_thread_; exposed for tests.
    void reap_once();

private:
    static bool validate_config(const SessionConfig& config, std::string& error);

    // Snapshot of the live session shared_ptrs taken under mutex_ and returned by
    // value, so callers can invoke per-session methods without holding the
    // registry lock (VG-30).
    std::vector<RtpSessionPtr> collect_sessions_locked_copy() const;

    void reaper_loop();

    // A self-stopped session is reaped once observed not-running for at least
    // this long. The grace period must comfortably exceed the request_stop()
    // teardown window so the reaper can never free a session while its own
    // stop epilogue (socket close + thread joins) is still in flight.
    static constexpr int64_t kReapGraceMs = 60000;
    static constexpr int64_t kReapSweepIntervalMs = 10000;

    // Hard ceiling on live sessions. Each session runs 3–4 threads and holds two
    // UDP sockets, so an unbounded count would exhaust threads/fds and take the
    // whole gateway down (VG-17). Sized well above realistic concurrent-call load.
    static constexpr std::size_t kMaxConcurrentSessions = 500;

    mutable std::mutex mutex_;
    std::unordered_map<std::string, RtpSessionPtr> sessions_;
    // Ids removed from sessions_ by stop_session() but still being torn down
    // (sockets closing / threads joining). A new start on the same id is rejected
    // while the id is here, so it cannot rebind the port before the old session's
    // sockets are actually closed (VG-16).
    std::unordered_set<std::string> stopping_;
    // session_id -> first steady_clock time the reaper observed it not-running.
    std::unordered_map<std::string, std::chrono::steady_clock::time_point> stopped_since_;
    uint64_t sessions_started_total_{0};
    uint64_t sessions_stopped_total_{0};
    uint64_t sessions_reaped_total_{0};

    // Reaper lifecycle. reaper_mutex_/reaper_cv_ are separate from mutex_ so the
    // sweep never holds the sessions lock while sleeping.
    std::mutex reaper_mutex_;
    std::condition_variable reaper_cv_;
    bool reaper_stop_{false};
    std::thread reaper_thread_;
};

}  // namespace voice_gateway
