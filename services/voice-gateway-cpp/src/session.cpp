#include "voice_gateway/session.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
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

// Per-packet RTP TX logging is extremely high frequency (~50 lines/sec/call)
// and floods journald under concurrent calls, so it is opt-in only. Set
// VOICE_GATEWAY_RTP_TX_DEBUG_LOG=1 in the environment to enable it; the
// value is read once at process start and cached, never per packet.
bool rtp_tx_debug_logging_enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("VOICE_GATEWAY_RTP_TX_DEBUG_LOG");
        return value != nullptr && std::string(value) == "1";
    }();
    return enabled;
}

// One buffered frame in the receiver's STT reorder window (VG-01), keyed on the
// EXTENDED (unwrapped) sequence number so ordering is a true total order across
// 16-bit wraps. Held only on the receiver thread, so no synchronization needed.
struct SttReorderEntry {
    int64_t ext_seq{0};
    std::chrono::steady_clock::time_point arrival;
    std::vector<uint8_t> payload;
};

// Outcome of classifying one received RTP packet for the STT path.
struct SttClassification {
    bool feed_stt{false};        // admit to the reorder window
    int64_t ext_seq{0};          // extended sequence (valid iff feed_stt)
    bool advanced{false};        // advanced the forward watermark -> refresh liveness
    bool restart_committed{false};  // a qualified restart (SSRC or jump) just committed
    bool probe_candidate{false};    // packet recorded as a probation member (droppable-but-bufferable)
    bool floor_rejected{false};     // rejected at/behind the STT emission floor
};

// Receiver-thread-local sequence tracker for the STT tap (Batch A, review
// findings #4/#5/#8/#10/#12). Unwraps 16-bit RTP sequence numbers into an
// extended (int64) space RFC3550-style, enforces a DUAL watermark — a hard STT
// emission floor plus a separate forward-progress watermark for liveness — and
// qualifies BOTH kinds of stream discontinuity via probation instead of
// trusting the first packet:
//   - an SSRC change (new stream / injected packet), and
//   - an RFC3550 large sequence jump on the SAME SSRC (bad_seq probation).
// The first packet of either discontinuity is never fed and never advances the
// forward watermark — one spoofed/anomalous packet can no longer push
// highest_received_ext far ahead and freeze liveness for every legitimate
// packet behind it (review #4). All state is touched only by the receiver
// thread, so it needs no lock.
struct SttSequencer {
    static constexpr int64_t kSeqMod = 0x10000;      // 2^16
    static constexpr uint16_t kMaxDropout = 3000;    // RFC3550
    static constexpr uint16_t kMaxMisorder = 100;    // RFC3550
    static constexpr int kMinSequential = 2;         // RFC3550 probation

    bool initialized{false};
    uint32_t ssrc{0};
    uint16_t max_seq{0};
    int64_t cycles{0};
    int64_t highest_received_ext{-1};  // forward-progress watermark (liveness)
    int64_t stt_emitted_high{-1};      // hard floor: reject ext_seq <= this

    // SSRC-change probation.
    bool probing{false};
    uint32_t probe_ssrc{0};
    uint16_t probe_max_seq{0};
    int probe_count{0};

    // Same-SSRC large-jump (bad_seq) probation, RFC3550 style: the jump commits
    // only when the immediately following packet continues it sequentially.
    bool jump_probing{false};
    uint16_t jump_next_seq{0};
    int jump_count{0};

    void init_stream(const uint32_t s, const uint16_t seq) {
        ssrc = s;
        max_seq = seq;
        cycles = 0;
        highest_received_ext = seq;  // first packet is forward
        stt_emitted_high = -1;       // advanced only on emit
        probing = false;
        probe_count = 0;
        jump_probing = false;
        jump_count = 0;
        initialized = true;
    }

    SttClassification classify(const uint32_t pkt_ssrc, const uint16_t seq) {
        SttClassification r;
        if (!initialized) {
            init_stream(pkt_ssrc, seq);
            r.feed_stt = true;
            r.ext_seq = seq;
            r.advanced = true;
            return r;
        }

        if (pkt_ssrc != ssrc) {
            // A different SSRC: qualify a restart via probation rather than
            // trusting the first packet (which could be injected / a stale
            // reflection). Only kMinSequential in-order packets commit the reset.
            if (probing && pkt_ssrc == probe_ssrc &&
                seq == static_cast<uint16_t>(probe_max_seq + 1)) {
                probe_max_seq = seq;
                ++probe_count;
                if (probe_count >= kMinSequential) {
                    init_stream(pkt_ssrc, seq);
                    r.feed_stt = true;
                    r.ext_seq = seq;
                    r.advanced = true;
                    r.restart_committed = true;
                    return r;
                }
            } else {
                probing = true;
                probe_ssrc = pkt_ssrc;
                probe_max_seq = seq;
                probe_count = 1;
            }
            r.probe_candidate = true;  // buffered by the receiver, replayed on commit
            return r;                  // unqualified new-SSRC packet: not fed
        }

        // Same SSRC as the accepted stream: any candidate SSRC probation is stale.
        probing = false;
        probe_count = 0;

        const uint16_t udelta = static_cast<uint16_t>(seq - max_seq);
        int64_t ext;
        if (udelta < kMaxDropout) {
            // In order (with a permissible small gap). Detect the wrap. A real
            // in-order packet also cancels any pending jump probation — a lone
            // spoofed jump packet cannot survive interleaved legitimate audio.
            jump_probing = false;
            jump_count = 0;
            if (seq < max_seq) {
                cycles += kSeqMod;
            }
            max_seq = seq;
            ext = cycles + seq;
        } else if (udelta <= kSeqMod - kMaxMisorder) {
            // Very large forward jump for the SAME SSRC. NEVER feed or advance
            // on the first such packet (review #4): the old code advanced the
            // forward watermark to the jump, after which every legitimate
            // packet was "not forward" and liveness froze until the watchdog
            // killed the call. RFC3550 bad_seq probation instead: remember the
            // jump and commit only if the next packet continues it sequentially
            // (a genuine sender resync), treating the commit as a restart.
            if (jump_probing && seq == jump_next_seq) {
                ++jump_count;
                if (jump_count >= kMinSequential) {
                    init_stream(pkt_ssrc, seq);
                    r.feed_stt = true;
                    r.ext_seq = seq;
                    r.advanced = true;
                    r.restart_committed = true;
                    return r;
                }
                jump_next_seq = static_cast<uint16_t>(seq + 1);
            } else {
                jump_probing = true;
                jump_next_seq = static_cast<uint16_t>(seq + 1);
                jump_count = 1;
            }
            r.probe_candidate = true;
            return r;  // unqualified jump packet: not fed, does NOT advance
        } else {
            // Small backward step: a reordered or duplicate packet. If seq is
            // ABOVE max_seq it is a delayed packet from the PREVIOUS cycle
            // (e.g. 65535 arriving after we already wrapped to 0).
            ext = (seq > max_seq) ? (cycles - kSeqMod + seq) : (cycles + seq);
        }

        // Hard floor: never hand STT a sequence at or behind one already emitted
        // (that is exactly what produced "...,100,99"). NO lateness allowance
        // below the emission watermark (#5).
        if (ext <= stt_emitted_high) {
            r.floor_rejected = true;
            return r;  // feed_stt=false
        }
        r.feed_stt = true;
        r.ext_seq = ext;
        if (ext > highest_received_ext) {
            highest_received_ext = ext;  // forward progress -> refresh liveness
            r.advanced = true;
        }
        return r;
    }
};

