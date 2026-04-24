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
    gateway_type: str = "browser"  # "browser" | "telephony"
    gateway_sample_rate: int = 24000
    gateway_channels: int = 1
    gateway_bit_depth: int = 16

    # Session metadata
    # session_type "telephony" covers ANY SIP B2BUA call (Asterisk or FreeSWITCH).
    # "freeswitch" is kept as an alias for backwards compat and maps to "telephony".
    # "vonage" indicates a Vonage-originated call.
    session_type: str = "ask_ai"  # "ask_ai" | "voice_demo" | "telephony" | "freeswitch" | "vonage"
    telephony_provider: str = "sip"  # "sip" | "vonage" | "twilio" | "browser"
    agent_config: Optional[AgentConfig] = None
    system_prompt: str = ""
    campaign_id: str = "ask-ai"
    lead_id: str = "demo-user"
    # Only enable this when the session is backed by a real row in `calls`.
    # Browser demos generate ephemeral call_ids that do not satisfy the FK.
    event_logging_enabled: bool = False


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

    def __init__(self, db_client=None, secrets_manager=None):
        """
        Args:
            db_client: Optional PostgreSQL client for event logging.
            secrets_manager: Optional SecretsManager for API keys.
        """
        self._db_client = db_client
        self._secrets_manager = secrets_manager
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
            provider_call_id=f"{config.session_type}-session",
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
        if config.event_logging_enabled and self._db_client and hasattr(self._db_client, "table"):
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
        elif config.event_logging_enabled and self._db_client:
            logger.debug(
                "Skipping call event logging: provided client does not implement table()"
            )
        else:
            logger.debug(
                "Skipping call event logging for %s session %s: no persisted calls row",
                config.session_type,
                call_id[:8],
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
        self, session: VoiceSession, websocket: Optional[WebSocket]
    ) -> asyncio.Task:
        """
        Start the voice pipeline as an asyncio task.

        When *websocket* is not None (browser path), this method also calls
        ``media_gateway.on_call_started`` so the gateway creates its session.

        When *websocket* is None (telephony/Asterisk path), the caller is
        responsible for having called ``media_gateway.on_call_started`` before
        invoking this method — ``TelephonyMediaGateway`` does not need a
        WebSocket and is initialised directly by the telephony bridge.
        """
        if not session.pipeline:
            raise RuntimeError("Pipeline not initialised")

        if websocket is not None:
            # Browser path: let the orchestrator own the gateway session setup.
            await session.media_gateway.on_call_started(
                session.call_id, {"websocket": websocket}
            )
        # Telephony path: on_call_started was already called by the bridge with
        # adapter/pbx_call_id metadata; no action needed here.

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
        """Delegate to the shared audio_utils helper to avoid code duplication."""
        from app.utils.audio_utils import clean_text_for_tts
        return clean_text_for_tts(text)

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
        sent_audio = False
        waited_for_browser_playback = False

        try:
            # Mute STT during greeting TTS to prevent echo
            if session.stt_provider and hasattr(session.stt_provider, 'mute'):
                await session.stt_provider.mute(session.call_id)
                logger.debug(f"Muted STT for call {session.call_id} during greeting")

            if hasattr(session.media_gateway, "start_playback_tracking"):
                session.media_gateway.start_playback_tracking(session.call_id)
            
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

                # Route greeting audio through the media gateway so browser
                # sessions use the same format conversion and buffering path as
                # normal replies.
                await session.media_gateway.send_audio(
                    session.call_id,
                    audio_chunk.data,
                )
                sent_audio = True

            # Flush any buffered browser audio at end of greeting.
            if not was_interrupted and hasattr(session.media_gateway, "flush_audio_buffer"):
                await session.media_gateway.flush_audio_buffer(session.call_id)
            if (
                not was_interrupted
                and sent_audio
                and hasattr(session.media_gateway, "wait_for_playback_complete")
            ):
                await websocket.send_json({"type": "tts_audio_complete"})
                waited_for_browser_playback = True
                await session.media_gateway.wait_for_playback_complete(session.call_id)

        except Exception as e:
            logger.error(f"Greeting TTS error: {e}")
        
        finally:
            # Unmute STT after greeting (with delay)
            if session.stt_provider and hasattr(session.stt_provider, 'unmute'):
                await asyncio.sleep(0.05 if waited_for_browser_playback else 0.3)
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

    async def _get_secret(self, secret_name: str, env_fallback: str) -> Optional[str]:
        """Helper to get secret from manager or environment."""
        if self._secrets_manager:
            try:
                # Platform secrets are stored without tenant_id
                secret = await self._secrets_manager.get_secret(
                    secret_name=secret_name,
                    tenant_id=None
                )
                if secret and "api_key" in secret.value:
                    return secret.value["api_key"]
            except Exception as e:
                logger.debug(f"Failed to get secret {secret_name} from manager: {e}")
        
        return os.getenv(env_fallback)

    async def _create_stt_provider(self, config: VoiceSessionConfig):
        """Initialise and return the STT provider."""
        from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider

        api_key = await self._get_secret("DEEPGRAM_API_KEY", "DEEPGRAM_API_KEY")
        
        provider = DeepgramFluxSTTProvider()
        await provider.initialize({
            "api_key": api_key,
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

        api_key = await self._get_secret("GROQ_API_KEY", "GROQ_API_KEY")

        provider = GroqLLMProvider()
        await provider.initialize({
            "api_key": api_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
        })
        return provider

    async def _create_tts_provider(self, config: VoiceSessionConfig):
        """Initialise and return the TTS provider."""
        if config.tts_provider_type == "deepgram":
            from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider

            api_key = await self._get_secret("DEEPGRAM_API_KEY", "DEEPGRAM_API_KEY")

            provider = DeepgramTTSProvider()
            await provider.initialize({
                "api_key": api_key,
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

        gateway = MediaGatewayFactory.create(config.gateway_type)

        init_config = {
            "sample_rate": config.gateway_sample_rate,
            "channels": config.gateway_channels,
            "bit_depth": config.gateway_bit_depth,
            "tts_source_format": "s16le" if config.tts_provider_type == "deepgram" else "f32le",
        }

        # Telephony mode: all SIP B2BUA calls (Asterisk or FreeSWITCH) need
        # Float32→Int16 conversion because the audio bridge delivers 8 kHz PCM.
        # "freeswitch" is kept as a backwards-compatible alias for "telephony".
        if config.session_type in ("telephony", "freeswitch"):
            init_config["telephony_mode"] = True

        await gateway.initialize(init_config)
        return gateway


# ---------------------------------------------------------------------------
# Helpers — session-type → event metadata
# ---------------------------------------------------------------------------

def _session_leg_type(config: VoiceSessionConfig) -> str:
    """Map session type to the leg_type used in call event logging."""
    return {
        "telephony": "sip",
        "freeswitch": "sip",   # backwards-compat alias
        "voice_demo": "browser",
    }.get(config.session_type, "websocket")


def _session_provider(config: VoiceSessionConfig) -> str:
    """Map session type to the provider name used in call event logging."""
    return {
        "telephony": "telephony",
        "freeswitch": "freeswitch",  # backwards-compat alias
        "voice_demo": "browser",
    }.get(config.session_type, "browser")
