#include "voice_gateway/http_server.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cctype>
#include <charconv>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iostream>
#include <list>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace voice_gateway {

namespace {

// Aggregate in-flight request-body budget (D remainder). The per-request 8 MiB
// cap bounds ONE request, but 256 concurrent handlers x 8 MiB is ~2 GiB — a
// coordinated flood of maximum-size bodies could OOM the process while every
// individual request stayed "valid". This charges each body's declared length
// against one process-wide budget for as long as the body is alive (charged
// before the body read, released when the HttpRequest is destroyed at the end
// of its handler) and rejects with 503 once the budget is exhausted.
std::atomic<std::size_t> g_inflight_body_bytes{0};

std::size_t max_inflight_body_bytes() {
    // Overridable for tests/deployments; read once. Default 64 MiB: eight
    // maximum-size bodies in flight, far above legitimate concurrent TTS load.
    static const std::size_t budget = [] {
        constexpr std::size_t kDefault = 64u * 1024u * 1024u;
        const char* v = std::getenv("VOICE_GATEWAY_MAX_INFLIGHT_BODY_BYTES");
        if (v == nullptr || *v == '\0') {
            return kDefault;
        }
        char* end = nullptr;
        const unsigned long long parsed = std::strtoull(v, &end, 10);
        if (end == v || (end != nullptr && *end != '\0') || parsed == 0) {
            return kDefault;
        }
        return static_cast<std::size_t>(parsed);
    }();
    return budget;
}

// RAII release of one body's budget charge. Held via shared_ptr inside the
// HttpRequest so copies/moves of the request keep exactly one charge alive.
struct BodyBudgetCharge {
    std::size_t bytes{0};
    explicit BodyBudgetCharge(const std::size_t n) : bytes(n) {}
    ~BodyBudgetCharge() {
        if (bytes > 0) {
            g_inflight_body_bytes.fetch_sub(bytes);
        }
    }
    BodyBudgetCharge(const BodyBudgetCharge&) = delete;
    BodyBudgetCharge& operator=(const BodyBudgetCharge&) = delete;
};

struct HttpRequest {
    std::string method;
    std::string path;
    std::unordered_map<std::string, std::string> headers;
    std::string body;
    std::shared_ptr<BodyBudgetCharge> budget_charge;  // released with the request
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

// Milliseconds left until `deadline`, floored at 0 (for poll timeouts).
int remaining_until_ms(const std::chrono::steady_clock::time_point& deadline) {
    const auto now = std::chrono::steady_clock::now();
    if (now >= deadline) {
        return 0;
    }
    return static_cast<int>(
        std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
}

// Read from a NON-BLOCKING fd within an absolute deadline. Returns bytes read,
// 0 on orderly EOF, or -1 on deadline/error. Because the socket is genuinely
// non-blocking and the wait is a poll() against the REMAINING deadline, the
// whole-operation bound holds exactly — a blocking recv started just before the
// deadline can no longer overshoot it by a full socket timeout (review #11).
ssize_t recv_within(const int fd, char* buf, const std::size_t len,
                    const std::chrono::steady_clock::time_point& deadline) {
    for (;;) {
        const int remaining = remaining_until_ms(deadline);
        if (remaining <= 0) {
            return -1;
        }
        pollfd pfd{fd, POLLIN, 0};
        const int pr = poll(&pfd, 1, remaining);
        if (pr < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (pr == 0) {
            return -1;  // deadline expired
        }
        const ssize_t n = recv(fd, buf, len, 0);
        if (n >= 0) {
            return n;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
            continue;
        }
        return -1;
    }
}

std::optional<HttpRequest> read_request(const int client_fd, bool& over_budget) {
    over_budget = false;
    std::string raw;
    raw.reserve(4096);

    char buffer[2048];
    std::size_t header_end = std::string::npos;

    // Absolute deadline for the WHOLE request read, enforced by poll() against
    // the remaining time on a non-blocking socket. The previous version used
    // blocking recv + SO_RCVTIMEO, so each recv could overshoot the deadline by
    // the socket timeout and a terminator arriving after the deadline was still
    // accepted (review #11). Now the bound is exact.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);

    for (;;) {
        const ssize_t n = recv_within(client_fd, buffer, sizeof(buffer), deadline);
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

    // Cap the declared body so a large (or bogus) Content-Length cannot drive
    // unbounded body accumulation and exhaust RAM (VG-04). 8 MiB is far above any
    // legitimate base64 TTS chunk while bounding the worst case.
    constexpr std::size_t kMaxBodyBytes = 8u * 1024u * 1024u;
    if (content_length > kMaxBodyBytes) {
        return std::nullopt;
    }

    // Aggregate budget (D remainder): charge the declared length before the body
    // is read; the charge lives as long as the returned request. fetch_add-then-
    // check keeps the reservation atomic across concurrent handlers (a loser
    // rolls its own addition back).
    std::shared_ptr<BodyBudgetCharge> charge;
    if (content_length > 0) {
        const std::size_t new_total = g_inflight_body_bytes.fetch_add(content_length) + content_length;
        if (new_total > max_inflight_body_bytes()) {
            g_inflight_body_bytes.fetch_sub(content_length);
            over_budget = true;
            return std::nullopt;
        }
        try {
            charge = std::make_shared<BodyBudgetCharge>(content_length);
        } catch (...) {
            g_inflight_body_bytes.fetch_sub(content_length);
            return std::nullopt;
        }
    }

    const std::size_t body_offset = header_end + 4;
    if (raw.size() > body_offset) {
        request.body = raw.substr(body_offset);
    }

    while (request.body.size() < content_length) {
        const ssize_t n = recv_within(client_fd, buffer, sizeof(buffer), deadline);
        if (n <= 0) {
            return std::nullopt;  // EOF, error, or slowloris body trickle (#11)
        }
        request.body.append(buffer, static_cast<std::size_t>(n));
    }

    if (request.body.size() > content_length) {
        request.body.resize(content_length);
    }

    request.budget_charge = std::move(charge);
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

    // Absolute deadline enforced with poll() on the non-blocking socket, so a
    // client that stops reading cannot pin the handler on the write side beyond
    // the bound — not even by one blocking send() (review #11).
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);

    while (remaining > 0) {
        const int wait_ms = remaining_until_ms(deadline);
        if (wait_ms <= 0) {
            break;
        }
        pollfd pfd{client_fd, POLLOUT, 0};
        const int pr = poll(&pfd, 1, wait_ms);
        if (pr < 0 && errno == EINTR) {
            continue;
        }
        if (pr <= 0) {
            break;  // deadline or poll error
        }
        // MSG_NOSIGNAL: a peer reset must surface as EPIPE here, never as a
        // process-killing SIGPIPE (VG-14).
        const ssize_t n = send(client_fd, ptr, remaining, MSG_NOSIGNAL);
        if (n > 0) {
            ptr += n;
            remaining -= static_cast<std::size_t>(n);
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            continue;
        }
        break;  // peer reset / hard error
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
    // Escape ", \, and ALL control characters. The previous version escaped only
    // quote/backslash, so a newline (or other control char) in a session_id or
    // reason produced structurally invalid JSON on the wire and allowed log
    // injection via embedded CR/LF (VG-29).
    static const char* hex = "0123456789abcdef";
    std::string out;
    out.reserve(s.size() + 8);
    for (const unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    out += "\\u00";
                    out.push_back(hex[(c >> 4) & 0xF]);
                    out.push_back(hex[c & 0xF]);
                } else {
                    out.push_back(static_cast<char>(c));
                }
                break;
        }
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
        << "\"tts_last_stop_reason\":\"" << escape_json(stats.tts_last_stop_reason) << "\","
        << "\"stt_frames_emitted_total\":" << stats.stt_frames_emitted_total << ','
        << "\"stt_floor_dropped_total\":" << stats.stt_floor_dropped_total << ','
        << "\"stt_probation_dropped_total\":" << stats.stt_probation_dropped_total << ','
        << "\"stt_restarts_committed_total\":" << stats.stt_restarts_committed_total << ','
        << "\"tts_chunks_rejected_stale_total\":" << stats.tts_chunks_rejected_stale_total
        << "}";
    return out.str();
}

std::string process_stats_json(const ProcessStatsSnapshot& stats) {
    std::ostringstream out;
    out << "{"
        << "\"sessions_started_total\":" << stats.sessions_started_total << ','
        << "\"sessions_stopped_total\":" << stats.sessions_stopped_total << ','
        << "\"sessions_reaped_total\":" << stats.sessions_reaped_total << ','
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
        << "\"tts_queue_depth_frames\":" << stats.tts_queue_depth_frames << ','
        << "\"stt_frames_emitted_total\":" << stats.stt_frames_emitted_total << ','
        << "\"stt_floor_dropped_total\":" << stats.stt_floor_dropped_total << ','
        << "\"stt_probation_dropped_total\":" << stats.stt_probation_dropped_total << ','
        << "\"stt_restarts_committed_total\":" << stats.stt_restarts_committed_total << ','
        << "\"tts_chunks_rejected_stale_total\":" << stats.tts_chunks_rejected_stale_total
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
    // Unsigned, masked accumulator. The previous `int val = (val << 6) + …`
    // overflowed the signed int after only a few base64 chars (guaranteed on any
    // 160-byte audio frame) — undefined behavior under g++ -O2 even where it
    // happened to wrap correctly (VG-09). uint32 shifts are well-defined; the
    // 24-bit mask bounds the live window (we never read above bit ~12).
    uint32_t val = 0;
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
        val = ((val << 6) | static_cast<uint32_t>(decoded)) & 0xFFFFFFu;
        valb += 6;
        if (valb >= 0) {
            output.push_back(static_cast<uint8_t>((val >> valb) & 0xFFu));
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
    // Unsigned, masked accumulator — same signed-overflow UB fix as the decoder
    // (VG-09). The tail shifts (val << 8) which fits in uint32 given the 24-bit
    // mask; the previous signed int overflowed on any multi-byte input.
    uint32_t val = 0;
    int valb = -6;
    for (const uint8_t c : input) {
        val = ((val << 8) | static_cast<uint32_t>(c)) & 0xFFFFFFu;
        valb += 8;
        while (valb >= 0) {
            output.push_back(chars[(val >> valb) & 0x3Fu]);
            valb -= 6;
        }
    }
    if (valb > -6) {
        output.push_back(chars[((val << 8) >> (valb + 8)) & 0x3Fu]);
    }
    while (output.size() % 4 != 0) {
        output.push_back('=');
    }
    return output;
}

// Owns a raw fd for the duration of a scope so an exception thrown anywhere in
// http_post (e.g. std::bad_alloc appending to the response string) can no
// longer leak the socket (review: sender-exception socket leak).
struct FdGuard {
    int fd{-1};
    ~FdGuard() {
        if (fd >= 0) {
            ::close(fd);
        }
    }
    FdGuard() = default;
    FdGuard(const FdGuard&) = delete;
    FdGuard& operator=(const FdGuard&) = delete;
};

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
        // Range-checked: the old unchecked stoul->uint16_t cast silently
        // truncated ":65537" to port 1 (review #13).
        try {
            const unsigned long parsed = std::stoul(host_port.substr(colon + 1));
            if (parsed == 0 || parsed > 65535) {
                return false;
            }
            port = static_cast<uint16_t>(parsed);
        } catch (...) {
            return false;
        }
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
        return false;
    }

    // Build the request BEFORE creating the socket so its allocations cannot
    // leak an open fd; everything after socket() is covered by FdGuard.
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.0\r\n"
        << "Host: " << host_port << "\r\n"
        << "Content-Type: application/json\r\n"
        << "Content-Length: " << json_body.size() << "\r\n"
        << "Connection: close\r\n"
        << "\r\n"
        << json_body;
    const std::string wire = req.str();

    // SOCK_NONBLOCK at creation: non-blocking mode is now guaranteed, not a
    // best-effort fcntl whose failure silently reverted connect() to a blocking
    // call with the kernel's minutes-long TCP timeout — which would defeat the
    // deadline and stall session teardown behind it (review #10).
    FdGuard guard;
    guard.fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (guard.fd < 0) {
        return false;
    }
    const int fd = guard.fd;

    // ONE absolute deadline covers the whole exchange: nonblocking connect ->
    // full request write -> status-line read. Per-operation SO_*TIMEO would let a
    // peer that trickles one byte just under each timeout keep the call alive
    // indefinitely and block session teardown (VG-10 / finding #7).
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);

    // Nonblocking connect, bounded by the deadline.
    if (connect(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) != 0) {
        if (errno != EINPROGRESS) {
            return false;
        }
        for (;;) {
            const int wait_ms = remaining_until_ms(deadline);
            if (wait_ms <= 0) {
                return false;
            }
            pollfd pfd{fd, POLLOUT, 0};
            const int pr = poll(&pfd, 1, wait_ms);
            if (pr < 0 && errno == EINTR) {
                continue;
            }
            if (pr <= 0) {
                return false;
            }
            break;
        }
        int soerr = 0;
        socklen_t soerr_len = sizeof(soerr);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &soerr_len) != 0 || soerr != 0) {
            return false;
        }
    }

