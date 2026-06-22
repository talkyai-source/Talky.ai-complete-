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
import enum
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


def _failover_enabled(env_var: str) -> bool:
    """Truthy parse for opt-in failover env flags. T1.3."""
    raw = (os.getenv(env_var) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_voice_map(raw: str) -> dict[str, str]:
    """Parse `TTS_SECONDARY_VOICE_MAP` into {primary_voice: secondary_voice}.

    Format: "primary1=secondary1,primary2=secondary2". Whitespace and
    bad entries are tolerated — empty dict on garbage input rather than
    raising at startup.
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in raw.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Call direction — first-class state, not a string sniff
# ---------------------------------------------------------------------------


class Direction(str, enum.Enum):
    """Who initiated the call from the AI agent's perspective.

    * ``OUTBOUND`` — the platform originated the call (campaign dialer).
      The AI is the caller. Default for every existing code path.
    * ``INBOUND``  — the carrier rang us OR the campaign owner explicitly
      configured caller-speaks-first semantics. The AI behaves like the
      receiver who picked up the phone.

    Stored as a string-backed enum so it serialises cleanly into logs,
    OpenTelemetry attributes, and the analytics pipeline without extra
    conversion code. Callers can compare with either ``Direction.INBOUND``
    or the string ``"inbound"`` — both work.
    """

    OUTBOUND = "outbound"
    INBOUND = "inbound"

    @classmethod
    def from_first_speaker(cls, first_speaker: Optional[str]) -> "Direction":
        """Map the legacy per-call ``first_speaker`` flag onto a Direction.

        Pre-T3.10 the codebase carried ``first_speaker = "user"`` to mean
        "treat this as inbound framing". The two concepts are actually
        orthogonal (who speaks first vs. who initiated the call), but
        until per-campaign direction lands in the UI, deriving direction
        from first_speaker preserves the existing intent without needing
        a schema change.
        """
        value = (first_speaker or "").strip().lower()
        return cls.INBOUND if value == "user" else cls.OUTBOUND


# ---------------------------------------------------------------------------
# Configuration dataclass — passed by each endpoint
# ---------------------------------------------------------------------------

# Cached providers.yaml flux keyterms — read once. Used as the default for any
# session that doesn't set stt_keyterms explicitly, so the configured list is
# live on both telephony and ask-AI. The env var DEEPGRAM_FLUX_KEYTERMS still
# overrides at provider initialize() time.
_FLUX_KEYTERMS_CACHE: Optional[list] = None
_FLUX_CAPTURE_KEYTERMS_CACHE: Optional[list] = None


def _default_flux_keyterms() -> list:
    """Load base (always-on) Flux keyterms from providers.yaml (cached).
    Fail-open to []. Email-spelling terms are NOT here — see
    _default_flux_capture_keyterms (capture-mode only)."""
    global _FLUX_KEYTERMS_CACHE
    if _FLUX_KEYTERMS_CACHE is None:
        try:
            from app.core.config import ConfigManager

            stt_cfg = ConfigManager().get_provider_config("stt") or {}
            raw = stt_cfg.get("keyterms") or []
            _FLUX_KEYTERMS_CACHE = [
                str(t).strip() for t in raw if str(t).strip()
            ]
        except Exception as exc:  # config missing/malformed — no biasing
            logger.warning("flux keyterms load failed: %s", exc)
            _FLUX_KEYTERMS_CACHE = []
    return _FLUX_KEYTERMS_CACHE


def _default_flux_capture_keyterms() -> list:
    """Load capture-only Flux keyterms (email domains + spell connectors) from
    providers.yaml (cached). These are injected ONLY during email/spell capture
    mode so words like "dot"/"at"/"dash" never bias ordinary speech."""
    global _FLUX_CAPTURE_KEYTERMS_CACHE
    if _FLUX_CAPTURE_KEYTERMS_CACHE is None:
        try:
            from app.core.config import ConfigManager

            stt_cfg = ConfigManager().get_provider_config("stt") or {}
            raw = stt_cfg.get("capture_keyterms") or []
            _FLUX_CAPTURE_KEYTERMS_CACHE = [
                str(t).strip() for t in raw if str(t).strip()
            ]
        except Exception as exc:  # config missing/malformed — no capture biasing
            logger.warning("flux capture keyterms load failed: %s", exc)
            _FLUX_CAPTURE_KEYTERMS_CACHE = []
    return _FLUX_CAPTURE_KEYTERMS_CACHE


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
    # Keyterm prompting — biases Flux toward expected vocabulary (email
    # domains, spelling connector words). Empty = fall back to the
    # providers.yaml flux keyterms (see _default_flux_keyterms). Applies to
    # BOTH telephony and ask-AI since both build STT via _create_stt_provider.
    stt_keyterms: list[str] = field(default_factory=list)

    # Turn-0 floor — see voice_pipeline_service._should_reject_turn_0.
    # Per-tenant overrides come through the voice_tuning resolver at
    # session-config build time. Defaults match the module-level
    # constants the pipeline used before T3.9 lifted them onto the
    # config so non-telephony sessions keep their historical behaviour.
    turn_0_min_confidence: float = 0.4
    turn_0_min_alpha_chars: int = 2

    # LLM settings
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.6
    llm_max_tokens: int = 150
    # Provider-specific knob, Gemini-only today. None = let the model decide
    # dynamically. 0 = disable thinking entirely (fastest path, recommended for
    # real-time voice agents where "reasoning tokens" are wasted latency).
    # Groq and any future non-thinking provider ignore this field.
    llm_thinking_budget: Optional[int] = None

    # TTS settings
    voice_id: str = "en-US-Chirp3-HD-Leda"
    tts_model: str = ""
    tts_sample_rate: int = 16000

    # Gateway
    gateway_type: str = "browser"  # "browser" | "telephony"
    gateway_sample_rate: int = 24000
    gateway_input_sample_rate: Optional[int] = None
    gateway_channels: int = 1
    gateway_bit_depth: int = 16
    # Coalescing buffer before audio is flushed to the gateway. 100ms is a
    # conservative floor; Vonage telephony runs 40ms cleanly. Env-overridable so
    # ops can lower it on the box and watch voice_turn_latency_seconds without a
    # redeploy. (The deeper root fix is an adaptive jitter-aware buffer — do that
    # on the server where audio smoothness can actually be validated.)
    gateway_target_buffer_ms: int = int(os.getenv("VOICE_GATEWAY_TARGET_BUFFER_MS", "100"))
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
    # Call direction — set by the bridge when the per-call first_speaker
    # is known, used by build_telephony_session_config to pick the right
    # base prompt (inbound vs outbound) up front. Defaults to OUTBOUND so
    # every legacy call path keeps its current behaviour.
    direction: Direction = Direction.OUTBOUND
    # Persona key — "lead_gen" / "customer_support" / "receptionist", or
    # None for legacy/non-telephony sessions. Set by
    # build_telephony_session_config when the campaign has a
    # script_config.persona_type. Used at runtime for greeting selection
    # (T4-A2) and is the natural carrier for any future per-persona
    # decision (voice recommendations, latency tuning, etc.).
    persona_type: Optional[str] = None
    campaign_id: str = "ask-ai"
    lead_id: str = "demo-user"
    # Tenant context for per-tenant credential resolution (T1.1 follow-up).
    # When set, provider creation looks up the tenant's encrypted API key
    # in `tenant_ai_credentials` first, falling back to env vars when no
    # row exists. None = legacy behaviour (env vars only).
    tenant_id: Optional[str] = None
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
        TTS is warmed and immediately discarded; active sessions still create
        fresh TTS instances to avoid sharing the synthesis lock across calls.
        """
        from app.domain.services.ask_ai_session_config import (
            build_ask_ai_session_config,
        )

        config = build_ask_ai_session_config()
        try:
            stt, llm, tts, gateway = await asyncio.gather(
                self._create_stt_provider(config),
                self._create_llm_provider(config),
                self._create_tts_provider(config),
                self._create_media_gateway(config),
            )
            try:
                cleanup = getattr(tts, "cleanup", None)
                if cleanup:
                    await cleanup()
            except Exception:
                logger.debug("Ask AI throwaway TTS cleanup failed", exc_info=True)
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

        Returns a fully-wired VoiceSession ready for pipeline start.
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
            persona_type=config.persona_type,
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

        # Cancel any in-flight turn task BEFORE tearing down the gateway, so a
        # reply still streaming TTS stops sending audio to a channel that's
        # gone (otherwise it logs "no gateway session" and wastes synthesis).
        if session.pipeline is not None:
            try:
                await session.pipeline.cancel_active_turn(call_id)
            except Exception:
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
        """Initialise and return the STT provider.

        T1.1 — Deepgram key resolved via CredentialResolver so a
        per-tenant key wins over the process env var when the
        session has a tenant_id.

        T1.3 — when `STT_FAILOVER_ENABLED=true`, the primary STT is
        wrapped in `ResilientSTTProvider` together with a secondary.
        The secondary today is `DeepgramFluxSTTProvider` with the
        Nova-3 model — same vendor + auth, different model. Operators
        can override the secondary model via `STT_SECONDARY_MODEL`.
        """
        from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )

        api_key = await get_credential_resolver().resolve(
            "deepgram", tenant_id=config.tenant_id,
        )
        primary = DeepgramFluxSTTProvider()
        primary_init = {
            "api_key": api_key,
            "model": config.stt_model,
            "sample_rate": config.stt_sample_rate,
            "encoding": config.stt_encoding,
            "eot_threshold": config.stt_eot_threshold,
            "eager_eot_threshold": config.stt_eager_eot_threshold,
            "eot_timeout_ms": config.stt_eot_timeout_ms,
            # Per-session keyterms win; otherwise fall back to the configured
            # providers.yaml list. Env DEEPGRAM_FLUX_KEYTERMS overrides both
            # inside initialize(). Covers telephony AND ask-AI (shared path).
            "keyterms": config.stt_keyterms or _default_flux_keyterms(),
            # Capture-only keyterms (email domains + spell connectors). Static
            # across campaigns; injected only during email/spell capture mode so
            # they never bias ordinary speech. Env DEEPGRAM_FLUX_CAPTURE_KEYTERMS
            # overrides inside initialize().
            "capture_keyterms": _default_flux_capture_keyterms(),
            # Privacy: keep caller PII out of Deepgram's training set.
            "mip_opt_out": True,
            # Observability: tag each STT session for per-tenant usage + debug.
            "tags": [
                f"tenant:{config.tenant_id or 'none'}",
                f"campaign:{config.campaign_id}",
            ],
        }
        await primary.initialize(primary_init)

        if not _failover_enabled("STT_FAILOVER_ENABLED"):
            return primary

        # Secondary — same vendor, different model. Cheap to set up
        # because the auth + WS shape are identical.
        secondary = DeepgramFluxSTTProvider()
        secondary_init = dict(primary_init)
        secondary_init["model"] = os.getenv("STT_SECONDARY_MODEL") or "flux-general-en"
        try:
            await secondary.initialize(secondary_init)
        except Exception as exc:
            logger.warning(
                "stt_secondary_init_failed err=%s — falling back to primary-only",
                exc,
            )
            return primary

        from app.domain.services.resilient_stt import (
            ReconnectPolicy,
            ResilientSTTProvider,
        )
        wrapper = ResilientSTTProvider(
            primary=primary,
            secondary=secondary,
            policy=ReconnectPolicy(),
        )
        logger.info(
            "stt_resilient_wrapper_active primary=flux-%s secondary=flux-%s",
            config.stt_model, secondary_init["model"],
        )
        return wrapper

    # Map provider type → env var holding its API key. Keep this small and
    # explicit; if it grows past ~5 entries, move it to a config object.
    _LLM_API_KEY_ENV = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }

    # Default secondary model per provider for LLM_FAILOVER_ENABLED. Same-vendor
    # different-model by default (mirrors the STT secondary choice): a Groq stall
    # is usually model / rate-limit specific, and llama-3.1-8b-instant is the
    # fastest Groq model — a clean low-latency fallback. Operators can point the
    # secondary at another vendor (LLM_SECONDARY_PROVIDER=gemini) for true vendor
    # isolation, or override the model with LLM_SECONDARY_MODEL.
    _LLM_DEFAULT_SECONDARY_MODEL = {
        "groq": "llama-3.1-8b-instant",
    }

    # Map TTS primary provider name → secondary provider config tuple
    # (provider_name, env_var). Used by T1.3 failover wiring. Keep small
    # and explicit; operators override per-deploy via env.
    _TTS_DEFAULT_SECONDARY = {
        "cartesia": ("elevenlabs", "ELEVENLABS_API_KEY"),
        "elevenlabs": ("cartesia", "CARTESIA_API_KEY"),
        "deepgram": ("cartesia", "CARTESIA_API_KEY"),
    }

    async def _create_llm_provider(self, config: VoiceSessionConfig):
        """Initialise and return the LLM provider via LLMFactory.

        Provider selection is driven entirely by `config.llm_provider_type`
        (sourced from the global AIProviderConfig the user picks in the Ask AI
        options UI). To add a new provider, register it in
        `app/infrastructure/llm/factory.py` and add its env-var mapping to
        `_LLM_API_KEY_ENV` above.

        T1.1 — when `config.tenant_id` is set, the per-tenant
        encrypted credential in `tenant_ai_credentials` wins over the
        env var. When no row exists, falls back to env so single-
        tenant deploys keep working.
        """
        from app.infrastructure.llm.factory import LLMFactory
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )

        provider_type = config.llm_provider_type or "groq"
        api_key_env = self._LLM_API_KEY_ENV.get(provider_type)
        api_key = await get_credential_resolver().resolve(
            provider_type,
            tenant_id=config.tenant_id,
            env_var=api_key_env,
        )

        provider = LLMFactory.create(provider_type, config={})
        init_config: dict = {
            "api_key": api_key,
            "model": config.llm_model,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
        }
        # Pass through Gemini-specific knobs. Non-Gemini providers ignore them.
        if config.llm_thinking_budget is not None:
            init_config["thinking_budget"] = config.llm_thinking_budget
        await provider.initialize(init_config)

        # T1.3 follow-on — first-token deadline + secondary-provider failover.
        # Opt-in; with the flag off (or no distinct secondary) the bare primary
        # is returned, so behaviour is byte-for-byte unchanged.
        if not _failover_enabled("LLM_FAILOVER_ENABLED"):
            return provider

        secondary = await self._build_secondary_llm_provider(
            config, primary_provider_type=provider_type,
        )
        if secondary is None:
            return provider

        from app.domain.services.resilient_llm import (
            LLMFailoverPolicy,
            ResilientLLMProvider,
        )
        deadline_ms = float(os.getenv("LLM_FIRST_TOKEN_DEADLINE_MS", "2500"))
        wrapper = ResilientLLMProvider(
            primary=provider,
            secondary=secondary,
            policy=LLMFailoverPolicy(
                first_token_deadline_seconds=max(0.3, deadline_ms / 1000.0),
            ),
        )
        logger.info(
            "llm_resilient_wrapper_active primary=%s/%s secondary=%s deadline_ms=%.0f",
            provider_type, config.llm_model, secondary.name, deadline_ms,
        )
        return wrapper

    async def _build_secondary_llm_provider(
        self, config: VoiceSessionConfig, *, primary_provider_type: str
    ):
        """Build + initialise the secondary LLM for ``LLM_FAILOVER_ENABLED``.

        Returns ``None`` (failover silently disabled) when no distinct secondary
        is configured or it can't initialise — never breaks the primary path.
        Secondary selection: ``LLM_SECONDARY_PROVIDER`` (default = primary's
        provider) + ``LLM_SECONDARY_MODEL`` (default per ``_LLM_DEFAULT_SECONDARY_MODEL``).
        """
        sec_provider = (
            os.getenv("LLM_SECONDARY_PROVIDER") or primary_provider_type
        ).strip()
        sec_model = (
            os.getenv("LLM_SECONDARY_MODEL")
            or self._LLM_DEFAULT_SECONDARY_MODEL.get(sec_provider)
        )
        if not sec_model:
            logger.warning(
                "llm_secondary_no_model provider=%s — set LLM_SECONDARY_MODEL "
                "to enable failover", sec_provider,
            )
            return None
        if sec_provider == primary_provider_type and sec_model == config.llm_model:
            logger.info(
                "llm_secondary_same_as_primary model=%s — skipping (no benefit)",
                sec_model,
            )
            return None

        try:
            from app.infrastructure.llm.factory import LLMFactory
            from app.domain.services.credential_resolver import (
                get_credential_resolver,
            )

            api_key = await get_credential_resolver().resolve(
                sec_provider,
                tenant_id=config.tenant_id,
                env_var=self._LLM_API_KEY_ENV.get(sec_provider),
            )
            secondary = LLMFactory.create(sec_provider, config={})
            sec_init: dict = {
                "api_key": api_key,
                "model": sec_model,
                "temperature": config.llm_temperature,
                "max_tokens": config.llm_max_tokens,
            }
            if config.llm_thinking_budget is not None:
                sec_init["thinking_budget"] = config.llm_thinking_budget
            await secondary.initialize(sec_init)
            return secondary
        except Exception as exc:
            logger.warning(
                "llm_secondary_init_failed provider=%s model=%s err=%s — "
                "failover disabled for this session",
                sec_provider, sec_model, exc,
            )
            return None

    async def _create_tts_provider(self, config: VoiceSessionConfig):
        """Initialise and return the TTS provider.

        T1.1 — provider API keys come from CredentialResolver, which
        prefers a per-tenant encrypted row when `config.tenant_id` is
        set and falls back to the process env var otherwise.
        """
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )
        resolver = get_credential_resolver()

        if config.tts_provider_type == "cartesia":
            from app.infrastructure.tts.cartesia import CartesiaTTSProvider

            api_key = await resolver.resolve("cartesia", tenant_id=config.tenant_id)
            provider = CartesiaTTSProvider()
            await provider.initialize(
                {
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "model_id": config.tts_model or "sonic-3",
                    "sample_rate": config.tts_sample_rate,
                }
            )
        elif config.tts_provider_type == "deepgram":
            from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider

            api_key = await resolver.resolve("deepgram", tenant_id=config.tenant_id)
            provider = DeepgramTTSProvider()
            await provider.initialize(
                {
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "sample_rate": config.tts_sample_rate,
                }
            )
        elif config.tts_provider_type == "elevenlabs":
            from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider

            api_key = await resolver.resolve("elevenlabs", tenant_id=config.tenant_id)
            provider = ElevenLabsTTSProvider()
            await provider.initialize(
                {
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "model_id": config.tts_model or "eleven_flash_v2_5",
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

        # T1.3 — optional resilient wrapper. Same pattern as STT:
        # primary stays the picked provider; secondary is the
        # configured cross-vendor fallback. Failure to init secondary
        # leaves us with the primary alone (still degrades better than
        # nothing).
        if _failover_enabled("TTS_FAILOVER_ENABLED"):
            secondary = await self._create_tts_secondary(config)
            if secondary is not None:
                from app.domain.services.resilient_tts import (
                    ResilientTTSProvider,
                    TTSFailoverPolicy,
                )
                voice_map_raw = os.getenv("TTS_SECONDARY_VOICE_MAP", "")
                voice_id_map = _parse_voice_map(voice_map_raw)
                wrapper = ResilientTTSProvider(
                    primary=provider,
                    secondary=secondary,
                    policy=TTSFailoverPolicy(voice_id_map=voice_id_map or None),
                )
                logger.info(
                    "tts_resilient_wrapper_active primary=%s secondary=%s",
                    provider.name, secondary.name,
                )
                return wrapper

        return provider

    async def _create_tts_secondary(
        self, config: VoiceSessionConfig,
    ):
        """Build the secondary TTS for failover. Returns None when no
        secondary is configured or its init fails — safe to ignore."""
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )

        primary_name = config.tts_provider_type
        secondary_provider_name = (
            os.getenv("TTS_SECONDARY_PROVIDER")
            or self._TTS_DEFAULT_SECONDARY.get(primary_name, (None, None))[0]
        )
        if not secondary_provider_name or secondary_provider_name == primary_name:
            return None

        resolver = get_credential_resolver()
        api_key = await resolver.resolve(
            secondary_provider_name, tenant_id=config.tenant_id,
        )

        try:
            if secondary_provider_name == "cartesia":
                from app.infrastructure.tts.cartesia import CartesiaTTSProvider
                provider = CartesiaTTSProvider()
                await provider.initialize({
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "model_id": "sonic-3",
                    "sample_rate": config.tts_sample_rate,
                })
            elif secondary_provider_name == "elevenlabs":
                from app.infrastructure.tts.elevenlabs_tts import (
                    ElevenLabsTTSProvider,
                )
                provider = ElevenLabsTTSProvider()
                await provider.initialize({
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "model_id": "eleven_flash_v2_5",
                    "sample_rate": config.tts_sample_rate,
                })
            elif secondary_provider_name == "deepgram":
                from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
                provider = DeepgramTTSProvider()
                await provider.initialize({
                    "api_key": api_key,
                    "voice_id": config.voice_id,
                    "sample_rate": config.tts_sample_rate,
                })
            else:
                logger.warning(
                    "tts_secondary_unknown provider=%s", secondary_provider_name,
                )
                return None
        except Exception as exc:
            logger.warning(
                "tts_secondary_init_failed provider=%s err=%s",
                secondary_provider_name, exc,
            )
            return None
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
            # Cartesia and Google output float32 PCM.
            # Deepgram and ElevenLabs output linear16 PCM.
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