// Identifies, per thread, which RtpSession this thread is a worker loop for.
// Set as the first statement of each worker loop (before any path can reach
// request_stop), so a worker stopping its OWN session is detected WITHOUT
// reading the std::thread member objects — reading their get_id() would itself
// race an external teardown's join() on the same objects. External callers
// (HTTP handlers, the reaper, ~RtpSession) never run a worker loop, so their
// t_owning_session stays nullptr and they take the full-teardown path.
thread_local RtpSession* t_owning_session = nullptr;

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
    // Single-use guard: the exchange winner is the only caller that proceeds.
    // Concurrent double-start is serialized here, and a start-stop-start is
    // rejected outright, so worker threads are never re-created over joinable
    // members (VG-32).
    if (ever_started_.exchange(true)) {
        error = "RtpSession is single-use; construct a new instance to start again";
        return false;
    }

    // Serialize the whole prepare/commit against any concurrent teardown
    // epilogue (review #7): a stop() racing this start() can no longer scan the
    // thread members mid-assignment, miss them, and leave three joinable
    // threads to std::terminate the process at destruction. The epilogue takes
    // the same mutex, so it runs strictly before or strictly after start().
    std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);
    if (teardown_started_.load()) {
        error = "session was stopped before start";
        return false;
    }

    // Marks the session honestly dead when startup fails partway: not running,
    // Failed state, real stop_reason, unhealthy — instead of a corpse that
    // still reports Starting/"running"/healthy (review: misleading state).
    const auto fail_start = [this, &error](const std::string& why) {
        error = why;
        std::lock_guard<std::mutex> lock(mutex_);
        ended_in_failure_.store(true);
        stop_reason_ = "start_failed";
        transition_state_locked(SessionState::Failed);
        return false;
    };

    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (running_.load()) {
            error = "session already running";
            return false;
        }
    }

    if (!is_power_of_two(config_.jitter_buffer_capacity_frames) || config_.jitter_buffer_capacity_frames == 0) {
        return fail_start("jitter_buffer_capacity_frames must be a non-zero power of two");
    }

    if (config_.jitter_buffer_prefetch_frames == 0 || config_.jitter_buffer_prefetch_frames > config_.jitter_buffer_capacity_frames) {
        return fail_start("jitter_buffer_prefetch_frames must be between 1 and jitter_buffer_capacity_frames");
    }

    if (config_.tts_max_queue_frames == 0) {
        return fail_start("tts_max_queue_frames must be >= 1");
    }

    bool listen_ip_ok = false;
    const sockaddr_in listen_addr = make_sockaddr(config_.listen_ip, config_.listen_port, listen_ip_ok);
    if (!listen_ip_ok) {
        return fail_start("invalid listen_ip");
    }

    bool remote_ip_ok = false;
    make_sockaddr(config_.remote_ip, config_.remote_port, remote_ip_ok);
    if (!remote_ip_ok) {
        return fail_start("invalid remote_ip");
    }

    // Sockets are std::atomic<int> (so request_stop can close each exactly once
    // via exchange). Every C socket syscall below must take a plain int via
    // .load(): passing the atomic to an unqualified call would add namespace
    // std to ADL and resolve e.g. bind() to std::bind.
    const int rx_fd = socket(AF_INET, SOCK_DGRAM, 0);
    rx_socket_.store(rx_fd);
    if (rx_fd < 0) {
        return fail_start(std::string("failed to create RX socket: ") + std::strerror(errno));
    }

    const int tx_fd = socket(AF_INET, SOCK_DGRAM, 0);
    tx_socket_.store(tx_fd);
    if (tx_fd < 0) {
        close(rx_fd);
        rx_socket_.store(-1);
        return fail_start(std::string("failed to create TX socket: ") + std::strerror(errno));
    }

    int reuse = 1;
    setsockopt(rx_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    // The RX timeout only needs to be small enough that recvfrom() wakes
    // promptly to observe running_==false during teardown. Bound it to
    // [50, 1000] ms and split into a NORMALIZED timeval (tv_usec always in
    // [0, 1e6)). The previous {0, tick*1000} produced tv_usec >= 1e6 for any
    // tick >= 1000 ms, which Linux rejects with EINVAL; that error was ignored,
    // leaving recvfrom() with no timeout so it blocked forever and stop()'s
    // join() of the receiver hung the session (VG-05). Also check the syscall.
    // With the STT reorder window enabled the idle wake is also what flushes an
    // aged reorder tail, so it must be comparable to the hold deadline — a 200ms+
    // wake would add up to a second of tail latency on the last frames of an
    // utterance (review: hold is not a true idle deadline). 20ms matches the RTP
    // frame cadence; without reorder, teardown responsiveness is the only need.
    const int recv_timeout_ms = config_.stt_reorder_enabled
                                    ? 20
                                    : std::clamp(config_.watchdog_tick_ms, 50, 1000);
    timeval read_timeout{};
    read_timeout.tv_sec = recv_timeout_ms / 1000;
    read_timeout.tv_usec = static_cast<suseconds_t>((recv_timeout_ms % 1000) * 1000);
    if (setsockopt(rx_fd, SOL_SOCKET, SO_RCVTIMEO, &read_timeout, sizeof(read_timeout)) != 0) {
        close(rx_fd);
        close(tx_fd);
        rx_socket_.store(-1);
        tx_socket_.store(-1);
        return fail_start(std::string("failed to set RX socket timeout: ") + std::strerror(errno));
    }

    if (bind(rx_fd, reinterpret_cast<const sockaddr*>(&listen_addr), sizeof(listen_addr)) < 0) {
        close(rx_fd);
        close(tx_fd);
        rx_socket_.store(-1);
        tx_socket_.store(-1);
        return fail_start(std::string("failed to bind RX socket: ") + std::strerror(errno));
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

        reset_jitter_buffer_locked();
        tts_queue_.clear();
        tts_segments_.clear();
        next_tts_segment_id_ = 1;
        tts_last_stop_reason_ = "none";
        tx_tts_send_inflight_ = false;
        tx_inflight_generation_ = 0;
        tts_current_utterance_id_.clear();
        tts_retired_utterance_id_.clear();
        tts_last_chunk_seq_ = -1;
        first_rtp_seen_ = false;
        playout_started_ = false;
        last_received_seq_valid_ = false;
        last_played_seq_valid_ = false;
        has_prev_arrival_ = false;
        rtp_source_locked_ = false;
        locked_source_ip_ = 0;
        locked_source_port_ = 0;
        prev_rtp_timestamp_ = 0;
        interarrival_jitter_ts_units_ = 0.0;
        last_rtcp_report_sent_at_ = started_at_;
    }

    // Two-phase startup (#3). Construct all three workers first; each parks at
    // its loop entry on the start gate and does NO work yet. If any construction
    // throws (e.g. thread exhaustion), abort cleanly: clear running_ so the
    // parked workers exit, join whatever was constructed, close the sockets, and
    // return an error — never a half-started session with a live receiver, and
    // never a throw propagating out of start().
    try {
        receiver_thread_ = std::thread(&RtpSession::receiver_loop, this);
        transmitter_thread_ = std::thread(&RtpSession::transmitter_loop, this);
        watchdog_thread_ = std::thread(&RtpSession::watchdog_loop, this);
    } catch (...) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            running_.store(false);  // parked workers will observe this and exit
        }
        queue_cv_.notify_all();
        if (receiver_thread_.joinable()) {
            receiver_thread_.join();
        }
        if (transmitter_thread_.joinable()) {
            transmitter_thread_.join();
        }
        if (watchdog_thread_.joinable()) {
            watchdog_thread_.join();
        }
        const int rx_fd_abort = rx_socket_.exchange(-1);
        if (rx_fd_abort >= 0) {
            shutdown(rx_fd_abort, SHUT_RDWR);
            close(rx_fd_abort);
        }
        const int tx_fd_abort = tx_socket_.exchange(-1);
        if (tx_fd_abort >= 0) {
            shutdown(tx_fd_abort, SHUT_RDWR);
            close(tx_fd_abort);
        }
        return fail_start("failed to start session worker threads");
    }

    // Commit: release the gate so the parked workers begin processing.
    {
        std::lock_guard<std::mutex> lock(mutex_);
        start_gate_committed_ = true;
    }
    queue_cv_.notify_all();

    return true;
}

