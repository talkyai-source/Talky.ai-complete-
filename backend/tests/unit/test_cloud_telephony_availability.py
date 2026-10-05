"""Production selection must not silently originate through a different provider."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
import json

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import telephony_providers as providers
from app.core import db_utils
from app.domain.services.campaign_service import CampaignReadinessError, CampaignService
from app.domain.services.telephony import trunk_resolver
from tests.unit import test_call_redial_service as redials
from tests.unit import test_campaign_start_dispatch_truth as starts
from tests.unit import test_outbound_campaign_boundaries as calls


class Context:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class Conn:
    def __init__(self, provider="none", *, fail=False, missing=False, saved=False):
        self.provider, self.fail, self.missing, self.saved = provider, fail, missing, saved
        self.writes = []
        self.reads = []

    def transaction(self):
        return Context()

    async def execute(self, sql, *args):
        if "UPDATE tenants" in sql:
            self.writes.append(args)
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        self.reads.append((sql, args))
        if self.fail:
            raise RuntimeError("synthetic selection lookup unavailable")
        if "tenant_telephony_credentials" in sql:
            return {"status": "active"}
        assert "active_telephony_provider" in sql
        return None if self.missing else {
            "active_telephony_provider": self.provider, "pool_trunk": None, "has_trunks": True,
        }

    async def fetch(self, _sql, *_args):
        return [{"provider": self.provider, "status": "active", "label": None,
                 "from_number": None, "last_tested_at": None,
                 "last_test_result": {"ok": True}}] if self.saved else []

    async def fetchval(self, _sql, *_args):
        return 1


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return Context(self.conn)


@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    monkeypatch.setenv("TWILIO_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("VONAGE_BRIDGE_ENABLED", "true")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["twilio", "vonage"])
async def test_production_activation_rejects_without_changing_selection(provider):
    conn = Conn(provider)
    with pytest.raises(HTTPException) as caught:
        await providers.activate_provider(providers.ProviderActivateRequest(provider=provider),
            SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn))
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "cloud_telephony_unavailable"
    assert conn.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
async def test_list_exposes_unavailable_unsaved_cards_and_retains_saved_selection(saved):
    conn = Conn("twilio", saved=saved)
    result = await providers.list_providers(SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn))
    data = result.model_dump()
    assert data["active"] == "twilio"
    assert len(data["providers"]) == int(saved)
    for provider in ("twilio", "vonage"):
        availability = data["availability"][provider]
        assert availability["activation_allowed"] is False
        assert availability["reason_code"] == "cloud_telephony_unavailable"
        assert availability["reason"]
    assert conn.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["twilio", "vonage"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_nonproduction_activation_is_explicit_qualification_only(monkeypatch, provider, enabled):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv(f"{provider.upper()}_BRIDGE_ENABLED", str(enabled))
    conn = Conn(provider)
    args = (providers.ProviderActivateRequest(provider=provider), SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn))
    if enabled:
        assert (await providers.activate_provider(*args))["active"] == provider
    else:
        with pytest.raises(HTTPException) as caught:
            await providers.activate_provider(*args)
        assert caught.value.status_code == 422
        assert conn.writes == []
    result = await providers.list_providers(args[1], args[2])
    availability = result.model_dump()["availability"][provider]
    assert availability["activation_allowed"] is enabled
    assert availability["qualification_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["sip", "none"])
async def test_existing_sip_and_none_activation_unchanged(provider):
    conn = Conn(provider)
    assert (await providers.activate_provider(providers.ProviderActivateRequest(provider=provider),
        SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn)))["active"] == provider
    assert len(conn.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["twilio", "vonage"])
async def test_campaign_cannot_queue_work_for_unsupported_saved_selection(provider):
    db, queue = starts._DB(), starts._Queue([True])
    db.tables["campaigns"][0].update(id=calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    db.tables["leads"][0].update(id=calls.LEAD_ID, campaign_id=calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    db.pool = Pool(Conn(provider))
    with pytest.raises(CampaignReadinessError):
        await CampaignService(db, queue_service=queue).start_campaign(calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    assert not queue.enqueued and not db.tables["dialer_jobs"]
    assert db.tables["campaigns"][0]["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["twilio", "vonage"])
async def test_manual_redial_uses_actual_selection_before_queue(monkeypatch, provider):
    actual_requires = trunk_resolver.requires_sip_readiness
    h = redials.harness.__wrapped__(monkeypatch)
    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", actual_requires)

    @asynccontextmanager
    async def scoped(*_args, **_kwargs):
        yield Conn(provider)

    monkeypatch.setattr(db_utils, "acquire_with_tenant", scoped)
    # The shared readiness module owns its own import of the same scope helper.
    from app.domain.services.telephony import outbound_readiness
    monkeypatch.setattr(outbound_readiness, "acquire_with_tenant", scoped)
    preview = await redials.preview(h)
    assert preview["eligible"] is False
    assert preview["reason_code"] == "cloud_telephony_unavailable"
    with pytest.raises(redials.service.RedialError):
        await redials.request(h)
    assert h.state.inserts == 0
    h.queue.schedule_job_once.assert_not_called()


def install_selection(monkeypatch, *, provider="none", fail=False, missing=False, intent=None):
    h = calls._install_call_path(monkeypatch, campaign_rows=[calls._campaign(), calls._campaign()],
                                 internal_intent_row=intent)
    original = h.conn.fetchrow
    h.selection_reads = []

    async def fetchrow(sql, *args):
        if "active_telephony_provider" in sql:
            h.selection_reads.append(args)
            if fail:
                raise RuntimeError("synthetic selection unavailable")
            return None if missing else {"active_telephony_provider": provider}
        return await original(sql, *args)

    monkeypatch.setattr(h.conn, "fetchrow", fetchrow)
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["twilio", "vonage"])
async def test_existing_intent_cannot_silently_originate_via_sip(monkeypatch, provider):
    h = install_selection(monkeypatch, provider=provider)
    caught = await calls._assert_call_error(h, 422)
    assert caught.detail["code"] == "cloud_telephony_unavailable"
    assert "prewarm" not in h.events and "originate" not in h.events and "claim_intent" not in h.events
    assert h.selection_reads == [(calls.TENANT_ID,)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failure", "missing"])
async def test_selection_uncertainty_never_warms_or_originates(monkeypatch, mode):
    h = install_selection(monkeypatch, fail=mode == "failure", missing=mode == "missing")
    caught = await calls._assert_call_error(h, 503)
    assert caught.detail["code"] == "telephony_selection_unavailable"
    assert "prewarm" not in h.events and "originate" not in h.events and "claim_intent" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["sip", "none"])
async def test_existing_sip_none_route_still_originates(monkeypatch, provider):
    h = install_selection(monkeypatch, provider=provider)
    await calls.telephony_bridge.make_call(h.request, h.body)
    assert h.events.count("originate") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,provider_id", [("completed", None), ("calling", "existing-provider-call")])
async def test_existing_receipt_replay_survives_current_selection_failure(monkeypatch, status, provider_id):
    h = install_selection(monkeypatch, fail=True,
        intent=calls._internal_intent_row(status=status, provider_call_id=provider_id))
    result = await calls.telephony_bridge.make_call(h.request, h.body)
    assert result.status_code == 200
    assert h.selection_reads == []
    assert "prewarm" not in h.events and "originate" not in h.events


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", ["error", "detail"])
@pytest.mark.parametrize("existing", [False, True])
async def test_unavailable_selection_preserves_original_worker_attempt_through_http(monkeypatch, envelope, existing):
    import aiohttp
    from app.workers.dialer_worker import DialerWorker
    from tests.unit.test_dialer_worker_guard_ordering import _base_worker, _job

    worker = _base_worker()
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker._make_call = DialerWorker._make_call.__get__(worker)
    worker._publish_block = AsyncMock()
    worker._mark_call_intent_not_originated = AsyncMock()
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)
    intent = worker._create_call_intent.return_value
    if existing:
        worker._load_existing_call_intent.return_value = intent
    bodies = []
    response_status = 503

    class Response(Context):
        @property
        def status(self):
            return response_status

        async def __aenter__(self):
            return self

        async def text(self):
            return json.dumps({envelope: {"code": "telephony_selection_unavailable"}} if response_status == 503
                              else {"status": "calling", "call_id": "provider-one"})

    class Session(Context):
        async def __aenter__(self):
            return self

        def post(self, _url, **kwargs):
            bodies.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    job = _job()
    original_attempt = job.attempt_number
    await worker.process_job(job)
    worker._mark_call_intent_not_originated.assert_not_awaited()
    worker._bind_call_intent.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_not_awaited()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(
        job.job_id, "telephony_selection_unavailable", delay_seconds=120, scheduled_at=ANY)
    response_status = 200
    worker._load_existing_call_intent.return_value = intent
    await worker.process_job(job)
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert job.attempt_number == original_attempt
    assert bodies[0]["durable_call_id"] == intent.call_id
    worker._bind_call_intent.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["smart", "legacy"])
@pytest.mark.parametrize("absence_proved", [False, True])
async def test_known_cloud_rejection_settles_no_provider_attempt_without_retry(monkeypatch, policy, absence_proved):
    import aiohttp
    from app.workers.dialer_worker import DialerWorker
    from tests.unit.test_dialer_worker_guard_ordering import _base_worker, _job

    monkeypatch.setenv("RETRY_POLICY", policy)
    worker = _base_worker()
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker._make_call = DialerWorker._make_call.__get__(worker)
    worker._mark_call_intent_not_originated = AsyncMock(return_value=absence_proved)
    worker._park_uncertain_origination = AsyncMock()
    worker._record_job_failure_classification = AsyncMock()
    bodies = []

    class Response(Context):
        status = 422

        async def __aenter__(self):
            return self

        async def text(self):
            return json.dumps({"error": {"code": "cloud_telephony_unavailable", "message": "Cloud calling is unavailable in production."}})

    class Session(Context):
        async def __aenter__(self):
            return self

        def post(self, _url, **kwargs):
            bodies.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    job = _job()
    attempt = job.attempt_number
    await worker.process_job(job)
    assert len(bodies) == 1
    worker._mark_call_intent_not_originated.assert_awaited_once()
    worker.queue_service.schedule_retry.assert_not_awaited()
    if absence_proved:
        worker.queue_service.mark_failed.assert_awaited_once()
        worker._update_lead_status.assert_awaited_with(job, "failed")
        worker._park_uncertain_origination.assert_not_awaited()
    else:
        worker.queue_service.mark_failed.assert_not_awaited()
        worker._update_lead_status.assert_not_awaited()
        worker._park_uncertain_origination.assert_awaited_once()
    worker._bind_call_intent.assert_not_awaited()
    assert job.attempt_number == attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,scope", [("twilio", "provider_account"), ("vonage", "sdk_initialization")])
async def test_credential_check_scope_is_returned_and_stored_without_enabling_calls(monkeypatch, provider, scope):
    from app.infrastructure.telephony import twilio_provider_adapter, vonage_provider_adapter
    conn = Conn(provider)
    stored = []

    async def fetchrow(_sql, *_args):
        return {"credentials_encrypted": "synthetic-not-a-credential", "from_number": None}

    async def execute(sql, *args):
        if "UPDATE tenant_telephony_credentials" in sql:
            stored.append(json.loads(args[2]))
        return "UPDATE 1"

    monkeypatch.setattr(conn, "fetchrow", fetchrow)
    monkeypatch.setattr(conn, "execute", execute)
    monkeypatch.setattr(providers, "get_encryption_service", lambda: SimpleNamespace(decrypt=lambda _: "{}"))
    fake = lambda **_: SimpleNamespace(ping_with_detail=AsyncMock(return_value={"ok": True, "latency_ms": 1}))
    monkeypatch.setattr(twilio_provider_adapter, "TwilioProviderAdapter", fake)
    monkeypatch.setattr(vonage_provider_adapter, "VonageProviderAdapter", fake)
    result = await providers.test_provider(provider, SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn))
    assert result.check_scope == scope
    assert stored[0]["check_scope"] == scope
    with pytest.raises(HTTPException) as caught:
        await providers.activate_provider(providers.ProviderActivateRequest(provider=provider),
            SimpleNamespace(tenant_id=calls.TENANT_ID), Pool(conn))
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_campaign_readiness_lookup_failure_surfaces_503(monkeypatch):
    from app.api.v1.endpoints import campaigns as endpoint
    db = starts._DB()
    db.tables["campaigns"][0].update(id=calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    db.pool = Pool(Conn(fail=True))
    monkeypatch.setattr(endpoint, "_get_campaign_service", lambda _: CampaignService(db))
    with pytest.raises(HTTPException) as caught:
        await endpoint.get_campaign_readiness(calls.CAMPAIGN_ID, SimpleNamespace(tenant_id=calls.TENANT_ID), db)
    assert caught.value.status_code == 503
    assert db.tables["campaigns"][0]["status"] == "draft"
    assert db.tables["dialer_jobs"] == []


@pytest.mark.asyncio
async def test_campaign_start_lookup_failure_preserves_temporary_unavailable_status():
    from app.domain.services.campaign_service import CampaignError
    db, queue = starts._DB(), starts._Queue([True])
    db.tables["campaigns"][0].update(id=calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    db.pool = Pool(Conn(fail=True))
    with pytest.raises(CampaignError) as caught:
        await CampaignService(db, queue_service=queue).start_campaign(calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    assert caught.value.status_code == 503
    assert queue.enqueued == [] and db.tables["dialer_jobs"] == []
    assert db.tables["campaigns"][0]["status"] == "draft"


@pytest.mark.asyncio
async def test_actual_twilio_timeout_retains_nullable_status_code():
    from app.infrastructure.telephony.twilio_provider_adapter import TwilioProviderAdapter
    adapter = TwilioProviderAdapter(account_sid="AC-synthetic", auth_token="synthetic")

    def timeout():
        raise TimeoutError("Synthetic account check timed out")

    adapter._client = SimpleNamespace(api=SimpleNamespace(v2010=SimpleNamespace(
        accounts=lambda _: SimpleNamespace(fetch=timeout))))
    result = await adapter.ping_with_detail()
    assert result["ok"] is False
    assert result["status_code"] is None
    assert providers.TestResult(**result).model_dump()["status_code"] is None


@pytest.mark.asyncio
async def test_manual_redial_lookup_failure_surfaces_503_without_queue(monkeypatch):
    from app.api.v1.endpoints import calls as endpoint
    h = redials.harness.__wrapped__(monkeypatch)

    @asynccontextmanager
    async def scoped(*_args, **_kwargs):
        yield Conn(fail=True)

    monkeypatch.setattr(db_utils, "acquire_with_tenant", scoped)
    with pytest.raises(HTTPException) as caught:
        await endpoint.get_call_redial(h.call, SimpleNamespace(tenant_id=h.tenant), SimpleNamespace(pool=h.pool))
    assert caught.value.status_code == 503
    assert h.state.inserts == 0
    h.queue.schedule_job_once.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_config_correction_and_campaign_restart_can_retry_failed_contact(monkeypatch):
    from app.domain.services.telephony import caller_id_guard
    db, queue = starts._DB(), starts._Queue([True])
    db.tables["campaigns"][0].update(id=calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID, status="paused")
    db.tables["leads"][0].update(id=calls.LEAD_ID, campaign_id=calls.CAMPAIGN_ID,
                                  tenant_id=calls.TENANT_ID, status="failed")
    conn = Conn("twilio")
    db.pool = Pool(conn)
    service = CampaignService(db, queue_service=queue)
    with pytest.raises(CampaignReadinessError):
        await service.start_campaign(calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    assert db.tables["leads"][0]["status"] == "failed" and queue.enqueued == []
    conn.provider = "sip"  # Explicit operator correction; the guard never rewrites it.
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", AsyncMock(return_value=SimpleNamespace(
        refused=False, endpoint="fixture", trunk_id="synthetic-trunk", caller_id="+14155550199")))
    monkeypatch.setattr(caller_id_guard, "check_caller_id_ownership", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    result = await service.start_campaign(calls.CAMPAIGN_ID, tenant_id=calls.TENANT_ID)
    assert result.success and result.jobs_enqueued == 1
    assert len(queue.enqueued) == 1 and len(db.tables["dialer_jobs"]) == 1