    // Write the full request within the deadline. MSG_NOSIGNAL: a backend reset
    // must return EPIPE, not kill the gateway (VG-14).
    std::size_t off = 0;
    while (off < wire.size()) {
        const int wait_ms = remaining_until_ms(deadline);
        if (wait_ms <= 0) {
            return false;
        }
        pollfd pfd{fd, POLLOUT, 0};
        const int pr = poll(&pfd, 1, wait_ms);
        if (pr < 0 && errno == EINTR) {
            continue;
        }
        if (pr <= 0) {
            return false;
        }
        const ssize_t s = send(fd, wire.data() + off, wire.size() - off, MSG_NOSIGNAL);
        if (s > 0) {
            off += static_cast<std::size_t>(s);
        } else if (s < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            continue;
        } else {
            return false;
        }
    }

    // Read up to the status line (first newline) within the deadline; do NOT
    // wait for the body / connection close once a valid status is available.
    // Previously the response was drained without inspection and always returned
    // true, so a backend 500/timeout/reset looked identical to success — silent
    // STT loss (VG-10). Non-2xx (or no parseable status) is now a failure.
    std::string resp;
    while (resp.find('\n') == std::string::npos && resp.size() < 512) {
        const int wait_ms = remaining_until_ms(deadline);
        if (wait_ms <= 0) {
            return false;
        }
        pollfd pfd{fd, POLLIN, 0};
        const int pr = poll(&pfd, 1, wait_ms);
        if (pr < 0 && errno == EINTR) {
            continue;
        }
        if (pr <= 0) {
            return false;
        }
        char rbuf[256];
        const ssize_t r = recv(fd, rbuf, sizeof(rbuf), 0);
        if (r > 0) {
            resp.append(rbuf, static_cast<std::size_t>(r));
        } else if (r == 0) {
            break;  // peer closed
        } else if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
            continue;
        } else {
            return false;
        }
    }

    // Strict status parse (review #12): require a COMPLETE status line
    // ("HTTP/x.y NNN ..." terminated by \n) and exactly three digits parsed via
    // from_chars — "HTTP/1.1 200" cut off by EOF, "200junk", or a hundreds-of-
    // digits status (previously undefined behavior through atoi) all fail.
    const std::size_t eol = resp.find('\n');
    if (eol == std::string::npos) {
        return false;
    }
    if (resp.compare(0, 5, "HTTP/") != 0) {
        return false;
    }
    const std::size_t sp = resp.find(' ');
    if (sp == std::string::npos || sp + 3 >= eol) {
        return false;
    }
    const char* digits = resp.data() + sp + 1;
    int status = 0;
    const auto [end_ptr, ec] = std::from_chars(digits, digits + 3, status);
    if (ec != std::errc() || end_ptr != digits + 3) {
        return false;
    }
    const char after = resp[sp + 4];  // sp+4 <= eol, so in-bounds
    if (after != ' ' && after != '\r' && after != '\n') {
        return false;  // "200junk" or a 4th digit is not a valid status field
    }
    return status >= 200 && status < 300;
}

