#include "voice_gateway/session.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <iostream>
#include <thread>

namespace voice_gateway {

namespace {

sockaddr_in make_sockaddr(const std::string& ip, const uint16_t port, bool& ok) {
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    ok = inet_pton(AF_INET, ip.c_str(), &addr.sin_addr) == 1;
    return addr;
}

std::string stop_reason_or_default(const std::string& reason) {
    return reason.empty() ? "stopped_by_request" : reason;
}

}  // namespace

const char* session_state_to_string(const SessionState state) {
    switch (state) {
        case SessionState::Created:
            return "created";
        case SessionState::Starting:
            return "starting";
        case SessionState::Buffering:
            return "buffering";
        case SessionState::Active:
            return "active";
        case SessionState::Degraded:
            return "degraded";
        case SessionState::Stopping:
            return "stopping";
        case SessionState::Stopped:
            return "stopped";
        case SessionState::Failed:
            return "failed";
    }
    return "unknown";
}

RtpSession::RtpSession(SessionConfig config)
    : config_(std::move(config)),
      sequencer_(RtpSequencer::random()),
      last_rtp_rx_time_(std::chrono::steady_clock::now()),
      last_rtp_tx_time_(std::chrono::steady_clock::now()),
      started_at_(std::chrono::steady_clock::now()) {}

RtpSession::~RtpSession() {
    stop("session_destructor");
}

bool RtpSession::start(std::string& error) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (running_.load()) {
            error = "session already running";
            return false;
        }
    }

    if (!is_power_of_two(config_.jitter_buffer_capacity_frames) || config_.jitter_buffer_capacity_frames == 0) {
        error = "jitter_buffer_capacity_frames must be a non-zero power of two";
        return false;
    }

    if (config_.jitter_buffer_prefetch_frames == 0 || config_.jitter_buffer_prefetch_frames > config_.jitter_buffer_capacity_frames) {
        error = "jitter_buffer_prefetch_frames must be between 1 and jitter_buffer_capacity_frames";
        return false;
    }

    if (config_.tts_max_queue_frames == 0) {
        error = "tts_max_queue_frames must be >= 1";
        return false;
    }

    bool listen_ip_ok = false;
    const sockaddr_in listen_addr = make_sockaddr(config_.listen_ip, config_.listen_port, listen_ip_ok);
    if (!listen_ip_ok) {
        error = "invalid listen_ip";
        return false;
    }

    bool remote_ip_ok = false;
    make_sockaddr(config_.remote_ip, config_.remote_port, remote_ip_ok);
    if (!remote_ip_ok) {
        error = "invalid remote_ip";
        return false;
    }

    rx_socket_ = socket(AF_INET, SOCK_DGRAM, 0);
    if (rx_socket_ < 0) {
        error = std::string("failed to create RX socket: ") + std::strerror(errno);
        return false;
    }

    tx_socket_ = socket(AF_INET, SOCK_DGRAM, 0);
    if (tx_socket_ < 0) {
        error = std::string("failed to create TX socket: ") + std::strerror(errno);
        close(rx_socket_);
        rx_socket_ = -1;
        return false;
    }

    int reuse = 1;
    setsockopt(rx_socket_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    const timeval read_timeout{0, static_cast<suseconds_t>(config_.watchdog_tick_ms * 1000)};
    setsockopt(rx_socket_, SOL_SOCKET, SO_RCVTIMEO, &read_timeout, sizeof(read_timeout));

    if (bind(rx_socket_, reinterpret_cast<const sockaddr*>(&listen_addr), sizeof(listen_addr)) < 0) {
        error = std::string("failed to bind RX socket: ") + std::strerror(errno);
        close(rx_socket_);
        close(tx_socket_);
        rx_socket_ = -1;
        tx_socket_ = -1;
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        running_.store(true);
        rx_healthy_.store(true);
        tx_healthy_.store(true);
        stop_reason_ = "running";
        started_at_ = std::chrono::steady_clock::now();
        last_rtp_rx_time_ = started_at_;
        last_rtp_tx_time_ = started_at_;
        transition_state_locked(SessionState::Starting);

        jitter_buffer_.clear();
        tts_queue_.clear();
        tts_segments_.clear();
        next_tts_segment_id_ = 1;
        tts_last_stop_reason_ = "none";
        first_rtp_seen_ = false;
        playout_started_ = false;
        last_received_seq_valid_ = false;
        last_played_seq_valid_ = false;
        has_prev_arrival_ = false;
        prev_rtp_timestamp_ = 0;
        interarrival_jitter_ts_units_ = 0.0;
    }

    receiver_thread_ = std::thread(&RtpSession::receiver_loop, this);
    transmitter_thread_ = std::thread(&RtpSession::transmitter_loop, this);
    watchdog_thread_ = std::thread(&RtpSession::watchdog_loop, this);

    return true;
}

