#include "voice_gateway/rtp_packet.h"

#include <array>
#include <limits>
#include <random>

namespace voice_gateway {

std::vector<uint8_t> RtpPacket::serialize() const {
    constexpr std::size_t kHeaderSize = 12;
    std::vector<uint8_t> bytes;
    bytes.resize(kHeaderSize + payload.size());

    bytes[0] = static_cast<uint8_t>((version & 0x03u) << 6);
    if (padding) {
        bytes[0] = static_cast<uint8_t>(bytes[0] | 0x20u);
    }
    if (extension) {
        bytes[0] = static_cast<uint8_t>(bytes[0] | 0x10u);
    }
    bytes[0] = static_cast<uint8_t>(bytes[0] | (csrc_count & 0x0Fu));

    bytes[1] = static_cast<uint8_t>(payload_type & 0x7Fu);
    if (marker) {
        bytes[1] = static_cast<uint8_t>(bytes[1] | 0x80u);
    }

    bytes[2] = static_cast<uint8_t>((sequence_number >> 8) & 0xFFu);
    bytes[3] = static_cast<uint8_t>(sequence_number & 0xFFu);

    bytes[4] = static_cast<uint8_t>((timestamp >> 24) & 0xFFu);
    bytes[5] = static_cast<uint8_t>((timestamp >> 16) & 0xFFu);
    bytes[6] = static_cast<uint8_t>((timestamp >> 8) & 0xFFu);
    bytes[7] = static_cast<uint8_t>(timestamp & 0xFFu);

    bytes[8] = static_cast<uint8_t>((ssrc >> 24) & 0xFFu);
    bytes[9] = static_cast<uint8_t>((ssrc >> 16) & 0xFFu);
    bytes[10] = static_cast<uint8_t>((ssrc >> 8) & 0xFFu);
    bytes[11] = static_cast<uint8_t>(ssrc & 0xFFu);

    std::copy(payload.begin(), payload.end(), bytes.begin() + static_cast<std::ptrdiff_t>(kHeaderSize));
    return bytes;
}

std::optional<RtpPacket> RtpPacket::parse(const uint8_t* data, const std::size_t len) {
    constexpr std::size_t kHeaderSize = 12;
    if (data == nullptr || len < kHeaderSize) {
        return std::nullopt;
    }

    RtpPacket packet;
    packet.version = static_cast<uint8_t>((data[0] >> 6) & 0x03u);
    if (packet.version != 2) {
        return std::nullopt;
    }

    packet.padding = (data[0] & 0x20u) != 0;
    packet.extension = (data[0] & 0x10u) != 0;
    packet.csrc_count = static_cast<uint8_t>(data[0] & 0x0Fu);

    std::size_t header_len = kHeaderSize + static_cast<std::size_t>(packet.csrc_count) * 4;
    if (len < header_len) {
        return std::nullopt;
    }

    if (packet.extension) {
        constexpr std::size_t kExtHeaderSize = 4;
        if (len < header_len + kExtHeaderSize) {
            return std::nullopt;
        }
        const std::size_t ext_offset = header_len;
        const uint16_t ext_len_words = static_cast<uint16_t>(
            (static_cast<uint16_t>(data[ext_offset + 2]) << 8) | data[ext_offset + 3]);
        const std::size_t ext_len_bytes = static_cast<std::size_t>(ext_len_words) * 4;
        header_len += kExtHeaderSize + ext_len_bytes;
        if (len < header_len) {
            return std::nullopt;
        }
    }

    packet.marker = (data[1] & 0x80u) != 0;
    packet.payload_type = static_cast<uint8_t>(data[1] & 0x7Fu);

    packet.sequence_number = static_cast<uint16_t>((static_cast<uint16_t>(data[2]) << 8) | data[3]);
    packet.timestamp =
        (static_cast<uint32_t>(data[4]) << 24) |
        (static_cast<uint32_t>(data[5]) << 16) |
        (static_cast<uint32_t>(data[6]) << 8) |
        static_cast<uint32_t>(data[7]);
    packet.ssrc =
        (static_cast<uint32_t>(data[8]) << 24) |
        (static_cast<uint32_t>(data[9]) << 16) |
        (static_cast<uint32_t>(data[10]) << 8) |
        static_cast<uint32_t>(data[11]);

    std::size_t payload_end = len;
    if (packet.padding) {
        const uint8_t padding_len = data[len - 1];
        if (padding_len == 0 || padding_len > (len - header_len)) {
            return std::nullopt;
        }
        payload_end = len - padding_len;
    }

    packet.payload.assign(
        data + static_cast<std::ptrdiff_t>(header_len),
        data + static_cast<std::ptrdiff_t>(payload_end));

    return packet;
}

RtpSequencer::RtpSequencer(uint16_t initial_sequence, uint32_t initial_timestamp, uint32_t ssrc)
    : sequence_number_(initial_sequence), timestamp_(initial_timestamp), ssrc_(ssrc) {}

RtpSequencer RtpSequencer::random() {
    std::random_device rd;
    std::mt19937 gen(rd());

    std::uniform_int_distribution<uint16_t> seq_dist(0, std::numeric_limits<uint16_t>::max());
    std::uniform_int_distribution<uint32_t> ts_dist(0, std::numeric_limits<uint32_t>::max());
    std::uniform_int_distribution<uint32_t> ssrc_dist(1, std::numeric_limits<uint32_t>::max());

    return RtpSequencer(seq_dist(gen), ts_dist(gen), ssrc_dist(gen));
}

RtpPacket RtpSequencer::next_packet(const std::vector<uint8_t>& payload, const uint8_t payload_type) {
    constexpr uint32_t kTimestampStep = 160;

    RtpPacket packet;
    packet.payload_type = payload_type;
    packet.sequence_number = sequence_number_;
    packet.timestamp = timestamp_;
    packet.ssrc = ssrc_;
    packet.payload = payload;

    sequence_number_ = static_cast<uint16_t>(sequence_number_ + 1);
    timestamp_ = static_cast<uint32_t>(timestamp_ + kTimestampStep);

    return packet;
}

uint16_t RtpSequencer::next_sequence_preview() const {
    return sequence_number_;
}

uint32_t RtpSequencer::next_timestamp_preview() const {
    return timestamp_;
}

uint32_t RtpSequencer::ssrc() const {
    return ssrc_;
}

}  // namespace voice_gateway