// One long-lived sender thread per session that drains a bounded FIFO queue of
// pre-built JSON bodies, POSTing them sequentially. Replaces the old model of
// spawning a fresh detached thread + new TCP connection per ~40ms audio batch,
// which let batch N+1 overtake a stalled batch N and deliver caller audio to
// the STT backend OUT OF ORDER (garbling correctness-critical email/number
// captures), while churning TIME_WAIT sockets and spawning unbounded threads.
//
// Ordering guarantee: a single worker thread performs every POST in strict
// dequeue (FIFO) order, one connection at a time, so the backend always
// receives batch N before batch N+1.
//
// Overflow policy: DROP-OLDEST once the queue reaches kMaxQueue. For realtime
// audio a bounded, near-live stream is better than an ever-growing backlog: if
// the backend stalls, we shed the stalest frames rather than block the RTP
// receiver thread (enqueue is non-blocking) or grow memory without bound. The
// tradeoff is a gap in the audio during a stall (some frames lost) instead of
// mounting latency; for STT this keeps transcription near-real-time.
class AudioCallbackSender {
public:
    // ~5s of buffering at ~50 POST/s before shedding; ~256 * a few hundred
    // bytes of JSON is tens of KB, safely bounded per session.
    static constexpr std::size_t kMaxQueue = 256;

