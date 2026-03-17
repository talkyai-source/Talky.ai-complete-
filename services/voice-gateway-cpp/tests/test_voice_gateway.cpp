#include "voice_gateway/rtp_packet.h"
#include "voice_gateway/session_registry.h"

#include <cstdint>
#include <chrono>
#include <exception>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
#include <vector>

using voice_gateway::ProcessStatsSnapshot;
using voice_gateway::RtpPacket;
using voice_gateway::RtpSequencer;
using voice_gateway::SessionConfig;
using voice_gateway::SessionRegistry;
using voice_gateway::StartSessionResult;

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void test_rtp_roundtrip() {
    RtpPacket packet;
    packet.payload_type = 0;
    packet.marker = true;
    packet.sequence_number = 32100;
    packet.timestamp = 123456789u;
    packet.ssrc = 77u;
    packet.payload = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};

    const auto bytes = packet.serialize();
    const auto decoded = RtpPacket::parse(bytes.data(), bytes.size());

    expect(decoded.has_value(), "roundtrip parse should succeed");
    expect(decoded->version == 2, "version should be 2");
    expect(decoded->payload_type == packet.payload_type, "payload type mismatch");
    expect(decoded->marker == packet.marker, "marker mismatch");
    expect(decoded->sequence_number == packet.sequence_number, "sequence mismatch");
    expect(decoded->timestamp == packet.timestamp, "timestamp mismatch");
    expect(decoded->ssrc == packet.ssrc, "ssrc mismatch");
    expect(decoded->payload == packet.payload, "payload mismatch");
}

void test_rtp_invalid_packets() {
    const uint8_t short_packet[6] = {0};
    expect(!RtpPacket::parse(short_packet, sizeof(short_packet)).has_value(), "short packet should fail");

    uint8_t invalid_version_packet[12] = {0};
    invalid_version_packet[0] = 0x00;  // version 0
    expect(!RtpPacket::parse(invalid_version_packet, sizeof(invalid_version_packet)).has_value(), "invalid version should fail");

    // Invalid extension length: extension bit set but no extension header bytes available.
    uint8_t invalid_ext_packet[12] = {0};
    invalid_ext_packet[0] = 0x90;  // version 2 + extension
    expect(!RtpPacket::parse(invalid_ext_packet, sizeof(invalid_ext_packet)).has_value(), "invalid extension should fail");

    // Invalid padding length: declares more padding bytes than payload length.
    uint8_t invalid_padding_packet[13] = {0};
    invalid_padding_packet[0] = 0xA0;  // version 2 + padding
    invalid_padding_packet[12] = 10;
    expect(!RtpPacket::parse(invalid_padding_packet, sizeof(invalid_padding_packet)).has_value(), "invalid padding should fail");
}

void test_rtp_sequencer_increments() {
    RtpSequencer sequencer(100, 1000, 1234);

    const auto p1 = sequencer.next_packet({1, 2, 3}, 0);
    const auto p2 = sequencer.next_packet({4, 5, 6}, 0);

    expect(p1.sequence_number == 100, "first packet sequence should match initial");
    expect(p2.sequence_number == 101, "second packet sequence should increment by 1");
    expect(p1.timestamp == 1000, "first packet timestamp should match initial");
    expect(p2.timestamp == 1160, "second packet timestamp should increment by 160");
    expect(sequencer.next_sequence_preview() == 102, "next sequence preview mismatch");
    expect(sequencer.next_timestamp_preview() == 1320, "next timestamp preview mismatch");
}

void test_rtp_sequencer_rollover() {
    const uint32_t near_max_ts = std::numeric_limits<uint32_t>::max() - 80;
    RtpSequencer sequencer(std::numeric_limits<uint16_t>::max(), near_max_ts, 5678);

    const auto p1 = sequencer.next_packet({1}, 0);
    const auto p2 = sequencer.next_packet({2}, 0);

    expect(p1.sequence_number == std::numeric_limits<uint16_t>::max(), "rollover packet 1 sequence mismatch");
    expect(p2.sequence_number == 0, "rollover packet 2 sequence should wrap");
    expect(p1.timestamp == near_max_ts, "rollover packet 1 timestamp mismatch");
    expect(p2.timestamp == static_cast<uint32_t>(near_max_ts + 160), "rollover packet 2 timestamp should wrap");
}

void test_session_registry_start_stop_idempotency() {
    SessionRegistry registry;

    bool already_stopped = false;
    expect(registry.stop_session("missing-session", "unit_test_stop", already_stopped), "stop_session should return true");
    expect(already_stopped, "first stop on missing session should be already_stopped");

    expect(registry.stop_session("missing-session", "unit_test_stop_again", already_stopped), "second stop should return true");
    expect(already_stopped, "second stop should remain already_stopped");

    const ProcessStatsSnapshot snap = registry.snapshot();
    expect(snap.sessions_started_total == 0, "sessions_started_total should be 0");
    expect(snap.sessions_stopped_total == 0, "sessions_stopped_total should be 0");
    expect(snap.active_sessions == 0, "active_sessions should be 0");
}

void test_session_registry_config_validation() {
    SessionRegistry registry;

    SessionConfig config;
    config.session_id = "bad-codec";
    config.listen_ip = "127.0.0.1";
    config.listen_port = 29991;
    config.remote_ip = "127.0.0.1";
    config.remote_port = 30991;
    config.codec = "opus";
    config.ptime_ms = 20;

    std::string error;
    const auto result = registry.start_session(config, error);
    expect(result == StartSessionResult::InvalidConfig, "invalid codec must be rejected");
    expect(error.find("unsupported codec") != std::string::npos, "invalid codec reason should be explicit");

    config.session_id = "bad-ptime";
    config.codec = "pcmu";
    config.ptime_ms = 40;
    error.clear();

    const auto ptime_result = registry.start_session(config, error);
    expect(ptime_result == StartSessionResult::InvalidConfig, "invalid ptime must be rejected");
    expect(error.find("ptime_ms") != std::string::npos, "invalid ptime reason should be explicit");

    config.session_id = "bad-jitter-capacity";
    config.codec = "pcmu";
    config.ptime_ms = 20;
    config.jitter_buffer_capacity_frames = 30;
    config.jitter_buffer_prefetch_frames = 3;
    error.clear();

    const auto jitter_result = registry.start_session(config, error);
    expect(jitter_result == StartSessionResult::InvalidConfig, "invalid jitter capacity must be rejected");
    expect(error.find("jitter_buffer_capacity_frames") != std::string::npos, "invalid jitter capacity reason should be explicit");
}

}  // namespace

int main() {
    int passed = 0;
    int failed = 0;

    const std::vector<std::pair<std::string, void (*)()>> tests = {
        {"rtp_roundtrip", test_rtp_roundtrip},
        {"rtp_invalid_packets", test_rtp_invalid_packets},
        {"rtp_sequencer_increments", test_rtp_sequencer_increments},
        {"rtp_sequencer_rollover", test_rtp_sequencer_rollover},
        {"session_registry_start_stop_idempotency", test_session_registry_start_stop_idempotency},
        {"session_registry_config_validation", test_session_registry_config_validation},
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