void RtpSession::stop(const std::string& reason) {
    request_stop(stop_reason_or_default(reason), false);
}

void RtpSession::stop_async(const std::string& reason) {
    // Signal-only: flips running_/state and wakes workers, but neither claims
    // nor waits on the teardown epilogue (review #14 bulk shutdown).
    request_stop(stop_reason_or_default(reason), false, false);
}

bool RtpSession::running() const {
    return running_.load();
}

bool RtpSession::healthy() const {
    if (!running_.load()) {
        // A cleanly stopped session is "healthy" (it did its job and left); a
        // session that DIED (socket_error/internal_error) must not masquerade as
        // healthy (VG-20).
        return !ended_in_failure_.load();
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
        snap.jitter_buffer_depth_frames = jitter_buffer_size_;
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
    snap.stt_frames_emitted_total = stt_frames_emitted_total_.load();
    snap.stt_floor_dropped_total = stt_floor_dropped_total_.load();
    snap.stt_probation_dropped_total = stt_probation_dropped_total_.load();
    snap.stt_restarts_committed_total = stt_restarts_committed_total_.load();
    snap.tts_chunks_rejected_stale_total = tts_chunks_rejected_stale_total_.load();

    return snap;
}

bool RtpSession::enqueue_tts_ulaw(
    const std::vector<uint8_t>& ulaw_audio,
    const bool clear_existing,
    std::size_t& queued_frames,
    std::string& error,
    const std::string& utterance_id,
    const int64_t chunk_seq) {
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

    // VG-13 idempotency gate — only when the backend stamps an utterance id.
    // clear_existing ran first, so a replacement submission retires the PREVIOUS
    // utterance and then admits its own id here.
    if (!utterance_id.empty()) {
        if (utterance_id == tts_retired_utterance_id_) {
            // A chunk of an utterance that was already interrupted/replaced —
            // the delayed-delivery race that used to re-speak pre-barge-in audio.
            tts_chunks_rejected_stale_total_.fetch_add(1);
            error = "utterance_interrupted";
            return false;
        }
        if (utterance_id != tts_current_utterance_id_) {
            tts_current_utterance_id_ = utterance_id;
            tts_last_chunk_seq_ = -1;
        }
        if (chunk_seq >= 0) {
            if (chunk_seq <= tts_last_chunk_seq_) {
                tts_chunks_rejected_stale_total_.fetch_add(1);
                error = "stale_or_duplicate_chunk";
                return false;
            }
            tts_last_chunk_seq_ = chunk_seq;
        }
    }

    const std::size_t frame_count = ulaw_audio.size() / static_cast<std::size_t>(kPcmuTimestampStep);
    const std::size_t cap = config_.tts_max_queue_frames;

    // If this single submission alone exceeds the queue capacity, only its LAST
    // `cap` frames could ever survive the drop-oldest trim below. Skip the
    // leading frames instead of copying them just to immediately evict them —
    // this avoids a transient allocation/lock-hold spike on an oversized body
    // and makes the accounting honest. The leading (skipped) frames are the
    // START of the utterance and are genuinely not spoken; that is reported to
    // the caller via queued_frames rather than masked (VG-25).
    const std::size_t skip_leading = (frame_count > cap) ? (frame_count - cap) : 0;
    const std::size_t submit_count = frame_count - skip_leading;

    const uint32_t segment_id = next_tts_segment_id_++;
    tts_segments_[segment_id] = TtsSegmentState{submit_count, false};
    tts_segments_started_total_.fetch_add(1);
    tts_frames_enqueued_total_.fetch_add(submit_count);

    for (std::size_t i = skip_leading; i < frame_count; ++i) {
        const std::size_t offset = i * static_cast<std::size_t>(kPcmuTimestampStep);
        QueuedTtsFrame frame{};
        frame.segment_id = segment_id;
        std::memcpy(
            frame.payload.data(),
            ulaw_audio.data() + static_cast<std::ptrdiff_t>(offset),
            static_cast<std::size_t>(kPcmuTimestampStep));
        tts_queue_.push_back(std::move(frame));
    }

    // Trim to capacity by dropping the oldest queued frames. Count how many of
    // THIS submission are evicted so queued_frames reflects only what actually
    // remains to be played — never the raw submitted count.
    std::size_t dropped_from_this = 0;
    while (tts_queue_.size() > cap) {
        const auto dropped = tts_queue_.front();
        tts_queue_.pop_front();
        if (dropped.segment_id == segment_id) {
            ++dropped_from_this;
        }
        mark_tts_frame_dropped_locked(dropped.segment_id);
    }

    queued_frames = submit_count - dropped_from_this;
    tts_last_stop_reason_ = "running";
    // notify_all, not notify_one: the watchdog waits on the same cv, and a
    // notify_one it consumed would be a lost wakeup for the transmitter.
    queue_cv_.notify_all();
    return true;
}

bool RtpSession::interrupt_tts(const std::string& reason, std::size_t& dropped_frames, std::size_t& interrupted_segments) {
    std::unique_lock<std::mutex> lock(mutex_);
    dropped_frames = tts_queue_.size();
    interrupted_segments = tts_segments_.size();
    clear_tts_queue_locked(reason.empty() ? "barge_in" : reason);
    queue_cv_.notify_all();  // watchdog shares the cv; see enqueue_tts_ulaw

    // VG-24 send-completion barrier: the transmitter re-checks the generation
    // under mutex_ before committing to a send, but the sendto() itself runs
    // outside the lock — so without this wait a frame that passed its check
    // just before the clear could still hit the wire AFTER this call returned.
    // Wait (bounded; the commit->send window is microseconds) until no frame
    // from a pre-clear generation is in that window. A frame enqueued AFTER the
    // clear carries the bumped generation and must not be waited on.
    queue_cv_.wait_for(lock, std::chrono::milliseconds(200), [this] {
        return !tx_tts_send_inflight_ || tx_inflight_generation_ >= tts_generation_;
    });
    return true;
}

void RtpSession::set_audio_callback(AudioCallback cb) {
    std::lock_guard<std::mutex> lock(audio_callback_mutex_);
    audio_callback_ = std::move(cb);
}

void RtpSession::set_audio_sink_finisher(std::function<void()> finisher) {
    std::lock_guard<std::mutex> lock(audio_callback_mutex_);
    audio_sink_finisher_ = std::move(finisher);
}

void RtpSession::fire_audio_callback(const std::vector<uint8_t>& pcmu_batch) {
    AudioCallback cb;
    {
        std::lock_guard<std::mutex> lock(audio_callback_mutex_);
        cb = audio_callback_;
    }
    if (cb) {
        try {
            cb(config_.session_id, pcmu_batch);
            stt_frames_emitted_total_.fetch_add(1);
        } catch (...) {
            // The callback base64-encodes and builds JSON on the RTP receiver
            // thread; a std::bad_alloc (or any other exception) must never escape
            // to the thread entry and std::terminate the whole gateway (VG-12).
            // Drop this batch and keep receiving.
        }
    }
}

void RtpSession::receiver_loop() {
    t_owning_session = this;
    try {
    // Park until start() commits all three workers (or aborts) — two-phase
    // startup (#3). Until then this thread processes no RTP and fires no callback.
    {
        std::unique_lock<std::mutex> lock(mutex_);
        queue_cv_.wait(lock, [this] { return start_gate_committed_ || !running_.load(); });
    }
    if (!running_.load()) {
        return;
    }
    // STT reorder window (VG-01 / Batch A). Receiver-thread-local: only this loop
    // touches the sequencer + deque, so no lock, no contention. The deque is keyed
    // on the EXTENDED sequence (int64) for a true total order, and bounded to
    // window+1 entries because every insert is followed by a drain.
    const bool stt_reorder = config_.stt_reorder_enabled;
    const std::size_t reorder_window =
        std::max<std::size_t>(1, static_cast<std::size_t>(std::max(1, config_.stt_reorder_window_frames)));
    const int64_t reorder_hold_ms = std::max(0, config_.stt_reorder_hold_ms);
    std::deque<SttReorderEntry> reorder;
    SttSequencer stt_seq;
    // Payloads of the CURRENT probation chain (SSRC change or large jump),
    // buffered so a committed restart replays them and loses no audio (review:
    // restart lost the first probation frame). Receiver-thread-local. A chain
    // holds at most kMinSequential-1 packets before it commits; the cap is a
    // defensive bound, never reached by a well-formed chain.
    std::vector<std::vector<uint8_t>> probe_payloads;
    constexpr std::size_t kMaxProbePayloads = 4;

    const auto reorder_insert = [&](const int64_t ext_seq, const std::vector<uint8_t>& payload,
                                    const std::chrono::steady_clock::time_point now) {
        auto it = reorder.begin();
        while (it != reorder.end() && ext_seq > it->ext_seq) {
            ++it;
        }
        if (it != reorder.end() && it->ext_seq == ext_seq) {
            return;  // duplicate already buffered (exact extended-seq match)
        }
        SttReorderEntry entry;
        entry.ext_seq = ext_seq;
        entry.arrival = now;
        entry.payload = payload;
        reorder.insert(it, std::move(entry));
    };

    // Emit (in ascending extended-sequence order) every frame past the window
    // depth or older than the hold deadline; flush_all drains everything. Each
    // emit advances the hard emission floor so a later, lower packet is rejected
    // by classify() instead of being emitted out of order (#5).
    const auto reorder_drain = [&](const std::chrono::steady_clock::time_point now, const bool flush_all) {
        while (!reorder.empty()) {
            const bool over_window = reorder.size() > reorder_window;
            const bool aged = std::chrono::duration_cast<std::chrono::milliseconds>(
                                  now - reorder.front().arrival).count() >= reorder_hold_ms;
            if (!flush_all && !over_window && !aged) {
                break;
            }
            stt_seq.stt_emitted_high = reorder.front().ext_seq;
            const std::vector<uint8_t> payload = std::move(reorder.front().payload);
            reorder.pop_front();
            fire_audio_callback(payload);
        }
    };

    while (running_.load()) {
        uint8_t buffer[2048]{};
        sockaddr_in from{};
        socklen_t from_len = sizeof(from);

        const ssize_t n = recvfrom(
            rx_socket_.load(),
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
                // Idle wake: flush any reorder-window tail whose hold deadline has
                // passed, so the last frames of an utterance reach STT during the
                // caller's silence rather than waiting for the next packet (VG-01).
                if (stt_reorder) {
                    reorder_drain(std::chrono::steady_clock::now(), false);
                }
                continue;
            }
            rx_healthy_.store(false);
            request_stop("socket_error", false);
            break;
        }

        if (is_rtcp_packet(buffer, static_cast<std::size_t>(n))) {
            if (stt_reorder) {
                reorder_drain(std::chrono::steady_clock::now(), false);  // don't starve the tail (#12)
            }
            continue;
        }

        const auto parsed = RtpPacket::parse(buffer, static_cast<std::size_t>(n));
        if (!parsed.has_value() || parsed->payload_type != 0 ||
            parsed->payload.size() != static_cast<std::size_t>(kPcmuTimestampStep)) {
            invalid_packets_.fetch_add(1);
            if (stt_reorder) {
                reorder_drain(std::chrono::steady_clock::now(), false);  // honor the hold deadline (#12)
            }
            continue;
        }

        // Optional RTP source pinning (VG-08), OUTSIDE the lock (state is
        // receiver-local). Runs BEFORE classify() so an injected/foreign packet
        // can neither pollute the sequence tracker nor refresh liveness. Pins
        // the (IP, port) tuple ONLY: an SSRC change from the trusted tuple must
        // reach the sequencer's restart probation instead of being dropped here
        // (review #6 — pinning and probation were mutually exclusive before).
        if (config_.enforce_rtp_source) {
            const uint32_t src_ip = static_cast<uint32_t>(from.sin_addr.s_addr);
            const uint16_t src_port = ntohs(from.sin_port);
            if (!rtp_source_locked_) {
                rtp_source_locked_ = true;
                locked_source_ip_ = src_ip;
                locked_source_port_ = src_port;
            } else if (src_ip != locked_source_ip_ || src_port != locked_source_port_) {
                invalid_packets_.fetch_add(1);
                if (stt_reorder) {
                    reorder_drain(std::chrono::steady_clock::now(), false);
                }
                continue;
            }
        }

        packets_in_.fetch_add(1);
        bytes_in_.fetch_add(parsed->payload.size());

        // Classify for the STT tap: extended sequence + dual watermark + SSRC/
        // jump restart probation. Receiver-thread-local, so done OUTSIDE the lock.
        const auto cls = stt_seq.classify(parsed->ssrc, parsed->sequence_number);
        if (cls.floor_rejected) {
            stt_floor_dropped_total_.fetch_add(1);
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto arrival = std::chrono::steady_clock::now();

            // A qualified SSRC restart: reset the echo jitter ring + sequence
            // bookkeeping so the new stream is not rejected against the old one.
            if (cls.restart_committed) {
                reset_jitter_buffer_locked();
                last_played_seq_valid_ = false;
                last_received_seq_valid_ = false;
                has_prev_arrival_ = false;
            }

            // Liveness / state / interarrival-jitter update ONLY on forward STT
            // progress (a packet that advanced the received watermark). Stale,
            // backward, duplicate or injected packets never refresh liveness now
            // (#8/#10) nor pollute the jitter estimate (#31).
            if (cls.advanced) {
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
            }

            // Echo jitter ring — only meaningful when echo is enabled (it is off
            // in production). Kept independent of the STT tap, so a jitter dup/
            // reject can never drop a valid STT frame (#5 decoupling).
            if (config_.echo_enabled) {
                if (last_played_seq_valid_ && sequence_diff(parsed->sequence_number, last_played_seq_) <= 0) {
                    jitter_buffer_late_drops_.fetch_add(1);
                    dropped_packets_.fetch_add(1);
                } else {
                    QueuedRtpFrame frame{};
                    frame.sequence_number = parsed->sequence_number;
                    frame.timestamp = parsed->timestamp;
                    frame.payload_size = parsed->payload.size();
                    std::memcpy(frame.payload.data(), parsed->payload.data(), frame.payload_size);
                    if (insert_jitter_frame_locked(frame)) {
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
                        if (playout_started_) {
                            const std::size_t target_depth = std::min(kDefaultJitterTargetDepthFrames, config_.jitter_buffer_capacity_frames);
                            while (jitter_buffer_size_ > target_depth) {
                                drop_oldest_jitter_frame_locked();
                            }
                        }
                    }
                }
            }
        }

        queue_cv_.notify_all();

        // Deliver to STT OUTSIDE the mutex. With stt_reorder, feed the extended-
        // sequence reorder window (spoken order, VG-01); otherwise arrival order
        // (legacy default — deliberately byte-identical behavior when the flag
        // is off). Draining every iteration honors the hold deadline.
        if (stt_reorder) {
            const auto now = std::chrono::steady_clock::now();
            if (cls.restart_committed) {
                // A qualified restart: the buffered entries belong to the RETIRED
                // epoch. Flush them in their own (ascending old-extended) order
                // WITHOUT letting them move the fresh epoch's emission floor —
                // otherwise the old tail (huge ext values) would flush after the
                // new stream started and poison the floor so every new-stream
                // packet was rejected (review #5).
                while (!reorder.empty()) {
                    const std::vector<uint8_t> payload = std::move(reorder.front().payload);
                    reorder.pop_front();
                    fire_audio_callback(payload);
                }
                // Replay the probation packets (sequential by construction) so a
                // restart loses no audio, then floor the fresh epoch just below
                // the committing packet so a duplicate probe cannot re-emit.
                for (const auto& probe : probe_payloads) {
                    fire_audio_callback(probe);
                }
                probe_payloads.clear();
                stt_seq.stt_emitted_high = cls.ext_seq - 1;
                stt_restarts_committed_total_.fetch_add(1);
            } else if (cls.probe_candidate) {
                // First packet(s) of an unqualified discontinuity: hold the
                // payload for replay-on-commit. A NEW chain (length 1) replaces
                // any previous candidate buffer.
                const int chain_len = stt_seq.probing ? stt_seq.probe_count
                                                      : (stt_seq.jump_probing ? stt_seq.jump_count : 0);
                if (chain_len == 1) {
                    stt_probation_dropped_total_.fetch_add(probe_payloads.size());
                    probe_payloads.clear();
                }
                if (probe_payloads.size() < kMaxProbePayloads) {
                    probe_payloads.push_back(parsed->payload);
                } else {
                    stt_probation_dropped_total_.fetch_add(1);
                }
            } else if (!stt_seq.probing && !stt_seq.jump_probing && !probe_payloads.empty()) {
                // The accepted stream kept flowing: the candidate chain is stale.
                stt_probation_dropped_total_.fetch_add(probe_payloads.size());
                probe_payloads.clear();
            }
            if (cls.feed_stt && !parsed->payload.empty()) {
                reorder_insert(cls.ext_seq, parsed->payload, now);
            }
            reorder_drain(now, false);
        } else if (!parsed->payload.empty()) {
            fire_audio_callback(parsed->payload);
        }
    }
    // Drain whatever remains so the final frames of the call still reach STT.
    if (stt_reorder) {
        reorder_drain(std::chrono::steady_clock::now(), true);
    }
    // Batch E: flush the partially-batched STT tail and drain the delivery sink
    // (bounded deadline inside the finisher). Runs on this thread AFTER the last
    // fire_audio_callback, so it races nothing; its runtime extends the stop()
    // join by at most the finisher's own bound.
    {
        std::function<void()> finisher;
        {
            std::lock_guard<std::mutex> lk(audio_callback_mutex_);
            finisher = audio_sink_finisher_;
        }
        if (finisher) {
            try {
                finisher();
            } catch (...) {
                // A sink-drain failure must never break teardown.
            }
        }
    }
    } catch (...) {
        // Nothing in the loop is expected to throw (the callback is already
        // guarded; RtpPacket::parse's allocation is the only other source), but
        // if anything does, fail the session cleanly rather than let it reach
        // the thread entry and std::terminate the gateway (VG-12).
        rx_healthy_.store(false);
        request_stop("internal_error", false);
    }
}

