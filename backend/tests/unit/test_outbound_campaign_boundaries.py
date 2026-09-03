"""Fail-closed direction and durability boundaries for outbound origination.

These are endpoint-level tests with deliberately small asyncpg/adapter fakes.  They
exercise ordering (not merely SQL text): an inbound, foreign, stopped, or
unverifiable campaign must never reach trunk lookup, warmup, or the real adapter;
and a tenant-user call must have a durable row before the adapter may ring.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.telephony_bridge import MakeCallRequest
from app.api.v1.endpoints.telephony_sip import trunks
from app.api.v1.endpoints.telephony_sip.trunks import CampaignTrunkBody
from app.domain.services.call_guard import GuardDecision
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter


TENANT_ID = "11111111-1111-4111-8111-111111111111"
FOREIGN_TENANT_ID = "22222222-2222-4222-8222-222222222222"
CAMPAIGN_ID = "33333333-3333-4333-8333-333333333333"
TRUNK_ID = "44444444-4444-4444-8444-444444444444"
USER_ID = "55555555-5555-4555-8555-555555555555"
VOICE_CALL_ID = "66666666-6666-4666-8666-666666666666"
LEAD_ID = "77777777-7777-4777-8777-777777777777"
DIALER_JOB_ID = "88888888-8888-4888-8888-888888888888"
CASE_CAMPAIGN_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _request(*, method: str = "POST", tenant_id: str | None = TENANT_ID, token=None):
    headers = [] if token is None else [(b"x-internal-service-token", token.encode())]
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "server": ("api.talky.test", 443),
            "path": "/api/v1/sip/telephony/call",
            "raw_path": b"/api/v1/sip/telephony/call",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "state": {},
        }
    )
    if tenant_id is not None:
        request.state.tenant_id = tenant_id
    return request


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _CallConn:
    def __init__(
        self,
        events,
        campaign_rows,
        *,
        campaign_error: Exception | None = None,
        lead_row=None,
        insert_error: Exception | None = None,
        provider_update_error: BaseException | None = None,
        pending_update_error: Exception | None = None,
        terminal_update_error: Exception | None = None,
        terminal_update_result: str = "UPDATE 1",
        internal_intent_row=None,
    ):
        self.events = events
        self.campaign_rows = list(campaign_rows)
        self.campaign_error = campaign_error
        self.lead_row = lead_row
        self.insert_error = insert_error
        self.provider_update_error = provider_update_error
        self.pending_update_error = pending_update_error
        self.terminal_update_error = terminal_update_error
        self.terminal_update_result = terminal_update_result
        self.internal_intent_row = internal_intent_row
        self.job_status = "processing"
        self.lead_status = "calling"
        self.failed_updates = 0
        self.bind_query = None
        self.campaign_queries = []
        self.insert_query = None
        self.insert_args = None
        self.pending_query = None
        self.pending_args = None
        self.intent_queries = []

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *args):
        normalized = " ".join(query.split()).lower()
        if "update calls" in normalized and "dialer_attempt_number" in normalized:
            self.events.append("claim_intent")
            self.intent_queries.append((normalized, args))
            if (
                self.internal_intent_row is None
                or self.internal_intent_row.get("provider_call_id")
            ):
                return None
            self.internal_intent_row = {
                **self.internal_intent_row,
                "status": "dialing",
                "provider": args[5],
                "provider_call_id": args[4],
            }
            return {
                "status": "dialing",
                "provider": args[5],
                "provider_call_id": args[4],
            }
        if "from calls" in normalized and "dialer_attempt_number" in normalized:
            self.events.append("load_intent")
            self.intent_queries.append((normalized, args))
            return self.internal_intent_row
        if "from campaigns" in normalized:
            self.events.append("campaign_lookup")
            self.campaign_queries.append((normalized, args))
            if self.campaign_error:
                raise self.campaign_error
            return self.campaign_rows.pop(0) if self.campaign_rows else None
        if "from dialer_jobs" in normalized and "for update" in normalized:
            self.events.append("lock_dialer_job")
            return {
                "status": self.job_status,
                "lead_id": self.internal_intent_row["lead_id"],
            }
        if "from leads" in normalized and "for update" in normalized:
            self.events.append("lock_lead")
            return {"id": self.internal_intent_row["lead_id"]}
        if "from leads" in normalized:
            self.events.append("lead_lookup")
            return self.lead_row
        if "insert into calls" in normalized:
            self.events.append("insert_call")
            self.insert_query = normalized
            self.insert_args = args
            if self.insert_error:
                raise self.insert_error
            return {"id": VOICE_CALL_ID}
        raise AssertionError(f"Unexpected fetchrow: {normalized}")

    async def execute(self, query, *_args):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("set local") or normalized.startswith("select set_config"):
            return "SELECT 1"
        if "update calls" in normalized and "termination_pending" in normalized:
            self.events.append("termination_pending")
            self.pending_query = normalized
            self.pending_args = _args
            if self.pending_update_error:
                raise self.pending_update_error
            return "UPDATE 1"
        if "update calls" in normalized and "provider_call_id" in normalized:
            self.events.append("bind_call")
            self.bind_query = normalized
            if self.provider_update_error:
                raise self.provider_update_error
            return "UPDATE 1"
        if "update calls" in normalized:
            self.events.append("fail_call")
            self.failed_updates += 1
            if self.terminal_update_error:
                raise self.terminal_update_error
            if self.terminal_update_result == "UPDATE 1" and self.internal_intent_row:
                self.internal_intent_row = {
                    **self.internal_intent_row,
                    "status": "failed",
                }
            return self.terminal_update_result
        if "update dialer_jobs" in normalized:
            self.events.append("settle_dialer_job")
            self.job_status = "failed"
            return "UPDATE 1"
        if "update leads" in normalized:
            self.events.append("release_lead")
            self.lead_status = "pending"
            return "UPDATE 1"
        raise AssertionError(f"Unexpected execute: {normalized}")


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_kwargs):
        return _Acquire(self.conn)


class _ProviderIdentityConn:
    """Tiny stateful calls-row fake proving planned/actual identity ordering."""

    def __init__(self, planned_call_id: str):
        self.external_call_uuid = planned_call_id
        self.provider_call_id = planned_call_id
        self.status = "initiated"
        self.queries = []

    def transaction(self):
        return _Transaction()

    async def execute(self, query, *args):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("set local") or normalized.startswith("select set_config"):
            return "SELECT 1"
        self.queries.append((normalized, args))
        if "external_call_uuid = $3" in normalized:
            _durable_id, _tenant_id, original_id, actual_id, _provider = args
            if self.external_call_uuid != original_id:
                return "UPDATE 0"
            if self.provider_call_id not in {original_id, actual_id}:
                return "UPDATE 0"
            self.provider_call_id = actual_id
            return "UPDATE 1"
        if "provider_call_id = case" in normalized:
            _durable_id, _tenant_id, returned_id, _provider = args
            if self.external_call_uuid is None:
                self.external_call_uuid = returned_id
            if self.provider_call_id is None or self.provider_call_id == self.external_call_uuid:
                self.provider_call_id = returned_id
            self.status = "dialing"
            return "UPDATE 1"
        raise AssertionError(f"Unexpected execute: {normalized}")


class _StateBackend:
    def __init__(self, events, *, cleanup_registration_error=None):
        self.events = events
        self.warmups = {}
        self.cleanup_obligations = {}
        self.cleanup_registration_error = cleanup_registration_error

    def is_telephony_owner(self):
        return True

    async def telephony_owner_id(self):
        return "owner"

    def voice_session_count(self):
        return 0

    def set_ringing_warmup(self, call_id, session, task, **_kwargs):
        self.events.append("store_warmup")
        self.warmups[call_id] = (session, task)

    def get_ringing_warmup(self, call_id):
        return self.warmups.get(call_id)

    def set_ringing_started_at(self, *_args):
        pass

    def set_ringing_event(self, *_args):
        pass

    def set_first_speaker(self, *_args):
        pass

    def pop_ringing_warmup(self, call_id):
        self.events.append("pop_warmup")
        return self.warmups.pop(call_id, None)

    def alias_ringing_call(self, original_call_id, actual_call_id):
        self.events.append("alias_warmup")
        warmup = self.warmups.pop(original_call_id, None)
        if warmup is None:
            return False
        self.warmups[actual_call_id] = warmup
        return True

    def clear_ringing_started_at(self, *_args):
        pass

    def pop_ringing_event(self, *_args):
        pass

    def clear_first_speaker(self, *_args):
        pass

    async def register_cleanup_obligation(self, call_id, **metadata):
        self.events.append("register_cleanup")
        if self.cleanup_registration_error:
            raise self.cleanup_registration_error
        self.cleanup_obligations[call_id] = dict(metadata)

    async def acknowledge_orphan_recovery(self, call_id):
        self.events.append("ack_cleanup")
        self.cleanup_obligations.pop(call_id, None)


class _Adapter:
    connected = True

    def __init__(
        self,
        events,
        *,
        originate_error: BaseException | None = None,
        name: str = "asterisk",
        originated_call_id: str | None = None,
        hangup_confirmed: bool = True,
    ):
        self.events = events
        self.originate_error = originate_error
        self.name = name
        self.originated_call_id = originated_call_id
        self.hangup_is_confirmed = hangup_confirmed
        self.hangups = []

    async def originate_call(self, **kwargs):
        self.events.append("originate")
        if self.originate_error:
            raise self.originate_error
        return self.originated_call_id or kwargs.get("channel_id") or "provider-call-actual"

    async def hangup(self, call_id):
        self.events.append("hangup")
        self.hangups.append(call_id)

    async def hangup_confirmed(self, call_id):
        self.events.append("hangup_confirmed")
        self.hangups.append(call_id)
        return self.hangup_is_confirmed


def _campaign(*, direction="outbound", status="running", tenant_id=TENANT_ID):
    return {
        "id": CAMPAIGN_ID,
        "tenant_id": tenant_id,
        "direction": direction,
        "status": status,
        "name": "Boundary campaign",
        "script_config": {},
        "calling_config": {},
    }


def _internal_intent_row(*, status="initiated", provider_call_id=None):
    return {
        "id": VOICE_CALL_ID,
        "tenant_id": TENANT_ID,
        "campaign_id": CAMPAIGN_ID,
        "lead_id": "77777777-7777-4777-8777-777777777777",
        "phone_number": "+15551234567",
        "direction": "outbound",
        "talklee_call_id": "TKY-DURABLE-INTERNAL",
        "dialer_job_id": "88888888-8888-4888-8888-888888888888",
        "dialer_attempt_number": 2,
        "status": status,
        "provider": "asterisk" if provider_call_id else None,
        "provider_call_id": provider_call_id,
    }


def _install_call_path(
    monkeypatch,
    *,
    campaign_rows,
    campaign_error=None,
    lead_row=None,
    insert_error=None,
    provider_update_error=None,
    pending_update_error=None,
    terminal_update_error=None,
    terminal_update_result="UPDATE 1",
    internal_intent_row=None,
    originate_error=None,
    adapter_name="asterisk",
    originated_call_id=None,
    hangup_confirmed=True,
    internal=True,
    durable_intent=True,
    route_refused=False,
    cleanup_registration_error=None,
):
    events = []
    if internal and durable_intent and internal_intent_row is None:
        internal_intent_row = _internal_intent_row()
    conn = _CallConn(
        events,
        campaign_rows,
        campaign_error=campaign_error,
        lead_row=lead_row,
        insert_error=insert_error,
        provider_update_error=provider_update_error,
        pending_update_error=pending_update_error,
        terminal_update_error=terminal_update_error,
        terminal_update_result=terminal_update_result,
        internal_intent_row=internal_intent_row,
    )
    pool = _Pool(conn)
    container = SimpleNamespace(is_initialized=True, db_pool=pool, redis=None)
    state = _StateBackend(
        events,
        cleanup_registration_error=cleanup_registration_error,
    )
    adapter = _Adapter(
        events,
        originate_error=originate_error,
        name=adapter_name,
        originated_call_id=originated_call_id,
        hangup_confirmed=hangup_confirmed,
    )
    session = SimpleNamespace(
        call_id=VOICE_CALL_ID,
        talklee_call_id="TKY-BOUNDARY-1",
        call_session=SimpleNamespace(),
    )
    ended_sessions = []
    prewarm_kwargs = {}

    def route():
        return SimpleNamespace(
            refused=route_refused,
            is_default=True,
            endpoint="carrier",
            caller_id=None,
            reason="missing_tenant_trunk" if route_refused else "test",
        )

    async def resolve_campaign_trunk(*_args, **_kwargs):
        events.append("trunk_lookup")
        return route()

    async def resolve_pool_assignment(*_args, **_kwargs):
        events.append("pool_lookup")
        return route()

    async def prewarm(**kwargs):
        events.append("prewarm")
        prewarm_kwargs.update(kwargs)
        return SimpleNamespace(
            session=session,
            effective_first_speaker="agent",
            failure_reason=None,
        )

    async def load_lead_context(lead_id, _tenant_id):
        if lead_id:
            events.append("lead_context")
        return None

    async def allow_caller_id(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, require_attestation=False)

    class AllowGuard:
        def __init__(self, **_kwargs):
            pass

        async def evaluate(self, **_kwargs):
            return SimpleNamespace(
                decision=GuardDecision.ALLOW,
                check_results=[],
                failed_checks=[],
                total_latency_ms=1,
            )

    class Orchestrator:
        async def end_session(self, value):
            events.append("end_session")
            ended_sessions.append(value)

    from app.core import container as container_module, readiness
    from app.domain.services.telephony import trunk_resolver

    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(telephony_bridge, "_load_agent_lead_context", load_lead_context)
    monkeypatch.setattr(telephony_bridge, "check_caller_id_ownership", allow_caller_id)
    monkeypatch.setattr(telephony_bridge, "get_state_backend", lambda: state)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(telephony_bridge, "CallGuard", AllowGuard)
    monkeypatch.setattr(telephony_bridge, "prepare_prewarmed_session", prewarm)
    monkeypatch.setattr(telephony_bridge, "_get_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(telephony_bridge, "AbuseDetectionService", lambda **_kwargs: object())
    monkeypatch.setattr(trunk_resolver, "_resolve_campaign_trunk", resolve_campaign_trunk)
    monkeypatch.setattr(trunk_resolver, "_resolve_pool_assignment", resolve_pool_assignment)
    monkeypatch.setattr(readiness, "is_draining", lambda: False)
    monkeypatch.setattr(readiness, "is_pod_at_capacity", lambda: False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    if internal:
        monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "internal-test-token")
        request = _request(tenant_id=None, token="internal-test-token")
    else:
        monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)
        request = _request()
    body = MakeCallRequest(
        destination="+15551234567",
        caller_id="+15557654321",
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
    )
    if internal_intent_row is not None:
        body.lead_id = internal_intent_row["lead_id"]
        body.durable_call_id = internal_intent_row["id"]
        body.talklee_call_id = internal_intent_row["talklee_call_id"]
        body.dialer_job_id = internal_intent_row["dialer_job_id"]
        body.dialer_attempt_number = internal_intent_row["dialer_attempt_number"]
    return SimpleNamespace(
        request=request,
        body=body,
        events=events,
        conn=conn,
        adapter=adapter,
        state=state,
        session=session,
        ended_sessions=ended_sessions,
        prewarm_kwargs=prewarm_kwargs,
    )


async def _assert_call_error(path, status):
    with pytest.raises(HTTPException) as caught:
        await telephony_bridge.make_call(path.request, path.body)
    assert caught.value.status_code == status
    return caught.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "status"),
    [
        (_campaign(direction="inbound"), 409),
        (_campaign(status="paused"), 409),
        (None, 404),
    ],
)
async def test_invalid_campaign_fails_before_trunk_warmup_or_originate(monkeypatch, row, status):
    path = _install_call_path(monkeypatch, campaign_rows=[row])

    await _assert_call_error(path, status)

    assert path.events == ["lead_context", "campaign_lookup"]


@pytest.mark.asyncio
async def test_campaign_lookup_failure_is_retryable_and_never_dials(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[],
        campaign_error=RuntimeError("postgres unavailable"),
    )

    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(
            telephony_bridge.make_call(path.request, path.body),
            timeout=0.2,
        )
    error = caught.value

    assert error.status_code == 503
    assert error.detail["error"] == "campaign_lookup_unavailable"
    assert path.events == ["lead_context", "campaign_lookup"]


@pytest.mark.asyncio
async def test_campaign_select_timeout_cancels_query_and_never_dials(monkeypatch):
    path = _install_call_path(monkeypatch, campaign_rows=[_campaign()])
    original_fetchrow = path.conn.fetchrow
    query_cancelled = asyncio.Event()

    async def blocked_fetchrow(query, *args):
        if "from campaigns" in " ".join(query.split()).lower():
            try:
                await asyncio.Event().wait()
            finally:
                query_cancelled.set()
        return await original_fetchrow(query, *args)

    path.conn.fetchrow = blocked_fetchrow
    monkeypatch.setattr(telephony_bridge, "_OUTBOUND_BOUNDARY_DB_TIMEOUT_S", 0.01)

    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(
            telephony_bridge.make_call(path.request, path.body),
            timeout=0.2,
        )
    error = caught.value

    assert error.status_code == 503
    assert error.detail["error"] == "campaign_lookup_unavailable"
    assert query_cancelled.is_set()
    assert "trunk_lookup" not in path.events
    assert "originate" not in path.events


@pytest.mark.asyncio
async def test_final_campaign_row_lock_timeout_cancels_query_and_never_dials(monkeypatch):
    path = _install_call_path(monkeypatch, campaign_rows=[_campaign(), _campaign()])
    original_fetchrow = path.conn.fetchrow
    campaign_reads = 0
    query_cancelled = asyncio.Event()

    async def blocked_second_campaign_fetchrow(query, *args):
        nonlocal campaign_reads
        if "from campaigns" in " ".join(query.split()).lower():
            campaign_reads += 1
            if campaign_reads == 2:
                try:
                    await asyncio.Event().wait()
                finally:
                    query_cancelled.set()
        return await original_fetchrow(query, *args)

    path.conn.fetchrow = blocked_second_campaign_fetchrow
    monkeypatch.setattr(telephony_bridge, "_OUTBOUND_BOUNDARY_DB_TIMEOUT_S", 0.01)

    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(
            telephony_bridge.make_call(path.request, path.body),
            timeout=0.2,
        )
    error = caught.value

    assert error.status_code == 503
    assert error.detail["error"] == "campaign_lookup_unavailable"
    assert query_cancelled.is_set()
    assert "originate" not in path.events
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_foreign_campaign_is_tenant_scoped_404_and_never_dials(monkeypatch):
    path = _install_call_path(monkeypatch, campaign_rows=[None])

    error = await _assert_call_error(path, 404)

    assert error.detail["error"] == "campaign_not_found"
    assert path.events == ["lead_context", "campaign_lookup"]
    query, args = path.conn.campaign_queries[0]
    assert "tenant_id = $2::uuid" in query
    assert args == (CAMPAIGN_ID, TENANT_ID)


@pytest.mark.asyncio
async def test_internal_call_without_durable_intent_is_rejected_before_work(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        internal=True,
        durable_intent=False,
    )

    error = await _assert_call_error(path, 422)

    assert error.detail["error"] == "dialer_intent_required"
    assert path.events == []


@pytest.mark.asyncio
async def test_campaign_direction_is_revalidated_after_warmup_before_originate(monkeypatch):
    row = _internal_intent_row()
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign(direction="inbound")],
        internal=True,
        internal_intent_row=row,
    )
    path.body.lead_id = row["lead_id"]
    path.body.durable_call_id = row["id"]
    path.body.talklee_call_id = row["talklee_call_id"]
    path.body.dialer_job_id = row["dialer_job_id"]
    path.body.dialer_attempt_number = row["dialer_attempt_number"]

    await _assert_call_error(path, 409)

    assert path.events == [
        "lead_context",
        "campaign_lookup",
        "load_intent",
        "trunk_lookup",
        "prewarm",
        "campaign_lookup",
        "end_session",
    ]
    assert row["status"] == "initiated"
    assert "originate" not in path.events


@pytest.mark.asyncio
async def test_internal_dialer_intent_is_claimed_and_session_stamped_before_ari(monkeypatch):
    row = _internal_intent_row()
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        internal=True,
        internal_intent_row=row,
    )
    path.body.lead_id = row["lead_id"]
    path.body.durable_call_id = row["id"]
    path.body.talklee_call_id = row["talklee_call_id"]
    path.body.dialer_job_id = row["dialer_job_id"]
    path.body.dialer_attempt_number = row["dialer_attempt_number"]

    response = await telephony_bridge.make_call(path.request, path.body)

    payload = json.loads(response.body)
    assert payload["durable_call_id"] == VOICE_CALL_ID
    assert path.events.index("load_intent") < path.events.index("claim_intent")
    assert path.events.index("claim_intent") < path.events.index("store_warmup")
    assert path.events.index("store_warmup") < path.events.index("originate")
    assert path.session._dialer_call_id == VOICE_CALL_ID
    assert path.session._dialer_tenant_id == TENANT_ID
    assert path.session._dialer_campaign_id == CAMPAIGN_ID
    assert path.session._dialer_lead_id == row["lead_id"]
    assert path.session._dialer_talklee_call_id == row["talklee_call_id"]
    assert path.session._dialer_provider_call_id == payload["call_id"]
    assert "insert_call" not in path.events


@pytest.mark.asyncio
async def test_internal_dialer_provider_replay_returns_without_guard_warmup_or_ari(monkeypatch):
    row = _internal_intent_row(
        status="dialing",
        provider_call_id="talky-out-already-owned",
    )
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign()],
        internal=True,
        internal_intent_row=row,
    )
    path.body.lead_id = row["lead_id"]
    path.body.durable_call_id = row["id"]
    path.body.talklee_call_id = row["talklee_call_id"]
    path.body.dialer_job_id = row["dialer_job_id"]
    path.body.dialer_attempt_number = row["dialer_attempt_number"]

    response = await telephony_bridge.make_call(path.request, path.body)

    payload = json.loads(response.body)
    assert payload["call_id"] == "talky-out-already-owned"
    assert payload["idempotent_replay"] is True
    assert path.events == ["lead_context", "campaign_lookup", "load_intent"]


@pytest.mark.asyncio
async def test_no_provider_attempt_leaves_worker_intent_actionable(monkeypatch):
    row = _internal_intent_row()
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        internal_intent_row=row,
        adapter_name="unsupported-provider",
    )

    error = await _assert_call_error(path, 503)

    assert error.detail["error"] == "durable_origination_not_supported"
    assert row["status"] == "initiated"
    assert "claim_intent" not in path.events
    assert "originate" not in path.events
    assert "fail_call" not in path.events
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_trunk_refusal_releases_prewarmed_session_before_return(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign()],
        route_refused=True,
    )

    error = await _assert_call_error(path, 422)

    assert error.detail["error"] == "tenant_pbx_required"
    assert path.events.index("prewarm") < path.events.index("end_session")
    assert path.ended_sessions == [path.session]
    assert "originate" not in path.events


@pytest.mark.asyncio
async def test_tenant_user_is_rejected_before_campaign_guard_or_ari(monkeypatch):
    path = _install_call_path(monkeypatch, campaign_rows=[], internal=False)

    error = await _assert_call_error(path, 403)

    assert error.detail["error"] == "telephony_call_internal_only"
    assert path.events == []


@pytest.mark.asyncio
async def test_same_internal_attempt_claim_is_atomic_and_replay_observes_winner():
    row = _internal_intent_row()
    conn = _CallConn([], [], internal_intent_row=row)
    pool = _Pool(conn)
    intent = telephony_bridge._InternalDialerIntent(
        call_id=row["id"],
        talklee_call_id=row["talklee_call_id"],
        dialer_job_id=row["dialer_job_id"],
        attempt_number=row["dialer_attempt_number"],
        tenant_id=row["tenant_id"],
        campaign_id=row["campaign_id"],
        lead_id=row["lead_id"],
        destination=row["phone_number"],
        status=row["status"],
        provider=None,
        provider_call_id=None,
    )

    async def claim_once():
        async with telephony_bridge._serialize_internal_origination(row["id"]):
            return await telephony_bridge._claim_internal_dialer_intent(
                pool,
                intent=intent,
                provider="asterisk",
                planned_provider_call_id=f"talky-out-{row['id']}",
            )

    first, second = await asyncio.gather(claim_once(), claim_once())

    assert [first[0], second[0]].count(True) == 1
    assert [first[0], second[0]].count(False) == 1
    assert first[1].provider_call_id == second[1].provider_call_id
    assert row["id"] not in telephony_bridge._attempt_locks


@pytest.mark.asyncio
async def test_cancelled_internal_attempt_waiter_does_not_leak_lock_registry_entry():
    call_id = "77777777-7777-4777-8777-777777777777"
    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        async with telephony_bridge._serialize_internal_origination(call_id):
            holder_entered.set()
            await release_holder.wait()

    async def waiter():
        async with telephony_bridge._serialize_internal_origination(call_id):
            raise AssertionError("cancelled waiter must never enter")

    holder_task = asyncio.create_task(holder())
    await holder_entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task
    release_holder.set()
    await holder_task

    assert call_id not in telephony_bridge._attempt_locks


@pytest.mark.asyncio
async def test_missing_warmup_alias_returns_false_so_adapter_owns_actual_leg_cleanup(
    monkeypatch,
):
    class EmptyState:
        def get_ringing_warmup(self, _call_id):
            return None

        def alias_ringing_call(self, _original, _actual):
            return False

    monkeypatch.setattr(telephony_bridge, "get_state_backend", lambda: EmptyState())

    moved = await telephony_bridge._alias_ringing_call_id(
        "talky-out-cleaned-up",
        "asterisk-late-actual-leg",
    )

    assert moved is False


@pytest.mark.asyncio
async def test_late_actual_leg_is_scheduled_for_cleanup_when_warmup_alias_is_gone():
    adapter = AsteriskAdapter()
    scheduled = []
    started = []

    async def correlate(_call_id):
        return "talky-out-already-cleaned"

    async def missing_alias(_original, _actual):
        return False

    adapter._correlate_trunk_leg = correlate
    adapter.set_outbound_channel_alias_callback(missing_alias)
    adapter._schedule_unclaimed_hangup = lambda call_id, *, reason: scheduled.append(
        (call_id, reason)
    )
    adapter._on_outbound_stasis_start = lambda call_id: started.append(call_id)

    await adapter._start_trunk_leg("asterisk-late-actual-leg")

    assert scheduled == [
        ("asterisk-late-actual-leg", "outbound_alias_persist_failed")
    ]
    assert started == []


@pytest.mark.asyncio
@pytest.mark.parametrize("alias_first", [True, False])
async def test_actual_asterisk_alias_wins_before_or_after_originate_bind(alias_first):
    planned_id = "talky-out-planned"
    actual_id = "asterisk-trunk-leg-actual"
    conn = _ProviderIdentityConn(planned_id)
    pool = _Pool(conn)

    async def bind_returned_planned_id():
        await telephony_bridge._bind_durable_outbound_call_provider(
            pool,
            tenant_id=TENANT_ID,
            durable_call_id=VOICE_CALL_ID,
            provider="asterisk",
            provider_call_id=planned_id,
        )

    async def persist_actual_alias():
        await telephony_bridge._persist_durable_outbound_channel_alias(
            pool,
            tenant_id=TENANT_ID,
            durable_call_id=VOICE_CALL_ID,
            provider="asterisk",
            original_call_id=planned_id,
            actual_call_id=actual_id,
        )

    if alias_first:
        await persist_actual_alias()
        await bind_returned_planned_id()
    else:
        await bind_returned_planned_id()
        await persist_actual_alias()

    assert conn.external_call_uuid == planned_id
    assert conn.provider_call_id == actual_id


@pytest.mark.asyncio
async def test_asterisk_waits_for_alias_persistence_before_starting_actual_leg():
    adapter = AsteriskAdapter()
    adapter._correlate_trunk_leg = lambda _call_id: None  # replaced below
    entered = asyncio.Event()
    release = asyncio.Event()
    events = []

    async def correlate(_call_id):
        return "talky-out-planned"

    async def persist_alias(_original, _actual):
        events.append("persist_started")
        entered.set()
        await release.wait()
        events.append("persisted")

    async def start_leg(call_id):
        events.append(("started", call_id))

    adapter._correlate_trunk_leg = correlate
    adapter.set_outbound_channel_alias_callback(persist_alias)
    adapter._on_outbound_stasis_start = start_leg

    task = asyncio.create_task(adapter._start_trunk_leg("asterisk-trunk-leg-actual"))
    await entered.wait()
    assert not any(isinstance(item, tuple) and item[0] == "started" for item in events)
    release.set()
    await task

    assert events == [
        "persist_started",
        "persisted",
        ("started", "asterisk-trunk-leg-actual"),
    ]


@pytest.mark.asyncio
async def test_post_originate_row_update_failure_hangs_up_and_cleans_warmup(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        provider_update_error=RuntimeError("write unavailable"),
    )

    error = await _assert_call_error(path, 503)

    assert error.detail["error"] == "durable_call_bind_failed"
    assert path.adapter.hangups
    assert "hangup_confirmed" in path.events
    assert "fail_call" in path.events
    assert "pop_warmup" in path.events
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_unconfirmed_cleanup_stays_pending_and_is_not_retryable_503(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        provider_update_error=RuntimeError("write unavailable"),
        hangup_confirmed=False,
    )

    error = await _assert_call_error(path, 409)

    assert error.detail["error"] == "origination_cleanup_pending"
    assert not error.headers or "Retry-After" not in error.headers
    assert path.conn.pending_args[2] == path.adapter.hangups[0]
    assert "termination_pending" in path.events
    assert "fail_call" not in path.events
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_dual_store_outage_retains_local_durability_retry(monkeypatch):
    scheduled = []

    def retain_retry(**context):
        scheduled.append(context)

    monkeypatch.setattr(
        telephony_bridge,
        "_retain_local_origination_recovery",
        retain_retry,
    )
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        provider_update_error=RuntimeError("bind unavailable"),
        pending_update_error=RuntimeError("postgres unavailable"),
        cleanup_registration_error=RuntimeError("redis unavailable"),
        hangup_confirmed=False,
    )

    error = await _assert_call_error(path, 409)

    assert error.detail["error"] == "origination_cleanup_pending"
    assert len(scheduled) == 1
    assert scheduled[0]["durable_call_id"] == VOICE_CALL_ID
    assert scheduled[0]["provider_call_id"].startswith("talky-out-")
    assert scheduled[0]["tenant_id"] == TENANT_ID
    assert "hangup_confirmed" not in path.events


@pytest.mark.asyncio
async def test_local_durability_retry_survives_until_one_store_recovers(monkeypatch):
    attempts = []

    class RedisDown:
        async def register_cleanup_obligation(self, *_args, **_kwargs):
            attempts.append("redis")
            raise RuntimeError("redis unavailable")

    async def mark_pending(*_args, **_kwargs):
        attempts.append("postgres")
        if attempts.count("postgres") == 1:
            raise RuntimeError("postgres unavailable")

    monkeypatch.setattr(telephony_bridge, "get_state_backend", lambda: RedisDown())
    monkeypatch.setattr(
        telephony_bridge,
        "_mark_durable_outbound_call_termination_pending",
        mark_pending,
    )
    monkeypatch.setattr(
        telephony_bridge,
        "_LOCAL_ORIGINATION_RECOVERY_RETRY_S",
        0,
    )

    task = telephony_bridge._retain_local_origination_recovery(
        db_pool=object(),
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        durable_call_id=VOICE_CALL_ID,
        provider="asterisk",
        provider_call_id="talky-out-dual-store-outage",
        reason="bind unavailable",
    )
    await asyncio.wait_for(task, timeout=0.2)
    await asyncio.sleep(0)

    assert attempts == ["redis", "postgres", "redis", "postgres"]
    assert (
        "talky-out-dual-store-outage"
        not in telephony_bridge._local_origination_recovery_tasks
    )


@pytest.mark.asyncio
async def test_confirmed_hangup_with_terminal_update_zero_retains_redis_ledger(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        provider_update_error=RuntimeError("bind unavailable"),
        terminal_update_result="UPDATE 0",
        hangup_confirmed=True,
    )

    error = await _assert_call_error(path, 409)

    provider_id = path.adapter.hangups[0]
    assert error.detail["error"] == "origination_cleanup_pending"
    assert error.detail["provider_hangup_confirmed"] is True
    assert provider_id in path.state.cleanup_obligations
    assert "ack_cleanup" not in path.events


@pytest.mark.asyncio
async def test_cancellation_after_originate_runs_confirmed_cleanup_before_propagating(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        provider_update_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await telephony_bridge.make_call(path.request, path.body)

    assert "hangup_confirmed" in path.events
    assert "fail_call" in path.events
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_real_task_cancellation_shields_cleanup_to_completion(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
    )
    bind_started = asyncio.Event()

    async def blocked_bind(*_args, **_kwargs):
        bind_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        telephony_bridge,
        "_bind_durable_outbound_call_provider",
        blocked_bind,
    )
    task = asyncio.create_task(telephony_bridge.make_call(path.request, path.body))
    await bind_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert path.events.index("register_cleanup") < path.events.index("hangup_confirmed")
    assert "fail_call" in path.events
    assert "ack_cleanup" in path.events
    assert path.state.cleanup_obligations == {}
    assert path.events[-1] == "end_session"


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_provider_cleanup(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
    )
    bind_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def blocked_bind(*_args, **_kwargs):
        bind_started.set()
        await asyncio.Event().wait()

    async def blocked_confirmed_hangup(_adapter, _provider_call_id):
        cleanup_started.set()
        await release_cleanup.wait()
        path.events.append("hangup_confirmed")
        return telephony_bridge.HangupProof(True, True, "confirmed")

    monkeypatch.setattr(
        telephony_bridge,
        "_bind_durable_outbound_call_provider",
        blocked_bind,
    )
    monkeypatch.setattr(
        telephony_bridge,
        "request_confirmed_hangup",
        blocked_confirmed_hangup,
    )
    task = asyncio.create_task(telephony_bridge.make_call(path.request, path.body))
    await bind_started.wait()
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    parent_waited_for_cleanup = not task.done()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert parent_waited_for_cleanup
    assert "fail_call" in path.events
    assert path.state.cleanup_obligations == {}


@pytest.mark.asyncio
async def test_originate_failure_marks_direct_row_failed_and_cleans_session(monkeypatch):
    path = _install_call_path(
        monkeypatch,
        campaign_rows=[_campaign(), _campaign()],
        originate_error=RuntimeError("ARI rejected originate"),
    )

    await _assert_call_error(path, 500)

    assert path.adapter.hangups
    assert "fail_call" in path.events
    assert "pop_warmup" in path.events
    assert path.events[-1] == "end_session"


class _SettlementTransaction:
    def __init__(self, conn):
        self.conn = conn
        self.snapshot = None

    async def __aenter__(self):
        self.snapshot = (self.conn.call_status, self.conn.job_status, self.conn.lead_status)
        return self

    async def __aexit__(self, exc_type, *_args):
        if exc_type is not None:
            self.conn.call_status, self.conn.job_status, self.conn.lead_status = self.snapshot
        return False


class _SettlementConn:
    def __init__(self, *, job_status, fail_on=None):
        self.call_status = "dialing"
        self.job_status = job_status
        self.lead_status = "calling"
        self.fail_on = fail_on
        self.queries = []

    def transaction(self):
        return _SettlementTransaction(self)

    async def fetchrow(self, query, *args):
        normalized = " ".join(query.split()).lower()
        self.queries.append((normalized, args))
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError("settlement database failure")
        if "from calls" in normalized and "for update" in normalized:
            return {
                "status": self.call_status,
                "dialer_job_id": DIALER_JOB_ID,
                "lead_id": LEAD_ID,
            }
        if "from dialer_jobs" in normalized and "for update" in normalized:
            return {"status": self.job_status, "lead_id": LEAD_ID}
        if "from leads" in normalized and "for update" in normalized:
            return {"id": LEAD_ID}
        raise AssertionError(f"Unexpected settlement fetchrow: {normalized}")

    async def execute(self, query, *args):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("set local") or normalized.startswith("select set_config"):
            return "SELECT 1"
        self.queries.append((normalized, args))
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError("settlement database failure")
        if "update calls" in normalized:
            self.call_status = "failed"
            return "UPDATE 1"
        if "update dialer_jobs" in normalized:
            self.job_status = "failed"
            return "UPDATE 1"
        if "update leads" in normalized:
            self.lead_status = "pending"
            return "UPDATE 1"
        raise AssertionError(f"Unexpected settlement execute: {normalized}")


@pytest.mark.asyncio
@pytest.mark.parametrize("job_status", ["processing", "retry_scheduled"])
async def test_confirmed_absence_atomically_settles_call_job_and_lead(job_status):
    conn = _SettlementConn(job_status=job_status)

    settled = await telephony_bridge._mark_durable_outbound_call_failed(
        _Pool(conn),
        tenant_id=TENANT_ID,
        durable_call_id=VOICE_CALL_ID,
        dialer_job_id=DIALER_JOB_ID,
        dialer_attempt_number=1,
        lead_id=LEAD_ID,
        reason="provider_absence_confirmed",
    )

    assert settled is True
    assert (conn.call_status, conn.job_status, conn.lead_status) == (
        "failed",
        "failed",
        "pending",
    )


@pytest.mark.asyncio
async def test_confirmed_absence_settlement_replay_is_idempotent():
    conn = _SettlementConn(job_status="processing")
    kwargs = {
        "tenant_id": TENANT_ID,
        "durable_call_id": VOICE_CALL_ID,
        "dialer_job_id": DIALER_JOB_ID,
        "dialer_attempt_number": 1,
        "lead_id": LEAD_ID,
        "reason": "provider_absence_confirmed",
    }

    assert await telephony_bridge._mark_durable_outbound_call_failed(_Pool(conn), **kwargs)
    assert await telephony_bridge._mark_durable_outbound_call_failed(_Pool(conn), **kwargs)
    assert (conn.call_status, conn.job_status, conn.lead_status) == (
        "failed",
        "failed",
        "pending",
    )


@pytest.mark.asyncio
async def test_confirmed_absence_settlement_db_failure_rolls_back_and_fails_closed():
    conn = _SettlementConn(job_status="processing", fail_on="update dialer_jobs")

    settled = await telephony_bridge._mark_durable_outbound_call_failed(
        _Pool(conn),
        tenant_id=TENANT_ID,
        durable_call_id=VOICE_CALL_ID,
        dialer_job_id=DIALER_JOB_ID,
        dialer_attempt_number=1,
        lead_id=LEAD_ID,
        reason="provider_absence_confirmed",
    )

    assert settled is False
    assert (conn.call_status, conn.job_status, conn.lead_status) == (
        "dialing",
        "processing",
        "calling",
    )


class _TrunkConn:
    def __init__(self, *, campaign_row, campaign_error=None):
        self.campaign_row = campaign_row
        self.campaign_error = campaign_error
        self.updates = 0
        self.trunk_reads = 0
        self.trunk_queries = []
        self.campaign_reads = 0

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query, *_args):
        normalized = " ".join(query.split()).lower()
        if "from campaigns" in normalized:
            self.campaign_reads += 1
            if self.campaign_error:
                raise self.campaign_error
            return self.campaign_row
        if "from tenant_sip_trunks" in normalized:
            self.trunk_reads += 1
            self.trunk_queries.append(normalized)
            return {
                "id": TRUNK_ID,
                "trunk_name": "Test trunk",
                "auth_username": "acct",
                "caller_id": "+15557654321",
                "is_active": True,
                "direction": "outbound",
                "metadata": {},
                "live_registration_status": "registered",
                "live_status_detail": None,
                "live_status_checked_at": None,
            }
        raise AssertionError(f"Unexpected fetchrow: {normalized}")

    async def fetchval(self, query, *_args):
        raise AssertionError(f"Campaign assignment must use an existence/direction row: {query}")

    async def execute(self, query, *_args):
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("set local") or normalized.startswith("select set_config"):
            return "SELECT 1"
        if "update campaigns" in normalized:
            self.updates += 1
            return "UPDATE 1"
        raise AssertionError(f"Unexpected execute: {normalized}")


def _trunk_context(*, campaign_row, campaign_error=None):
    conn = _TrunkConn(campaign_row=campaign_row, campaign_error=campaign_error)
    return (
        conn,
        _Pool(conn),
        CurrentUser(
            id=USER_ID,
            email="owner@example.test",
            tenant_id=TENANT_ID,
            role="tenant_admin",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("trunk_id", [None, TRUNK_ID])
async def test_trunk_assignment_and_clear_reject_inbound_before_update(trunk_id):
    conn, pool, user = _trunk_context(campaign_row=_campaign(direction="inbound"))
    request = _request(method="PUT")

    response = await trunks.set_campaign_trunk_assignment(
        CampaignTrunkBody(campaign_id=CAMPAIGN_ID, trunk_id=trunk_id),
        request,
        user,
        pool,
    )

    assert response.status_code == 409
    assert conn.updates == 0
    assert conn.trunk_reads == 0


@pytest.mark.asyncio
async def test_trunk_clear_missing_or_foreign_campaign_is_real_404():
    conn, pool, user = _trunk_context(campaign_row=None)

    response = await trunks.set_campaign_trunk_assignment(
        CampaignTrunkBody(campaign_id=CAMPAIGN_ID, trunk_id=None),
        _request(method="PUT"),
        user,
        pool,
    )

    assert response.status_code == 404
    assert conn.updates == 0


@pytest.mark.asyncio
async def test_trunk_assignment_lookup_failure_is_503_not_fake_404():
    conn, pool, user = _trunk_context(
        campaign_row=None,
        campaign_error=RuntimeError("postgres unavailable"),
    )

    response = await trunks.set_campaign_trunk_assignment(
        CampaignTrunkBody(campaign_id=CAMPAIGN_ID, trunk_id=None),
        _request(method="PUT"),
        user,
        pool,
    )

    assert response.status_code == 503
    assert conn.updates == 0


@pytest.mark.asyncio
async def test_outbound_trunk_clear_updates_exactly_one_locked_campaign():
    conn, pool, user = _trunk_context(campaign_row=_campaign())

    response = await trunks.set_campaign_trunk_assignment(
        CampaignTrunkBody(campaign_id=CAMPAIGN_ID, trunk_id=None),
        _request(method="PUT"),
        user,
        pool,
    )

    assert response.campaign_id == CAMPAIGN_ID
    assert response.trunk_id is None
    assert conn.updates == 1


@pytest.mark.asyncio
async def test_outbound_trunk_assignment_still_updates_locked_campaign(monkeypatch):
    conn, pool, user = _trunk_context(campaign_row=_campaign())
    from app.domain.services.telephony import trunk_resolver

    monkeypatch.setattr(
        trunks,
        "evaluate_trunk_runtime",
        lambda *_args, **_kwargs: SimpleNamespace(ready=True, detail="ready"),
    )
    monkeypatch.setattr(trunk_resolver, "platform_default_trunk_name", lambda: "platform")
    monkeypatch.setattr(trunk_resolver, "env_default_endpoint", lambda: "platform-endpoint")

    response = await trunks.set_campaign_trunk_assignment(
        CampaignTrunkBody(campaign_id=CAMPAIGN_ID, trunk_id=TRUNK_ID),
        _request(method="PUT"),
        user,
        pool,
    )

    assert response.campaign_id == CAMPAIGN_ID
    assert response.trunk_id == TRUNK_ID
    assert response.label == "Test trunk"
    assert conn.trunk_reads == 1
    assert "for share" in conn.trunk_queries[0]
    assert conn.updates == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("campaign_row", "expected_status"),
    [(_campaign(direction="inbound"), 409), (None, 404)],
)
async def test_get_trunk_assignment_does_not_hide_inbound_or_missing_campaign(
    campaign_row, expected_status
):
    conn, pool, user = _trunk_context(campaign_row=campaign_row)

    response = await trunks.get_campaign_trunk_assignment(
        CAMPAIGN_ID,
        _request(method="GET"),
        user,
        pool,
    )

    assert response.status_code == expected_status
    assert conn.updates == 0


@pytest.mark.asyncio
async def test_get_trunk_assignment_rejects_malformed_uuid_before_database():
    conn, pool, user = _trunk_context(campaign_row=_campaign())

    response = await trunks.get_campaign_trunk_assignment(
        "not-a-uuid",
        _request(method="GET"),
        user,
        pool,
    )

    assert response.status_code == 422
    assert conn.campaign_reads == 0
    assert conn.trunk_reads == 0
