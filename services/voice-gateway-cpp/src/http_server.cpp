#include "voice_gateway/http_server.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace voice_gateway {

namespace {

struct HttpRequest {
    std::string method;
    std::string path;
    std::unordered_map<std::string, std::string> headers;
    std::string body;
};

std::string to_lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

std::string trim(const std::string& input) {
    std::size_t begin = 0;
    while (begin < input.size() && std::isspace(static_cast<unsigned char>(input[begin])) != 0) {
        ++begin;
    }

    std::size_t end = input.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(input[end - 1])) != 0) {
        --end;
    }

    return input.substr(begin, end - begin);
}

std::optional<HttpRequest> read_request(const int client_fd) {
    std::string raw;
    raw.reserve(4096);

    char buffer[2048];
    std::size_t header_end = std::string::npos;

    for (;;) {
        const ssize_t n = recv(client_fd, buffer, sizeof(buffer), 0);
        if (n <= 0) {
            return std::nullopt;
        }
        raw.append(buffer, static_cast<std::size_t>(n));

        header_end = raw.find("\r\n\r\n");
        if (header_end != std::string::npos) {
            break;
        }

        if (raw.size() > 1024 * 1024) {
            return std::nullopt;
        }
    }

    HttpRequest request;

    std::istringstream header_stream(raw.substr(0, header_end));
    std::string request_line;
    if (!std::getline(header_stream, request_line)) {
        return std::nullopt;
    }
    if (!request_line.empty() && request_line.back() == '\r') {
        request_line.pop_back();
    }

    {
        std::istringstream line_stream(request_line);
        std::string http_version;
        if (!(line_stream >> request.method >> request.path >> http_version)) {
            return std::nullopt;
        }
    }

    std::string header_line;
    while (std::getline(header_stream, header_line)) {
        if (!header_line.empty() && header_line.back() == '\r') {
            header_line.pop_back();
        }
        const auto delimiter = header_line.find(':');
        if (delimiter == std::string::npos) {
            continue;
        }
        std::string key = to_lower(trim(header_line.substr(0, delimiter)));
        std::string value = trim(header_line.substr(delimiter + 1));
        request.headers[key] = value;
    }

    std::size_t content_length = 0;
    const auto it = request.headers.find("content-length");
    if (it != request.headers.end()) {
        try {
            content_length = static_cast<std::size_t>(std::stoul(it->second));
        } catch (...) {
            return std::nullopt;
        }
    }

    const std::size_t body_offset = header_end + 4;
    if (raw.size() > body_offset) {
        request.body = raw.substr(body_offset);
    }

    while (request.body.size() < content_length) {
        const ssize_t n = recv(client_fd, buffer, sizeof(buffer), 0);
        if (n <= 0) {
            return std::nullopt;
        }
        request.body.append(buffer, static_cast<std::size_t>(n));
    }

    if (request.body.size() > content_length) {
        request.body.resize(content_length);
    }

    return request;
}

void write_response(const int client_fd, const int status_code, const std::string& status_text, const std::string& body) {
    std::ostringstream out;
    out << "HTTP/1.1 " << status_code << ' ' << status_text << "\r\n"
        << "Content-Type: application/json\r\n"
        << "Content-Length: " << body.size() << "\r\n"
        << "Connection: close\r\n"
        << "\r\n"
        << body;

    const std::string wire = out.str();
    const char* ptr = wire.c_str();
    std::size_t remaining = wire.size();

    while (remaining > 0) {
        const ssize_t n = send(client_fd, ptr, remaining, 0);
        if (n <= 0) {
            break;
        }
        ptr += n;
        remaining -= static_cast<std::size_t>(n);
    }
}

std::optional<std::size_t> json_value_offset(const std::string& body, const std::string& key) {
    const std::string quoted_key = "\"" + key + "\"";
    std::size_t pos = body.find(quoted_key);
    if (pos == std::string::npos) {
        return std::nullopt;
    }

    pos += quoted_key.size();
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos])) != 0) {
        ++pos;
    }
    if (pos >= body.size() || body[pos] != ':') {
        return std::nullopt;
    }
    ++pos;
    while (pos < body.size() && std::isspace(static_cast<unsigned char>(body[pos])) != 0) {
        ++pos;
    }
    if (pos >= body.size()) {
        return std::nullopt;
    }
    return pos;
}