void RtpSession::stop(const std::string& reason) {
    request_stop(stop_reason_or_default(reason), false);
}

bool RtpSession::running() const {
    return running_.load();
}

bool RtpSession::healthy() const {
    if (!running_.load()) {
        return true;
    }
    return rx_healthy_.load() && tx_healthy_.load();
}

SessionState RtpSession::state() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return state_;
}

SessionStatsSnapshot RtpSession::snapshot() const {
    SessionStatsSnapshot snap;
    snap.session_id = config_.session_id;

    {
        std::lock_guard<std::mutex> lock(mutex_);
        snap.state = session_state_to_string(state_);
        snap.stop_reason = stop_reason_;
        snap.last_rtp_rx_ms_ago = millis_since(last_rtp_rx_time_);
        snap.last_rtp_tx_ms_ago = millis_since(last_rtp_tx_time_);
        snap.tx_next_sequence = sequencer_.next_sequence_preview();
        snap.tx_next_timestamp = sequencer_.next_timestamp_preview();
        snap.tx_ssrc = sequencer_.ssrc();
        snap.rx_interarrival_jitter_ts_units = interarrival_jitter_ts_units_;
        snap.rx_interarrival_jitter_ms = interarrival_jitter_ts_units_ / static_cast<double>(kPcmuClockRateHz / 1000);
        snap.jitter_buffer_depth_frames = jitter_buffer_.size();
        snap.tts_queue_depth_frames = tts_queue_.size();
        snap.tts_last_stop_reason = tts_last_stop_reason_;
    }

    snap.packets_in = packets_in_.load();
    snap.packets_out = packets_out_.load();
    snap.bytes_in = bytes_in_.load();
    snap.bytes_out = bytes_out_.load();
    snap.invalid_packets = invalid_packets_.load();
    snap.dropped_packets = dropped_packets_.load();
    snap.jitter_buffer_overflow_drops = jitter_buffer_overflow_drops_.load();
    snap.jitter_buffer_late_drops = jitter_buffer_late_drops_.load();
    snap.duplicate_packets = duplicate_packets_.load();
    snap.out_of_order_packets = out_of_order_packets_.load();
    snap.timeout_events_total = timeout_events_total_.load();
    snap.tts_segments_started_total = tts_segments_started_total_.load();
    snap.tts_segments_completed_total = tts_segments_completed_total_.load();
    snap.tts_segments_interrupted_total = tts_segments_interrupted_total_.load();
    snap.tts_frames_enqueued_total = tts_frames_enqueued_total_.load();
    snap.tts_frames_sent_total = tts_frames_sent_total_.load();
    snap.tts_frames_dropped_total = tts_frames_dropped_total_.load();

    return snap;
}

bool RtpSession::enqueue_tts_ulaw(
    const std::vector<uint8_t>& ulaw_audio,
    const bool clear_existing,
    std::size_t& queued_frames,
    std::string& error) {
    if (ulaw_audio.empty()) {
        error = "ulaw_audio is empty";
        return false;
    }

    if ((ulaw_audio.size() % static_cast<std::size_t>(kPcmuTimestampStep)) != 0) {
        error = "ulaw_audio length must be a multiple of 160 bytes";
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!running_.load()) {
        error = "session not running";
        return false;
    }

    if (clear_existing) {
        clear_tts_queue_locked("clear_existing");
    }

    const std::size_t frame_count = ulaw_audio.size() / static_cast<std::size_t>(kPcmuTimestampStep);
    const uint32_t segment_id = next_tts_segment_id_++;
    tts_segments_[segment_id] = TtsSegmentState{frame_count, false};
    tts_segments_started_total_.fetch_add(1);
    tts_frames_enqueued_total_.fetch_add(frame_count);

    for (std::size_t i = 0; i < frame_count; ++i) {
        const std::size_t offset = i * static_cast<std::size_t>(kPcmuTimestampStep);
        std::vector<uint8_t> payload(
            ulaw_audio.begin() + static_cast<std::ptrdiff_t>(offset),
            ulaw_audio.begin() + static_cast<std::ptrdiff_t>(offset + static_cast<std::size_t>(kPcmuTimestampStep)));
        tts_queue_.push_back(QueuedTtsFrame{segment_id, std::move(payload)});
    }

    while (tts_queue_.size() > config_.tts_max_queue_frames) {
        const auto dropped = tts_queue_.front();
        tts_queue_.pop_front();
        mark_tts_frame_dropped_locked(dropped.segment_id);
    }

    queued_frames = frame_count;
    tts_last_stop_reason_ = "running";
    queue_cv_.notify_one();
    return true;
}

