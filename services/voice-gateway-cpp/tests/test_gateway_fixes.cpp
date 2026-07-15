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

    // After drain, the server closed our socket (finish_request) — recv sees EOF.
    char buf[8];
    const ssize_t n = recv(fd, buf, sizeof(buf), 0);
    check(n <= 0, "vg03_client_socket_closed_by_server");
    close(fd);
}

// Batch B (#11): a truncated request (client sends a partial header then closes
// its write side) must make the handler return and close the connection promptly
// — not hang — and clean up via finish_request.
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
    test_control_plane_callback_validation();
    test_shutdown_drains_inflight_handler();
    test_truncated_request_no_hang();
    std::cout << "passed=" << g_pass << " failed=" << g_fail << "\n";
    return g_fail == 0 ? 0 : 1;
}
