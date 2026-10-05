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
from app.services.action_execution import DurableActionExecutor


def _object(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _result(action, status, message, **extra):
    return {"version": 1, "action": action, "success": False, "status": status,
            "confirmation_allowed": False, "message": message, **extra}


def enabled_voice_actions(session):
    return {"end_call", *getattr(session, "_voice_action_capabilities", {}).keys()}


async def _pool(session):
    pool = getattr(session, "_voice_action_pool", None)
    if pool is None:
        from app.core.container import get_container
        pool = get_container().db_pool
    return pool


def _inbound_transfer_policy_available(context, tenant):
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
        destination = normalize_did(context["brief"].get("transfer_destination"))
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


async def _context(session, pool):
    tenant = getattr(session, "tenant_id", None)
    campaign = getattr(session, "campaign_id", None)
    call = getattr(session, "call_id", None)
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
        allowed = set(context["brief"].get("approved_next_actions") or [])
        if ("transfer" in allowed and context.get("call_direction") == "inbound"
                and _inbound_transfer_policy_available(context, str(tenant))):
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


def _capabilities(context):
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
    if ("transfer" in allowed and brief.get("transfer_destination") and context.get("transfer_connected")
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
        destination = str(brief["transfer_destination"]).strip()
        requested = str(arguments.get("destination") or "").strip()
        if requested and requested != destination:
            raise ValueError("Only the campaign's configured transfer destination is available.")
        return {"destination": destination}, f"transfer this call to {destination}"
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
            receipt = await EmailService(pool).send_email(tenant_id=str(session.tenant_id),
                to=[payload["recipient"]], subject=payload["subject"], body=body,
                lead_ids=[str(context["lead_id"])] if context.get("lead_id") else None,
                call_id=str(context["call_id"]), triggered_by="voice")
            if not receipt.get("success") or not receipt.get("message_id"):
                return _result(action, receipt.get("status") or "unknown", "Sending was not confirmed; do not resend automatically.")
            return _result(action, "provider_accepted", "The provider accepted the email for sending. Recipient delivery is unconfirmed.",
                           success=True, confirmation_allowed=True, provider=receipt.get("provider"), message_id=receipt["message_id"])
        if action == "transfer_call":
            from app.domain.services.telephony.adapter_registry import execute_transfer
            receipt = await execute_transfer(str(context["provider_call_id"]), payload["destination"], "blind",
                idempotency_key="voice-" + proposal["id"], actor_type="service", actor_role="campaign_voice")
            connected = receipt.get("status") in {"connected", "completed", "transferred"}
            if receipt.get("status") == "success":
                return _result(action, "provider_accepted", "The PBX accepted the transfer request; connection is unconfirmed.",
                               success=True, transfer_receipt=receipt)
            return _result(action, "connected" if connected else ("failed" if receipt.get("status") == "failed" else "unknown"), "Transfer connected." if connected else "Transfer has not been confirmed.",
                           success=connected, confirmation_allowed=connected, transfer_receipt=receipt)
        if action == "schedule_callback":
            # The scheduled action IS the durable outbox. The existing worker
            # publishes a deterministic dialer job once the due time arrives.
            return _result(action, "scheduled", "The callback is scheduled for the confirmed time, subject to campaign availability and call policy.",
                           success=True, confirmation_allowed=True, scheduled_at=payload["scheduled_at"], timezone=payload["timezone"])
        return _result(action, "unavailable", "No executor exists for this action.")

    return await DurableActionExecutor(pool).execute(tenant_id=str(session.tenant_id),
        idempotency_key="voice-" + proposal["id"], action=action, payload=request, executor=perform,
        call_id=str(context["call_id"]), lead_id=str(context["lead_id"]) if context.get("lead_id") else None,
        campaign_id=str(context["campaign_id"]), triggered_by="voice")
