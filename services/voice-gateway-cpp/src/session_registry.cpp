#include "voice_gateway/session_registry.h"

#include <algorithm>
#include <cctype>

namespace voice_gateway {

StartSessionResult SessionRegistry::start_session(const SessionConfig& config, std::string& error) {
    if (!validate_config(config, error)) {
        return StartSessionResult::InvalidConfig;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (sessions_.find(config.session_id) != sessions_.end()) {
        error = "session already exists";
        return StartSessionResult::AlreadyExists;
    }

    auto session = std::make_shared<RtpSession>(config);
    if (!session->start(error)) {
        return StartSessionResult::InternalError;
    }

    sessions_.emplace(config.session_id, session);
    ++sessions_started_total_;
    return StartSessionResult::Started;
}

bool SessionRegistry::stop_session(const std::string& session_id, const std::string& reason, bool& already_stopped) {
    already_stopped = false;

    RtpSessionPtr session;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = sessions_.find(session_id);
        if (it == sessions_.end()) {
            already_stopped = true;
            return true;
        }
        session = it->second;
        sessions_.erase(it);
        ++sessions_stopped_total_;
    }

    session->stop(reason.empty() ? "stopped_by_request" : reason);
    return true;
}

RtpSessionPtr SessionRegistry::get_session(const std::string& session_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sessions_.find(session_id);
    if (it == sessions_.end()) {
        return nullptr;
    }
    return it->second;
}

std::vector<SessionStatsSnapshot> SessionRegistry::list_sessions() const {
    std::vector<SessionStatsSnapshot> rows;
    std::lock_guard<std::mutex> lock(mutex_);
    rows.reserve(sessions_.size());
    for (const auto& [_, session] : sessions_) {
        rows.push_back(session->snapshot());
    }
    return rows;
}

bool SessionRegistry::all_sessions_healthy() const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [_, session] : sessions_) {
        if (!session->healthy()) {
            return false;
        }
    }
    return true;
}

ProcessStatsSnapshot SessionRegistry::snapshot() const {
    ProcessStatsSnapshot snap;

    std::lock_guard<std::mutex> lock(mutex_);
    snap.sessions_started_total = sessions_started_total_;
    snap.sessions_stopped_total = sessions_stopped_total_;
    snap.active_sessions = 0;
    snap.stopped_sessions = 0;

    for (const auto& [_, session] : sessions_) {
        const SessionStatsSnapshot session_stats = session->snapshot();
        if (session->running()) {
            ++snap.active_sessions;
        } else {
            ++snap.stopped_sessions;
        }
        snap.packets_in += session_stats.packets_in;
        snap.packets_out += session_stats.packets_out;
        snap.bytes_in += session_stats.bytes_in;
        snap.bytes_out += session_stats.bytes_out;
        snap.invalid_packets += session_stats.invalid_packets;
        snap.dropped_packets += session_stats.dropped_packets;
        snap.jitter_buffer_overflow_drops += session_stats.jitter_buffer_overflow_drops;
        snap.jitter_buffer_late_drops += session_stats.jitter_buffer_late_drops;
        snap.duplicate_packets += session_stats.duplicate_packets;
        snap.out_of_order_packets += session_stats.out_of_order_packets;
        snap.timeout_events_total += session_stats.timeout_events_total;
        snap.tts_segments_started_total += session_stats.tts_segments_started_total;
        snap.tts_segments_completed_total += session_stats.tts_segments_completed_total;
        snap.tts_segments_interrupted_total += session_stats.tts_segments_interrupted_total;
        snap.tts_frames_enqueued_total += session_stats.tts_frames_enqueued_total;
        snap.tts_frames_sent_total += session_stats.tts_frames_sent_total;
        snap.tts_frames_dropped_total += session_stats.tts_frames_dropped_total;
        snap.tts_queue_depth_frames += session_stats.tts_queue_depth_frames;
    }

    return snap;
}

bool SessionRegistry::validate_config(const SessionConfig& config, std::string& error) {
    if (config.session_id.empty()) {
        error = "session_id is required";
        return false;
    }

    if (config.listen_ip.empty() || config.remote_ip.empty()) {
        error = "listen_ip and remote_ip are required";
        return false;
    }

    if (config.listen_port == 0 || config.remote_port == 0) {
        error = "listen_port and remote_port must be non-zero";
        return false;
    }

    std::string codec = config.codec;
    std::transform(codec.begin(), codec.end(), codec.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (codec != "pcmu") {
        error = "unsupported codec; only pcmu is allowed";
        return false;
    }

    if (config.ptime_ms != 20) {
        error = "unsupported ptime_ms; only 20ms is allowed";
        return false;
    }

    if (config.startup_no_rtp_timeout_ms < 100) {
        error = "startup_no_rtp_timeout_ms must be >= 100";
        return false;
    }

    if (config.active_no_rtp_timeout_ms < 100) {
        error = "active_no_rtp_timeout_ms must be >= 100";
        return false;
    }

    if (config.hold_no_rtp_timeout_ms < 100) {
        error = "hold_no_rtp_timeout_ms must be >= 100";
        return false;
    }

    if (config.session_final_timeout_ms < config.active_no_rtp_timeout_ms) {
        error = "session_final_timeout_ms must be >= active_no_rtp_timeout_ms";
        return false;
    }

    if (config.watchdog_tick_ms < 50) {
        error = "watchdog_tick_ms must be >= 50";
        return false;
    }

    if (config.jitter_buffer_capacity_frames == 0 || (config.jitter_buffer_capacity_frames & (config.jitter_buffer_capacity_frames - 1)) != 0) {
        error = "jitter_buffer_capacity_frames must be a non-zero power of two";
        return false;
    }

    if (config.jitter_buffer_prefetch_frames == 0 || config.jitter_buffer_prefetch_frames > config.jitter_buffer_capacity_frames) {
        error = "jitter_buffer_prefetch_frames must be between 1 and jitter_buffer_capacity_frames";
        return false;
    }

    if (config.tts_max_queue_frames == 0) {
        error = "tts_max_queue_frames must be >= 1";
        return false;
    }

    return true;
}

}  // namespace voice_gateway
