#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "voice_gateway/rtp_packet.h"

struct sockaddr_in;

namespace voice_gateway {

enum class SessionState {
    Created,
    Starting,
    Buffering,
    Active,
    Degraded,
    Stopping,
    Stopped,
    Failed,
};

[[nodiscard]] const char* session_state_to_string(SessionState state);

struct SessionConfig {
    std::string session_id;
    std::string listen_ip;
    uint16_t listen_port{0};
    std::string remote_ip;
    uint16_t remote_port{0};
    std::string codec{"pcmu"};
    int ptime_ms{20};
    int startup_no_rtp_timeout_ms{5000};
    int active_no_rtp_timeout_ms{8000};
    int hold_no_rtp_timeout_ms{45000};
    int session_final_timeout_ms{7200000};
    int watchdog_tick_ms{200};
    bool jitter_buffer_enabled{true};
    std::size_t jitter_buffer_capacity_frames{64};
    std::size_t jitter_buffer_prefetch_frames{3};
    bool echo_enabled{true};
    std::size_t tts_max_queue_frames{400};
    // When set, every received RTP audio frame (G.711 µ-law, 160 bytes) is
    // base64-encoded and POSTed to this URL as JSON:
    //   {"session_id":"...","pcmu_base64":"...","codec":"pcmu"}
    // The backend uses this to feed caller audio into the STT pipeline.
    // Optional — if empty, received audio is only used for echo/jitter buffer.
    std::string audio_callback_url;
    // Maximum number of audio frames to batch into a single callback POST.
    // 1 = one POST per 20 ms frame (lowest latency). Default: 1.
    int audio_callback_batch_frames{1};
};

struct SessionStatsSnapshot {
    std::string session_id;
    std::string state;
    std::string stop_reason;
    uint64_t packets_in{0};
    uint64_t packets_out{0};
    uint64_t bytes_in{0};
    uint64_t bytes_out{0};
    uint64_t invalid_packets{0};
    uint64_t dropped_packets{0};
    int64_t last_rtp_rx_ms_ago{-1};
    int64_t last_rtp_tx_ms_ago{-1};
    uint16_t tx_next_sequence{0};
    uint32_t tx_next_timestamp{0};
    uint32_t tx_ssrc{0};
    double rx_interarrival_jitter_ts_units{0.0};
    double rx_interarrival_jitter_ms{0.0};
    uint64_t jitter_buffer_depth_frames{0};
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
    std::string tts_last_stop_reason;
};

class RtpSession {
public:
    explicit RtpSession(SessionConfig config);
    ~RtpSession();

    RtpSession(const RtpSession&) = delete;
    RtpSession& operator=(const RtpSession&) = delete;

    bool start(std::string& error);
    void stop(const std::string& reason);

    [[nodiscard]] bool running() const;
    [[nodiscard]] bool healthy() const;
    [[nodiscard]] SessionState state() const;
    [[nodiscard]] SessionStatsSnapshot snapshot() const;
    bool enqueue_tts_ulaw(const std::vector<uint8_t>& ulaw_audio, bool clear_existing, std::size_t& queued_frames, std::string& error);
    bool interrupt_tts(const std::string& reason, std::size_t& dropped_frames, std::size_t& interrupted_segments);

    // Register a callback that fires with each received audio batch.
    // Called from the receiver thread; keep it non-blocking.
    using AudioCallback = std::function<void(const std::string& session_id, const std::vector<uint8_t>& pcmu_audio)>;
    void set_audio_callback(AudioCallback cb);

private:
    static constexpr std::size_t kDefaultJitterTargetDepthFrames = 6;
    static constexpr int kPcmuClockRateHz = 8000;
    static constexpr int kPcmuTimestampStep = 160;
    static constexpr int kRtcpReportIntervalMs = 5000;

    struct QueuedRtpFrame {
        uint16_t sequence_number{0};
        uint32_t timestamp{0};
        std::size_t payload_size{0};
        std::array<uint8_t, kPcmuTimestampStep> payload{};
    };
    struct QueuedTtsFrame {
        uint32_t segment_id{0};
        std::array<uint8_t, kPcmuTimestampStep> payload{};
    };
    struct JitterSlot {
        bool occupied{false};
        QueuedRtpFrame frame;
    };
    struct TtsSegmentState {
        std::size_t remaining_frames{0};
        bool interrupted{false};
    };

