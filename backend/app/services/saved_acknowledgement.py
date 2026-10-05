"""Recognize an existing Gmail acknowledgement, never infer remote success.

Only the durable executor's failed final-save envelope is eligible. The caller
must supply the original same-object proof from the existing receipt parser.
There is no provider, credential, execution or status mutation in this module.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID


def _object(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise ValueError("A saved object is required")
    return value


def _uuid(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, (str, UUID)):
        raise ValueError("Invalid saved UUID")
    result = str(UUID(str(value)))
    if isinstance(value, str) and value != result:
        raise ValueError("Noncanonical saved UUID")
    return result


def _time(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("An aware saved timestamp is required")
    return value.astimezone(timezone.utc).isoformat()


def source_digest(row):
    """Same digest for adapter strings and native PostgreSQL scalar values.

    JSON payload values are not normalized: a different recipient/reference or
    confirmed parameter remains different. This is separate from request_hash.
    """
    snapshot = {key: _uuid(row.get(key), optional=key not in {"id", "tenant_id"})
                for key in ("id", "tenant_id", "user_id", "conversation_id", "call_id",
                            "lead_id", "campaign_id", "connector_id")}
    snapshot.update({key: _time(row.get(key), optional=key == "scheduled_at")
                     for key in ("created_at", "started_at", "completed_at", "scheduled_at")})
    snapshot.update({key: row.get(key) for key in
                     ("type", "status", "triggered_by", "idempotency_key", "outcome_status", "error", "duration_ms")})
    snapshot.update(input_data=_object(row.get("input_data")), output_data=_object(row.get("output_data")))
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def gmail_acknowledgement(row, original_bundle):
    """Return only a validated already-saved result, or no eligible recovery."""
    try:
        if row.get("type") != "send_email" or row.get("status") != "unknown" or original_bundle is None:
            return None
        action_id = _uuid(row.get("id"))
        outer, intent = _object(row.get("output_data")), _object(row.get("input_data"))
        if (type(outer.get("version")) is not int or outer["version"] != 1
                or outer.get("action") != "send_email" or outer.get("action_id") != action_id
                or outer.get("status") != "unknown" or outer.get("success") is not False
                or outer.get("confirmation_allowed") is not False or "receipt_recovery_id" in outer):
            return None
        result = _object(outer.get("provider_result"))
        if (result.get("success") is not True or result.get("confirmation_allowed") is not True
                or result.get("action") != "send_email" or result.get("action_id") != action_id
                or "provider_result" in result or "receipt_recovery_id" in result
                or _uuid(result.get("child_action_id")) == action_id):
            return None
        proof, message_id = original_bundle
        if proof.get("provider") != "gmail" or result.get("message_id") != message_id:
            return None
        if set(intent) != {"request_hash", "parameters"}:
            return None
        parameters = _object(intent["parameters"])
        # Match DurableActionExecutor exactly, including ensure_ascii=True.
        encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str)
        if intent.get("request_hash") != hashlib.sha256(encoded.encode()).hexdigest():
            return None
        key = row.get("idempotency_key")
        if row.get("triggered_by") == "assistant":
            user_id = _uuid(row.get("user_id"))
            if (not isinstance(key, str) or re.fullmatch(r"assistant:" + re.escape(user_id) + r":prop_[0-9a-f]{16}", key) is None
                    or parameters.get("confirm") is not True or result.get("status") != "accepted"):
                return None
            reviewed = _object(parameters.get("_reviewed_connector"))
            for field in ("identity_version", "tenant_id", "connector_id", "provider", "account_row_id", "external_account_id"):
                if reviewed.get(field) != proof.get(field):
                    return None
            recipients = parameters.get("to")
            if (not isinstance(recipients, list) or not recipients
                    or any(not isinstance(value, str) or not value.strip() for value in recipients)
                    or result.get("recipients") != recipients
                    or type(result.get("recipient_count")) is not int
                    or result["recipient_count"] != len(recipients)):
                return None
        elif row.get("triggered_by") == "voice":
            confirmation = _object(parameters.get("confirmation"))
            payload = _object(parameters.get("parameters"))
            request_id = _uuid(confirmation.get("request_id"))
            if (key != "voice-" + request_id or result.get("status") != "provider_accepted"
                    or type(result.get("version")) is not int or result["version"] != 1
                    or not isinstance(confirmation.get("turn"), str) or not confirmation["turn"].strip()
                    or not isinstance(confirmation.get("caller_text_hash"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", confirmation["caller_text_hash"]) is None
                    or not isinstance(payload.get("recipient"), str) or not payload["recipient"].strip()):
                return None
            _uuid(row.get("call_id"))
            _uuid(row.get("campaign_id"))
        else:
            return None
        return {"source_digest": source_digest(row), "provider_status": result["status"], "result": result,
                "original_output": outer, "original_timestamps": {
                    key: _time(row.get(key), optional=key == "scheduled_at")
                    for key in ("created_at", "started_at", "completed_at", "scheduled_at")}}
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
