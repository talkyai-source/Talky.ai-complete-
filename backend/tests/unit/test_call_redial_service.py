"""Manual redial admission, tenant isolation and uncertain queue handoff."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.services import call_redial_service as service
from app.domain.services.telephony import caller_id_guard, trunk_resolver


@pytest.fixture
def harness(monkeypatch):
    tenant, call, campaign, lead = [str(uuid4()) for _ in range(4)]
    row = dict(id=call, tenant_id=tenant, campaign_id=campaign, lead_id=lead,
        status="ended", direction="outbound", outcome="answered",
        original_phone="+14165550123", phone_number="+14165550123", lead_status="completed",
        do_not_call=False, first_name="Ava", last_name="Example", custom_fields={"company": "Fixture"},
        campaign_status="running", campaign_direction="outbound", calling_config={}, calling_rules={})
    state = SimpleNamespace(row=row, job=None, busy=False, committed=False, inserts=0, on_lock=None,
                            fail_ack=False, scopes=[], dnc=False)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "FROM calls c" in sql:
                if args[:2] != (call, tenant):
                    return None
                if "FOR UPDATE" in sql and state.on_lock:
                    state.on_lock()
                return dict(state.row)
            if "FROM dialer_jobs" in sql:
                return dict(state.job) if state.job and args[1] == tenant else None
            raise AssertionError(sql)

        async def fetchval(self, sql, *args):
            if "SELECT EXISTS" in sql:
                return state.busy
            if "UPDATE dialer_jobs" in sql:
                if state.fail_ack:
                    raise RuntimeError("database acknowledgement unavailable")
                if state.job["status"] == "pending":
                    state.job["status"] = "queued"
                    return "queued"
                return None
            if "SELECT status FROM dialer_jobs" in sql:
                return state.job["status"] if state.job else None
            raise AssertionError(sql)

        async def execute(self, sql, *args):
            assert "INSERT INTO dialer_jobs" in sql
            if state.job is None:
                state.inserts += 1
                state.job = dict(id=args[0], status="pending", scheduled_at=args[5], phone_number=args[4])
            return "INSERT 0 1"

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        state.scopes.append(tenant_id)
        state.committed = False
        yield Conn()
        state.committed = True

    async def schedule(job, *, idempotency_key):
        assert state.committed and state.job, "database must commit before queue publication"
        assert job.job_id == state.job["id"]
        assert job.lead_first_name == "Ava"
        return True

    monkeypatch.setattr(service, "acquire_with_tenant", scoped)
    dnc = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "DNCService", lambda _: SimpleNamespace(is_on_dnc=dnc))
    route = AsyncMock(return_value=SimpleNamespace(refused=False, endpoint="trunk-fixture",
        caller_id="+14165550199", trunk_id="fixture-trunk", reason="trunk_unavailable"))
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", route)
    requires_sip = AsyncMock(return_value=True)
    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", requires_sip)
    ownership = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(caller_id_guard, "check_caller_id_ownership", ownership)
    queue = SimpleNamespace(schedule_job_once=AsyncMock(side_effect=schedule), confirm_retry_once=AsyncMock())
    return SimpleNamespace(state=state, tenant=tenant, call=call, pool=object(), queue=queue,
                           dnc=dnc, route=route, ownership=ownership, requires_sip=requires_sip)


async def preview(h):
    return await service.preview_redial(h.pool, tenant_id=h.tenant, call_id=h.call)


async def request(h):
    return await service.request_redial(h.pool, h.queue, tenant_id=h.tenant, call_id=h.call)


@pytest.mark.asyncio
async def test_preview_is_read_only_and_uses_current_route(harness):
    h = harness
    result = await preview(h)
    assert result["eligible"] is True
    assert result["caller_id"] == "+14165550199"
    assert result["trunk_id"] == "fixture-trunk"
    assert h.state.inserts == 0
    h.queue.schedule_job_once.assert_not_called()
    assert h.ownership.await_args.kwargs["caller_id"] == result["caller_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("patch,reason", [
    ({"direction": "inbound"}, "outbound_only"),
    ({"campaign_direction": "inbound"}, "outbound_only"),
    ({"status": "termination_pending"}, "call_not_finished"),
    ({"status": "ringing"}, "call_not_finished"),
    ({"do_not_call": True}, "do_not_call"),
    ({"lead_status": "deleted"}, "do_not_call"),
    ({"outcome": "opt_out"}, "outcome_blocked"),
    ({"campaign_status": "paused"}, "campaign_not_running"),
    ({"phone_number": "+14165550124"}, "contact_changed"),
])
async def test_ineligible_contact_never_reaches_queue(harness, patch, reason):
    h = harness
    h.state.row.update(patch)
    assert (await preview(h))["reason_code"] == reason
    with pytest.raises(service.RedialError) as exc:
        await request(h)
    assert exc.value.code == reason
    assert h.state.inserts == 0
    h.queue.schedule_job_once.assert_not_called()


@pytest.mark.asyncio
async def test_global_or_tenant_dnc_blocks_redial(harness):
    h = harness
    h.dnc.return_value = True
    assert (await preview(h))["reason_code"] == "do_not_call"
    with pytest.raises(service.RedialError):
        await request(h)
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_unavailable_or_unverified_selected_route_blocks(harness):
    h = harness
    h.route.return_value.refused = True
    assert (await preview(h))["reason_code"] == "trunk_unavailable"
    h.route.return_value.refused = False
    h.ownership.return_value.allowed = False
    assert (await preview(h))["reason_code"] == "caller_id_not_verified"
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_cloud_provider_does_not_inherit_sip_readiness(harness):
    h = harness
    h.requires_sip.return_value = False
    assert (await preview(h))["eligible"] is True
    h.route.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_failure_never_creates_work(harness):
    h = harness
    h.dnc.side_effect = RuntimeError("suppression lookup unavailable")
    with pytest.raises(RuntimeError):
        await request(h)
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_db_commits_before_publication_and_double_click_reuses_job(harness):
    h = harness
    first = await request(h)
    second = await request(h)
    assert first["job_id"] == second["job_id"] == service.redial_job_id(h.call)
    assert first["status"] == second["status"] == "queued"
    assert h.state.inserts == 1
    h.queue.schedule_job_once.assert_awaited_once()
    assert set(h.state.scopes) == {h.tenant}


@pytest.mark.asyncio
async def test_uncertain_queue_handoff_is_pending_and_retry_keeps_identity(harness):
    h = harness
    h.queue.schedule_job_once.side_effect = [False, True]
    first = await request(h)
    assert first["status"] == "pending"
    second = await request(h)
    assert second["status"] == "queued"
    assert first["job_id"] == second["job_id"]
    assert h.state.inserts == 1
    calls = h.queue.schedule_job_once.await_args_list
    assert calls[0].kwargs["idempotency_key"] == calls[1].kwargs["idempotency_key"]


@pytest.mark.asyncio
async def test_lost_db_ack_preserves_pending_retry_and_idempotency_key(harness):
    h = harness
    h.state.fail_ack = True
    with pytest.raises(RuntimeError):
        await request(h)
    assert h.state.job["status"] == "pending"
    h.queue.confirm_retry_once.assert_not_called()
    h.state.fail_ack = False
    assert (await request(h))["status"] == "queued"
    assert h.state.inserts == 1


@pytest.mark.asyncio
async def test_an_active_contact_attempt_blocks_even_after_old_call_finished(harness):
    h = harness
    h.state.busy = True
    assert (await preview(h))["reason_code"] == "contact_busy"
    with pytest.raises(service.RedialError):
        await request(h)
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_contact_change_between_preview_and_lock_is_rechecked(harness):
    h = harness
    h.state.on_lock = lambda: h.state.row.update(do_not_call=True)
    with pytest.raises(service.RedialError) as exc:
        await request(h)
    assert exc.value.code == "do_not_call"
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_another_tenant_cannot_redial_or_discover_the_call(harness):
    h = harness
    with pytest.raises(service.RedialError) as exc:
        await service.request_redial(h.pool, h.queue, tenant_id=str(uuid4()), call_id=h.call)
    assert exc.value.status_code == 404
    assert h.state.inserts == 0


@pytest.mark.asyncio
async def test_finished_redial_receipt_cannot_start_a_second_job_from_old_call(harness):
    h = harness
    h.state.job = dict(id=service.redial_job_id(h.call), status="completed", scheduled_at=datetime.now(timezone.utc), phone_number=h.state.row["phone_number"])
    assert (await preview(h))["reason_code"] == "already_requested"
    assert (await request(h))["status"] == "completed"
    assert h.state.inserts == 0
    h.queue.schedule_job_once.assert_not_called()


@pytest.mark.asyncio
async def test_replayed_receipt_keeps_durable_destination_after_contact_edit(harness):
    h = harness
    first = await request(h)
    h.state.row["phone_number"] = "+14165550124"
    replay = await request(h)
    assert replay["phone_number"] == first["phone_number"] == "+14165550123"
    assert replay["job_id"] == first["job_id"]
    h.queue.schedule_job_once.assert_awaited_once()