std::optional<std::string> json_get_string(const std::string& body, const std::string& key) {
    const auto value_pos = json_value_offset(body, key);
    if (!value_pos.has_value() || body[value_pos.value()] != '"') {
        return std::nullopt;
    }

    std::string out;
    out.reserve(64);
    bool escaped = false;

    for (std::size_t i = value_pos.value() + 1; i < body.size(); ++i) {
        const char c = body[i];
        if (escaped) {
            switch (c) {
                case '"':
                case '\\':
                case '/':
                    out.push_back(c);
                    break;
                case 'b':
                    out.push_back('\b');
                    break;
                case 'f':
                    out.push_back('\f');
                    break;
                case 'n':
                    out.push_back('\n');
                    break;
                case 'r':
                    out.push_back('\r');
                    break;
                case 't':
                    out.push_back('\t');
                    break;
                default:
                    out.push_back(c);
                    break;
            }
            escaped = false;
            continue;
        }
        if (c == '\\') {
            escaped = true;
            continue;
        }
        if (c == '"') {
            return out;
        }
        out.push_back(c);
    }

    return std::nullopt;
}

std::optional<int> json_get_int(const std::string& body, const std::string& key) {
    const auto value_pos = json_value_offset(body, key);
    if (!value_pos.has_value()) {
        return std::nullopt;
    }

    std::size_t end = value_pos.value();
    while (end < body.size() && std::isdigit(static_cast<unsigned char>(body[end])) != 0) {
        ++end;
    }
    if (end == value_pos.value()) {
        return std::nullopt;
    }

    try {
        return std::stoi(body.substr(value_pos.value(), end - value_pos.value()));
    } catch (...) {
        return std::nullopt;
    }
}

std::optional<bool> json_get_bool(const std::string& body, const std::string& key) {
    const auto value_pos = json_value_offset(body, key);
    if (!value_pos.has_value()) {
        return std::nullopt;
    }
    if (body.compare(value_pos.value(), 4, "true") == 0) {
        return true;
    }
    if (body.compare(value_pos.value(), 5, "false") == 0) {
        return false;
    }
    return std::nullopt;
}

std::string escape_json(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (const char c : s) {
        if (c == '\\' || c == '"') {
            out.push_back('\\');
        }
        out.push_back(c);
    }
    return out;
}

std::string session_stats_json(const SessionStatsSnapshot& stats) {
    std::ostringstream out;
    out << "{"
        << "\"session_id\":\"" << escape_json(stats.session_id) << "\"," 
        << "\"state\":\"" << escape_json(stats.state) << "\"," 
        << "\"stop_reason\":\"" << escape_json(stats.stop_reason) << "\"," 
        << "\"packets_in\":" << stats.packets_in << ','
        << "\"packets_out\":" << stats.packets_out << ','
        << "\"bytes_in\":" << stats.bytes_in << ','
        << "\"bytes_out\":" << stats.bytes_out << ','
        << "\"invalid_packets\":" << stats.invalid_packets << ','
        << "\"dropped_packets\":" << stats.dropped_packets << ','
        << "\"last_rtp_rx_ms_ago\":" << stats.last_rtp_rx_ms_ago << ','
        << "\"last_rtp_tx_ms_ago\":" << stats.last_rtp_tx_ms_ago << ','
        << "\"tx_next_sequence\":" << stats.tx_next_sequence << ','
        << "\"tx_next_timestamp\":" << stats.tx_next_timestamp << ','
        << "\"tx_ssrc\":" << stats.tx_ssrc << ','
        << "\"rx_interarrival_jitter_ts_units\":" << stats.rx_interarrival_jitter_ts_units << ','
        << "\"rx_interarrival_jitter_ms\":" << stats.rx_interarrival_jitter_ms << ','
        << "\"jitter_buffer_depth_frames\":" << stats.jitter_buffer_depth_frames << ','
        << "\"jitter_buffer_overflow_drops\":" << stats.jitter_buffer_overflow_drops << ','
        << "\"jitter_buffer_late_drops\":" << stats.jitter_buffer_late_drops << ','
        << "\"duplicate_packets\":" << stats.duplicate_packets << ','
        << "\"out_of_order_packets\":" << stats.out_of_order_packets << ','
        << "\"timeout_events_total\":" << stats.timeout_events_total << ','
        << "\"tts_segments_started_total\":" << stats.tts_segments_started_total << ','
        << "\"tts_segments_completed_total\":" << stats.tts_segments_completed_total << ','
        << "\"tts_segments_interrupted_total\":" << stats.tts_segments_interrupted_total << ','
        << "\"tts_frames_enqueued_total\":" << stats.tts_frames_enqueued_total << ','
        << "\"tts_frames_sent_total\":" << stats.tts_frames_sent_total << ','
        << "\"tts_frames_dropped_total\":" << stats.tts_frames_dropped_total << ','
        << "\"tts_queue_depth_frames\":" << stats.tts_queue_depth_frames << ','
        << "\"tts_last_stop_reason\":\"" << escape_json(stats.tts_last_stop_reason) << "\""
        << "}";
    return out.str();
}

