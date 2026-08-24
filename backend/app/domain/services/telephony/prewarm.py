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


async def _resolve_session_accent(call_session, provider: Optional[str]) -> None:
    """Resolve the selected voice's accent (warming the ElevenLabs catalog if
    needed) and cache it on the session, so accent-matched fillers/dialect are
    reliably applied from the first turn — including ElevenLabs voices, whose
    accent isn't derivable from the id. Best-effort: never raises."""
    try:
        from app.services.scripts.prompts.accent_fillers import resolve_accent_async
        accent = await resolve_accent_async(
            getattr(call_session, "voice_id", None), provider
        )
        call_session._voice_accent = accent
        logger.info(
            "prewarm: resolved voice accent=%s for call %s",
            accent, getattr(call_session, "call_id", "?"),
        )
    except Exception:
        pass


async def _lookup_campaign_row(campaign_id: Optional[str]):
    """Best-effort campaign fetch for the layered prompt composer.

    Failure is non-fatal — we fall back to the legacy prompt — so any
    error is logged and swallowed.

    2026-07-08: this runs on every outbound call during pre-warm (hot path),
    so it was moved off the blocking postgres_adapter (`db_client.table()`
    — blocks the event loop on the shared thread pool AND opens an unpooled
    asyncpg connection per call) onto the pooled async `get_db()` connection.
    `get_db()` reads the same tenant-isolation contextvars the adapter did,
    so RLS/tenant behaviour is unchanged.
    """
    if not campaign_id:
        return None
    try:
        from app.core.db import get_db

        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM campaigns WHERE id = $1::uuid LIMIT 1",
                str(campaign_id),
            )
        if not row:
            return None
        data = dict(row)
        # 2026-07-09 REGRESSION FIX: raw asyncpg returns jsonb columns as JSON
        # *strings* (no codec registered), while the old postgres_adapter
        # decoded them to dicts. Downstream, _extract_script_config() rejects a
        # str ("unexpected type=str — ignoring"), which silently threw away the
        # campaign's ENTIRE identity — agent names, company, persona — and every
        # call got a random built-in name + the fallback company (observed live
        # 2026-07-09, 40 warnings). Decode jsonb fields back to dicts here.
        import json as _json
        for _k in ("script_config", "calling_config"):
            _v = data.get(_k)
            if isinstance(_v, str) and _v:
                try:
                    data[_k] = _json.loads(_v)
                except Exception:
                    pass  # leave as-is; downstream guards handle it
        return data
    except Exception as cexc:
        logger.warning(
            "campaign_lookup_failed campaign_id=%s err=%s — using legacy prompt",
            campaign_id, cexc,
        )
    return None


