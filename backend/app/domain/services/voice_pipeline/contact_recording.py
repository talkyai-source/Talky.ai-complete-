"""Model-interpreted contacts with server-owned source and save evidence.

The model interprets language; this boundary validates data and currentness.
An exact quote proves where an interpretation came from, not that it is correct.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email

from app.domain.services.phone_number_normalizer import normalize_phone_for_capture
from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus, ContactCaptureState, ContactSource,
)
from app.services.scripts.call_state_tracker import CallState

CONTACT_TOOL_NAME = "record_contact"
_UNSET = object()
CONTACT_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": CONTACT_TOOL_NAME,
        "description": (
            "Record the caller's own email or phone from their current words. "
            "Interpret spelling and corrections in context; never guess missing parts or "
            "record another person's contact as the caller's. Set keeps a pending candidate; "
            "confirm records a later caller's clear agreement to that exact candidate. "
            "Add preserves a confirmed contact when the caller offers another. Withdraw "
            "removes the current contact. Quote their current words exactly. Ask naturally "
            "when unclear. Only claim saving when saved=true; this tool sends nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["email", "phone"]},
                "operation": {"type": "string", "enum": ["set", "add", "confirm", "withdraw"]},
                "value": {"type": ["string", "null"], "description": "Complete intended value, or incomplete caller-provided value to retain for clarification; null for withdrawal."},
                "expected_value": {"type": ["string", "null"], "description": "Exact current candidate shown in contact state, or null when absent. Never substitute the replacement here."},
                "source_quote": {"type": "string", "description": "Exact excerpt of the current caller turn supporting this operation."},
            },
            "required": ["kind", "operation", "value", "expected_value", "source_quote"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class ContactTurn:
    text: str
    source: ContactSource


def bind_contact_turn(session, text: str, source: ContactSource | None) -> ContactTurn | None:
    """Install only a canonical final caller turn; never parse its meaning."""
    turn = None
    valid_source = (isinstance(text, str) and isinstance(source, ContactSource)
        and source.revision_sha256 == hashlib.sha256(text.strip().encode("utf-8")).hexdigest())
    if valid_source and text.strip():
        turn = ContactTurn(text.strip(), source)
    session._contact_turn = turn
    if not isinstance(getattr(session, "captured_slots", None), CallState):
        session.captured_slots = CallState()
    if valid_source:
        invalidate_revised_contacts(session, source)
    return turn


def invalidate_revised_contacts(session, source: ContactSource) -> bool:
    """A revised source withdraws only its own current contribution, not new facts."""
    state = getattr(session, "captured_slots", None)
    if not isinstance(state, CallState):
        return False
    changes = {}
    for kind in ("email", "phone"):
        capture = getattr(state, f"{kind}_capture", None)
        if capture is None:
            continue
        def changed(owner):
            return (owner is not None and owner.provider_item_id == source.provider_item_id
                    and owner.caller_turn_order == source.caller_turn_order
                    and owner.revision_sha256 != source.revision_sha256)
        if changed(capture.value_source):
            updated = replace(capture, status=CaptureStatus.NEEDS_CLARIFICATION,
                normalized_value=None, raw_value=None, confirmed_at=None,
                confirmation_source=None, confirmation_evidence=None, readback=None,
                status_source=source)
        elif changed(capture.confirmation_source):
            updated = replace(capture, status=CaptureStatus.AWAITING_CONFIRMATION,
                confirmed_at=None, confirmation_source=None,
                confirmation_evidence=None, readback=None, status_source=source)
        elif changed(capture.status_source):
            updated = replace(capture, status=CaptureStatus.NEEDS_CLARIFICATION,
                normalized_value=None, raw_value=None, confirmed_at=None,
                confirmation_source=None, confirmation_evidence=None, readback=None,
                status_source=source)
        else:
            continue
        changes.update({f"{kind}_capture": updated, kind: updated.normalized_value,
                        f"{kind}_confirmed": False})
    if changes:
        session.captured_slots = replace(state, **changes)
        session._lead_capture_revision_source = source
    return bool(changes)


def _normalise(kind, value, region):
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        return None
    try:
        if kind == "email":
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        return normalize_phone_for_capture(value.strip(), region)
    except (EmailNotValidError, ValueError):
        return None


def _result(status, *, capture=None, saved=False):
    return {"action": CONTACT_TOOL_NAME, "success": saved, "saved": saved,
            "status": status, "value": getattr(capture, "normalized_value", None),
            "validation_status": getattr(capture, "validation_status", None),
            "caller_quote": getattr(capture, "raw_value", None),
            "confirmation_allowed": saved}


async def record_contact(session, arguments, *, turn=_UNSET, pool=None) -> dict:
    """Apply one model interpretation against a fixed caller and candidate snapshot."""
    turn = getattr(session, "_contact_turn", None) if turn is _UNSET else turn
    kind = arguments.get("kind") if isinstance(arguments, dict) else None
    def current_capture():
        return (getattr(getattr(session, "captured_slots", None), f"{kind}_capture", None)
                if kind in ("email", "phone") else None)
    if not isinstance(turn, ContactTurn):
        return _result("caller_evidence_unavailable", capture=current_capture())
    if not isinstance(arguments, dict) or set(arguments) != {"kind", "operation", "value", "expected_value", "source_quote"}:
        return _result("invalid_arguments", capture=current_capture())
    kind, operation = arguments["kind"], arguments["operation"]
    quote, value, expected = arguments["source_quote"], arguments["value"], arguments["expected_value"]
    if (kind not in ("email", "phone") or operation not in ("set", "add", "confirm", "withdraw")
            or not isinstance(quote, str) or not 1 <= len(quote.strip()) <= 2000
            or quote.strip() not in turn.text
            or (value is not None and (not isinstance(value, str) or len(value) > 320))
            or (expected is not None and not isinstance(expected, str))):
        return _result("invalid_arguments", capture=current_capture())
    lock = getattr(session, "_contact_record_lock", None)
    if lock is None:
        lock = session._contact_record_lock = asyncio.Lock()
    async with lock:
        if getattr(session, "_contact_turn", None) is not turn:
            return _result("stale_caller_turn", capture=current_capture())
        state = session.captured_slots
        current = getattr(state, f"{kind}_capture", None)
        if expected != getattr(current, "normalized_value", None):
            return _result("contact_changed", capture=current)
        normalized = _normalise(kind, value, getattr(session, "contact_phone_region", None))
        source = turn.source
        extra = {}
        if operation == "confirm":
            if (current is None or normalized is None or normalized != current.normalized_value
                    or current.status not in {CaptureStatus.AWAITING_CONFIRMATION, CaptureStatus.CONFIRMED}
                    or current.value_source is None
                    or source.caller_turn_order <= current.value_source.caller_turn_order):
                return _result("confirmation_not_current", capture=current)
            capture = current if current.status is CaptureStatus.CONFIRMED else replace(
                current, status=CaptureStatus.CONFIRMED, confirmed_at=datetime.now(timezone.utc),
                confirmation_source=source, status_source=source, readback=None,
                confirmation_evidence="model_interpreted_caller_confirmation")
        elif operation == "withdraw":
            if value is not None or current is None:
                return _result("invalid_arguments", capture=current)
            capture = replace(current, status=CaptureStatus.CANCELLED, normalized_value=None,
                raw_value=quote.strip(), confirmed_at=None, confirmation_source=None,
                confirmation_evidence=None, readback=None, status_source=source)
        else:
            if not isinstance(value, str) or not value.strip():
                return _result("invalid_arguments", capture=current)
            if operation == "add":
                if (current is None or current.status is not CaptureStatus.CONFIRMED
                        or current.confirmation_source is None
                        or source.caller_turn_order <= current.confirmation_source.caller_turn_order
                        or normalized == current.normalized_value):
                    return _result("additional_contact_not_ready", capture=current)
                extra[f"earlier_{kind}_captures"] = (*getattr(state, f"earlier_{kind}_captures"), current)
            capture = ContactCaptureState(kind=kind,
                status=CaptureStatus.AWAITING_CONFIRMATION if normalized else CaptureStatus.NEEDS_CLARIFICATION,
                raw_value=quote.strip(), normalized_value=normalized, from_caller=True,
                value_source=source, status_source=source)
            if operation == "set" and current is not None and normalized and normalized == current.normalized_value:
                capture = current  # Repeating a value does not erase prior confirmation.
        session.captured_slots = replace(state, **extra, **{
            f"{kind}_capture": capture, kind: capture.normalized_value,
            f"{kind}_confirmed": capture.status is CaptureStatus.CONFIRMED,
            "active_contact_kind": None, "agent_asked_kind": None,
        })
        from app.domain.services.voice_pipeline.lead_slot_capture import (
            capture_turn_slots, contact_field_key, contact_save_acknowledged, contact_save_failed_fields,
        )
        if pool is None:
            pool = getattr(session, "_voice_action_pool", None)
        if pool is None:
            try:
                from app.core.container import get_container
                pool = get_container().db_pool
            except Exception:
                pool = None
        if pool is not None:
            await capture_turn_slots(session, pool=pool, reason="record_contact_tool")
        if getattr(session, "_contact_turn", None) is not turn:
            return _result("stale_caller_turn", capture=current_capture())
        saved = contact_save_acknowledged(session, kind)
        failed = contact_field_key(session.captured_slots, kind) in contact_save_failed_fields(session)
        return _result("saved" if saved else "save_failed" if failed else "not_saved",
                       capture=capture, saved=saved)
