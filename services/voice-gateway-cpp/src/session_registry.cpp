#include "voice_gateway/session_registry.h"

#include <algorithm>
#include <cctype>
#include <utility>
#include <vector>

namespace voice_gateway {

SessionRegistry::SessionRegistry() {
    reaper_thread_ = std::thread(&SessionRegistry::reaper_loop, this);
}

SessionRegistry::~SessionRegistry() {
    {
        std::lock_guard<std::mutex> lock(reaper_mutex_);
        reaper_stop_ = true;
    }
    reaper_cv_.notify_all();
    if (reaper_thread_.joinable()) {
        reaper_thread_.join();
    }
}

StartSessionResult SessionRegistry::start_session(const SessionConfig& config, std::string& error, RtpSession::AudioCallback audio_cb) {
    if (!validate_config(config, error)) {
        return StartSessionResult::InvalidConfig;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (sessions_.find(config.session_id) != sessions_.end()) {
        error = "session already exists";
        return StartSessionResult::AlreadyExists;
    }

    if (sessions_.size() >= kMaxConcurrentSessions) {
        error = "maximum concurrent sessions reached";
        return StartSessionResult::InternalError;
    }

    auto session = std::make_shared<RtpSession>(config);
    // Install the STT sink BEFORE start() launches the receiver thread (VG-11).
    if (audio_cb) {
        session->set_audio_callback(std::move(audio_cb));
    }
    if (!session->start(error)) {
        return StartSessionResult::InternalError;
    }

    sessions_.emplace(config.session_id, session);
    stopped_since_.erase(config.session_id);  // defensive: no stale reaper record
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
        stopped_since_.erase(session_id);
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

// Copy the live session shared_ptrs under mutex_, then release it. Callers below
// invoke per-session snapshot()/healthy() (which take the SESSION mutex) OUTSIDE
// the registry lock, so a single slow/stuck session can no longer hold up
// unrelated start/stop/lookup by blocking behind the registry lock (VG-30).
std::vector<RtpSessionPtr> SessionRegistry::collect_sessions_locked_copy() const {
    std::vector<RtpSessionPtr> sessions_copy;
    std::lock_guard<std::mutex> lock(mutex_);
    sessions_copy.reserve(sessions_.size());
    for (const auto& [_, session] : sessions_) {
        sessions_copy.push_back(session);
    }
    return sessions_copy;
}

std::vector<SessionStatsSnapshot> SessionRegistry::list_sessions() const {
    const std::vector<RtpSessionPtr> sessions_copy = collect_sessions_locked_copy();
    std::vector<SessionStatsSnapshot> rows;
    rows.reserve(sessions_copy.size());
    for (const auto& session : sessions_copy) {
        rows.push_back(session->snapshot());
    }
    return rows;
}

bool SessionRegistry::all_sessions_healthy() const {
    const std::vector<RtpSessionPtr> sessions_copy = collect_sessions_locked_copy();
    for (const auto& session : sessions_copy) {
        if (!session->healthy()) {
            return false;
        }
    }
    return true;
}

ProcessStatsSnapshot SessionRegistry::snapshot() const {
    ProcessStatsSnapshot snap;

    std::vector<RtpSessionPtr> sessions_copy;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snap.sessions_started_total = sessions_started_total_;
        snap.sessions_stopped_total = sessions_stopped_total_;
        snap.sessions_reaped_total = sessions_reaped_total_;
        sessions_copy.reserve(sessions_.size());
        for (const auto& [_, session] : sessions_) {
            sessions_copy.push_back(session);
        }
    }

    snap.active_sessions = 0;
    snap.stopped_sessions = 0;

    for (const auto& session : sessions_copy) {
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

void SessionRegistry::reaper_loop() {
    std::unique_lock<std::mutex> lock(reaper_mutex_);
    while (!reaper_stop_) {
        reaper_cv_.wait_for(
            lock,
            std::chrono::milliseconds(kReapSweepIntervalMs),
            [this] { return reaper_stop_; });
        if (reaper_stop_) {
            break;
        }
        lock.unlock();
        reap_once();
        lock.lock();
    }
}

void SessionRegistry::reap_once() {
    // Collect the sessions to drop while holding mutex_, then release the lock
    // BEFORE the shared_ptrs destruct. Dropping the last reference runs
    // ~RtpSession (which joins the session's threads); doing that outside mutex_
    // keeps start/stop/snapshot from stalling behind a thread join.
    std::vector<RtpSessionPtr> to_destroy;
    const auto now = std::chrono::steady_clock::now();

    {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto it = sessions_.begin(); it != sessions_.end();) {
            const std::string id = it->first;
            const RtpSessionPtr& session = it->second;

            if (session->running()) {
                stopped_since_.erase(id);
                ++it;
                continue;
            }

            auto since = stopped_since_.find(id);
            if (since == stopped_since_.end()) {
                stopped_since_.emplace(id, now);
                ++it;
                continue;
            }

            const int64_t stopped_ms =
                std::chrono::duration_cast<std::chrono::milliseconds>(now - since->second).count();
            if (stopped_ms < kReapGraceMs) {
                ++it;
                continue;
            }

            to_destroy.push_back(std::move(it->second));
            stopped_since_.erase(since);
            it = sessions_.erase(it);
            ++sessions_reaped_total_;
        }
    }
    // to_destroy destructs here, outside mutex_.
}

bool SessionRegistry::validate_config(const SessionConfig& config, std::string& error) {
    // Upper bounds turn "valid lower-bound / correct type" into actual resource
    // governance: without them a single well-formed request can size a
    // billion-slot jitter ring or a giant TTS queue and OOM the process (VG-17).
    constexpr std::size_t kMaxJitterBufferCapacityFrames = 4096;  // ~82s @ 20ms
    constexpr std::size_t kMaxTtsQueueFrames = 3000;              // ~60s @ 20ms
    constexpr std::size_t kMaxSessionIdLength = 128;
    constexpr int kMaxAudioCallbackBatchFrames = 100;            // ~2s @ 20ms

    if (config.session_id.empty()) {
        error = "session_id is required";
        return false;
    }

    if (config.session_id.size() > kMaxSessionIdLength) {
        error = "session_id exceeds maximum length";
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

    if (config.watchdog_tick_ms < 50 || config.watchdog_tick_ms > 5000) {
        error = "watchdog_tick_ms must be between 50 and 5000";
        return false;
    }

    if (config.jitter_buffer_capacity_frames == 0 || (config.jitter_buffer_capacity_frames & (config.jitter_buffer_capacity_frames - 1)) != 0) {
        error = "jitter_buffer_capacity_frames must be a non-zero power of two";
        return false;
    }

    if (config.jitter_buffer_capacity_frames > kMaxJitterBufferCapacityFrames) {
        error = "jitter_buffer_capacity_frames exceeds maximum";
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

    if (config.tts_max_queue_frames > kMaxTtsQueueFrames) {
        error = "tts_max_queue_frames exceeds maximum";
        return false;
    }

    if (config.audio_callback_batch_frames < 1 || config.audio_callback_batch_frames > kMaxAudioCallbackBatchFrames) {
        error = "audio_callback_batch_frames must be between 1 and its maximum";
        return false;
    }

    if (config.stt_reorder_window_frames < 1 || config.stt_reorder_window_frames > 25) {
        error = "stt_reorder_window_frames must be between 1 and 25";
        return false;
    }

    if (config.stt_reorder_hold_ms < 0 || config.stt_reorder_hold_ms > 1000) {
        error = "stt_reorder_hold_ms must be between 0 and 1000";
        return false;
    }

    return true;
}

}  // namespace voice_gateway