void RtpSession::transmitter_loop() {
    t_owning_session = this;
    try {
    // Park until start() commits (or aborts) — two-phase startup (#3).
    {
        std::unique_lock<std::mutex> lock(mutex_);
        queue_cv_.wait(lock, [this] { return start_gate_committed_ || !running_.load(); });
    }
    if (!running_.load()) {
        return;
    }
    bool remote_ok = false;
    const sockaddr_in remote_addr = make_sockaddr(config_.remote_ip, config_.remote_port, remote_ok);
    if (!remote_ok) {
        tx_healthy_.store(false);
        request_stop("internal_error", false);
        return;
    }

    auto next_send_time = std::chrono::steady_clock::now();

    while (running_.load()) {
        std::array<uint8_t, kPcmuTimestampStep> payload{};
        std::size_t payload_size = 0;
        uint32_t tts_segment_id = 0;
        bool sending_tts = false;
        uint64_t tts_gen = 0;

        {
            std::unique_lock<std::mutex> lock(mutex_);

            const auto has_output_ready = [this] {
                if (!running_.load()) {
                    return true;
                }
                if (!tts_queue_.empty()) {
                    return true;
                }
                if (!config_.echo_enabled) {
                    return false;
                }
                if (jitter_buffer_size_ == 0) {
                    return false;
                }
                if (!config_.jitter_buffer_enabled) {
                    return true;
                }
                if (playout_started_) {
                    return true;
                }
                return jitter_buffer_size_ >= config_.jitter_buffer_prefetch_frames;
            };

            // Within the underrun-fill window we cannot block indefinitely: we
            // must wake at the next 20 ms boundary to emit a silence frame that
            // bridges the gap between TTS chunks (VG-06). Outside the window,
            // block until there is real audio to send.
            const bool within_fill_window =
                config_.tts_underrun_fill_ms > 0 &&
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - last_tts_sent_at_).count() < config_.tts_underrun_fill_ms;

            if (within_fill_window) {
                queue_cv_.wait_until(lock, next_send_time, has_output_ready);
            } else {
                queue_cv_.wait(lock, has_output_ready);
            }

            if (!running_.load()) {
                break;
            }
            if (!tts_queue_.empty()) {
                QueuedTtsFrame tts_frame = std::move(tts_queue_.front());
                tts_queue_.pop_front();
                payload = tts_frame.payload;
                payload_size = static_cast<std::size_t>(kPcmuTimestampStep);
                tts_segment_id = tts_frame.segment_id;
                sending_tts = true;
                tts_gen = tts_generation_;
            } else if (config_.echo_enabled && jitter_buffer_size_ > 0) {
                if (!playout_started_) {
                    playout_started_ = true;
                    if (state_ == SessionState::Buffering || state_ == SessionState::Starting || state_ == SessionState::Degraded) {
                        transition_state_locked(SessionState::Active);
                    }
                }

                QueuedRtpFrame frame{};
                if (!pop_next_jitter_frame_locked(frame)) {
                    continue;
                }
                last_played_seq_valid_ = true;
                last_played_seq_ = frame.sequence_number;
                payload = frame.payload;
                payload_size = frame.payload_size;
            } else {
                // Nothing queued. If we are still within the underrun-fill window
                // (agent mid-utterance), emit one µ-law silence frame to hold the
                // RTP cadence; otherwise idle until real audio arrives (VG-06).
                const bool fill_now =
                    config_.tts_underrun_fill_ms > 0 &&
                    std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now() - last_tts_sent_at_).count() < config_.tts_underrun_fill_ms;
                if (!fill_now) {
                    continue;
                }
                payload.fill(static_cast<uint8_t>(0xFF));  // PCMU digital silence
                payload_size = static_cast<std::size_t>(kPcmuTimestampStep);
            }
        }

        if (payload_size == 0) {
            continue;
        }

        const auto now = std::chrono::steady_clock::now();
        if (next_send_time < now) {
            next_send_time = now;
        }

        std::this_thread::sleep_until(next_send_time);

        RtpPacket outbound;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (sending_tts && tts_generation_ != tts_gen) {
                // A barge-in / interrupt / replace cleared the queue after this
                // frame was dequeued. Drop it rather than speak stale agent audio,
                // and do NOT consume a sequence number (VG-24).
                mark_tts_frame_dropped_locked(tts_segment_id);
                continue;
            }
            if (sending_tts) {
                // Commit-to-send window opens: interrupt_tts's barrier waits for
                // it to close before returning (VG-24 completion half).
                tx_tts_send_inflight_ = true;
                tx_inflight_generation_ = tts_gen;
            }
            outbound = sequencer_.next_packet({}, 0);
        }
        constexpr std::size_t kRtpHeaderSize = 12;
        std::array<uint8_t, kRtpHeaderSize + kPcmuTimestampStep> packet_bytes{};
        packet_bytes[0] = 0x80;
        packet_bytes[1] = static_cast<uint8_t>(outbound.payload_type & 0x7Fu);
        packet_bytes[2] = static_cast<uint8_t>((outbound.sequence_number >> 8) & 0xFFu);
        packet_bytes[3] = static_cast<uint8_t>(outbound.sequence_number & 0xFFu);
        packet_bytes[4] = static_cast<uint8_t>((outbound.timestamp >> 24) & 0xFFu);
        packet_bytes[5] = static_cast<uint8_t>((outbound.timestamp >> 16) & 0xFFu);
        packet_bytes[6] = static_cast<uint8_t>((outbound.timestamp >> 8) & 0xFFu);
        packet_bytes[7] = static_cast<uint8_t>(outbound.timestamp & 0xFFu);
        packet_bytes[8] = static_cast<uint8_t>((outbound.ssrc >> 24) & 0xFFu);
        packet_bytes[9] = static_cast<uint8_t>((outbound.ssrc >> 16) & 0xFFu);
        packet_bytes[10] = static_cast<uint8_t>((outbound.ssrc >> 8) & 0xFFu);
        packet_bytes[11] = static_cast<uint8_t>(outbound.ssrc & 0xFFu);
        std::memcpy(packet_bytes.data() + static_cast<std::ptrdiff_t>(kRtpHeaderSize), payload.data(), payload_size);
        const std::size_t packet_size = kRtpHeaderSize + payload_size;

        const ssize_t sent = sendto(
            tx_socket_.load(),
            packet_bytes.data(),
            packet_size,
            0,
            reinterpret_cast<const sockaddr*>(&remote_addr),
            sizeof(remote_addr));

        if (sent < 0) {
            if (sending_tts) {
                std::lock_guard<std::mutex> lock(mutex_);
                mark_tts_frame_dropped_locked(tts_segment_id);
                tx_tts_send_inflight_ = false;  // barrier window closed (failed send)
            }
            queue_cv_.notify_all();  // release a waiting interrupt_tts barrier
            if (!running_.load()) {
                break;
            }
            tx_healthy_.store(false);
            request_stop("socket_error", false);
            break;
        }

        packets_out_.fetch_add(1);
        bytes_out_.fetch_add(static_cast<std::size_t>(sent));

        {
            std::lock_guard<std::mutex> lock(mutex_);
            const auto tx_now = std::chrono::steady_clock::now();
            last_rtp_tx_time_ = tx_now;
            if (sending_tts) {
                last_tts_sent_at_ = tx_now;  // arms the VG-06 underrun silence fill
                mark_tts_frame_sent_locked(tts_segment_id);
                tx_tts_send_inflight_ = false;  // barrier window closed (sent)
            }
        }
        if (sending_tts) {
            queue_cv_.notify_all();  // release a waiting interrupt_tts barrier
        }

        maybe_send_rtcp_report(remote_addr, std::chrono::steady_clock::now());

        if (rtp_tx_debug_logging_enabled()) {
            std::cout << "event=rtp_tx"
                      << " session_id=" << config_.session_id
                      << " seq=" << outbound.sequence_number
                      << " ts=" << outbound.timestamp
                      << " ssrc=" << outbound.ssrc
                      << " mode=" << (sending_tts ? "tts" : "echo")
                      << " state=" << session_state_to_string(state())
                      << " packets_in=" << packets_in_.load()
                      << " packets_out=" << packets_out_.load()
                      << "\n";
        }

        next_send_time += std::chrono::milliseconds(config_.ptime_ms);
    }
    } catch (...) {
        // No exception is expected here, but contain any so it cannot reach the
        // thread entry and std::terminate the gateway (#2).
        tx_healthy_.store(false);
        request_stop("internal_error", false);
    }
}

