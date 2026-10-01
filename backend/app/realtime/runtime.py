"""Realtime session assembly and persona mapping. Shared orchestrator is an entry point only."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from app.domain.models.session import CallSession, CallState
from app.domain.models.conversation_state import ConversationState, ConversationContext
from app.domain.services.voice_orchestrator import VoiceSession, VoiceSessionConfig, Direction, OPENING_MODE_AGENT_FIRST

from app.realtime.prompt_config import build_realtime_persona, prepare_realtime_prompt

logger = logging.getLogger(__name__)

async def create_realtime_voice_session(
    self,
    config: VoiceSessionConfig,
    call_id: str,
    talklee_call_id: str,
):
    """Build a VoiceSession backed by an OpenAI gpt-realtime-2 bridge.

    Returns the assembled VoiceSession on success, or None on any failure
    (the caller reports an explicit setup failure). NEVER raises —
    a realtime setup error must not take down call origination.

    Kept deliberately separate from the cascaded provider machinery:
    - resolves OPENAI_API_KEY via CredentialResolver ("openai"),
    - builds instructions with build_realtime_instructions (NOT
      compose_prompt / compliance_floor / prompt_builder),
    - creates + connects OpenAIRealtimeSession,
    - wires a RealtimeBridge to the SAME media-gateway transport TTS uses.
    """
    rt = None
    gateway = None
    assembled = False
    try:
        from app.domain.services.credential_resolver import (
            get_credential_resolver,
        )
        from app.realtime.openai import (
            OpenAIRealtimeSession,
            knowledge_lookup_tool,
        )
        from app.realtime.bridge import (
            RealtimeBridge,
        )
        from app.realtime.tools import (
            realtime_voice_action_tools,
        )

        # Provider selection — STRICTLY opt-in, per-tenant/campaign. Rides
        # the EXISTING realtime_settings JSONB dict (no new DB column):
        # realtime_settings["provider"] = "openai" (default, unchanged) |
        # "xai". Every existing tenant/campaign that has never set this
        # key gets byte-for-byte the same OpenAI path as before.
        from app.realtime.config import normalize_realtime_settings
        rt_settings = config.realtime_settings = normalize_realtime_settings(config.realtime_settings)
        provider = str(rt_settings.get("provider") or "openai").strip().lower()

        if provider == "xai":
            api_key = await get_credential_resolver().resolve(
                "xai", tenant_id=config.tenant_id,
            )
            if not api_key:
                logger.error(
                    "realtime session call_id=%s: no XAI_API_KEY resolved "
                    "(tenant=%s) — cannot start xai realtime", call_id[:8],
                    config.tenant_id,
                )
                return None
        else:
            api_key = await get_credential_resolver().resolve(
                "openai", tenant_id=config.tenant_id,
            )
            if not api_key:
                logger.error(
                    "realtime session call_id=%s: no OPENAI_API_KEY resolved "
                    "(tenant=%s) — cannot start realtime", call_id[:8],
                    config.tenant_id,
                )
                return None

        # Persona/company/goal → clean realtime instructions. Pull from the
        # campaign agent_config when present; fall back to sane defaults.
        instructions = prepare_realtime_prompt(config)
        from types import SimpleNamespace
        from app.domain.services.voice_pipeline.action_execution import prepare_voice_action_context
        action_context = SimpleNamespace(tenant_id=config.tenant_id, campaign_id=config.campaign_id,
                                         call_id=call_id, lead_id=config.lead_id)
        await prepare_voice_action_context(action_context)

        # Media gateway at 8 kHz internal so the μ-law wire needs NO
        # resampling — only the codec conversion in the bridge.
        gateway = await self._create_media_gateway(config)
        internal_rate = getattr(gateway, "_sample_rate", config.gateway_sample_rate)

        if provider == "xai":
            from app.realtime.xai import (
                XAIRealtimeSession,
                XAI_DEFAULT_MODEL,
            )
            # config.realtime_model defaults to the OpenAI model name
            # ("gpt-realtime-2") for every tenant that hasn't touched
            # this field, so that value is NOT a meaningful xAI override.
            # Precedence: explicit realtime_settings["model"] > a
            # realtime_model the operator actually customised > the xAI
            # default.
            xai_model = (
                rt_settings.get("model")
                or (config.realtime_model
                    if config.realtime_model and config.realtime_model != "gpt-realtime-2"
                    else XAI_DEFAULT_MODEL)
            )
            rt = XAIRealtimeSession(
                api_key=api_key,
                model=xai_model,
                agent_id=rt_settings.get("agent_id"),
                instructions=instructions,
                tools=[knowledge_lookup_tool(), *realtime_voice_action_tools(action_context)],
                settings=config.realtime_settings,
                call_id=call_id,
            )
        else:
            rt = OpenAIRealtimeSession(
                api_key=api_key,
                model=config.realtime_model or "gpt-realtime-2",
                voice=config.realtime_voice or "marin",
                instructions=instructions,
                tools=[knowledge_lookup_tool(), *realtime_voice_action_tools(action_context)],
                settings=config.realtime_settings,
                call_id=call_id,
            )
        connected = await rt.connect()
        if not connected:
            logger.warning(
                "realtime session call_id=%s: connect() failed", call_id[:8],
            )
            try:
                await rt.close()
            except Exception:
                pass
            return None

        # Knowledge context (reuse the cascaded retrieval). Best-effort — a
        # missing pool just means the knowledge tool returns "no info".
        knowledge_pool = None
        try:
            from app.core.container import get_container
            c = get_container()
            if c.is_initialized:
                # Use the SAME accessor the cascaded per-turn retrieval uses
                # (turn_streamer._knowledge_block_for_turn:
                #   getattr(container.db_client, "pool", None)). Both resolve
                # to the one asyncpg pool, but aligning the accessor keeps the
                # two knowledge paths provably identical and avoids the
                # db_pool @property raising if the pool is torn down mid-setup.
                knowledge_pool = getattr(c.db_client, "pool", None)
        except Exception:
            knowledge_pool = None

        call_session = CallSession(
            call_id=call_id,
            tenant_id=config.tenant_id,
            campaign_id=config.campaign_id,
            lead_id=config.lead_id,
            provider_call_id=f"{config.session_type}-realtime",
            state=CallState.ACTIVE,
            conversation_state=ConversationState.GREETING,
            conversation_context=ConversationContext(),
            agent_config=config.agent_config,
            persona_type=config.persona_type,
            # CallSession REQUIRES these (they're cascaded fields). Omitting
            # them raised "2 validation errors for CallSession" → the realtime
            # setup crashed and fell back to cascaded (at the wrong 8kHz rate),
            # which is what produced the "ghost" audio. Realtime doesn't USE the
            # cascaded system_prompt, but the model needs the field populated;
            # voice_id carries the realtime voice for telemetry/consistency.
            system_prompt=config.system_prompt,
            voice_id=(getattr(config, "realtime_voice", None) or config.voice_id),
            contact_phone_region=config.contact_phone_region,
            started_at=datetime.utcnow(),
            last_activity_at=datetime.utcnow(),
        )
        call_session.talklee_call_id = talklee_call_id
        call_session.barge_in_event = asyncio.Event()
        call_session._call_direction = config.direction.value
        call_session._voice_action_context = action_context._voice_action_context
        call_session._voice_action_capabilities = action_context._voice_action_capabilities
        call_session._voice_action_context_loaded = True

        # Transcript accumulation for the realtime path. The speech-to-speech
        # model emits no transcript on its own, so the bridge feeds the
        # model's final agent + caller transcripts into a TranscriptService
        # (class-level buffer keyed by call_id) — the SAME buffer the shared
        # hangup persister reads. We stash it on the voice_session so
        # lifecycle._on_call_ended finds it (pipeline is None on realtime).
        from app.domain.services.transcript_service import TranscriptService
        realtime_transcript_service = TranscriptService()

        # Wire barge-in into the media gateway's pacing loop, mirroring
        # the cascaded path (voice_pipeline_service.start_pipeline).
        # Without this, TelephonyMediaGateway.send_audio's pacing loop
        # never sees the event fire (it stays None), so it can't
        # early-exit and the agent keeps talking ~200-400ms over the
        # caller after a barge-in on realtime calls.
        set_barge_in = getattr(gateway, "set_barge_in_event", None)
        if set_barge_in:
            set_barge_in(call_id, call_session.barge_in_event)

        greet_on_start = resolve_realtime_greet_on_start(config)
        bridge = RealtimeBridge(
            call_id=call_id,
            realtime_session=rt,
            media_gateway=gateway,
            internal_sample_rate=internal_rate,
            knowledge_pool=knowledge_pool,
            tenant_id=config.tenant_id,
            campaign_id=config.campaign_id,
            lead_id=config.lead_id,
            contact_phone_region=config.contact_phone_region,
            contact_session=call_session,
            session_active=lambda: self._active_sessions.get(call_id) is not None,
            barge_in_event=call_session.barge_in_event,
            # True inbound can be caller-first OR agent-first. Admission
            # pins that distinction explicitly on the config; direction is
            # only the backwards-compatible default for older callers.
            greet_on_start=greet_on_start,
            transcript_service=realtime_transcript_service,
            talklee_call_id=talklee_call_id,
            call_direction=config.direction.value,
            action_session=call_session,
        )

        voice_session = VoiceSession(
            call_id=call_id,
            talklee_call_id=talklee_call_id,
            call_session=call_session,
            media_gateway=gateway,
            realtime_session=rt,
            realtime_bridge=bridge,
            config=config,
        )
        # Surface the transcript buffer for the hangup persister
        # (lifecycle._on_call_ended falls back to this when pipeline is None).
        voice_session.transcript_service = realtime_transcript_service
        assembled = True
        return voice_session
    except Exception as exc:  # noqa: BLE001 — explicit setup failure
        logger.error(
            "realtime session setup raised call_id=%s: %s — setup failed",
            call_id[:8], exc,
        )
        return None
    finally:
        if not assembled:
            if rt is not None:
                try:
                    await rt.close()
                except Exception:
                    logger.debug("Realtime cleanup failed", exc_info=True)
            if gateway is not None:
                try:
                    await gateway.cleanup()
                except Exception:
                    logger.debug("Realtime gateway cleanup failed", exc_info=True)

def resolve_realtime_greet_on_start(config: VoiceSessionConfig) -> bool:
    """Who opens a realtime call.

    Precedence: the immutable admission pin (true inbound decides this
    before the socket exists) → the explicit ``opening_mode`` → the
    direction-derived legacy default for callers that set neither.
    """
    pinned = getattr(config, "realtime_greet_on_start", None)
    if pinned is not None:
        return bool(pinned)
    opening_mode = getattr(config, "opening_mode", None)
    if opening_mode:
        return opening_mode == OPENING_MODE_AGENT_FIRST
    return config.direction == Direction.OUTBOUND