    explicit AudioCallbackSender(std::string url)
        : url_(std::move(url)), worker_([this] { run(); }) {}

    ~AudioCallbackSender() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = true;
        }
        cv_.notify_all();
        if (worker_.joinable()) {
            worker_.join();
        }
    }

    AudioCallbackSender(const AudioCallbackSender&) = delete;
    AudioCallbackSender& operator=(const AudioCallbackSender&) = delete;

    // Called from the RTP receiver thread. Non-blocking: never performs
    // network I/O and never blocks the receiver loop.
    void enqueue(std::string body) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (stop_ || finishing_) {
                return;
            }
            while (queue_.size() >= kMaxQueue) {
                queue_.pop_front();  // drop-oldest
            }
            queue_.push_back(std::move(body));
        }
        cv_.notify_one();
    }

    // Batch E: drain the remaining queue (the caller's final words) with a hard
    // deadline, instead of the destructor's discard. Called from the receiver
    // thread's exit path via the session's sink finisher, so it runs exactly
    // once, after the last enqueue. Whatever the deadline cannot flush is shed;
    // the worker exits once drained (or on the destructor's stop).
    void finish(const std::chrono::milliseconds deadline) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (stop_ || finishing_) {
            return;
        }
        finishing_ = true;
        cv_.notify_all();
        drained_cv_.wait_for(lock, deadline, [this] { return queue_.empty() && !posting_; });
    }

private:
    void run() {
      try {
        for (;;) {
            std::string body;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                cv_.wait(lock, [this] { return stop_ || finishing_ || !queue_.empty(); });
                if (stop_) {
                    // Session is going away: discard any remaining audio and
                    // exit promptly so the destructor's join is bounded to at
                    // most one in-flight POST (~200ms).
                    return;
                }
                if (queue_.empty()) {
                    // finishing_ with nothing left: the drain is complete.
                    drained_cv_.notify_all();
                    return;
                }
                body = std::move(queue_.front());
                queue_.pop_front();
                posting_ = true;
            }
            // Sequential send outside the lock preserves strict FIFO ordering
            // (single thread, dequeue order) without holding up enqueue().
            // consecutive_failures_ is touched only here (single worker), so it
            // needs no lock. Surface a dead/erroring backend instead of dropping
            // STT audio silently (VG-10), rate-limited so it can't flood logs.
            //
            // The try/catch is PER BATCH: one exception (e.g. std::bad_alloc in
            // http_post's buffers) costs one batch, not the worker. The old
            // whole-loop catch let a single throw permanently silence STT
            // delivery while enqueue() kept filling a never-drained queue
            // (review: sender-exception permanent silence).
            try {
                if (http_post(url_, body)) {
                    consecutive_failures_ = 0;
                } else {
                    ++consecutive_failures_;
                    if (consecutive_failures_ == 1 || consecutive_failures_ % 100 == 0) {
                        std::cerr << "event=stt_callback_delivery_failed url=" << url_
                                  << " consecutive_failures=" << consecutive_failures_ << "\n";
                    }
                }
            } catch (...) {
                ++consecutive_failures_;
            }
            {
                std::lock_guard<std::mutex> lock(mutex_);
                posting_ = false;
                if (queue_.empty()) {
                    drained_cv_.notify_all();  // a finish() may be waiting
                }
            }
        }
      } catch (...) {
        // Only reachable if the mutex/cv machinery itself throws — contain it so
        // it cannot reach the thread entry and std::terminate the gateway (#2).
      }
    }

    const std::string url_;
    std::mutex mutex_;
    std::condition_variable cv_;
    std::condition_variable drained_cv_;
    std::deque<std::string> queue_;
    bool stop_{false};
    bool finishing_{false};   // finish() called: drain then exit (under mutex_)
    bool posting_{false};     // a POST is in flight outside the lock (under mutex_)
    int consecutive_failures_{0};  // worker-thread-only; no lock needed
    std::thread worker_;  // declared last: constructed after the fields run() touches
};

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

// --- Control-plane auth + callback allowlisting (VG-18) ---
// Both are OPT-IN via environment so default behavior is unchanged (the control
// plane is localhost-bound today). Set VOICE_GATEWAY_AUTH_TOKEN to require
// "Authorization: Bearer <token>" on every request except GET /health; set
// VOICE_GATEWAY_CALLBACK_HOST to restrict the audio_callback_url host. Read once.
const std::string& gateway_auth_token() {
    static const std::string token = [] {
        const char* v = std::getenv("VOICE_GATEWAY_AUTH_TOKEN");
        return (v != nullptr) ? std::string(v) : std::string();
    }();
    return token;
}

const std::string& callback_host_allowlist() {
    static const std::string host = [] {
        const char* v = std::getenv("VOICE_GATEWAY_CALLBACK_HOST");
        return (v != nullptr && *v != '\0') ? std::string(v) : std::string();
    }();
    return host;
}

