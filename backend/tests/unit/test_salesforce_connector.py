"""Salesforce CRM connector — OAuth, identity, SOQL/SOSL lookup, Task logging.

Every HTTP round trip goes through ``httpx.MockTransport`` injected at the
module's ``_async_client`` seam, so the assertions are on the exact request
Salesforce would receive.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.infrastructure.connectors.crm.salesforce as sf_module
from app.infrastructure.connectors.base import ConnectorFactory, ConnectorProviderError
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "csecret")
    monkeypatch.delenv("SALESFORCE_LOGIN_URL", raising=False)
    monkeypatch.delenv("SALESFORCE_API_VERSION", raising=False)


@pytest.fixture
def connector(creds):
    return SalesforceConnector(tenant_id=TENANT, connector_id="conn-1")


class Recorder:
    """MockTransport handler that records requests and serves scripted replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        status, body = self.replies.pop(0)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body or "")


def install(monkeypatch, recorder: Recorder):
    transport = httpx.MockTransport(recorder)
    monkeypatch.setattr(
        sf_module, "_async_client", lambda **kw: httpx.AsyncClient(transport=transport, **kw)
    )


def test_factory_registers_salesforce():
    assert ConnectorFactory.is_registered("salesforce")
    created = ConnectorFactory.create(provider="salesforce", tenant_id=TENANT, connector_id="c")
    assert isinstance(created, SalesforceConnector)
    assert created.connector_type == "crm"


def test_oauth_url_carries_pkce_scopes_and_login_host(connector, monkeypatch):
    url = connector.get_oauth_url(
        redirect_uri="https://api.example/api/v1/connectors/callback",
        state="st4te",
        code_challenge="chall",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https" and parsed.netloc == "login.salesforce.com"
    assert parsed.path == "/services/oauth2/authorize"
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge"] == ["chall"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st4te"]
    assert set(q["scope"][0].split()) == {"api", "id", "refresh_token", "offline_access"}

    # Sandbox / My Domain host is an env switch, not a code change.
    monkeypatch.setenv("SALESFORCE_LOGIN_URL", "https://test.salesforce.com/")
    assert connector.get_oauth_url("https://x", "s").startswith("https://test.salesforce.com/services/oauth2/authorize?")


def test_is_configured_requires_both_env_vars(monkeypatch):
    monkeypatch.delenv("SALESFORCE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SALESFORCE_CLIENT_SECRET", raising=False)
    assert SalesforceConnector.is_configured() is False
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "x")
    assert SalesforceConnector.is_configured() is False
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "y")
    assert SalesforceConnector.is_configured() is True