bool RtpSession::interrupt_tts(const std::string& reason, std::size_t& dropped_frames, std::size_t& interrupted_segments) {
    std::lock_guard<std::mutex> lock(mutex_);
    dropped_frames = tts_queue_.size();
    interrupted_segments = tts_segments_.size();
    clear_tts_queue_locked(reason.empty() ? "barge_in" : reason);
    queue_cv_.notify_one();
    return true;
}

void RtpSession::set_audio_callback(AudioCallback cb) {
    std::lock_guard<std::mutex> lock(audio_callback_mutex_);
    audio_callback_ = std::move(cb);
}

void RtpSession::fire_audio_callback(const std::vector<uint8_t>& pcmu_batch) {
    AudioCallback cb;
    {
        std::lock_guard<std::mutex> lock(audio_callback_mutex_);
        cb = audio_callback_;
    }
    if (cb) {
        cb(config_.session_id, pcmu_batch);
    }
}

void RtpSession::receiver_loop() {
    while (running_.load()) {
        uint8_t buffer[2048]{};
        sockaddr_in from{};
        socklen_t from_len = sizeof(from);

        const ssize_t n = recvfrom(
            rx_socket_,
            buffer,
            sizeof(buffer),
            0,
            reinterpret_cast<sockaddr*>(&from),
            &from_len);

        if (n < 0) {
            if (!running_.load()) {
                break;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                continue;
            }
            rx_healthy_.store(false);
            request_stop("socket_error", false);
            break;
        }

        const auto parsed = RtpPacket::parse(buffer, static_cast<std::size_t>(n));
        if (!parsed.has_value()) {
            invalid_packets_.fetch_add(1);
            continue;
        }
        if (parsed->payload_type != 0) {
            invalid_packets_.fetch_add(1);
            continue;
        }

        packets_in_.fetch_add(1);
        bytes_in_.fetch_add(parsed->payload.size());

        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto arrival = std::chrono::steady_clock::now();
            last_rtp_rx_time_ = arrival;

            if (!first_rtp_seen_) {
                first_rtp_seen_ = true;
                transition_state_locked(SessionState::Buffering);
            } else if (state_ == SessionState::Degraded) {
                transition_state_locked(SessionState::Active);
            }

            if (has_prev_arrival_) {
                const double arrival_delta_seconds = std::chrono::duration<double>(arrival - prev_arrival_time_).count();
                const double arrival_delta_rtp_units = arrival_delta_seconds * static_cast<double>(kPcmuClockRateHz);
                const int32_t rtp_delta = static_cast<int32_t>(parsed->timestamp - prev_rtp_timestamp_);
                const double d = std::fabs(arrival_delta_rtp_units - static_cast<double>(rtp_delta));
                interarrival_jitter_ts_units_ += (d - interarrival_jitter_ts_units_) / 16.0;
            }
            prev_arrival_time_ = arrival;
            prev_rtp_timestamp_ = parsed->timestamp;
            has_prev_arrival_ = true;

            if (last_played_seq_valid_ && sequence_diff(parsed->sequence_number, last_played_seq_) <= 0) {
                jitter_buffer_late_drops_.fetch_add(1);
                dropped_packets_.fetch_add(1);
                continue;
            }

            bool duplicate = false;
            for (const auto& frame : jitter_buffer_) {
                if (frame.sequence_number == parsed->sequence_number) {
                    duplicate = true;
                    break;
                }
            }
            if (duplicate) {
                duplicate_packets_.fetch_add(1);
                continue;
            }

            if (last_received_seq_valid_) {
                const uint16_t expected_next = static_cast<uint16_t>(last_received_seq_ + 1);
                if (parsed->sequence_number != expected_next) {
                    out_of_order_packets_.fetch_add(1);
                }
                if (sequence_diff(parsed->sequence_number, last_received_seq_) > 0) {
                    last_received_seq_ = parsed->sequence_number;
                }
            } else {
                last_received_seq_valid_ = true;
                last_received_seq_ = parsed->sequence_number;
            }

            const QueuedRtpFrame frame{parsed->sequence_number, parsed->timestamp, parsed->payload};

            auto insert_pos = jitter_buffer_.end();
            for (auto it = jitter_buffer_.begin(); it != jitter_buffer_.end(); ++it) {
                if (sequence_diff(frame.sequence_number, it->sequence_number) < 0) {
                    insert_pos = it;
                    break;
                }
            }
            jitter_buffer_.insert(insert_pos, frame);

            while (jitter_buffer_.size() > config_.jitter_buffer_capacity_frames) {
                jitter_buffer_.pop_front();
                jitter_buffer_overflow_drops_.fetch_add(1);
                dropped_packets_.fetch_add(1);
            }

            if (playout_started_) {
                const std::size_t target_depth = std::min(kDefaultJitterTargetDepthFrames, config_.jitter_buffer_capacity_frames);
                while (jitter_buffer_.size() > target_depth) {
                    jitter_buffer_.pop_front();
                    jitter_buffer_overflow_drops_.fetch_add(1);
                    dropped_packets_.fetch_add(1);
                }
            }
        }

        queue_cv_.notify_one();

        // Fire audio callback with the received payload so the AI pipeline
        // (STT) can process caller speech. The callback runs outside the
        // mutex to avoid blocking the jitter buffer / TTS paths.
        if (!parsed->payload.empty()) {
            fire_audio_callback(parsed->payload);
        }
    }
}

