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
#include "voice_gateway/session_registry.h"
#include "voice_gateway/http_server.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
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

// Send one 160-byte PCMU RTP packet (PT0) to dst. payload_marker fills the
// payload (its first byte lets a test identify which packet was delivered).
void send_rtp(const int fd, const sockaddr_in& dst, const uint16_t seq, const uint32_t ts, const uint32_t ssrc,
              const uint8_t payload_marker = 0xFF) {
    uint8_t pkt[12 + 160];
    std::memset(pkt, payload_marker, sizeof(pkt));
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

// VG-01: with stt_reorder enabled, caller RTP delivered out of order must reach
// the STT callback in ascending SEQUENCE order. Each payload's first byte encodes
// its sequence, so the callback can record the delivered order.
void test_stt_reorder_ordering() {
    const int dummy = make_udp_bound(34302);  // absorb nothing (echo off), keep symmetry
    (void)dummy;

    SessionConfig cfg = base_config("reorder", 34301, 34302);
    cfg.echo_enabled = false;
    cfg.stt_reorder_enabled = true;
    cfg.stt_reorder_window_frames = 3;
    cfg.stt_reorder_hold_ms = 60;
    cfg.watchdog_tick_ms = 50;  // frequent idle wakes -> prompt tail flush
    RtpSession session(cfg);

    auto recorded = std::make_shared<std::vector<int>>();
    auto rec_mutex = std::make_shared<std::mutex>();
    session.set_audio_callback([recorded, rec_mutex](const std::string&, const std::vector<uint8_t>& pl) {
        if (!pl.empty()) {
            std::lock_guard<std::mutex> lk(*rec_mutex);
            recorded->push_back(static_cast<int>(pl[0]));
        }
    });

    std::string err;
    check(session.start(err), "vg01_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34301);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    // Deliberately out of order: the 102-before-101 and 105-before-104 swaps are
    // exactly the arrival-order corruption VG-01 fixes.
    const uint16_t seqs[] = {100, 102, 101, 103, 105, 104};
    uint32_t ts = 0;
    for (const uint16_t s : seqs) {
        send_rtp(tx, dst, s, ts, 0x2222u, static_cast<uint8_t>(s & 0xFF));
        ts += 160;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(600));  // window drain + aged tail flush

    std::vector<int> got;
    {
        std::lock_guard<std::mutex> lk(*rec_mutex);
        got = *recorded;
    }

    check(got.size() == 6, "vg01_all_frames_delivered (got=" + std::to_string(got.size()) + ")");
    bool ascending = got.size() == 6;
    for (std::size_t i = 1; i < got.size(); ++i) {
        if (got[i] <= got[i - 1]) {
            ascending = false;
        }
    }
    check(ascending, "vg01_delivered_in_sequence_order");
    if (got.size() >= 3) {
        check(got[0] == 100 && got[1] == 101 && got[2] == 102, "vg01_reordered_100_101_102");
    }

    session.stop("test_done");
    close(tx);
    if (dummy >= 0) {
        close(dummy);
    }
}

// Send a raw HTTP request to 127.0.0.1:port and return the full response.
std::string http_roundtrip(const uint16_t port, const std::string& raw) {
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return "";
    }
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    const timeval tv{2, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    if (connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) != 0) {
        close(fd);
        return "";
    }
    (void)send(fd, raw.data(), raw.size(), 0);
    std::string resp;
    char buf[2048];
    ssize_t n;
    while ((n = recv(fd, buf, sizeof(buf), 0)) > 0) {
        resp.append(buf, static_cast<std::size_t>(n));
        if (resp.size() > 65536) {
            break;
        }
    }
    close(fd);
    return resp;
}

std::string post_request(const std::string& path, const std::string& body) {
    std::ostringstream o;
    o << "POST " << path << " HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
      << "Content-Length: " << body.size() << "\r\nConnection: close\r\n\r\n"
      << body;
    return o.str();
}

// VG-18 (+ VG-11 start path, + VG-19/VG-03 shutdown): drive the real HttpServer
// end to end. A CRLF-laced callback URL must be rejected; a clean start must
// succeed with auth off (default); /health is unauthenticated.
void test_control_plane_callback_validation() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18099, registry);
    std::string err;
    check(server.start(err), "vg18_server_start");
    std::thread server_thread([&server] { server.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(80));

    const std::string bad_body =
        "{\"session_id\":\"vg18-bad\",\"listen_ip\":\"127.0.0.1\",\"listen_port\":41700,"
        "\"remote_ip\":\"127.0.0.1\",\"remote_port\":41701,\"codec\":\"pcmu\",\"ptime_ms\":20,"
        "\"audio_callback_url\":\"http://127.0.0.1:8000/x\\r\\nX-Injected: 1\"}";
    const std::string bad_resp = http_roundtrip(18099, post_request("/v1/sessions/start", bad_body));
    check(bad_resp.find("callback_url_not_allowed") != std::string::npos, "vg18_crlf_callback_rejected");

    const std::string ok_body =
        "{\"session_id\":\"vg18-ok\",\"listen_ip\":\"127.0.0.1\",\"listen_port\":41702,"
        "\"remote_ip\":\"127.0.0.1\",\"remote_port\":41703,\"codec\":\"pcmu\",\"ptime_ms\":20}";
    const std::string ok_resp = http_roundtrip(18099, post_request("/v1/sessions/start", ok_body));
    check(ok_resp.find("\"status\":\"started\"") != std::string::npos, "vg18_valid_start_ok");
    (void)http_roundtrip(18099, post_request("/v1/sessions/stop", "{\"session_id\":\"vg18-ok\"}"));

    const std::string health =
        http_roundtrip(18099, "GET /health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n");
    check(health.find("\"status\":\"ok\"") != std::string::npos, "vg18_health_unauthenticated_ok");

    server.stop();  // exercises the VG-19/VG-03 graceful drain path
    server_thread.join();
}

