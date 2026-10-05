"""Selective meter failure must not grant calls or invent an allowance."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from app.domain.services.call_guard import CallGuard, TenantCallLimits
from app.domain.services.minutes_quota import compute_minutes_status
from app.services.scripts.tenant_minutes import compute_tenant_minutes_remaining
from app.workers.dialer_worker import DialerWorker
from tests.unit.test_dialer_worker_guard_ordering import _base_worker, _job


class Meter:
    def __init__(self, allocated=30, used=120, plan_minutes=30, *, missing=False, fail=False):
        self.allocated, self.used, self.plan_minutes = allocated, used, plan_minutes
        self.missing, self.fail = missing, fail

    async def fetchrow(self, *_):
        return None if self.missing else {'minutes_allocated': self.allocated, 'plan_minutes': self.plan_minutes}

    async def fetchval(self, sql, *_):
        if 'SELECT minutes_allocated' in sql:
            return None if self.missing else self.allocated
        if self.fail:
            raise RuntimeError('synthetic selective usage-query failure')
        return self.used

    async def execute(self, *_):
        return 'SELECT 1'

    @asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self, **_):
        yield self.conn


@pytest.mark.parametrize('meter', [Meter(missing=True), Meter(allocated=None), Meter(allocated=0, plan_minutes=None)])
async def test_missing_entitlement_is_not_unlimited(meter):
    result = await compute_minutes_status(meter, '11111111-1111-4111-8111-111111111111')
    assert result.unlimited is False
    assert result.state == 'unavailable'
    assert result.remaining_minutes is None


async def test_zero_allocation_on_finite_plan_is_exhausted_not_unlimited():
    result = await compute_minutes_status(Meter(allocated=0, plan_minutes=30), '11111111-1111-4111-8111-111111111111')
    assert result.unlimited is False and result.exhausted is True


async def test_explicit_unlimited_plan_still_reports_settled_usage():
    result = await compute_minutes_status(Meter(allocated=0, plan_minutes=0), '11111111-1111-4111-8111-111111111111')
    assert result.unlimited is True and result.used_minutes == 2


async def test_final_guard_does_not_admit_using_stale_stored_zero():
    guard = SimpleNamespace(_db_pool=Pool(Meter(fail=True)))
    result = await CallGuard._check_minutes_quota(guard, tenant_id='11111111-1111-4111-8111-111111111111',
        tenant_limits=TenantCallLimits(monthly_minutes_allocated=30, monthly_minutes_used=0))
    assert result.passed is False
    assert result.reason == 'metering_unavailable'


async def test_profile_query_failure_does_not_return_full_remaining():
    result = await compute_tenant_minutes_remaining(Pool(Meter(fail=True)),
        tenant_id='11111111-1111-4111-8111-111111111111', minutes_allocated=30)
    assert result is None


async def test_worker_defers_same_job_on_selective_meter_failure_then_recovers():
    worker = _base_worker()
    meter = Meter(fail=True)
    @asynccontextmanager
    async def acquire():
        yield meter
    worker._acquire_db = acquire
    worker._tenant_minutes_exhausted = DialerWorker._tenant_minutes_exhausted.__get__(worker)
    worker._publish_block = AsyncMock()
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)
    job = _job()
    original_attempt = job.attempt_number
    await worker.process_job(job)
    worker._make_call.assert_not_awaited()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(job.job_id, 'metering_unavailable', delay_seconds=120, scheduled_at=ANY)
    worker.queue_service.schedule_retry.assert_not_awaited()
    assert job.attempt_number == original_attempt
    meter.fail = False
    await worker.process_job(job)
    worker._make_call.assert_awaited_once()
    assert job.attempt_number == original_attempt


async def test_real_bridge_returns_retryable_unavailable_before_any_origination(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.endpoints import telephony_bridge
    from app.domain.services.call_guard import CheckResult, GuardCheck
    from tests.unit.test_outbound_campaign_boundaries import _install_call_path, _campaign

    path = _install_call_path(monkeypatch, campaign_rows=[_campaign()])
    guard = CallGuard(Pool(Meter(fail=True)))
    guard._get_tenant_limits = AsyncMock(return_value=TenantCallLimits(monthly_minutes_allocated=30))
    guard._get_partner_limits = AsyncMock(return_value=None)
    guard._get_partner_id = AsyncMock(return_value=None)
    # Keep all unrelated admission checks healthy, while the real canonical
    # meter fails only its usage SELECT. Exercise real guard and HTTP handler.
    for name in dir(guard):
        if name.startswith('_check_') and name != '_check_minutes_quota':
            setattr(guard, name, AsyncMock(return_value=CheckResult(check=GuardCheck.TENANT_ACTIVE, passed=True)))
    monkeypatch.setattr(telephony_bridge, 'CallGuard', lambda **_: guard)
    with pytest.raises(HTTPException) as caught:
        await telephony_bridge.make_call(path.request, path.body)
    assert caught.value.status_code == 503
    assert caught.value.detail['code'] == 'metering_unavailable'
    assert int(caught.value.headers['Retry-After']) > 0
    assert 'prewarm' not in path.events and 'originate' not in path.events


@pytest.mark.parametrize('existing_intent', [False, True])
async def test_bridge_meter_failure_preserves_attempt_through_actual_http_adapter(monkeypatch, existing_intent):
    import json
    import aiohttp

    worker = _base_worker()
    worker._get_lead_timezone = AsyncMock(return_value=None)
    worker._make_call = DialerWorker._make_call.__get__(worker)
    worker._publish_block = AsyncMock()
    worker._mark_call_intent_not_originated = AsyncMock()
    worker.queue_service._redefer_inflight = AsyncMock(return_value=True)
    intent = worker._create_call_intent.return_value
    if existing_intent:
        worker._load_existing_call_intent.return_value = intent
    bodies = []
    response_status = 503

    class Response:
        @property
        def status(self):
            return response_status

        async def text(self):
            return json.dumps({'error': {'code': 'metering_unavailable'}} if response_status == 503
                              else {'status': 'calling', 'call_id': 'provider-one'})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, _url, **kwargs):
            bodies.append(kwargs['json'])
            return Response()

    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    job = _job()
    await worker.process_job(job)
    worker._mark_call_intent_not_originated.assert_not_awaited()
    worker._bind_call_intent.assert_not_awaited()
    worker.queue_service.schedule_retry.assert_not_awaited()
    worker.queue_service._redefer_inflight.assert_awaited_once_with(job.job_id, 'metering_unavailable', delay_seconds=120, scheduled_at=ANY)
    worker._load_existing_call_intent.return_value = intent
    response_status = 200
    await worker.process_job(job)
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert bodies[0]['durable_call_id'] == intent.call_id
    worker._bind_call_intent.assert_awaited_once()
    worker.queue_service.schedule_retry.assert_not_awaited()


async def test_existing_queue_deferral_preserves_identity_and_bounded_delay(monkeypatch):
    import json
    from datetime import datetime, timezone
    from app.domain.services.queue_service import DialerQueueService

    job = _job()
    redis = SimpleNamespace(hget=AsyncMock(return_value=json.dumps(job.to_redis_dict())),
                            zadd=AsyncMock(), lrem=AsyncMock(), hdel=AsyncMock(), zrem=AsyncMock())
    queue = DialerQueueService(redis)
    monkeypatch.setenv('DIALER_PAUSE_REDEFER_S', '0')
    before = datetime.now(timezone.utc).timestamp()
    assert await queue._redefer_inflight(job.job_id, 'metering_unavailable') is True
    entries = redis.zadd.await_args.args[1]
    assert len(entries) == 1
    payload, scheduled = next(iter(entries.items()))
    saved = json.loads(payload)
    assert saved['job_id'] == job.job_id and saved['attempt_number'] == job.attempt_number
    assert scheduled >= before + 5


def test_public_allowance_schemas_express_null_and_state():
    from app.api.v1.endpoints.auth.schemas import AuthTokenResponse, MeResponse
    from app.api.v1.endpoints.mfa.schemas import MFAChallengeVerifyResponse
    from app.api.v1.endpoints.passkeys import LoginCompleteResponse
    from app.api.v1.endpoints.dashboard import DashboardSummary
    for model in (AuthTokenResponse, MeResponse, MFAChallengeVerifyResponse, LoginCompleteResponse, DashboardSummary):
        fields = model.model_json_schema()['properties']
        assert {'type': 'null'} in fields['minutes_remaining']['anyOf']
        assert fields['minutes_state']['enum'] == ['known', 'unlimited', 'unavailable']



@pytest.mark.parametrize('state,remaining', [('known', 28), ('unlimited', None), ('unavailable', None)])
async def test_profile_and_dashboard_share_canonical_allowance_without_blocking_identity(state, remaining):
    from app.api.v1.dependencies import CurrentUser
    from app.api.v1.endpoints.auth.profile import get_me
    from app.api.v1.endpoints.dashboard import get_dashboard_summary
    from app.services.scripts.tenant_minutes import compute_tenant_minutes_status
    from tests.unit.test_dashboard_summary import _FakeClient

    pool = Pool(Meter(allocated=0, plan_minutes=0) if state == 'unlimited' else Meter(fail=state == 'unavailable'))
    tenant = '11111111-1111-4111-8111-111111111111'
    meter = await compute_tenant_minutes_status(pool, tenant)
    user = CurrentUser(id='user', email='test@example.com', tenant_id=tenant, **meter.allowance())
    profile_pool = Pool(SimpleNamespace(fetchrow=AsyncMock(return_value=None), execute=AsyncMock(), transaction=Meter().transaction))
    profile = await get_me(current_user=user, db_client=SimpleNamespace(pool=profile_pool))
    assert profile.minutes_state == state and profile.minutes_remaining == remaining
    db = _FakeClient({})
    db.pool = pool
    dashboard = await get_dashboard_summary(current_user=user, db_client=db)
    assert dashboard.minutes_state == state
    assert dashboard.minutes_used == (None if state == 'unavailable' else 2)
    assert dashboard.minutes_remaining == remaining


@pytest.mark.parametrize('plan_minutes,state', [(0, 'unlimited'), (30, 'known')])
async def test_subscription_zero_allocation_preserves_verified_state(plan_minutes, state):
    from app.api.v1.endpoints.billing import get_subscription
    result = await get_subscription(
        current_user=SimpleNamespace(tenant_id='11111111-1111-4111-8111-111111111111'),
        billing=SimpleNamespace(get_subscription=AsyncMock(return_value={'status': 'active'})),
        db_pool=Pool(Meter(allocated=0, plan_minutes=plan_minutes)),
    )
    assert result.minutes_allocated == 0 and result.minutes_remaining == 0
    assert result.minutes_state == state


async def test_canonical_unknown_still_produces_existing_billing_503_contract():
    from fastapi import HTTPException
    from app.api.v1.endpoints.billing import get_subscription, get_usage_summary
    from app.domain.services.billing_service import BillingService
    tenant = '11111111-1111-4111-8111-111111111111'
    pool = Pool(Meter(fail=True))
    billing = BillingService.__new__(BillingService)
    billing.db_client = SimpleNamespace(pool=pool)
    user = SimpleNamespace(tenant_id=tenant)
    with pytest.raises(HTTPException) as caught:
        await get_usage_summary('minutes', user, billing)
    assert caught.value.status_code == 503 and caught.value.detail['code'] == 'usage_unavailable'
    with pytest.raises(HTTPException) as caught:
        await get_subscription(current_user=user,
            billing=SimpleNamespace(get_subscription=AsyncMock(return_value=None)), db_pool=pool)
    assert caught.value.status_code == 503 and caught.value.detail['code'] == 'usage_unavailable'
