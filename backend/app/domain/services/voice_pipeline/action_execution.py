"""Execute campaign-authorized voice actions through existing business services.

Model arguments never choose a tenant, campaign, email body or transfer number.
The server proposes exact parameters; a later caller confirmation is bound to
those parameters and the contact revision immediately before the durable claim.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.db_utils import acquire_with_tenant
from app.services.action_execution import DurableActionExecutor, public_action_receipt


def _object(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _result(action, status, message, **extra):
    return {"version": 1, "action": action, "success": False, "status": status,
            "confirmation_allowed": False, "message": message, **extra}


_STILL = " You are still with the caller."
_NEXT = " Offer to take a message or arrange a callback if that is available; do not promise another attempt."
_NOT_CONFIRMED = ("The transfer did not complete and its state is not confirmed. Do not say the caller is "
                  "connected, and do not try again or offer another transfer.")
_UNREACHABLE = frozenset({"chanunavail", "congestion", "cancel", "dontcall", "torture", "target_unavailable",
                          "transfer_cancelled"})
_NO_LINE = frozenset({"outbound_trunk_unavailable", "invalid_trunk_endpoint", "invalid_caller_id",
                      "inbound_trunk_unavailable", "inbound_trunk_identity_unavailable"})
_END_NEXT = " Tell the caller, then end the call politely; do not promise another attempt."
# Refused before any phone-system action (adapter.transfer returns these before
# /channels/create; the bridge policy refuses the last two before calling it):
# nothing was dialled, so the caller is certainly still with the agent.
_PRE_DIAL = frozenset({"outbound_trunk_unavailable", "invalid_trunk_endpoint", "invalid_caller_id",
                       "inbound_trunk_unavailable", "inbound_trunk_identity_unavailable",
                       "invalid_transfer_destination", "transfer_destination_not_allowed", "transfer_closed"})
_NOT_ALLOWED = frozenset({"transfer_destination_not_allowed", "invalid_transfer_destination"})


def _transfer_outcome(receipt):
    """(status, success, confirmation_allowed, message): only "completed" is connected."""
    status = str(receipt.get("status") or "").strip().lower()
    error = str(receipt.get("error") or "").strip().lower()
    if status == "completed":
        return ("connected", True, True,
                "The person answered and the call has been handed over to them. You are no longer on the "
                "call, so say nothing more to the caller.")
    if status == "success":
        return "provider_accepted", True, False, "The PBX accepted the transfer request; connection is unconfirmed."
    if status in {"cleanup_pending", "unconfirmed", "termination_unconfirmed", "in_progress"} or (
            status == "failed" and error == "target_termination_unconfirmed"):
        return "unknown", False, False, _NOT_CONFIRMED
    if status == "failed":
        retained = receipt.get("caller_media_retained") is True or error in _PRE_DIAL
        if not retained or receipt.get("target_termination_confirmed") is False \
                or error == "transfer_already_in_progress":
            return "unknown", False, False, _NOT_CONFIRMED
        if error in {"no_answer", "noanswer"}:
            reason = "The person did not answer."
        elif error == "busy":
            reason = "The person's line was busy."
        elif error in _UNREACHABLE:
            reason = "The person could not be reached."
        elif error == "transfer_closed":
            reason = "The team is outside its hours for transfers."
        elif error in _NO_LINE:
            reason = "No line is available for transfers right now."
        elif error in _NOT_ALLOWED:
            reason = "A transfer is not available for this call."
        else:
            return "failed", False, False, "Transfer has not been confirmed."
        next_step = _END_NEXT if receipt.get("fallback_action") == "end_call" else _NEXT
        return "failed", False, False, reason + _STILL + next_step
    return "unknown", False, False, "Transfer has not been confirmed."


def enabled_voice_actions(session, now=None):
    capabilities = dict(getattr(session, "_voice_action_capabilities", {}))
    if "transfer_call" in capabilities:
        # Checked on every call of this function (each turn), not once at call
        # start, so a call that crosses closing time stops offering transfer.
        from app.domain.services.campaign_brief import transfer_open_now
        brief = (getattr(session, "_voice_action_context", None) or {}).get("brief") or {}
        if not transfer_open_now(brief, now):
            capabilities.pop("transfer_call")
    return {"end_call", *capabilities.keys()}


def _brief_destinations(brief):
    listed = [dict(item) for item in brief.get("transfer_destinations") or [] if isinstance(item, dict)]
    if not listed:
        legacy = str(brief.get("transfer_destination") or "").strip()
        listed = [{"name": "", "number": legacy, "when": ""}] if legacy else []
    return listed


def offered_transfer_destinations(context):
    """Named destinations the agent may use on this call: [{name, number, when}].

    Numbers always come from the stored brief. A legacy single destination is one
    unnamed entry. Inbound calls keep only destinations that passed the
    inbound allow-list check."""
    listed = _brief_destinations((context or {}).get("brief") or {})
    if (context.get("call_direction", context.get("direction")) == "inbound"
            and "inbound_transfer_numbers" in context):
        allowed = context["inbound_transfer_numbers"]
        listed = [item for item in listed if item["number"] in allowed]
    return listed


async def _pool(session):
    pool = getattr(session, "_voice_action_pool", None)
    if pool is None:
        from app.core.container import get_container
        pool = get_container().db_pool
    return pool


def _inbound_transfer_policy_available(context, tenant, destination=None):
    """Read-only capability check; final admission still owns limits and effects."""
    from app.domain.services.telephony.inbound_transfer import (
        inbound_transfer_destination_approved,
        inbound_transfer_scope_available,
        normalize_did,
    )
    try:
        snapshot = _object(context.get("route_snapshot"))
        route = _object(snapshot.get("route"))
        policy = _object(_object(snapshot.get("inbound_config")).get("transfer_policy"))
        destination = normalize_did(destination or context["brief"].get("transfer_destination"))
        return bool(
            inbound_transfer_scope_available(tenant_id=tenant, config_id=route.get("config_id"))
            and context.get("admission_status") == "allowed"
            and context.get("processing_status") == "active"
            and context.get("billing_status") == "reserved"
            and int(context.get("reserved_seconds") or 0) > 0
            and policy.get("enabled") is True
            and destination
            and destination != normalize_did(route.get("called_did"))
            and inbound_transfer_destination_approved(policy, destination)
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _call_ids(session):
    """(tenant, campaign, call) for this live call, or Nones until bound.

    session.tenant_id / session.call_id are NOT the dialer's ids on a cascaded
    call (see lead_slot_capture.resolve_call_binding), so read the binding and
    fall back to the session fields only for sessions that carry the real ids
    directly (the realtime path)."""
    from app.domain.services.voice_pipeline.lead_slot_capture import resolve_call_binding

    binding = resolve_call_binding(session)
    return (
        binding.get("tenant_id") or getattr(session, "tenant_id", None),
        binding.get("campaign_id") or getattr(session, "campaign_id", None),
        binding.get("call_id") or getattr(session, "call_id", None),
    )


async def _context(session, pool):
    tenant, campaign, call = _call_ids(session)
    if not tenant or not campaign or not call:
        return None
    async with acquire_with_tenant(pool, str(tenant)) as conn:
        row = await conn.fetchrow("""
            SELECT c.id AS call_id, COALESCE(c.provider_call_id,c.external_call_uuid) AS provider_call_id, c.lead_id, c.provider,
                   c.status AS call_status, p.id AS campaign_id, p.status AS campaign_status,
                   p.direction, p.script_config, l.phone_number, l.do_not_call,
                   l.status AS lead_status, c.direction AS call_direction, c.route_snapshot,
                   c.admission_status, c.processing_status, c.billing_status, c.reserved_seconds
            FROM calls c JOIN campaigns p ON p.id=c.campaign_id AND p.tenant_id=c.tenant_id
            LEFT JOIN leads l ON l.id=c.lead_id AND l.tenant_id=c.tenant_id AND l.campaign_id=p.id
            WHERE c.tenant_id=$1::uuid AND p.id=$2::uuid
              AND (c.id::text=$3 OR c.provider_call_id=$3 OR c.external_call_uuid=$3 OR c.talklee_call_id=$3)
            ORDER BY c.created_at DESC LIMIT 1
        """, str(tenant), str(campaign), str(call))
        if not row:
            return None
        context = dict(row)
        context["brief"] = _object(_object(context["script_config"]).get("campaign_brief"))
        context["email_connected"] = bool(await conn.fetchval("""
            SELECT EXISTS(SELECT 1 FROM connectors c JOIN connector_accounts a ON a.connector_id=c.id
                WHERE c.tenant_id=$1::uuid AND a.tenant_id=$1::uuid AND c.type='email'
                  AND c.status='active' AND a.status='active')
        """, str(tenant)))
        context["inbound_transfer_available"] = False
        context["inbound_transfer_numbers"] = set()
        allowed = set(context["brief"].get("approved_next_actions") or [])
        if "transfer" in allowed and context.get("call_direction") == "inbound":
            # The allow-list check runs for EACH destination.
            context["inbound_transfer_numbers"] = {
                item["number"] for item in _brief_destinations(context["brief"])
                if _inbound_transfer_policy_available(context, str(tenant), item["number"])
            }
        if context["inbound_transfer_numbers"]:
            context["inbound_transfer_available"] = await conn.fetchval(
                "SELECT inbound_transfer_enabled FROM platform_runtime_controls WHERE id=1"
            ) is True
    context["callback_worker_ready"] = False
    if "schedule_callback" in allowed:
        try:
            from app.core.container import get_container
            redis = get_container().redis
            async with asyncio.timeout(.5):
                context["callback_worker_ready"] = bool(await redis.get("reminder:heartbeat_ts"))
        except Exception:
            pass
    context["transfer_connected"] = False
    if "transfer" in allowed:
        try:
            from app.domain.services.telephony.adapter_registry import get_adapter
            adapter = get_adapter()
            context["transfer_connected"] = bool(adapter and adapter.connected)
        except Exception:
            pass
    return context


def _capabilities(context, now=None):
    if not context:
        return {}
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES
    if context.get("call_status") in {*TERMINAL_CALL_STATUSES, "termination_pending"}:
        return {}
    brief = context["brief"]
    allowed = set(brief.get("approved_next_actions") or [])
    out = {}
    if "send_email" in allowed and brief.get("email_action") and context["email_connected"]:
        out["send_email"] = "Configured email through the tenant's connected provider."
    if "submit_form" in allowed and brief.get("form_action") and context["email_connected"]:
        out["submit_form"] = "Configured form submitted to its approved email inbox."
    if ("schedule_callback" in allowed and context.get("direction") == "outbound"
            and context.get("callback_worker_ready")
            and context.get("campaign_status") in {"running", "active"}
            and context.get("lead_id") and context.get("phone_number")
            and not context.get("do_not_call") and context.get("lead_status") != "deleted"):
        out["schedule_callback"] = "Schedule the confirmed number and exact time in the existing dialer."
    from app.domain.services.campaign_brief import transfer_open_now
    if ("transfer" in allowed and offered_transfer_destinations(context)
            and transfer_open_now(brief, now) and context.get("transfer_connected")
            and context.get("provider") in {"asterisk", "freeswitch"}
            and (context.get("call_direction", context.get("direction")) != "inbound"
                 or context.get("inbound_transfer_available") is True)):
        out["transfer_call"] = "Approved destination only; live telephony policy also applies."
    return out


async def prepare_voice_action_context(session, *, refresh=False):
    if getattr(session, "_voice_action_context_loaded", False) and not refresh:
        return enabled_voice_actions(session)
    try:
        pool = await _pool(session)
        context = await _context(session, pool)
        session._voice_action_context = context
        session._voice_action_capabilities = _capabilities(context)
    except Exception:
        session._voice_action_context = None
        session._voice_action_capabilities = {}
    # A lookup before the call is bound has nothing to look up. Do not remember
    # that as "this call has no actions"; try again on a later turn.
    if all(_call_ids(session)):
        session._voice_action_context_loaded = True
    return enabled_voice_actions(session)


def _contact(session, kind):
    state = getattr(session, "captured_slots", None)
    capture = getattr(state, f"{kind}_capture", None)
    status = getattr(getattr(capture, "status", None), "value", None)
    if (status != "confirmed" or not capture.from_caller or not capture.normalized_value
            or not capture.confirmed_at):
        raise ValueError(f"Confirm the caller's {kind} before requesting this action.")
    return capture.normalized_value, str(capture.confirmed_at)


def _callback_time(arguments):
    # No guessing what 'Thursday' means. A model-proposed exact local datetime
    # is only a proposal until the caller confirms the server's full readback.
    raw = str(arguments.get("requested_time") or "")
    zone = str(arguments.get("timezone") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        tz = ZoneInfo(zone)
    except (ValueError, KeyError):
        raise ValueError("Ask for a specific date, time and IANA timezone before scheduling.") from None
    # The spoken confirmation names a city and clock time, not a DST fold.
    # Even a model-supplied offset cannot establish which repeated clock time
    # the caller meant. Ask for an unambiguous time rather than guessing.
    wall = parsed.replace(tzinfo=None)
    if wall.replace(tzinfo=tz, fold=0).utcoffset() != wall.replace(tzinfo=tz, fold=1).utcoffset():
        raise ValueError("That local time is ambiguous or nonexistent; choose an unambiguous callback time.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    if parsed.astimezone(tz).utcoffset() != parsed.utcoffset():
        raise ValueError("The supplied time offset does not match the requested timezone.")
    if not datetime.now(timezone.utc) + timedelta(minutes=1) < parsed < datetime.now(timezone.utc) + timedelta(days=90):
        raise ValueError("Choose a callback between one minute and ninety days from now.")
    return parsed.astimezone(timezone.utc).isoformat(), zone, parsed.astimezone(tz).isoformat()


def _parameters(session, context, action, arguments):
    brief = context["brief"]
    if action == "send_email":
        recipient, revision = _contact(session, "email")
        if arguments.get("recipient") and str(arguments["recipient"]).casefold() != recipient.casefold():
            raise ValueError("The requested recipient differs from the currently confirmed address.")
        content = brief["email_action"]
        return {"recipient": recipient, "contact_revision": revision, **content}, f"send the email titled {content['subject']} to {recipient}"
    if action == "schedule_callback":
        phone, revision = _contact(session, "phone")
        # The existing dialer must not route a new number through another
        # lead's identity. A correction is saved for review, not auto-reassigned.
        if phone != context["phone_number"]:
            raise ValueError("The confirmed number differs from this campaign contact; update that contact before scheduling.")
        when, zone, local = _callback_time(arguments)
        local_text = datetime.fromisoformat(local).strftime("%d %B %Y at %I:%M %p")
        return {"phone": phone, "contact_revision": revision, "scheduled_at": when, "timezone": zone}, f"schedule a callback to {phone} on {local_text} in {zone}"
    if action == "transfer_call":
        offered = offered_transfer_destinations(context)
        requested = str(arguments.get("destination") or "").strip().casefold()
        if len(offered) == 1 and offered[0]["name"]:
            # One named destination: omitted, empty or its name all mean it.
            chosen = offered[0] if not requested or requested == offered[0]["name"].casefold() else None
        elif len(offered) == 1:
            # Legacy free-text destination keeps its exact-match rule.
            chosen = offered[0] if not requested or requested == offered[0]["number"].casefold() else None
        else:
            chosen = next((item for item in offered if item["name"].casefold() == requested), None)
        if chosen is None:
            # Name the choices so the model can correct itself. A destination
            # with no name is a stored number or reference: never echo it back.
            names = ", ".join(item["name"] for item in offered if item["name"])
            raise ValueError(
                "Choose one of the configured transfer destinations: " + names + "."
                if names else "Only the campaign's configured transfer destination is available.")
        # The dialled value always comes from the brief, never from the model.
        shown = chosen["name"] or chosen["number"]
        return {"destination": chosen["number"]}, f"transfer this call to {shown}"
    if action == "submit_form":
        form = brief["form_action"]
        if arguments.get("form_name") and arguments["form_name"] != form["name"]:
            raise ValueError("Only the configured form is available.")
        state = getattr(session, "captured_slots", None)
        fields, revisions = {}, {}
        for name in form["fields"]:
            if name in {"email", "phone"}:
                fields[name], revisions[name] = _contact(session, name)
            else:
                value = getattr(state, name, None)
                if value is None:
                    raise ValueError(f"Collect the required {name.replace('_', ' ')} before submitting.")
                fields[name] = value
        payload = {**form, "values": fields, "contact_revisions": revisions}
        fields_text = "; ".join(f"{key}: {value}" for key, value in fields.items())
        return payload, f"submit {form['name']} to {form['recipient']} with {fields_text}"
    raise ValueError("Unknown action")


def _turn(session):
    return getattr(session, "_voice_action_user_turn", getattr(session, "turn_id", 0))


def _normalize(text):
    return " ".join(str(text or "").casefold().split()).rstrip(".!?")


def _confirmed(session, proposal, user_text):
    if proposal["turn"] == _turn(session):
        return False
    if re.search(r"\b(?:no|not|don't|do not|cancel|instead|wrong|stop|wait|change)\b", user_text, re.I):
        return False
    # Independent full repeatback works when a PSTN transport can prove only
    # transmission. A bare yes needs correlated completed readback evidence.
    literal = re.sub(r"^yes\s*,?\s+", "", user_text, flags=re.I)
    if literal != user_text and _normalize(literal) == _normalize(proposal["summary"]):
        return True
    from app.domain.services.voice_pipeline.action_confirmation import explicit_action_matches
    if explicit_action_matches(proposal.get("action"), proposal.get("payload") or {}, user_text):
        return True
    delivered = getattr(session, "_voice_action_delivered_text", "")
    return bool(re.fullmatch(r"\s*(?:yes|yes please|correct|confirm|i confirm|go ahead)[.!\s]*", user_text, re.I)
                and _normalize(proposal["summary"]) in _normalize(delivered))


async def execute_connected_voice_action(session, action, arguments, user_text):
    # Bind the supplied caller text before any async policy/DB read. An ASR
    # correction must not let an older request adopt a newer admission token.
    admission_turn = _turn(session)
    if re.search(r"\b(?:cancel|don't|do not|never mind|stop|wait)\b", user_text, re.I):
        getattr(session, "_voice_action_proposals", {}).pop(action, None)
        return _result(action, "cancelled", "The caller has not authorized execution of this action.")
    try:
        pool = await _pool(session)
        context = await _context(session, pool)  # Fresh policy and tenant ownership.
        if action not in _capabilities(context):
            return _result(action, "unavailable", "This action is not configured and authorized for this call.")
        payload, summary = _parameters(session, context, action, arguments)
    except ValueError as exc:
        return _result(action, "needs_details", str(exc))
    except Exception:
        return _result(action, "unavailable", "The action configuration could not be verified.")
    if _turn(session) != admission_turn:
        return _result(action, "confirmation_expired", "The caller changed the request. Confirm the current details again.")
    proposals = getattr(session, "_voice_action_proposals", {})
    proposal = proposals.get(action)
    if not proposal or proposal["payload"] != payload:
        proposal = {"id": str(uuid4()), "turn": _turn(session), "action": action, "payload": payload, "summary": summary}
        proposals[action] = proposal
        session._voice_action_proposals = proposals
    if not _confirmed(session, proposal, user_text):
        return _result(action, "needs_confirmation",
            f"Read this request to the caller: {summary}. Ask them to confirm the action and repeat "
            "its complete address/number and, for a callback, the date including year, time with AM or PM, "
            "and timezone city. They may use ordinary spoken digits, dates, 'at' and 'dot'; "
            "do not ask them to recite a JSON value or IANA timezone identifier. For a form, ask them "
            "to name the form and repeat its captured values, not the internal inbox or subject. "
            "Do not execute until a later caller turn confirms it.", request_id=proposal["id"], confirmation_summary=summary)
    request = {"parameters": payload, "confirmation": {"request_id": proposal["id"],
               "turn": str(admission_turn), "caller_text_hash": hashlib.sha256(user_text.encode()).hexdigest()}}
    # Replays reuse the original confirmation as well as the same parameters.
    request = proposal.setdefault("confirmed_request", request)

    async def perform():
        # A new caller turn may arrive while the database claim is committing.
        # Check the current revision immediately before any side effect.
        latest = await _context(session, pool)
        if action not in _capabilities(latest):
            return _result(action, "authorization_changed", "This action is no longer authorized for the call.")
        current, _ = _parameters(session, latest, action, arguments)
        if current != payload or str(_turn(session)) != request["confirmation"]["turn"]:
            return _result(action, "confirmation_expired", "The caller changed the request. Confirm the current details again.")
        if action in {"send_email", "submit_form"}:
            from app.services.email_service import EmailService
            body = payload.get("body") or "\n".join(f"{k}: {v}" for k, v in payload["values"].items())
            receipt = await EmailService(pool).send_email(tenant_id=str(_call_ids(session)[0]),
                to=[payload["recipient"]], subject=payload["subject"], body=body,
                lead_ids=[str(context["lead_id"])] if context.get("lead_id") else None,
                call_id=str(context["call_id"]), triggered_by="voice")
            # Project only existing safe receipt fields; the outer executor owns
            # its action ID, while this explicitly records the returned inner ID.
            evidence = public_action_receipt({"id": None, "output_data": {
                **receipt, "child_action_id": receipt.get("action_id"),
            }})["receipt"]
            if not receipt.get("success") or not receipt.get("message_id"):
                return _result(action, receipt.get("status") or "unknown", "Sending was not confirmed; do not resend automatically.", **evidence)
            return _result(action, "provider_accepted", "The provider accepted the email for sending. Recipient delivery is unconfirmed.",
                           success=True, confirmation_allowed=True, **evidence)
        if action == "transfer_call":
            from app.domain.services.telephony.adapter_registry import execute_transfer
            brief = context.get("brief") or {}
            ring = {key: brief[field] for key, field in (
                ("ring_seconds", "transfer_ring_seconds"), ("if_no_answer", "transfer_if_no_answer"))
                if brief.get(field) is not None}
            receipt = await execute_transfer(str(context["provider_call_id"]), payload["destination"], "blind",
                idempotency_key="voice-" + proposal["id"], actor_type="service", actor_role="campaign_voice", **ring)
            status, success, allowed, message = _transfer_outcome(receipt)
            return _result(action, status, message, success=success, confirmation_allowed=allowed,
                           transfer_receipt=receipt)
        if action == "schedule_callback":
            # The scheduled action IS the durable outbox. The existing worker
            # publishes a deterministic dialer job once the due time arrives.
            return _result(action, "scheduled", "The callback is scheduled for the confirmed time, subject to campaign availability and call policy.",
                           success=True, confirmation_allowed=True, scheduled_at=payload["scheduled_at"], timezone=payload["timezone"])
        return _result(action, "unavailable", "No executor exists for this action.")

    return await DurableActionExecutor(pool).execute(tenant_id=str(_call_ids(session)[0]),
        idempotency_key="voice-" + proposal["id"], action=action, payload=request, executor=perform,
        call_id=str(context["call_id"]), lead_id=str(context["lead_id"]) if context.get("lead_id") else None,
        campaign_id=str(context["campaign_id"]), triggered_by="voice")