void RtpSession::watchdog_loop() {
    t_owning_session = this;
    try {
    // Park until start() commits (or aborts) — two-phase startup (#3).
    {
        std::unique_lock<std::mutex> lock(mutex_);
        queue_cv_.wait(lock, [this] { return start_gate_committed_ || !running_.load(); });
    }
    if (!running_.load()) {
        return;
    }
    const int tick_ms = std::max(50, config_.watchdog_tick_ms);

    while (running_.load()) {
        // Interruptible tick: a stop() notifies queue_cv_, so the watchdog wakes
        // and exits immediately instead of sleeping out the remainder of its
        // tick — the largest single contributor to per-session teardown latency
        // (review #14; a 5000ms tick meant a 5s join). Uses the shared cv, which
        // is why every waker must notify_all: a notify_one could be consumed
        // here and starve the transmitter.
        {
            std::unique_lock<std::mutex> lock(mutex_);
            queue_cv_.wait_for(lock, std::chrono::milliseconds(tick_ms),
                               [this] { return !running_.load(); });
        }
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

                // Hard no-RTP timeout governs on active_no_rtp_timeout_ms.
                // hold_no_rtp_timeout_ms is intentionally NOT honored: there is
                // no hold/unhold signal to know a call is genuinely parked, and
                // its 45s default exceeds active, so the former `hold <= active`
                // branch was unreachable — it advertised a 45s tolerance while
                // calls actually ended at 8s (VG-33). Governing solely on active
                // keeps the real, tested behavior explicit. A longer parked-call
                // ceiling would require wiring an actual hold state first.
                if (elapsed_since_rx_ms >= config_.active_no_rtp_timeout_ms) {
                    timeout_reason = "no_rtp_timeout";
                }
            }
        }

        if (!timeout_reason.empty()) {
            request_stop(timeout_reason, true);
            break;
        }
    }
    } catch (...) {
        // Contain any exception so it cannot reach the thread entry and
        // std::terminate the gateway (#2).
        request_stop("internal_error", false);
    }
}

