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

from app.realtime.session_config import RealtimeSessionConfig
from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.models.agent_config import AgentConfig
from app.domain.models.ai_config import GroqModel
from app.domain.models.voice_contract import generate_talklee_call_id
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.domain.repositories.call_event_repository import CallEventRepository

logger = logging.getLogger(__name__)

# 2026-07-08: post-greeting-TTS STT unmute tail for telephony (non-browser)
# calls. SOTA guidance (Coval, Gladia, Deepgram, Retell) recommends 200-500ms
# here to absorb network jitter + echo-tail decay on PSTN before STT goes
# live again; the previous 0.1s was clipping fast-caller replies. Env
# overridable for tuning without a code change.
_STT_UNMUTE_TAIL_S = float(os.getenv("STT_UNMUTE_TAIL_S", "0.25"))


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
        """DEPRECATED — do not call from campaign, dialer or browser-test paths.

        Pre-T3.10 the codebase carried ``first_speaker = "user"`` to mean
        "treat this as inbound framing", and this mapping was a bridge "until
        per-campaign direction lands in the UI". It never did, and every
        later consumer of ``direction`` read INBOUND as *carrier inbound*:
        realtime instructions told the agent the caller had rung in, AMD and
        voicemail detection switched off, and the 2026-08-30 recording gate
        discarded the recording. Who speaks first is
        :func:`opening_mode_from_first_speaker`; who originated the call is a
        fact the caller already knows and must pass explicitly.
        """
        value = (first_speaker or "").strip().lower()
        return cls.INBOUND if value == "user" else cls.OUTBOUND


OPENING_MODE_AGENT_FIRST = "agent_first"
OPENING_MODE_CALLEE_FIRST = "callee_first"

