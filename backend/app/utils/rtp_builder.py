"""
RTP Packet Builder
Constructs RTP packets for VoIP audio streaming (RFC 3550)
"""
import struct
import random
import time
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# RTP Payload Types (RFC 3551)
class PayloadType:
    PCMU = 0      # G.711 mu-law (North America, Japan)
    PCMA = 8      # G.711 A-law (Europe, rest of world)
    G722 = 9      # G.722 wideband
    L16_MONO = 11 # 16-bit linear PCM mono
    

@dataclass
class RTPPacket:
    """
    RTP Packet structure according to RFC 3550.
    
    Header format (12 bytes):
     0                   1                   2                   3
     0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |V=2|P|X|  CC   |M|     PT      |       sequence number         |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                           timestamp                           |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |           synchronization source (SSRC) identifier            |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    """
    payload: bytes
    sequence_number: int
    timestamp: int
    ssrc: int
    payload_type: int = PayloadType.PCMU
    version: int = 2
    padding: bool = False
    extension: bool = False
    csrc_count: int = 0
    marker: bool = False
    
    def to_bytes(self) -> bytes:
        """
        Serialize RTP packet to bytes.
        
        Returns:
            Complete RTP packet as bytes (header + payload)
        """
        # Build first byte: V(2) + P(1) + X(1) + CC(4)
        first_byte = (
            ((self.version & 0x03) << 6) |
            ((1 if self.padding else 0) << 5) |
            ((1 if self.extension else 0) << 4) |
            (self.csrc_count & 0x0F)
        )
        
        # Build second byte: M(1) + PT(7)
        second_byte = (
            ((1 if self.marker else 0) << 7) |
            (self.payload_type & 0x7F)
        )
        
        # Pack header (12 bytes)
        header = struct.pack(
            '>BBHII',
            first_byte,
            second_byte,
            self.sequence_number & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc & 0xFFFFFFFF
        )
        
        return header + self.payload
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'RTPPacket':
        """
        Parse RTP packet from bytes.
        
        Args:
            data: Raw RTP packet bytes
            
        Returns:
            Parsed RTPPacket object
        """
        if len(data) < 12:
            raise ValueError("RTP packet too short (minimum 12 bytes)")
        
        # Unpack header
        first_byte, second_byte, seq, ts, ssrc = struct.unpack(
            '>BBHII', data[:12]
        )
        
        # Parse first byte
        version = (first_byte >> 6) & 0x03
        padding = bool((first_byte >> 5) & 0x01)
        extension = bool((first_byte >> 4) & 0x01)
        csrc_count = first_byte & 0x0F
        
        # Parse second byte
        marker = bool((second_byte >> 7) & 0x01)
        payload_type = second_byte & 0x7F
        
        # Calculate header length (12 + CSRC list)
        header_len = 12 + (csrc_count * 4)
        
        if len(data) < header_len:
            raise ValueError(f"RTP packet truncated (expected {header_len}+ bytes)")
        
        payload = data[header_len:]
        
        return cls(
            payload=payload,
            sequence_number=seq,
            timestamp=ts,
            ssrc=ssrc,
            payload_type=payload_type,
            version=version,
            padding=padding,
            extension=extension,
            csrc_count=csrc_count,
            marker=marker
        )


class RTPPacketBuilder:
    """
    Builds RTP packets for audio streaming.
    
    Handles sequence numbers, timestamps, and SSRC management.
    Designed for G.711 audio at 8000Hz (160 samples per 20ms packet).
    """
    
    def __init__(
        self,
        ssrc: Optional[int] = None,
        payload_type: int = PayloadType.PCMU,
        sample_rate: int = 8000,
        samples_per_packet: int = 160  # 20ms at 8000Hz
    ):
        """
        Initialize RTP packet builder.
        
        Args:
            ssrc: Synchronization source identifier (random if not provided)
            payload_type: RTP payload type (0=PCMU, 8=PCMA)
            sample_rate: Audio sample rate in Hz
            samples_per_packet: Number of samples per RTP packet
        """
        self.ssrc = ssrc or random.randint(0, 0xFFFFFFFF)
        self.payload_type = payload_type
        self.sample_rate = sample_rate
        self.samples_per_packet = samples_per_packet
        
        # Initialize sequence and timestamp
        self._sequence_number = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)
        self._start_time = time.time()
        self._packets_sent = 0
        
        logger.debug(
            f"RTPPacketBuilder initialized: SSRC={self.ssrc:08x}, "
            f"PT={self.payload_type}, rate={self.sample_rate}"
        )
    
    def build_packet(
        self,
        audio_chunk: bytes,
        marker: bool = False
    ) -> bytes:
        """
        Build an RTP packet from audio data.
        
        Args:
            audio_chunk: G.711 encoded audio bytes
            marker: Set marker bit (typically for first packet of talk spurt)
            
        Returns:
            Complete RTP packet as bytes
        """
        packet = RTPPacket(
            payload=audio_chunk,
            sequence_number=self._sequence_number,
            timestamp=self._timestamp,
            ssrc=self.ssrc,
            payload_type=self.payload_type,
            marker=marker
        )
        
        # Increment sequence number (wrap at 16-bit max)
        self._sequence_number = (self._sequence_number + 1) & 0xFFFF
        
        # Increment timestamp by samples per packet
        self._timestamp = (self._timestamp + self.samples_per_packet) & 0xFFFFFFFF
        
        self._packets_sent += 1
        
        return packet.to_bytes()
    
    def build_packets_from_audio(
        self,
        audio_data: bytes,
        mark_first: bool = True
    ) -> list[bytes]:
        """
        Split audio data into multiple RTP packets.
        
        Each packet contains samples_per_packet samples (20ms default).
        
        Args:
            audio_data: G.711 encoded audio bytes
            mark_first: Set marker bit on first packet
            
        Returns:
            List of RTP packets as bytes
        """
        packets = []
        offset = 0
        is_first = True
        
        while offset < len(audio_data):
            chunk = audio_data[offset:offset + self.samples_per_packet]
            
            # Pad last packet if needed
            if len(chunk) < self.samples_per_packet:
                chunk = chunk + bytes(self.samples_per_packet - len(chunk))
            
            marker = mark_first and is_first
            packets.append(self.build_packet(chunk, marker=marker))
            
            offset += self.samples_per_packet
            is_first = False
        
        return packets
    
    def reset(self) -> None:
        """Reset sequence number and timestamp to new random values."""
        self._sequence_number = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)
        self._start_time = time.time()
        self._packets_sent = 0
        
        logger.debug(f"RTPPacketBuilder reset: seq={self._sequence_number}")
    
    @property
    def packets_sent(self) -> int:
        """Number of packets sent since creation or last reset."""
        return self._packets_sent
    
    @property
    def current_sequence(self) -> int:
        """Current sequence number (next packet will use this)."""
        return self._sequence_number
    
    @property
    def current_timestamp(self) -> int:
        """Current timestamp (next packet will use this)."""
        return self._timestamp


def create_rtp_builder(codec: str = "ulaw") -> RTPPacketBuilder:
    """
    Factory function to create an RTP packet builder for a codec.
    
    Args:
        codec: Codec name ("ulaw" or "alaw")
        
    Returns:
        Configured RTPPacketBuilder
    """
    if codec == "ulaw":
        return RTPPacketBuilder(payload_type=PayloadType.PCMU)
    elif codec == "alaw":
        return RTPPacketBuilder(payload_type=PayloadType.PCMA)
    else:
        raise ValueError(f"Unknown codec: {codec}. Use 'ulaw' or 'alaw'")
