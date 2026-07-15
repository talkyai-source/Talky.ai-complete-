#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_set>

#include "voice_gateway/session_registry.h"

namespace voice_gateway {

class HttpServer {
public:
    HttpServer(std::string host, uint16_t port, SessionRegistry& registry);
    ~HttpServer();

    HttpServer(const HttpServer&) = delete;
    HttpServer& operator=(const HttpServer&) = delete;

    bool start(std::string& error);
    void run();
    void stop();

    [[nodiscard]] bool healthy() const;

private:
    void handle_client(int client_fd);
    // Untrack + close the client fd and update the handler count/CV exactly once
    // (idempotent). Called by every handler exit path and by the RAII cleanup in
    // run(), so cleanup is guaranteed even if a handler throws.
    void finish_request(int client_fd);
    void close_listener();

    // Hard cap on concurrently-executing HTTP handlers. Each handler is a
    // thread; without a cap a connection flood spawns unbounded threads and
    // takes the process down (VG-04). Sized for control-plane + audio/TTS
    // callback traffic across all sessions with generous headroom.
    static constexpr int kMaxActiveHandlers = 256;

    std::string host_;
    uint16_t port_;
    SessionRegistry& registry_;

    std::atomic<bool> running_{false};
    std::atomic<bool> healthy_{true};
    std::atomic<int> active_handlers_{0};

    // Handler-lifetime ownership. stop() sets draining_, shuts down every tracked
    // client socket to unblock its handler, then waits on handlers_cv_ (NO
    // timeout escape) until active_client_fds_ is empty — so no detached handler
    // can touch this server / the registry after shutdown returns (VG-03).
    std::mutex handlers_mutex_;
    std::condition_variable handlers_cv_;
    std::unordered_set<int> active_client_fds_;
    bool draining_{false};

    int server_fd_{-1};
};

}  // namespace voice_gateway
