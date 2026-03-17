#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace voice_gateway {

struct RtpPacket {
    uint8_t version{2};
    bool padding{false};
    bool extension{false};
    uint8_t csrc_count{0};
    bool marker{false};
    uint8_t payload_type{0};
    uint16_t sequence_number{0};
    uint32_t timestamp{0};
    uint32_t ssrc{0};
    std::vector<uint8_t> payload;

    [[nodiscard]] std::vector<uint8_t> serialize() const;
    static std::optional<RtpPacket> parse(const uint8_t* data, std::size_t len);
};

class RtpSequencer {
public:
    RtpSequencer(uint16_t initial_sequence, uint32_t initial_timestamp, uint32_t ssrc);

    static RtpSequencer random();

    [[nodiscard]] RtpPacket next_packet(const std::vector<uint8_t>& payload, uint8_t payload_type = 0);

    [[nodiscard]] uint16_t next_sequence_preview() const;
    [[nodiscard]] uint32_t next_timestamp_preview() const;
    [[nodiscard]] uint32_t ssrc() const;

private:
    uint16_t sequence_number_;
    uint32_t timestamp_;
    uint32_t ssrc_;
};

}  // namespace voice_gateway
