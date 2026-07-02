"""Configuration helpers for the telephony bridge.

Leaf module — no dependencies on other telephony submodules. Owns:
- env-driven first-speaker default
- session-config builder
- greeting builder
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from app.domain.services.telephony_session_config import (
    build_persona_greeting,
    build_telephony_session_config,
    build_telephony_greeting,
    build_telephony_inbound_greeting,
)
from app.domain.services.voice_orchestrator import Direction

logger = logging.getLogger(__name__)


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
    )


def _resolve_greeting_context(session) -> tuple[str, str]:
    """Pull (agent_name, company_name) off the session's agent_config with
    sensible fallbacks. The fallbacks read naturally on the wire — a
    misconfigured campaign without an agent_name still produces a
    grammatical greeting rather than crashing the call."""
    agent_config = getattr(session, "agent_config", None)
    agent_name = (
        getattr(agent_config, "agent_name", None) if agent_config else None
    ) or "your assistant"
    company = (
        getattr(agent_config, "company_name", None) if agent_config else None
    ) or "All States Estimation"
    return agent_name, company


def _build_call_greeting(session, *, first_speaker: str) -> str:
    """Build the spoken greeting for the call's persona.

    The telephony bridge endpoint only originates OUTBOUND calls — we
    dialed them. So the greeting is ALWAYS the outbound persona greeting
    (the AI introduces itself as a caller), regardless of whether
    ``first_speaker`` is "agent" (speak immediately) or "user" (speak
    after a 2-second pause). The previous direction = inbound mapping
    on caller-first was wrong: it made the AI sound like a receptionist
    asking "How can I help?" on a call WE initiated, which is jarring.

    Falls back to the generic outbound opener when ``persona_type`` is
    None (legacy estimation campaign) or missing from the dispatch
    table — keeps historical behaviour intact for anything not migrated
    to the persona system.

    Both the persona-specific templates and the generic fallback
    reference the same ``agent_name`` / ``company_name`` set up in
    :func:`build_telephony_session_config`, so the spoken greeting and
    the system prompt always refer to the same identity.
    """
    # `first_speaker` is intentionally accepted but unused for direction
    # selection — it controls TIMING (immediate vs 2s pause) in the
    # lifecycle layer, not greeting content.
    del first_speaker
    agent_name, company = _resolve_greeting_context(session)

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

    return build_persona_greeting(
        persona_type=persona_type,
        agent_name=agent_name,
        company_name=company,
        direction="outbound",
    )


def _build_outbound_greeting(session) -> str:
    """Backward-compatible alias used by the agent-first answer path.
    New code should call :func:`_build_call_greeting` with an explicit
    ``first_speaker`` so the direction is visible at the call site."""
    return _build_call_greeting(session, first_speaker="agent")
