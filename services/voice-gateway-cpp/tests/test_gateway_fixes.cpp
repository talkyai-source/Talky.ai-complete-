// Regression tests for the audit-fix batch (VG-xx) landed on the voice gateway.
//
// Standalone (own main), built like test_concurrency.cpp against the same
// voice_gateway_lib sources under -O2, TSan, and ASan+UBSan. Uses ONLY the
// public API in session.h plus real loopback UDP to drive the RTP receive path.
//
// Focus (the subtle, high-risk fixes the existing suites do NOT cover):
//   - VG-02: a >capacity sequence gap + slot collision must never spin
//            insert_jitter_frame_locked() forever under mutex_ (deadlock).
//   - VG-25: TTS queue overflow must report frames RETAINED, not submitted.

#include "voice_gateway/session.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <future>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

using voice_gateway::RtpSession;
using voice_gateway::SessionConfig;

namespace {

int g_pass = 0;
int g_fail = 0;

void check(const bool cond, const std::string& name) {
    if (cond) {
        std::cout << "[PASS] " << name << "\n";
        ++g_pass;
    } else {
        std::cout << "[FAIL] " << name << "\n";
        ++g_fail;
    }
}

// Send one 160-byte PCMU RTP packet (PT0) to dst.
void send_rtp(const int fd, const sockaddr_in& dst, const uint16_t seq, const uint32_t ts, const uint32_t ssrc) {
    uint8_t pkt[12 + 160];
    std::memset(pkt, 0xFF, sizeof(pkt));  // payload = µ-law silence
    pkt[0] = 0x80;
    pkt[1] = 0x00;
    pkt[2] = static_cast<uint8_t>(seq >> 8);
    pkt[3] = static_cast<uint8_t>(seq & 0xFF);
    pkt[4] = static_cast<uint8_t>(ts >> 24);
    pkt[5] = static_cast<uint8_t>(ts >> 16);
    pkt[6] = static_cast<uint8_t>(ts >> 8);
    pkt[7] = static_cast<uint8_t>(ts & 0xFF);
    pkt[8] = static_cast<uint8_t>(ssrc >> 24);
    pkt[9] = static_cast<uint8_t>(ssrc >> 16);
    pkt[10] = static_cast<uint8_t>(ssrc >> 8);
    pkt[11] = static_cast<uint8_t>(ssrc & 0xFF);
    (void)sendto(fd, pkt, sizeof(pkt), 0, reinterpret_cast<const sockaddr*>(&dst), sizeof(dst));
}

int make_udp_bound(const uint16_t port) {
    const int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        return -1;
    }
    int reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    if (bind(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

SessionConfig base_config(const std::string& id, const uint16_t listen_port, const uint16_t remote_port) {
    SessionConfig cfg;
    cfg.session_id = id;
    cfg.listen_ip = "127.0.0.1";
    cfg.listen_port = listen_port;
    cfg.remote_ip = "127.0.0.1";
    cfg.remote_port = remote_port;
    cfg.codec = "pcmu";
    cfg.ptime_ms = 20;
    return cfg;
}

// VG-25: submitting more frames than the queue capacity must return the number
// actually retained (the tail), never the raw submitted count.
void test_tts_overflow_accounting() {
    SessionConfig cfg = base_config("tts-of", 34101, 34102);
    cfg.tts_max_queue_frames = 10;
    RtpSession session(cfg);

    std::string err;
    check(session.start(err), "vg25_start");

    std::vector<uint8_t> audio(25 * 160, 0xFF);  // 25 frames into a 10-frame queue
    std::size_t queued = 0;
    std::string e2;
    const bool ok = session.enqueue_tts_ulaw(audio, false, queued, e2);
    check(ok, "vg25_enqueue_ok");
    check(queued == 10, "vg25_reports_retained_not_submitted (queued=" + std::to_string(queued) + ")");
    // Depth is a live value the transmitter drains concurrently, so assert the
    // race-free invariant: it is capped and never exceeds tts_max_queue_frames.
    check(session.snapshot().tts_queue_depth_frames <= 10, "vg25_queue_depth_never_exceeds_cap");

    session.stop("test_done");
}

// VG-02: a receiver fed a >capacity sequence gap, slot collisions, duplicates
// and wrap-around must never deadlock. If the orphaned-slot spin regressed, the
// receiver would hold mutex_ forever and both snapshot() and stop() would hang;
// we assert both complete within a generous timeout.
void test_jitter_flood_no_deadlock() {
    const int dummy = make_udp_bound(34202);  // absorb echo packets (no ICMP unreachable)
    check(dummy >= 0, "vg02_dummy_remote_bound");

    SessionConfig cfg = base_config("flood", 34201, 34202);
    cfg.echo_enabled = true;              // drive playout -> pop -> advance (the VG-02 path)
    cfg.jitter_buffer_enabled = true;
    cfg.jitter_buffer_capacity_frames = 64;
    cfg.jitter_buffer_prefetch_frames = 1;
    RtpSession session(cfg);

    std::string err;
    check(session.start(err), "vg02_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34201);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    uint32_t ts = 0;
    const auto burst = [&](const uint16_t seq) {
        send_rtp(tx, dst, seq, ts, 0x1111u);
        ts += 160;
    };
    for (int round = 0; round < 300; ++round) {
        burst(100);
        burst(201);
        burst(202);      // >64 from 100 -> advance runs off its window
        burst(265);      // 265 & 63 == 9 == 201's slot -> the collision that used to spin
        burst(100);      // duplicate / late
        burst(60000);    // near wrap
        burst(3);        // wrapped past 65535
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }

    auto snap_fut = std::async(std::launch::async, [&] { return session.snapshot().packets_in; });
    const bool responsive = snap_fut.wait_for(std::chrono::seconds(5)) == std::future_status::ready;
    check(responsive, "vg02_session_responsive_no_deadlock");
    if (responsive) {
        (void)snap_fut.get();
    }

    auto stop_fut = std::async(std::launch::async, [&] {
        session.stop("test_done");
        return true;
    });
    const bool stopped = stop_fut.wait_for(std::chrono::seconds(5)) == std::future_status::ready;
    check(stopped, "vg02_clean_stop_no_hang");
    if (stopped) {
        (void)stop_fut.get();
    }

    close(tx);
    if (dummy >= 0) {
        close(dummy);
    }
}

}  // namespace

int main() {
    test_tts_overflow_accounting();
    test_jitter_flood_no_deadlock();
    std::cout << "passed=" << g_pass << " failed=" << g_fail << "\n";
    return g_fail == 0 ? 0 : 1;
}
