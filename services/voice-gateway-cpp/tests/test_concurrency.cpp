// Standalone concurrency stress test for the voice-gateway session lifecycle.
//
// This file is intentionally NOT wired into CMakeLists.txt by this change --
// the orchestrator compiles/links it directly (against the same
// voice_gateway_lib sources) under ThreadSanitizer and AddressSanitizer. See
// the build command in the accompanying report.
//
// It exercises the just-landed thread-safety fixes in:
//   - src/session.cpp:           RtpSession::request_stop() single-winner
//                                 teardown (atomic<bool> running_ exchange +
//                                 atomic<int> rx_socket_/tx_socket_ exchange
//                                 to prevent double-close / use-after-free).
//   - src/session_registry.cpp:  SessionRegistry's mutex_-guarded sessions_
//                                 map, the reaper thread's stopped_since_
//                                 bookkeeping, and sessions_reaped_total_.
//
// All test scenarios use ONLY the public API declared in
// include/voice_gateway/session.h and include/voice_gateway/session_registry.h.

#include "voice_gateway/session.h"
#include "voice_gateway/session_registry.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

using voice_gateway::ProcessStatsSnapshot;
using voice_gateway::RtpSessionPtr;
using voice_gateway::SessionConfig;
using voice_gateway::SessionRegistry;
using voice_gateway::SessionStatsSnapshot;
using voice_gateway::StartSessionResult;