bool request_authorized(const HttpRequest& request) {
    const std::string& token = gateway_auth_token();
    if (token.empty()) {
        return true;  // auth disabled (default)
    }
    const auto it = request.headers.find("authorization");
    if (it == request.headers.end()) {
        return false;
    }
    static const std::string kPrefix = "Bearer ";
    const std::string& value = it->second;
    if (value.size() != kPrefix.size() + token.size()) {
        return false;
    }
    if (value.compare(0, kPrefix.size(), kPrefix) != 0) {
        return false;
    }
    return value.compare(kPrefix.size(), token.size(), token) == 0;
}

// Acceptable iff it has no control characters (blocks CRLF request-line injection
// into the outbound POST), uses plaintext http:// (the only scheme http_post
// actually speaks), and — when the allowlist env is set — targets that host
// (SSRF/audio-exfiltration containment).
bool is_allowed_callback_url(const std::string& url) {
    // Bounded: the URL is echoed into every outbound POST request line, so an
    // unbounded value would inflate every callback (review/batch-D byte cap).
    if (url.size() > 512) {
        return false;
    }
    for (const unsigned char c : url) {
        if (c < 0x20 || c == 0x7F) {
            return false;
        }
    }
    static const std::string kScheme = "http://";
    if (url.size() <= kScheme.size() || url.compare(0, kScheme.size(), kScheme) != 0) {
        return false;
    }
    const std::string& allowed = callback_host_allowlist();
    if (allowed.empty()) {
        return true;  // host allowlist disabled (default)
    }
    const std::size_t host_start = kScheme.size();
    const std::size_t host_end = url.find_first_of(":/", host_start);
    const std::string host = (host_end == std::string::npos)
                                 ? url.substr(host_start)
                                 : url.substr(host_start, host_end - host_start);
    return host == allowed;
}

}  // namespace

HttpServer::HttpServer(std::string host, uint16_t port, SessionRegistry& registry)
    : host_(std::move(host)), port_(port), registry_(registry) {}

HttpServer::~HttpServer() {
    stop();
    // Close the listener only AFTER stop() has drained handlers and the caller
    // has joined run() (main does). Doing it here — not in stop() — avoids
    // closing the fd while the accept loop may still be mid-syscall on it (VG-19).
    close_listener();
}

bool HttpServer::start(std::string& error) {
    if (running_.load()) {
        return true;
    }

    // Single-use: after stop() the listener is being torn down and draining_ is
    // latched; a second start() would overwrite server_fd_ (leaking the old
    // listener) and serve with draining_ still true (review: unsafe restart).
    {
        std::lock_guard<std::mutex> lk(handlers_mutex_);
        if (stopped_ || draining_) {
            error = "HttpServer is single-use; construct a new instance to restart";
            return false;
        }
    }

    // Fail CLOSED when exposed beyond loopback without auth (review #13): the
    // control plane can originate calls and receive caller audio, so listening
    // on a non-loopback address with no VOICE_GATEWAY_AUTH_TOKEN configured is
    // refused outright rather than silently unauthenticated.
    if (host_.rfind("127.", 0) != 0 && host_ != "localhost" && gateway_auth_token().empty()) {
        error = "refusing to listen on non-loopback host without VOICE_GATEWAY_AUTH_TOKEN";
        healthy_.store(false);
        return false;
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

    // Bound accept() so the loop periodically re-checks running_ and exits on
    // shutdown WITHOUT another thread having to close the listener fd out from
    // under a blocked accept() — the signal-context / fd-reuse race VG-19 raised.
    // CHECKED: if this fails, accept() would block forever and stop() (which
    // deliberately does not close the listener) could hang main in join()
    // (review #15). stop() also shutdown()s the listener as a second wake path.
    const timeval accept_timeout{0, 250000};  // 250 ms
    if (setsockopt(server_fd_, SOL_SOCKET, SO_RCVTIMEO, &accept_timeout, sizeof(accept_timeout)) != 0) {
        error = std::string("failed to set listener accept timeout: ") + std::strerror(errno);
        healthy_.store(false);
        close_listener();
        return false;
    }

    running_.store(true);
    healthy_.store(true);
    return true;
}

void HttpServer::run() {
    int accept_backoff_ms = 0;
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
            // The 250ms accept timeout expiring is normal (it exists so we
            // re-check running_ on shutdown) — not an error condition (VG-19).
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                continue;
            }
            // Transient/overload errors (EMFILE/ENFILE/ECONNABORTED/ENOBUFS):
            // mark unhealthy and back off (capped) so a persistent failure such
            // as fd exhaustion does not become a 100%-CPU spin (VG-21).
            healthy_.store(false);
            accept_backoff_ms = accept_backoff_ms == 0 ? 5 : std::min(accept_backoff_ms * 2, 200);
            std::this_thread::sleep_for(std::chrono::milliseconds(accept_backoff_ms));
            continue;
        }

        // A successful accept means the listener recovered.
        accept_backoff_ms = 0;
        healthy_.store(true);

        // The poll-based absolute deadlines in read_request/write_response
        // REQUIRE a non-blocking socket; a silent fcntl failure would revert to
        // blocking I/O and defeat every deadline (reviews #10/#11), so it is
        // checked and the connection refused on failure.
        const int fl = fcntl(client_fd, F_GETFL, 0);
        if (fl < 0 || fcntl(client_fd, F_SETFL, fl | O_NONBLOCK) != 0) {
            ::close(client_fd);
            continue;
        }

        spawn_handler(client_fd);

        // Opportunistically join+free finished handler slots so a long uptime
        // does not accumulate joinable thread corpses.
        reap_finished_handlers();
    }
}

