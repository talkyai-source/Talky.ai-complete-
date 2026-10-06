"""Persist what a LIVE call established into ``call_lead_details`` (goals.md §7).

WHY THIS MODULE EXISTS
----------------------
``LeadCaptureService`` was written, tested and migrated, and then never called
from a call. Its only two callers were the manual edit form and itself, so
``call_lead_details`` held zero rows for every call ever placed — and the
interested-lead panel, its badge and its missing-required banner rendered an
empty state on every call, permanently. This module is the missing writer.

WHAT COUNTS AS AN ESTABLISHED FACT
----------------------------------
The shared record_contact tool stores the model's interpretation of caller
words with server-owned source/revision evidence. That attribution is not
proof that the interpretation is correct. Pending caller-owned email/phone
values are visible with confirmed=FALSE; later caller confirmation is separate.
Clarification, invalid and cancelled transitions retain NULL values and status
rather than disappearing. Current and earlier contacts use the same rules.
Other lead fields remain handled by post-call extraction.

WHAT MUST NOT HAPPEN
--------------------
1. **It must never raise into the call path.** A lead-form row is worth
   strictly less than the conversation, so every failure is logged and
   swallowed. ``capture_session_slots`` has no raising path.
2. **It must not fire for test calls.** ``campaign_test_ws`` inserts a real
   ``calls`` row flagged ``is_test``; those slots must not enter the tenant's
   lead data. The flag is read once per call and cached on the session.
3. **It must not re-issue the same INSERT every turn.** ``capture()`` is
   idempotent in SQL, but a 40-turn call would otherwise put 40 identical
   round trips on the latency-critical path. The last written
   ``(value, confirmed)`` per field is memoised on the session; only a change
   is written.
4. **It must name the tenant in SQL.** RLS context and an explicit ownership
   predicate are complementary controls — the ``is_test`` lookup carries its
   own ``tenant_id`` predicate.

WHERE IT IS CALLED FROM
-----------------------
``turn_ender.handle`` after each completed turn (the same place the incremental
transcript flush runs, and the point at which ``session.captured_slots`` has
just been updated by ``turn_runner``), and
``call_transcript_persister.save_call_transcript_on_hangup`` at teardown, so a
fact established on a turn that never completed — the caller says their email
and the line drops — is not lost.
"""
from __future__ import annotations

import logging
import json
from dataclasses import asdict
from typing import Any, Optional
from uuid import UUID

from app.services.scripts.call_state_tracker import contact_entries

logger = logging.getLogger(__name__)

# Contact records are attributed to caller words with source/revision evidence.
# record_contact uses model interpretation; attribution is not a semantic proof.
CAPTURE_SOURCE = "caller_stated"

# (CallState attribute, field_key, field_type, confirmed-flag attribute or None)
SLOT_FIELDS: tuple[tuple[str, str, str, Optional[str]], ...] = (
    ("email", "email", "email", "email_confirmed"),
    ("phone", "phone", "phone", "phone_confirmed"),
)
# follow_up / project_type / bidding_active were captured here until 2026-09-28;
# the owner narrowed live capture to what the caller gives as contact details.
# Add a row to widen it again — the table and the lead panel take any field.

# Capture states whose value is WRITTEN. pending_contact_revocations decides
# when an already-written row is withdrawn.
_KEPT_CAPTURE_STATES = ("confirmed", "awaiting_confirmation")

_WRITTEN_ATTR = "_lead_capture_written"
_IS_TEST_ATTR = "_lead_capture_is_test"
_BINDING_ATTR = "_lead_capture_binding"
_FAILED_ATTR = "_lead_capture_failed_fields"


def contact_save_failed_fields(session: Any) -> tuple[str, ...]:
    """Unacknowledged write failures, separate from caller confirmation."""
    return tuple(sorted(getattr(session, _FAILED_ATTR, ()) or ()))


def _save_failed(session: Any, keys, *, failed: bool = True) -> None:
    current = set(contact_save_failed_fields(session))
    if failed:
        current.update(keys)
    else:
        current.difference_update(keys)
    _stash(session, _FAILED_ATTR, current)