// Batch C (VG-03/#1): a handler stuck in read_request's recv() (client declared a
// body but sent none) must be force-unblocked by stop()'s socket shutdown, so
// shutdown drains it and returns instead of hanging or proceeding while it runs.
void test_shutdown_drains_inflight_handler() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18098, registry);
    std::string err;
    check(server.start(err), "vg03_server_start");
    std::thread server_thread([&server] { server.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(80));

    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(18098);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    check(connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) == 0, "vg03_client_connected");

    // Declares a 100-byte body but sends none -> the handler blocks in recv().
    const std::string partial = "POST /v1/sessions/start HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n";
    (void)send(fd, partial.data(), partial.size(), 0);
    std::this_thread::sleep_for(std::chrono::milliseconds(80));  // let the handler reach recv()

    auto stop_fut = std::async(std::launch::async, [&server] {
        server.stop();  // must shutdown the stuck client socket and drain, no timeout escape
        return true;
    });
    const bool stopped = stop_fut.wait_for(std::chrono::seconds(10)) == std::future_status::ready;
    check(stopped, "vg03_stop_drains_stuck_handler_no_hang");
    if (stopped) {
        (void)stop_fut.get();
        server_thread.join();
    }

    // After drain, the handler closed our socket on its exit path — recv sees EOF.
    char buf[8];
    const ssize_t n = recv(fd, buf, sizeof(buf), 0);
    check(n <= 0, "vg03_client_socket_closed_by_server");
    close(fd);
}

