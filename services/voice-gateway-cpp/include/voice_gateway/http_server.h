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

    std::string host_;
    uint16_t port_;
    SessionRegistry& registry_;

    std::atomic<bool> running_{false};
    std::atomic<bool> healthy_{true};

    int server_fd_{-1};
};

}  // namespace voice_gateway
