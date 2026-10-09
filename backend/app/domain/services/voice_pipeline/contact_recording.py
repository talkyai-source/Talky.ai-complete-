"""Model-interpreted contacts with server-owned source and save evidence.

The model interprets language; this boundary validates data and currentness.
An exact quote proves where an interpretation came from, not that it is correct.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email

from app.domain.services.phone_number_normalizer import normalize_phone_for_capture
from app.domain.services.voice_pipeline.contact_capture import (
    CAPTURE_FIELD_TYPES, CaptureStatus, ContactCaptureState, ContactSource,
)
from app.services.scripts.call_state_tracker import CallState, contact_entries

logger = logging.getLogger(__name__)

CONTACT_TOOL_NAME = "record_contact"
_UNSET = object()
CONTACT_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": CONTACT_TOOL_NAME,
        "description": (
            "Record the caller's own email, phone, full name or company. "
            "Interpret spelling and corrections in context; never guess missing parts or "
            "record another person's details or the agent's company as the caller's. Keep name/company "
            "spelling and case; do not split names. Set keeps a pending candidate, quoting the "
            "caller's words where they gave it (this turn or an earlier one); "
            "confirm records the caller's clear agreement in this turn to that exact candidate. "
            "If they agreed to a detail you never set, set it, then confirm. For a phone, give "
            "+country code when the country is clear from the call. "
            "Add preserves a confirmed email/phone when the caller offers another; name/company "
            "each have one current value. Withdraw "
            "removes the selected contact. Use its field_key to correct or withdraw an earlier "
            "contact; omit it for the current contact. Quote their current words exactly. Ask naturally "
            "when unclear. Only claim saving when saved=true; this tool sends nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(CAPTURE_FIELD_TYPES)},
                "operation": {"type": "string", "enum": ["set", "add", "confirm", "withdraw"]},
                "value": {"type": ["string", "null"], "description": "Complete intended value, or incomplete caller-provided value to retain for clarification; null for withdrawal."},
                "expected_value": {"type": ["string", "null"], "description": "Exact selected candidate shown in contact state, or null when absent. Never substitute the replacement here."},
                "field_key": {"type": ["string", "null"], "description": "Existing contact key from state or tool results (email, email_2, phone, full_name, company_name); omitted/null selects current. Add is current email/phone only."},
                "source_quote": {"type": "string", "description": "The caller's exact words supporting this operation: where they gave the detail, or their agreement now for confirm."},
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


_TURN_HISTORY = 20  # earlier caller turns a quote may come from


def bind_contact_turn(session, text: str, source: ContactSource | None) -> ContactTurn | None:
    """Install only a canonical final caller turn; never parse its meaning.

    Bound turns are also kept (the last 20) so a detail given in one turn can be
    recorded once the caller confirms it in a later one, quoting where it was
    actually said.
    """
    turn = None
    valid_source = (isinstance(text, str) and isinstance(source, ContactSource)
        and source.revision_sha256 == hashlib.sha256(text.strip().encode("utf-8")).hexdigest())
    if valid_source and text.strip():
        turn = ContactTurn(text.strip(), source)
        history = [item for item in getattr(session, "_contact_turns", None) or ()
                   if item.source.caller_turn_order != source.caller_turn_order]
        session._contact_turns = (history + [turn])[-_TURN_HISTORY:]
    session._contact_turn = turn
    if not isinstance(getattr(session, "captured_slots", None), CallState):
        session.captured_slots = CallState()
    if valid_source:
        invalidate_revised_contacts(session, source)
    return turn


def _with_contact_changes(state: CallState, **changes) -> CallState:
    updated = replace(state, **changes)
    # CallState's legacy scalar lift must not manufacture capture evidence for
    # an unrelated field while applying this contact's change.
    for field in (f"{kind}_capture" for kind in CAPTURE_FIELD_TYPES):
        if field not in changes:
            object.__setattr__(updated, field, getattr(state, field))
    return updated


def invalidate_revised_contacts(session, source: ContactSource) -> bool:
    """A revised source withdraws only its owned current or earlier contribution."""
    state = getattr(session, "captured_slots", None)
    if not isinstance(state, CallState):
        return False
    def changed(owner):
        return (owner is not None and owner.provider_item_id == source.provider_item_id
                and owner.caller_turn_order == source.caller_turn_order
                and owner.revision_sha256 != source.revision_sha256)

    def revised(capture):
        if capture is None:
            return capture
        if changed(capture.value_source):
            return replace(capture, status=CaptureStatus.NEEDS_CLARIFICATION,
                normalized_value=None, raw_value=None, confirmed_at=None,
                confirmation_source=None, confirmation_evidence=None, readback=None,
                status_source=source)
        if changed(capture.confirmation_source):
            return replace(capture, status=CaptureStatus.AWAITING_CONFIRMATION,
                confirmed_at=None, confirmation_source=None,
                confirmation_evidence=None, readback=None, status_source=source)
        if changed(capture.status_source):
            return replace(capture, status=CaptureStatus.NEEDS_CLARIFICATION,
                normalized_value=None, raw_value=None, confirmed_at=None,
                confirmation_source=None, confirmation_evidence=None, readback=None,
                status_source=source)
        return capture

    changes = {}
    for kind in CAPTURE_FIELD_TYPES:
        capture = getattr(state, f"{kind}_capture", None)
        updated = revised(capture)
        if updated is not capture:
            changes.update({f"{kind}_capture": updated, kind: updated.normalized_value,
                            f"{kind}_confirmed": False})
        earlier = getattr(state, f"earlier_{kind}_captures", ())
        updated_earlier = tuple(revised(item) for item in earlier)
        if updated_earlier != earlier:
            changes[f"earlier_{kind}_captures"] = updated_earlier
    if changes:
        session.captured_slots = _with_contact_changes(state, **changes)
        session._lead_capture_revision_source = source
    return bool(changes)


def _quote_words(text) -> list[str]:
    """Words of a quote or transcript, ignoring case, punctuation and apostrophes:
    STT and the model punctuate and capitalise the same words differently."""
    return re.findall(r"[a-z0-9]+", str(text or "").lower().replace("’", "").replace("'", ""))


def _quote_turn(session, current: ContactTurn, quote: str, *, current_only: bool) -> ContactTurn | None:
    """The caller turn the quote comes from (most recent first), or None.

    A detail is often given in one turn and confirmed in the next; the model
    then records it quoting where it was said. Test call f5dcac8e
    (2026-10-08): "zero three one two zero seven five zero four nine six"
    was quoted on the caller's "Yes." turn, and every save was refused because
    only the current turn counted. Provenance stays exact: the value's source
    is the turn that holds the words. Confirmation and withdrawal still need
    the current turn.
    """
    words = _quote_words(quote)
    if not words:
        return None
    turns = [current] if current_only else [current, *reversed(getattr(session, "_contact_turns", None) or ())]
    size = len(words)
    for candidate in turns:
        if not isinstance(candidate, ContactTurn):
            continue
        said = _quote_words(candidate.text)
        if any(said[i:i + size] == words for i in range(len(said) - size + 1)):
            return candidate
    return None


_SPOKEN_SEPARATORS = {".": ("dot", "point", "period", "full stop"), "_": ("underscore",),
                      "-": ("dash", "hyphen", "minus")}


def _separators_said(value: str, quote: str) -> bool:
    """Every . _ - in an email's name part was said by the caller there.

    Test call 43020665 (2026-10-08): the caller said "mike one two three at g
    mail dot com" and the agent read back "mike dot one two three". The words
    before "at" are the name part; the "dot" in "gmail dot com" says nothing
    about it. A literal symbol in the transcript counts as said.
    """
    local = value.split("@", 1)[0]
    needed = {char for char in _SPOKEN_SEPARATORS if char in local}
    if not needed:
        return True
    text = str(quote or "").lower()
    literal_local = text.split("@", 1)[0] if "@" in text else ""
    words = re.findall(r"[a-z0-9]+", text)
    cut = max((i for i, word in enumerate(words) if word == "at"), default=len(words))
    spoken_local = " ".join(words[:cut])
    return all(char in literal_local or any(re.search(rf"\b{name}\b", spoken_local) for name in _SPOKEN_SEPARATORS[char])
               for char in needed)


def _phone_region(session) -> str | None:
    """The campaign's region, else the country of the line this call is on."""
    from app.domain.services.phone_number_normalizer import region_of_e164

    region = getattr(session, "contact_phone_region", None)
    if region:
        return region
    return region_of_e164(getattr(getattr(session, "captured_slots", None), "line_phone", None))