async def _attach_llm_opener(
    pre_warm_session,
    campaign_row,
    *,
    lead_first_name: Optional[str],
    lead_last_name: Optional[str],
    lead_company: Optional[str],
) -> None:
    """Author this call's opening sentence with the LLM, if the flag is on.

    WHERE THE LATENCY GOES (this is the whole reason it lives here)
    --------------------------------------------------------------
    ``prepare_prewarmed_session`` is awaited by the bridge endpoint BEFORE it
    originates the SIP call, so nothing in this function is on the ANSWER path:
    the callee's phone has not rung yet and no one is listening to silence
    while we think. It also runs AFTER the strict warmup gate above, so the
    LLM connection and its streaming path are already hot — the generation is
    a warm round-trip, not a cold one.

    The answer path (``modes.agent_first._send_outbound_greeting``) only pumps
    the ``_presynth_greeting_audio`` chunks that ``prepare_pre_originate_
    greeting`` produces immediately after this returns. It never calls into
    :mod:`llm_opener`.

    FAIL-SOFT, ALWAYS
    -----------------
    Stashing nothing is the safe outcome: ``_build_call_greeting`` falls back
    to the bare pickup greeting every call gets today (see the 2026-08-11
    OPENER REDESIGN note in telephony_session_config.py, above
    ``_PERSONA_GREETINGS``). So every failure — flag off, no campaign, no
    buyer context, provider error, timeout, output that fails validation —
    simply leaves the attribute unset. This function must therefore never
    raise: it sits inside the warmup try-block, and an exception escaping
    here would tear the session down and refuse to ring a call over a
    *cosmetic* feature.

    NOTE: if this flag is ever turned on, the LLM-authored opener is spoken
    as the literal FIRST turn (validate_opener requires identity in it,
    see llm_opener.py) — which is the monologue-on-turn-1 shape the
    2026-08-11 redesign removed from the default path. Turning the flag on
    without revisiting that is a deliberate exception to the new default
    behaviour, not an oversight.
    """
    try:
        from app.domain.services.telephony.llm_opener import (
            generate_opener,
            llm_opener_enabled,
        )

        # Cheapest possible check first: with the flag off this costs one
        # env read and the call is byte-for-byte what it is today.
        if not llm_opener_enabled():
            return

        call_session = getattr(pre_warm_session, "call_session", None)
        config = getattr(pre_warm_session, "config", None)
        if call_session is None or config is None:
            return

        agent_config = getattr(config, "agent_config", None)
        if agent_config is None:
            return

        # campaign_slots is where the operator's real business context lives
        # (industry, services, value proposition...). Re-use the SAME extractor
        # the prompt composer uses so a jsonb-as-str campaign row decodes
        # identically here — see _extract_script_config's 2026-07-09 note.
        from app.domain.services.telephony_session_config import (
            _extract_script_config,
        )

        script_config = _extract_script_config(campaign_row) or {}
        campaign_slots = script_config.get("campaign_slots")

        opener = await generate_opener(
            llm_provider=getattr(pre_warm_session, "llm_provider", None),
            persona_type=getattr(config, "persona_type", None),
            agent_name=getattr(agent_config, "agent_name", None),
            company_name=getattr(agent_config, "company_name", None),
            call_reason=getattr(agent_config, "call_reason", None),
            campaign_slots=campaign_slots,
            lead_first_name=lead_first_name,
            lead_last_name=lead_last_name,
            lead_company=lead_company,
            # The bridge only ever ORIGINATES calls, and _build_call_greeting
            # deliberately speaks the outbound opener in both first-speaker
            # modes ("user" changes the TIMING, not who dialled whom).
            direction="outbound",
            call_id=getattr(pre_warm_session, "call_id", None),
        )
        if opener:
            # Read back by telephony.config._build_call_greeting, which is the
            # single place the spoken opener text is chosen — so this covers
            # BOTH the pre-synth fast path and the realtime slow-path fallback
            # in _send_outbound_greeting.
            call_session._llm_opener_text = opener
    except Exception as exc:  # noqa: BLE001 — never fail a call over an opener
        logger.warning(
            "llm_opener_attach_failed call_id=%s err=%r — using the template",
            str(getattr(pre_warm_session, "call_id", "-"))[:12], exc,
        )


