"""
RTP Media Gateway Implementation
Handles RTP-based audio streaming for Asterisk/FreeSWITCH integration
"""
import asyncio
import socket as socket_module
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from app.domain.interfaces.media_gateway import MediaGateway
from app.utils.audio_utils import (
    validate_pcm_format,
    calculate_audio_duration_ms,
    convert_for_rtp,
    pcm_float32_to_int16,
    resample_audio,
    ulaw_to_pcm,
    alaw_to_pcm
)
from app.utils.rtp_builder import RTPPacketBuilder, PayloadType, RTPPacket
from app.domain.services.recording_service import RecordingBuffer

logger = logging.getLogger(__name__)


@dataclass
class RTPSession:
    """RTP session state for a single call."""
    call_id: str
    remote_ip: str
    remote_port: int
    local_port: int
    codec: str
    rtp_builder: RTPPacketBuilder
    udp_socket: Optional[socket_module.socket] = None
    input_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    output_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    started_at: Optional[datetime] = None
    packets_sent: int = 0
    packets_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0


class RTPMediaGateway(MediaGateway):
    """
    RTP media gateway for direct VoIP integration.
    
    Handles:
    - RTP packet transmission via UDP
    - Audio format conversion (PCM to G.711)
    - Codec support (mu-law and A-law)
    - Session lifecycle management
    
    This gateway is designed to work alongside VonageMediaGateway.
    Use MediaGatewayFactory to switch between them via configuration.
    
    RTP Audio Format:
    - G.711 mu-law or A-law
    - Sample Rate: 8000 Hz
    - Packet size: 160 samples (20ms)
    """
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._sessions: Dict[str, RTPSession] = {}
        
        # Default configuration
        self._default_remote_ip: str = "127.0.0.1"
        self._default_remote_port: int = 5004
        self._default_local_port: int = 5005
        self._default_codec: str = "ulaw"
        
        # Source audio format (from Cartesia TTS)
        self._source_sample_rate: int = 22050
        self._source_format: str = "pcm_f32le"
        
        # Recording buffers for Day 10 (provider-agnostic recording)
        self._recording_buffers: Dict[str, RecordingBuffer] = {}
        
    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Initialize RTP media gateway.
        
        Args:
            config: Configuration dictionary
                - remote_ip: Default RTP destination IP
                - remote_port: Default RTP destination port
                - local_port: Local port for receiving RTP
                - codec: Codec to use ("ulaw" or "alaw")
                - source_sample_rate: Input audio sample rate
                - source_format: Input audio format
        """
        self._config = config
        self._default_remote_ip = config.get("remote_ip", "127.0.0.1")
        self._default_remote_port = config.get("remote_port", 5004)
        self._default_local_port = config.get("local_port", 5005)
        self._default_codec = config.get("codec", "ulaw")
        self._source_sample_rate = config.get("source_sample_rate", 22050)
        self._source_format = config.get("source_format", "pcm_f32le")
        
        logger.info(
            f"RTP Media Gateway initialized: "
            f"{self._default_remote_ip}:{self._default_remote_port}, "
            f"codec={self._default_codec}"
        )
    
    async def on_call_started(
        self,
        call_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Handle call start event.
        
        Creates RTP session with UDP socket and packet builder.
        
        Args:
            call_id: Unique call identifier
            metadata: Call metadata (can include remote_ip, remote_port, codec)
        """
        logger.info(f"RTP call started: {call_id}", extra={"call_id": call_id})
        
        # Get session-specific config from metadata or use defaults
        remote_ip = metadata.get("remote_ip", self._default_remote_ip)
        remote_port = metadata.get("remote_port", self._default_remote_port)
        local_port = metadata.get("local_port", self._default_local_port)
        codec = metadata.get("codec", self._default_codec)
        
        # Create RTP packet builder with appropriate payload type
        payload_type = PayloadType.PCMU if codec == "ulaw" else PayloadType.PCMA
        rtp_builder = RTPPacketBuilder(
            payload_type=payload_type,
            sample_rate=8000,
            samples_per_packet=160  # 20ms
        )
        
        # Create UDP socket
        sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
        sock.setblocking(False)
        
        try:
            sock.bind(("0.0.0.0", local_port))
        except OSError as e:
            # Port in use, try dynamic port
            sock.bind(("0.0.0.0", 0))
            local_port = sock.getsockname()[1]
            logger.warning(f"Using dynamic port {local_port} for call {call_id}")
        
        # Create session
        session = RTPSession(
            call_id=call_id,
            remote_ip=remote_ip,
            remote_port=remote_port,
            local_port=local_port,
            codec=codec,
            rtp_builder=rtp_builder,
            udp_socket=sock,
            started_at=datetime.utcnow()
        )
        
        self._sessions[call_id] = session
        
        logger.info(
            f"RTP session created: {call_id} -> {remote_ip}:{remote_port} "
            f"(local port {local_port}, codec={codec})",
            extra={"call_id": call_id}
        )
        
        # Initialize recording buffer with 8kHz for RTP/G.711 (Day 10)
        self._recording_buffers[call_id] = RecordingBuffer(
            call_id=call_id,
            sample_rate=8000,  # G.711 standard rate
            channels=1,
            bit_depth=16
        )
    
    async def on_audio_received(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Handle incoming RTP audio (from remote PBX).
        
        Decodes G.711 and buffers for STT processing.
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw RTP packet or decoded audio
        """
        if call_id not in self._sessions:
            logger.warning(f"Audio received for unknown call: {call_id}")
            return
        
        session = self._sessions[call_id]
        
        try:
            # If this is an RTP packet, parse it
            if len(audio_chunk) > 12:
                try:
                    packet = RTPPacket.from_bytes(audio_chunk)
                    audio_data = packet.payload
                    
                    # Decode G.711 to PCM
                    if packet.payload_type == PayloadType.PCMU:
                        pcm_data = ulaw_to_pcm(audio_data)
                    elif packet.payload_type == PayloadType.PCMA:
                        pcm_data = alaw_to_pcm(audio_data)
                    else:
                        pcm_data = audio_data
                    
                    session.packets_received += 1
                    session.bytes_received += len(audio_chunk)
                    
                except Exception:
                    # Not an RTP packet, treat as raw audio
                    pcm_data = audio_chunk
            else:
                pcm_data = audio_chunk
            
            # Buffer for STT processing
            try:
                session.input_queue.put_nowait(pcm_data)
            except asyncio.QueueFull:
                # Drop oldest if full
                try:
                    session.input_queue.get_nowait()
                    session.input_queue.put_nowait(pcm_data)
                except:
                    pass
                    
        except Exception as e:
            logger.error(f"Error processing RTP audio: {e}", extra={"call_id": call_id})
        
        # Add decoded PCM to recording buffer (Day 10)
        if call_id in self._recording_buffers and pcm_data:
            self._recording_buffers[call_id].add_chunk(pcm_data)
    
    async def send_audio(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Send audio to remote endpoint via RTP.
        
        Handles full conversion pipeline:
        - Format conversion (F32 to PCM16)
        - Resampling (22050/16000 Hz to 8000 Hz)
        - G.711 encoding
        - RTP packetization
        - UDP transmission
        
        Args:
            call_id: Unique call identifier
            audio_chunk: Raw audio data (PCM F32 or PCM16)
        """
        if call_id not in self._sessions:
            logger.warning(f"Cannot send audio for unknown call: {call_id}")
            return
        
        session = self._sessions[call_id]
        
        if not session.udp_socket:
            logger.error(f"No socket for call: {call_id}")
            return
        
        try:
            # Convert audio to G.711 at 8000Hz
            g711_audio = convert_for_rtp(
                audio_chunk,
                source_rate=self._source_sample_rate,
                source_format=self._source_format,
                codec=session.codec
            )
            
            # Build RTP packets (multiple if audio is long)
            rtp_packets = session.rtp_builder.build_packets_from_audio(
                g711_audio,
                mark_first=(session.packets_sent == 0)
            )
            
            # Send packets via UDP
            loop = asyncio.get_event_loop()
            
            for packet in rtp_packets:
                await loop.run_in_executor(
                    None,
                    lambda p=packet: session.udp_socket.sendto(
                        p,
                        (session.remote_ip, session.remote_port)
                    )
                )
                session.packets_sent += 1
                session.bytes_sent += len(packet)
            
            logger.debug(
                f"Sent {len(rtp_packets)} RTP packets ({len(g711_audio)} bytes G.711)",
                extra={"call_id": call_id}
            )
            
            # Also queue for output (for monitoring/testing)
            try:
                await session.output_queue.put(audio_chunk)
            except asyncio.QueueFull:
                pass
                
        except Exception as e:
            logger.error(
                f"Error sending RTP audio: {e}",
                extra={"call_id": call_id, "error": str(e)}
            )
    
    async def on_call_ended(
        self,
        call_id: str,
        reason: str
    ) -> None:
        """
        Handle call end event.
        
        Logs metrics and cleans up RTP session.
        
        Args:
            call_id: Unique call identifier
            reason: Reason for call ending
        """
        logger.info(
            f"RTP call ended: {call_id} (reason: {reason})",
            extra={"call_id": call_id, "reason": reason}
        )
        
        if call_id not in self._sessions:
            return
        
        session = self._sessions[call_id]
        
        # Log session metrics
        duration = 0
        if session.started_at:
            duration = (datetime.utcnow() - session.started_at).total_seconds()
        
        logger.info(
            f"RTP session metrics for {call_id}",
            extra={
                "call_id": call_id,
                "duration_seconds": duration,
                "packets_sent": session.packets_sent,
                "packets_received": session.packets_received,
                "bytes_sent": session.bytes_sent,
                "bytes_received": session.bytes_received
            }
        )
        
        # Close socket
        if session.udp_socket:
            try:
                session.udp_socket.close()
            except:
                pass
        
        # Remove session
        del self._sessions[call_id]
    
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get the audio input queue for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Audio queue or None if call not found
        """
        session = self._sessions.get(call_id)
        return session.input_queue if session else None
    
    def get_output_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """
        Get the audio output queue for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            Output queue or None if call not found
        """
        session = self._sessions.get(call_id)
        return session.output_queue if session else None
    
    def get_session(self, call_id: str) -> Optional[RTPSession]:
        """
        Get RTP session for a call.
        
        Args:
            call_id: Call identifier
            
        Returns:
            RTPSession or None
        """
        return self._sessions.get(call_id)
    
    # =========================================================================
    # Recording Buffer Methods (Day 10)
    # =========================================================================
    
    def get_recording_buffer(self, call_id: str):
        """
        Get the recording buffer for a call.
        
        Args:
            call_id: Unique call identifier
            
        Returns:
            RecordingBuffer or None if call not found
        """
        return self._recording_buffers.get(call_id)
    
    def clear_recording_buffer(self, call_id: str) -> None:
        """
        Clear the recording buffer for a call.
        
        Args:
            call_id: Unique call identifier
        """
        if call_id in self._recording_buffers:
            self._recording_buffers[call_id].clear()
            del self._recording_buffers[call_id]
            logger.debug(f"Recording buffer cleared for call {call_id}")
    
    async def cleanup(self) -> None:
        """Release all resources and clean up."""
        logger.info("Cleaning up RTP Media Gateway")
        
        # Close all sockets
        for call_id, session in list(self._sessions.items()):
            if session.udp_socket:
                try:
                    session.udp_socket.close()
                except:
                    pass
        
        self._sessions.clear()
        logger.info("RTP Media Gateway cleanup complete")
    
    @property
    def name(self) -> str:
        """Provider name."""
        return "rtp"
    
    def __repr__(self) -> str:
        return (
            f"RTPMediaGateway("
            f"remote={self._default_remote_ip}:{self._default_remote_port}, "
            f"codec={self._default_codec}, "
            f"active_calls={len(self._sessions)})"
        )