def _normalise(kind, value, region):
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        return None
    try:
        if kind == "email":
            return validate_email(value.strip(), check_deliverability=False).normalized.lower()
        if kind == "phone":
            return normalize_phone_for_capture(value.strip(), region)
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            return None
        return value.strip()  # Identity spelling/case belongs to the caller/model, not a parser.
    except (EmailNotValidError, ValueError):
        return None


_CONFIRM_FROM_LATEST = ("They have spoken since giving this. If their latest words agree to this exact "
                        "value, confirm it now quoting those words; otherwise read it back.")
_SET_BEFORE_CONFIRM = ("Nothing is pending for this detail yet. Set it in this reply, quoting where they "
                       "said it; this confirmation then applies to that value.")


def _hold_confirm(session, turn, key, normalized) -> None:
    """Keep an early confirmation for the rest of this reply only."""
    held = getattr(session, "_held_contact_confirms", None)
    if not isinstance(held, dict) or held.get("turn") is not turn:
        held = {"turn": turn}
    held[key] = normalized
    session._held_contact_confirms = held


def _take_held_confirm(session, turn, key, normalized) -> bool:
    held = getattr(session, "_held_contact_confirms", None)
    return (normalized is not None and isinstance(held, dict) and held.get("turn") is turn
            and held.pop(key, None) == normalized)