bool HttpServer::spawn_handler(const int client_fd) {
    // Admission + tracking are one transaction under handlers_mutex_ (review
    // #9): the slot is inserted BEFORE the thread starts, every failure path
    // rolls the insertion and count back, and the (potentially allocating)
    // best-effort 503 happens only AFTER bookkeeping is consistent, inside its
    // own try so a second exception cannot leak the fd or wedge the count.
    HandlerSlot* raw = nullptr;
    try {
        auto slot = std::make_unique<HandlerSlot>();
        slot->fd = client_fd;
        raw = slot.get();
        {
            std::lock_guard<std::mutex> lk(handlers_mutex_);
            if (draining_ || active_handler_count_ >= kMaxActiveHandlers) {
                raw = nullptr;  // rejected: nothing tracked yet
            } else {
                handlers_.push_back(std::move(slot));
                ++active_handler_count_;
            }
        }
    } catch (...) {
        raw = nullptr;  // allocation failed: nothing tracked
    }

    if (raw == nullptr) {
        try {
            write_response(client_fd, 503, "Service Unavailable", "{\"error\":\"too_many_connections\"}");
        } catch (...) {
        }
        ::close(client_fd);
        return false;
    }

    try {
        raw->thread = std::thread([this, raw] { handler_main(raw); });
        return true;
    } catch (...) {
        // Thread construction failed: remove the slot, rebalance, respond, close.
        {
            std::lock_guard<std::mutex> lk(handlers_mutex_);
            for (auto it = handlers_.begin(); it != handlers_.end(); ++it) {
                if (it->get() == raw) {
                    handlers_.erase(it);
                    --active_handler_count_;
                    break;
                }
            }
        }
        try {
            write_response(client_fd, 503, "Service Unavailable", "{\"error\":\"handler_spawn_failed\"}");
        } catch (...) {
        }
        ::close(client_fd);
        return false;
    }
}

void HttpServer::handler_main(HandlerSlot* slot) {
    // Nothing may escape a thread entry (std::terminate, finding #2). The
    // completion sequence is deliberately allocation-free: no shared_ptr control
    // block whose construction could itself throw before the guard is armed
    // (review #3).
    try {
        try {
            handle_client(slot->fd);
        } catch (...) {
            // Contain handler exceptions (std::bad_alloc, callback-sender spawn
            // failures, ...) so the gateway survives (finding #2 / VG-04).
        }
        std::lock_guard<std::mutex> lk(handlers_mutex_);
        if (!slot->closed) {
            ::close(slot->fd);  // under the lock: stop() can never shutdown a recycled fd
            slot->closed = true;
        }
        slot->done = true;
        --active_handler_count_;
    } catch (...) {
        // Only reachable if locking handlers_mutex_ itself throws — the join in
        // stop()/reap does not depend on `done`, so shutdown still completes.
    }
}

void HttpServer::reap_finished_handlers() {
    std::vector<std::unique_ptr<HandlerSlot>> finished;
    {
        std::lock_guard<std::mutex> lk(handlers_mutex_);
        for (auto it = handlers_.begin(); it != handlers_.end();) {
            if ((*it)->done) {
                finished.push_back(std::move(*it));
                it = handlers_.erase(it);
            } else {
                ++it;
            }
        }
    }
    // Join OUTSIDE the lock: a done handler's only remaining work is returning
    // from its function, which never needs handlers_mutex_ again — so each join
    // returns near-instantly and can never deadlock against a handler that is
    // still waiting for the lock to mark itself done.
    for (auto& h : finished) {
        if (h->thread.joinable()) {
            h->thread.join();
        }
    }
}

void HttpServer::stop() {
    running_.store(false);

    // Wake a blocked accept() immediately. shutdown() (never close()) is safe
    // against a concurrent accept() on the same fd and doubles as the escape
    // hatch if the listener's SO_RCVTIMEO were ever ineffective (review #15).
    // The fd itself is closed only in the destructor, after run() was joined.
    if (server_fd_ >= 0) {
        ::shutdown(server_fd_, SHUT_RDWR);
    }

    // Stop admission, force every in-flight handler's socket to return from its
    // poll/recv/send, take ownership of ALL handler slots, then JOIN every
    // handler thread. Join is a true completion barrier: after it returns the
    // thread has fully exited, so no handler can touch this server / the
    // registry afterwards — unlike the old detached-thread + "fd set empty"
    // condition, which a handler could still race after signalling (reviews
    // #1/#2). The socket shutdowns + absolute I/O deadlines bound every join.
    std::list<std::unique_ptr<HandlerSlot>> to_join;
    {
        std::lock_guard<std::mutex> lk(handlers_mutex_);
        draining_ = true;
        stopped_ = true;
        for (const auto& h : handlers_) {
            if (!h->closed) {
                ::shutdown(h->fd, SHUT_RDWR);
            }
        }
        to_join.swap(handlers_);
    }
    for (const auto& h : to_join) {
        if (h->thread.joinable()) {
            h->thread.join();
        }
    }
    // After the joins no handler thread exists; close any fd whose handler
    // failed to reach its completion sequence (defensive; normally none).
    for (const auto& h : to_join) {
        if (!h->closed && h->fd >= 0) {
            ::close(h->fd);
        }
    }
}

bool HttpServer::healthy() const {
    // /health is a PROCESS/listener liveness signal: is the accept loop alive and
    // able to serve requests. It deliberately does NOT aggregate per-session
    // health — a single failed call must never flip /health to 503 and trigger an
    // orchestrator restart that kills every other live call (VG-20). Per-session
    // state/outcome is exposed via /stats and the per-session stats endpoint.
    return healthy_.load();
}