// Batch A (#5): once STT has been handed a frame, a LATER packet with a lower
// sequence must be REJECTED (hard emission floor), never emitted backwards.
void test_stt_reorder_rejects_late_after_emit() {
    SessionConfig cfg = base_config("reorder2", 34401, 34402);
    cfg.echo_enabled = false;
    cfg.stt_reorder_enabled = true;
    cfg.stt_reorder_window_frames = 3;
    cfg.stt_reorder_hold_ms = 60;
    cfg.watchdog_tick_ms = 50;
    RtpSession session(cfg);

    auto recorded = std::make_shared<std::vector<int>>();
    auto rec_mutex = std::make_shared<std::mutex>();
    session.set_audio_callback([recorded, rec_mutex](const std::string&, const std::vector<uint8_t>& pl) {
        if (!pl.empty()) {
            std::lock_guard<std::mutex> lk(*rec_mutex);
            recorded->push_back(static_cast<int>(pl[0]));
        }
    });

    std::string err;
    check(session.start(err), "vg01b_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34401);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    uint32_t ts = 0;
    const auto burst = [&](const uint16_t s) {
        send_rtp(tx, dst, s, ts, 0x3333u, static_cast<uint8_t>(s & 0xFF));
        ts += 160;
    };
    burst(100);
    burst(101);
    burst(102);
    burst(103);  // window=3 -> the 4th pushes 100 out to STT
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    burst(99);  // arrives AFTER 100 was emitted -> must be rejected
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    std::vector<int> got;
    {
        std::lock_guard<std::mutex> lk(*rec_mutex);
        got = *recorded;
    }

    bool has99 = false;
    bool ascending = true;
    for (std::size_t i = 0; i < got.size(); ++i) {
        if (got[i] == 99) {
            has99 = true;
        }
        if (i > 0 && got[i] <= got[i - 1]) {
            ascending = false;
        }
    }
    check(!has99, "vg01b_late_packet_after_emit_rejected");
    check(ascending, "vg01b_emitted_strictly_ascending");

    session.stop("test_done");
    close(tx);
}

// Review #4: ONE same-SSRC packet with a huge forward sequence jump must not
// poison liveness. The old classifier advanced the forward watermark to the
// jump, after which every legitimate packet was "not forward", last_rtp_rx
// froze, and the watchdog killed the live call at active_no_rtp_timeout_ms.
// The anomaly packet itself must also never reach STT (unqualified jump).
void test_anomaly_jump_keeps_liveness() {
    SessionConfig cfg = base_config("anomaly", 34501, 34502);
    cfg.echo_enabled = false;
    cfg.stt_reorder_enabled = true;
    cfg.stt_reorder_window_frames = 3;
    cfg.stt_reorder_hold_ms = 100;
    cfg.watchdog_tick_ms = 50;
    cfg.startup_no_rtp_timeout_ms = 1000;
    cfg.active_no_rtp_timeout_ms = 400;  // trips fast if liveness freezes
    cfg.session_final_timeout_ms = 100000;
    RtpSession session(cfg);

    auto recorded = std::make_shared<std::vector<int>>();
    auto rec_mutex = std::make_shared<std::mutex>();
    session.set_audio_callback([recorded, rec_mutex](const std::string&, const std::vector<uint8_t>& pl) {
        if (!pl.empty()) {
            std::lock_guard<std::mutex> lk(*rec_mutex);
            recorded->push_back(static_cast<int>(pl[0]));
        }
    });

    std::string err;
    check(session.start(err), "rev4_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34501);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    uint32_t ts = 0;
    // Establish the stream, inject the anomaly, then keep streaming legitimate
    // audio for ~1.2s — well past the 400ms no-RTP timeout.
    send_rtp(tx, dst, 1000, ts, 0x4444u, static_cast<uint8_t>(1000 & 0xFF));
    ts += 160;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    send_rtp(tx, dst, 50000, ts, 0x4444u, static_cast<uint8_t>(50000 & 0xFF));  // marker 80
    ts += 160;
    for (uint16_t s = 1001; s <= 1060; ++s) {
        send_rtp(tx, dst, s, ts, 0x4444u, static_cast<uint8_t>(s & 0xFF));
        ts += 160;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    check(session.running(), "rev4_liveness_survives_anomalous_jump");

    std::this_thread::sleep_for(std::chrono::milliseconds(300));  // drain tail
    bool anomaly_delivered = false;
    std::size_t delivered = 0;
    {
        std::lock_guard<std::mutex> lk(*rec_mutex);
        delivered = recorded->size();
        for (const int m : *recorded) {
            if (m == 80) {  // 50000 & 0xFF
                anomaly_delivered = true;
            }
        }
    }
    check(!anomaly_delivered, "rev4_unqualified_jump_packet_not_fed_to_stt");
    check(delivered >= 50, "rev4_legitimate_audio_still_delivered (n=" + std::to_string(delivered) + ")");

    session.stop("test_done");
    close(tx);
}

// Batch A wrap coverage (review: no wrap-boundary test existed): frames across
// the 16-bit wrap must be delivered in true extended-sequence order.
void test_wrap_boundary_ordering() {
    SessionConfig cfg = base_config("wrap", 34601, 34602);
    cfg.echo_enabled = false;
    cfg.stt_reorder_enabled = true;
    cfg.stt_reorder_window_frames = 3;
    cfg.stt_reorder_hold_ms = 60;
    cfg.watchdog_tick_ms = 50;
    RtpSession session(cfg);

    auto recorded = std::make_shared<std::vector<int>>();
    auto rec_mutex = std::make_shared<std::mutex>();
    session.set_audio_callback([recorded, rec_mutex](const std::string&, const std::vector<uint8_t>& pl) {
        if (!pl.empty()) {
            std::lock_guard<std::mutex> lk(*rec_mutex);
            recorded->push_back(static_cast<int>(pl[0]));
        }
    });

    std::string err;
    check(session.start(err), "wrap_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34601);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    const uint16_t seqs[] = {65534, 65535, 0, 1};
    uint32_t ts = 0;
    for (const uint16_t s : seqs) {
        send_rtp(tx, dst, s, ts, 0x5555u, static_cast<uint8_t>(s & 0xFF));
        ts += 160;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(400));

    std::vector<int> got;
    {
        std::lock_guard<std::mutex> lk(*rec_mutex);
        got = *recorded;
    }
    const std::vector<int> expected = {254, 255, 0, 1};
    check(got == expected, "wrap_delivered_in_extended_order (n=" + std::to_string(got.size()) + ")");

    session.stop("test_done");
    close(tx);
}

// Review #5: an SSRC restart with a NONEMPTY reorder window must flush the old
// epoch first and must NOT let its (huge) extended sequences poison the fresh
// epoch's emission floor — and the probation packet must be replayed, not lost.
void test_ssrc_restart_epoch_flush() {
    SessionConfig cfg = base_config("epoch", 34701, 34702);
    cfg.echo_enabled = false;
    cfg.stt_reorder_enabled = true;
    cfg.stt_reorder_window_frames = 3;
    cfg.stt_reorder_hold_ms = 500;  // hold the old epoch in the window across the restart
    cfg.watchdog_tick_ms = 50;
    RtpSession session(cfg);

    auto recorded = std::make_shared<std::vector<int>>();
    auto rec_mutex = std::make_shared<std::mutex>();
    session.set_audio_callback([recorded, rec_mutex](const std::string&, const std::vector<uint8_t>& pl) {
        if (!pl.empty()) {
            std::lock_guard<std::mutex> lk(*rec_mutex);
            recorded->push_back(static_cast<int>(pl[0]));
        }
    });

    std::string err;
    check(session.start(err), "epoch_start");

    const int tx = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(34701);
    inet_pton(AF_INET, "127.0.0.1", &dst.sin_addr);

    uint32_t ts = 0;
    const auto burst = [&](const uint16_t s, const uint32_t ssrc) {
        send_rtp(tx, dst, s, ts, ssrc, static_cast<uint8_t>(s & 0xFF));
        ts += 160;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    };
    // Old stream A: two frames buffered in the window (not yet emitted).
    burst(60000, 0xAAAAu);  // marker 96
    burst(60001, 0xAAAAu);  // marker 97
    // New stream B: 100 starts probation (buffered), 101 commits the restart.
    burst(100, 0xBBBBu);
    burst(101, 0xBBBBu);
    burst(102, 0xBBBBu);
    burst(103, 0xBBBBu);
    std::this_thread::sleep_for(std::chrono::milliseconds(800));  // aged tail flush

    std::vector<int> got;
    {
        std::lock_guard<std::mutex> lk(*rec_mutex);
        got = *recorded;
    }
    // Old epoch flushed in order, probation frame replayed, then the new stream
    // — nothing rejected against the dead epoch's floor, nothing lost.
    const std::vector<int> expected = {96, 97, 100, 101, 102, 103};
    check(got == expected, "epoch_old_flushed_probe_replayed_new_admitted (n=" + std::to_string(got.size()) + ")");

    session.stop("test_done");
    close(tx);
}

// Review #14: destroying a registry with many live sessions must signal them
// ALL first and only then join — total ~max(single teardown), not the sum.
// 10 sessions with a 1000ms receiver wake would take ~5s average serially.
void test_registry_parallel_shutdown() {
    auto registry = std::make_unique<voice_gateway::SessionRegistry>();
    for (int i = 0; i < 10; ++i) {
        SessionConfig cfg = base_config("bulk-" + std::to_string(i),
                                        static_cast<uint16_t>(42000 + i * 2),
                                        static_cast<uint16_t>(42001 + i * 2));
        cfg.watchdog_tick_ms = 1000;              // 1000ms receiver wake = serial worst case
        cfg.startup_no_rtp_timeout_ms = 3600000;  // keep sessions alive for the test
        cfg.active_no_rtp_timeout_ms = 3600000;
        cfg.hold_no_rtp_timeout_ms = 3600000;
        cfg.session_final_timeout_ms = 3600000;
        std::string err;
        check(registry->start_session(cfg, err) == voice_gateway::StartSessionResult::Started,
              "bulk_start_" + std::to_string(i));
    }

    const auto t0 = std::chrono::steady_clock::now();
    registry.reset();  // ~SessionRegistry: signal-all pass, then join-all pass
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                                std::chrono::steady_clock::now() - t0).count();
    check(elapsed_ms < 4000,
          "bulk_shutdown_parallel_not_serial (took " + std::to_string(elapsed_ms) + "ms)");
}

// Review: HttpServer is single-use — start() after stop() must be refused, not
// leak the old listener / serve with draining_ latched.
void test_server_single_use() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18096, registry);
    std::string err;
    check(server.start(err), "single_use_first_start_ok");
    std::thread server_thread([&server] { server.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    server.stop();
    server_thread.join();
    std::string err2;
    check(!server.start(err2), "single_use_restart_refused");
}

// Review #7 (deterministic half): stop() issued BEFORE start() must win — the
// session must refuse to start, and destruction must be clean (no terminate).
void test_stop_before_start_wins() {
    SessionConfig cfg = base_config("early-stop", 34801, 34802);
    RtpSession session(cfg);
    session.stop("stopped_before_start");
    std::string err;
    check(!session.start(err), "stop_before_start_start_refused");
    check(!session.running(), "stop_before_start_not_running");
}

// Batch B / review #11: a slowloris client that trickles header bytes forever
// (never sending the terminator) must be cut off at the ABSOLUTE 10s deadline.
// The old per-recv timeout never fired as long as bytes kept arriving. This
// test intentionally runs ~11s of wall clock.
void test_slowloris_header_deadline() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18095, registry);
    std::string err;
    check(server.start(err), "slowloris_server_start");
    std::thread server_thread([&server] { server.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(80));

    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(18095);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    check(connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) == 0, "slowloris_connected");

    const auto t0 = std::chrono::steady_clock::now();
    const std::string opener = "GET /health HTTP/1.1\r\nHost: x\r\nX-Drip: ";
    (void)send(fd, opener.data(), opener.size(), MSG_NOSIGNAL);

    // Reader watches for the server cutting us off (EOF/reset).
    auto closed_at = std::async(std::launch::async, [fd, t0] {
        char b[512];
        for (;;) {
            const ssize_t n = recv(fd, b, sizeof(b), 0);
            if (n <= 0) {
                break;
            }
        }
        return std::chrono::duration_cast<std::chrono::milliseconds>(
                   std::chrono::steady_clock::now() - t0).count();
    });

    // Drip one byte every 250ms — always inside any per-recv timeout.
    for (int i = 0; i < 60; ++i) {
        if (send(fd, "a", 1, MSG_NOSIGNAL) <= 0) {
            break;  // server already reset the connection
        }
        if (closed_at.wait_for(std::chrono::milliseconds(250)) == std::future_status::ready) {
            break;
        }
    }

    const bool cut = closed_at.wait_for(std::chrono::seconds(14)) == std::future_status::ready;
    check(cut, "slowloris_connection_cut");
    if (cut) {
        const auto ms = closed_at.get();
        check(ms >= 8000 && ms <= 13000,
              "slowloris_cut_at_absolute_deadline (" + std::to_string(ms) + "ms)");
    }
    close(fd);

    server.stop();
    server_thread.join();
}

// Batch B (#11): a truncated request (client sends a partial header then closes
// its write side) must make the handler return and close the connection promptly
// — not hang — and clean up via the owned-handler exit path.
void test_truncated_request_no_hang() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18097, registry);
    std::string err;
    check(server.start(err), "vgB_server_start");
    std::thread server_thread([&server] { server.run(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(80));

    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(18097);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    check(connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) == 0, "vgB_connected");

    const std::string junk = "GET /nope HTTP/1.1\r\nHost: x\r\n";  // no terminating blank line
    (void)send(fd, junk.data(), junk.size(), 0);
    ::shutdown(fd, SHUT_WR);  // EOF -> server recv returns 0 -> read_request bails

    auto fut = std::async(std::launch::async, [fd] {
        char b[512];
        std::string r;
        ssize_t n;
        while ((n = recv(fd, b, sizeof(b), 0)) > 0) {
            r.append(b, static_cast<std::size_t>(n));
        }
        return r;
    });
    const bool done = fut.wait_for(std::chrono::seconds(8)) == std::future_status::ready;
    check(done, "vgB_truncated_request_handler_completes_no_hang");
    if (done) {
        (void)fut.get();
    }

    server.stop();
    server_thread.join();
    close(fd);
}

}  // namespace

int main() {
    test_tts_overflow_accounting();
    test_jitter_flood_no_deadlock();
    test_stt_reorder_ordering();
    test_stt_reorder_rejects_late_after_emit();
    test_anomaly_jump_keeps_liveness();
    test_wrap_boundary_ordering();
    test_ssrc_restart_epoch_flush();
    test_stop_before_start_wins();
    test_registry_parallel_shutdown();
    test_server_single_use();
    test_control_plane_callback_validation();
    test_shutdown_drains_inflight_handler();
    test_truncated_request_no_hang();
    test_slowloris_header_deadline();
    std::cout << "passed=" << g_pass << " failed=" << g_fail << "\n";
    return g_fail == 0 ? 0 : 1;
}
