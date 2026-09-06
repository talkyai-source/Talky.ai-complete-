"""CRM sync service — logs a finished call into every connected CRM.

The service is exercised with the SQL and connector-resolution seams
replaced, so each test asserts the CRM-facing behaviour: which record the
call is logged against, what body/disposition goes out, idempotency across
the settlement and summary hooks, and the forced-refresh retry on a 401.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from app.infrastructure.connectors.base import ConnectorProviderError  # noqa: E402
from app.services import crm_sync_service as svc  # noqa: E402
from app.services.crm_sync_service import (  # noqa: E402
    CRMNotConnectedWarning,
    CRMSyncResult,
    CRMSyncService,
    build_call_body,
    outcome_label,
    provider_outcome,
)

TENANT = "66666666-6666-6666-6666-666666666666"
CALL = "77777777-7777-7777-7777-777777777777"
LEAD = "88888888-8888-8888-8888-888888888888"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeConnector:
    """Records the CRMProvider calls the sync makes."""

    def __init__(self, provider="salesforce", *, found=None, config=None, fail_first_with=None):
        self.provider = provider
        self.found = found
        self.config = config or {}
        self.calls = []
        self._fail_first_with = fail_first_with

    async def search_contact(self, email=None, phone=None):
        self.calls.append(("search", email, phone))
        return self.found

    async def create_contact(self, email, first_name=None, last_name=None, phone=None, properties=None):
        self.calls.append(("create", email, first_name, last_name, phone, properties))
        return {"id": "00Qcreated", "object": "Lead"}

    async def log_call(self, contact_id, call_body, duration_seconds, outcome="COMPLETED", call_direction="OUTBOUND", timestamp=None):
        if self._fail_first_with is not None:
            exc = self._fail_first_with
            self._fail_first_with = None
            raise exc
        self.calls.append(("log", contact_id, call_body, duration_seconds, outcome, call_direction))
        return "00Ttask"

    async def update_call_log(self, call_log_id, *, call_body=None, outcome=None):
        self.calls.append(("update", call_log_id, call_body, outcome))
        return True


def _call_row(**over):
    row = {
        "id": CALL, "tenant_id": TENANT, "campaign_id": "camp-1", "lead_id": LEAD,
        "phone_number": "+15550100100", "direction": "outbound", "status": "completed",
        "outcome": "answered", "duration_seconds": 95, "transcript": "Agent: hi\nUser: hello",
        "summary": None, "summary_json": None, "recording_url": None, "crm_call_id": None,
        "crm_synced_at": None, "started_at": datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc),
        "answered_at": None, "ended_at": None, "created_at": None,
    }
    row.update(over)
    return row


def _lead_row(**over):
    row = {"id": LEAD, "first_name": "Pat", "last_name": "Lee", "email": "pat@acme.com",
           "phone_number": "+15550100100", "crm_contact_id": None, "custom_fields": {"company": "ACME"},
           "company_name": None, "job_title": "CTO"}
    row.update(over)
    return row


@pytest.fixture
def harness(monkeypatch):
    """A CRMSyncService with every DB/resolver seam replaced."""
    state = {
        "providers": ["salesforce"], "connectors": {}, "call": _call_row(), "lead": _lead_row(),
        "marked": [], "remembered": [], "resolved": [],
    }
    service = CRMSyncService(db_client=object(), db_pool=object())

    monkeypatch.setattr(svc, "list_active_connector_providers", lambda db, t, typ: list(state["providers"]))

    async def _connector(tenant_id, provider, *, force_refresh=False):
        state["resolved"].append((provider, force_refresh))
        return state["connectors"][provider]

    async def _load_call(tenant_id, call_id):
        return state["call"]

    async def _load_lead(tenant_id, lead_id):
        return state["lead"] if lead_id else None

    async def _campaign_name(tenant_id, campaign_id):
        return "Spring promo"

    async def _remember(tenant_id, lead_id, provider, contact_id, existing_ids):
        state["remembered"].append((lead_id, provider, contact_id))

    async def _mark(tenant_id, call_id, crm_call_id):
        state["marked"].append((call_id, crm_call_id))

    monkeypatch.setattr(service, "_connector", _connector)
    monkeypatch.setattr(service, "_load_call", _load_call)
    monkeypatch.setattr(service, "_load_lead", _load_lead)
    monkeypatch.setattr(service, "_campaign_name", _campaign_name)
    monkeypatch.setattr(service, "_remember_contact_id", _remember)
    monkeypatch.setattr(service, "_mark_call_synced", _mark)
    state["service"] = service
    return state


# ---------------------------------------------------------------------------
# Pure shaping
# ---------------------------------------------------------------------------

def test_outcome_label_prefers_the_ai_summary_then_the_telephony_outcome():
    assert outcome_label("no_answer") == "No answer"
    assert outcome_label("customer_hung_up") == "Completed"
    assert outcome_label("answered", {"outcome": "qualified | wants a demo"}) == "Qualified"
    assert outcome_label("answered", {"outcome": "callback: next Tuesday"}) == "Callback"
    assert outcome_label(None) == "Completed"


def test_provider_outcome_gives_hubspot_its_enum_and_salesforce_free_text():
    assert provider_outcome("hubspot", "no_answer") == "NO_ANSWER"
    assert provider_outcome("hubspot", "weird") == "COMPLETED"
    assert provider_outcome("salesforce", "no_answer") == "No answer"
    assert provider_outcome("salesforce", "answered", {"outcome": "disqualified - budget"}) == "Disqualified"


def test_build_call_body_carries_outcome_campaign_summary_recording_and_excerpt():
    call = _call_row(recording_url="https://rec/1.wav", transcript="x" * 2000)
    body = build_call_body(call, {"headline": "Qualified - demo Tue", "outcome": "qualified | wants a demo", "next_step": "Send deck", "follow_up_tips": ["Call at 10"]},
                           campaign_name="Spring promo")
    assert body.startswith("Talky.ai outbound call - Qualified")
    assert "Campaign: Spring promo" in body
    assert "Duration: 1m 35s" in body
    assert "Summary: Qualified - demo Tue" in body and "Next step: Send deck" in body and "- Call at 10" in body
    assert "Recording: https://rec/1.wav" in body
    assert "Transcript excerpt:" in body
    assert ("x" * 1500 + " ...") in body and ("x" * 1501) not in body  # excerpt is capped
    assert f"Talky.ai call id: {CALL}" in body


def test_result_and_warning_shapes_are_stable():
    r = CRMSyncResult(success=False, call_id=CALL, skipped=True, skipped_reason="no_crm_connected",
                      warning_message=CRMNotConnectedWarning.MISSING_CRM)
    assert r.skipped and "Connectors page" in r.warning_message
    assert "Salesforce" in CRMNotConnectedWarning.MISSING_CRM


# ---------------------------------------------------------------------------
# Sync behaviour
# ---------------------------------------------------------------------------

def test_no_crm_connected_is_a_skip_with_operator_guidance(harness):
    harness["providers"] = []
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success is False and out.skipped and out.skipped_reason == "no_crm_connected"


def test_known_salesforce_id_on_the_lead_is_used_without_searching(harness):
    conn = FakeConnector()
    harness["connectors"]["salesforce"] = conn
    harness["lead"] = _lead_row(custom_fields={"crm_ids": {"salesforce": "00Qknown"}})
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success and out.providers == ["salesforce"]
    assert out.crm_contact_id == "00Qknown" and out.crm_call_id == "00Ttask"
    kinds = [c[0] for c in conn.calls]
    assert kinds == ["log"]  # no search, no create
    log = conn.calls[0]
    assert log[1] == "00Qknown" and log[3] == 95 and log[4] == "Answered" and log[5] == "OUTBOUND"
    assert "Campaign: Spring promo" in log[2]
    assert harness["marked"] == [(CALL, "00Ttask")]


def test_unknown_callee_is_searched_then_created_and_remembered(harness):
    conn = FakeConnector(found=None)
    harness["connectors"]["salesforce"] = conn
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success and out.crm_contact_id == "00Qcreated"
    kinds = [c[0] for c in conn.calls]
    assert kinds == ["search", "create", "log"]
    search = conn.calls[0]
    assert search[1] == "pat@acme.com" and search[2] == "+15550100100"
    create = conn.calls[1]
    assert create[2] == "Pat" and create[3] == "Lee" and create[4] == "+15550100100"
    assert create[5]["company"] == "ACME" and create[5]["Title"] == "CTO"
    assert "Created by Talky.ai" in create[5]["description"]
    assert harness["remembered"] == [(LEAD, "salesforce", "00Qcreated")]


def test_existing_crm_record_is_reused_not_duplicated(harness):
    conn = FakeConnector(found={"id": "003existing", "object": "Contact"})
    harness["connectors"]["salesforce"] = conn
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.crm_contact_id == "003existing"
    assert [c[0] for c in conn.calls] == ["search", "log"]


def test_create_leads_disabled_skips_unknown_callees(harness):
    conn = FakeConnector(found=None, config={"create_leads": False})
    harness["connectors"]["salesforce"] = conn
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success is False and "creation disabled" in (out.error_message or "")
    assert [c[0] for c in conn.calls] == ["search"]


def test_log_calls_disabled_and_inbound_opt_out_are_honoured(harness):
    conn = FakeConnector(config={"log_calls": False})
    harness["connectors"]["salesforce"] = conn
    assert _run(harness["service"].sync_call(TENANT, CALL)).providers == []
    assert conn.calls == []

    conn2 = FakeConnector(config={"sync_inbound": False})
    harness["connectors"]["salesforce"] = conn2
    harness["call"] = _call_row(direction="inbound")
    assert _run(harness["service"].sync_call(TENANT, CALL)).providers == []
    assert conn2.calls == []


def test_settlement_pass_is_idempotent_once_a_log_exists(harness):
    conn = FakeConnector()
    harness["connectors"]["salesforce"] = conn
    harness["call"] = _call_row(crm_call_id="00Talready")
    out = _run(harness["service"].sync_call(TENANT, CALL, reason="settlement"))
    assert out.success and out.skipped and out.skipped_reason == "already_synced"
    assert conn.calls == [] and harness["marked"] == []


def test_summary_pass_amends_the_existing_log_with_the_ai_summary(harness):
    conn = FakeConnector()
    harness["connectors"]["salesforce"] = conn
    harness["call"] = _call_row(crm_call_id="00Tfirst", summary_json={"headline": "Qualified", "outcome": "qualified | demo", "next_step": "Send deck"})
    out = _run(harness["service"].sync_call(TENANT, CALL, reason="summary"))
    assert out.success and out.updated_existing and out.crm_call_id == "00Tfirst"
    assert len(conn.calls) == 1 and conn.calls[0][0] == "update"
    _, log_id, body, outcome = conn.calls[0]
    assert log_id == "00Tfirst" and outcome == "Qualified"
    assert "Summary: Qualified" in body and "Next step: Send deck" in body
    assert harness["marked"] == [(CALL, "00Tfirst")]


def test_summary_pass_creates_the_log_when_settlement_never_ran(harness):
    """Inbound calls settle inside the inbound lifecycle (no CRM hook); the
    summary hook must therefore create, not amend."""
    conn = FakeConnector(found={"id": "003c"})
    harness["connectors"]["salesforce"] = conn
    harness["call"] = _call_row(direction="inbound", crm_call_id=None, summary_json={"headline": "Booked", "outcome": "qualified"})
    out = _run(harness["service"].sync_call(TENANT, CALL, reason="summary"))
    assert out.success and out.crm_call_id == "00Ttask"
    log = [c for c in conn.calls if c[0] == "log"][0]
    assert log[5] == "INBOUND" and log[4] == "Qualified"


def test_authentication_failure_forces_one_refresh_and_retries(harness):
    stale = FakeConnector(found={"id": "003c"}, fail_first_with=ConnectorProviderError(
        provider="salesforce", operation="create_task", category="authentication", message="INVALID_SESSION_ID", status_code=401,
    ))
    harness["connectors"]["salesforce"] = stale
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success and out.crm_call_id == "00Ttask"
    # resolved once normally, then once with force_refresh=True
    assert harness["resolved"] == [("salesforce", False), ("salesforce", True)]


def test_non_auth_provider_error_is_reported_not_retried(harness):
    conn = FakeConnector(found={"id": "003c"}, fail_first_with=ConnectorProviderError(
        provider="salesforce", operation="create_task", category="rate_limit", message="REQUEST_LIMIT_EXCEEDED", status_code=403,
    ))
    harness["connectors"]["salesforce"] = conn
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success is False and "rate_limit" in out.error_message
    assert harness["resolved"] == [("salesforce", False)]
    assert harness["marked"] == []


def test_both_crms_are_logged_and_one_failure_does_not_block_the_other(harness):
    harness["providers"] = ["salesforce", "hubspot"]
    sf = FakeConnector(found={"id": "003sf"})
    hs = FakeConnector(provider="hubspot", found={"id": "hs-1"})
    harness["connectors"] = {"salesforce": sf, "hubspot": hs}
    harness["call"] = _call_row(outcome="no_answer", transcript=None, duration_seconds=0)
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success and out.providers == ["salesforce", "hubspot"]
    sf_log = [c for c in sf.calls if c[0] == "log"][0]
    hs_log = [c for c in hs.calls if c[0] == "log"][0]
    assert sf_log[4] == "No answer"      # Salesforce free-text disposition
    assert hs_log[4] == "NO_ANSWER"      # HubSpot enum
    # Legacy single crm_contact_id is NOT trusted when two CRMs are active.
    assert harness["remembered"] == [(LEAD, "salesforce", "003sf"), (LEAD, "hubspot", "hs-1")]


def test_hubspot_without_an_email_cannot_create_but_salesforce_can(harness):
    harness["providers"] = ["hubspot", "salesforce"]
    hs = FakeConnector(provider="hubspot", found=None)
    sf = FakeConnector(found=None)
    harness["connectors"] = {"hubspot": hs, "salesforce": sf}
    harness["lead"] = _lead_row(email=None)
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.providers == ["salesforce"]
    assert [c[0] for c in hs.calls] == ["search"]
    assert [c[0] for c in sf.calls] == ["search", "create", "log"]


def test_schedule_without_a_running_loop_is_a_noop():
    svc.schedule_crm_sync(CALL, tenant_id=TENANT)  # must not raise
    svc.schedule_crm_sync(None)


def test_get_crm_sync_service_returns_one_instance_per_client():
    a = object()
    s1 = svc.get_crm_sync_service(a)
    assert svc.get_crm_sync_service(a) is s1
    assert svc.get_crm_sync_service(object()) is not s1