void RtpSession::transmitter_loop() {
    bool remote_ok = false;
    const sockaddr_in remote_addr = make_sockaddr(config_.remote_ip, config_.remote_port, remote_ok);
    if (!remote_ok) {
        tx_healthy_.store(false);
        request_stop("internal_error", false);
        return;
    }

    auto next_send_time = std::chrono::steady_clock::now();

    while (running_.load()) {
        std::vector<uint8_t> payload;
        uint32_t tts_segment_id = 0;
        bool sending_tts = false;

        {
            std::unique_lock<std::mutex> lock(mutex_);
            queue_cv_.wait(lock, [this] {
                if (!running_.load()) {
                    return true;
                }
                if (!tts_queue_.empty()) {
                    return true;
                }
                if (!config_.echo_enabled) {
                    return false;
                }
                if (jitter_buffer_.empty()) {
                    return false;
                }
                if (!config_.jitter_buffer_enabled) {
                    return true;
                }
                if (playout_started_) {
                    return true;
                }
                return jitter_buffer_.size() >= config_.jitter_buffer_prefetch_frames;
            });

            if (!running_.load()) {
                break;
            }
            if (!tts_queue_.empty()) {
                QueuedTtsFrame tts_frame = std::move(tts_queue_.front());
                tts_queue_.pop_front();
                payload = std::move(tts_frame.payload);
                tts_segment_id = tts_frame.segment_id;
                sending_tts = true;
            } else {
                if (!config_.echo_enabled || jitter_buffer_.empty()) {
                    continue;
                }

                if (!playout_started_) {
                    playout_started_ = true;
                    if (state_ == SessionState::Buffering || state_ == SessionState::Starting || state_ == SessionState::Degraded) {
                        transition_state_locked(SessionState::Active);
                    }
                }

                QueuedRtpFrame frame = std::move(jitter_buffer_.front());
                jitter_buffer_.pop_front();
                last_played_seq_valid_ = true;
                last_played_seq_ = frame.sequence_number;
                payload = std::move(frame.payload);
            }
        }

        const auto now = std::chrono::steady_clock::now();
        if (next_send_time < now) {
            next_send_time = now;
        }

        std::this_thread::sleep_until(next_send_time);

        RtpPacket outbound;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            outbound = sequencer_.next_packet(payload, 0);
        }
        const std::vector<uint8_t> packet_bytes = outbound.serialize();

        const ssize_t sent = sendto(
            tx_socket_,
            packet_bytes.data(),
            packet_bytes.size(),
            0,
            reinterpret_cast<const sockaddr*>(&remote_addr),
            sizeof(remote_addr));

        if (sent < 0) {
            if (!running_.load()) {
                break;
            }
            if (sending_tts) {
                std::lock_guard<std::mutex> lock(mutex_);
                mark_tts_frame_dropped_locked(tts_segment_id);
            }
            tx_healthy_.store(false);
            request_stop("socket_error", false);
            break;
        }

        packets_out_.fetch_add(1);
        bytes_out_.fetch_add(static_cast<std::size_t>(sent));

        {
            std::lock_guard<std::mutex> lock(mutex_);
            last_rtp_tx_time_ = std::chrono::steady_clock::now();
            if (sending_tts) {
                mark_tts_frame_sent_locked(tts_segment_id);
            }
        }

        std::cout << "event=rtp_tx"
                  << " session_id=" << config_.session_id
                  << " seq=" << outbound.sequence_number
                  << " ts=" << outbound.timestamp
                  << " ssrc=" << outbound.ssrc
                  << " mode=" << (sending_tts ? "tts" : "echo")
                  << " state=" << session_state_to_string(state())
                  << " packets_in=" << packets_in_.load()
                  << " packets_out=" << packets_out_.load()
                  << std::endl;

        next_send_time += std::chrono::milliseconds(config_.ptime_ms);
    }
}