def opening_mode_from_first_speaker(first_speaker: Optional[str]) -> str:
    """Map the per-call ``first_speaker`` knob onto an opening mode.

    ``"user"`` → the agent waits for the other party to speak (callee-first).
    Anything else, including None, → the agent opens. This is a turn-taking
    choice and says nothing about who originated the call.
    """
    value = (first_speaker or "").strip().lower()
    return OPENING_MODE_CALLEE_FIRST if value == "user" else OPENING_MODE_AGENT_FIRST


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
class VoiceSessionConfig(RealtimeSessionConfig):
    """All parameters needed to spin up a voice session."""

    # Provider selection
    stt_provider_type: str = "deepgram_flux"
    llm_provider_type: str = "groq"
    tts_provider_type: str = "google"  # "google" | "deepgram" | "elevenlabs"

    # STT settings
    stt_model: str = "flux-general-en"
    # Saved in AI Options (AIProviderConfig.stt_language). Flux is English-only
    # ("flux-general-en"), so a non-English language forces the Nova-3 primary
    # and is passed to Deepgram on every stream (2026-09-06 audit, F09 — the
    # setting was accepted, stored and then never sent anywhere).
    stt_language: str = "en"
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
    llm_model: str = GroqModel.GPT_OSS_20B.value
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
    # Validated tenant-authored guidance only, without cascaded voice tags or
    # an outbound persona body. Shared with the realtime instruction builder.
    campaign_guidance: str = ""
    # ── Prompt identity (goals.md §6) ────────────────────────────────────
    # Which instructions this call actually ran on. `prompt_version` is the
    # name a human rolls back to; `prompt_hash` is derived from the composed
    # text and cannot go stale when someone edits a persona and forgets to
    # bump the version. Both travel with the session so the per-call log and
    # the calls row can carry them — a QA batch has to be able to prove all
    # 30 calls used one prompt, and that it differed from the batch before.
    #
    # The hash covers the STABLE prompt only: it is taken before the callee's
    # name is prepended, because per-lead context changes every call and would
    # make the hash unique per call — varying for the wrong reason.
    prompt_template: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    # Call direction — set by the bridge when the per-call first_speaker
    # is known, used by build_telephony_session_config to pick the right
    # base prompt (inbound vs outbound) up front. Defaults to OUTBOUND so
    # every legacy call path keeps its current behaviour.
    direction: Direction = Direction.OUTBOUND
    # Who talks first — "agent_first" or "callee_first" — independent of
    # direction: an outbound dialer call can be callee-first (a courtesy pause
    # for "hello?") and a true inbound call can be agent-first. Drives the
    # callee-first prompt directive and realtime greet_on_start. None means the
    # caller did not say, and the direction-derived legacy default applies.
    opening_mode: Optional[str] = None
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
    # Explicit region used only to interpret a caller-stated national phone
    # number. None is fail-closed: the agent asks for a country code rather than
    # silently assuming US.
    contact_phone_region: Optional[str] = None
    # Only enable this when the session is backed by a real row in `calls`.
    # Browser demos generate ephemeral call_ids that do not satisfy the FK.
    event_logging_enabled: bool = False

    # ── Pipeline mode (Realtime add-on) ──────────────────────────────────
    # "cascaded" (default) = the classic STT→LLM→TTS pipeline, unchanged.
    # "realtime" = a single OpenAI gpt-realtime-2 speech-to-speech session
    # (see app/realtime/openai.py). Mirrors the fields
    # on AIProviderConfig; the telephony session-config builder threads them
    # through. Default "cascaded" keeps every existing call byte-for-byte.
    # ── Callee identity ("who you're calling") ───────────────────────────
    # Set by build_telephony_session_config from the lead threaded through
    # make_call. The cascaded path bakes this into system_prompt directly;
    # these fields carry the same identity to the realtime pipeline, which
    # builds its own instruction string from the config. All optional →
    # None means a blind dial (unchanged behaviour).
    callee_first_name: Optional[str] = None
    callee_last_name: Optional[str] = None
    callee_company: Optional[str] = None


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

    # Realtime pipeline mode (pipeline_mode == "realtime"): the connected
    # speech-to-speech session and the bridge that pumps audio between it and
    # the media gateway. Both None for cascaded sessions. When realtime_bridge
    # is set, the telephony lifecycle runs bridge.run() as the pipeline_task
    # INSTEAD of pipeline.start_pipeline() (see telephony/lifecycle.py).
    realtime_session: Any = None
    realtime_bridge: Any = None

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

    async def _persist_prompt_identity(
        self, call_row_id: Any, config: VoiceSessionConfig, tenant_id: Any = None
    ) -> None:
        """Write the prompt identity onto the durable ``calls`` row
        (goals.md §6, task #90).

        WHICH KEY, AND WHY NOT talklee_call_id (2026-08-28)
        ---------------------------------------------------
        This used to run at session-creation time and match on
        ``talklee_call_id``. It could never match a row. ``create_voice_session``
        MINTS a fresh ``generate_talklee_call_id()`` for the session; the dialer
        minted its own, unrelated one when it inserted the ``calls`` row
        (``dialer_worker._create_call_record``), and nothing carries the
        dialer's id onto ``VoiceSessionConfig``. Two independent random ids →
        ``UPDATE 0`` on every single call, which is exactly why
        ``calls.prompt_version`` was NULL everywhere.

        The join the telephony bridge really does establish is
        ``voice_session._dialer_call_id`` = ``calls.id`` — set by
        ``call_transcript_persister.bind_telephony_call`` for outbound and from
        the pinned admission snapshot for true inbound. That is the key used
        here, and it is the same key the transcript, outcome and duration
        writes use at hangup.

        Best-effort by design. A failed bookkeeping write must never fail a
        call — losing a conversation because an UPDATE hit a busy row would be
        the wrong trade every time. The identity is still in the
        ``call_prompt_identity`` log line, so a failure costs queryability, not
        the fact.
        """
        pool = getattr(self._db_client, "pool", None)
        if pool is None or not call_row_id:
            return
        # An empty identity is not an identity: ask-AI and the browser
        # assistant build their config elsewhere and have no campaign prompt.
        # Writing '' over the row would turn "no evidence" into a value.
        if not getattr(config, "prompt_hash", ""):
            return
        try:
            from app.core.db_utils import acquire_with_tenant

            # Tenant context so this keeps working once the app role stops
            # bypassing row security (task #80). None routes through the
            # documented bypass path for sessions with no tenant.
            tenant = tenant_id or getattr(config, "tenant_id", None)
            async with acquire_with_tenant(pool, str(tenant) if tenant else None) as conn:
                updated = await conn.execute(
                    """UPDATE calls
                          SET prompt_template = $2,
                              prompt_version  = $3,
                              prompt_hash     = $4
                        WHERE id = $1::uuid""",
                    str(call_row_id),
                    config.prompt_template,
                    config.prompt_version,
                    config.prompt_hash,
                )
            # "UPDATE 0" means the join key was wrong — which is invisible
            # otherwise, and would leave every review unattributable. Now that
            # the key is a bound calls.id, this signal actually varies: it is
            # silent on the pre-warm/orphan sessions that never bound a row
            # (they return above) and loud only when a bound id misses.
            if isinstance(updated, str) and updated.endswith(" 0"):
                logger.warning(
                    "prompt_identity_persist_matched_no_row call=%s — no calls "
                    "row with that id",
                    str(call_row_id)[:12],
                )
        except Exception:  # noqa: BLE001 — see docstring
            logger.warning(
                "prompt_identity_persist_failed call=%s",
                str(call_row_id)[:12], exc_info=True,
            )

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

        # The selected Realtime engine must never silently become cascaded.
        if config.pipeline_mode == "realtime":
            rt_session = await self._create_realtime_voice_session(config, call_id, talklee_call_id)
            if rt_session is None:
                raise RuntimeError("Realtime could not start. Check OpenAI configuration and connection; no alternative engine was used.")
            self._active_sessions[call_id] = rt_session
            return rt_session

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
            stt_language=config.stt_language or "en",
            voice_id=config.voice_id,
            contact_phone_region=config.contact_phone_region,
            started_at=datetime.utcnow(),
            last_activity_at=datetime.utcnow(),
        )
        call_session.talklee_call_id = talklee_call_id
        call_session.barge_in_event = asyncio.Event()

        # WHICH INSTRUCTIONS DID THIS CALL RUN ON? (goals.md §6)
        # The identity is computed in build_telephony_session_config, which
        # runs before a call exists — so `telephony_prompt_identity` can only
        # name the campaign, and a QA batch would have to correlate by
        # timestamp. This is the first point where the call id and the config
        # are both in scope, so log them together and make the join trivial.
        #
        # Guarded on prompt_hash because non-telephony sessions (ask-AI, the
        # browser assistant) build their config elsewhere and legitimately have
        # no campaign prompt; an empty line there would be noise, not evidence.
        if getattr(config, "prompt_hash", ""):
            logger.info(
                "call_prompt_identity call=%s campaign=%s template=%s "
                "version=%s hash=%s",
                str(call_id)[:12], config.campaign_id, config.prompt_template,
                config.prompt_version, config.prompt_hash,
            )
            # AND PERSIST IT (task #90). Logs rotate, and neither the QA release
            # gate ("every call has a prompt version") nor a conversation review
            # can JOIN on a log line. The row is the only durable home.
            #
            # NOT HERE, THOUGH (2026-08-28). There is no calls row this session
            # can be matched to yet: neither this session's call_id nor its
            # freshly-minted talklee_call_id exists in `calls`, and the dialer's
            # row is keyed by ids this code has never seen. The durable
            # calls.id only arrives later, as `_dialer_call_id`, from
            # bind_telephony_call (outbound) or the admission snapshot
            # (inbound) — so the write happens in end_session, alongside the
            # transcript/outcome/duration writes that use the same key.

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
        # Back-reference for pipeline-layer collaborators (e.g. the caller-first
        # instant opener needs the VoiceSession's pre-synth greeting from a
        # context that only holds the CallSession).
        try:
            call_session._voice_session_ref = voice_session
        except Exception:
            pass
        try:
            self._active_sessions[call_id] = voice_session
            logger.info(f"Voice session created: {call_id[:8]}")
            return voice_session
        except Exception:
            # Ensure we never leave a half-registered zombie session
            self._active_sessions.pop(call_id, None)
            raise

    # ------------------------------------------------------------------
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
        if not session.pipeline and not session.realtime_bridge:
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
                if session.realtime_bridge is not None:
                    await session.realtime_bridge.run()
                else:
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
                # 2026-07-08: telephony (non-browser) tail widened 0.1s -> 0.25s.
                # SOTA guidance (Coval, Gladia, Deepgram, Retell) recommends a
                # 200-500ms post-TTS unmute tail on PSTN to cover network jitter
                # + echo-tail decay before re-enabling STT; 0.1s was clipping
                # fast-caller replies right after the agent finished speaking.
                # Browser sessions keep 0.05s — they have real client-side AEC.
                await asyncio.sleep(
                    0.05 if waited_for_browser_playback else _STT_UNMUTE_TAIL_S
                )
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
            except Exception:
                # A pipeline may fail in its own finally while being cancelled.
                # Gateway/provider cleanup and active-session removal still apply.
                logger.warning("voice_pipeline_cancel_failed call=%s", call_id[:8], exc_info=True)

        # Realtime teardown (idempotent): cancelling pipeline_task already runs
        # bridge.run()'s finally→stop(), which closes the speech-to-speech
        # socket. Call stop() again explicitly so a session torn down WITHOUT a
        # running pipeline_task (e.g. connect succeeded but the call failed
        # before start) still releases the socket. Never raises.
        bridge = getattr(session, "realtime_bridge", None)
        if bridge is not None:
            try:
                await bridge.stop()
            except Exception:
                pass
        rt = getattr(session, "realtime_session", None)
        if rt is not None:
            try:
                await rt.close()
            except Exception:
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

        # WHICH INSTRUCTIONS DID THIS CALL RUN ON? (goals.md §6, task #90)
        # Written here rather than at session creation because this is the
        # first point where the durable `calls.id` is on the session: the
        # bridge binds it after answer (bind_telephony_call for outbound, the
        # pinned admission snapshot for inbound). Sessions that never bound a
        # row — pre-warm, ringing, orphans — carry no id and are skipped.
        # No-ops for sessions with no campaign prompt. Never raises.
        if session.config is not None:
            await self._persist_prompt_identity(
                getattr(session, "_dialer_call_id", None),
                session.config,
                getattr(session, "_dialer_tenant_id", None),
            )

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
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )

        api_key = await get_credential_resolver().resolve(
            "deepgram", tenant_id=config.tenant_id,
        )

        def _build_flux(model: str):
            from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
            init = {
                "api_key": api_key,
                "model": model,
                "sample_rate": config.stt_sample_rate,
                "encoding": config.stt_encoding,
                "eot_threshold": config.stt_eot_threshold,
                "eager_eot_threshold": config.stt_eager_eot_threshold,
                "eot_timeout_ms": config.stt_eot_timeout_ms,
                # Per-session keyterms win; else the providers.yaml list (env
                # DEEPGRAM_FLUX_KEYTERMS overrides inside initialize()).
                "keyterms": config.stt_keyterms or _default_flux_keyterms(),
                "capture_keyterms": _default_flux_capture_keyterms(),
                "mip_opt_out": True,  # keep caller PII out of training
                "tags": [
                    f"tenant:{config.tenant_id or 'none'}",
                    f"campaign:{config.campaign_id}",
                ],
            }
            return DeepgramFluxSTTProvider(), init

        def _build_nova(model: str):
            from app.infrastructure.stt.deepgram_nova import DeepgramNovaSTTProvider
            init = {
                "api_key": api_key,
                "model": model,
                "sample_rate": config.stt_sample_rate,
                "encoding": config.stt_encoding,
            }
            return DeepgramNovaSTTProvider(), init

        # PRIMARY = the engine the tenant picked in AI Options (default Flux).
        engine = (config.stt_provider_type or "deepgram_flux").lower()
        is_nova_primary = engine in ("deepgram_nova", "deepgram-nova", "nova", "nova-3")
        # Flux only ships an English model; a non-English tenant language can
        # only be honoured by Nova-3. Switch here as well as in the session
        # builder so non-telephony callers get the same rule.
        _lang = (getattr(config, "stt_language", None) or "en").strip().lower()
        if not is_nova_primary and _lang not in ("en", "en-us", "en-gb", "en-au", "en-in", "en-nz"):
            logger.info(
                "stt_language_forces_nova language=%s (Flux is English-only)", _lang,
            )
            is_nova_primary = True
        if is_nova_primary:
            primary, primary_init = _build_nova(config.stt_model or "nova-3")
        else:
            primary, primary_init = _build_flux(config.stt_model or "flux-general-en")
        await primary.initialize(primary_init)

        failover_on = _failover_enabled("STT_FAILOVER_ENABLED")

        # Controlled fault injection (2026-08-18). Off unless an operator names
        # THIS campaign in VOICE_STT_FAULT_SILENT_CAMPAIGN with a live expiry,
        # and refused outright when there is no secondary to promote. Applied
        # here, to the initialised primary, so the deaf provider still opens a
        # real socket and still receives real audio — the failure being
        # simulated is a provider that answers nothing, not one that never
        # connected. See stt_fault_injection for why it is shaped this way.
        from app.domain.services.stt_fault_injection import maybe_deafen_primary

        primary = maybe_deafen_primary(
            primary,
            campaign_id=str(config.campaign_id) if config.campaign_id else None,
            call_id=getattr(config, "call_id", None),
            failover_enabled=failover_on,
        )

        if not failover_on:
            return primary

        # SECONDARY = the OTHER engine (cross-engine resilience). A Flux-side
        # failure (e.g. the 2026-06-29 numerals=true HTTP 400) fails over to
        # Nova-3 and vice-versa, so one provider's outage never silences calls.
        # Same Deepgram auth. Override the secondary model via STT_SECONDARY_MODEL.
        sec_override = os.getenv("STT_SECONDARY_MODEL")
        if is_nova_primary:
            secondary, secondary_init = _build_flux(sec_override or "flux-general-en")
            sec_label = f"flux-{secondary_init['model']}"
        else:
            secondary, secondary_init = _build_nova(sec_override or "nova-3")
            sec_label = f"nova-{secondary_init['model']}"
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
            "stt_resilient_wrapper_active primary=%s-%s secondary=%s",
            "nova" if is_nova_primary else "flux", primary_init["model"], sec_label,
        )
        return wrapper

    # Map provider type → env var holding its API key. Keep this small and
    # explicit; if it grows past ~5 entries, move it to a config object.
    _LLM_API_KEY_ENV = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
    }

    # Default secondary model per provider for LLM_FAILOVER_ENABLED. Same-vendor
    # different-model by default (mirrors the STT secondary choice): a Groq stall
    # is usually model / rate-limit specific, and llama-3.1-8b-instant is the
    # fastest Groq model — a clean low-latency fallback. Operators can point the
    # secondary at another vendor (LLM_SECONDARY_PROVIDER=gemini) for true vendor
    # isolation, or override the model with LLM_SECONDARY_MODEL.
    # The two production models are each other's fallback. When the configured
    # secondary (env) turns out to be the same provider+model as the tenant's
    # primary — which is exactly what happens when a tenant picks Groq 20B while
    # LLM_SECONDARY_* names Groq 20B — the counterpart is used instead of
    # silently running with no failover at all (2026-09-06 audit, F06).
    _LLM_PAIR_FALLBACK = {
        ("groq", "openai/gpt-oss-20b"): ("cerebras", "gpt-oss-120b"),
        ("cerebras", "gpt-oss-120b"): ("groq", "openai/gpt-oss-20b"),
    }

    _LLM_DEFAULT_SECONDARY_MODEL = {
        # Was llama-3.1-8b-instant until 2026-08-17, by which point that model
        # 404'd on this account — so the default same-vendor fallback was a
        # model that could not answer, i.e. failover to nothing. Production is
        # unaffected because LLM_SECONDARY_PROVIDER=gemini overrides it, but a
        # deployment without that env var had a silently dead safety net.
        # gpt-oss-20b is the fastest id this account can actually serve.
        "groq": "openai/gpt-oss-20b",
        "cerebras": "gpt-oss-120b",
    }

    @classmethod
    def _pick_secondary_llm(
        cls,
        *,
        primary_provider: str,
        primary_model: Optional[str],
        env_provider: Optional[str],
        env_model: Optional[str],
    ) -> Optional[tuple[str, str]]:
        """Resolve the (provider, model) to fail over to, or None.

        Env wins when it names something different from the primary. When it
        names the primary itself (or is unset for a provider without a default
        model), fall back to the production pair's counterpart so selecting
        either target model keeps the other as its safety net.
        """
        primary_provider = (primary_provider or "").strip()
        primary_model = (primary_model or "").strip()
        sec_provider = (env_provider or primary_provider).strip()
        sec_model = (env_model or cls._LLM_DEFAULT_SECONDARY_MODEL.get(sec_provider) or "").strip()
        if sec_provider and sec_model and not (
            sec_provider == primary_provider and sec_model == primary_model
        ):
            return sec_provider, sec_model
        counterpart = cls._LLM_PAIR_FALLBACK.get((primary_provider, primary_model))
        if counterpart and counterpart != (primary_provider, primary_model):
            return counterpart
        return None

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
        from app.domain.models.ai_config import validate_traditional_llm_selection
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )

        provider_type = config.llm_provider_type
        # Invalid saved profiles must not turn into first-token failures that
        # silently invoke an unrelated secondary model.
        validate_traditional_llm_selection(provider_type, config.llm_model)
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
        # 2500 -> 1500 on 2026-09-23, from 194 production turns over 14 days
        # (primary Cerebras gpt-oss-120b, secondary Groq gpt-oss-20b):
        #
        #   primary first token   <=1.0s 180 | 1.0-1.5s 4 | 1.5-2.0s 0
        #                         2.0-2.5s 2 | >2.5s (failed over) 8
        #   secondary first token when it served: 0.8-1.2s
        #
        # Every failover turn paid the full deadline in silence before the
        # secondary even started - the 3.3-3.8s turns that were most of the
        # slow tail. At 1.5s those eight turns get ~1s faster, the two
        # 2.0-2.5s turns come out about even, and none gets slower because
        # nothing landed between 1.5s and 2.0s. Going lower would start
        # failing over the four 1.0-1.5s turns and make them slower. Re-check
        # the distribution if the primary model or provider changes.
        deadline_ms = float(os.getenv("LLM_FIRST_TOKEN_DEADLINE_MS", "1500"))

        # Controlled fault injection, off unless a campaign is named AND the
        # window is open. Applied to the PRIMARY here, inside the wrapper, so
        # the failover path under test is the real one — no special-casing
        # anywhere in resilient_llm. See llm_fault_injection for the four
        # safety properties; it declines when there is no secondary, which
        # cannot happen on this line because we returned above if so.
        from app.domain.services.llm_fault_injection import maybe_break_primary

        primary_for_wrapper = maybe_break_primary(
            provider,
            getattr(config, "campaign_id", None),
            getattr(config, "call_id", None) or getattr(config, "campaign_id", None),
            failover_enabled=True,
        )

        wrapper = ResilientLLMProvider(
            primary=primary_for_wrapper,
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
        picked = self._pick_secondary_llm(
            primary_provider=primary_provider_type,
            primary_model=config.llm_model,
            env_provider=os.getenv("LLM_SECONDARY_PROVIDER"),
            env_model=os.getenv("LLM_SECONDARY_MODEL"),
        )
        if picked is None:
            logger.warning(
                "llm_secondary_unavailable primary=%s/%s — env secondary is the "
                "primary itself and no counterpart is known; failover disabled",
                primary_provider_type, config.llm_model,
            )
            return None
        sec_provider, sec_model = picked
        logger.info(
            "llm_secondary_selected primary=%s/%s secondary=%s/%s",
            primary_provider_type, config.llm_model, sec_provider, sec_model,
        )

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

            from app.domain.services.voice_eligibility import require_elevenlabs_voice_eligible
            await require_elevenlabs_voice_eligible(getattr(self._db_client, "pool", None), config.tenant_id, config.voice_id)
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

        secondary_voice = _parse_voice_map(
            os.getenv("TTS_SECONDARY_VOICE_MAP", "")
        ).get(config.voice_id)
        if not secondary_voice:
            logger.warning(
                "tts_secondary_unavailable reason=missing_voice_mapping primary=%s secondary=%s",
                primary_name, secondary_provider_name,
            )
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
                    "voice_id": secondary_voice,
                    "model_id": "sonic-3",
                    "sample_rate": config.tts_sample_rate,
                })
            elif secondary_provider_name == "elevenlabs":
                from app.infrastructure.tts.elevenlabs_tts import (
                    ElevenLabsTTSProvider,
                )
                from app.domain.services.voice_eligibility import require_elevenlabs_voice_eligible
                await require_elevenlabs_voice_eligible(getattr(self._db_client, "pool", None), config.tenant_id, secondary_voice)
                provider = ElevenLabsTTSProvider()
                await provider.initialize({
                    "api_key": api_key,
                    "voice_id": secondary_voice,
                    "model_id": "eleven_flash_v2_5",
                    "sample_rate": config.tts_sample_rate,
                })
            elif secondary_provider_name == "deepgram":
                from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider
                provider = DeepgramTTSProvider()
                await provider.initialize({
                    "api_key": api_key,
                    "voice_id": secondary_voice,
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

    # ------------------------------------------------------------------
    # Realtime (speech-to-speech) session assembly
    # ------------------------------------------------------------------

    async def _create_realtime_voice_session(self, config, call_id, talklee_call_id):
        from app.realtime.runtime import create_realtime_voice_session
        return await create_realtime_voice_session(self, config, call_id, talklee_call_id)

    @staticmethod
    def _resolve_realtime_greet_on_start(config):
        from app.realtime.runtime import resolve_realtime_greet_on_start
        return resolve_realtime_greet_on_start(config)

    @staticmethod
    def _build_realtime_persona(config):
        from app.realtime.runtime import build_realtime_persona
        return build_realtime_persona(config)

    async def _create_media_gateway(self, config: VoiceSessionConfig):
        """Initialise and return a media gateway via the factory."""
        from app.infrastructure.telephony.factory import MediaGatewayFactory

        gateway = MediaGatewayFactory.create(config.gateway_type)

        # Realtime mode: force an 8 kHz internal rate and s16le TTS-source so
        # the μ-law wire needs NO internal resampling (the realtime bridge feeds
        # PCM16 @ 8k and reads PCM16 @ 8k). Cascaded sessions are untouched.
        _is_realtime = getattr(config, "pipeline_mode", "cascaded") == "realtime"

        init_config = {
            "sample_rate": 8000 if _is_realtime else config.gateway_sample_rate,
            # Realtime runs the gateway at ONE 8 kHz rate in BOTH directions.
            # The RealtimeBridge only knows a single internal rate (it reads
            # gateway._sample_rate) and resamples that <-> the μ-law 8 kHz wire.
            # If the gateway's INPUT rate is left at the cascaded STT rate
            # (16 kHz) while output is forced to 8 kHz, the caller-audio the
            # bridge pulls off get_audio_queue() is 16 kHz but the bridge treats
            # it as 8 kHz — so the model receives the caller at half speed /
            # garbled ("my voice is not flowing into the model"). Forcing input
            # to 8 kHz too keeps the gateway self-consistent with the bridge's
            # single-rate model. No-op on the telephony gateway (which ignores
            # input_sample_rate and always decodes the 8 kHz μ-law wire);
            # correct for Twilio (8 kHz μ-law wire) and browser (client must
            # send 8 kHz). Cascaded keeps its STT-native input rate.
            "input_sample_rate": (
                8000 if _is_realtime
                else (config.gateway_input_sample_rate or config.stt_sample_rate)
            ),
            "channels": config.gateway_channels,
            "bit_depth": config.gateway_bit_depth,
            "target_buffer_ms": config.gateway_target_buffer_ms,
            # Cartesia and Google output float32 PCM.
            # Deepgram and ElevenLabs output linear16 PCM.
            # Realtime bridge always feeds linear16 (s16le).
            "tts_source_format": "s16le"
            if (_is_realtime or config.tts_provider_type in {"deepgram", "elevenlabs"})
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