@pytest.mark.asyncio
async def test_exchange_code_records_instance_url_and_stamps_expiry(connector, monkeypatch):
    rec = Recorder([(200, {
        "access_token": "AT", "refresh_token": "RT", "token_type": "Bearer",
        "instance_url": "https://acme.my.salesforce.com/",
        "id": "https://login.salesforce.com/id/00Dxx/005xx",
        "scope": "api id refresh_token",
    })])
    install(monkeypatch, rec)

    tokens = await connector.exchange_code("the-code", "https://api/cb", code_verifier="ver")

    assert tokens.access_token == "AT" and tokens.refresh_token == "RT"
    # Salesforce sends no expires_in — we must still schedule a refresh.
    assert tokens.expires_at is not None and tokens.expires_at > datetime.now(timezone.utc)
    assert connector.instance_url == "https://acme.my.salesforce.com"
    assert connector.config_from_tokens(tokens) == {
        "instance_url": "https://acme.my.salesforce.com",
        "identity_url": "https://login.salesforce.com/id/00Dxx/005xx",
    }
    req = rec.requests[0]
    assert req.url == "https://login.salesforce.com/services/oauth2/token"
    form = parse_qs(req.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code_verifier"] == ["ver"]
    assert form["client_secret"] == ["csecret"]


@pytest.mark.asyncio
async def test_refresh_keeps_old_refresh_token_when_salesforce_omits_it(connector, monkeypatch):
    rec = Recorder([(200, {"access_token": "AT2", "instance_url": "https://acme.my.salesforce.com"})])
    install(monkeypatch, rec)
    tokens = await connector.refresh_tokens("RT-old")
    assert tokens.access_token == "AT2"
    assert tokens.refresh_token == "RT-old"
    form = parse_qs(rec.requests[0].content.decode())
    assert form["grant_type"] == ["refresh_token"]


@pytest.mark.asyncio
async def test_invalid_grant_is_an_authentication_failure(connector, monkeypatch):
    rec = Recorder([(400, {"error": "invalid_grant", "error_description": "expired access/refresh token"})])
    install(monkeypatch, rec)
    with pytest.raises(ConnectorProviderError) as exc:
        await connector.refresh_tokens("RT")
    assert exc.value.category == "authentication"


@pytest.mark.asyncio
async def test_identity_probe_returns_org_and_username(connector, monkeypatch):
    connector.apply_config({"identity_url": "https://login.salesforce.com/id/00D1/0051"})
    await connector.set_access_token("AT")
    rec = Recorder([(200, {
        "organization_id": "00D000000000001EAA", "user_id": "0051", "username": "ops@acme.com",
        "email": "ops@acme.com", "display_name": "Ops",
    })])
    install(monkeypatch, rec)
    identity = await connector.fetch_account_identity()
    assert identity["email"] == "ops@acme.com"
    assert identity["external_account_id"] == "00D000000000001EAA"
    assert identity["config"]["org_id"] == "00D000000000001EAA"
    assert rec.requests[0].headers["Authorization"] == "Bearer AT"


@pytest.mark.asyncio
async def test_api_call_without_instance_url_fails_closed(connector):
    await connector.set_access_token("AT")
    with pytest.raises(ConnectorProviderError):
        await connector.query("SELECT Id FROM Task LIMIT 1")


@pytest.fixture
def ready(connector):
    connector.apply_config({"instance_url": "https://acme.my.salesforce.com"})
    import asyncio
    asyncio.get_event_loop().run_until_complete(connector.set_access_token("AT"))
    return connector


@pytest.mark.asyncio
async def test_search_contact_tries_contact_then_lead_by_email_then_phone_sosl(ready, monkeypatch):
    rec = Recorder([
        (200, {"records": []}),                       # Contact by email
        (200, {"records": []}),                       # Lead by email
        (200, {"searchRecords": [                     # SOSL by phone digits
            {"attributes": {"type": "Lead"}, "Id": "00Q1", "FirstName": "Lee", "LastName": "Ad", "Phone": "+44 7700 900123"},
            {"attributes": {"type": "Contact"}, "Id": "0031", "FirstName": "Con", "LastName": "Tact", "Phone": "07700900123"},
        ]}),
    ])
    install(monkeypatch, rec)
    found = await ready.search_contact(email="o'brien@acme.com", phone="+44 (0)7700 900-123")
    # Contact wins over Lead when both match.
    assert found["id"] == "0031" and found["object"] == "Contact"
    q0 = parse_qs(urlparse(str(rec.requests[0].url)).query)["q"][0]
    assert q0.startswith("SELECT") and "FROM Contact WHERE Email = 'o\\'brien@acme.com'" in q0
    q1 = parse_qs(urlparse(str(rec.requests[1].url)).query)["q"][0]
    assert "FROM Lead WHERE Email" in q1 and "IsConverted = false" in q1
    sosl = parse_qs(urlparse(str(rec.requests[2].url)).query)["q"][0]
    assert sosl.startswith("FIND {4407700900123} IN PHONE FIELDS RETURNING Contact(")
    assert rec.requests[2].url.path.endswith("/services/data/v60.0/search")


@pytest.mark.asyncio
async def test_search_contact_returns_none_when_nothing_matches(ready, monkeypatch):
    rec = Recorder([(200, {"searchRecords": []})])
    install(monkeypatch, rec)
    assert await ready.search_contact(phone="+15551234567") is None


@pytest.mark.asyncio
async def test_create_contact_builds_a_lead_with_required_fallbacks(ready, monkeypatch):
    rec = Recorder([(201, {"id": "00Qnew", "success": True})])
    install(monkeypatch, rec)
    created = await ready.create_contact(email="", first_name="Ada", last_name=None, phone="+15551234567", properties={"description": "from call"})
    assert created["id"] == "00Qnew" and created["object"] == "Lead"
    body = json.loads(rec.requests[0].content)
    assert rec.requests[0].url.path.endswith("/sobjects/Lead")
    assert body["LastName"] == "Ada"          # no last name -> first name becomes LastName
    assert "FirstName" not in body
    assert body["Company"] == "Unknown"       # Company is mandatory on Lead
    assert body["Phone"] == "+15551234567"
    assert body["Description"] == "from call"
    assert "Email" not in body


@pytest.mark.asyncio
async def test_log_call_creates_a_completed_call_task_against_the_record(ready, monkeypatch):
    rec = Recorder([(201, {"id": "00Ttask"})])
    install(monkeypatch, rec)
    task_id = await ready.log_call(
        contact_id="00Q1", call_body="hello", duration_seconds=125,
        outcome="Qualified", call_direction="INBOUND",
        timestamp=datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc),
    )
    assert task_id == "00Ttask"
    body = json.loads(rec.requests[0].content)
    assert body["WhoId"] == "00Q1"
    assert body["Status"] == "Completed"
    assert body["TaskSubtype"] == "Call"
    assert body["CallType"] == "Inbound"
    assert body["CallDurationInSeconds"] == 125
    assert body["CallDisposition"] == "Qualified"
    assert body["ActivityDate"] == "2026-09-07"
    assert body["Subject"] == "Call - Qualified"


