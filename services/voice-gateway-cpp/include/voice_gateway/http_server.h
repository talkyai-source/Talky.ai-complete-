#pragma once

#include <atomic>
#include <cstdint>
#include <string>

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

    int server_fd_{-1};
};

}  // namespace voice_gateway
