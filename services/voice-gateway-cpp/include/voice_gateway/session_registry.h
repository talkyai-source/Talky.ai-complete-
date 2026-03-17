#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
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
};

class SessionRegistry {
public:
    SessionRegistry() = default;

    StartSessionResult start_session(const SessionConfig& config, std::string& error);
    bool stop_session(const std::string& session_id, const std::string& reason, bool& already_stopped);

    [[nodiscard]] RtpSessionPtr get_session(const std::string& session_id) const;
    [[nodiscard]] std::vector<SessionStatsSnapshot> list_sessions() const;
    [[nodiscard]] bool all_sessions_healthy() const;
    [[nodiscard]] ProcessStatsSnapshot snapshot() const;

private:
    static bool validate_config(const SessionConfig& config, std::string& error);

    mutable std::mutex mutex_;
    std::unordered_map<std::string, RtpSessionPtr> sessions_;
    uint64_t sessions_started_total_{0};
    uint64_t sessions_stopped_total_{0};
};

}  // namespace voice_gateway