void RtpSession::clear_tts_queue_locked(const std::string& reason) {
    // Invalidate any TTS frame the transmitter has already dequeued but not yet
    // sent, so a barge-in/replace/stop cannot leak one stale frame (VG-24).
    ++tts_generation_;
    // Retire the active utterance (VG-13): any chunk still in flight over HTTP
    // for it will be rejected by enqueue_tts_ulaw instead of being spoken.
    if (!tts_current_utterance_id_.empty()) {
        tts_retired_utterance_id_ = tts_current_utterance_id_;
        tts_current_utterance_id_.clear();
        tts_last_chunk_seq_ = -1;
    }
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

void RtpSession::request_stop(const std::string& reason, const bool timeout_event, const bool run_epilogue) {
    // request_stop() is reachable from several threads: the session's own worker
    // loops (watchdog timeout, receiver/transmitter socket_error) and EXTERNAL
    // callers (POST /stop via stop_session, an HTTP handler, the registry reaper,
    // and ~RtpSession). Two responsibilities are split:
    //
    //  (a) Signal the stop + record state/reason: done ONCE by the caller that
    //      wins running_.exchange(false). Any caller (self or external) may win.
    //
    //  (b) The join+close epilogue: run ONLY by an EXTERNAL caller, never by a
    //      worker stopping its own session. A self-stopping worker that ran the
    //      epilogue would (i) have to join itself (impossible) and (ii) keep
    //      touching this object after ~RtpSession may have freed it once the last
    //      shared_ptr dropped — the exact use-after-free TSan caught. So a
    //      self-stop just flips running_ and returns; the worker then exits its
    //      loop. The epilogue is driven later by an external caller whose join()
    //      of the now-finished worker threads is the barrier that guarantees the
    //      object outlives every worker thread. Worker threads hold no shared_ptr
    //      to the session, so ~RtpSession never runs on a worker thread.
    const bool is_self = (t_owning_session == this);

    const bool was_running = running_.exchange(false);
    if (was_running) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (timeout_event) {
            timeout_events_total_.fetch_add(1);
        }
        clear_tts_queue_locked(reason);

        // The exchange winner is the first (and only) caller to reach the
        // transition block, so state_ is never Stopped here.
        const bool failed_state = (reason == "socket_error" || reason == "internal_error");
        if (failed_state) {
            ended_in_failure_.store(true);
            transition_state_locked(SessionState::Failed);
        }
        transition_state_locked(SessionState::Stopping);
        if (stop_reason_ == "running") {
            stop_reason_ = stop_reason_or_default(reason);
        }
        transition_state_locked(SessionState::Stopped);
    } else if (timeout_event) {
        // A losing caller still records the timeout metric to preserve the count.
        timeout_events_total_.fetch_add(1);
    }

    // Wake a transmitter blocked on the cv so it observes running_==false and
    // exits; safe from any caller (this object is still alive here — a worker
    // owns it, or an external caller holds a reference / is ~RtpSession itself).
    queue_cv_.notify_all();

    // Self-stop: never touch the thread objects or sockets; just return.
    if (is_self) {
        return;
    }

    // Signal-only callers (stop_async / bulk shutdown pass 1) stop here.
    if (!run_epilogue) {
        return;
    }

    // External caller: claim the epilogue latch so exactly ONE external caller
    // runs the join+close, even when several call stop() on the same session
    // concurrently (the stress test does). A LOSING caller does NOT return
    // early: it waits until the winner publishes completion, so every external
    // stop() return means "sockets closed, threads joined". This closes the
    // latch-loser race where SessionRegistry::stop_session released its
    // stopping_ (id/port) reservation while a direct racer's teardown was still
    // in flight, letting a same-port restart bind against the old socket.
    if (teardown_started_.exchange(true)) {
        std::unique_lock<std::mutex> lock(mutex_);
        queue_cv_.wait(lock, [this] { return teardown_done_; });
        return;
    }

    // Epilogue, serialized against start() (review #7). If a concurrent start()
    // is mid-construction, this blocks until its threads exist and its gate
    // decision is made, then joins them; without this ordering the joinable()
    // scan below could race the thread assignments and miss all three.
    std::lock_guard<std::mutex> lifecycle_lock(lifecycle_mutex_);

    // A stop() that latched BEFORE start() flipped running_ would have skipped
    // the signal block above (was_running == false), letting the session start
    // and run afterwards with the epilogue latch already spent — the workers
    // would then be joined here while still live, blocking forever. Re-assert
    // the stop now that start() can no longer be mid-flight: stop always wins.
    if (running_.exchange(false)) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            clear_tts_queue_locked(reason);
            transition_state_locked(SessionState::Stopping);
            if (stop_reason_ == "running") {
                stop_reason_ = stop_reason_or_default(reason);
            }
            transition_state_locked(SessionState::Stopped);
        }
        queue_cv_.notify_all();
    }

    // Join every worker thread BEFORE closing the sockets, so no thread is inside
    // recvfrom()/sendto() on an fd when it is closed (which would be UB per POSIX
    // and could let the fd be recycled under a blocked reader). The workers are
    // never blocked forever — recvfrom returns within SO_RCVTIMEO (bounded), the
    // transmitter woke on the cv above, the watchdog waits on the same cv — so
    // each join is bounded. The claiming caller's join() of each now-finished
    // worker thread is also the barrier that guarantees this object outlives
    // every worker thread (workers hold no shared_ptr, so ~RtpSession never
    // runs on one).
    if (receiver_thread_.joinable()) {
        receiver_thread_.join();
    }
    if (transmitter_thread_.joinable()) {
        transmitter_thread_.join();
    }
    if (watchdog_thread_.joinable()) {
        watchdog_thread_.join();
    }

    const int rx_fd = rx_socket_.exchange(-1);
    if (rx_fd >= 0) {
        shutdown(rx_fd, SHUT_RDWR);
        close(rx_fd);
    }

    const int tx_fd = tx_socket_.exchange(-1);
    if (tx_fd >= 0) {
        shutdown(tx_fd, SHUT_RDWR);
        close(tx_fd);
    }

    // Publish completion for any waiting latch-losers.
    {
        std::lock_guard<std::mutex> lock(mutex_);
        teardown_done_ = true;
    }
    queue_cv_.notify_all();
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