namespace {

// ---------------------------------------------------------------------------
// Tunables -- kept as constants at the top so the orchestrator can dial them
// up/down depending on how much time is budgeted under TSan/ASan (both
// sanitizers add significant per-syscall/per-access overhead).
// ---------------------------------------------------------------------------
constexpr int kConcurrentStopIterations = 200;   // fresh sessions raced in test 1
constexpr int kConcurrentStopThreads = 8;        // racer threads per session in test 1
constexpr int kChurnLongLivedSessions = 4;       // long-lived neighbors in test 2
constexpr int kChurnShortLivedSessions = 100;    // short-lived churn sessions in test 2
constexpr int kChurnThreads = 4;                 // worker threads driving churn in test 2
constexpr int kReaperPollThreads = 4;             // threads hammering reap_once() in test 3
constexpr int kReaperPollIterationsPerThread = 10;
constexpr int kSnapshotHammerReaderThreads = 4;   // readers in test 4
constexpr std::chrono::milliseconds kSnapshotHammerDuration{1500};

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

// Minimal valid loopback config. remote_ip/remote_port never need to be a
// real listener -- the transmitter only sendto()s to it (echo/TTS path is
// never exercised here), so a dummy 127.0.0.1 destination is sufficient to
// pass validate_config()/RtpSession::start() and get a real bound RX socket.
SessionConfig make_config(const std::string& session_id, const uint16_t listen_port, const uint16_t remote_port) {
    SessionConfig config;
    config.session_id = session_id;
    config.listen_ip = "127.0.0.1";
    config.listen_port = listen_port;
    config.remote_ip = "127.0.0.1";
    config.remote_port = remote_port;
    config.codec = "pcmu";
    config.ptime_ms = 20;
    return config;
}

// ---------------------------------------------------------------------------
// Test 1: CONCURRENT STOP (targets #7 -- single-winner teardown in
// RtpSession::request_stop()).
//
// For each of kConcurrentStopIterations fresh sessions, kConcurrentStopThreads
// threads race to stop the same session simultaneously via TWO different
// public entry points:
//   - half go through SessionRegistry::stop_session() (map erase + stop())
//   - half call RtpSession::stop() directly on a shared_ptr obtained before
//     the race, which is what actually drives concurrent calls into
//     request_stop()'s atomic running_.exchange(false) single-winner logic
//     and the atomic<int> rx_socket_/tx_socket_ exchange(-1) double-close
//     guard.
//
// Under TSan this must not report any data race. Under ASan, any
// use-after-free/double-free in the teardown epilogue (socket close, thread
// join/detach) will abort the process.
// ---------------------------------------------------------------------------
void test_concurrent_stop_races() {
    constexpr uint16_t kBasePort = 33000;

    for (int i = 0; i < kConcurrentStopIterations; ++i) {
        SessionRegistry registry;
        SessionConfig config = make_config(
            "cstop-" + std::to_string(i),
            static_cast<uint16_t>(kBasePort + i * 2),
            static_cast<uint16_t>(kBasePort + i * 2 + 1));

        std::string error;
        const auto result = registry.start_session(config, error);
        expect(result == StartSessionResult::Started, "concurrent_stop: session should start: " + error);

        RtpSessionPtr session = registry.get_session(config.session_id);
        expect(session != nullptr, "concurrent_stop: session ptr must be retrievable after start");

        std::atomic<int> direct_stop_returns{0};
        std::atomic<int> already_stopped_via_registry{0};
        std::vector<std::thread> stoppers;
        stoppers.reserve(static_cast<std::size_t>(kConcurrentStopThreads));

        for (int t = 0; t < kConcurrentStopThreads; ++t) {
            if (t % 2 == 0) {
                stoppers.emplace_back([&registry, &config, &already_stopped_via_registry]() {
                    bool already_stopped = false;
                    registry.stop_session(config.session_id, "race_test_registry", already_stopped);
                    if (already_stopped) {
                        already_stopped_via_registry.fetch_add(1);
                    }
                });
            } else {
                stoppers.emplace_back([session, &direct_stop_returns]() {
                    session->stop("race_test_direct");
                    direct_stop_returns.fetch_add(1);
                });
            }
        }

        for (auto& th : stoppers) {
            th.join();
        }

        expect(!session->running(), "concurrent_stop: session must be stopped exactly once (running()==false) after racing stoppers");
        expect(
            direct_stop_returns.load() == kConcurrentStopThreads / 2,
            "concurrent_stop: every direct stop() caller must return (no deadlock/hang in request_stop())");
        // Exactly one of the registry-path racers can win the map erase
        // (mutex_-protected find+erase is atomic); every other registry-path
        // racer must observe the session already gone.
        expect(
            already_stopped_via_registry.load() == kConcurrentStopThreads / 2 - 1,
            "concurrent_stop: exactly one registry.stop_session() racer should win the erase");
    }
}

// ---------------------------------------------------------------------------
// Test 2: START/STOP CHURN WITH LIVE NEIGHBORS (targets #7 fd-recycle safety
// and #10 registry map safety).
//
// A pool of kChurnLongLivedSessions sessions is started and kept running for
// the whole test while kChurnThreads worker threads concurrently create and
// immediately stop kChurnShortLivedSessions short-lived sessions (each on its
// own dedicated port range, so no two churn sessions ever bind-race). Because
// closing a churn session's rx/tx fds can hand those integer fd numbers back
// to the OS, this is exactly the scenario that would surface a stale-fd bug
// (e.g. a long-lived session's socket getting closed out from under it by a
// racing churn session that was handed the same recycled fd number).
// ---------------------------------------------------------------------------
void test_start_stop_churn_with_live_neighbors() {
    SessionRegistry registry;

    std::vector<RtpSessionPtr> long_lived;
    std::vector<std::string> long_lived_ids;
    for (int i = 0; i < kChurnLongLivedSessions; ++i) {
        SessionConfig config = make_config(
            "longlived-" + std::to_string(i),
            static_cast<uint16_t>(34000 + i * 2),
            static_cast<uint16_t>(34000 + i * 2 + 1));
        // No RTP is ever sent to these loopback ports, so the default 5s
        // startup_no_rtp_timeout would make the watchdog self-stop them
        // mid-test (especially under TSan's slowdown) and the "still running"
        // assertion below would fail on incidental timeout rather than on the
        // fd-stealing behavior this test targets. Push the no-RTP/final
        // watchdogs well past the whole-test runtime so the neighbors stay up.
        config.startup_no_rtp_timeout_ms = 3600000;   // 1h
        config.active_no_rtp_timeout_ms = 3600000;    // 1h
        config.hold_no_rtp_timeout_ms = 3600000;      // 1h
        config.session_final_timeout_ms = 3600000;    // 1h (>= active, per validate_config)
        std::string error;
        const auto result = registry.start_session(config, error);
        expect(result == StartSessionResult::Started, "churn: long-lived session should start: " + error);

        RtpSessionPtr session = registry.get_session(config.session_id);
        expect(session != nullptr, "churn: long-lived session ptr must be retrievable");
        long_lived_ids.push_back(config.session_id);
        long_lived.push_back(session);
    }

    const int churn_per_thread = kChurnShortLivedSessions / kChurnThreads;
    std::vector<std::thread> workers;
    workers.reserve(static_cast<std::size_t>(kChurnThreads));
    for (int t = 0; t < kChurnThreads; ++t) {
        workers.emplace_back([&registry, t, churn_per_thread]() {
            const uint16_t base_port = static_cast<uint16_t>(35000 + t * churn_per_thread * 2);
            for (int i = 0; i < churn_per_thread; ++i) {
                const std::string id = "churn-" + std::to_string(t) + "-" + std::to_string(i);
                SessionConfig config = make_config(
                    id,
                    static_cast<uint16_t>(base_port + i * 2),
                    static_cast<uint16_t>(base_port + i * 2 + 1));
                std::string error;
                const auto result = registry.start_session(config, error);
                if (result != StartSessionResult::Started) {
                    // Do not throw off the worker thread; the invariant we
                    // actually care about (long-lived neighbors survive) is
                    // asserted on the main thread below.
                    continue;
                }
                bool already_stopped = false;
                registry.stop_session(id, "churn_teardown", already_stopped);
            }
        });
    }
    for (auto& th : workers) {
        th.join();
    }

    for (std::size_t i = 0; i < long_lived.size(); ++i) {
        expect(
            long_lived[i]->running(),
            "churn: long-lived session " + long_lived_ids[i] + " must still be running (no fd stolen by churn)");
        const SessionStatsSnapshot snap = long_lived[i]->snapshot();
        expect(snap.session_id == long_lived_ids[i], "churn: long-lived session object must remain intact");
    }

    for (const auto& id : long_lived_ids) {
        bool already_stopped = false;
        registry.stop_session(id, "test_cleanup", already_stopped);
        expect(!already_stopped, "churn: cleanup stop of long-lived session should not be already_stopped");
    }
}

// ---------------------------------------------------------------------------
// Test 3: REAPER (targets #10).
//
// IMPORTANT LIMITATION: SessionRegistry::kReapGraceMs is a hardcoded private
// `static constexpr int64_t = 60000` (session_registry.h) with NO test hook
// (no setter, no constructor parameter, no compile-time override) to shorten
// it. reap_once() IS public ("exposed for tests" per its header comment), so
// this test calls it directly and repeatedly/concurrently, but it CANNOT
// observe the actual erase-after-grace-period behavior within a sane test
// budget (that would require sleeping ~60s, which violates the ~15s total
// runtime budget for this whole file).
//
// What this test DOES verify with the public API alone:
//   1. A session that self-stops via the watchdog (start_timeout, i.e. never
//      goes through registry.stop_session()) is correctly observed as
//      running()==false while still remaining registered/retrievable
//      (get_session() still resolves it) -- i.e. reap_once() does NOT erase
//      it early / before the grace period.
//   2. Hammering reap_once() from multiple threads concurrently, at the same
//      time start/stop/get_session calls could plausibly be happening on the
//      same registry, does not crash and does not corrupt
//      sessions_reaped_total_ (it must stay unchanged for a not-yet-eligible
//      session).
//
// RECOMMENDATION for full reaper coverage: add a test-only hook, e.g.
//   void SessionRegistry::set_reap_grace_ms_for_testing(int64_t ms);
// or a constructor overload taking the grace/sweep intervals, so a test can
// shrink kReapGraceMs to e.g. 50ms and assert the session is actually erased
// and sessions_reaped_total increments.
// ---------------------------------------------------------------------------
void test_reaper_self_stopped_session_not_erased_before_grace() {
    SessionRegistry registry;

    SessionConfig config = make_config("reaper-selfstop", 36000, 36001);
    config.startup_no_rtp_timeout_ms = 100;   // minimum allowed by validate_config()
    config.watchdog_tick_ms = 50;             // minimum allowed by validate_config()
    config.active_no_rtp_timeout_ms = 100;    // minimum allowed
    config.hold_no_rtp_timeout_ms = 100;      // minimum allowed; equals active timeout so
                                               // the hold branch and the active branch agree
    config.session_final_timeout_ms = 100000;

    std::string error;
    const auto result = registry.start_session(config, error);
    expect(result == StartSessionResult::Started, "reaper: session should start: " + error);

    // Wait for the watchdog thread to self-stop the session (no RTP ever
    // arrives on this loopback port), without ever calling
    // registry.stop_session(). This is precisely the "self-stopped" case the
    // reaper exists to eventually clean up.
    bool became_not_running = false;
    for (int i = 0; i < 100 && !became_not_running; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        RtpSessionPtr session = registry.get_session(config.session_id);
        expect(session != nullptr, "reaper: session must still be registered while polling for self-stop");
        if (!session->running()) {
            became_not_running = true;
        }
    }
    expect(became_not_running, "reaper: session should self-stop via watchdog timeout within ~2s");

    const ProcessStatsSnapshot before = registry.snapshot();

    std::vector<std::thread> reapers;
    reapers.reserve(static_cast<std::size_t>(kReaperPollThreads));
    for (int t = 0; t < kReaperPollThreads; ++t) {
        reapers.emplace_back([&registry]() {
            for (int i = 0; i < kReaperPollIterationsPerThread; ++i) {
                registry.reap_once();
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
        });
    }
    for (auto& th : reapers) {
        th.join();
    }

    const ProcessStatsSnapshot after = registry.snapshot();

    expect(
        registry.get_session(config.session_id) != nullptr,
        "reaper: self-stopped session must NOT be erased before kReapGraceMs elapses");
    expect(
        after.sessions_reaped_total == before.sessions_reaped_total,
        "reaper: sessions_reaped_total must not increase for a not-yet-eligible session");

    bool already_stopped = false;
    registry.stop_session(config.session_id, "test_cleanup", already_stopped);
}

// ---------------------------------------------------------------------------
// Test 4: CONCURRENT snapshot()/list_sessions() WHILE start/stop CHURN RUNS
// (targets registry lock-safety around mutex_).
//
// One thread continuously starts+stops short-lived sessions (recycling a
// small pool of ports so we never run out) while several reader threads
// hammer SessionRegistry::snapshot(), list_sessions(), and
// all_sessions_healthy() concurrently. This is a torn-read/race detector for
// the mutex_-guarded sessions_/stopped_since_ maps and the plain uint64_t
// counters aggregated under that same lock.
// ---------------------------------------------------------------------------
void test_concurrent_snapshot_during_churn() {
    SessionRegistry registry;
    std::atomic<bool> stop_flag{false};
    std::atomic<int> churn_iterations{0};
    std::atomic<bool> churn_thread_error{false};

    std::thread churn_thread([&registry, &stop_flag, &churn_iterations, &churn_thread_error]() {
        int i = 0;
        while (!stop_flag.load()) {
            const std::string id = "snapchurn-" + std::to_string(i);
            const int port_slot = i % 200;
            SessionConfig config = make_config(
                id,
                static_cast<uint16_t>(37000 + port_slot * 2),
                static_cast<uint16_t>(37000 + port_slot * 2 + 1));
            std::string error;
            const auto result = registry.start_session(config, error);
            if (result == StartSessionResult::Started) {
                bool already_stopped = false;
                const bool ok = registry.stop_session(id, "snapshot_churn", already_stopped);
                if (!ok || already_stopped) {
                    churn_thread_error.store(true);
                }
                churn_iterations.fetch_add(1);
            }
            ++i;
        }
    });

    std::atomic<bool> snapshot_ok{true};
    std::vector<std::thread> readers;
    readers.reserve(static_cast<std::size_t>(kSnapshotHammerReaderThreads));
    for (int t = 0; t < kSnapshotHammerReaderThreads; ++t) {
        readers.emplace_back([&registry, &stop_flag, &snapshot_ok]() {
            while (!stop_flag.load()) {
                const ProcessStatsSnapshot snap = registry.snapshot();
                const std::vector<SessionStatsSnapshot> rows = registry.list_sessions();
                (void)rows;
                (void)registry.all_sessions_healthy();
                if (snap.active_sessions > snap.sessions_started_total) {
                    snapshot_ok.store(false);
                }
            }
        });
    }

    std::this_thread::sleep_for(kSnapshotHammerDuration);
    stop_flag.store(true);
    churn_thread.join();
    for (auto& th : readers) {
        th.join();
    }

    expect(!churn_thread_error.load(), "snapshot_churn: stop_session() on a just-started session must succeed and not be already_stopped");
    expect(snapshot_ok.load(), "snapshot_churn: active_sessions must never exceed sessions_started_total (no torn read)");
    expect(churn_iterations.load() > 0, "snapshot_churn: churn thread should have made progress during the hammer window");
}

// ---------------------------------------------------------------------------
// Test 5: STOP-then-RESTART the SAME session id on the SAME listen port while a
// reaper thread hammers reap_once() (targets VG-16 stop-before-erase teardown
// ordering + VG-15 reaper-driven prompt teardown).
//
// Under TSan any data race, and under ASan any use-after-free / double-close in
// the teardown/erase/rebind interleaving, aborts. Functional invariant: because
// stop_session() now fully tears the old session down (sockets closed, threads
// joined) BEFORE releasing its id, a fresh start on the same port must always
// succeed to rebind — so every cycle reports Started.
// ---------------------------------------------------------------------------
void test_stop_start_same_id_port_race() {
    constexpr uint16_t kListenPort = 33900;
    constexpr uint16_t kRemotePort = 33901;
    constexpr int kCycles = 40;

    SessionRegistry registry;

    std::atomic<bool> stop_reaper{false};
    std::thread reaper([&registry, &stop_reaper]() {
        while (!stop_reaper.load()) {
            registry.reap_once();
            (void)registry.snapshot();
        }
    });

    int restarted_ok = 0;
    for (int i = 0; i < kCycles; ++i) {
        SessionConfig cfg = make_config("reuse", kListenPort, kRemotePort);
        cfg.watchdog_tick_ms = 50;  // fast worker wake -> bounded teardown join
        std::string err;
        if (registry.start_session(cfg, err) == StartSessionResult::Started) {
            ++restarted_ok;
        }
        bool already = false;
        registry.stop_session("reuse", "cycle", already);
    }

    stop_reaper.store(true);
    reaper.join();

    expect(restarted_ok == kCycles,
           "stop_start_same_id_port: every restart on the reused port must succeed after full teardown");
}

// ---------------------------------------------------------------------------
// Test 6: CONCURRENT start() vs stop() ON THE SAME SESSION (review #7).
//
// Before the lifecycle mutex, a stop() racing a concurrent start() could scan
// and join the std::thread members WHILE start() was assigning them (a TSan-
// visible data race), find nothing joinable, and leave three joinable threads
// behind — std::terminate at destruction. Now the epilogue and start()'s
// prepare/commit are serialized: whatever the interleaving, stop wins, all
// workers are joined, and destruction is clean. Any regression aborts the
// process (terminate/TSan/ASan) or hangs the joins (caught by the gate's
// process-level timeout).
// ---------------------------------------------------------------------------
void test_concurrent_start_stop_same_session() {
    constexpr int kIterations = 100;
    constexpr uint16_t kListenPort = 38500;
    constexpr uint16_t kRemotePort = 38501;

    for (int i = 0; i < kIterations; ++i) {
        auto session = std::make_shared<voice_gateway::RtpSession>(
            make_config("ss-race", kListenPort, kRemotePort));

        std::thread starter([session]() {
            std::string error;
            (void)session->start(error);  // may legitimately fail if stop won
        });
        std::thread stopper([session]() {
            session->stop("race_stop");
        });
        starter.join();
        stopper.join();

        // Whatever the interleaving: the session must end not-running with a
        // completed teardown, so this (idempotent) stop and the destructor are
        // clean. stop() returning implies teardown_done_ (loser-wait), so the
        // next iteration can rebind the same port immediately.
        session->stop("cleanup");
        expect(!session->running(), "start_stop_race: session must not be running after both racers finish");
    }
}

}  // namespace

int main() {
    int passed = 0;
    int failed = 0;

    const std::vector<std::pair<std::string, void (*)()>> tests = {
        {"concurrent_stop_races", test_concurrent_stop_races},
        {"start_stop_churn_with_live_neighbors", test_start_stop_churn_with_live_neighbors},
        {"reaper_self_stopped_session_not_erased_before_grace", test_reaper_self_stopped_session_not_erased_before_grace},
        {"concurrent_snapshot_during_churn", test_concurrent_snapshot_during_churn},
        {"stop_start_same_id_port_race", test_stop_start_same_id_port_race},
        {"concurrent_start_stop_same_session", test_concurrent_start_stop_same_session},
    };

    for (const auto& [name, fn] : tests) {
        try {
            fn();
            ++passed;
            std::cout << "[PASS] " << name << '\n';
        } catch (const std::exception& ex) {
            ++failed;
            std::cerr << "[FAIL] " << name << ": " << ex.what() << '\n';
        } catch (...) {
            ++failed;
            std::cerr << "[FAIL] " << name << ": unknown exception" << '\n';
        }
    }

    std::cout << "passed=" << passed << " failed=" << failed << '\n';
    return failed == 0 ? 0 : 1;
}