def _result(status, *, session, field_key=None, capture=None, saved=False, next_step=None):
    contacts = {key: {"value": item.normalized_value, "validation_status": item.validation_status,
                      "caller_quote": item.raw_value}
                for kind in CAPTURE_FIELD_TYPES
                for key, item in contact_entries(getattr(session, "captured_slots", None), kind).items()
                if item is not None}
    result = {"action": CONTACT_TOOL_NAME, "success": saved, "saved": saved,
              "field_key": field_key, "contacts": contacts,
              "status": status, "value": getattr(capture, "normalized_value", None),
              "validation_status": getattr(capture, "validation_status", None),
              "caller_quote": getattr(capture, "raw_value", None),
              "confirmation_allowed": saved}
    if next_step:
        result["next_step"] = next_step
    return result


async def record_contact(session, arguments, *, turn=_UNSET, pool=None) -> dict:
    """Apply one model interpretation against a fixed caller and candidate snapshot."""
    turn = getattr(session, "_contact_turn", None) if turn is _UNSET else turn
    kind = arguments.get("kind") if isinstance(arguments, dict) else None
    field_key = arguments.get("field_key") if isinstance(arguments, dict) else None
    operation_name = arguments.get("operation") if isinstance(arguments, dict) else None
    quote_order = None

    def result(status, *, capture=None, saved=False, next_step=None):
        entries = contact_entries(getattr(session, "captured_slots", None), kind) if isinstance(kind, str) and kind in CAPTURE_FIELD_TYPES else {}
        key = field_key if isinstance(field_key, str) else next(reversed(entries), None)
        # One line per call so a refused or unsaved contact is never silent
        # again (2026-10-08: four calls lost every contact with no trace).
        logger.info("record_contact_result call=%s kind=%s operation=%s status=%s saved=%s quote_turn=%s",
                    str(getattr(session, "call_id", "?"))[:12], kind, operation_name, status, saved, quote_order)
        # A pending detail quoted from an earlier turn: the caller has spoken
        # since (often "Yes." to the read-back). The model judges whether those
        # words agree; replaying f5dcac8e, DeepSeek set both details and never
        # confirmed them.
        pending = getattr(capture, "status", None) is CaptureStatus.AWAITING_CONFIRMATION
        earlier = isinstance(turn, ContactTurn) and quote_order is not None and quote_order < turn.source.caller_turn_order
        if next_step is None and saved and pending and earlier and operation_name in ("set", "add"):
            next_step = _CONFIRM_FROM_LATEST
        return _result(status, session=session, field_key=key,
                       capture=capture if capture is not None else entries.get(key), saved=saved,
                       next_step=next_step)
    if not isinstance(turn, ContactTurn):
        return result("caller_evidence_unavailable")
    if not isinstance(arguments, dict) or set(arguments) not in ({"kind", "operation", "value", "expected_value", "source_quote"},
            {"kind", "operation", "value", "expected_value", "source_quote", "field_key"}):
        return result("invalid_arguments")
    kind, operation = arguments["kind"], arguments["operation"]
    quote, value, expected = arguments["source_quote"], arguments["value"], arguments["expected_value"]
    if (not isinstance(kind, str) or kind not in CAPTURE_FIELD_TYPES or operation not in ("set", "add", "confirm", "withdraw")
            or not isinstance(quote, str) or not 1 <= len(quote.strip()) <= 2000
            or (value is not None and (not isinstance(value, str) or len(value) > 320))
            or (expected is not None and not isinstance(expected, str))
            or (field_key is not None and (not isinstance(field_key, str) or not field_key))):
        return result("invalid_arguments")
    # Where the caller said it: set/add may quote an earlier caller turn (the
    # detail confirmed now was given before); confirm/withdraw are about now.
    quote_turn = _quote_turn(session, turn, quote, current_only=operation in ("confirm", "withdraw"))
    if quote_turn is None:
        return result("quote_not_found")
    quote_order = quote_turn.source.caller_turn_order
    if isinstance(expected, str) and expected.strip().lower() in ("", "null", "none"):
        # An empty or spelled-out null candidate is "absent", as the schema
        # says (replay of 08b2791d: DeepSeek sent the string "null").
        expected = None
    if (kind == "email" and operation in ("set", "add") and isinstance(value, str)
            and not _separators_said(value, quote_turn.text)):
        return result("separator_not_said")
    lock = getattr(session, "_contact_record_lock", None)
    if lock is None:
        lock = session._contact_record_lock = asyncio.Lock()
    async with lock:
        if getattr(session, "_contact_turn", None) is not turn:
            return result("stale_caller_turn")
        state = session.captured_slots
        entries = contact_entries(state, kind)
        current_key = next(reversed(entries))
        field_key = current_key if field_key is None else field_key
        if field_key not in entries:
            return result("contact_not_found")
        if operation == "add" and (kind not in ("email", "phone") or field_key != current_key):
            return result("additional_contact_not_ready")
        current = entries[field_key]
        region = _phone_region(session)
        normalized = _normalise(kind, value, region)
        # The candidate may be named in any formatting ("+92 312 0750496").
        named = None if expected is None else (_normalise(kind, expected, region) or expected)
        if current is None and operation == "set" and named is not None and named == normalized:
            # No candidate, so nothing can be stale: the model named the value
            # it is setting (replays of f5dcac8e and 43020665, 2026-10-09).
            named = None
        if current is None and operation == "confirm" and normalized is not None:
            # Agreement sent before its set in the same reply (replay of
            # 43020665): held for that set, which brings the value's evidence.
            _hold_confirm(session, turn, (kind, field_key), normalized)
            return result("confirm_deferred", next_step=_SET_BEFORE_CONFIRM)
        if named != getattr(current, "normalized_value", None):
            return result("contact_changed", capture=current)
        source = turn.source if operation in ("confirm", "withdraw") else quote_turn.source
        extra = {}
        if operation == "confirm":
            if (current is None or normalized is None or normalized != current.normalized_value
                    or current.status not in {CaptureStatus.AWAITING_CONFIRMATION, CaptureStatus.CONFIRMED}
                    or current.value_source is None
                    or source.caller_turn_order <= current.value_source.caller_turn_order):
                return result("confirmation_not_current", capture=current)
            capture = current if current.status is CaptureStatus.CONFIRMED else replace(
                current, status=CaptureStatus.CONFIRMED, confirmed_at=datetime.now(timezone.utc),
                confirmation_source=source, status_source=source, readback=None,
                confirmation_evidence="model_interpreted_caller_confirmation")
        elif operation == "withdraw":
            if value is not None or current is None:
                return result("invalid_arguments", capture=current)
            capture = replace(current, status=CaptureStatus.CANCELLED, normalized_value=None,
                raw_value=quote.strip(), confirmed_at=None, confirmation_source=None,
                confirmation_evidence=None, readback=None, status_source=source)
        else:
            if not isinstance(value, str) or not value.strip():
                return result("invalid_arguments", capture=current)
            if operation == "add":
                if (current is None or current.status is not CaptureStatus.CONFIRMED
                        or current.confirmation_source is None
                        or source.caller_turn_order <= current.confirmation_source.caller_turn_order
                        or normalized == current.normalized_value):
                    return result("additional_contact_not_ready", capture=current)
                extra[f"earlier_{kind}_captures"] = (*getattr(state, f"earlier_{kind}_captures"), current)
            capture = ContactCaptureState(kind=kind,
                status=CaptureStatus.AWAITING_CONFIRMATION if normalized else CaptureStatus.NEEDS_CLARIFICATION,
                raw_value=quote.strip(), normalized_value=normalized, from_caller=True,
                value_source=source, status_source=source)
            if operation == "set" and current is not None and normalized and normalized == current.normalized_value:
                capture = current  # Repeating a value does not erase prior confirmation.
            if (capture.status is CaptureStatus.AWAITING_CONFIRMATION and capture.value_source is not None
                    and turn.source.caller_turn_order > capture.value_source.caller_turn_order
                    and _take_held_confirm(session, turn, (kind, field_key), normalized)):
                capture = replace(capture, status=CaptureStatus.CONFIRMED, confirmed_at=datetime.now(timezone.utc),
                    confirmation_source=turn.source, status_source=turn.source, readback=None,
                    confirmation_evidence="model_interpreted_caller_confirmation")
        if field_key == current_key:
            extra.update({f"{kind}_capture": capture, kind: capture.normalized_value,
                          f"{kind}_confirmed": capture.status is CaptureStatus.CONFIRMED})
        else:
            earlier = list(getattr(state, f"earlier_{kind}_captures"))
            earlier[list(entries).index(field_key)] = capture
            extra[f"earlier_{kind}_captures"] = tuple(earlier)
        session.captured_slots = _with_contact_changes(state, **extra,
            active_contact_kind=None, agent_asked_kind=None)
        from app.domain.services.voice_pipeline.lead_slot_capture import (
            capture_turn_slots, contact_field_key, contact_save_acknowledged, contact_save_failed_fields,
        )
        if operation == "add":
            field_key = contact_field_key(session.captured_slots, kind)
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
            return result("stale_caller_turn")
        saved = contact_save_acknowledged(session, kind, field_key=field_key)
        failed = field_key in contact_save_failed_fields(session)
        if not saved and not failed and getattr(session, "_lead_capture_is_test", False) is True:
            # A browser test call never writes lead data (lead_slot_capture
            # rule 2). Tell the model it is recorded, as it would be on a real
            # call, so the test shows real behaviour instead of retries.
            return result("saved_test_call", capture=capture, saved=True)
        return result("saved" if saved else "save_failed" if failed else "not_saved",
                       capture=capture, saved=saved)