void RtpSession::watchdog_loop() {
    const int tick_ms = std::max(50, config_.watchdog_tick_ms);

    while (running_.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(tick_ms));
        if (!running_.load()) {
            break;
        }

        std::string timeout_reason;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto now = std::chrono::steady_clock::now();
            const int64_t elapsed_since_start_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - started_at_).count();
            const int64_t elapsed_since_rx_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - last_rtp_rx_time_).count();

            if (elapsed_since_start_ms >= config_.session_final_timeout_ms) {
                timeout_reason = "final_timeout";
            } else if (!first_rtp_seen_ && elapsed_since_start_ms >= config_.startup_no_rtp_timeout_ms) {
                timeout_reason = "start_timeout";
            } else if (first_rtp_seen_) {
                const int warning_threshold = std::max(200, config_.active_no_rtp_timeout_ms / 2);
                if (elapsed_since_rx_ms >= warning_threshold && (state_ == SessionState::Active || state_ == SessionState::Buffering)) {
                    transition_state_locked(SessionState::Degraded);
                }

                if (config_.hold_no_rtp_timeout_ms > 0 &&
                    config_.hold_no_rtp_timeout_ms <= config_.active_no_rtp_timeout_ms &&
                    elapsed_since_rx_ms >= config_.hold_no_rtp_timeout_ms) {
                    timeout_reason = "no_rtp_timeout_hold";
                } else if (elapsed_since_rx_ms >= config_.active_no_rtp_timeout_ms) {
                    timeout_reason = "no_rtp_timeout";
                }
            }
        }

        if (!timeout_reason.empty()) {
            request_stop(timeout_reason, true);
            break;
        }
    }
}

void RtpSession::clear_tts_queue_locked(const std::string& reason) {
    const bool had_tts_activity = !tts_queue_.empty() || !tts_segments_.empty();
    while (!tts_queue_.empty()) {
        const auto frame = tts_queue_.front();
        tts_queue_.pop_front();
        mark_tts_frame_dropped_locked(frame.segment_id);
    }
    if (had_tts_activity || tts_last_stop_reason_ == "none" || tts_last_stop_reason_ == "running") {
        tts_last_stop_reason_ = reason.empty() ? "interrupted" : reason;
    }
}

void RtpSession::mark_tts_frame_sent_locked(const uint32_t segment_id) {
    auto it = tts_segments_.find(segment_id);
    if (it == tts_segments_.end()) {
        return;
    }
    if (it->second.remaining_frames > 0) {
        --it->second.remaining_frames;
    }
    tts_frames_sent_total_.fetch_add(1);
    if (it->second.remaining_frames == 0) {
        if (it->second.interrupted) {
            tts_segments_interrupted_total_.fetch_add(1);
        } else {
            tts_segments_completed_total_.fetch_add(1);
            tts_last_stop_reason_ = "tts_complete";
        }
        tts_segments_.erase(it);
    }
}