bool RtpSession::is_rtcp_packet(const uint8_t* data, const std::size_t len) {
    if (data == nullptr || len < 8) {
        return false;
    }
    const uint8_t version = static_cast<uint8_t>((data[0] >> 6) & 0x03u);
    const uint8_t packet_type = data[1];
    return version == 2 && packet_type >= 200 && packet_type <= 204;
}

void RtpSession::reset_jitter_buffer_locked() {
    jitter_slots_.clear();
    jitter_slots_.resize(config_.jitter_buffer_capacity_frames);
    jitter_buffer_size_ = 0;
    jitter_min_seq_valid_ = false;
    jitter_min_seq_ = 0;
}

// Mark every slot free WITHOUT reallocating, restoring the invariant
// jitter_buffer_size_ == (number of occupied slots) == 0. Used as the recovery
// path when the min-seq linear scan cannot reach a still-occupied slot (a
// sequence discontinuity larger than the ring can represent, e.g. an SSRC
// restart). Leaving those slots occupied while zeroing the size is what let
// drop_oldest_jitter_frame_locked() early-return on size==0 while a slot stayed
// occupied, spinning insert_jitter_frame_locked()'s eviction loop forever under
// mutex_ (VG-02). Safe to call from insert/pop/drop: it never reallocates
// jitter_slots_, so any &jitter_slots_[index] the caller re-derives afterward
// stays valid.
void RtpSession::clear_all_jitter_slots_locked() {
    // D-remainder accounting: the stranded frames cleared here were real received
    // audio being discarded — count them as drops instead of vanishing silently.
    // (A seq-rebase that preserved them is deliberately NOT attempted: with echo
    // off in production this ring is vestigial (VG-23 moot), the frames are
    // beyond the ring's representable window so any new base would be an
    // arbitrary guess, and clear-all is the recovery path proven deadlock-free.)
    uint64_t cleared = 0;
    for (auto& slot : jitter_slots_) {
        if (slot.occupied) {
            ++cleared;
        }
        slot.occupied = false;
    }
    if (cleared > 0) {
        jitter_buffer_overflow_drops_.fetch_add(cleared);
        dropped_packets_.fetch_add(cleared);
    }
    jitter_buffer_size_ = 0;
    jitter_min_seq_valid_ = false;
    jitter_min_seq_ = 0;
}