    void receiver_loop();
    void transmitter_loop();
    void watchdog_loop();
    void clear_tts_queue_locked(const std::string& reason);
    void mark_tts_frame_sent_locked(uint32_t segment_id);
    void mark_tts_frame_dropped_locked(uint32_t segment_id);
    void request_stop(const std::string& reason, bool timeout_event = false);
    void transition_state_locked(SessionState next_state);
    static bool can_transition(SessionState from, SessionState to);
    static int16_t sequence_diff(uint16_t lhs, uint16_t rhs);
    static bool is_power_of_two(std::size_t value);
    static int64_t millis_since(const std::chrono::steady_clock::time_point& ts);
    static bool is_rtcp_packet(const uint8_t* data, std::size_t len);
    void fire_audio_callback(const std::vector<uint8_t>& pcmu_batch);
    void reset_jitter_buffer_locked();
    std::size_t jitter_index(uint16_t sequence_number) const;
    bool insert_jitter_frame_locked(const QueuedRtpFrame& frame);
    bool pop_next_jitter_frame_locked(QueuedRtpFrame& frame);
    void drop_oldest_jitter_frame_locked();
    void advance_jitter_min_seq_locked();
    void maybe_send_rtcp_report(const sockaddr_in& remote_addr, const std::chrono::steady_clock::time_point& now);

    // Audio callback (optional) — fires for every received audio batch.
    // Protected by audio_callback_mutex_ to allow set_audio_callback() at any time.
    mutable std::mutex audio_callback_mutex_;
    AudioCallback audio_callback_;

    SessionConfig config_;

    mutable std::mutex mutex_;
    std::condition_variable queue_cv_;
    std::vector<JitterSlot> jitter_slots_;
    std::size_t jitter_buffer_size_{0};
    bool jitter_min_seq_valid_{false};
    uint16_t jitter_min_seq_{0};
    std::deque<QueuedTtsFrame> tts_queue_;
    std::unordered_map<uint32_t, TtsSegmentState> tts_segments_;
    uint32_t next_tts_segment_id_{1};
    std::string tts_last_stop_reason_{"none"};

    std::atomic<bool> running_{false};
    std::atomic<bool> rx_healthy_{true};
    std::atomic<bool> tx_healthy_{true};

    std::thread receiver_thread_;
    std::thread transmitter_thread_;
    std::thread watchdog_thread_;

    int rx_socket_{-1};
    int tx_socket_{-1};

    RtpSequencer sequencer_;

    std::atomic<uint64_t> packets_in_{0};
    std::atomic<uint64_t> packets_out_{0};
    std::atomic<uint64_t> bytes_in_{0};
    std::atomic<uint64_t> bytes_out_{0};
    std::atomic<uint64_t> invalid_packets_{0};
    std::atomic<uint64_t> dropped_packets_{0};
    std::atomic<uint64_t> jitter_buffer_overflow_drops_{0};
    std::atomic<uint64_t> jitter_buffer_late_drops_{0};
    std::atomic<uint64_t> duplicate_packets_{0};
    std::atomic<uint64_t> out_of_order_packets_{0};
    std::atomic<uint64_t> timeout_events_total_{0};
    std::atomic<uint64_t> tts_segments_started_total_{0};
    std::atomic<uint64_t> tts_segments_completed_total_{0};
    std::atomic<uint64_t> tts_segments_interrupted_total_{0};
    std::atomic<uint64_t> tts_frames_enqueued_total_{0};
    std::atomic<uint64_t> tts_frames_sent_total_{0};
    std::atomic<uint64_t> tts_frames_dropped_total_{0};

    std::chrono::steady_clock::time_point last_rtp_rx_time_;
    std::chrono::steady_clock::time_point last_rtp_tx_time_;
    std::chrono::steady_clock::time_point started_at_;
    SessionState state_{SessionState::Created};
    std::string stop_reason_{"running"};
    bool first_rtp_seen_{false};
    bool playout_started_{false};
    bool last_received_seq_valid_{false};
    uint16_t last_received_seq_{0};
    bool last_played_seq_valid_{false};
    uint16_t last_played_seq_{0};
    bool has_prev_arrival_{false};
    uint32_t prev_rtp_timestamp_{0};
    std::chrono::steady_clock::time_point prev_arrival_time_{};
    std::chrono::steady_clock::time_point last_rtcp_report_sent_at_{};
    double interarrival_jitter_ts_units_{0.0};
};

using RtpSessionPtr = std::shared_ptr<RtpSession>;

}  // namespace voice_gateway
