"""
SIP Media Gateway Implementation
Bridges SIP/RTP audio from softphones (MicroSIP) to Talky.ai voice pipeline.

Day 18: Implements MediaGateway interface for SIP-based telephony.

Architecture:
    MicroSIP → SIP/RTP → SIPMediaGateway → VoicePipelineService
              (G.711)    (converts to PCM)    (STT→LLM→TTS)
"""
import asyncio
import logging
import audioop
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.interfaces.media_gateway import MediaGateway

logger = logging.getLogger(__name__)


@dataclass
class SIPSession:
    """Session state for a SIP call"""
    call_id: str
    remote_addr: tuple  # (host, port)
    rtp_port: int
    codec: str = "PCMU"  # G.711 μ-law
    created_at: datetime = field(default_factory=datetime.utcnow)
    audio_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    recording_buffer: list = field(default_factory=list)
    is_active: bool = True
    chunks_received: int = 0
    chunks_sent: int = 0
    total_bytes_received: int = 0
    total_bytes_sent: int = 0


class SIPMediaGateway(MediaGateway):
    """
    Media gateway for SIP-based voice calls.
    
    Handles:
    - G.711 μ-law (PCMU) audio from MicroSIP
    - Converts 8kHz → 16kHz for STT
    - Sends TTS audio back via RTP
    
    Audio Format:
    - Input:  G.711 μ-law, 8000 Hz, mono (from SIP)
    - Output: PCM s16le, 16000 Hz, mono (for pipeline)
    - Return: PCM s16le, 16000 Hz → G.711 (for SIP)
    """
    
    def __init__(self):
        self._sessions: Dict[str, SIPSession] = {}
        self._config: Dict[str, Any] = {}
        self._initialized = False
        
        # Audio conversion state
        self._ulaw_to_linear_state = None
        self._resample_state = None
    
    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the SIP media gateway"""
        self._config = config
        self._initialized = True
        
        logger.info(
            "SIP Media Gateway initialized",
            extra={
                "input_rate": config.get("audio", {}).get("input_sample_rate", 8000),
                "output_rate": config.get("audio", {}).get("output_sample_rate", 16000)
            }
        )
    
    async def on_call_started(
        self,
        call_id: str,
        metadata: Dict[str, Any]
    ) -> None:
        """
        Handle SIP call start.
        
        Args:
            call_id: Unique call identifier
            metadata: Contains 'remote_addr', 'rtp_port', 'codec'
        """
        remote_addr = metadata.get("remote_addr", ("0.0.0.0", 0))
        rtp_port = metadata.get("rtp_port", 0)
        codec = metadata.get("codec", "PCMU")
        
        session = SIPSession(
            call_id=call_id,
            remote_addr=remote_addr,
            rtp_port=rtp_port,
            codec=codec
        )
        self._sessions[call_id] = session
        
        logger.info(
            "SIP call started",
            extra={
                "call_id": call_id,
                "remote_addr": f"{remote_addr[0]}:{remote_addr[1]}",
                "rtp_port": rtp_port,
                "codec": codec
            }
        )
    
    async def on_audio_received(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Handle RTP audio chunk from SIP phone.
        
        Converts G.711 μ-law (8kHz) to PCM (16kHz) for STT.
        
        Args:
            call_id: Call identifier
            audio_chunk: Raw RTP audio payload (G.711)
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            logger.warning(f"Audio received for unknown/inactive session: {call_id}")
            return
        
        try:
            # Step 1: Decode G.711 μ-law → PCM 16-bit (8kHz)
            pcm_8k = audioop.ulaw2lin(audio_chunk, 2)  # 2 bytes = 16-bit
            
            # Step 2: Resample 8kHz → 16kHz for STT
            pcm_16k, self._resample_state = audioop.ratecv(
                pcm_8k,
                2,      # sample width (bytes)
                1,      # channels
                8000,   # input rate
                16000,  # output rate
                self._resample_state
            )
            
            # Queue for STT pipeline
            try:
                session.audio_queue.put_nowait(pcm_16k)
                session.chunks_received += 1
                session.total_bytes_received += len(audio_chunk)
            except asyncio.QueueFull:
                logger.warning(f"Audio queue full for {call_id}, dropping chunk")
            
            # Recording buffer
            session.recording_buffer.append(pcm_16k)
            
        except Exception as e:
            logger.error(f"Audio conversion error: {e}", extra={"call_id": call_id})
    
    async def send_audio(
        self,
        call_id: str,
        audio_chunk: bytes
    ) -> None:
        """
        Send TTS audio to SIP phone via RTP.
        
        Converts PCM (16kHz) to G.711 μ-law (8kHz) for transmission.
        
        Args:
            call_id: Call identifier
            audio_chunk: PCM audio from TTS (16kHz)
        """
        session = self._sessions.get(call_id)
        if not session or not session.is_active:
            logger.warning(f"Cannot send audio to unknown/inactive session: {call_id}")
            return
        
        try:
            # Step 1: Resample 16kHz → 8kHz
            pcm_8k, _ = audioop.ratecv(
                audio_chunk,
                2,      # sample width
                1,      # channels
                16000,  # input rate
                8000,   # output rate
                None
            )
            
            # Step 2: Encode PCM → G.711 μ-law
            ulaw_audio = audioop.lin2ulaw(pcm_8k, 2)
            
            session.chunks_sent += 1
            session.total_bytes_sent += len(ulaw_audio)
            
            # TODO: Actually send via RTP socket
            # This will be implemented when we add RTP transport
            logger.debug(
                f"Audio ready for RTP: {len(ulaw_audio)} bytes",
                extra={"call_id": call_id}
            )
            
        except Exception as e:
            logger.error(f"Audio encoding error: {e}", extra={"call_id": call_id})
    
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """Get audio input queue for STT pipeline"""
        session = self._sessions.get(call_id)
        return session.audio_queue if session else None
    
    async def on_call_ended(
        self,
        call_id: str,
        reason: str
    ) -> None:
        """
        Handle SIP call end.
        
        Args:
            call_id: Call identifier
            reason: Reason for ending (BYE, timeout, error)
        """
        session = self._sessions.get(call_id)
        if not session:
            logger.warning(f"End request for unknown session: {call_id}")
            return
        
        session.is_active = False
        
        # Calculate stats
        duration = (datetime.utcnow() - session.created_at).total_seconds()
        
        logger.info(
            "SIP call ended",
            extra={
                "call_id": call_id,
                "reason": reason,
                "duration_seconds": duration,
                "chunks_received": session.chunks_received,
                "chunks_sent": session.chunks_sent,
                "bytes_received": session.total_bytes_received,
                "bytes_sent": session.total_bytes_sent
            }
        )
        
        # Cleanup
        del self._sessions[call_id]
    
    def get_recording_buffer(self, call_id: str) -> Optional[list]:
        """Get recording buffer for session"""
        session = self._sessions.get(call_id)
        return session.recording_buffer if session else None
    
    def clear_recording_buffer(self, call_id: str) -> None:
        """Clear recording buffer to free memory"""
        session = self._sessions.get(call_id)
        if session:
            session.recording_buffer = []
    
    async def cleanup(self) -> None:
        """Clean up all sessions"""
        for call_id in list(self._sessions.keys()):
            await self.on_call_ended(call_id, "gateway_cleanup")
        
        self._sessions.clear()
        logger.info("SIP Media Gateway cleaned up")
    
    @property
    def name(self) -> str:
        return "sip"
    
    def get_session(self, call_id: str) -> Optional[SIPSession]:
        """Get session by call_id"""
        return self._sessions.get(call_id)
    
    def is_session_active(self, call_id: str) -> bool:
        """Check if session is active"""
        session = self._sessions.get(call_id)
        return session.is_active if session else False
    
    def get_session_metrics(self, call_id: str) -> Optional[Dict[str, Any]]:
        """Get session metrics for display"""
        session = self._sessions.get(call_id)
        if not session:
            return None
        
        return {
            "call_id": call_id,
            "remote_addr": f"{session.remote_addr[0]}:{session.remote_addr[1]}",
            "codec": session.codec,
            "duration_seconds": (datetime.utcnow() - session.created_at).total_seconds(),
            "chunks_received": session.chunks_received,
            "chunks_sent": session.chunks_sent,
            "is_active": session.is_active
        }