std::size_t RtpSession::jitter_index(const uint16_t sequence_number) const {
    return static_cast<std::size_t>(sequence_number) & (config_.jitter_buffer_capacity_frames - 1);
}

bool RtpSession::insert_jitter_frame_locked(const QueuedRtpFrame& frame) {
    if (jitter_slots_.empty()) {
        reset_jitter_buffer_locked();
    }

    std::size_t index = jitter_index(frame.sequence_number);
    JitterSlot* slot = &jitter_slots_[index];
    if (slot->occupied && slot->frame.sequence_number == frame.sequence_number) {
        duplicate_packets_.fetch_add(1);
        return false;
    }

    std::size_t evict_guard = 0;
    while (jitter_buffer_size_ >= config_.jitter_buffer_capacity_frames || slot->occupied) {
        drop_oldest_jitter_frame_locked();
        // Guarantee forward progress. drop_oldest_jitter_frame_locked() +
        // advance_jitter_min_seq_locked() now always either free a slot or clear
        // the whole ring, so this loop terminates; the guard is a hard backstop
        // against any future bookkeeping drift re-introducing the VG-02 spin.
        if (++evict_guard > config_.jitter_buffer_capacity_frames) {
            clear_all_jitter_slots_locked();
            break;
        }
        index = jitter_index(frame.sequence_number);
        slot = &jitter_slots_[index];
        if (slot->occupied && slot->frame.sequence_number == frame.sequence_number) {
            duplicate_packets_.fetch_add(1);
            return false;
        }
    }

    // Re-derive after the eviction loop: clear_all_jitter_slots_locked() may have
    // run above, and although it never reallocates, recomputing keeps the write
    // target unambiguous.
    index = jitter_index(frame.sequence_number);
    slot = &jitter_slots_[index];
    slot->occupied = true;
    slot->frame = frame;
    ++jitter_buffer_size_;
    if (!jitter_min_seq_valid_ || sequence_diff(frame.sequence_number, jitter_min_seq_) < 0) {
        jitter_min_seq_valid_ = true;
        jitter_min_seq_ = frame.sequence_number;
    }
    return true;
}

bool RtpSession::pop_next_jitter_frame_locked(QueuedRtpFrame& frame) {
    if (!jitter_min_seq_valid_ || jitter_buffer_size_ == 0) {
        return false;
    }

    const std::size_t index = jitter_index(jitter_min_seq_);
    JitterSlot& slot = jitter_slots_[index];
    if (!slot.occupied || slot.frame.sequence_number != jitter_min_seq_) {
        advance_jitter_min_seq_locked();
        if (!jitter_min_seq_valid_) {
            return false;
        }
    }

    JitterSlot& current = jitter_slots_[jitter_index(jitter_min_seq_)];
    if (!current.occupied || current.frame.sequence_number != jitter_min_seq_) {
        return false;
    }

    frame = current.frame;
    current.occupied = false;
    if (jitter_buffer_size_ > 0) {
        --jitter_buffer_size_;
    }
    if (jitter_buffer_size_ == 0) {
        jitter_min_seq_valid_ = false;
    } else {
        advance_jitter_min_seq_locked();
    }
    return true;
}

void RtpSession::drop_oldest_jitter_frame_locked() {
    if (!jitter_min_seq_valid_ || jitter_buffer_size_ == 0) {
        return;
    }

    JitterSlot& slot = jitter_slots_[jitter_index(jitter_min_seq_)];
    if (slot.occupied && slot.frame.sequence_number == jitter_min_seq_) {
        slot.occupied = false;
        if (jitter_buffer_size_ > 0) {
            --jitter_buffer_size_;
        }
        jitter_buffer_overflow_drops_.fetch_add(1);
        dropped_packets_.fetch_add(1);
    }

    if (jitter_buffer_size_ == 0) {
        jitter_min_seq_valid_ = false;
        return;
    }
    advance_jitter_min_seq_locked();
}

void RtpSession::advance_jitter_min_seq_locked() {
    if (jitter_buffer_size_ == 0) {
        jitter_min_seq_valid_ = false;
        return;
    }

    uint16_t candidate = jitter_min_seq_;
    for (std::size_t offset = 1; offset <= config_.jitter_buffer_capacity_frames; ++offset) {
        candidate = static_cast<uint16_t>(candidate + 1);
        const JitterSlot& slot = jitter_slots_[jitter_index(candidate)];
        if (slot.occupied && slot.frame.sequence_number == candidate) {
            jitter_min_seq_valid_ = true;
            jitter_min_seq_ = candidate;
            return;
        }
    }

    // No reachable successor within the ring's representable window. Any slots
    // still marked occupied are stranded beyond that window (a >capacity
    // sequence jump / stream restart) and can never be popped or dropped by
    // seq-directed logic. Clear them outright so size stays consistent with
    // occupancy; otherwise the next colliding insert() spins forever (VG-02).
    clear_all_jitter_slots_locked();
}

void RtpSession::maybe_send_rtcp_report(
    const sockaddr_in& remote_addr,
    const std::chrono::steady_clock::time_point& now) {
    const int tx_fd = tx_socket_.load();
    if (tx_fd < 0 || config_.remote_port == 0 || config_.remote_port == std::numeric_limits<uint16_t>::max()) {
        return;
    }

    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - last_rtcp_report_sent_at_).count();
    if (elapsed < kRtcpReportIntervalMs) {
        return;
    }

    sockaddr_in rtcp_addr = remote_addr;
    rtcp_addr.sin_port = htons(static_cast<uint16_t>(config_.remote_port + 1));

    std::array<uint8_t, 8> rr{};
    rr[0] = 0x80;
    rr[1] = 201;  // Receiver Report
    rr[2] = 0x00;
    rr[3] = 0x01;  // length in 32-bit words minus one

    const uint32_t ssrc = sequencer_.ssrc();
    rr[4] = static_cast<uint8_t>((ssrc >> 24) & 0xFFu);
    rr[5] = static_cast<uint8_t>((ssrc >> 16) & 0xFFu);
    rr[6] = static_cast<uint8_t>((ssrc >> 8) & 0xFFu);
    rr[7] = static_cast<uint8_t>(ssrc & 0xFFu);

    const ssize_t sent = sendto(
        tx_fd,
        rr.data(),
        rr.size(),
        0,
        reinterpret_cast<const sockaddr*>(&rtcp_addr),
        sizeof(rtcp_addr));
    if (sent >= 0) {
        last_rtcp_report_sent_at_ = now;
    }
}

}  // namespace voice_gateway