void HttpServer::handle_client(const int client_fd) {
    bool over_budget = false;
    const auto request = read_request(client_fd, over_budget);
    if (!request.has_value()) {
        if (over_budget) {
            // Aggregate body budget exhausted — a load condition, not a bad
            // request. 503 tells the backend to back off/retry.
            write_response(client_fd, 503, "Service Unavailable", "{\"error\":\"body_budget_exhausted\"}");
            return;
        }
        write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_http_request\"}");
        return;
    }

    // Auth gate (VG-18): everything except the liveness probe requires a valid
    // bearer token when VOICE_GATEWAY_AUTH_TOKEN is configured; a no-op otherwise.
    if (!(request->method == "GET" && request->path == "/health") && !request_authorized(request.value())) {
        write_response(client_fd, 401, "Unauthorized", "{\"error\":\"unauthorized\"}");
        return;
    }

    if (request->method == "GET" && request->path == "/health") {
        const bool is_healthy = healthy();
        const std::string body = std::string("{\"status\":\"") + (is_healthy ? "ok" : "degraded") + "\",\"io_loop_healthy\":" + (is_healthy ? "true" : "false") + "}";
        write_response(client_fd, is_healthy ? 200 : 503, is_healthy ? "OK" : "Service Unavailable", body);
        return;
    }

    if (request->method == "GET" && request->path == "/stats") {
        write_response(client_fd, 200, "OK", process_stats_json(registry_.snapshot()));
        return;
    }

    if (request->method == "GET" && request->path == "/v1/sessions") {
        write_response(client_fd, 200, "OK", sessions_list_json(registry_.list_sessions()));
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
        const auto enforce_rtp_source = json_get_bool(request->body, "enforce_rtp_source");
        const auto stt_reorder_enabled = json_get_bool(request->body, "stt_reorder_enabled");
        const auto stt_reorder_window_frames = json_get_int(request->body, "stt_reorder_window_frames");
        const auto stt_reorder_hold_ms = json_get_int(request->body, "stt_reorder_hold_ms");

        if (!session_id.has_value() || !listen_ip.has_value() || !listen_port.has_value() ||
            !remote_ip.has_value() || !remote_port.has_value() || !codec.has_value() || !ptime_ms.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_required_start_fields\"}");
            return;
        }

        if (listen_port.value() <= 0 || listen_port.value() > 65535 ||
            remote_port.value() <= 0 || remote_port.value() > 65535) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_port_range\"}");
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
                return;
            }
            config.jitter_buffer_capacity_frames = static_cast<std::size_t>(jitter_buffer_capacity_frames.value());
        }
        if (jitter_buffer_prefetch_frames.has_value()) {
            if (jitter_buffer_prefetch_frames.value() <= 0) {
                write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_jitter_buffer_prefetch_frames\"}");
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
                return;
            }
            config.tts_max_queue_frames = static_cast<std::size_t>(tts_max_queue_frames.value());
        }
        if (audio_callback_url.has_value()) {
            // Reject control-char / non-http / off-allowlist callback URLs before
            // the gateway will POST caller audio to them (VG-18).
            if (!is_allowed_callback_url(audio_callback_url.value())) {
                write_response(client_fd, 400, "Bad Request", "{\"error\":\"callback_url_not_allowed\"}");
                return;
            }
            config.audio_callback_url = audio_callback_url.value();
        }
        if (audio_callback_batch_frames.has_value() && audio_callback_batch_frames.value() > 0) {
            config.audio_callback_batch_frames = audio_callback_batch_frames.value();
        }
        if (enforce_rtp_source.has_value()) {
            config.enforce_rtp_source = enforce_rtp_source.value();
        }
        if (stt_reorder_enabled.has_value()) {
            config.stt_reorder_enabled = stt_reorder_enabled.value();
        }
        if (stt_reorder_window_frames.has_value()) {
            config.stt_reorder_window_frames = stt_reorder_window_frames.value();
        }
        if (stt_reorder_hold_ms.has_value()) {
            config.stt_reorder_hold_ms = stt_reorder_hold_ms.value();
        }

        // Build the STT audio callback (if requested) BEFORE starting the
        // session, and hand it to start_session so it is installed before the
        // receiver thread can process the first RTP packet. This removes both
        // the early-audio-lost window and the old get_session()-after-start race
        // where a concurrent stop returned 200 with no callback attached (VG-11).
        // The closure captures only per-call state (id, batch size, buffer,
        // sender) — never the session — so it is safe to build before creation.
        RtpSession::AudioCallback audio_cb;
        std::function<void()> sink_finisher;
        if (!config.audio_callback_url.empty()) {
            const std::string cb_session_id = config.session_id;
            const int batch_frames = config.audio_callback_batch_frames;

            struct BatchState {
                std::vector<uint8_t> buffer;
                int frame_count{0};
            };
            auto state = std::make_shared<BatchState>();
            // Owned solely by these closures, which live in RtpSession::
            // audio_callback_ / audio_sink_finisher_, so the sender (and its
            // worker thread) is destroyed and JOINED when the session is
            // destroyed. No detached thread outlives the session.
            auto sender = std::make_shared<AudioCallbackSender>(config.audio_callback_url);

            audio_cb = [cb_session_id, batch_frames, state, sender](
                           const std::string& /*sid*/, const std::vector<uint8_t>& pcmu) {
                state->buffer.insert(state->buffer.end(), pcmu.begin(), pcmu.end());
                state->frame_count++;
                if (state->frame_count >= batch_frames) {
                    const std::string b64 = base64_encode(state->buffer);
                    std::string body =
                        "{\"session_id\":\"" + escape_json(cb_session_id) +
                        "\",\"pcmu_base64\":\"" + b64 +
                        "\",\"codec\":\"pcmu\"}";
                    state->buffer.clear();
                    state->frame_count = 0;
                    // Non-blocking hand-off; never blocks the RTP receiver thread
                    // on network I/O.
                    sender->enqueue(std::move(body));
                }
            };

            // Batch E: runs ONCE on the receiver thread as it exits — flush the
            // partial batch (a batch_frames > 1 config otherwise silently drops
            // the final frames of every call) and drain the sender with a hard
            // deadline instead of discarding whatever is still queued. Same
            // thread as audio_cb, so BatchState needs no extra locking.
            sink_finisher = [cb_session_id, state, sender] {
                if (!state->buffer.empty()) {
                    const std::string b64 = base64_encode(state->buffer);
                    std::string body =
                        "{\"session_id\":\"" + escape_json(cb_session_id) +
                        "\",\"pcmu_base64\":\"" + b64 +
                        "\",\"codec\":\"pcmu\"}";
                    state->buffer.clear();
                    state->frame_count = 0;
                    sender->enqueue(std::move(body));
                }
                sender->finish(std::chrono::milliseconds(500));
            };
        }

        std::string error;
        const auto result = registry_.start_session(config, error, std::move(audio_cb), std::move(sink_finisher));

        if (result == StartSessionResult::Started) {
            write_response(client_fd, 200, "OK", "{\"status\":\"started\",\"session_id\":\"" + escape_json(config.session_id) + "\"}");
            return;
        }

        if (result == StartSessionResult::AlreadyExists) {
            write_response(client_fd, 409, "Conflict", "{\"status\":\"already_exists\",\"error\":\"" + escape_json(error) + "\"}");
            return;
        }

        if (result == StartSessionResult::InternalError) {
            write_response(client_fd, 500, "Internal Server Error", "{\"status\":\"failed\",\"error\":\"" + escape_json(error) + "\"}");
            return;
        }

        write_response(client_fd, 400, "Bad Request", "{\"status\":\"failed\",\"error\":\"" + escape_json(error) + "\"}");
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/stop") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto reason = json_get_string(request->body, "reason");
        if (!session_id.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_session_id\"}");
            return;
        }

        bool already_stopped = false;
        registry_.stop_session(session_id.value(), reason.value_or("stopped_by_request"), already_stopped);
        if (already_stopped) {
            write_response(client_fd, 200, "OK", "{\"status\":\"already_stopped\",\"session_id\":\"" + escape_json(session_id.value()) + "\"}");
            return;
        }

        write_response(client_fd, 200, "OK", "{\"status\":\"stopped\",\"session_id\":\"" + escape_json(session_id.value()) + "\"}");
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/tts/play") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto pcmu_base64 = json_get_string(request->body, "pcmu_base64");
        const auto clear_existing = json_get_bool(request->body, "clear_existing");
        // Optional idempotency identity (VG-13). Absent fields = legacy behavior.
        const auto utterance_id = json_get_string(request->body, "utterance_id");
        const auto chunk_seq = json_get_int(request->body, "chunk_seq");
        if (!session_id.has_value() || !pcmu_base64.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_tts_play_fields\"}");
            return;
        }
        if (utterance_id.has_value() && utterance_id->size() > 64) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"utterance_id_too_long\"}");
            return;
        }

        const auto session = registry_.get_session(session_id.value());
        if (!session) {
            write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
            return;
        }

        const auto decoded = base64_decode(pcmu_base64.value());
        if (!decoded.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"invalid_pcmu_base64\"}");
            return;
        }

        std::string error;
        std::size_t queued_frames = 0;
        if (!session->enqueue_tts_ulaw(decoded.value(), clear_existing.value_or(false), queued_frames, error,
                                       utterance_id.value_or(std::string()),
                                       chunk_seq.has_value() ? static_cast<int64_t>(chunk_seq.value()) : -1)) {
            // Stale-chunk rejections are a CONFLICT (expected after a barge-in),
            // distinguishable from malformed requests so the backend can treat
            // them as benign.
            const bool stale = (error == "utterance_interrupted" || error == "stale_or_duplicate_chunk");
            write_response(client_fd, stale ? 409 : 400, stale ? "Conflict" : "Bad Request",
                           "{\"error\":\"" + escape_json(error) + "\"}");
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
        return;
    }

    if (request->method == "POST" && request->path == "/v1/sessions/tts/interrupt") {
        const auto session_id = json_get_string(request->body, "session_id");
        const auto reason = json_get_string(request->body, "reason");
        if (!session_id.has_value()) {
            write_response(client_fd, 400, "Bad Request", "{\"error\":\"missing_session_id\"}");
            return;
        }

        const auto session = registry_.get_session(session_id.value());
        if (!session) {
            write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
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
        return;
    }

    if (request->method == "GET") {
        const auto session_id = extract_session_id_from_path(request->path);
        if (session_id.has_value()) {
            const auto session = registry_.get_session(session_id.value());
            if (!session) {
                write_response(client_fd, 404, "Not Found", "{\"error\":\"session_not_found\"}");
                return;
            }
            write_response(client_fd, 200, "OK", session_stats_json(session->snapshot()));
            return;
        }
    }

    write_response(client_fd, 404, "Not Found", "{\"error\":\"route_not_found\"}");
}

void HttpServer::close_listener() {
    if (server_fd_ >= 0) {
        shutdown(server_fd_, SHUT_RDWR);
        close(server_fd_);
        server_fd_ = -1;
    }
}

}  // namespace voice_gateway