std::string process_stats_json(const ProcessStatsSnapshot& stats) {
    std::ostringstream out;
    out << "{"
        << "\"sessions_started_total\":" << stats.sessions_started_total << ','
        << "\"sessions_stopped_total\":" << stats.sessions_stopped_total << ','
        << "\"active_sessions\":" << stats.active_sessions << ','
        << "\"stopped_sessions\":" << stats.stopped_sessions << ','
        << "\"packets_in\":" << stats.packets_in << ','
        << "\"packets_out\":" << stats.packets_out << ','
        << "\"bytes_in\":" << stats.bytes_in << ','
        << "\"bytes_out\":" << stats.bytes_out << ','
        << "\"invalid_packets\":" << stats.invalid_packets << ','
        << "\"dropped_packets\":" << stats.dropped_packets << ','
        << "\"jitter_buffer_overflow_drops\":" << stats.jitter_buffer_overflow_drops << ','
        << "\"jitter_buffer_late_drops\":" << stats.jitter_buffer_late_drops << ','
        << "\"duplicate_packets\":" << stats.duplicate_packets << ','
        << "\"out_of_order_packets\":" << stats.out_of_order_packets << ','
        << "\"timeout_events_total\":" << stats.timeout_events_total << ','
        << "\"tts_segments_started_total\":" << stats.tts_segments_started_total << ','
        << "\"tts_segments_completed_total\":" << stats.tts_segments_completed_total << ','
        << "\"tts_segments_interrupted_total\":" << stats.tts_segments_interrupted_total << ','
        << "\"tts_frames_enqueued_total\":" << stats.tts_frames_enqueued_total << ','
        << "\"tts_frames_sent_total\":" << stats.tts_frames_sent_total << ','
        << "\"tts_frames_dropped_total\":" << stats.tts_frames_dropped_total << ','
        << "\"tts_queue_depth_frames\":" << stats.tts_queue_depth_frames
        << "}";
    return out.str();
}

std::string sessions_list_json(const std::vector<SessionStatsSnapshot>& sessions) {
    std::ostringstream out;
    out << "{\"sessions\":[";
    for (std::size_t i = 0; i < sessions.size(); ++i) {
        if (i > 0) {
            out << ",";
        }
        const auto& s = sessions[i];
        out << "{"
            << "\"session_id\":\"" << escape_json(s.session_id) << "\","
            << "\"state\":\"" << escape_json(s.state) << "\","
            << "\"stop_reason\":\"" << escape_json(s.stop_reason) << "\","
            << "\"tts_queue_depth_frames\":" << s.tts_queue_depth_frames
            << "}";
    }
    out << "]}";
    return out.str();
}

std::optional<std::vector<uint8_t>> base64_decode(const std::string& input) {
    static const std::string chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789+/";
    static const std::vector<int> table = [] {
        std::vector<int> values(256, -1);
        for (std::size_t i = 0; i < chars.size(); ++i) {
            values[static_cast<unsigned char>(chars[i])] = static_cast<int>(i);
        }
        return values;
    }();

    std::vector<uint8_t> output;
    int val = 0;
    int valb = -8;
    for (const unsigned char c : input) {
        if (std::isspace(c) != 0) {
            continue;
        }
        if (c == '=') {
            break;
        }
        const int decoded = table[c];
        if (decoded < 0) {
            return std::nullopt;
        }
        val = (val << 6) + decoded;
        valb += 6;
        if (valb >= 0) {
            output.push_back(static_cast<uint8_t>((val >> valb) & 0xFF));
            valb -= 8;
        }
    }
    return output;
}

