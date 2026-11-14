"""Documentation for the mounted API; never an authentication/validation layer.

Keep exceptions exact: a new route inherits user authentication, not a prefix
exemption. Successful legacy bodies remain visible response-schema debt.
"""
from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts


USER_SECURITY = [{"BearerAuth": []}, {"AccessCookieAuth": []}, {"SessionCookieAuth": []}]
_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_PUBLIC = {
    ("get", path) for path in (
        "/", "/health", "/api/v1/health", "/api/v1/healthz/live", "/api/v1/healthz/ready",
        "/api/v1/healthz/deep", "/api/v1/healthz/workers", "/api/v1/plans/", "/api/v1/plans/payg",
        "/api/v1/billing/plans", "/api/v1/billing/config", "/api/v1/connectors/providers",
        "/api/v1/connectors/callback", "/api/v1/auth/verify-email", "/api/v1/auth/legal-versions", "/api/v1/ask-ai/greeting",
    )
} | {
    ("post", path) for path in (
        "/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/signup/start",
        "/api/v1/auth/signup/verify-code", "/api/v1/auth/signup/complete",
        "/api/v1/auth/passkey-check", "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password",
        "/api/v1/auth/mfa/verify", "/api/v1/auth/passkeys/login/begin",
        "/api/v1/auth/passkeys/login/complete", "/api/v1/admin/secrets/validate-api-key",
        "/api/v1/public/contact-enquiries", "/api/v1/auth/invites/preview", "/api/v1/auth/invites/accept",
    )
}
_HEADER_SCHEMES = {
    "MetricsToken": ("X-Metrics-Token", "Dedicated metrics credential; not a user access token."),
    "InternalServiceToken": ("X-Internal-Service-Token", "Configured internal-service credential; not a user session."),
    "WebhookSignature": ("X-Webhook-Signature", "Tenant HMAC signature. Timestamp freshness and required webhook headers also apply."),
    "StripeSignature": ("Stripe-Signature", "Stripe webhook signature over the original request body."),
    "TwilioSignature": ("X-Twilio-Signature", "Provider signature over the configured callback URL and form fields."),
}
_SIGNED = {
    ("get", "/metrics"): "MetricsToken",
    ("get", "/api/v1/health/detailed"): "InternalServiceToken",
    ("post", "/api/v1/billing/webhooks"): "StripeSignature",
    ("post", "/api/v1/webhooks/call/goal-achieved"): "WebhookSignature",
    ("post", "/api/v1/webhooks/call/mark-spam"): "WebhookSignature",
    ("post", "/api/v1/webhooks/secure/call/goal-achieved"): "WebhookSignature",
    ("post", "/api/v1/webhooks/secure/call/mark-spam"): "WebhookSignature",
    ("post", "/api/v1/webhooks/secure/idempotent-example"): "WebhookSignature",
    ("post", "/api/v1/webhooks/secure/admin/configure"): "InternalServiceToken",
    ("post", "/api/v1/twilio/answer"): "TwilioSignature",
    ("post", "/api/v1/twilio/event"): "TwilioSignature",
    ("post", "/api/v1/vonage/answer"): "VonageWebhookJWT",
    ("post", "/api/v1/vonage/event"): "VonageWebhookJWT",
    ("post", "/api/v1/sip/telephony/audio/{session_id}"): "InternalServiceToken",
    ("post", "/api/v1/sip/telephony/call"): "InternalServiceToken",
    ("get", "/api/v1/support/attachments/{attachment_id}/content"): "SupportDownloadSignature",
    ("post", "/api/v1/auth/refresh"): "RefreshCookieAuth",
}
_INTERNAL_OR_USER = {
    ("post", "/api/v1/sip/telephony/start"), ("post", "/api/v1/sip/telephony/stop"),
    ("get", "/api/v1/sip/telephony/status"),
}
_INTERNAL_OR_BEARER = {
    ("post", "/api/v1/sip/telephony/hangup/{call_id}"),
    ("post", "/api/v1/sip/telephony/transfer/blind"), ("post", "/api/v1/sip/telephony/transfer/attended"),
    ("post", "/api/v1/sip/telephony/transfer/deflect"),
    ("get", "/api/v1/sip/telephony/transfer/attempts/{attempt_id}"),
}
_PATH_SECRET = {
    ("post", "/api/v1/connectors/salesforce/callback-requests/{tenant_id}/{token}"),
    ("post", "/api/v1/connectors/salesforce/outbound-message/{tenant_id}/{token}"),
}


def _object(properties, *, required=None, **extra):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, **extra}


