"""Legacy caller-first OUTBOUND shaping and inbound recording helpers.

Choosing who speaks first does not change who originated the call. New
outbound prompts already carry the callee-first directive from composition;
the runtime helper is an idempotent fallback for legacy outbound sessions.
True carrier-inbound sessions are excluded and use their admitted prompt.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

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
    "prepare_inbound_recording",
]


def select_inbound_base_prompt(voice_session) -> None:
    """Add the outbound callee-first directive only to eligible legacy sessions.

    Idempotent, and a no-op for true inbound sessions.
    """
    session = getattr(voice_session, "call_session", None)
    if session is None:
        # Defensive: unusual but possible during teardown races.
        logger.info("caller_first_skip_swap reason=no_call_session")
        return

    direction = getattr(getattr(voice_session, "config", None), "direction", None)
    direction = getattr(direction, "value", direction) or getattr(session, "_call_direction", None)
    if direction == "inbound":
        return  # Carrier-inbound composition is owned by the admitted snapshot.

    current = session.system_prompt or ""
    if INBOUND_DIRECTIVE_SENTINEL in current:
        return  # already applied

    agent_name, company_name = _resolve_agent_context(session)
    call_label = _short_call_id(voice_session)

    # Persona-composed or any other custom prompt — keep the body, prepend
    # an inbound directive that the LLM cannot ignore. Persona-composed
    # prompts produced by `compose_prompt(direction=INBOUND)` already
    # carry the sentinel, so the early-return above short-circuits before we
    # get here. This branch handles:
    # 1. Caller-first OUTBOUND calls — composed outbound, re-framed at runtime.
    # 2. Custom user-provided prompts that don't use compose_prompt.
    # 3. Persona-composed prompts whose direction wasn't propagated by an
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


async def _live_inbound_recording_state(db_pool) -> Optional[bool]:
    """Read the live platform switch: True, False, or None when unreadable.

    None means "could not read"; callers must not treat it as "off" (a failed
    read must never destroy audio that was already captured).
    """

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                enabled = await conn.fetchval(
                    "SELECT inbound_recording_enabled "
                    "FROM platform_runtime_controls WHERE id=1"
                )
        return enabled is True
    except Exception as exc:  # noqa: BLE001 - unreadable is not "off"
        logger.error("inbound_recording_live_switch_unavailable err=%s", exc)
        return None


async def _live_inbound_recording_enabled(db_pool) -> bool:
    """True only when the live switch was read and is on."""

    return (await _live_inbound_recording_state(db_pool)) is True


async def _set_inbound_consent_status(db_pool, call_id: str, status: str) -> None:
    if not call_id:
        return
    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls = 'on'")
                await conn.execute(
                    "UPDATE calls SET consent_status=$2, updated_at=NOW() "
                    "WHERE id=$1::uuid AND direction='inbound'",
                    call_id,
                    status,
                )
    except Exception as exc:  # noqa: BLE001 - retention remains closed
        logger.error(
            "inbound_consent_status_persist_failed call=%s status=%s err=%s",
            call_id[:12], status, exc,
        )


async def prepare_inbound_recording(voice_session) -> bool:
    """Run the shared disclosure flow, then open the inbound buffer gate.

    True inbound calls can receive caller RTP immediately after ARI answers.
    Their media session therefore starts with recording disabled; STT may still
    listen, but neither caller nor agent audio is retained until this function
    proves both that tenant policy permits recording and that any required
    disclosure completed.  Every error leaves the gate closed.
    """
    gateway = getattr(voice_session, "media_gateway", None)
    call_id = str(getattr(voice_session, "call_id", "") or "")
    set_gate = getattr(gateway, "set_recording_enabled", None)

    if callable(set_gate):
        set_gate(call_id, False)

    db_pool = None
    durable_call_id = ""
    try:
        # Admission pins both campaign policy and platform runtime controls.
        # Neither may be bypassed by the generic tenant recording policy: the
        # effective decision is the intersection of all three layers. Missing
        # snapshot data is uncertainty and therefore keeps retention closed.
        admission = getattr(voice_session, "_inbound_admission", None) or {}
        snapshot = admission.get("config_snapshot") or {}
        inbound_config = snapshot.get("inbound_config") or {}
        controls = snapshot.get("controls") or {}
        campaign_enabled = inbound_config.get("recording_enabled") is True
        platform_enabled = controls.get("recording_enabled") is True

        # Reuse the exact disclosure implementation used by agent-first calls;
        # this keeps text, interruption semantics, and the consent ledger in
        # one place instead of introducing a caller-first variant.
        from app.domain.services.telephony.modes.agent_first import (
            _disclosure_call_ids,
            _disclosure_tenant_id,
            _speak_recording_disclosure,
        )
        from app.domain.services.recording_policy_service import (
            RecordingPolicyService,
            disclosure_delivered,
            is_saved_policy,
            notice_proof_required,
        )
        from app.core.container import get_container

        container = get_container()
        db_pool = (
            getattr(container, "db_pool", None)
            if getattr(container, "is_initialized", False)
            else None
        )
        tenant_id = _disclosure_tenant_id(voice_session)
        if db_pool is None or not tenant_id:
            raise RuntimeError("recording policy context unavailable")

        durable_call_id = str(
            getattr(voice_session, "_dialer_call_id", "")
            or admission.get("call_id")
            or ""
        )
        if not campaign_enabled or not platform_enabled:
            # Not recorded: the notice must not be spoken either.
            voice_session._recording_disclosure_attempted = True
            await _set_inbound_consent_status(db_pool, durable_call_id, "not_required")
            voice_session._recording_allowed = False
            logger.info(
                "inbound_recording_gate call=%s enabled=false reason=%s",
                call_id[:12],
                "campaign_disabled" if not campaign_enabled else "platform_disabled",
            )
            return False

        # The snapshot proves what was admitted; the live read makes the
        # platform switch an actual emergency stop during call setup. An
        # unreadable switch (None) is not "off": admission just read it on.
        if (await _live_inbound_recording_state(db_pool)) is False:
            voice_session._recording_disclosure_attempted = True
            await _set_inbound_consent_status(db_pool, durable_call_id, "not_required")
            voice_session._recording_allowed = False
            logger.info(
                "inbound_recording_gate call=%s enabled=false reason=live_platform_disabled",
                call_id[:12],
            )
            return False

        strict = notice_proof_required()
        decision = await RecordingPolicyService(db_pool).decide(
            tenant_id=str(tenant_id),
        )
        # Same rule as outbound (owner decision, 18 November 2026): no saved policy is
        # recorded with the spoken notice; a missing receipt keeps the audio marked unconfirmed.
        # RECORDING_REQUIRE_NOTICE_PROOF=true restores the strict rule (saved policy + receipt).
        if not decision.should_record or (strict and not is_saved_policy(decision)):
            voice_session._recording_disclosure_attempted = True
            await _set_inbound_consent_status(db_pool, durable_call_id, "not_required")
            voice_session._recording_allowed = False
            return False

        pinned_disclosure = str(inbound_config.get("consent_message") or "").strip()
        if not pinned_disclosure:
            raise RuntimeError("pinned inbound recording disclosure is missing")
        voice_session._recording_disclosure_text_override = pinned_disclosure
        await _set_inbound_consent_status(db_pool, durable_call_id, "pending")
        await _speak_recording_disclosure(voice_session)

        call_ids = _disclosure_call_ids(voice_session)
        disclosure_spoken = disclosure_delivered(*call_ids)
        # Re-check after speech so a switch flipped during the notice still
        # prevents the buffers from ever opening.
        live_state = await _live_inbound_recording_state(db_pool)
        # A missing receipt is tolerated only when a notice could be spoken at all.
        # A realtime session has no pipeline: nothing was said, so nothing is recorded.
        # (The disclosure ledger says FAILED for both cases, so it cannot tell them apart.)
        can_speak = getattr(voice_session, "pipeline", None) is not None
        allowed = bool(
            decision.should_record
            and live_state is not False
            and (disclosure_spoken or (not strict and can_speak))
        )
        not_spoken = bool(
            decision.should_record and live_state is not False
            and not disclosure_spoken and not strict and not can_speak
        )
        if callable(set_gate):
            set_gate(call_id, allowed)
        voice_session._recording_allowed = allowed
        await _set_inbound_consent_status(
            db_pool,
            durable_call_id,
            ("granted" if disclosure_spoken else "unknown") if allowed else "declined",
        )
        logger.info(
            "inbound_recording_gate call=%s enabled=%s reason=%s notice=%s",
            call_id[:12], allowed,
            "notice_not_spoken" if not_spoken else getattr(decision, "reason", "unknown"),
            "delivered" if disclosure_spoken else "unconfirmed",
        )
        return allowed
    except Exception as exc:  # noqa: BLE001 — compliance path fails closed
        if callable(set_gate):
            set_gate(call_id, False)
        voice_session._recording_allowed = False
        # Not recorded, so no later step may tell the caller that it is.
        voice_session._recording_disclosure_attempted = True
        if db_pool is not None and durable_call_id:
            await _set_inbound_consent_status(db_pool, durable_call_id, "declined")
        logger.error(
            "inbound_recording_gate_failed call=%s err=%s — recording remains disabled",
            call_id[:12], exc,
        )
        return False
