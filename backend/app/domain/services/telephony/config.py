"""Configuration helpers for the telephony bridge.

Leaf module — no dependencies on other telephony submodules. Owns:
- env-driven first-speaker default
- session-config builder
- greeting builder
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from app.domain.services.telephony_session_config import (
    TELEPHONY_COMPANY_NAME,
    _warn_company_name_fallback,
    build_identity_opener,
    build_persona_greeting,
    build_telephony_session_config,
    build_telephony_greeting,
    build_telephony_inbound_greeting,
)
from app.domain.services.voice_orchestrator import Direction

logger = logging.getLogger(__name__)


# Max concurrent telephony sessions; override with MAX_TELEPHONY_SESSIONS env var.
# Each session holds 1 Deepgram WS + 1 Groq connection + audio buffers (~60KB–57MB).
# Groq free-tier llama-3.1-8b-instant hits 30K TPM at ~28-40 concurrent calls.
#
# Canonical home for this constant (moved from telephony_bridge.py so the
# domain layer — lifecycle.py's watchdog/capacity checks — doesn't have to
# reach into the API layer to read it; telephony_bridge.py now imports it
# from here instead of defining it locally).
_MAX_TELEPHONY_SESSIONS = int(os.getenv("MAX_TELEPHONY_SESSIONS", "50"))

# How many 20ms RTP frames the C++ gateway batches into one audio callback.
#
# CANONICAL HOME — both the producer and the consumer of that cadence must
# agree, and they had silently drifted apart (2026-07-31):
#
#   asterisk_adapter.py sent audio_callback_batch_frames=2 (40ms), having been
#   lowered from 4 (80ms) to match Deepgram Flux's native chunk size;
#   telephony_media_gateway.py still documented "Expected batch interval is
#   80ms (4 frames x 20ms)" and calibrated its gap detector against it.
#
# The detector therefore flagged >150ms, which is 3.75x the real 40ms cadence
# instead of the ~1.9x it was tuned for — so a genuine stall of two or three
# missed batches went unreported, while the gaps that DID fire were far more
# severe than the log made them look.
#
# Deriving both ends from these constants is what keeps the next change to the
# batch size from re-introducing the same drift.
RTP_FRAME_MS = 20
AUDIO_CALLBACK_BATCH_FRAMES = int(
    os.getenv("TELEPHONY_AUDIO_CALLBACK_BATCH_FRAMES", "2")
)
AUDIO_CALLBACK_INTERVAL_MS = RTP_FRAME_MS * AUDIO_CALLBACK_BATCH_FRAMES

# Maximum age (seconds) for an entry in _ringing_warmups / _ringing_events
# before the watchdog drops it. Outbound calls almost always connect or fail
# within ~60s; 180s is a conservative safety net for genuinely-slow carriers.
#
# Canonical home for this constant — see _MAX_TELEPHONY_SESSIONS above for
# why it lives here rather than on telephony_bridge.py.
_RINGING_MAX_AGE_S: int = 180


def _outbound_first_speaker() -> str:
    """
    Who speaks first on an outbound (campaign) call after the callee answers.

    Returns "user" or "agent".  Default is "agent" — the estimation agent speaks
    an immediate greeting so the callee never hears dead silence after picking up.
    Set TELEPHONY_FIRST_SPEAKER=user to wait for the callee to speak first
    (useful for inbound-style testing).

    Reads through TelephonySettings (T4-C5) — central env-knob registry.
    """
    from app.core.telephony_settings import get_telephony_settings
    return get_telephony_settings().first_speaker_default


def _build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign=None,
    agent_name: Optional[str] = None,
    direction: Direction = Direction.OUTBOUND,
    voice_tuning_override=None,
    ai_config_override=None,
    lead_first_name: Optional[str] = None,
    lead_last_name: Optional[str] = None,
    lead_company: Optional[str] = None,
    lead_context: Optional[dict] = None,
    opening_mode: Optional[str] = None,
):
    """
    Thin shim kept for call-site compatibility.
    All config logic lives in telephony_session_config.build_telephony_session_config().

    ``voice_tuning_override`` (T4-C3): pass an already-resolved
    :class:`VoiceTuning` to skip the sync env-only resolver. The bridge
    resolves it asynchronously (DB+env) before calling this.

    ``ai_config_override``: pass the tenant's already-resolved
    :class:`AIProviderConfig` (model/provider/pipeline selection) so the
    call sources its provider selection from the tenant's own persisted row
    rather than the process-global default. The async caller resolves it via
    :mod:`tenant_ai_config_resolver` before calling this.
    """
    return build_telephony_session_config(
        gateway_type=gateway_type,
        campaign=campaign,
        agent_name_override=agent_name,
        direction=direction,
        voice_tuning_override=voice_tuning_override,
        ai_config_override=ai_config_override,
        lead_first_name=lead_first_name,
        lead_last_name=lead_last_name,
        lead_company=lead_company,
        lead_context=lead_context,
        opening_mode=opening_mode,
    )


def _resolve_greeting_context(session) -> tuple[str, str]:
    """Pull (agent_name, company_name) off the session's agent_config with
    sensible fallbacks. The fallbacks read naturally on the wire — a
    misconfigured campaign without an agent_name still produces a
    grammatical greeting rather than crashing the call.

    The company fallback used to be a real company's name hardcoded here, a
    second copy of the one in telephony_session_config. On a multi-tenant
    platform that made an unconfigured tenant's agent announce ANOTHER
    tenant's brand on a live call. It now uses the single neutral placeholder
    (TELEPHONY_COMPANY_NAME) and says so in the log, once per call — this
    function runs on both the pre-synth and realtime greeting paths, so the
    warning is de-duplicated on the session rather than emitted per call site.
    """
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = getattr(agent_config, "company_name", None) if agent_config else None
    if isinstance(company, str):
        company = company.strip()
    if company:
        return agent_name, company

    if not getattr(session, "_company_name_fallback_logged", False):
        try:
            session._company_name_fallback_logged = True
        except Exception:  # noqa: BLE001 — a log guard must never break a call
            pass
        config = getattr(session, "config", None)
        _tenant = getattr(session, "tenant_id", None) or (
            getattr(config, "tenant_id", None) if config else None
        )
        _campaign = getattr(session, "campaign_id", None) or (
            getattr(config, "campaign_id", None) if config else None
        )
        _warn_company_name_fallback(
            tenant_id=str(_tenant) if _tenant else None,
            campaign_id=str(_campaign) if _campaign else None,
            source="agent_config.company_name",
        )
    return agent_name, TELEPHONY_COMPANY_NAME


def _call_direction(session) -> str:
    config = getattr(session, "config", None)
    raw_direction = getattr(session, "_call_direction", None)
    if raw_direction is None:
        raw_direction = getattr(config, "direction", Direction.OUTBOUND) if config else Direction.OUTBOUND
    return (
        raw_direction.value
        if isinstance(raw_direction, Direction)
        else str(raw_direction or "outbound").strip().lower()
    )


def _callee_first_name(session) -> Optional[str]:
    """The lead's cleaned first name (VoiceSessionConfig.callee_first_name)."""
    for holder in (getattr(session, "_voice_session_ref", None), session):
        config = getattr(holder, "config", None)
        name = getattr(config, "callee_first_name", None) if config is not None else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