def _schemas():
    text = {"type": "string"}
    uuid = {"type": "string", "format": "uuid"}
    nullable_text = {"type": ["string", "null"]}
    form = _object({"contact": text, "interest": text, "nextStep": text, "completed": {"type": "boolean"}})
    workflow = _object({"leadType": {"type": "string", "enum": ["cold", "warm", "hot", "follow_up"]},
                        "notes": text, "form": form, "updatedAt": {"type": "string", "format": "date-time"}})
    manifest = _object({
        "version": {"type": "integer", "const": 1}, "request_id": uuid, "tenant_id": uuid,
        "subject_kind": {"type": "string", "enum": ["tenant", "phone", "email"]},
        "mode": {"type": "string", "const": "dry_run"},
        "destructive_apply_supported": {"type": "boolean", "const": False},
        "full_dsar_complete": {"type": "boolean", "const": False},
        "complete": {"type": "boolean", "description": "Only the declared operational registry is complete; never full erasure approval."},
        "coverage": text, "consistency": text,
        "counts": {"type": "object", "additionalProperties": {"type": ["integer", "null"], "minimum": 0}},
        "missing_tables": {"type": "array", "items": text},
        "issues": {"type": "array", "items": _object({"code": text, "table": text, "count": {"type": "integer"},
                                                           "resource_id": text}, required=["code"], additionalProperties=True)},
        "legal_hold": {"type": ["boolean", "null"]},
        "retained_or_unimplemented_scopes": {"type": "array", "items": _object({"scope": text, "reason": text})},
        "limits": _object({key: {"type": "integer", "minimum": 1} for key in (
            "rows_per_table", "row_bytes", "all_row_bytes", "artifact_bytes", "all_artifact_bytes", "artifact_work_seconds")}),
    }, description="Audited preview only. Whole-tenant and retained/unimplemented scopes remain incomplete.")
    campaign = _object({
        "id": uuid, "tenant_id": uuid, "name": text, "description": nullable_text,
        "status": {"type": "string", "const": "draft"}, "direction": {"type": "string", "const": "outbound"},
        "system_prompt": nullable_text, "voice_id": nullable_text, "tts_provider": nullable_text,
        "max_concurrent_calls": {"type": ["integer", "null"]}, "retry_failed": {"type": ["boolean", "null"]},
        "max_retries": {"type": ["integer", "null"]}, "goal": nullable_text,
        "script_config": {"type": ["object", "null"], "additionalProperties": True},
        "calling_config": {"type": ["object", "null"], "additionalProperties": True},
        "knowledge_mode": nullable_text, "knowledge_model": nullable_text, "prompt_version_pin": nullable_text,
        **{key: {"type": "integer", "const": 0} for key in ("total_leads", "calls_completed", "calls_failed")},
        "started_at": {"type": "null"}, "completed_at": {"type": "null"},
        "created_at": {"type": "string", "format": "date-time"}, "updated_at": {"type": "string", "format": "date-time"},
    }, additionalProperties=True, description="Copied campaign row. Knowledge is copied independently; contacts, calls and activity are not copied.")
    return {
        "CanonicalErrorEnvelope": _object({"error": _object({
            "code": text, "message": text, "details": {"description": "Optional structured context; null when absent."},
            "request_id": text,
        })}),
        "StoredCallWorkflow": workflow, "DsarPreviewManifest": manifest, "DuplicatedCampaign": campaign,
    }