@pytest.mark.asyncio
async def test_log_call_retries_without_task_subtype_when_the_org_rejects_it(ready, monkeypatch):
    rec = Recorder([
        (400, [{"errorCode": "INVALID_FIELD_FOR_INSERT_UPDATE", "message": "Unable to create/update fields: TaskSubtype"}]),
        (201, {"id": "00Tretry"}),
    ])
    install(monkeypatch, rec)
    assert await ready.log_call("0031", "body", 10) == "00Tretry"
    assert "TaskSubtype" in json.loads(rec.requests[0].content)
    assert "TaskSubtype" not in json.loads(rec.requests[1].content)


@pytest.mark.asyncio
async def test_log_call_omits_who_id_for_non_person_records(ready, monkeypatch):
    rec = Recorder([(201, {"id": "00T"})])
    install(monkeypatch, rec)
    await ready.log_call("001account", "body", 1)
    assert "WhoId" not in json.loads(rec.requests[0].content)


@pytest.mark.asyncio
async def test_update_call_log_patches_the_task(ready, monkeypatch):
    rec = Recorder([(204, "")])
    install(monkeypatch, rec)
    assert await ready.update_call_log("00T1", call_body="with summary", outcome="Callback") is True
    req = rec.requests[0]
    assert req.method == "PATCH" and req.url.path.endswith("/sobjects/Task/00T1")
    body = json.loads(req.content)
    assert body == {"Description": "with summary", "CallDisposition": "Callback", "Subject": "Call - Callback"}


@pytest.mark.asyncio
async def test_401_maps_to_authentication_so_the_sync_can_force_a_refresh(ready, monkeypatch):
    rec = Recorder([(401, [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired or invalid"}])])
    install(monkeypatch, rec)
    with pytest.raises(ConnectorProviderError) as exc:
        await ready.query("SELECT Id FROM Task LIMIT 1")
    assert exc.value.category == "authentication"
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_list_people_builds_a_phone_filtered_soql_and_shapes_rows(ready, monkeypatch):
    rec = Recorder([(200, {"records": [
        {"attributes": {"type": "Lead"}, "Id": "00Q9", "FirstName": "A", "LastName": "B", "Phone": None,
         "MobilePhone": "+15550001", "Company": "ACME", "Title": "CTO"},
    ]})])
    install(monkeypatch, rec)
    people = await ready.list_people("lead", where="Status = 'Open - Not Contacted'", limit=5000)
    soql = parse_qs(urlparse(str(rec.requests[0].url)).query)["q"][0]
    assert "FROM Lead WHERE (Phone != null OR MobilePhone != null) AND IsConverted = false AND (Status = 'Open - Not Contacted')" in soql
    assert soql.endswith("LIMIT 2000")  # clamped
    assert people[0]["phone"] == "+15550001" and people[0]["company"] == "ACME" and people[0]["title"] == "CTO"
