"""Salesforce endpoints — webhook verification, callback enqueue, Outbound Message parsing."""
from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import salesforce as sf  # noqa: E402

TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _isolated_tenant_context():
    """_verify_webhook sets the RLS tenant contextvar; never leak it into
    other tests (the IDOR suite asserts an empty ambient context)."""
    from app.core.security.tenant_isolation import clear_tenant_context

    clear_tenant_context()
    yield
    clear_tenant_context()
TOKEN = "tok_secret_value_123456"
CAMPAIGN = "33333333-3333-3333-3333-333333333333"


def _resp(data=None, error=None, count=None):
    return SimpleNamespace(data=data, error=error, count=count)


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.op = "select"
        self.filters = []
        self.payload = None

    def select(self, *a, **k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.db.calls.append((self.table_name, self.op, list(self.filters), self.payload))
        queue = self.db.responses.get((self.table_name, self.op)) or []
        if queue:
            return queue.pop(0)
        return _resp(data=[])


class FakeDB:
    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls = []

    def table(self, name):
        return _Query(self, name)


def _connector_row(config=None, status="active"):
    cfg = {"webhook_token_hash": hashlib.sha256(TOKEN.encode()).hexdigest(), "org_id": "00D000000000001EAA"}
    cfg.update(config or {})
    return {"id": "conn-1", "status": status, "config": cfg, "created_at": "2026-09-07T00:00:00Z"}


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------

def test_verify_webhook_accepts_the_stored_token_and_sets_tenant_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.core.security.tenant_isolation.set_current_tenant_id", lambda t: seen.setdefault("tenant", t)
    )
    db = FakeDB({("connectors", "select"): [_resp([_connector_row()])]})
    row, config = sf._verify_webhook(db, TENANT, TOKEN)
    assert row["id"] == "conn-1" and config["org_id"] == "00D000000000001EAA"
    assert seen["tenant"] == TENANT
    # The lookup is provider- and tenant-scoped.
    filters = db.calls[0][2]
    assert ("eq", "tenant_id", TENANT) in filters and ("eq", "provider", "salesforce") in filters


@pytest.mark.parametrize("bad", ["wrong", "", TOKEN + "x"])
def test_verify_webhook_rejects_bad_tokens(bad):
    db = FakeDB({("connectors", "select"): [_resp([_connector_row()])]})
    with pytest.raises(HTTPException) as exc:
        sf._verify_webhook(db, TENANT, bad)
    assert exc.value.status_code == 401


def test_verify_webhook_rejects_non_uuid_tenant_without_touching_the_db():
    db = FakeDB({})
    with pytest.raises(HTTPException) as exc:
        sf._verify_webhook(db, "not-a-uuid", TOKEN)
    assert exc.value.status_code == 401
    assert db.calls == []


def test_verify_webhook_rejects_when_no_active_connector():
    db = FakeDB({("connectors", "select"): [_resp([_connector_row(status="revoked")])]})
    with pytest.raises(HTTPException) as exc:
        sf._verify_webhook(db, TENANT, TOKEN)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Callback enqueue
# ---------------------------------------------------------------------------

@pytest.fixture
def campaign_ok(monkeypatch):
    calls = {}

    def fake_require(db, campaign_id, *, tenant_id, extra_columns=()):
        calls["campaign_id"] = campaign_id
        calls["tenant_id"] = tenant_id
        return {"id": campaign_id, "tenant_id": tenant_id, "direction": "outbound",
                "status": calls.get("status", "running"), "name": "Callbacks", "script_config": {}}

    monkeypatch.setattr(sf, "require_owned_outbound_campaign", fake_require)
    monkeypatch.setattr(
        "app.api.v1.endpoints.contact_lists.create_contact_list",
        lambda db, **kw: calls.setdefault("list", ("list-1", kw))[0],
    )
    return calls


@pytest.fixture
def dispatch(monkeypatch):
    """Capture start_campaign + ingest calls."""
    state = {"start": None, "ingest": None}

    class FakeService:
        async def start_campaign(self, **kw):
            state["start"] = kw
            return SimpleNamespace(jobs_enqueued=1)

    monkeypatch.setattr("app.api.v1.endpoints.campaigns._get_campaign_service", lambda db: FakeService())

    def fake_ingest(db, *, campaign_id, tenant_id, records, normalize, list_id=None, **_kw):
        state["ingest"] = {"campaign_id": campaign_id, "tenant_id": tenant_id, "records": records, "list_id": list_id}
        return SimpleNamespace(imported=1, revived=0, duplicates_skipped=0, invalid=0, errors=[])

    monkeypatch.setattr("app.domain.services.dialer.bulk_ingest.ingest_lead_records", fake_ingest)
    return state