def _success_contract(method, path):
    if path == "/api/v1/calls/{call_id}/workflow" and method in {"get", "put"}:
        workflow = {"$ref": "#/components/schemas/StoredCallWorkflow"}
        fields = ({"workflow": {"anyOf": [workflow, {"type": "null"}]}} if method == "get"
                  else {"workflow": workflow, "created": {"type": "boolean"}})
        return "200", {"description": "Current tenant-owned workflow; a GET returns null before its first save.",
                       "content": {"application/json": {"schema": _object(fields)}}}
    if (method, path) == ("post", "/api/v1/campaigns/{campaign_id}/duplicate"):
        return "201", {"description": "Owned draft persisted with independent knowledge copies and atomic audit receipt.",
                       "content": {"application/json": {"schema": _object({"campaign": {"$ref": "#/components/schemas/DuplicatedCampaign"}})}}}
    if (method, path) == ("post", "/api/v1/admin/dsar/preview"):
        return "200", {"description": "Bounded preview manifest; not a complete DSAR or permission to erase.",
                       "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DsarPreviewManifest"}}}}
    if (method, path) == ("post", "/api/v1/admin/dsar/export"):
        return "200", {"description": "Private ZIP with data and an explicit incomplete-coverage manifest; no destructive apply.",
                       "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    return None


def _authentication(operation, method, path):
    # Native route contracts, including Alertmanager's dedicated Bearer scheme,
    # take precedence. Do not turn a custom auth scheme into a user token.
    if "security" in operation:
        return
    key = method, path
    if key in _PUBLIC or key in _PATH_SECRET:
        operation["security"] = []
        if key in _PATH_SECRET:
            operation["x-authentication"] = "Tenant-scoped secret in the token path parameter; not anonymous. OpenAPI apiKey cannot describe path credentials."
        elif "/auth/" in path or path.endswith("/connectors/callback") or path.endswith("/validate-api-key"):
            operation["x-authentication"] = "Pre-authentication ceremony: validate the documented body/query credential or OAuth state. No user-session token is required."
        elif path in {"/api/v1/healthz/deep", "/api/v1/healthz/workers"}:
            operation["x-authentication"] = "Public minimal probe; detailed fields require X-Internal-Service-Token."
    elif key in _SIGNED:
        operation["security"] = [{_SIGNED[key]: []}]
        if path.startswith(("/api/v1/twilio/", "/api/v1/vonage/")):
            operation["x-authentication"] = "Legacy provider callback. Production activation and configured provider verification policy also apply; this is not a user JWT."
        elif path.endswith("/content"):
            operation["x-authentication"] = "Expiring signed download: both signature and expires query parameters are required."
    elif key in _INTERNAL_OR_USER:
        operation["security"] = [{"InternalServiceToken": []}, *deepcopy(USER_SECURITY)]
        operation["x-authentication"] = "Internal service or live platform-admin session; role and deployment guards still apply."
    elif key in _INTERNAL_OR_BEARER:
        operation["security"] = [{"InternalServiceToken": []}, {"BearerAuth": []}]
        operation["x-authentication"] = "Internal service or live Bearer session with tenant permissions; origination mode restrictions still apply."


def install_openapi_contract(app: FastAPI) -> None:
    """Install once before router mounting; derive/cache the schema on demand."""
    if getattr(app.state, "openapi_contract_installed", False):
        return
    app.state.openapi_contract_installed = True
    original = app.openapi
    cached_fingerprint = None

    def openapi():
        nonlocal cached_fingerprint
        routes = list(iter_route_contexts(app.routes))
        fingerprint = tuple((id(route.original_route), route.path, tuple(sorted(route.methods or ())),
                             bool(getattr(route, "include_in_schema", False))) for route in routes)
        if app.openapi_schema is not None and cached_fingerprint == fingerprint:
            return app.openapi_schema
        # Invalidate before asking FastAPI to regenerate. Never mutate a schema
        # object previously returned to a caller when late routers are mounted.
        app.openapi_schema = None
        schema = original()
        components = schema.setdefault("components", {})
        models = components.setdefault("schemas", {})
        for name, value in _schemas().items():
            models.setdefault(name, value)
        schemes = components.setdefault("securitySchemes", {})
        schemes.setdefault("BearerAuth", {"type": "http", "scheme": "bearer", "bearerFormat": "JWT", "description": "User access token bound to a live session."})
        for name, cookie in (("AccessCookieAuth", "talky_at"), ("SessionCookieAuth", "talky_sid"), ("RefreshCookieAuth", "talky_rt")):
            schemes.setdefault(name, {"type": "apiKey", "in": "cookie", "name": cookie})
        for name, (header, description) in _HEADER_SCHEMES.items():
            schemes.setdefault(name, {"type": "apiKey", "in": "header", "name": header, "description": description})
        schemes.setdefault("VonageWebhookJWT", {"type": "http", "scheme": "bearer", "bearerFormat": "Provider-signed JWT", "description": "Vonage webhook signature, not a user access token."})
        schemes.setdefault("SupportDownloadSignature", {"type": "apiKey", "in": "query", "name": "signature", "description": "Signed attachment capability; expires is also required."})
        schema["security"] = deepcopy(USER_SECURITY)
        route_map = {(route.path, method.lower()): route for route in routes for method in (route.methods or ())}
        for path, item in schema.get("paths", {}).items():
            for method, operation in item.items():
                if method not in _METHODS:
                    continue
                if not operation.get("tags"):
                    operation["tags"] = ["operations" if path in {"/", "/health", "/metrics"} else "api"]
                _authentication(operation, method, path)
                responses = operation.setdefault("responses", {})
                route = route_map.get((path, method))
                declared = getattr(route, "responses", {}) or {}
                extras = (getattr(route, "openapi_extra", None) or {}).get("responses", {})
                for status, description in (("401", "Authentication required"), ("403", "Permission denied"),
                                            ("422", "Request validation failed"), ("429", "Too many requests"),
                                            ("503", "Service temporarily unavailable")):
                    existing = responses.get(status)
                    default_validation = (status == "422" and status not in declared and 422 not in declared
                                          and status not in extras and 422 not in extras and existing is not None
                                          and existing.get("content", {}).get("application/json", {}).get("schema")
                                          == {"$ref": "#/components/schemas/HTTPValidationError"})
                    if existing is None or default_validation:
                        responses[status] = {"description": description, "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/CanonicalErrorEnvelope"}}}}
                        if status in {"429", "503"}:
                            responses[status]["headers"] = {"Retry-After": {"description": "When supplied, seconds before retrying.", "schema": {"type": "string"}}}
                success = _success_contract(method, path)
                if success is not None:
                    status, contract = success
                    existing = responses.get(status, {})
                    if (status not in declared and int(status) not in declared
                            and status not in extras and int(status) not in extras
                            and not any(media.get("schema") for media in existing.get("content", {}).values())):
                        responses[status] = contract
        cached_fingerprint = fingerprint
        return schema

    app.openapi = openapi
