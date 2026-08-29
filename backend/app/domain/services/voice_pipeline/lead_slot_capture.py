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
``CallState`` (app.services.scripts.call_state_tracker) is the per-call sticky
slot store the turn loop already maintains. Every one of its slots is derived
by parsing THE CALLER'S OWN WORDS — a spoken email, a spoken number, a spoken
day, a yes/no on an open question. Nothing in it is a model inference. So the
provenance for everything written here is ``caller_stated``, never
``agent_inferred``; writing a guess under a caller's name is precisely what §7
forbids.

``email`` and ``phone`` additionally carry the read-back confirmation flag from
the confirm-before-commit gate. An unconfirmed value is still captured (it is
real information) but is stored ``confirmed = FALSE`` so the panel can show it
as not yet settled.

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
4. **It must name the tenant in SQL.** Prod's app role is superuser +
   BYPASSRLS, so an RLS policy is decorative — the ``is_test`` lookup carries
   its own ``tenant_id`` predicate.

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
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Everything CallState knows, it learned by parsing the caller's own utterance.
# None of it is a model inference, so none of it may be stored as one.
CAPTURE_SOURCE = "caller_stated"

# (CallState attribute, field_key, field_type, confirmed-flag attribute or None)
SLOT_FIELDS: tuple[tuple[str, str, str, Optional[str]], ...] = (
    ("email", "email", "email", "email_confirmed"),
    ("phone", "phone", "phone", "phone_confirmed"),
    ("follow_up", "follow_up", "text", None),
    ("project_type", "project_type", "text", None),
    ("bidding_active", "bidding_active", "single_select", None),
)

_WRITTEN_ATTR = "_lead_capture_written"
_IS_TEST_ATTR = "_lead_capture_is_test"
_BINDING_ATTR = "_lead_capture_binding"


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


def snapshot_slots(captured_slots: Any) -> dict[str, dict]:
    """The facts this call has established, keyed by ``field_key``.

    Empty for most turns — a caller who has not yet given an email, a number, a
    day or a yes/no has established nothing, and §7's "unknown" is represented
    by the ABSENCE of a row, not by writing the string.
    """
    out: dict[str, dict] = {}
    if captured_slots is None:
        return out
    for attr, field_key, field_type, confirmed_attr in SLOT_FIELDS:
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
        out[field_key] = {
            "value": value,
            "field_type": field_type,
            "confirmed": confirmed,
        }
    return out


def _stash(session: Any, name: str, value: Any) -> None:
    """Best-effort memo on the session. Losing it costs redundant writes, not
    correctness — ``capture()`` is idempotent in SQL either way."""
    try:
        setattr(session, name, value)
    except Exception:  # noqa: BLE001 - a memo must never break a call
        pass


async def _call_is_test(pool: Any, tenant_id: str, call_id: str) -> Optional[bool]:
    """``calls.is_test`` for this call, or None when no such row exists.

    EXPLICIT TENANT PREDICATE: prod's app role is superuser + BYPASSRLS, so the
    table's policy is inert and the statement must scope itself.
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
    if not pending:
        # The common case. Checked BEFORE any database work so a call that
        # establishes nothing never touches the pool at all.
        return 0

    written = getattr(session, _WRITTEN_ATTR, None)
    if not isinstance(written, dict):
        written = {}
    changed = {
        key: item
        for key, item in pending.items()
        if written.get(key) != (item["value"], item["confirmed"])
    }
    if not changed:
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
            sorted(changed),
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
    for field_key, item in changed.items():
        try:
            stored = await service.capture(
                tenant_id=tenant,
                call_id=target_call_id,
                field_key=field_key,
                value=item["value"],
                source=CAPTURE_SOURCE,
                field_type=item["field_type"],
                confirmed=item["confirmed"],
                campaign_id=campaign,
                lead_id=lead,
            )
        except InvalidCaptureError as exc:
            # Permanently unwritable (e.g. an overlong note). Memoise it so the
            # next 39 turns do not retry it, and keep the other fields.
            logger.warning(
                "lead_slot_capture_rejected call=%s field=%s - %s",
                target_call_id[:8],
                field_key,
                exc,
            )
            written[field_key] = (item["value"], item["confirmed"])
            continue
        except Exception as exc:  # noqa: BLE001 - transient; retry next turn
            logger.warning(
                "lead_slot_capture_write_failed call=%s field=%s err=%s",
                target_call_id[:8],
                field_key,
                exc,
            )
            continue
        written[field_key] = (item["value"], item["confirmed"])
        if stored:
            count += 1

    _stash(session, _WRITTEN_ATTR, written)
    if count:
        logger.info(
            "lead_slot_capture call=%s reason=%s fields=%s written=%d",
            target_call_id[:8],
            reason,
            sorted(changed),
            count,
        )
    return count