@pytest.mark.asyncio
async def test_new_lead_is_ingested_into_the_callback_list_and_dialed_now(campaign_ok, dispatch):
    db = FakeDB({
        ("leads", "select"): [_resp([]), _resp([{"id": "lead-9"}])],
        ("leads", "update"): [_resp([{"id": "lead-9"}])],
    })
    payload = sf.CallbackRequest(
        phone="+1 (555) 010-0100", record_id="00Q000000000001", object_type="Lead",
        first_name="Pat", last_name="Lee", email="pat@acme.com", company="ACME", notes="Asked for a call back",
    )
    out = await sf._enqueue_callback(db, tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN}, payload=payload)

    assert out.accepted and out.queued and out.jobs_enqueued == 1 and out.duplicate is False
    assert out.lead_id == "lead-9" and out.campaign_id == CAMPAIGN and out.list_id == "list-1"
    # Campaign guard saw the configured campaign under the tenant from the URL.
    assert campaign_ok["campaign_id"] == CAMPAIGN and campaign_ok["tenant_id"] == TENANT
    assert campaign_ok["list"][1]["name"] == sf.CALLBACK_LIST_NAME
    # The lead carries the Salesforce id so the post-call sync logs against it.
    rec = dispatch["ingest"]["records"][0]
    assert rec.phone_raw == "+15550100100"
    assert rec.custom_fields["crm_ids"] == {"salesforce": "00Q000000000001"}
    assert rec.custom_fields["source"] == "salesforce_callback"
    assert rec.calling_notes == "Asked for a call back"
    assert dispatch["ingest"]["list_id"] == "list-1"
    # Dialing reuses the "call this list" flow: scoped to the list, allowed while running.
    assert dispatch["start"] == {"campaign_id": CAMPAIGN, "tenant_id": TENANT, "list_id": "list-1", "allow_running": True}
    # crm_contact_id stamped on the new lead row.
    stamps = [c for c in db.calls if c[0] == "leads" and c[1] == "update"]
    assert stamps and stamps[0][3]["crm_contact_id"] == "00Q000000000001" and stamps[0][3]["priority"] == 8


@pytest.mark.asyncio
async def test_repeat_request_for_a_finished_lead_revives_it_and_redials(campaign_ok, dispatch):
    db = FakeDB({
        ("leads", "select"): [_resp([{"id": "lead-1", "status": "completed", "do_not_call": False, "custom_fields": {"company": "Old"}}])],
        ("leads", "update"): [_resp([{"id": "lead-1"}])],
    })
    payload = sf.CallbackRequest(phone="+15550100100", record_id="00Q1", priority=10)
    out = await sf._enqueue_callback(db, tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN}, payload=payload)

    assert out.duplicate is True and out.queued is True and out.lead_id == "lead-1"
    assert dispatch["ingest"] is None  # no second insert
    update = [c for c in db.calls if c[0] == "leads" and c[1] == "update"][0]
    assert update[3]["status"] == "pending"
    assert update[3]["priority"] == 10
    assert update[3]["crm_contact_id"] == "00Q1"
    assert update[3]["custom_fields"]["company"] == "Old"  # merged, not replaced
    assert ("eq", "tenant_id", TENANT) in update[2]


@pytest.mark.asyncio
async def test_do_not_call_lead_is_refused_and_never_dialed(campaign_ok, dispatch):
    db = FakeDB({("leads", "select"): [_resp([{"id": "lead-1", "status": "completed", "do_not_call": True, "custom_fields": {}}])]})
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(db, tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN},
                                   payload=sf.CallbackRequest(phone="+15550100100"))
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "do_not_call"
    assert dispatch["start"] is None


@pytest.mark.asyncio
async def test_paused_campaign_saves_the_lead_but_does_not_start_dialing(campaign_ok, dispatch):
    campaign_ok["status"] = "paused"
    db = FakeDB({("leads", "select"): [_resp([]), _resp([{"id": "lead-2"}])], ("leads", "update"): [_resp([{"id": "lead-2"}])]})
    out = await sf._enqueue_callback(db, tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN},
                                     payload=sf.CallbackRequest(phone="+15550100100"))
    assert out.accepted and out.queued is False and dispatch["start"] is None
    assert "paused" in out.message


@pytest.mark.asyncio
async def test_missing_callback_campaign_is_a_409_the_operator_can_act_on(dispatch):
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(FakeDB({}), tenant_id=TENANT, config={}, payload=sf.CallbackRequest(phone="+15550100100"))
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "callback_campaign_not_configured"


@pytest.mark.asyncio
async def test_inbound_campaign_can_never_be_a_callback_target(monkeypatch, dispatch):
    from app.api.v1.endpoints._outbound_campaign import outbound_campaign_conflict

    def refuse(db, campaign_id, *, tenant_id, extra_columns=()):
        raise outbound_campaign_conflict(campaign_id)

    monkeypatch.setattr(sf, "require_owned_outbound_campaign", refuse)
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(FakeDB({}), tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN},
                                   payload=sf.CallbackRequest(phone="+15550100100"))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert dispatch["start"] is None


@pytest.mark.asyncio
async def test_unparseable_phone_is_a_422(campaign_ok, dispatch):
    with pytest.raises(sf._CallbackConflict) as exc:
        await sf._enqueue_callback(FakeDB({}), tenant_id=TENANT, config={"callback_campaign_id": CAMPAIGN},
                                   payload=sf.CallbackRequest(phone="abc"))
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "invalid_phone"


