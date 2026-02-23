"""
Voice Orchestrator — Day 41
Centralises call lifecycle management: provider init → session → greeting → pipeline → cleanup.

All WebSocket endpoints delegate to this orchestrator instead of
duplicating provider creation, session setup, and teardown logic.

Usage:
    from app.core.container import get_container

    orchestrator = get_container().voice_orchestrator
    session = await orchestrator.create_voice_session(config)
    pipeline_task = await orchestrator.start_pipeline(session, websocket)
    await orchestrator.send_greeting(session, "Hi!", websocket, barge_in_event)
    # … message loop …
    await orchestrator.end_session(session)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import AgentConfig
from app.domain.models.voice_contract import generate_talklee_call_id
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.domain.repositories.call_event_repository import CallEventRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass — passed by each endpoint
# ---------------------------------------------------------------------------

@dataclass
class VoiceSessionConfig:
    """All parameters needed to spin up a voice session."""

    # Provider selection
    stt_provider_type: str = "deepgram_flux"
    llm_provider_type: str = "groq"
    tts_provider_type: str = "google"  # "google" | "deepgram"

    # STT settings
    stt_model: str = "flux-general-en"
    stt_sample_rate: int = 16000
    stt_encoding: str = "linear16"
    stt_eot_threshold: float = 0.7
    stt_eager_eot_threshold: Optional[float] = None
    stt_eot_timeout_ms: int = 5000

    # LLM settings
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.6
    llm_max_tokens: int = 150

    # TTS settings
    voice_id: str = "en-US-Chirp3-HD-Leda"
    tts_sample_rate: int = 24000

    # Gateway
    gateway_type: str = "browser"  # "browser" | "rtp" | "vonage" | "sip"
    gateway_sample_rate: int = 24000
    gateway_channels: int = 1
    gateway_bit_depth: int = 16

    # Session metadata
    session_type: str = "ask_ai"  # "ask_ai" | "voice_demo" | "freeswitch"
    agent_config: Optional[AgentConfig] = None
    system_prompt: str = ""
    campaign_id: str = "ask-ai"
    lead_id: str = "demo-user"


# ---------------------------------------------------------------------------
# VoiceSession — container for a running session's resources
# ---------------------------------------------------------------------------

@dataclass
class VoiceSession:
    """Holds all resources for one active voice session."""

    call_id: str
    talklee_call_id: str
    call_session: CallSession

    # Providers (set after creation)
    stt_provider: Any = None
    llm_provider: Any = None
    tts_provider: Any = None
    media_gateway: Any = None
    pipeline: Optional[VoicePipelineService] = None

    # Event logging
    event_repo: Optional[CallEventRepository] = None
    leg_id: Optional[str] = None

    # Config used to create this session
    config: Optional[VoiceSessionConfig] = None

    # Runtime
    pipeline_task: Optional[asyncio.Task] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# VoiceOrchestrator service
# ---------------------------------------------------------------------------

class VoiceOrchestrator:
    """
    Owns the full call lifecycle: init → greet → pipeline → cleanup.

    Responsibilities:
    - Create and initialise STT, LLM, TTS providers
    - Create BrowserMediaGateway and VoicePipelineService
    - Generate talklee_call_id and create CallSession
    - Log call events via CallEventRepository (when PostgreSQL available)
    - Tear down all resources on session end
    """

    def __init__(self, db_client=None):
        """
        Args:
            db_client: Optional PostgreSQL client for event logging.
        """
        self._db_client = db_client
        self._active_sessions: Dict[str, VoiceSession] = {}
        logger.info("VoiceOrchestrator initialised")

    # ------------------------------------------------------------------
    # 1. Create session
    # ------------------------------------------------------------------

    async def create_voice_session(
        self, config: VoiceSessionConfig
    ) -> VoiceSession:
        """
        Initialise providers, create a CallSession, and set up event logging.

        Returns a fully-wired VoiceSession.
        """
        call_id = str(uuid.uuid4())
        talklee_call_id = generate_talklee_call_id()

        logger.info(
            f"Creating voice session call_id={call_id[:8]} "
            f"talklee={talklee_call_id} type={config.session_type}"
        )

        # --- Initialise providers ---
        stt_provider = await self._create_stt_provider(config)
        llm_provider = await self._create_llm_provider(config)
        tts_provider = await self._create_tts_provider(config)
        media_gateway = await self._create_media_gateway(config)

        # --- Build pipeline ---
        pipeline = VoicePipelineService(
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=media_gateway,
            stt_sample_rate=config.stt_sample_rate,
            tts_sample_rate=config.tts_sample_rate,
        )

        # --- Build CallSession ---
        call_session = CallSession(
            call_id=call_id,
            campaign_id=config.campaign_id,
            lead_id=config.lead_id,
            vonage_call_uuid=f"{config.session_type}-session",
            state=CallState.ACTIVE,
            conversation_state=ConversationState.GREETING,
            conversation_context=ConversationContext(),
            agent_config=config.agent_config,
            system_prompt=config.system_prompt,
            llm_model=config.llm_model,
            llm_temperature=config.llm_temperature,
            llm_max_tokens=config.llm_max_tokens,
            voice_id=config.voice_id,
            started_at=datetime.utcnow(),
            last_activity_at=datetime.utcnow(),
        )
        call_session.talklee_call_id = talklee_call_id

        # --- Event logging ---
        event_repo = None
        leg_id = None
        if self._db_client and hasattr(self._db_client, "table"):
            event_repo = CallEventRepository(self._db_client)
            try:
                leg_id = await event_repo.create_leg(
                    call_id=call_id,
                    talklee_call_id=talklee_call_id,
                    leg_type=_session_leg_type(config),
                    direction="inbound",
                    provider=_session_provider(config),
                    metadata={
                        "session_type": config.session_type,
                    },
                )
                await event_repo.log_event(
                    call_id=call_id,
                    talklee_call_id=talklee_call_id,
                    leg_id=leg_id,
                    event_type="session_start",
                    source="voice_orchestrator",
                    event_data={
                        "session_type": config.session_type,
                        "voice_id": config.voice_id,
                    },
                )
            except Exception as evt_err:
                logger.debug(f"Event logging failed (non-critical): {evt_err}")
        elif self._db_client:
            logger.debug(
                "Skipping call event logging: provided client does not implement table()"
            )

        # --- Assemble VoiceSession ---
        voice_session = VoiceSession(
            call_id=call_id,
            talklee_call_id=talklee_call_id,
            call_session=call_session,
            stt_provider=stt_provider,
            llm_provider=llm_provider,
            tts_provider=tts_provider,
            media_gateway=media_gateway,
            pipeline=pipeline,
            event_repo=event_repo,
            leg_id=leg_id,
            config=config,
        )
        self._active_sessions[call_id] = voice_session

        logger.info(f"Voice session created: {call_id[:8]}")
        return voice_session

    # ------------------------------------------------------------------
    # 2. Start pipeline
    # ------------------------------------------------------------------

    async def start_pipeline(
        self, session: VoiceSession, websocket: WebSocket
    ) -> asyncio.Task:
        """
        Start the voice pipeline as an asyncio task.

        The caller is responsible for cancelling the returned task
        when the WebSocket message loop ends.
        """
        if not session.pipeline:
            raise RuntimeError("Pipeline not initialised")

        await session.media_gateway.on_call_started(
            session.call_id, {"websocket": websocket}
        )

        async def _pipeline_with_error_handling():
            try:
                await session.pipeline.start_pipeline(
                    session.call_session, websocket
                )
            except Exception as e:
                logger.error(f"Pipeline error for {session.call_id[:8]}: {e}")
                raise

        task = asyncio.create_task(_pipeline_with_error_handling())
        session.pipeline_task = task

        logger.info(f"Pipeline started for {session.call_id[:8]}")
        return task

    # ------------------------------------------------------------------
    # 3. Send greeting
    # ------------------------------------------------------------------

    def _clean_text_for_tts(self, text: str) -> str:
        """
        Clean text for TTS by removing markdown, special characters, and emojis.
        
        Order matters - process complex patterns before simple ones to avoid
        interference (e.g., markdown links before URL removal).
        """
        import re
        
        if not text:
            return text
        
        # 1. First handle markdown links (before URL removal)
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # 2. Remove code blocks and inline code
        cleaned = re.sub(r'```[\s\S]*?```', ' code block ', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        
        # 3. Remove standalone URLs (after markdown links)
        cleaned = re.sub(r'https?://\S+', ' link ', cleaned)
        cleaned = re.sub(r'www\.\S+', ' website ', cleaned)
        
        # 4. Remove markdown formatting (bold, italic, strikethrough)
        cleaned = re.sub(r'\*\*\*?|\*\*?|__?|~~', '', cleaned)
        
        # 5. Remove headers
        cleaned = re.sub(r'^#{1,6}\s*', '', cleaned, flags=re.MULTILINE)
        
        # 6. Remove blockquotes
        cleaned = re.sub(r'^>\s*', '', cleaned, flags=re.MULTILINE)
        
        # 7. Remove emojis (comprehensive range)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"  # dingbats
            "\U000024C2-\U0001F251"  # enclosed characters
            "\U0001F900-\U0001F9FF"  # supplemental symbols
            "\U00002600-\U000026FF"  # miscellaneous symbols
            "]+",
            flags=re.UNICODE
        )
        cleaned = emoji_pattern.sub('', cleaned)
        
        # 8. Replace common symbols with spoken words
        replacements = {
            '&': ' and ', '@': ' at ', '#': ' number ',
            '$': ' dollars ', '%': ' percent ', '+': ' plus ',
            '=': ' equals ', '→': ' to ', '←': ' from ',
            '•': ', ', '·': ', ', '…': '...', '|': ', ',
            '™': ' trademark ', '®': ' registered ', '©': ' copyright ',
            '°': ' degrees ', '×': ' times ', '÷': ' divided by ',
            '–': '-', '—': '-',  # en-dash and em-dash to hyphen
        }
        for symbol, spoken in replacements.items():
            cleaned = cleaned.replace(symbol, spoken)
        
        # 9. Remove bullet points (but preserve the text)
        cleaned = re.sub(r'^[\s]*[-*+•]\s+', '', cleaned, flags=re.MULTILINE)
        
        # 10. Normalize whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'!{2,}', '!', cleaned)
        cleaned = re.sub(r'\?{2,}', '?', cleaned)
        cleaned = re.sub(r'\.{3,}', '...', cleaned)
        
        return cleaned.strip()

    async def send_greeting(
        self,
        session: VoiceSession,
        greeting_text: str,
        websocket: WebSocket,
        barge_in_event: asyncio.Event,
        first_chunk_bytes: int = 48000,
        regular_chunk_bytes: int = 96000,
    ) -> None:
        """
        Synthesise and stream a greeting via the TTS provider.

        Supports barge-in interruption through *barge_in_event*.
        """
        # Clean text for TTS
        cleaned_greeting = self._clean_text_for_tts(greeting_text)
        
        # Send text to frontend (original text for display)
        await websocket.send_json({
            "type": "llm_response",
            "text": cleaned_greeting,
            "latency_ms": 0,
        })

        tts_start = time.time()
        was_interrupted = False
        audio_buffer = bytearray()
        chunks_sent = 0

        try:
            # Mute STT during greeting TTS to prevent echo
            if session.stt_provider and hasattr(session.stt_provider, 'mute'):
                await session.stt_provider.mute(session.call_id)
                logger.debug(f"Muted STT for call {session.call_id} during greeting")
            
            async for audio_chunk in session.tts_provider.stream_synthesize(
                text=cleaned_greeting,
                voice_id=session.config.voice_id if session.config else "default",
                sample_rate=session.config.tts_sample_rate if session.config else 24000,
            ):
                if barge_in_event.is_set():
                    was_interrupted = True
                    barge_in_event.clear()
                    await websocket.send_json({
                        "type": "tts_interrupted",
                        "reason": "barge_in",
                    })
                    break

                audio_buffer.extend(audio_chunk.data)

                target_size = (
                    first_chunk_bytes if chunks_sent == 0 else regular_chunk_bytes
                )
                if len(audio_buffer) >= target_size:
                    await websocket.send_bytes(bytes(audio_buffer))
                    audio_buffer = bytearray()
                    chunks_sent += 1

            # Flush remaining audio
            if audio_buffer and not was_interrupted:
                await websocket.send_bytes(bytes(audio_buffer))

        except Exception as e:
            logger.error(f"Greeting TTS error: {e}")
        
        finally:
            # Unmute STT after greeting (with delay)
            if session.stt_provider and hasattr(session.stt_provider, 'unmute'):
                await asyncio.sleep(0.3)
                await session.stt_provider.unmute(session.call_id)
                logger.debug(f"Unmuted STT for call {session.call_id} after greeting")

        tts_latency = (time.time() - tts_start) * 1000

        await websocket.send_json({
            "type": "turn_complete",
            "llm_latency_ms": 0,
            "tts_latency_ms": tts_latency,
            "total_latency_ms": tts_latency,
            "was_interrupted": was_interrupted,
        })

    # ------------------------------------------------------------------
    # 4. End session — clean up everything
    # ------------------------------------------------------------------

    async def end_session(self, session: VoiceSession) -> None:
        """
        Shut down all providers, cancel the pipeline task, and log events.
        """
        call_id = session.call_id
        logger.info(f"Ending voice session {call_id[:8]}")

        # Cancel pipeline task
        if session.pipeline_task and not session.pipeline_task.done():
            session.pipeline_task.cancel()
            try:
                await session.pipeline_task
            except asyncio.CancelledError:
                pass

        # Log session end event
        if session.event_repo and session.call_session:
            try:
                await session.event_repo.log_event(
                    call_id=call_id,
                    talklee_call_id=session.talklee_call_id,
                    leg_id=session.leg_id,
                    event_type="session_end",
                    source="voice_orchestrator",
                    event_data={"session_type": session.config.session_type if session.config else "unknown"},
                )
            except Exception as evt_err:
                logger.debug(f"Failed to log session end: {evt_err}")

        # Tear down gateway
        if session.media_gateway:
            try:
                await session.media_gateway.on_call_ended(call_id, "session_ended")
            except Exception:
                pass
            try:
                await session.media_gateway.cleanup()
            except Exception:
                pass

        # Tear down providers
        for provider_name in ("stt_provider", "llm_provider", "tts_provider"):
            provider = getattr(session, provider_name, None)
            if provider:
                try:
                    await provider.cleanup()
                except Exception:
                    pass

        # Remove from active sessions
        self._active_sessions.pop(call_id, None)

        logger.info(f"Voice session ended: {call_id[:8]}")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_session(self, call_id: str) -> Optional[VoiceSession]:
        """Retrieve an active VoiceSession by call_id."""
        return self._active_sessions.get(call_id)

    @property
    def active_session_count(self) -> int:
        return len(self._active_sessions)

    # ------------------------------------------------------------------
    # Private: provider factories
    # ------------------------------------------------------------------

    async def _create_stt_provider(self, config: VoiceSessionConfig):
        """Initialise and return the STT provider."""
        from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider

        provider = DeepgramFluxSTTProvider()
        await provider.initialize({
            "api_key": os.getenv("DEEPGRAM_API_KEY"),
            "model": config.stt_model,
            "sample_rate": config.stt_sample_rate,
            "encoding": config.stt_encoding,
            "eot_threshold": config.stt_eot_threshold,
            "eager_eot_threshold": config.stt_eager_eot_threshold,
            "eot_timeout_ms": config.stt_eot_timeout_ms,
        })
        return provider

    async def _create_llm_provider(self, config: VoiceSessionConfig):
        """Initialise and return the LLM provider."""
        from app.infrastructure.llm.groq import GroqLLMProvider

        provider = GroqLLMProvider()
        await provider.initialize({
            "api_key": os.getenv("GROQ_API_KEY"),
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
        })
        return provider

    async def _create_tts_provider(self, config: VoiceSessionConfig):
        """Initialise and return the TTS provider."""
        if config.tts_provider_type == "deepgram":
            from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider

            provider = DeepgramTTSProvider()
            await provider.initialize({
                "api_key": os.getenv("DEEPGRAM_API_KEY"),
                "voice_id": config.voice_id,
                "sample_rate": config.tts_sample_rate,
            })
        else:
            # Default: Google TTS Streaming
            from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

            provider = GoogleTTSStreamingProvider()
            await provider.initialize({
                "voice_id": config.voice_id,
                "sample_rate": config.tts_sample_rate,
            })
        return provider

    async def _create_media_gateway(self, config: VoiceSessionConfig):
        """Initialise and return a media gateway via the factory."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory
        from app.core.voice_config import get_voice_config

        _vc = get_voice_config()
        gateway = MediaGatewayFactory.create(config.gateway_type)

        # Build init config — add provider-specific fields when needed
        init_config = {
            "sample_rate": config.gateway_sample_rate,
            "channels": config.gateway_channels,
            "bit_depth": config.gateway_bit_depth,
            "tts_source_format": "s16le" if config.tts_provider_type == "deepgram" else "f32le",
        }
        if config.gateway_type == "rtp":
            init_config.update({
                "source_sample_rate": config.tts_sample_rate,
                "source_format": _vc.tts_source_format,
                "codec": _vc.rtp_codec,
            })

        # Telephony mode: FreeSWITCH needs Float32→Int16 conversion
        if config.session_type == "freeswitch":
            init_config["telephony_mode"] = True

        await gateway.initialize(init_config)
        return gateway


# ---------------------------------------------------------------------------
# Helpers — session-type → event metadata
# ---------------------------------------------------------------------------

def _session_leg_type(config: VoiceSessionConfig) -> str:
    """Map session type to the leg_type used in call event logging."""
    return {"freeswitch": "sip", "voice_demo": "browser"}.get(
        config.session_type, "websocket"
    )


def _session_provider(config: VoiceSessionConfig) -> str:
    """Map session type to the provider name used in call event logging."""
    return {"freeswitch": "freeswitch", "voice_demo": "browser"}.get(
        config.session_type, "browser"
    )