std::string base64_encode(const std::vector<uint8_t>& input) {
    static const char* chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789+/";
    std::string output;
    output.reserve(((input.size() + 2) / 3) * 4);
    int val = 0;
    int valb = -6;
    for (const uint8_t c : input) {
        val = (val << 8) + static_cast<int>(c);
        valb += 8;
        while (valb >= 0) {
            output.push_back(chars[(val >> valb) & 0x3F]);
            valb -= 6;
        }
    }
    if (valb > -6) {
        output.push_back(chars[((val << 8) >> (valb + 8)) & 0x3F]);
    }
    while (output.size() % 4 != 0) {
        output.push_back('=');
    }
    return output;
}

// Send a fire-and-forget HTTP POST to the given URL with a JSON body.
// Runs synchronously; callers should dispatch to a thread if low latency is needed.
// Returns false on any network/parse error (silently drops the callback).
bool http_post(const std::string& url, const std::string& json_body) {
    // Parse URL: http://host:port/path
    if (url.size() < 8) {
        return false;
    }
    const std::size_t scheme_end = url.find("://");
    if (scheme_end == std::string::npos) {
        return false;
    }
    const std::size_t host_start = scheme_end + 3;
    const std::size_t path_start = url.find('/', host_start);
    const std::string host_port = (path_start == std::string::npos) ? url.substr(host_start) : url.substr(host_start, path_start - host_start);
    const std::string path = (path_start == std::string::npos) ? "/" : url.substr(path_start);

    std::string host = host_port;
    uint16_t port = 80;
    const std::size_t colon = host_port.rfind(':');
    if (colon != std::string::npos) {
        host = host_port.substr(0, colon);
        try {
            port = static_cast<uint16_t>(std::stoul(host_port.substr(colon + 1)));
        } catch (...) {
            return false;
        }
    }

    // Resolve and connect
    bool addr_ok = false;
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
        return false;
    }

    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return false;
    }

    // Connect with a short timeout
    timeval tv{0, 200000};  // 200 ms
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    if (connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) != 0) {
        close(fd);
        return false;
    }

    // Build HTTP/1.0 POST (no keep-alive, simple)
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.0\r\n"
        << "Host: " << host_port << "\r\n"
        << "Content-Type: application/json\r\n"
        << "Content-Length: " << json_body.size() << "\r\n"
        << "Connection: close\r\n"
        << "\r\n"
        << json_body;

    const std::string wire = req.str();
    const char* ptr = wire.c_str();
    std::size_t remaining = wire.size();
    while (remaining > 0) {
        const ssize_t sent = send(fd, ptr, remaining, 0);
        if (sent <= 0) {
            close(fd);
            return false;
        }
        ptr += sent;
        remaining -= static_cast<std::size_t>(sent);
    }

    // Drain response (just enough to not leave the server in CLOSE_WAIT)
    char drain[256];
    while (recv(fd, drain, sizeof(drain), 0) > 0) {}

    close(fd);
    return true;
}

std::optional<std::string> extract_session_id_from_path(const std::string& path) {
    static const std::string prefix = "/v1/sessions/";
    static const std::string suffix = "/stats";
    if (path.size() <= (prefix.size() + suffix.size())) {
        return std::nullopt;
    }
    if (path.rfind(prefix, 0) != 0) {
        return std::nullopt;
    }
    if (path.compare(path.size() - suffix.size(), suffix.size(), suffix) != 0) {
        return std::nullopt;
    }

    const std::size_t begin = prefix.size();
    const std::size_t end = path.size() - suffix.size();
    if (end <= begin) {
        return std::nullopt;
    }
    const std::string session_id = path.substr(begin, end - begin);
    if (session_id.find('/') != std::string::npos || session_id.empty()) {
        return std::nullopt;
    }
    return session_id;
}

}  // namespace

HttpServer::HttpServer(std::string host, uint16_t port, SessionRegistry& registry)
    : host_(std::move(host)), port_(port), registry_(registry) {}

HttpServer::~HttpServer() {
    stop();
}

bool HttpServer::start(std::string& error) {
    if (running_.load()) {
        return true;
    }

    server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd_ < 0) {
        error = std::string("failed to create server socket: ") + std::strerror(errno);
        healthy_.store(false);
        return false;
    }

    int reuse = 1;
    setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port_);

    if (inet_pton(AF_INET, host_.c_str(), &addr.sin_addr) != 1) {
        error = "invalid host IP";
        healthy_.store(false);
        close_listener();
        return false;
    }

    if (bind(server_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        error = std::string("failed to bind HTTP socket: ") + std::strerror(errno);
        healthy_.store(false);
        close_listener();
        return false;
    }

    if (listen(server_fd_, 64) < 0) {
        error = std::string("failed to listen on HTTP socket: ") + std::strerror(errno);
        healthy_.store(false);
        close_listener();
        return false;
    }

    running_.store(true);
    healthy_.store(true);
    return true;
}

