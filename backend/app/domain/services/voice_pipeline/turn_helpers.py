"""Pure turn-0 floor + telemetry-label helpers extracted from
VoicePipelineService (item 2, slice 8). No instance state — plain functions.
Re-exported from voice_pipeline_service for backward-compat (a test imports
_alpha_char_count / _should_reject_turn_0 from there)."""
from __future__ import annotations

from typing import Optional


def _first_speaker_label(session) -> str:
    """Return ``"agent"`` or ``"user"`` for telemetry. The bridge stashes
    the per-call first-speaker on call_session at session creation; this
    helper just reads it with a safe default for legacy code paths that
    haven't been updated yet."""
    raw = getattr(session, "_first_speaker", None) or "agent"
    value = str(raw).strip().lower()
    return "user" if value == "user" else "agent"


def _persona_label(session) -> Optional[str]:
    """Return ``session.config.persona_type`` if set, else None.

    Used for metric labelling — bounded to {lead_gen, customer_support,
    receptionist, none} downstream so cardinality stays sane.
    """
    config = getattr(session, "config", None)
    if config is None:
        return None
    raw = getattr(config, "persona_type", None)
    return str(raw) if raw else None


def _prompt_kind_label(session) -> str:
    """Return ``"inbound"`` or ``"outbound"`` for telemetry.

    Preferred source: ``session.config.direction`` — a typed
    ``Direction`` enum set by ``build_telephony_session_config``. This
    is the contract path and never lies about the call direction.

    Fallback: substring search for the inbound directive sentinel in
    the active system_prompt. This covers two edge cases:
    1. Sessions created via the legacy code path that never set
       ``direction`` on the config (older browser/ask_ai entry points).
    2. Persona-composed prompts where the bridge applied a runtime
       directive prepend without updating the config — a transitional
       state we'll eliminate when persona templates gain direction
       awareness in a future change.
    """
    config = getattr(session, "config", None)
    if config is not None and getattr(config, "direction", None) is not None:
        # Direction is a string-backed enum; comparing the value works
        # for both the enum instance and the bare string form.
        return str(config.direction.value).lower()

    # Local import keeps the latency_tracker callable on its own without
    # importing the telephony modes (used in non-telephony contexts too).
    from app.domain.services.telephony.modes.caller_first import (
        INBOUND_DIRECTIVE_SENTINEL,
    )
    prompt = getattr(session, "system_prompt", "") or ""
    return "inbound" if INBOUND_DIRECTIVE_SENTINEL in prompt else "outbound"


# Default turn-0 floor — used when the session.config doesn't carry an
# explicit tuning value (legacy code paths, ask_ai sessions). Production
# telephony reads its values from the per-tenant voice_tuning resolver
# via VoiceSessionConfig at session-build time. See voice_tuning.py.
_TURN_0_MIN_CONFIDENCE = 0.4
_TURN_0_MIN_ALPHA_CHARS = 2


def _alpha_char_count(text: str) -> int:
    """Count letters in a string (ignores digits, whitespace, punctuation)."""
    return sum(1 for ch in text if ch.isalpha())


def _should_reject_turn_0(
    transcript: str,
    confidence: Optional[float],
    *,
    min_confidence: float = _TURN_0_MIN_CONFIDENCE,
    min_alpha_chars: int = _TURN_0_MIN_ALPHA_CHARS,
) -> Optional[str]:
    """Return a short reason string if a turn-0 transcript should be
    dropped, or ``None`` if it should pass.

    Only applies when this is the first user turn — callers must check
    that before invoking this function. Splitting the predicate out keeps
    handle_turn_end readable and lets the rule be tested in isolation.

    The floors are passed in (rather than read from the module constants)
    so per-tenant tuning at T3.9 reaches this rule. Callers default the
    kwargs to the module constants when running outside a configured
    session.
    """
    if _alpha_char_count(transcript) < min_alpha_chars:
        return "too_short"
    if confidence is not None and confidence < min_confidence:
        return "low_confidence"
    return None


def _resolve_turn_0_floors(session) -> tuple[float, int]:
    """Return ``(min_confidence, min_alpha_chars)`` for the active session.

    Reads the per-tenant tuning that landed on ``session.config`` when
    the session was built; falls back to the module defaults when those
    fields are missing (legacy or non-telephony sessions)."""
    config = getattr(session, "config", None)
    if config is None:
        return _TURN_0_MIN_CONFIDENCE, _TURN_0_MIN_ALPHA_CHARS
    min_conf = getattr(config, "turn_0_min_confidence", _TURN_0_MIN_CONFIDENCE)
    min_chars = getattr(config, "turn_0_min_alpha_chars", _TURN_0_MIN_ALPHA_CHARS)
    return float(min_conf), int(min_chars)
