"""
Unit Tests for RTP Packet Builder
Tests RTP packet construction and parsing
"""
import pytest
import struct
from app.utils.rtp_builder import (
    RTPPacket,
    RTPPacketBuilder,
    PayloadType,
    create_rtp_builder
)


class TestRTPPacket:
    """Tests for RTPPacket dataclass."""
    
    def test_packet_to_bytes(self):
        """Test RTP packet serialization."""
        payload = b'\x00' * 160  # 20ms of silence
        
        packet = RTPPacket(
            payload=payload,
            sequence_number=1234,
            timestamp=5678,
            ssrc=0x12345678,
            payload_type=PayloadType.PCMU
        )
        
        data = packet.to_bytes()
        
        # Header is 12 bytes + 160 bytes payload
        assert len(data) == 12 + 160
        
        # Check header fields
        first_byte = data[0]
        assert (first_byte >> 6) == 2  # RTP version 2
        
        second_byte = data[1]
        assert (second_byte & 0x7F) == PayloadType.PCMU  # Payload type
        
        # Check sequence number (bytes 2-3, big-endian)
        seq = struct.unpack('>H', data[2:4])[0]
        assert seq == 1234
        
        # Check timestamp (bytes 4-7, big-endian)
        ts = struct.unpack('>I', data[4:8])[0]
        assert ts == 5678
        
        # Check SSRC (bytes 8-11, big-endian)
        ssrc = struct.unpack('>I', data[8:12])[0]
        assert ssrc == 0x12345678
    
    def test_packet_from_bytes(self):
        """Test RTP packet parsing."""
        # Create a valid RTP packet manually
        header = struct.pack(
            '>BBHII',
            0x80,  # V=2, P=0, X=0, CC=0
            0x00,  # M=0, PT=0 (PCMU)
            1234,  # sequence
            5678,  # timestamp
            0xABCD1234  # SSRC
        )
        payload = b'\xFF' * 160
        data = header + payload
        
        packet = RTPPacket.from_bytes(data)
        
        assert packet.version == 2
        assert packet.padding == False
        assert packet.extension == False
        assert packet.marker == False
        assert packet.payload_type == PayloadType.PCMU
        assert packet.sequence_number == 1234
        assert packet.timestamp == 5678
        assert packet.ssrc == 0xABCD1234
        assert packet.payload == payload
    
    def test_packet_roundtrip(self):
        """Test serialize then parse produces same packet."""
        original = RTPPacket(
            payload=b'\x55' * 160,
            sequence_number=9999,
            timestamp=88888,
            ssrc=0xDEADBEEF,
            payload_type=PayloadType.PCMA,
            marker=True
        )
        
        data = original.to_bytes()
        parsed = RTPPacket.from_bytes(data)
        
        assert parsed.sequence_number == original.sequence_number
        assert parsed.timestamp == original.timestamp
        assert parsed.ssrc == original.ssrc
        assert parsed.payload_type == original.payload_type
        assert parsed.marker == original.marker
        assert parsed.payload == original.payload


class TestRTPPacketBuilder:
    """Tests for RTPPacketBuilder class."""
    
    def test_builder_initialization(self):
        """Test builder initializes with correct defaults."""
        builder = RTPPacketBuilder(
            ssrc=0x12345678,
            payload_type=PayloadType.PCMU
        )
        
        assert builder.ssrc == 0x12345678
        assert builder.payload_type == PayloadType.PCMU
        assert builder.sample_rate == 8000
        assert builder.samples_per_packet == 160
    
    def test_build_single_packet(self):
        """Test building a single RTP packet."""
        builder = RTPPacketBuilder(
            ssrc=0xAAAAAAAA,
            payload_type=PayloadType.PCMU
        )
        
        initial_seq = builder.current_sequence
        initial_ts = builder.current_timestamp
        
        audio = b'\x00' * 160
        packet_data = builder.build_packet(audio)
        
        # Verify packet is valid
        packet = RTPPacket.from_bytes(packet_data)
        assert packet.ssrc == 0xAAAAAAAA
        assert packet.payload_type == PayloadType.PCMU
        assert packet.sequence_number == initial_seq
        assert packet.timestamp == initial_ts
        
        # Verify sequence and timestamp incremented
        assert builder.current_sequence == (initial_seq + 1) & 0xFFFF
        assert builder.current_timestamp == (initial_ts + 160) & 0xFFFFFFFF
        assert builder.packets_sent == 1
    
    def test_build_multiple_packets(self):
        """Test building multiple packets with correct sequencing."""
        builder = RTPPacketBuilder(ssrc=0xBBBBBBBB)
        
        initial_seq = builder.current_sequence
        initial_ts = builder.current_timestamp
        
        for i in range(10):
            builder.build_packet(b'\x00' * 160)
        
        # After 10 packets
        assert builder.current_sequence == (initial_seq + 10) & 0xFFFF
        assert builder.current_timestamp == (initial_ts + 10 * 160) & 0xFFFFFFFF
        assert builder.packets_sent == 10
    
    def test_build_packets_from_audio(self):
        """Test splitting audio into multiple packets."""
        builder = RTPPacketBuilder(samples_per_packet=160)
        
        # 500 bytes of audio = 4 packets (3 full + 1 partial padded)
        audio = b'\x00' * 500
        packets = builder.build_packets_from_audio(audio)
        
        assert len(packets) == 4
        
        # First packet should have marker bit
        first = RTPPacket.from_bytes(packets[0])
        assert first.marker == True
        
        # Rest should not
        for pkt_data in packets[1:]:
            pkt = RTPPacket.from_bytes(pkt_data)
            assert pkt.marker == False
    
    def test_reset(self):
        """Test resetting the builder."""
        builder = RTPPacketBuilder()
        
        # Build some packets
        for _ in range(5):
            builder.build_packet(b'\x00' * 160)
        
        assert builder.packets_sent == 5
        
        # Reset
        builder.reset()
        
        assert builder.packets_sent == 0


class TestFactoryFunction:
    """Tests for create_rtp_builder factory."""
    
    def test_create_ulaw_builder(self):
        """Test creating mu-law builder."""
        builder = create_rtp_builder("ulaw")
        assert builder.payload_type == PayloadType.PCMU
    
    def test_create_alaw_builder(self):
        """Test creating A-law builder."""
        builder = create_rtp_builder("alaw")
        assert builder.payload_type == PayloadType.PCMA
    
    def test_unknown_codec_raises(self):
        """Test unknown codec raises ValueError."""
        with pytest.raises(ValueError):
            create_rtp_builder("unknown")
