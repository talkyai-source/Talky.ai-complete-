#include "voice_gateway/http_server.h"

#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

voice_gateway::HttpServer* g_server = nullptr;

void handle_signal(const int) {
    if (g_server != nullptr) {
        g_server->stop();
    }
}

uint16_t parse_port(const std::string& value) {
    // Reject any trailing/non-digit junk ("18080abc" used to parse as 18080)
    // (VG-35).
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos) {
        throw std::out_of_range("port must be a positive integer");
    }
    const unsigned long parsed = std::stoul(value);
    if (parsed == 0 || parsed > 65535) {
        throw std::out_of_range("port out of range");
    }
    return static_cast<uint16_t>(parsed);
}

}  // namespace

int main(int argc, char** argv) {
    std::string host = "127.0.0.1";
    uint16_t port = 18080;

    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg == "--host" && i + 1 < argc) {
            host = argv[++i];
            continue;
        }
        if (arg == "--port" && i + 1 < argc) {
            try {
                port = parse_port(argv[++i]);
            } catch (...) {
                std::cerr << "Invalid --port value" << std::endl;
                return 2;
            }
            continue;
        }
        if (arg == "--help") {
            std::cout << "Usage: voice_gateway [--host 127.0.0.1] [--port 18080]" << std::endl;
            return 0;
        }
        // Fail closed on anything unrecognized or a flag missing its value,
        // rather than silently starting on defaults (VG-35).
        std::cerr << "Unknown or malformed argument: " << arg << std::endl;
        std::cerr << "Usage: voice_gateway [--host 127.0.0.1] [--port 18080]" << std::endl;
        return 2;
    }

    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server(host, port, registry);

    std::string error;
    if (!server.start(error)) {
        std::cerr << "Failed to start HTTP server: " << error << std::endl;
        return 1;
    }

    g_server = &server;
    // A peer that resets the TCP connection mid-write would otherwise raise
    // SIGPIPE, whose default disposition terminates the whole gateway and drops
    // every live call. Ignore it process-wide; write paths additionally pass
    // MSG_NOSIGNAL and handle EPIPE/ECONNRESET locally (VG-14).
    std::signal(SIGPIPE, SIG_IGN);
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    std::cout << "voice-gateway-cpp started"
              << " host=" << host
              << " port=" << port
              << " codec=pcmu"
              << " ptime_ms=20"
              << std::endl;

    server.run();

    std::cout << "voice-gateway-cpp stopped" << std::endl;
    return 0;
}