_OPENER_NAME = re.compile(r"(Am I speaking with )[^?]*\?")


def opener_for_log(text: Optional[str]) -> str:
    """The opener without the callee's name, for log lines."""
    return _OPENER_NAME.sub(r"\1<name>?", text or "")


def outbound_opener_with_notice(session) -> Optional[str]:
    """The outbound opener saying ", on a recorded line"; None on an inbound call."""
    if _call_direction(session) == "inbound":
        return None
    return _build_call_greeting(session, first_speaker="agent", recorded_line=True)


def _build_call_greeting(session, *, first_speaker: str, recorded_line: bool = False) -> str:
    """Build the spoken greeting for the call's persona.

    Direction comes from the pinned session config, never from first-speaker
    timing. Genuine inbound agent-first calls consume the configured inbound
    greeting when present, otherwise an inbound persona greeting. Outbound
    calls open with the identity line: agent, company, that it is an AI
    assistant, and the callee's name (owner decision 2026-11-05, QA F12-03).

    Falls back to the generic outbound opener when ``persona_type`` is
    None (legacy estimation campaign) or missing from the dispatch
    table — keeps historical behaviour intact for anything not migrated
    to the persona system.

    Both the persona-specific templates and the generic fallback
    reference the same ``agent_name`` / ``company_name`` set up in
    :func:`build_telephony_session_config`, so the spoken greeting and
    the system prompt always refer to the same identity.
    """
    # first_speaker controls timing in lifecycle; it must never be used to
    # infer direction (caller-first outbound is still outbound).
    del first_speaker
    agent_name, company = _resolve_greeting_context(session)

    direction = _call_direction(session)
    if direction == "inbound":
        custom = getattr(session, "_inbound_greeting", None)
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
    else:
        # The flag-gated LLM opener (`_llm_opener_text`, llm_opener.py) is not
        # read: it was written to hide the AI ("You are a person") and must
        # never replace this line. The raw agent name, not the "your
        # assistant" fallback, so an unnamed agent says "an AI assistant".
        agent_config = getattr(session, "agent_config", None)
        return build_identity_opener(
            getattr(agent_config, "agent_name", None) if agent_config else None,
            company,
            _callee_first_name(session),
            recorded_line=recorded_line,
        )

    # Persona drives which greeting template is used. It is mirrored straight
    # onto the CallSession (CallSession.persona_type, copied from the
    # VoiceSessionConfig at session creation). Read it off the session first;
    # fall back to a nested ``config`` object for any caller that passes a
    # config-bearing object (e.g. the VoiceSessionConfig itself). Older /
    # non-telephony contexts have neither → None, which build_persona_greeting
    # handles by falling back to the generic opener.
    persona_type = getattr(session, "persona_type", None)
    if persona_type is None:
        config = getattr(session, "config", None)
        persona_type = getattr(config, "persona_type", None) if config else None

    # The campaign's reason-for-calling, when it supplied a short one. Carried
    # on AgentConfig alongside agent_name/company_name so the SPOKEN opener can
    # state it in the first breath — which is what the persona prompt has always
    # instructed, and which the pre-synthesised greeting used to make impossible
    # (it speaks first and flips _has_introduced, so the model's reason-first
    # opener never ran). None => the generic opener, exactly as before.
    agent_config = getattr(session, "agent_config", None)
    call_reason = getattr(agent_config, "call_reason", None) if agent_config else None

    return build_persona_greeting(
        persona_type=persona_type,
        agent_name=agent_name,
        company_name=company,
        direction="inbound" if direction == "inbound" else "outbound",
        call_reason=call_reason,
    )


def _build_outbound_greeting(session) -> str:
    """Backward-compatible alias used by the agent-first answer path.
    New code should call :func:`_build_call_greeting` with an explicit
    ``first_speaker`` so the direction is visible at the call site."""
    return _build_call_greeting(session, first_speaker="agent")
