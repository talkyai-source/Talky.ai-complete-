"""Dialling-safety fixes, 2026-11-20 (items 1-5 of the inspector's list)."""
from __future__ import annotations

import pytest

from app.domain.services.campaign_service import (
    CampaignError,
    CampaignNotFoundError,
    CampaignService,
)
from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue


# --- item 1: deleted / paused campaigns -------------------------------------
@pytest.mark.parametrize("allow_running", [False, True])
async def test_a_deleted_campaign_cannot_be_started(allow_running):
    db, queue = _DB(lead_count=2), _Queue([True, True])
    db.tables["campaigns"][0]["status"] = "deleted"
    service = CampaignService(db, queue_service=queue)

    with pytest.raises(CampaignNotFoundError):
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=allow_running)

    assert db.tables["campaigns"][0]["status"] == "deleted"
    assert queue.enqueued == []


async def test_call_this_list_does_not_unpause_a_paused_campaign():
    db, queue = _DB(lead_count=2), _Queue([True, True])
    db.tables["campaigns"][0]["status"] = "paused"
    service = CampaignService(db, queue_service=queue)

    with pytest.raises(CampaignError) as err:
        await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert err.value.status_code == 409
    assert err.value.message == "This campaign is paused. Resume it first, then call the list."
    assert db.tables["campaigns"][0]["status"] == "paused"
    assert queue.enqueued == []


async def test_call_this_list_on_a_stopped_campaign_is_a_compare_and_set():
    db, queue = _DB(lead_count=1), _Queue([True])
    db.tables["campaigns"][0]["status"] = "stopped"
    service = CampaignService(db, queue_service=queue)
    seen = []
    original = service._update_campaign_status

    async def spy(campaign_id, status, **kw):
        seen.append((status, kw.get("from_statuses")))
        return await original(campaign_id, status, **kw)

    service._update_campaign_status = spy
    await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)

    assert ("running", ("stopped",)) in seen


# --- item 2: a pipeline failure after the callee spoke is not FAILED ---------
def test_pipeline_failure_after_the_callee_spoke_is_not_failed():
    from app.domain.services.telephony.outcome_resolver import CallOutcome, resolve_call_outcome
    from tests.unit.test_outcome_resolver import _voice_session

    spoke = _voice_session(pipeline_failed=True, user_turns=2)
    assert resolve_call_outcome(spoke) is not CallOutcome.FAILED
    silent = _voice_session(pipeline_failed=True, user_turns=0)
    assert resolve_call_outcome(silent) is CallOutcome.FAILED


# --- item 3: the testing override never widens the legal-hours floor ---------
_LEGAL = "outside_calling_window_legal_hours_Europe/London"


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["env", "campaign"])
async def test_testing_override_does_not_dial_through_a_legal_hours_refusal(via, monkeypatch):
    from tests.unit.test_dialer_worker_block_visibility import (
        TESTING_OVERRIDE_ENV, AsyncMock, _job, _worker,
    )

    if via == "env":
        monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")
        cfg = {}
    else:
        monkeypatch.delenv(TESTING_OVERRIDE_ENV, raising=False)
        cfg = {"testing_mode_ignore_schedule": True}
    w = _worker(campaign_cfg=cfg)
    w.rules_engine.can_make_call = AsyncMock(return_value=(False, _LEGAL))

    await w.process_job(_job())

    w._make_call.assert_not_awaited()
    assert w._schedule_override is None


@pytest.mark.asyncio
async def test_replayed_held_attempt_is_not_dialled_through_a_legal_hours_refusal(monkeypatch):
    from app.domain.models.calling_rules import CallingRules
    from app.workers.dialer_worker import HeldIntentExpired
    from tests.unit.test_dialer_worker_block_visibility import (
        TESTING_OVERRIDE_ENV, AsyncMock, _job, _worker,
    )

    monkeypatch.setenv(TESTING_OVERRIDE_ENV, "true")
    w = _worker(campaign_cfg={})
    w._get_lead_timezone = AsyncMock(return_value=None)
    w.rules_engine.can_make_call = AsyncMock(return_value=(False, _LEGAL))

    with pytest.raises(HeldIntentExpired):
        await w._require_open_window_for_unclaimed_intent(_job(), CallingRules(), {})


# --- item 4: REJECTED gets one confirming dial a day later, then is final ----
def test_rejected_is_confirmed_once_then_final():
    from app.domain.services.telephony.outcome_resolver import CallOutcome
    from app.workers.disposition_policy import decide

    first = decide(CallOutcome.REJECTED, 1)
    assert first.should_retry is True and first.delay_seconds == 24 * 3600
    second = decide(CallOutcome.REJECTED, 2)
    assert second.should_retry is False
    assert decide(CallOutcome.REJECTED, 0).should_retry is False  # unknown count fails closed


# --- item 4b: the failure streak counts calls settled as status='ended' -------
# Call teardown writes calls.status='ended' (lifecycle._on_call_ended) BEFORE the
# settlement runs, so settlement keeps 'ended' (call_service already_terminal
# branch); only a call settled without that write has status='completed'.
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ended", "completed"])
async def test_failure_streak_counts_a_settled_call_whatever_its_terminal_status(status):
    from app.domain.services import campaign_alerts
    from tests.unit.test_campaign_alert_delivery import TENANT, Store

    store = Store()
    try:
        call_id = store.call(1, "failed")
        store.db.execute("UPDATE calls SET status=? WHERE id=?", (status, call_id))
        async with store.transaction():
            await campaign_alerts.record_terminal_call(store, TENANT, call_id, threshold=1)
        assert len(store.rows("campaign_alert_outbox")) == 1
    finally:
        store.db.close()


# --- item 5: an exhausted allowance / spend cap waits for a top-up ------------
@pytest.mark.parametrize("check,reason", [
    ("MINUTES_QUOTA", "minutes_quota_exhausted: 100/100 used"),
    ("SPEND_LIMIT", "spend_cap_exceeded: $50.00/$50.00"),
])
def test_minutes_and_spend_guard_denials_defer_instead_of_blocking(check, reason):
    from types import SimpleNamespace

    from app.domain.services.call_guard import GuardCheck
    from app.workers.dialer_worker import _GUARD_DEFER_SECONDS, _classify_guard_denial

    denial = SimpleNamespace(check_results=[
        SimpleNamespace(check=getattr(GuardCheck, check), passed=False, reason=reason)])
    assert _classify_guard_denial(denial) == (reason, _GUARD_DEFER_SECONDS)


def test_a_deferred_minutes_quota_reason_reads_as_out_of_minutes():
    from app.domain.services.dialer.block_reasons import BlockCode, classify

    assert classify("minutes_quota_exhausted: 100/100 used").code is BlockCode.OUT_OF_MINUTES
