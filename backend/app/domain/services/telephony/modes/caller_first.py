"""Caller-speaks-first prompt shaping.

When the campaign owner picks ``first_speaker = "user"`` the AI must sound
like the person who answered the phone, not like an outbound caller. The
persona prompts the rest of the system was built around assume an outbound
opener, so something has to re-frame the call before the first LLM call.

There are two ways the active prompt arrives here:

1. **Legacy estimation prompt** — the original hardcoded
   ``TELEPHONY_ESTIMATION_SYSTEM_PROMPT``. This is end-to-end outbound (the
   persona, the GREETING RESPONSE block, the consent-gating, all of it). A
   surgical patch would leave conflicting outbound framing in the body, so
   we replace it with a dedicated inbound base.

2. **Persona-composed prompt** — produced by
   :func:`app.services.scripts.prompts.compose_prompt` for campaigns whose
   ``script_config.persona_type`` is set. We do *not* want to throw away the
   persona's voice, objection handling, slot-collection rules, etc. — those
   are the customer's configuration. Instead we prepend a short, dominant
   directive block that re-frames the call direction. The LLM weighs early
   tokens most heavily (the same reason the legacy prompt's HARD RULES sit
   at the top), so a top-anchored directive beats anything below it that
   says "you are calling them".

Both paths share the same end-state visible to the LLM: the model knows it
answered the phone, knows how to open the call, and otherwise behaves like
the persona the campaign owner configured.
"""
from __future__ import annotations

import logging
from typing import Tuple

from app.services.scripts.prompts.direction import (
    INBOUND_DIRECTIVE_SENTINEL,
    inbound_directive_block,
)

logger = logging.getLogger(__name__)

# Re-exported for backward compatibility with existing imports
# (`from caller_first import INBOUND_DIRECTIVE_SENTINEL`). The single
# canonical definition lives in ``prompts/direction.py``.
__all__ = [
    "INBOUND_DIRECTIVE_SENTINEL",
    "select_inbound_base_prompt",
    "apply_caller_first_inbound_prompt",
]


def select_inbound_base_prompt(voice_session) -> None:
    """Re-frame ``voice_session.call_session.system_prompt`` for caller-first.

    Idempotent — safe to call multiple times. The legacy outbound prompt is
    swapped wholesale; every other prompt receives a top-anchored directive
    block that overrides outbound framing while preserving persona voice.
    """
    session = getattr(voice_session, "call_session", None)
    if session is None:
        # Defensive: unusual but possible during teardown races.
        logger.info("caller_first_skip_swap reason=no_call_session")
        return

    current = session.system_prompt or ""
    if INBOUND_DIRECTIVE_SENTINEL in current:
        return  # already applied

    agent_name, company_name = _resolve_agent_context(session)
    call_label = _short_call_id(voice_session)

    if _is_legacy_outbound(current):
        from app.domain.services.telephony_session_config import (
            TELEPHONY_INBOUND_SYSTEM_PROMPT,
        )

        session.system_prompt = TELEPHONY_INBOUND_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            company_name=company_name,
        )
        logger.info(
            "caller_first_inbound_prompt_swapped call=%s agent=%s company=%s",
            call_label, agent_name, company_name,
        )
        return

    # Persona-composed or any other custom prompt — keep the body, prepend
    # an inbound directive that the LLM cannot ignore. Persona-composed
    # prompts produced by `compose_prompt(direction=INBOUND)` already
    # carry the sentinel, so the early-return at the top of this
    # function short-circuits before we get here. This branch handles:
    # 1. Custom user-provided prompts that don't use compose_prompt.
    # 2. Persona-composed prompts whose direction wasn't propagated by an
    #    older code path (e.g. retries / migration windows).
    directive = inbound_directive_block(
        agent_name=agent_name,
        company_name=company_name,
    )
    body = current.lstrip()
    session.system_prompt = f"{directive}\n\n{body}" if body else directive
    logger.info(
        "caller_first_inbound_directive_prepended call=%s agent=%s company=%s "
        "body_chars=%d",
        call_label, agent_name, company_name, len(body),
    )
    # Metric (T4-B2). source="runtime" — a climb in this counter
    # relative to source="compose" means some persona-driven path is
    # missing direction propagation and falling through here as
    # defense-in-depth instead of being shaped at compose time.
    try:
        from app.infrastructure.metrics.voice_metrics import (
            record_inbound_directive_applied,
        )
        record_inbound_directive_applied("runtime")
    except Exception as exc:  # noqa: BLE001
        logger.debug("voice_metrics_directive_record_failed err=%s", exc)


def _is_legacy_outbound(prompt: str) -> bool:
    """Triple-marker sniff for the legacy estimation prompt.

    All three substrings appear together only in the legacy hardcoded prompt
    in telephony_session_config.py. Persona-composed prompts share none of
    them. False positives here would replace a customer's persona prompt
    with our generic inbound base — much worse than a false negative, which
    just means the directive-prepend path runs instead.
    """
    return (
        "HARD RULES" in prompt
        and "Business Development Specialist" in prompt
        and "GREETING RESPONSE" in prompt
    )


def _resolve_agent_context(call_session) -> Tuple[str, str]:
    """Pull (agent_name, company_name) off the session's agent_config with
    sensible fallbacks. The fallbacks make the directive grammatical even
    when the campaign hasn't supplied a name — the LLM will read
    'this is your assistant' once and adapt naturally."""
    cfg = getattr(call_session, "agent_config", None)
    agent_name = (getattr(cfg, "agent_name", None) if cfg else None) or "your assistant"
    company_name = (getattr(cfg, "company_name", None) if cfg else None) or "the company"
    return agent_name, company_name


def _short_call_id(voice_session) -> str:
    raw = getattr(voice_session, "call_id", None) or ""
    return raw[:12] if raw else "-"


# Backwards-compat shim. The old name is still imported in some places
# (older test files, possible plugins). Delete in a future cleanup pass
# once nothing references it.
def apply_caller_first_inbound_prompt(voice_session) -> None:
    select_inbound_base_prompt(voice_session)