# ---------------------------------------------------------------------------
# Outbound Message (SOAP)
# ---------------------------------------------------------------------------

SOAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <soapenv:Body>
  <notifications xmlns="http://soap.sforce.com/2005/09/outbound">
   <OrganizationId>00D000000000001EAA</OrganizationId>
   <ActionId>04k000000000001</ActionId>
   <SessionId xsi:nil="true"/>
   <EnterpriseUrl>https://acme.my.salesforce.com/services/Soap/c/60.0/00D000000000001</EnterpriseUrl>
   <Notification>
    <Id>04l000000000001</Id>
    <sObject xsi:type="sf:Lead" xmlns:sf="urn:sobject.enterprise.soap.sforce.com">
     <sf:Id>00Q000000000001AAA</sf:Id>
     <sf:FirstName>Pat</sf:FirstName>
     <sf:LastName>Lee</sf:LastName>
     <sf:Phone>(555) 010-0100</sf:Phone>
     <sf:Email>pat@acme.com</sf:Email>
     <sf:Company>ACME</sf:Company>
     <sf:Talky_Notes__c>Wants a demo</sf:Talky_Notes__c>
    </sObject>
   </Notification>
  </notifications>
 </soapenv:Body>
</soapenv:Envelope>"""


def test_parse_outbound_message_extracts_org_and_sobject_fields():
    org, records = sf.parse_outbound_message(SOAP)
    assert org == "00D000000000001EAA"
    assert len(records) == 1
    rec = records[0]
    assert rec["Id"] == "00Q000000000001AAA" and rec["Phone"] == "(555) 010-0100" and rec["__type"] == "Lead"
    payload = sf._callback_from_sobject(rec)
    assert payload.record_id == "00Q000000000001AAA"
    assert payload.first_name == "Pat" and payload.last_name == "Lee"
    assert payload.email == "pat@acme.com" and payload.company == "ACME"
    assert payload.notes == "Wants a demo" and payload.object_type == "Lead"


def test_parse_outbound_message_refuses_dtds_and_oversized_bodies():
    with pytest.raises(ValueError):
        sf.parse_outbound_message(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
    with pytest.raises(ValueError):
        sf.parse_outbound_message(b"<x>" + b"a" * (sf._MAX_XML_BYTES + 1) + b"</x>")
    with pytest.raises(ValueError):
        sf.parse_outbound_message(b"<not xml")


def test_sobject_without_a_phone_is_skipped():
    assert sf._callback_from_sobject({"Id": "00Q1", "LastName": "X"}) is None


def test_ack_envelope_is_what_salesforce_expects():
    assert "<Ack>true</Ack>" in sf._ACK_OK and "notificationsResponse" in sf._ACK_OK


# ---------------------------------------------------------------------------
# Settings shaping
# ---------------------------------------------------------------------------

def test_settings_from_config_defaults_and_overrides():
    assert sf._settings_from_config({}).model_dump() == {
        "callback_campaign_id": None, "log_calls": True, "create_leads": True, "sync_inbound": True, "callback_priority": 8,
    }
    s = sf._settings_from_config({"callback_campaign_id": CAMPAIGN, "log_calls": False, "callback_priority": 3})
    assert s.callback_campaign_id == CAMPAIGN and s.log_calls is False and s.callback_priority == 3


def test_masking_never_reveals_the_middle_of_a_token():
    masked = sf._mask("abcdefghijklmnopqrstuvwxyz")
    assert masked.startswith("abcd") and masked.endswith("wxyz") and "efgh" not in masked
    assert sf._mask(None) is None


def test_import_request_accepts_lead_or_contact_only():
    assert sf.ImportRequest(campaign_id=CAMPAIGN, object_type="contact").object_type == "contact"
    with pytest.raises(Exception):
        sf.ImportRequest(campaign_id=CAMPAIGN, object_type="Account")


# ---------------------------------------------------------------------------
# Security middleware must let Salesforce's SOAP (text/xml) reach the endpoint
# ---------------------------------------------------------------------------

def test_security_middleware_admits_text_xml_on_the_outbound_message_path():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.api_security_middleware import APISecurityMiddleware

    app = FastAPI()
    app.add_middleware(APISecurityMiddleware)

    @app.post("/api/v1/connectors/salesforce/outbound-message/{tenant_id}/{token}")
    async def om(tenant_id: str, token: str):
        return {"ok": True}

    @app.post("/api/v1/other")
    async def other():
        return {"ok": True}

    client = TestClient(app)
    headers = {"Content-Type": "text/xml; charset=UTF-8", "User-Agent": "SFDC-Callout/60.0"}
    ok = client.post(f"/api/v1/connectors/salesforce/outbound-message/{TENANT}/tok", content="<x/>", headers=headers)
    assert ok.status_code == 200, ok.text
    blocked = client.post("/api/v1/other", content="<x/>", headers=headers)
    assert blocked.status_code == 415
