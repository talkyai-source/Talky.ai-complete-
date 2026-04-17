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
    await orchestrator.send_greeting(session, "Hi!", websocket)
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
    tts_provider_type: str = "google"  # "google" | "deepgram" | "elevenlabs"

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
    tts_model: str = ""
    tts_sample_rate: int = 24000

    # Gateway
    gateway_type: str = "browser"  # "browser" | "telephony"
    gateway_sample_rate: int = 24000
    gateway_input_sample_rate: Optional[int] = None
    gateway_channels: int = 1
    gateway_bit_depth: int = 16
    gateway_target_buffer_ms: int = 40   # Output buffer coalescing threshold (40ms for faster barge-in)
    mute_during_tts: bool = True

    # Session metadata
    # session_type "telephony" covers ANY SIP B2BUA call (Asterisk or FreeSWITCH).
    # "freeswitch" is kept as an alias for backwards compat and maps to "telephony".
    # "vonage" indicates a Vonage-originated call.
    session_type: str = (
        "ask_ai"  # "ask_ai" | "voice_demo" | "telephony" | "freeswitch" | "vonage"
    )
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

    def __init__(self, db_client=None):
        """
        Args:
            db_client: Optional PostgreSQL client for event logging.
        """
        self._db_client = db_client
        self._active_sessions: Dict[str, VoiceSession] = {}
        # Singleton providers for Ask AI — STT, LLM, and media_gateway are
        # stateless per-call (keyed by call_id internally), safe to share.
        # TTS is intentionally excluded from the singleton — it holds a warm
        # WebSocket and a non-reentrant asyncio.Lock (_synthesis_lock) that
        # serializes all synthesis calls on the same instance.  With shared TTS,
        # N concurrent sessions queue behind that lock: session N waits
        # (N-1) × TTS synthesis time before it can speak.  TTS is created fresh
        # per session instead (cost: ~75ms Deepgram WebSocket handshake, hidden
        # during greeting / pre-warm).
        self._ask_ai_providers: Optional[tuple] = None  # (stt, llm, None, gateway)
        logger.info("VoiceOrchestrator initialised")

    async def prewarm_ask_ai_providers(self) -> None:
        """
        Pre-initialise Ask AI provider singletons at server startup.

        Called once by the container so that the first user who clicks
        "Ask AI" pays zero provider-init cost.  STT, LLM, and media_gateway
        are stateless per call (keyed by call_id internally) — safe to share.
        TTS is NOT pre-warmed here; it is created fresh per session to avoid
        sharing the synthesis lock across concurrent sessions.
        """
        from app.domain.services.ask_ai_session_config import (
            build_ask_ai_session_config,
        )

        config = build_ask_ai_session_config()
        try:
            stt, llm, gateway = await asyncio.gather(
                self._create_stt_provider(config),
                self._create_llm_provider(config),
                self._create_media_gateway(config),
            )
            self._ask_ai_providers = (stt, llm, None, gateway)
            logger.info("Ask AI providers pre-warmed and ready (TTS is per-session)")
        except Exception as e:
            logger.warning(
                f"Ask AI provider pre-warm failed (will init on first request): {e}"
            )

    # ------------------------------------------------------------------
    # 1. Create session
    # ------------------------------------------------------------------

    async def create_voice_session(self, config: VoiceSessionConfig) -> VoiceSession:
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

        # --- Providers: reuse singletons for ask_ai, init fresh for all others ---
        # TTS is always created fresh per-session (see __init__ comment for why).
        if config.session_type == "ask_ai" and self._ask_ai_providers is not None:
            stt_provider, llm_provider, _, media_gateway = self._ask_ai_providers
            tts_provider = await self._create_tts_provider(config)
        else:
            (
                stt_provider,
                llm_provider,
                tts_provider,
                media_gateway,
            ) = await asyncio.gather(
                self._create_stt_provider(config),
                self._create_llm_provider(config),
                self._create_tts_provider(config),
                self._create_media_gateway(config),
            )
            # Cache STT, LLM, and gateway on first successful init for ask_ai.
            # TTS slot is left None — it is never shared.
            if config.session_type == "ask_ai" and self._ask_ai_providers is None:
                self._ask_ai_providers = (
                    stt_provider,
                    llm_provider,
                    None,
                    media_gateway,
                )

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
        call_session.barge_in_event = asyncio.Event()

        # --- Event logging ---
        event_repo = None
        leg_id = None
        if (
            config.event_logging_enabled
            and self._db_client
            and hasattr(self._db_client, "table")
        ):
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
        try:
            self._active_sessions[call_id] = voice_session
            logger.info(f"Voice session created: {call_id[:8]}")
            return voice_session
        except Exception:
            # Ensure we never leave a half-registered zombie session
            self._active_sessions.pop(call_id, None)
            raise

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
                await session.pipeline.start_pipeline(session.call_session, websocket=websocket)
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
        barge_in_event: Optional[asyncio.Event] = None,
        first_chunk_bytes: int = 48000,
        regular_chunk_bytes: int = 96000,
    ) -> None:
        """
        Synthesise and stream a greeting via the TTS provider.

        Uses the session's shared barge-in event so the greeting and normal
        reply TTS react to the same interruption signal.
        """
        # Clean text for TTS
        cleaned_greeting = self._clean_text_for_tts(greeting_text)

        interrupt_event = barge_in_event or session.call_session.barge_in_event
        if interrupt_event is None:
            interrupt_event = asyncio.Event()
            session.call_session.barge_in_event = interrupt_event
        elif session.call_session.barge_in_event is not interrupt_event:
            session.call_session.barge_in_event = interrupt_event

        # Send text to frontend (original text for display)
        await websocket.send_json(
            {
                "type": "llm_response",
                "text": cleaned_greeting,
                "latency_ms": 0,
            }
        )

        tts_start = time.time()
        was_interrupted = False
        sent_audio = False
        waited_for_browser_playback = False

        try:
            mute_during_tts = (
                session.config.mute_during_tts if session.config else True
            )

            if interrupt_event.is_set():
                was_interrupted = True
                interrupt_event.clear()
                await websocket.send_json(
                    {
                        "type": "tts_interrupted",
                        "reason": "barge_in",
                    }
                )

            # Mute STT during greeting TTS to prevent echo
            if (
                not was_interrupted
                and
                mute_during_tts
                and session.stt_provider
                and hasattr(session.stt_provider, "mute")
            ):
                await session.stt_provider.mute(session.call_id)
                logger.debug(f"Muted STT for call {session.call_id} during greeting")

            if not was_interrupted and hasattr(
                session.media_gateway, "start_playback_tracking"
            ):
                session.media_gateway.start_playback_tracking(session.call_id)

            if not was_interrupted:
                async for audio_chunk in session.tts_provider.stream_synthesize(
                    text=cleaned_greeting,
                    voice_id=session.config.voice_id if session.config else "default",
                    sample_rate=(
                        session.config.tts_sample_rate if session.config else 24000
                    ),
                    call_id=session.call_id,
                ):
                    if interrupt_event.is_set():
                        was_interrupted = True
                        interrupt_event.clear()
                        await websocket.send_json(
                            {
                                "type": "tts_interrupted",
                                "reason": "barge_in",
                            }
                        )
                        break

                    # Route greeting audio through the media gateway so browser
                    # sessions use the same format conversion and buffering path as
                    # normal replies.
                    await session.media_gateway.send_audio(
                        session.call_id,
                        audio_chunk.data,
                    )
                    sent_audio = True
                    # Check barge-in immediately after send — it may have fired
                    # during the gateway send await before the next chunk arrives.
                    if interrupt_event.is_set():
                        was_interrupted = True
                        interrupt_event.clear()
                        await websocket.send_json(
                            {"type": "tts_interrupted", "reason": "barge_in"}
                        )
                        break

            # Flush any buffered browser audio at end of greeting.
            if not was_interrupted and hasattr(
                session.media_gateway, "flush_audio_buffer"
            ):
                await session.media_gateway.flush_audio_buffer(session.call_id)
            elif was_interrupted and hasattr(
                session.media_gateway, "clear_output_buffer"
            ):
                await session.media_gateway.clear_output_buffer(session.call_id)
                # Tell Deepgram TTS to stop generating further audio chunks.
                # Without this, already-buffered text continues to produce audio
                # that arrives after the barge-in, causing audio overlap.
                clear_tts = getattr(session.tts_provider, "clear_queue", None)
                if clear_tts:
                    try:
                        await clear_tts()
                    except Exception as _exc:
                        logger.debug("clear_queue on greeting barge-in failed: %s", _exc)
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
            if (
                mute_during_tts
                and session.stt_provider
                and hasattr(session.stt_provider, "unmute")
            ):
                await asyncio.sleep(0.05 if waited_for_browser_playback else 0.1)
                await session.stt_provider.unmute(session.call_id)
                logger.debug(f"Unmuted STT for call {session.call_id} after greeting")

        tts_latency = (time.time() - tts_start) * 1000

        try:
            await websocket.send_json(
                {
                    "type": "turn_complete",
                    "llm_latency_ms": 0,
                    "tts_latency_ms": tts_latency,
                    "total_latency_ms": tts_latency,
                    "was_interrupted": was_interrupted,
                }
            )
        except Exception:
            pass  # WebSocket may have closed before greeting completed

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
                    event_data={
                        "session_type": session.config.session_type
                        if session.config
                        else "unknown"
                    },
                )
            except Exception as evt_err:
                logger.debug(f"Failed to log session end: {evt_err}")

        # Determine whether this session uses singleton (shared) providers.
        # Singleton providers must NOT be cleaned up — they are reused across
        # sessions. Only the per-call session entry inside the gateway is removed.
        is_singleton_session = (
            session.config is not None
            and session.config.session_type == "ask_ai"
            and self._ask_ai_providers is not None
        )

        # Tear down gateway
        if session.media_gateway:
            try:
                # Always remove the per-call entry from the gateway's session map
                await session.media_gateway.on_call_ended(call_id, "session_ended")
            except Exception:
                pass
            if not is_singleton_session:
                # Full gateway teardown only for non-singleton sessions
                try:
                    await session.media_gateway.cleanup()
                except Exception:
                    pass

        # Tear down providers — skip shared singletons (STT, LLM, gateway) to
        # keep them alive for the next session.  TTS is ALWAYS cleaned up because
        # it is per-session (holds a warm WebSocket and synthesis lock).
        tts_provider = getattr(session, "tts_provider", None)
        if tts_provider:
            try:
                await tts_provider.cleanup()
            except Exception:
                pass

        if not is_singleton_session:
            for provider_name in ("stt_provider", "llm_provider"):
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
        await provider.initialize(
            {
                "api_key": os.getenv("DEEPGRAM_API_KEY"),
                "model": config.stt_model,
                "sample_rate": config.stt_sample_rate,
                "encoding": config.stt_encoding,
                "eot_threshold": config.stt_eot_threshold,
                "eager_eot_threshold": config.stt_eager_eot_threshold,
                "eot_timeout_ms": config.stt_eot_timeout_ms,
            }
        )
        return provider

    async def _create_llm_provider(self, config: VoiceSessionConfig):
        """Initialise and return the LLM provider."""
        from app.infrastructure.llm.groq import GroqLLMProvider

        provider = GroqLLMProvider()
        await provider.initialize(
            {
                "api_key": os.getenv("GROQ_API_KEY"),
                "model": config.llm_model,
                "temperature": config.llm_temperature,
                "max_tokens": config.llm_max_tokens,
            }
        )
        # Fire-and-forget warmup: primes httpx HTTP/2 + TLS + DNS so the first
        # real LLM call (the first agent response in user-first mode) reuses a
        # warm socket instead of paying the cold-connect 80–200 ms tax.
        _warm_up = getattr(provider, "warm_up", None)
        if _warm_up is not None:
            asyncio.create_task(_warm_up())
        return provider

    async def _create_tts_provider(self, config: VoiceSessionConfig):
        """Initialise and return the TTS provider."""
        if config.tts_provider_type == "deepgram":
            from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider

            provider = DeepgramTTSProvider()
            await provider.initialize(
                {
                    "api_key": os.getenv("DEEPGRAM_API_KEY"),
                    "voice_id": config.voice_id,
                    "sample_rate": config.tts_sample_rate,
                }
            )
        elif config.tts_provider_type == "elevenlabs":
            from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider

            provider = ElevenLabsTTSProvider()
            await provider.initialize(
                {
                    "api_key": os.getenv("ELEVENLABS_API_KEY"),
                    "voice_id": config.voice_id,
                    "model_id": config.tts_model or "eleven_flash_v2_5",
                    "sample_rate": config.tts_sample_rate,
                }
            )
        elif config.tts_provider_type == "cartesia":
            from app.infrastructure.tts.cartesia import CartesiaTTSProvider

            provider = CartesiaTTSProvider()
            await provider.initialize(
                {
                    "api_key": os.getenv("CARTESIA_API_KEY"),
                    "voice_id": config.voice_id,
                    "model_id": config.tts_model or "sonic-3",
                    "sample_rate": config.tts_sample_rate,
                }
            )
        else:
            # Default: Google TTS Streaming
            from app.infrastructure.tts.google_tts_streaming import (
                GoogleTTSStreamingProvider,
            )

            provider = GoogleTTSStreamingProvider()
            await provider.initialize(
                {
                    "voice_id": config.voice_id,
                    "sample_rate": config.tts_sample_rate,
                }
            )
        return provider

    async def _create_media_gateway(self, config: VoiceSessionConfig):
        """Initialise and return a media gateway via the factory."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory

        gateway = MediaGatewayFactory.create(config.gateway_type)

        init_config = {
            "sample_rate": config.gateway_sample_rate,
            "input_sample_rate": (
                config.gateway_input_sample_rate or config.stt_sample_rate
            ),
            "channels": config.gateway_channels,
            "bit_depth": config.gateway_bit_depth,
            "target_buffer_ms": config.gateway_target_buffer_ms,
            "tts_source_format": "s16le"
            if config.tts_provider_type in {"deepgram", "elevenlabs"}
            else "f32le",
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
        "freeswitch": "sip",  # backwards-compat alias
        "voice_demo": "browser",
    }.get(config.session_type, "websocket")


def _session_provider(config: VoiceSessionConfig) -> str:
    """Map session type to the provider name used in call event logging."""
    return {
        "telephony": "telephony",
        "freeswitch": "freeswitch",  # backwards-compat alias
        "voice_demo": "browser",
    }.get(config.session_type, "browser")