def _start_opening_ladder_generation(pre_warm_session, effective_first_speaker: str) -> None:
    """Fire-and-forget: generate this call's LLM opening ladder in the
    background, during the ring/warmup window.

    NEVER AWAITED — this is the whole point. The opening "Hello?" ladder is
    read by audio_ingest.py's silence monitor only once the callee has
    already gone silent after pickup; an LLM round-trip at that moment is
    exactly what the task forbids. Firing it here instead (before the strict
    warmup gate even starts, so it gets the most wall-clock time possible
    before the phone is answered) means a slow or failing provider costs the
    call NOTHING: the strict gate below doesn't wait on this task, origination
    proceeds on schedule either way, and turn_director.choose_silence_phrase
    simply reads whatever landed on ``call_session._opening_ladder`` by the
    time a nudge actually fires — the static ladder if this task is still
    running, errored, or was never started.

    Gated to caller-first ("user" first-speaker) calls only: the opening
    ladder is only ever consulted when ``_is_caller_first`` is True in
    audio_ingest.py (see turn_director.OPENING_PHRASES' docstring) — an
    agent-first call would never read this, so generating it would just be a
    wasted LLM call.
    """
    call_session = getattr(pre_warm_session, "call_session", None)
    if call_session is None or effective_first_speaker != "user":
        return
    try:
        from app.domain.services.telephony.opening_ladder import (
            generate_opening_ladder,
            opening_ladder_enabled,
        )

        # Cheapest possible check first: flag off costs one env read and
        # starts no task at all.
        if not opening_ladder_enabled():
            return

        llm_provider = getattr(pre_warm_session, "llm_provider", None)
        if llm_provider is None:
            return

        call_id = getattr(pre_warm_session, "call_id", None)

        async def _run() -> None:
            try:
                ladder = await generate_opening_ladder(
                    llm_provider=llm_provider, call_id=call_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as run_exc:  # noqa: BLE001 - never surface
                logger.debug(
                    "opening_ladder_background_failed call=%s err=%r",
                    str(call_id or "-")[:12], run_exc,
                )
                return
            if ladder:
                # Stashed exactly like _presynth_greeting_audio /
                # _presynth_greeting_text below — a plain attribute the
                # answer-path / nudge-path reads with getattr(..., None).
                call_session._opening_ladder = ladder

        asyncio.create_task(_run())
    except Exception as exc:  # noqa: BLE001 - starting the task must never fail a call
        logger.debug(
            "opening_ladder_start_failed call=%s err=%r",
            str(getattr(pre_warm_session, "call_id", "-"))[:12], exc,
        )


async def prepare_prewarmed_session(
    *,
    first_speaker: Optional[str],
    campaign_id: Optional[str],
    agent_name: Optional[str],
    container,
    lead_first_name: Optional[str] = None,
    lead_last_name: Optional[str] = None,
    lead_company: Optional[str] = None,
    lead_context: Optional[dict] = None,
) -> PrewarmResult:
    """Build + fully warm a VoiceSession before the SIP call is originated.

    Returns a :class:`PrewarmResult`. On any warmup failure the session is
    cleaned up and ``session`` is None with ``failure_reason`` set.
    """
    effective_first_speaker = _resolve_first_speaker(first_speaker)
    campaign_row = await _lookup_campaign_row(campaign_id)

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

        # Resolve the tenant's persisted AI provider config (model / provider /
        # temperature / STT engine / pipeline mode) the same async way. This is
        # what stops one tenant's AI-Options selection from bleeding into
        # another tenant's live call: the outbound call now sources its provider
        # selection from campaign.tenant_id's own row, not a process-global.
        from app.domain.services.tenant_ai_config_resolver import (
            get_tenant_ai_config_resolver,
        )
        ai_config = await get_tenant_ai_config_resolver().for_tenant_async(
            str(_campaign_tenant_id) if _campaign_tenant_id else None,
        )

        config = _build_telephony_session_config(
            gateway_type="telephony",
            campaign=campaign_row,
            agent_name=agent_name,
            direction=call_direction,
            voice_tuning_override=voice_tuning,
            ai_config_override=ai_config,
            lead_first_name=lead_first_name,
            lead_last_name=lead_last_name,
            lead_company=lead_company,
            lead_context=lead_context,
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

        # ── Realtime (gpt-realtime-2): unified pipeline — skip cascaded warmup ──
        # A realtime session is ONE OpenAI speech-to-speech WebSocket, already
        # connected by _create_realtime_voice_session. There is NO separate
        # STT / TTS / LLM provider to pre-warm, so the strict cascaded warmup
        # gate below (STT pre_connect, TTS connect_for_call, LLM warm_up, TTS
        # inference load, LLM stream prime, greeting pre-synth) does not apply —
        # and it FAILS on the absent cascaded providers, surfacing to the user
        # as "voice provider not ready" and refusing the call. Realtime is a
        # unified system: the only readiness that matters (the OpenAI Realtime
        # session) is already established, and the opening greeting is handled by
        # the bridge's own trigger_greeting. So go straight to ready.
        if getattr(pre_warm_session, "realtime_bridge", None) is not None:
            logger.info(
                "prewarm: realtime session %s — unified pipeline ready, "
                "skipping cascaded warmup gate",
                pre_warm_session.call_id[:12],
            )
            return PrewarmResult(
                session=pre_warm_session,
                effective_first_speaker=effective_first_speaker,
                failure_reason=None,
            )

        # LLM-authored opening ladder (flag-gated, DEFAULT OFF). Fired here,
        # BEFORE the strict warmup gate below, so the background task gets the
        # most possible time to land before the callee picks up — but it is
        # never awaited, so a slow provider costs this call nothing. See
        # _start_opening_ladder_generation's docstring for the full fail-soft
        # story.
        _start_opening_ladder_generation(pre_warm_session, effective_first_speaker)

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

        # Resolve the voice accent in the background (non-gating). Warms the
        # ElevenLabs catalog if cold so an EL British voice reliably gets its
        # British dialect from turn 0. Never blocks or fails the call.
        try:
            asyncio.create_task(
                _resolve_session_accent(
                    pre_warm_session.call_session,
                    getattr(config, "tts_provider_type", None),
                )
            )
        except Exception:
            pass

        # LLM-authored opener (flag-gated, DEFAULT OFF). Must run BEFORE the
        # pre-synth below, because pre-synth is what turns the opener TEXT into
        # the audio the callee actually hears. Never raises; when it stashes
        # nothing the pre-synth uses the existing template, unchanged.
        await _attach_llm_opener(
            pre_warm_session,
            campaign_row,
            lead_first_name=lead_first_name,
            lead_last_name=lead_last_name,
            lead_company=lead_company,
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
