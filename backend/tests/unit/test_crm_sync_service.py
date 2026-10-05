"""CRM sync service — logs a finished call into every connected CRM.

The service is exercised with the SQL and connector-resolution seams
replaced, so each test asserts the CRM-facing behaviour: which record the
call is logged against, what body/disposition goes out, idempotency across
the settlement and summary hooks, and the forced-refresh retry on a 401.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock

from app.infrastructure.connectors.base import ConnectorProviderError  # noqa: E402
from app.services import crm_sync_service as svc  # noqa: E402
from app.domain.services.lead_capture_service import LeadCaptureService
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
    return asyncio.run(coro)


class FakeConnector:
    """Records the CRMProvider calls the sync makes."""

    def __init__(self, provider="salesforce", *, found=None, config=None, fail_first_with=None):
        self.provider = provider
        self.connector_id = '11111111-1111-1111-1111-111111111111'
        self.external_account_id = provider + '-account'
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
        "id": CALL, "source_revision": "1", "tenant_id": TENANT, "campaign_id": "camp-1", "lead_id": LEAD,
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


class MemoryDeliveries:
    """The delivery persistence seam, retained when service instances restart."""
    def __init__(self):
        self.rows = {}

    async def enqueue(self, tenant_id, call_id, provider, desired_key, *, legacy_id=None, source_revision=None):
        key = (tenant_id, call_id, provider)
        row = self.rows.get(key)
        if row is None:
            row = dict(tenant_id=tenant_id, call_id=call_id, provider=provider,
                desired_key=desired_key, completed_key=None, remote_contact_id=None,
                remote_call_id=None, status='unknown' if legacy_id else 'pending',
                phase='legacy_unverified' if legacy_id else 'pending', attempts=0)
            self.rows[key] = row
        elif row['desired_key'] != desired_key:
            row['desired_key'] = desired_key
            if row['status'] not in ('processing','unknown'):
                row.update(status='pending', attempts=0)
        return dict(row)

    async def claim(self, tenant_id, call_id, provider, *, source_revision=None, expected_key=None):
        row = self.rows[(tenant_id,call_id,provider)]
        if row['status'] not in ('pending','unknown') or row['attempts'] >= 6:
            return None
        receipt = dict(row)
        receipt['reconcile'] = row['status'] == 'unknown'
        row.update(status='processing', attempts=row['attempts']+1, lease_token='lease')
        receipt.update(attempts=row['attempts'], lease_token='lease')
        return receipt

    async def save(self, receipt, *, phase=None, status=None, contact_id=None, call_id=None, error=None):
        row = self.rows[(receipt['tenant_id'],receipt['call_id'],receipt['provider'])]
        for key, value in [('phase',phase),('status',status),('remote_contact_id',contact_id),('remote_call_id',call_id)]:
            if value is not None:
                row[key] = value
                receipt[key] = value
        row['last_error'] = error
        if status == 'succeeded':
            row['completed_key'] = receipt['desired_key']
            if row['desired_key'] != receipt['desired_key']:
                row['status'] = 'pending'

    async def bind_destination(self, receipt, connector_id, account_id):
        row = self.rows[(receipt['tenant_id'], receipt['call_id'], receipt['provider'])]
        saved = (row.get('destination_connector_id'), row.get('destination_account_id'))
        if saved != (connector_id, account_id):
            if saved != (None, None) or row.get('remote_call_id') or row.get('remote_contact_id'):
                return False
        row.update(destination_connector_id=connector_id, destination_account_id=account_id)
        receipt.update(destination_connector_id=connector_id, destination_account_id=account_id)
        return True

    async def bind_contact_effect(self, receipt, effect):
        row = self.rows[(receipt['tenant_id'], receipt['call_id'], receipt['provider'])]
        if row.get('contact_effect') not in (None, effect):
            raise RuntimeError('Original contact effect cannot change')
        row.update(contact_effect=effect)
        receipt.update(contact_effect=effect)

    async def begin_contact_create(self, receipt, effect, *, source_revision=None):
        row = self.rows[(receipt['tenant_id'], receipt['call_id'], receipt['provider'])]
        assert row.get('contact_effect') == effect
        row['phase'] = receipt['phase'] = 'creating_contact'


@pytest.fixture
def harness(monkeypatch):
    """A CRMSyncService with every DB/resolver seam replaced."""
    state = {
        "providers": ["salesforce"], "connectors": {}, "call": _call_row(), "lead": _lead_row(),
        "marked": [], "remembered": [], "resolved": [],
        "call_details": [], "lead_details": [],
    }
    service = CRMSyncService(db_client=object(), db_pool=object())
    service.deliveries = MemoryDeliveries()
    state["deliveries"] = service.deliveries

    monkeypatch.setattr(svc, "list_active_connector_providers", lambda db, t, typ: list(state["providers"]))

    async def _connector(tenant_id, provider, *, force_refresh=False, connector_id=None):
        state["resolved"].append((provider, force_refresh))
        return state["connectors"][provider]

    async def _load_call(tenant_id, call_id):
        row = dict(state["call"])
        # Ordinary fixtures represent a summary of the supplied source. Tests
        # for stale or legacy evidence explicitly supply their different hash.
        row.setdefault("summary_transcript_hash", svc.transcript_revision(row))
        return row

    async def _load_lead(tenant_id, lead_id):
        return state["lead"] if lead_id else None

    async def _campaign_name(tenant_id, campaign_id):
        return "Spring promo"

    async def _remember(tenant_id, lead_id, provider, contact_id, existing_ids, **destination):
        state["remembered"].append((lead_id, provider, contact_id))

    async def _mark(tenant_id, call_id, crm_call_id):
        state["marked"].append((call_id, crm_call_id))

    async def _stamp(tenant_id, call_id, connector_id, provider):
        connector = state["connectors"][provider]
        return copy.deepcopy({
            "call_revision": state["call"].get("source_revision"),
            "lead": state["lead"], "call_details": state["call_details"],
            "lead_details": state["lead_details"],
            "external_account_id": connector.external_account_id,
            "connector_config": connector.config,
            "connector_id": connector.connector_id,
        })

    monkeypatch.setattr(service, "_connector", _connector)
    monkeypatch.setattr(service, "_load_call", _load_call)
    monkeypatch.setattr(service, "_load_lead", _load_lead)
    monkeypatch.setattr(service, "_campaign_name", _campaign_name)
    monkeypatch.setattr(service, "_remember_contact_id", _remember)
    monkeypatch.setattr(service, "_mark_call_synced", _mark)
    monkeypatch.setattr(service, "_write_admission_stamp", _stamp)
    async def call_details(_service, *_args):
        return state["call_details"]
    async def lead_details(_service, *_args):
        return state["lead_details"]
    monkeypatch.setattr(LeadCaptureService, "details_for_call", call_details)
    monkeypatch.setattr(LeadCaptureService, "details_for_lead", lead_details)
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
    harness["lead"] = _lead_row(custom_fields={"crm_ids": {"salesforce": "00Qknown"},
        "crm_destinations": {"salesforce": {"account_id": conn.external_account_id, "contact_id": "00Qknown"}}})
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
    assert out.success is False and "invalid_request" in (out.error_message or "")
    assert harness["deliveries"].rows[(TENANT, CALL, "salesforce")]["status"] == "failed"
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


def test_settlement_pass_is_idempotent_once_a_provider_receipt_exists(harness):
    conn = FakeConnector(found={"id": "003c"})
    harness["connectors"]["salesforce"] = conn
    first = _run(harness["service"].sync_call(TENANT, CALL))
    second = _run(harness["service"].sync_call(TENANT, CALL))
    assert first.success and second.success
    assert len([c for c in conn.calls if c[0] == "log"]) == 1


def test_summary_pass_amends_only_its_provider_receipt(harness):
    conn = FakeConnector(found={"id": "003c"})
    harness["connectors"]["salesforce"] = conn
    _run(harness["service"].sync_call(TENANT, CALL))
    conn.calls.clear()
    harness["call"]["summary_json"] = {"headline": "Qualified", "outcome": "qualified | demo", "next_step": "Send deck"}
    out = _run(harness["service"].sync_call(TENANT, CALL, reason="summary"))
    assert out.success and out.updated_existing and out.crm_call_id == "00Ttask"
    assert len(conn.calls) == 1 and conn.calls[0][0] == "update"
    _, log_id, body, outcome = conn.calls[0]
    assert log_id == "00Ttask" and outcome == "Qualified"
    assert "Summary: Qualified" in body and "Next step: Send deck" in body


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
    # Resolve initially and revalidate immediately before the write, then one
    # bounded forced refresh after the provider rejects authentication.
    assert harness["resolved"] == [("salesforce", False), ("salesforce", False), ("salesforce", True)]


@pytest.mark.parametrize('switch', ['connector', 'account'])
def test_account_switch_during_auth_retry_never_replays_the_remote_write(harness, monkeypatch, switch):
    original = FakeConnector(found={'id': 'original-contact'}, fail_first_with=ConnectorProviderError(
        provider='salesforce', operation='log_call', category='authentication', message='expired', status_code=401))
    replacement = FakeConnector(found={'id': 'different-contact'})
    if switch == 'connector': replacement.connector_id = '22222222-2222-2222-2222-222222222222'
    else: replacement.external_account_id = 'different-org'
    async def resolve(tenant, provider, *, force_refresh=False, connector_id=None):
        if force_refresh:
            assert connector_id == original.connector_id
        return replacement if force_refresh else original
    monkeypatch.setattr(harness['service'], '_connector', resolve)
    result = _run(harness['service'].sync_call(TENANT, CALL))
    assert not result.success and not replacement.calls
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['status'] == 'unknown' and row['destination_account_id'] == original.external_account_id


def test_account_switch_between_summary_updates_does_not_touch_old_remote_id(harness):
    original = FakeConnector(found={'id': 'old-contact'})
    harness['connectors']['salesforce'] = original
    assert _run(harness['service'].sync_call(TENANT, CALL)).success
    replacement = FakeConnector(found={'id': 'new-contact'})
    replacement.external_account_id = 'new-org'
    harness['connectors']['salesforce'] = replacement
    harness['call']['summary_json'] = {'headline': 'new summary'}
    assert not _run(harness['service'].sync_call(TENANT, CALL)).success
    assert replacement.calls == []
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['status'] == 'unknown' and row['remote_call_id'] == '00Ttask'


@pytest.mark.asyncio
async def test_legacy_connection_without_saved_identity_is_verified_before_binding(monkeypatch):
    connector = FakeConnector()
    connector.external_account_id = None
    connector.fetch_account_identity = AsyncMock(return_value={'external_account_id': 'verified-org'})
    resolver = AsyncMock(return_value=(connector, connector.connector_id, 'salesforce'))
    monkeypatch.setattr(svc, 'resolve_active_connector', resolver)
    service = CRMSyncService(object(), object())
    resolved = await service._connector(TENANT, 'salesforce', connector_id=connector.connector_id)
    assert resolved.external_account_id == 'verified-org'
    connector.fetch_account_identity.assert_awaited_once()
    assert resolver.await_args.kwargs['connector_id'] == connector.connector_id


@pytest.mark.parametrize('custom', [
    {'crm_ids': {'salesforce': 'unowned-id'}},
    {'crm_destinations': {'salesforce': {'account_id': 'different-org', 'contact_id': 'wrong-id'}}},
])
def test_unowned_or_other_account_lead_ids_are_resolved_in_current_account(harness, custom):
    connector = FakeConnector(found={'id': 'verified-current-contact'})
    harness['connectors']['salesforce'] = connector
    harness['lead'] = _lead_row(custom_fields=custom, crm_contact_id='legacy-unowned-id')
    result = _run(harness['service'].sync_call(TENANT, CALL))
    assert result.success and result.crm_contact_id == 'verified-current-contact'
    assert [call[0] for call in connector.calls] == ['search', 'log']


def test_non_auth_provider_error_is_reported_not_retried(harness):
    conn = FakeConnector(found={"id": "003c"}, fail_first_with=ConnectorProviderError(
        provider="salesforce", operation="create_task", category="rate_limit", message="REQUEST_LIMIT_EXCEEDED", status_code=403,
    ))
    harness["connectors"]["salesforce"] = conn
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.success is False and "rate_limit" in out.error_message
    assert harness["resolved"] == [("salesforce", False), ("salesforce", False)]
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


def test_both_destinations_keep_their_own_ids_across_summary_and_restart(harness):
    class Destination(FakeConnector):
        async def log_call(self, *args, **kwargs):
            await super().log_call(*args, **kwargs)
            return self.provider + '-call'
    harness['providers'] = ['salesforce', 'hubspot']
    sf = Destination('salesforce', found={'id': 'sf-contact'})
    hs = Destination('hubspot', found={'id': 'hs-contact'})
    harness['connectors'] = {'salesforce': sf, 'hubspot': hs}
    assert _run(harness['service'].sync_call(TENANT, CALL)).success
    harness['call']['crm_call_id'] = 'salesforce-call'  # legacy column cannot route HubSpot
    harness['call']['summary_json'] = {'headline': 'A new summary'}
    # Restart loses all per-instance state except the durable store.
    restarted = CRMSyncService(object(), object())
    for name in ('_connector', '_load_call', '_load_lead', '_campaign_name', '_remember_contact_id', '_mark_call_synced', '_write_admission_stamp'):
        setattr(restarted, name, getattr(harness['service'], name))
    restarted.deliveries = harness['deliveries']
    assert _run(restarted.sync_call(TENANT, CALL, reason='summary')).success
    assert [c[1] for c in sf.calls if c[0] == 'update'] == ['salesforce-call']
    assert [c[1] for c in hs.calls if c[0] == 'update'] == ['hubspot-call']
    assert len([c for c in sf.calls if c[0] == 'log']) == 1
    assert len([c for c in hs.calls if c[0] == 'log']) == 1


def test_one_failed_provider_retries_independently_without_recreating_success(harness):
    harness['providers'] = ['salesforce', 'hubspot']
    sf = FakeConnector(found={'id': 'sf'})
    hs = FakeConnector('hubspot', found={'id': 'hs'}, fail_first_with=ConnectorProviderError(
        provider='hubspot', operation='log_call', category='rate_limit', message='limited', status_code=429))
    harness['connectors'] = {'salesforce': sf, 'hubspot': hs}
    first = _run(harness['service'].sync_call(TENANT, CALL))
    assert not first.success and first.providers == ['salesforce']
    second = _run(harness['service'].sync_call(TENANT, CALL))
    assert second.success
    assert len([c for c in sf.calls if c[0] == 'log']) == 1
    assert len([c for c in hs.calls if c[0] == 'log']) == 1


def test_lost_remote_success_reconciles_without_repeating_create(harness):
    class LostResponse(FakeConnector):
        async def log_call(self, *args, **kwargs):
            await super().log_call(*args, **kwargs)
            raise TimeoutError('response lost after remote write')
        async def find_call_by_reference(self, reference):
            self.calls.append(('reconcile', reference))
            return 'recovered-call'
    connector = LostResponse(found={'id': 'contact'})
    harness['connectors']['salesforce'] = connector
    assert not _run(harness['service'].sync_call(TENANT, CALL)).success
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['status'] == 'unknown' and row['phase'] == 'creating_call'
    second = _run(harness['service'].sync_call(TENANT, CALL))
    assert second.success and second.crm_call_id == 'recovered-call'
    assert len([c for c in connector.calls if c[0] == 'log']) == 1
    assert ('reconcile', CALL) in connector.calls


def test_unknown_create_without_reconciliation_evidence_is_never_resent(harness):
    class Unknown(FakeConnector):
        async def log_call(self, *args, **kwargs):
            await super().log_call(*args, **kwargs)
            raise TimeoutError('lost response')
        async def find_call_by_reference(self, reference):
            return None
    connector = Unknown(found={'id': 'contact'})
    harness['connectors']['salesforce'] = connector
    for _ in range(8):
        assert not _run(harness['service'].sync_call(TENANT, CALL)).success
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['attempts'] == 6 and row['status'] == 'unknown'
    assert len([c for c in connector.calls if c[0] == 'log']) == 1


def test_legacy_shared_id_is_held_instead_of_guessed_or_duplicated(harness):
    harness['providers'] = ['salesforce', 'hubspot']
    harness['connectors'] = {name: FakeConnector(name) for name in harness['providers']}
    harness['call']['crm_call_id'] = 'legacy-owner-unknown'
    out = _run(harness['service'].sync_call(TENANT, CALL, reason='summary'))
    assert not out.success
    assert all(c.calls == [] for c in harness['connectors'].values())
    assert all(row['status'] == 'unknown' for row in harness['deliveries'].rows.values())


def test_false_update_is_failure_and_does_not_publish_success_receipt(harness):
    connector = FakeConnector(found={'id': 'contact'})
    harness['connectors']['salesforce'] = connector
    assert _run(harness['service'].sync_call(TENANT, CALL)).success
    async def noop(*args, **kwargs):
        return False
    connector.update_call_log = noop
    harness['call']['summary_json'] = {'headline': 'new'}
    result = _run(harness['service'].sync_call(TENANT, CALL, reason='summary'))
    assert not result.success and not result.updated_existing
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['status'] == 'pending' and row['completed_key'] != row['desired_key']


def test_retry_exhaustion_is_visible_and_bounded(harness):
    connector = FakeConnector(found={'id': 'contact'})
    async def unavailable(*args, **kwargs):
        raise ConnectorProviderError(provider='salesforce', operation='log_call',
            category='rate_limit', message='limited', status_code=429)
    connector.log_call = unavailable
    harness['connectors']['salesforce'] = connector
    for _ in range(8):
        assert not _run(harness['service'].sync_call(TENANT, CALL)).success
    row = harness['deliveries'].rows[(TENANT, CALL, 'salesforce')]
    assert row['status'] == 'failed' and row['attempts'] == 6