void HttpServer::run() {
    while (running_.load()) {
        sockaddr_in client_addr{};
        socklen_t client_len = sizeof(client_addr);
        const int client_fd = accept(server_fd_, reinterpret_cast<sockaddr*>(&client_addr), &client_len);

        if (client_fd < 0) {
            if (!running_.load()) {
                break;
            }
            if (errno == EINTR) {
                continue;
            }
            healthy_.store(false);
            continue;
        }

        std::thread(&HttpServer::handle_client, this, client_fd).detach();
    }
}

void HttpServer::stop() {
    running_.store(false);
    close_listener();
}

bool HttpServer::healthy() const {
    if (!healthy_.load()) {
        return false;
    }
    return registry_.all_sessions_healthy();
}

void HttpServer::handle_client(const int client_fd) {
    const auto request = read_request(client_fd);
    if (!request.has_value()) {
        write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_http_request\"}");
        close(client_fd);
        return;
    }

    if (request->method == "GET" && request->path == "/health") {
        const bool is_healthy = healthy();
        const std::string body = std::string("{\"status\":\"") + (is_healthy ? "ok" : "degraded") + "\",\"io_loop_healthy\":" + (is_healthy ? "true" : "false") + "}";
        write_response(client_fd, is_healthy ? 200 : 503, is_healthy ? "OK" : "Service Unavailable", body);
        close(client_fd);
        return;
    }

    if (request->method == "GET" && request->path == "/stats") {
        write_response(client_fd, 200, "OK", process_stats_json(registry_.snapshot()));
        close(client_fd);
        return;
    }

    if (request->method == "GET" && request->path == "/v1/sessions") {
        write_response(client_fd, 200, "OK", sessions_list_json(registry_.list_sessions()));
        close(client_fd);
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/start") {
        SessionConfig config;

        const auto session_id = json_get_string(request->body, "session_id");
        const auto listen_ip = json_get_string(request->body, "listen_ip");
        const auto listen_port = json_get_int(request->body, "listen_port");
        const auto remote_ip = json_get_string(request->body, "remote_ip");
        const auto remote_port = json_get_int(request->body, "remote_port");
        const auto codec = json_get_string(request->body, "codec");
        const auto ptime_ms = json_get_int(request->body, "ptime_ms");
        const auto startup_no_rtp_timeout_ms = json_get_int(request->body, "startup_no_rtp_timeout_ms");
        const auto active_no_rtp_timeout_ms = json_get_int(request->body, "active_no_rtp_timeout_ms");
        const auto hold_no_rtp_timeout_ms = json_get_int(request->body, "hold_no_rtp_timeout_ms");
        const auto session_final_timeout_ms = json_get_int(request->body, "session_final_timeout_ms");
        const auto watchdog_tick_ms = json_get_int(request->body, "watchdog_tick_ms");
        const auto jitter_buffer_enabled = json_get_bool(request->body, "jitter_buffer_enabled");
        const auto jitter_buffer_capacity_frames = json_get_int(request->body, "jitter_buffer_capacity_frames");
        const auto jitter_buffer_prefetch_frames = json_get_int(request->body, "jitter_buffer_prefetch_frames");
        const auto echo_enabled = json_get_bool(request->body, "echo_enabled");
        const auto tts_max_queue_frames = json_get_int(request->body, "tts_max_queue_frames");
        const auto audio_callback_url = json_get_string(request->body, "audio_callback_url");
        const auto audio_callback_batch_frames = json_get_int(request->body, "audio_callback_batch_frames");

        if (!session_id.has_value() || !listen_ip.has_value() || !listen_port.has_value() ||
            !remote_ip.has_value() || !remote_port.has_value() || !codec.has_value() || !ptime_ms.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_required_start_fields\"}");
            close(client_fd);
            return;
        }

        if (listen_port.value() <= 0 || listen_port.value() > 65535 ||
            remote_port.value() <= 0 || remote_port.value() > 65535) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_port_range\"}");
            close(client_fd);
            return;
        }

        config.session_id = session_id.value();
        config.listen_ip = listen_ip.value();
        config.remote_ip = remote_ip.value();
        config.codec = codec.value();
        config.listen_port = static_cast<uint16_t>(listen_port.value());
        config.remote_port = static_cast<uint16_t>(remote_port.value());
        config.ptime_ms = ptime_ms.value();

        if (startup_no_rtp_timeout_ms.has_value()) {
            config.startup_no_rtp_timeout_ms = startup_no_rtp_timeout_ms.value();
        }
        if (active_no_rtp_timeout_ms.has_value()) {
            config.active_no_rtp_timeout_ms = active_no_rtp_timeout_ms.value();
        }
        if (hold_no_rtp_timeout_ms.has_value()) {
            config.hold_no_rtp_timeout_ms = hold_no_rtp_timeout_ms.value();
        }
        if (session_final_timeout_ms.has_value()) {
            config.session_final_timeout_ms = session_final_timeout_ms.value();
        }
        if (watchdog_tick_ms.has_value()) {
            config.watchdog_tick_ms = watchdog_tick_ms.value();
        }
        if (jitter_buffer_enabled.has_value()) {
            config.jitter_buffer_enabled = jitter_buffer_enabled.value();
        }
        if (jitter_buffer_capacity_frames.has_value()) {
            if (jitter_buffer_capacity_frames.value() <= 0) {
                write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_jitter_buffer_capacity_frames\"}");
                close(client_fd);
                return;
            }
            config.jitter_buffer_capacity_frames = static_cast<std::size_t>(jitter_buffer_capacity_frames.value());
        }
        if (jitter_buffer_prefetch_frames.has_value()) {
            if (jitter_buffer_prefetch_frames.value() <= 0) {
                write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_jitter_buffer_prefetch_frames\"}");
                close(client_fd);
                return;
            }
            config.jitter_buffer_prefetch_frames = static_cast<std::size_t>(jitter_buffer_prefetch_frames.value());
        }
        if (echo_enabled.has_value()) {
            config.echo_enabled = echo_enabled.value();
        }
        if (tts_max_queue_frames.has_value()) {
            if (tts_max_queue_frames.value() <= 0) {
                write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_tts_max_queue_frames\"}");
                close(client_fd);
                return;
            }
            config.tts_max_queue_frames = static_cast<std::size_t>(tts_max_queue_frames.value());
        }
        if (audio_callback_url.has_value()) {
            config.audio_callback_url = audio_callback_url.value();
        }
        if (audio_callback_batch_frames.has_value() && audio_callback_batch_frames.value() > 0) {
            config.audio_callback_batch_frames = audio_callback_batch_frames.value();
        }

        std::string error;
        const auto result = registry_.start_session(config, error);

        if (result == StartSessionResult::Started) {
            // If an audio_callback_url was requested, register it on the live session.
            // The callback POSTs each received audio frame as base64 JSON to the backend.
            if (!config.audio_callback_url.empty()) {
                const auto session = registry_.get_session(config.session_id);
                if (session) {
                    const std::string cb_url = config.audio_callback_url;
                    const std::string cb_session_id = config.session_id;
                    const int batch_frames = config.audio_callback_batch_frames;

                    // Accumulate frames in a thread-local deque; POST when batch is full.
                    // Each RtpSession gets its own lambda closure (no shared state between sessions).
                    struct BatchState {
                        std::vector<uint8_t> buffer;
                        int frame_count{0};
                    };
                    auto state = std::make_shared<BatchState>();

                    session->set_audio_callback(
                        [cb_url, cb_session_id, batch_frames, state](
                            const std::string& /*sid*/, const std::vector<uint8_t>& pcmu) {
                            state->buffer.insert(state->buffer.end(), pcmu.begin(), pcmu.end());
                            state->frame_count++;
                            if (state->frame_count >= batch_frames) {
                                const std::string b64 = base64_encode(state->buffer);
                                const std::string body =
                                    "{\"session_id\":\"" + escape_json(cb_session_id) +
                                    "\",\"pcmu_base64\":\"" + b64 +
                                    "\",\"codec\":\"pcmu\"}";
                                state->buffer.clear();
                                state->frame_count = 0;
                                // Dispatch HTTP POST on a detached thread so the
                                // receiver loop is never blocked by network I/O.
                                std::thread([cb_url, body]() {
                                    http_post(cb_url, body);
                                }).detach();
                            }
                        });
                }
            }

            write_response(client_fd, 200, "OK", "{\"status\":\"started\",\"session_id\":\"" + escape_json(config.session_id) + "\"}");
            close(client_fd);
            return;
        }

        if (result == StartSessionResult::AlreadyExists) {
            write_response(client_fd, 409, "Conflict", "{\"status\":\"already_exists\",\"error\":\"" + escape_json(error) + "\"}");
            close(client_fd);
            return;
        }

        if (result == StartSessionResult::InternalError) {
            write_response(client_fd, 500, "Internal Server Error", "{\"status\":\"failed\",\"error\":\"" + escape_json(error) + "\"}");
            close(client_fd);
            return;
        }

        write_response(client_fd, 400, "Bad Request", "{\"status\":\"failed\",\"error\":\"" + escape_json(error) + "\"}");
        close(client_fd);
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/stop") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto reason = json_get_string(request->body, "reason");
        if (!session_id.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_session_id\"}");
            close(client_fd);
            return;
        }

        bool already_stopped = false;
        registry_.stop_session(session_id.value(), reason.value_or("stopped_by_request"), already_stopped);
        if (already_stopped) {
            write_response(client_fd, 200, "OK", "{\"status\":\"already_stopped\",\"session_id\":\"" + escape_json(session_id.value()) + "\"}");
            close(client_fd);
            return;
        }

        write_response(client_fd, 200, "OK", "{\"status\":\"stopped\",\"session_id\":\"" + escape_json(session_id.value()) + "\"}");
        close(client_fd);
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/tts/play") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto pcmu_base64 = json_get_string(request->body, "pcmu_base64");
        const auto clear_existing = json_get_bool(request->body, "clear_existing");
        if (!session_id.has_value() || !pcmu_base64.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_tts_play_fields\"}");
            close(client_fd);
            return;
        }

        const auto session = registry_.get_session(session_id.value());
        if (!session) {
            write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
            close(client_fd);
            return;
        }

        const auto decoded = base64_decode(pcmu_base64.value());
        if (!decoded.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_pcmu_base64\"}");
            close(client_fd);
            return;
        }

        std::string error;
        std::size_t queued_frames = 0;
        if (!session->enqueue_tts_ulaw(decoded.value(), clear_existing.value_or(false), queued_frames, error)) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"" + escape_json(error) + "\"}");
            close(client_fd);
            return;
        }

        const auto snap = session->snapshot();
        write_response(
            client_fd,
            200,
            "OK",
            "{\"status\":\"queued\",\"session_id\":\"" + escape_json(session_id.value()) +
                "\",\"queued_frames\":" + std::to_string(queued_frames) +
                ",\"tts_queue_depth_frames\":" + std::to_string(snap.tts_queue_depth_frames) + "}");
        close(client_fd);
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/tts/interrupt") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto reason = json_get_string(request->body, "reason");
        if (!session_id.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_session_id\"}");
            close(client_fd);
            return;
        }

        const auto session = registry_.get_session(session_id.value());
        if (!session) {
            write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
            close(client_fd);
            return;
        }

        std::size_t dropped_frames = 0;
        std::size_t interrupted_segments = 0;
        session->interrupt_tts(reason.value_or("barge_in"), dropped_frames, interrupted_segments);
        write_response(
            client_fd,
            200,
            "OK",
            "{\"status\":\"interrupted\",\"session_id\":\"" + escape_json(session_id.value()) +
                "\",\"dropped_frames\":" + std::to_string(dropped_frames) +
                ",\"interrupted_segments\":" + std::to_string(interrupted_segments) + "}");
        close(client_fd);
        return;
    }

    if (request->method == "GET") {
        const auto session_id = extract_session_id_from_path(request->path);
        if (session_id.has_value()) {
            const auto session = registry_.get_session(session_id.value());
            if (!session) {
                write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
                close(client_fd);
                return;
            }
            write_response(client_fd, 200, "OK", session_stats_json(session->snapshot()));
            close(client_fd);
            return;
        }
    }

    write_response(client_fd, 404, "Not Found", "{\"error\":\"route_not_found\"}");
    close(client_fd);
}

void HttpServer::close_listener() {
    if (server_fd_ >= 0) {
        shutdown(server_fd_, SHUT_RDWR);
        close(server_fd_);
        server_fd_ = -1;
    }
}

}  // namespace voice_gateway
