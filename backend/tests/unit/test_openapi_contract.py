"""F2-11: inspect the actually mounted schema; no lifespan/provider/DB starts."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from tests.unit.test_operational_endpoint_exposure import mounted  # noqa: F401


USER_SECURITY = [{"BearerAuth": []}, {"AccessCookieAuth": []}, {"SessionCookieAuth": []}]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


@pytest.fixture(scope="module")
def contract_snapshot(tmp_path_factory):
    """Build the real mounted schema once for read-only assertions.

    Restore its socket/config patches before another Windows event loop starts.
    Tests that mutate routes or send HTTP requests retain fresh mounted apps.
    """
    async def build():
        patch = pytest.MonkeyPatch()
        worlds = mounted.__wrapped__(patch, tmp_path_factory.mktemp("openapi-snapshot"))
        try:
            world = await anext(worlds)
            return SimpleNamespace(app=world.app, schema=world.app.openapi())
        finally:
            try:
                await worlds.aclose()
            finally:
                patch.undo()

    return asyncio.run(build())


def operations(schema):
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield method, path, operation


def dereference(schema, value):
    while "$ref" in value:
        target = schema
        for segment in value["$ref"].removeprefix("#/").split("/"):
            target = target[segment]
        value = target
    return value


def test_global_security_matches_real_user_credentials_and_uses_or_not_and(contract_snapshot):
    schema = contract_snapshot.schema
    assert schema.get("security") == USER_SECURITY
    schemes = schema["components"]["securitySchemes"]
    assert schemes["BearerAuth"]["type"] == "http" and schemes["BearerAuth"]["scheme"] == "bearer"
    for name, cookie in (("AccessCookieAuth", "talky_at"), ("SessionCookieAuth", "talky_sid")):
        assert schemes[name]["type"] == "apiKey"
        assert schemes[name]["in"] == "cookie" and schemes[name]["name"] == cookie
    for path, method in (("/api/v1/auth/me", "get"), ("/api/v1/calls/", "get"), ("/api/v1/auth/logout-all", "post")):
        assert schema["paths"][path][method].get("security", schema["security"]) == USER_SECURITY


@pytest.mark.parametrize("method,path", [
    ("get", "/"), ("get", "/health"), ("get", "/api/v1/health"),
    ("get", "/api/v1/healthz/live"), ("get", "/api/v1/healthz/ready"),
    ("get", "/api/v1/healthz/deep"), ("get", "/api/v1/healthz/workers"),
    ("get", "/api/v1/plans/"), ("get", "/api/v1/plans/payg"),
    ("get", "/api/v1/connectors/providers"), ("get", "/api/v1/connectors/callback"),
    ("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/register"),
    ("post", "/api/v1/auth/signup/start"), ("post", "/api/v1/auth/signup/verify-code"),
    ("post", "/api/v1/auth/signup/complete"), ("post", "/api/v1/auth/passkey-check"),
    ("post", "/api/v1/auth/forgot-password"), ("post", "/api/v1/auth/reset-password"),
    ("get", "/api/v1/auth/verify-email"), ("post", "/api/v1/auth/mfa/verify"),
    ("post", "/api/v1/auth/passkeys/login/begin"), ("post", "/api/v1/auth/passkeys/login/complete"),
    ("post", "/api/v1/auth/invites/preview"), ("post", "/api/v1/auth/invites/accept"),
])
def test_public_and_body_credential_routes_do_not_inherit_user_bearer(method, path, contract_snapshot):
    assert contract_snapshot.schema["paths"][path][method].get("security") == []


def test_refresh_documents_only_the_actual_refresh_cookie(contract_snapshot):
    schema = contract_snapshot.schema
    assert schema["paths"]["/api/v1/auth/refresh"]["post"]["security"] == [{"RefreshCookieAuth": []}]
    scheme = schema["components"]["securitySchemes"]["RefreshCookieAuth"]
    assert scheme["in"] == "cookie" and scheme["name"] == "talky_rt"
    assert {"RefreshCookieAuth": []} not in schema["security"]


@pytest.mark.parametrize("path,method,name,header", [
    ("/metrics", "get", "MetricsToken", "X-Metrics-Token"),
    ("/api/v1/health/detailed", "get", "InternalServiceToken", "X-Internal-Service-Token"),
    ("/api/v1/webhooks/call/goal-achieved", "post", "WebhookSignature", "X-Webhook-Signature"),
    ("/api/v1/webhooks/secure/call/mark-spam", "post", "WebhookSignature", "X-Webhook-Signature"),
])
def test_service_and_signed_routes_do_not_invent_user_session_auth(contract_snapshot, path, method, name, header):
    schema = contract_snapshot.schema
    assert schema["paths"][path][method]["security"] == [{name: []}]
    scheme = schema["components"]["securitySchemes"][name]
    assert scheme["type"] == "apiKey" and scheme["in"] == "header" and scheme["name"] == header


def test_dedicated_alertmanager_security_and_explicit_response_contract_are_preserved(contract_snapshot):
    schema = contract_snapshot.schema
    selected = [op for _, _, op in operations(schema) if {"AlertmanagerIngestBearer": []} in op.get("security", [])]
    assert len(selected) == 1
    assert "accepted_alerts" in str(selected[0]["responses"]["200"])
    assert schema["components"]["securitySchemes"]["AlertmanagerIngestBearer"]["scheme"] == "bearer"


def test_every_mounted_operation_has_tags_and_common_error_responses(contract_snapshot):
    schema = contract_snapshot.schema
    errors = []
    for method, path, operation in operations(schema):
        if not operation.get("tags"):
            errors.append(f"{method.upper()} {path}: missing tags")
        for status in ("401", "403", "422", "429", "503"):
            response = operation.get("responses", {}).get(status)
            if not response or "application/json" not in dereference(schema, response).get("content", {}):
                errors.append(f"{method.upper()} {path}: missing documented JSON {status}")
    assert not errors, "\n".join(errors)


class ValidationProbe(BaseModel):
    count: int


async def test_documented_validation_envelope_matches_actual_registered_handler(mounted):
    @mounted.app.post("/qa-contract-validation", tags=["qa"], openapi_extra={"security": []})
    async def probe(body: ValidationProbe) -> ValidationProbe:
        return body

    schema = mounted.app.openapi()
    documented = dereference(schema, schema["paths"]["/qa-contract-validation"]["post"]["responses"]["422"])
    body_schema = dereference(schema, documented["content"]["application/json"]["schema"])
    assert "error" in body_schema.get("properties", {}), "FastAPI's default HTTPValidationError is not the registered envelope"
    transport = httpx.ASGITransport(app=mounted.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.synthetic.example") as client:
        response = await client.post("/qa-contract-validation", json={"count": "not-an-int"},
                                     headers={"Origin": mounted.settings.allowed_origins[0]})
    assert response.status_code == 422, response.text
    assert set(response.json()) == {"error"}
    assert {"code", "message", "details", "request_id"} <= set(response.json()["error"])
    assert response.json()["error"]["code"] == "validation_error"


def test_schema_is_lazy_cached_and_refreshes_when_a_router_is_added(mounted):
    before = mounted.app.openapi()
    assert mounted.app.openapi() is before

    @mounted.app.get("/qa-contract-late", tags=["qa"], openapi_extra={"security": []})
    async def late() -> ValidationProbe:
        return ValidationProbe(count=1)

    after = mounted.app.openapi()
    assert "/qa-contract-late" in after["paths"]
    assert "/qa-contract-late" not in before["paths"], "Do not mutate an already-exported schema snapshot"


def test_intentional_explicit_error_response_is_not_overwritten(mounted):
    explicit = {"description": "Intentional fixture contract", "content": {
        "application/json": {"schema": {"type": "object", "properties": {"reason": {"type": "string"}}}},
    }}

    @mounted.app.get("/qa-contract-explicit", tags=["qa"], responses={403: explicit}, openapi_extra={"security": []})
    async def probe() -> ValidationProbe:
        return ValidationProbe(count=1)

    assert mounted.app.openapi()["paths"]["/qa-contract-explicit"]["get"]["responses"]["403"] == explicit


def test_new_workflow_success_contract_describes_stored_fields_and_nullable_get(contract_snapshot):
    schema = contract_snapshot.schema
    path = schema["paths"]["/api/v1/calls/{call_id}/workflow"]
    read = dereference(schema, path["get"]["responses"]["200"]["content"]["application/json"]["schema"])
    write = dereference(schema, path["put"]["responses"]["200"]["content"]["application/json"]["schema"])
    variants = read["properties"]["workflow"]["anyOf"]
    assert {"type": "null"} in variants
    workflow = dereference(schema, next(item for item in variants if item != {"type": "null"}))
    assert {"leadType", "notes", "form", "updatedAt"} <= workflow["properties"].keys()
    assert {"contact", "interest", "nextStep", "completed"} <= workflow["properties"]["form"]["properties"].keys()
    assert write["properties"]["created"]["type"] == "boolean"
    assert dereference(schema, write["properties"]["workflow"]) == workflow


def test_new_dsar_contract_states_manifest_limits_and_binary_export(contract_snapshot):
    schema = contract_snapshot.schema
    preview = schema["paths"]["/api/v1/admin/dsar/preview"]["post"]["responses"]["200"]
    manifest = dereference(schema, preview["content"]["application/json"]["schema"])
    properties = manifest["properties"]
    assert {"tenant_id", "subject_kind", "counts", "missing_tables", "issues", "legal_hold",
            "retained_or_unimplemented_scopes", "limits", "coverage", "consistency"} <= properties.keys()
    assert properties["full_dsar_complete"]["const"] is False
    assert properties["destructive_apply_supported"]["const"] is False
    assert properties["complete"]["type"] == "boolean"
    export = schema["paths"]["/api/v1/admin/dsar/export"]["post"]["responses"]["200"]
    assert export["content"] == {"application/zip": {"schema": {"type": "string", "format": "binary"}}}


def test_new_duplicate_contract_describes_owned_draft_without_invented_activity(contract_snapshot):
    schema = contract_snapshot.schema
    response = schema["paths"]["/api/v1/campaigns/{campaign_id}/duplicate"]["post"]["responses"]["201"]
    body = dereference(schema, response["content"]["application/json"]["schema"])
    campaign = dereference(schema, body["properties"]["campaign"])
    properties = campaign["properties"]
    assert {"id", "tenant_id", "name", "voice_id", "tts_provider", "script_config", "calling_config",
            "knowledge_mode", "knowledge_model", "prompt_version_pin"} <= properties.keys()
    assert properties["status"]["const"] == "draft"
    assert properties["direction"]["const"] == "outbound"
    for field, kind in (("max_concurrent_calls", "integer"), ("max_retries", "integer"), ("retry_failed", "boolean")):
        assert set(properties[field]["type"]) == {kind, "null"}, "Legacy nullable settings are copied verbatim"
    for field in ("total_leads", "calls_completed", "calls_failed"):
        assert properties[field]["const"] == 0


@pytest.mark.parametrize("method,path,security", [
    ("get", "/api/v1/ask-ai/greeting", []),
    ("get", "/api/v1/billing/config", []),
    ("get", "/api/v1/billing/plans", []),
    ("post", "/api/v1/admin/secrets/validate-api-key", []),
    ("post", "/api/v1/public/contact-enquiries", []),
    ("post", "/api/v1/billing/webhooks", [{"StripeSignature": []}]),
    ("post", "/api/v1/twilio/answer", [{"TwilioSignature": []}]),
    ("post", "/api/v1/vonage/event", [{"VonageWebhookJWT": []}]),
    ("post", "/api/v1/sip/telephony/call", [{"InternalServiceToken": []}]),
    ("post", "/api/v1/sip/telephony/audio/{session_id}", [{"InternalServiceToken": []}]),
    ("post", "/api/v1/sip/telephony/start", [{"InternalServiceToken": []}, *USER_SECURITY]),
    ("post", "/api/v1/sip/telephony/hangup/{call_id}", [{"InternalServiceToken": []}, {"BearerAuth": []}]),
    ("get", "/api/v1/support/attachments/{attachment_id}/content", [{"SupportDownloadSignature": []}]),
])
def test_reviewed_special_credentials_are_exact_not_user_token_aliases(contract_snapshot, method, path, security):
    assert contract_snapshot.schema["paths"][path][method]["security"] == security


def test_path_secret_and_oauth_state_ceremonies_are_not_described_as_anonymous(contract_snapshot):
    schema = contract_snapshot.schema
    for path in ("/api/v1/connectors/salesforce/callback-requests/{tenant_id}/{token}",
                 "/api/v1/connectors/salesforce/outbound-message/{tenant_id}/{token}"):
        operation = schema["paths"][path]["post"]
        assert operation["security"] == []
        assert "secret" in operation["x-authentication"] and "not anonymous" in operation["x-authentication"]
    assert "OAuth state" in schema["paths"]["/api/v1/connectors/callback"]["get"]["x-authentication"]