void RtpSession::mark_tts_frame_dropped_locked(const uint32_t segment_id) {
    auto it = tts_segments_.find(segment_id);
    if (it == tts_segments_.end()) {
        return;
    }
    if (it->second.remaining_frames > 0) {
        --it->second.remaining_frames;
    }
    it->second.interrupted = true;
    tts_frames_dropped_total_.fetch_add(1);
    if (it->second.remaining_frames == 0) {
        tts_segments_interrupted_total_.fetch_add(1);
        tts_segments_.erase(it);
    }
}

void RtpSession::request_stop(const std::string& reason, const bool timeout_event) {
    const std::thread::id caller = std::this_thread::get_id();
    const bool was_running = running_.exchange(false);

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (timeout_event) {
            timeout_events_total_.fetch_add(1);
        }
        clear_tts_queue_locked(reason);

        if (state_ == SessionState::Stopped) {
            if (stop_reason_ == "running") {
                stop_reason_ = stop_reason_or_default(reason);
            }
        } else {
            const bool failed_state = (reason == "socket_error" || reason == "internal_error");
            if (failed_state) {
                transition_state_locked(SessionState::Failed);
            }

            if (was_running) {
                transition_state_locked(SessionState::Stopping);
            }

            if (stop_reason_ == "running") {
                stop_reason_ = stop_reason_or_default(reason);
            }

            transition_state_locked(SessionState::Stopped);
        }
    }

    if (rx_socket_ >= 0) {
        shutdown(rx_socket_, SHUT_RDWR);
        close(rx_socket_);
        rx_socket_ = -1;
    }

    if (tx_socket_ >= 0) {
        shutdown(tx_socket_, SHUT_RDWR);
        close(tx_socket_);
        tx_socket_ = -1;
    }

    queue_cv_.notify_all();

    if (receiver_thread_.joinable()) {
        if (receiver_thread_.get_id() == caller) {
            receiver_thread_.detach();
        } else {
            receiver_thread_.join();
        }
    }

    if (transmitter_thread_.joinable()) {
        if (transmitter_thread_.get_id() == caller) {
            transmitter_thread_.detach();
        } else {
            transmitter_thread_.join();
        }
    }

    if (watchdog_thread_.joinable()) {
        if (watchdog_thread_.get_id() == caller) {
            watchdog_thread_.detach();
        } else {
            watchdog_thread_.join();
        }
    }
}

void RtpSession::transition_state_locked(const SessionState next_state) {
    if (state_ == next_state) {
        return;
    }
    if (!can_transition(state_, next_state)) {
        return;
    }
    state_ = next_state;
}

bool RtpSession::can_transition(const SessionState from, const SessionState to) {
    switch (from) {
        case SessionState::Created:
            return to == SessionState::Starting || to == SessionState::Stopping || to == SessionState::Stopped || to == SessionState::Failed;
        case SessionState::Starting:
            return to == SessionState::Buffering || to == SessionState::Degraded || to == SessionState::Stopping || to == SessionState::Stopped || to == SessionState::Failed;
        case SessionState::Buffering:
            return to == SessionState::Active || to == SessionState::Degraded || to == SessionState::Stopping || to == SessionState::Stopped || to == SessionState::Failed;
        case SessionState::Active:
            return to == SessionState::Degraded || to == SessionState::Stopping || to == SessionState::Stopped || to == SessionState::Failed;
        case SessionState::Degraded:
            return to == SessionState::Active || to == SessionState::Stopping || to == SessionState::Stopped || to == SessionState::Failed;
        case SessionState::Stopping:
            return to == SessionState::Stopped;
        case SessionState::Failed:
            return to == SessionState::Stopping || to == SessionState::Stopped;
        case SessionState::Stopped:
            return false;
    }
    return false;
}

int16_t RtpSession::sequence_diff(const uint16_t lhs, const uint16_t rhs) {
    return static_cast<int16_t>(lhs - rhs);
}

bool RtpSession::is_power_of_two(const std::size_t value) {
    return value > 0 && (value & (value - 1)) == 0;
}

int64_t RtpSession::millis_since(const std::chrono::steady_clock::time_point& ts) {
    const auto now = std::chrono::steady_clock::now();
    const auto delta = std::chrono::duration_cast<std::chrono::milliseconds>(now - ts);
    return std::max<int64_t>(0, delta.count());
}

}  // namespace voice_gateway