def resolve_call_binding(session: Any) -> dict:
    """The dialer's ids for this live voice session.

    DO NOT USE ``session.tenant_id`` AS THE TENANT. It looks authoritative and
    it is not: ``voice_orchestrator`` constructs ``CallSession`` without a
    tenant, and the ONLY thing that ever fills it in is the knowledge layer
    (``knowledge/session_inject.py``), which runs only when the campaign has a
    knowledge base AND the feature flag is on. On a campaign with
    ``knowledge_mode = 'none'`` — the default — it stays None for the whole
    call. Wiring the capture to it would be a guard fed by a signal that is
    constant in production, which is a mistake this repo has now made several
    times.

    The authoritative ids are the ones ``bind_telephony_call`` stamped on the
    telephony ``VoiceSession`` at answer time: ``_dialer_call_id`` /
    ``_dialer_tenant_id`` / ``_dialer_campaign_id`` / ``_dialer_lead_id``. The
    inbound path stamps the first two straight onto the ``CallSession`` too, so
    check there first and only then walk the telephony session map.

    Returns a dict with all four keys (any of them possibly None). Cached on
    the session once a call id is found — binding completes before the first
    turn, so one resolve per call is enough.
    """
    cached = getattr(session, _BINDING_ATTR, None)
    if isinstance(cached, dict):
        return cached

    binding = {
        "call_id": getattr(session, "_dialer_call_id", None),
        "tenant_id": getattr(session, "_dialer_tenant_id", None),
        "campaign_id": getattr(session, "_dialer_campaign_id", None),
        "lead_id": getattr(session, "_dialer_lead_id", None),
    }
    if not binding["call_id"]:
        try:
            from app.domain.services.telephony.lifecycle import _state

            for _pbx_channel, vs in _state().iter_voice_session_items():
                if getattr(vs, "call_session", None) is session:
                    binding = {
                        "call_id": getattr(vs, "_dialer_call_id", None),
                        "tenant_id": getattr(vs, "_dialer_tenant_id", None),
                        "campaign_id": getattr(vs, "_dialer_campaign_id", None),
                        "lead_id": getattr(vs, "_dialer_lead_id", None),
                    }
                    break
        except Exception:  # noqa: BLE001 - non-telephony session, or teardown
            pass

    # Last resort only: the session's own fields. campaign_id / lead_id are
    # reliably set there; tenant_id usually is not (see above).
    binding["tenant_id"] = binding["tenant_id"] or getattr(session, "tenant_id", None)
    binding["campaign_id"] = binding["campaign_id"] or getattr(
        session, "campaign_id", None
    )
    binding["lead_id"] = binding["lead_id"] or getattr(session, "lead_id", None)

    if binding["call_id"]:
        _stash(session, _BINDING_ATTR, binding)
    return binding


async def capture_turn_slots(session: Any, *, pool: Any, reason: str = "turn") -> int:
    """Per-turn entry point: resolve the call's binding, then capture.

    NEVER RAISES.
    """
    try:
        binding = resolve_call_binding(session)
    except Exception:  # noqa: BLE001
        logger.warning("lead_slot_capture_binding_failed", exc_info=True)
        return 0
    return await capture_session_slots(session, pool=pool, reason=reason, **binding)


def _as_uuid(value: Any) -> Optional[str]:
    """A UUID string, or None for anything that is not one.

    Campaign / lead ids on a session are not always real UUIDs (ask_ai uses the
    literal ``"ask-ai"``), and every id here lands in a ``::uuid`` cast. Drop
    what cannot be cast rather than letting one bad optional id fail the whole
    write.
    """
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def contact_field_key(captured_slots: Any, kind: str) -> str:
    """Where the CURRENT capture of ``kind`` is stored: ``email`` for the first,
    ``email_2``, ``email_3`` for each one the caller asked us to add after it."""
    earlier = getattr(captured_slots, f"earlier_{kind}_captures", ()) or ()
    return kind if not earlier else f"{kind}_{len(earlier) + 1}"


def contact_evidence(capture: Any) -> dict:
    return {
        "confirmation_evidence": getattr(capture, "confirmation_evidence", None),
        **{name: asdict(value) if (value := getattr(capture, name, None)) is not None else None
           for name in ("value_source", "confirmation_source", "status_source", "readback")},
    }


