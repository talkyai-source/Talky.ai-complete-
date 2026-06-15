"""Pre-originate pipeline warmup for outbound calls.

Extracted from telephony_bridge.make_call. Framework-free: returns a
:class:`PrewarmResult`; the endpoint maps a failed warmup to its 503.

Why pre-warm BEFORE originating: the greeting audio (agent-first) and the
STT/TTS/LLM connections must be hot before the callee's bell rings, so a
0-ms local-PBX pickup still lands on a fully-ready pipeline instead of
dead air. This is a strict gate — if any layer fails or hangs past
TELEPHONY_PREWARM_TIMEOUT_S, we refuse to ring rather than connect a
caller to a half-cold pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.domain.services.telephony.config import (
    _outbound_first_speaker,
    _build_telephony_session_config,
)
from app.domain.services.telephony.lifecycle import _get_orchestrator
from app.domain.services.telephony.modes.agent_first import (
    prepare_pre_originate_greeting,
    warm_tts_inference_path,
    warm_llm_stream,
)
from app.domain.services.telephony.modes.caller_first import (
    select_inbound_base_prompt,
)

logger = logging.getLogger(__name__)

# TTS models that load/first-synth too slowly for the default 5s warmup gate
# (expressive, non-realtime engines). They get TELEPHONY_PREWARM_TIMEOUT_SLOW_S
# instead so the call still rings on the chosen voice.
_SLOW_TTS_MODELS = {"eleven_v3"}


@dataclass
class PrewarmResult:
    """Outcome of the pre-originate warmup.

    ``session`` is None when the pipeline failed to warm — the caller must
    then refuse origination (503). ``effective_first_speaker`` is resolved
    here (per-call override > env default) and is read back by the
    originate/store path, so it's returned even on failure.
    """
    session: Optional[Any]
    effective_first_speaker: str
    failure_reason: Optional[str]


def _resolve_first_speaker(first_speaker: Optional[str]) -> str:
    """Per-call first-speaker choice (explicit value > env default).

    The answer path reads this back off the pre-warm session in
    _on_new_call, falling back to _outbound_first_speaker() when no
    per-call value is set.
    """
    effective = (first_speaker or _outbound_first_speaker()).strip().lower()
    return effective if effective in ("agent", "user") else "agent"


def _lookup_campaign_row(container, campaign_id: Optional[str]):
    """Best-effort campaign fetch for the layered prompt composer.

    Failure is non-fatal — we fall back to the legacy prompt — so any
    error is logged and swallowed.
    """
    if not campaign_id:
        return None
    try:
        db_client = getattr(container, "db_client", None)
        if db_client is None:
            return None
        row = (
            db_client.table("campaigns")
            .select("*")
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        if getattr(row, "data", None):
            return row.data[0]
    except Exception as cexc:
        logger.warning(
            "campaign_lookup_failed campaign_id=%s err=%s — using legacy prompt",
            campaign_id, cexc,
        )
    return None


async def prepare_prewarmed_session(
    *,
    first_speaker: Optional[str],
    campaign_id: Optional[str],
    agent_name: Optional[str],
    container,
) -> PrewarmResult:
    """Build + fully warm a VoiceSession before the SIP call is originated.

    Returns a :class:`PrewarmResult`. On any warmup failure the session is
    cleaned up and ``session`` is None with ``failure_reason`` set.
    """
    effective_first_speaker = _resolve_first_speaker(first_speaker)
    campaign_row = _lookup_campaign_row(container, campaign_id)

    pre_warm_session = None
    warmup_failure_reason: Optional[str] = None
    try:
        orchestrator = _get_orchestrator()
        # Derive the call direction from per-call first_speaker so the
        # session config picks the correct base prompt up front. With
        # this in place, the runtime select_inbound_base_prompt() call
        # below becomes idempotent (the inbound sentinel is already in
        # the prompt for INBOUND calls); we keep that runtime call as
        # defense-in-depth for persona-composed prompts.
        from app.domain.services.voice_orchestrator import Direction
        call_direction = Direction.from_first_speaker(effective_first_speaker)

        # Resolve per-tenant voice tuning asynchronously (T4-C3): the
        # production path consults the DB-backed override on
        # tenant_ai_configs.voice_tuning, layered on top of env defaults
        # and code defaults. The bridge is async, build_session_config
        # is sync — so we resolve here and pass the result through.
        from app.domain.services.voice_tuning import (
            get_voice_tuning_resolver,
        )
        _campaign_tenant_id = None
        if campaign_row is not None:
            _campaign_tenant_id = (
                campaign_row.get("tenant_id") if isinstance(campaign_row, dict)
                else getattr(campaign_row, "tenant_id", None)
            )
        voice_tuning = await get_voice_tuning_resolver().for_tenant_async(
            str(_campaign_tenant_id) if _campaign_tenant_id else None,
        )

        config = _build_telephony_session_config(
            gateway_type="telephony",
            campaign=campaign_row,
            agent_name=agent_name,
            direction=call_direction,
            voice_tuning_override=voice_tuning,
        )
        # User-first only: relax the Flux end-of-turn timeout from 500ms
        # to 1000ms. The 500ms default is aggressive and was the cause of
        # the StartOfTurn → EndOfTurn → TurnResumed → EndOfTurn fragment
        # pattern observed on the very first utterance: a natural rising
        # "Hello?" with even a tiny breath gap was endpointed too eagerly,
        # firing a speculative LLM that got cancelled when the callee kept
        # talking, and the *real* turn-0 LLM call paid full streaming
        # setup again. 1000ms is still well below conversational latency
        # but lets a short opener finish cleanly. Agent-first keeps the
        # tighter 500ms because that mode's first turn is the agent's
        # greeting and the callee's reply is short and back-and-forth.
        if effective_first_speaker == "user":
            config.stt_eot_timeout_ms = 1000
        pre_warm_session = await orchestrator.create_voice_session(config)
        pre_warm_session._first_speaker = effective_first_speaker
        # Mirror onto call_session so downstream code (latency telemetry,
        # turn handlers) can read it without holding a voice_session ref.
        # The two stashes are kept in sync; nothing else writes either.
        if pre_warm_session.call_session is not None:
            pre_warm_session.call_session._first_speaker = effective_first_speaker
        if effective_first_speaker == "user":
            # Swap the system prompt for the dedicated inbound base when in
            # caller-speaks-first mode.
            select_inbound_base_prompt(pre_warm_session)
            logger.info(
                "stt_eot_timeout_user_first_relaxed call_id=%s timeout_ms=1000",
                pre_warm_session.call_id[:12],
            )
        if agent_name:
            pre_warm_session._agent_name = agent_name

        # ── Campaign knowledge (vectorless RAG, P2) ─────────────────────
        # Flag-gated + fail-soft: for inline/map_retrieve campaigns this bakes
        # the (compacted) knowledge tree into the session's system prompt now,
        # while we're async with a DB pool in hand, so every turn has it for
        # free. retrieve-mode campaigns get nothing here and are served per
        # turn in turn_streamer. A failure leaves the persona prompt untouched.
        try:
            from app.services.scripts.knowledge.session_inject import (
                apply_campaign_knowledge,
            )
            _kb_pool = getattr(getattr(container, "db_client", None), "pool", None)
            await apply_campaign_knowledge(
                pre_warm_session.call_session, campaign_row, pool=_kb_pool,
            )
        except Exception as _kb_exc:
            logger.debug("campaign_knowledge_inject_skipped: %s", _kb_exc)

        # ───────────────────────────────────────────────────────────────
        # Strict warmup gate — racer-in-starting-blocks model.
        #
        # Every layer of the pipeline (STT, TTS WebSocket, TTS voice-model
        # inference path, LLM connection + KV-cache prime) must be ready
        # before we ring the callee's bell. The callee's pickup MUST land
        # on a fully-hot pipeline regardless of which mode the campaign
        # owner picked. If any warmup fails or hangs past the timeout, we
        # refuse to originate rather than letting the callee pick up to a
        # half-cold pipeline.
        #
        # This is intentionally stricter than before — `llm_warm()` used
        # to be `asyncio.create_task(...)` (fire-and-forget) and the TTS
        # voice model only loaded as a side effect of greeting pre-synth
        # in agent-first mode. User-first calls were therefore picking up
        # to a cold TTS inference path, costing ~2s on the first turn.
        # ───────────────────────────────────────────────────────────────
        warmup_coros = []

        # 1. STT WebSocket (Deepgram Flux ready to listen)
        if hasattr(pre_warm_session.stt_provider, "pre_connect"):
            warmup_coros.append(
                pre_warm_session.stt_provider.pre_connect(
                    pre_warm_session.call_session.call_id
                )
            )

        # 2. TTS WebSocket (auth handshake done)
        _tts_connect = getattr(pre_warm_session.tts_provider, "connect_for_call", None)
        if _tts_connect is not None:
            warmup_coros.append(_tts_connect(pre_warm_session.call_id))

        # 3. LLM connection + KV-cache prime (was fire-and-forget — now strict)
        llm_warm = getattr(pre_warm_session.llm_provider, "warm_up", None)
        if llm_warm is not None:
            warmup_coros.append(llm_warm())

        # 4. TTS voice-model load (forces the inference worker to load the
        #    voice so turn 0 doesn't pay model-load latency).
        warmup_coros.append(warm_tts_inference_path(pre_warm_session))

        # 5. LLM streaming inference path warmup. `warm_up()` above opens the
        #    connection but doesn't run a real streaming generation — most
        #    providers' first stream after warm_up still pays a one-time
        #    setup cost. This drains a tiny "hi" stream so turn 0 in BOTH
        #    modes lands on a fully-streamed-end-to-end LLM path.
        warmup_coros.append(warm_llm_stream(pre_warm_session))

        # Most TTS engines warm up well within 5s. The expressive ElevenLabs
        # models (eleven_v3) load + first-synth much slower and were being
        # refused at the gate ("pipeline not ready within 5s"). Give those a
        # longer window so the call still rings on the chosen high-quality
        # voice instead of failing. Fast models keep the tight 5s.
        prewarm_timeout_s = float(
            os.getenv("TELEPHONY_PREWARM_TIMEOUT_S", "5.0")
        )
        try:
            _provider = (getattr(config, "tts_provider_type", "") or "").lower()
            _model = (getattr(config, "tts_model", "") or "").lower()
            if _provider == "elevenlabs" and _model in _SLOW_TTS_MODELS:
                prewarm_timeout_s = float(
                    os.getenv("TELEPHONY_PREWARM_TIMEOUT_SLOW_S", "20.0")
                )
                logger.info(
                    "prewarm: slow TTS model %s — extended warmup window %.1fs",
                    _model, prewarm_timeout_s,
                )
        except Exception:
            pass
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*warmup_coros, return_exceptions=True),
                timeout=prewarm_timeout_s,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                "pre_originate_warmup_timeout: pipeline not ready within "
                f"{prewarm_timeout_s}s — refusing to ring"
            )
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            raise RuntimeError(
                f"pre_originate_warmup_handshake_failed: {failed[0]!r}"
            )

        # Greeting pre-synth (only when audio will actually be played).
        # By now the TTS voice model is already loaded by step 4 above,
        # so this synth is fast even on the very first call of the process.
        await prepare_pre_originate_greeting(pre_warm_session, effective_first_speaker)

    except Exception as warm_exc:
        warmup_failure_reason = repr(warm_exc)
        logger.error(
            "pre_originate_warmup_failed: %s — refusing to ring with cold pipeline",
            warm_exc,
        )
        if pre_warm_session is not None:
            try:
                await _get_orchestrator().end_session(pre_warm_session)
            except Exception:
                pass
            pre_warm_session = None

    return PrewarmResult(
        session=pre_warm_session,
        effective_first_speaker=effective_first_speaker,
        failure_reason=warmup_failure_reason,
    )
