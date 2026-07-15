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
    // Loopback of caller audio back to the caller. MUST be false for a voice
    // agent — otherwise, whenever the TTS queue underruns, buffered caller audio
    // is spliced into the agent's speech (heard as echo/buzz/fragments, VG-07).
    // Production (asterisk_adapter.py) already sets this false explicitly; the
    // default is false so no caller/test path accidentally echoes.
    bool echo_enabled{false};
    std::size_t tts_max_queue_frames{400};
    // When the TTS queue underruns WHILE the agent is mid-utterance (a chunk was
    // sent within this window but the next hasn't arrived yet), emit µ-law
    // silence frames to keep the 20 ms RTP cadence continuous instead of leaving
    // a hole the far end renders as a gap/buzz (VG-06). Measured from the last
    // TTS frame sent, so a short trailing silence follows each burst and then TX
    // goes idle. 0 disables. Telephony-standard continuity; safe with Asterisk.
    int tts_underrun_fill_ms{500};
    // When set, every received RTP audio frame (G.711 µ-law, 160 bytes) is
    // base64-encoded and POSTed to this URL as JSON:
    //   {"session_id":"...","pcmu_base64":"...","codec":"pcmu"}
    // The backend uses this to feed caller audio into the STT pipeline.
    // Optional — if empty, received audio is only used for echo/jitter buffer.
    std::string audio_callback_url;
    // Maximum number of audio frames to batch into a single callback POST.
    // 1 = one POST per 20 ms frame (lowest latency). Default: 1.
    int audio_callback_batch_frames{1};
    // When true, the receiver locks onto the (source IP, source port, SSRC) of
    // the first accepted RTP packet and drops any later packet that does not
    // match — closing off-path audio injection / session-keepalive attacks
    // (VG-08). Default OFF: today the gateway binds to a trusted localhost/
    // Asterisk peer, and strict pinning could drop a legitimate mid-call source
    // change. Enable before exposing RTP to any untrusted network.
    bool enforce_rtp_source{false};
    // VG-01 (fixes S1: garbled spoken emails/phone digits). When true, caller
    // audio is delivered to the STT callback in RTP SEQUENCE order via a small
    // reorder window, instead of raw network-arrival order. Adds up to
    // stt_reorder_window_frames * 20 ms of transcription latency. Default OFF —
    // enable per-call after validating on a test call, since it is a real
    // latency<->correctness tradeoff.
    bool stt_reorder_enabled{false};
    // Reorder window depth in 20 ms frames (3 = 60 ms). Steady-state STT latency
    // added ~= this * 20 ms; also the max out-of-order distance corrected.
    int stt_reorder_window_frames{3};
    // Max time a frame may wait in the reorder window before being emitted even
    // if the window is not full — flushes the tail of an utterance during the
    // caller's silence (e.g. the final digits of a phone number). Must exceed
    // the steady-state window hold (window_frames * 20 ms) to avoid early emit.
    int stt_reorder_hold_ms{80};
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
    void clear_all_jitter_slots_locked();
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
    // Bumped every time the TTS queue is cleared (barge-in / interrupt / replace
    // / stop). The transmitter captures this when it dequeues a frame and drops
    // that frame if the epoch changed before it is sent, so a frame already
    // popped at barge-in time is not spoken (VG-24).
    uint64_t tts_generation_{0};
    std::string tts_last_stop_reason_{"none"};

    // Latches on the first start() call. RtpSession is single-use (its worker
    // std::threads are one-shot); a second start() would assign over joinable
    // thread members and std::terminate. The registry always constructs a fresh
    // session, so this only guards misuse of the public API (VG-32).
    std::atomic<bool> ever_started_{false};
    std::atomic<bool> running_{false};
    std::atomic<bool> rx_healthy_{true};
    std::atomic<bool> tx_healthy_{true};
    // True once the session terminated via a failure (socket_error /
    // internal_error) rather than a clean stop. Lets healthy() report a failed
    // session as unhealthy instead of masking it behind running_==false (VG-20).
    std::atomic<bool> ended_in_failure_{false};

    // Latches the join+close teardown epilogue to a SINGLE external caller.
    // Separate from running_ because the running_ winner may be a worker thread
    // self-stopping (which must never run the epilogue: it cannot join itself
    // and could touch this object after ~RtpSession frees it). The epilogue is
    // instead claimed once by whichever EXTERNAL caller (stop_session, an HTTP
    // handler, the reaper, or ~RtpSession) reaches it first; later external
    // callers observe true and skip. This also serializes the concurrent direct
    // RtpSession::stop() callers exercised by the concurrency stress test.
    std::atomic<bool> teardown_started_{false};

    std::thread receiver_thread_;
    std::thread transmitter_thread_;
    std::thread watchdog_thread_;

    // Atomic so the stop epilogue can close each fd exactly once via
    // exchange(-1); prevents a double-close of an fd number the OS may have
    // recycled to an unrelated live session's socket. See request_stop().
    std::atomic<int> rx_socket_{-1};
    std::atomic<int> tx_socket_{-1};

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
    // Time the last real TTS frame was sent. Drives the VG-06 underrun silence
    // fill: silence is emitted only within tts_underrun_fill_ms of this instant,
    // so gaps between chunks of one utterance are bridged but TX still idles
    // shortly after the agent stops speaking. Default-constructed (epoch) so fill
    // is inactive until the first TTS frame is sent.
    std::chrono::steady_clock::time_point last_tts_sent_at_{};
    std::chrono::steady_clock::time_point started_at_;
    SessionState state_{SessionState::Created};
    std::string stop_reason_{"running"};
    // Two-phase startup gate (finding #3). Workers park at loop entry until
    // start() has successfully constructed ALL three threads and sets this true.
    // If a later thread construction throws, start() leaves it false and clears
    // running_, so the parked workers exit without processing any packet — no
    // half-started session with a live receiver already firing callbacks.
    bool start_gate_committed_{false};
    bool first_rtp_seen_{false};
    bool playout_started_{false};
    bool last_received_seq_valid_{false};
    uint16_t last_received_seq_{0};
    bool last_played_seq_valid_{false};
    uint16_t last_played_seq_{0};
    bool has_prev_arrival_{false};
    // RTP source pinning (only consulted when config_.enforce_rtp_source). Locked
    // to the first accepted packet's source tuple + SSRC; later mismatches are
    // dropped as injected (VG-08).
    bool rtp_source_locked_{false};
    uint32_t locked_source_ip_{0};
    uint16_t locked_source_port_{0};
    uint32_t locked_ssrc_{0};
    uint32_t prev_rtp_timestamp_{0};
    std::chrono::steady_clock::time_point prev_arrival_time_{};
    std::chrono::steady_clock::time_point last_rtcp_report_sent_at_{};
    double interarrival_jitter_ts_units_{0.0};
};

using RtpSessionPtr = std::shared_ptr<RtpSession>;

}  // namespace voice_gateway