def _capture_row(capture: Any, field_type: str) -> dict | None:
    from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

    if (capture.validation_status in {"needs_clarification", "invalid", "cancelled"}
            and capture.from_caller and (capture.value_source is not None or capture.status_source is not None)):
        return {"value": None, "field_type": field_type, "confirmed": False,
                "raw_value": capture.raw_value, "normalized_value": None,
                "validation_status": capture.validation_status, "confirmed_at": None,
                "evidence": contact_evidence(capture)}
    if capture.validation_status not in _KEPT_CAPTURE_STATES or not capture.normalized_value:
        return None
    confirmed = capture.status is CaptureStatus.CONFIRMED
    if not confirmed and not capture.from_caller:
        return None
    return {"value": capture.normalized_value, "field_type": field_type,
            "confirmed": confirmed, "raw_value": capture.raw_value,
            "normalized_value": capture.normalized_value,
            "validation_status": capture.validation_status,
            "confirmed_at": capture.confirmed_at if confirmed else None,
            "evidence": contact_evidence(capture)}


def snapshot_slots(captured_slots: Any) -> dict[str, dict]:
    """Caller-owned contact snapshots keyed by stable lead field keys."""
    out: dict[str, dict] = {}
    if captured_slots is None:
        return out
    for kind in ("email", "phone"):
        for key, capture in contact_entries(captured_slots, kind).items():
            if capture is not None and (row := _capture_row(capture, kind)) is not None:
                out[key] = row
    for attr, field_key, field_type, confirmed_attr in SLOT_FIELDS:
        capture = (
            getattr(captured_slots, f"{field_key}_capture", None)
            if field_key in {"email", "phone"}
            else None
        )
        if field_key in {"email", "phone"}:
            field_key = contact_field_key(captured_slots, field_key)
        if capture is not None:
            continue  # Already projected above, including earlier entries.
        raw = getattr(captured_slots, attr, None)
        if raw is None:
            continue
        if isinstance(raw, bool):
            # A yes/no answer stored as str(True) reads as "True" in a CRM.
            value = "yes" if raw else "no"
        else:
            value = str(raw).strip()
            if not value:
                continue
        confirmed = (
            bool(getattr(captured_slots, confirmed_attr, False))
            if confirmed_attr
            else False
        )
        if confirmed_attr and not confirmed:
            # Historical scalar-only contacts lack caller source evidence;
            # retain their existing confirmed-only persistence behavior.
            continue
        out[field_key] = {
            "value": value,
            "field_type": field_type,
            "confirmed": confirmed,
            "raw_value": value if field_key in {"email", "phone"} else None,
            "normalized_value": value if field_key in {"email", "phone"} else None,
            "validation_status": (
                "confirmed" if field_key in {"email", "phone"} and confirmed else None
            ),
            "confirmed_at": None,
        }
    return out


def pending_contact_revocations(session: Any) -> dict[str, str]:
    """Caller-stated contacts written by this session that must stop showing.

    Two cases: the value was withdrawn or became unusable (clarification,
    invalid, cancelled), or a CONFIRMED value is being replaced by a pending
    correction — the upsert refuses to put an unconfirmed value over a
    confirmed one, so the old row is tombstoned first and the pending
    replacement is then written in its place.
    """
    written = getattr(session, _WRITTEN_ATTR, None)
    captured_slots = getattr(session, "captured_slots", None)
    if not isinstance(written, dict) or captured_slots is None:
        return {}

    from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

    revocations: dict[str, str] = {}
    capture_keys = set()
    for kind in ("email", "phone"):
        for field_key, capture in contact_entries(captured_slots, kind).items():
            capture_keys.add(field_key)
            previous = written.get(field_key)
            if previous is None or capture is None:
                continue
            previous_value = previous[0] if isinstance(previous, tuple) else None
            previous_confirmed = bool(previous[1]) if isinstance(previous, tuple) else True
            if (previous_value is None and not previous_confirmed and len(previous) > 4
                    and previous[4] == capture.validation_status):
                continue  # The same durable tombstone was already acknowledged.
            if capture.status in (CaptureStatus.CANCELLED, CaptureStatus.INVALID):
                withdrawn = True
            elif capture.status is CaptureStatus.NEEDS_CLARIFICATION:
                # A rejected read-back clears normalized_value ("no, that's wrong");
                # an exhausted unclear loop keeps it — the caller never disowned
                # the value they gave, so its unconfirmed row stays.
                withdrawn = (
                    previous_confirmed
                    or not capture.normalized_value
                    or capture.normalized_value != previous_value
                )
            else:
                withdrawn = previous_confirmed and capture.status is not CaptureStatus.CONFIRMED
            if withdrawn:
                revocations[field_key] = capture.validation_status
    for key, previous in written.items():
        if key not in capture_keys and isinstance(previous, tuple) and previous[0] is not None:
            # A corrected current "additional contact" item can retract its
            # extra slot. Only rows acknowledged by this session are owned.
            if key.startswith(("email_", "phone_")) and key.rsplit("_", 1)[-1].isdigit():
                revocations[key] = "needs_clarification"
    return revocations


def _stash(session: Any, name: str, value: Any) -> None:
    """Best-effort bounded memo. SQL fences retries; missing prior ownership
    may require review, never permission to overwrite an independent row."""
    try:
        setattr(session, name, value)
    except Exception:  # noqa: BLE001 - a memo must never break a call
        pass


def _fingerprint(item: dict) -> tuple:
    return (item["value"], item["confirmed"], item.get("raw_value"),
            item.get("normalized_value"), item.get("validation_status"),
            item.get("confirmed_at"), json.dumps(item.get("evidence") or {}, sort_keys=True))


def contact_save_acknowledged(session: Any, kind: str, *, field_key: str | None = None) -> bool:
    """Whether the exact selected contact snapshot has an acknowledged write."""
    slots = getattr(session, "captured_slots", None)
    key = contact_field_key(slots, kind) if field_key is None else field_key
    if key not in contact_entries(slots, kind):
        return False
    item = snapshot_slots(slots).get(key)
    written = getattr(session, _WRITTEN_ATTR, None)
    return bool(item is not None and isinstance(written, dict)
                and written.get(key) == _fingerprint(item))


async def _call_is_test(pool: Any, tenant_id: str, call_id: str) -> Optional[bool]:
    """``calls.is_test`` for this call, or None when no such row exists.

    EXPLICIT TENANT PREDICATE: RLS context is deliberately not the only
    isolation boundary here.
    """
    from app.core.db_utils import acquire_with_tenant

    async with acquire_with_tenant(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT is_test
              FROM calls
             WHERE id = $1::uuid
               AND tenant_id = $2::uuid
            """,
            call_id,
            tenant_id,
        )
    if row is None:
        return None
    return bool(row["is_test"])


async def capture_session_slots(
    session: Any,
    *,
    pool: Any,
    call_id: Optional[str],
    tenant_id: Optional[str],
    campaign_id: Optional[str] = None,
    lead_id: Optional[str] = None,
    reason: str = "turn",
) -> int:
    """Persist newly-established facts for this call. Returns rows written.

    NEVER RAISES. ``call_id`` is the dialer's real ``calls.id`` (not the
    voice-session UUID); pass None for a session that has no ``calls`` row and
    nothing is written.
    """
    try:
        return await _capture(
            session,
            pool=pool,
            call_id=call_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - see module docstring, rule 1
        _save_failed(session, set(snapshot_slots(getattr(session, "captured_slots", None)))
                     | set(pending_contact_revocations(session)))
        logger.warning(
            "lead_slot_capture_failed call=%s reason=%s",
            str(call_id or "?")[:8],
            reason,
            exc_info=True,
        )
        return 0


async def _capture(
    session: Any,
    *,
    pool: Any,
    call_id: Optional[str],
    tenant_id: Optional[str],
    campaign_id: Optional[str],
    lead_id: Optional[str],
    reason: str,
) -> int:
    target_call_id = _as_uuid(call_id)
    tenant = _as_uuid(tenant_id)
    if pool is None or not target_call_id or not tenant:
        return 0

    pending = snapshot_slots(getattr(session, "captured_slots", None))
    revocations = pending_contact_revocations(session)
    if not pending and not revocations:
        # The common case. Checked BEFORE any database work so a call that
        # establishes nothing never touches the pool at all.
        return 0

    written = getattr(session, _WRITTEN_ATTR, None)
    if not isinstance(written, dict):
        written = {}

    def expected_contact(previous) -> dict:
        if previous is None:
            return {"absent": True}
        return {"value": previous[0],
                "evidence": json.loads(previous[6]) if len(previous) > 6 else {}}

    changed = {
        key: item
        for key, item in pending.items()
        if written.get(key) != _fingerprint(item)
    }
    if not changed and not revocations:
        return 0

    is_test = getattr(session, _IS_TEST_ATTR, None)
    if is_test is None:
        is_test = await _call_is_test(pool, tenant, target_call_id)
        if is_test is None:
            # No calls row under this tenant — a browser / ask_ai session, or a
            # binding that never resolved. call_lead_details.call_id is a FK to
            # calls(id), so there is nothing to attach to.
            logger.debug(
                "lead_slot_capture_no_calls_row call=%s tenant=%s",
                target_call_id[:8],
                tenant[:8],
            )
            return 0
        _stash(session, _IS_TEST_ATTR, is_test)
    if is_test:
        logger.debug(
            "lead_slot_capture_skipped_test_call call=%s fields=%s",
            target_call_id[:8],
            sorted(set(changed) | set(revocations)),
        )
        _stash(session, _WRITTEN_ATTR, written)
        return 0

    from app.domain.services.lead_capture_service import (
        InvalidCaptureError,
        LeadCaptureService,
    )

    service = LeadCaptureService(pool)
    campaign = _as_uuid(campaign_id)
    lead = _as_uuid(lead_id)
    count = 0
    blocked = set()
    for field_key, validation_status in revocations.items():
        expected = expected_contact(written.get(field_key))
        cause = pending.get(field_key, {}).get("evidence", {}).get("status_source")
        revision = getattr(session, "_lead_capture_revision_source", None)
        if cause is None and revision is not None and any(
            isinstance(owner := expected.get("evidence", {}).get(name), dict)
            and owner.get("provider_item_id") == revision.provider_item_id
            for name in ("value_source", "confirmation_source", "status_source")
        ):
            cause = asdict(revision)
        try:
            revoked = await service.revoke_caller_contact(
                tenant_id=tenant,
                call_id=target_call_id,
                field_key=field_key,
                validation_status=validation_status,
                expected_contact=expected,
                revocation_source=cause,
            )
        except Exception as exc:  # noqa: BLE001 - transient; retry next turn
            logger.warning(
                "lead_slot_capture_revoke_failed call=%s field=%s err=%s",
                target_call_id[:8],
                field_key,
                exc,
            )
            blocked.add(field_key)
            _save_failed(session, (field_key,))
            continue
        if not revoked:
            # A newer/manual writer now owns the row. Never clear it or turn
            # a rejected pending replacement into a successful local memo.
            blocked.add(field_key)
            _save_failed(session, (field_key,), failed=False)
            continue
        revoked_evidence = {**expected["evidence"], "status": "revoked"}
        if cause is not None:
            revoked_evidence["status_source"] = cause
        written[field_key] = _fingerprint({"value": None, "confirmed": False,
            "validation_status": validation_status, "evidence": revoked_evidence})
        if revoked:
            _save_failed(session, (field_key,), failed=False)
            count += 1

    for field_key, item in changed.items():
        if field_key in blocked:
            continue
        try:
            stored = await service.capture(
                tenant_id=tenant,
                call_id=target_call_id,
                field_key=field_key,
                value=item["value"],
                source=CAPTURE_SOURCE,
                field_type=item["field_type"],
                confirmed=item["confirmed"],
                raw_value=item.get("raw_value"),
                normalized_value=item.get("normalized_value"),
                validation_status=item.get("validation_status"),
                confirmed_at=item.get("confirmed_at"),
                evidence=item.get("evidence"),
                expected_contact=expected_contact(written.get(field_key)),
                campaign_id=campaign,
                lead_id=lead,
            )
        except InvalidCaptureError as exc:
            # Keep an explicit failure, never a successful-write fingerprint.
            logger.warning(
                "lead_slot_capture_rejected call=%s field=%s - %s",
                target_call_id[:8],
                field_key,
                exc,
            )
            _save_failed(session, (field_key,))
            continue
        except Exception as exc:  # noqa: BLE001 - transient; retry next turn
            logger.warning(
                "lead_slot_capture_write_failed call=%s field=%s err=%s",
                target_call_id[:8],
                field_key,
                exc,
            )
            _save_failed(session, (field_key,))
            continue
        _save_failed(session, (field_key,), failed=False)
        if stored:
            written[field_key] = _fingerprint(item)
            count += 1

    _stash(session, _WRITTEN_ATTR, written)
    if count:
        logger.info(
            "lead_slot_capture call=%s reason=%s fields=%s written=%d",
            target_call_id[:8],
            reason,
            sorted(set(changed) | set(revocations)),
            count,
        )
    return count


# ── End-of-call contact outcome (2026-09-30) ────────────────────────────────
#
# Two jobs at hang-up:
#   1. one log line per call saying how contact capture went, so success rate
#      and turns-to-confirm can be measured from production instead of guessed;
#   2. when the caller tried to give an email or number and it never got
#      confirmed, leave a visible note on the lead ("confirm it with them")
#      instead of silently having nothing -- the agent tells the caller the
#      team will confirm it, and this is what makes that true.

FOLLOWUP_FIELD = "contact_followup"


def contact_outcome(captured_slots: Any) -> dict:
    """Per field: "confirmed", "unconfirmed" (tried, not settled) or "none"."""
    from app.domain.services.voice_pipeline.contact_capture import CaptureStatus

    out: dict[str, Any] = {}
    for field in ("email", "phone"):
        capture = getattr(captured_slots, f"{field}_capture", None)
        confirmed = bool(getattr(captured_slots, f"{field}_confirmed", False)) or (
            capture is not None and capture.status is CaptureStatus.CONFIRMED
        )
        earlier = getattr(captured_slots, f"earlier_{field}_captures", ())
        if not confirmed:
            confirmed = any(item.status is CaptureStatus.CONFIRMED and item.normalized_value
                            for item in earlier)
        if confirmed:
            status = "confirmed"
        elif any(item is not None and item.status is not CaptureStatus.CANCELLED
                 for item in (*earlier, capture)):
            status = "unconfirmed"
        elif capture is not None and capture.attempts:
            status = "unconfirmed"  # gave up after repeated tries, not a "never mind"
        else:
            status = "none"
        out[field] = status
        out[f"{field}_attempts"] = int(getattr(capture, "attempts", 0) or 0)
    out["line_phone_known"] = bool(getattr(captured_slots, "line_phone", None))
    return out


async def record_contact_outcome(
    session: Any,
    *,
    pool: Any,
    call_id: Optional[str],
    tenant_id: Optional[str],
    campaign_id: Optional[str] = None,
    lead_id: Optional[str] = None,
) -> None:
    """Log the call's contact outcome and flag unconfirmed contacts. Never raises."""
    try:
        slots = getattr(session, "captured_slots", None)
        outcome = contact_outcome(slots)
        target_call_id, tenant = _as_uuid(call_id), _as_uuid(tenant_id)
        logger.info(
            "contact_capture_outcome call=%s email=%s email_attempts=%d phone=%s "
            "phone_attempts=%d line_phone_known=%s",
            (target_call_id or "-")[:8], outcome["email"], outcome["email_attempts"],
            outcome["phone"], outcome["phone_attempts"], outcome["line_phone_known"],
        )
        pending = [f for f in ("email", "phone") if outcome[f] == "unconfirmed"]
        failed_fields = contact_save_failed_fields(session)
        prior_failure_notice = bool(getattr(session, "_lead_capture_failure_notice", False))
        if (not pending and not failed_fields and not prior_failure_notice) or pool is None or not target_call_id or not tenant:
            return
        is_test = getattr(session, _IS_TEST_ATTR, None)
        if is_test is None:
            is_test = await _call_is_test(pool, tenant, target_call_id)
        if is_test or is_test is None:
            return
        from app.domain.services.lead_capture_service import LeadCaptureService

        names = " and ".join("email address" if f == "email" else "phone number" for f in pending)
        if failed_fields:
            value = "Contact evidence could not be saved for: " + ", ".join(failed_fields) + ". Review the call before using these details."
            evidence = {"status": "contact_save_failed", "failed_fields": list(failed_fields)}
        else:
            value = f"The caller's {names} could not be confirmed on the call. Please confirm it with them." if pending else None
            evidence = {"status": "confirmation_pending" if pending else "contact_save_recovered"}
        stored = await LeadCaptureService(pool).capture(
            tenant_id=tenant,
            call_id=target_call_id,
            field_key=FOLLOWUP_FIELD,
            value=value,
            source="agent_inferred",
            field_type="notes",
            confirmed=False,
            campaign_id=_as_uuid(campaign_id),
            lead_id=_as_uuid(lead_id),
            evidence=evidence,
        )
        if stored:
            _stash(session, "_lead_capture_failure_notice", bool(failed_fields))
    except Exception as exc:  # noqa: BLE001 - teardown must never break
        logger.warning("contact_capture_outcome_failed call=%s err=%s", str(call_id)[:8], exc)
