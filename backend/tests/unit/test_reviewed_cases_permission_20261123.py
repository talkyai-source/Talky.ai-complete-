"""Cases the reviews of the permission check before dialling named, now pinned.

7.  switch unset: a contact with NO recorded permission costs the dial-time path exactly the
    same database calls as one WITH permission, and is not blocked;
8.  switch `warn`, the permission lookup raises, seen through a real campaign START;
9.  switch `on`: the queue helper itself (not only the route) for paused/stopped/completed/draft;
10. switch `on`: the campaign stopped between the status read and the start -> "not attempted".
Case 11 (expected_count 0 / negative / string / bool / float -> 422, nothing written) is already
pinned by test_contact_permission_switch_on.py::test_ungrouped_422_bad_expected_count.
"""
from __future__ import annotations

import logging

import pytest

from app.workers.dialer_worker import DialerWorker
from tests.unit.test_contact_permission_dial_gate import PLAIN, _row, _start, _start_db
from tests.unit.test_contact_permission_switch_on import (  # noqa: F401  (autouse fixtures are re-used)
    BODY,
    CAMPAIGN,
    GATE,
    LOG,
    TENANT,
    _Conn,
    _Service,
    _User2,
    _db,
    _each_test_starts_with_no_decision_logged,
    _gate_unset,
    _service_patch,
    _wire,
)
from tests.unit.test_w1_dialer_job_lifecycle import _conn_pool, _job


# -- 7 -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_unset_a_contact_without_permission_costs_no_extra_database_call():
    results = {}
    for name, row in {"with": _row(), "without": _row(confirmed=None),
                      "no_list": _row(list_id=None, confirmed=None)}.items():
        w = DialerWorker()
        conn = _conn_pool(w, fetchrow=row)
        results[name] = (await w._stale_payload_reason(_job()), conn.method_calls)
    assert results["with"][0] is None
    assert results["without"][0] is None and results["no_list"][0] is None
    names = [[c[0] for c in calls] for _, calls in results.values()]
    assert names[0] == ["fetchrow"], names  # one tenant-scoped read, nothing else
    assert names[0] == names[1] == names[2]


# -- 8 -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_start_with_a_failing_permission_lookup_selects_everyone_and_withholds_nobody(
        monkeypatch, caplog):
    monkeypatch.setenv(GATE, "warn")
    caplog.set_level(logging.INFO, logger=LOG)
    db = _start_db(pending_ok=2, pending_no=3)
    real_table = db.table

    class Boom:
        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self):
            raise RuntimeError("contact_lists unavailable")

    db.table = lambda name: Boom() if name == "contact_lists" else real_table(name)
    result, _ = await _start(db, monkeypatch)
    assert result.jobs_enqueued == 5 and result.jobs_withheld == 0
    assert result.message == PLAIN
    assert "lookup=failed" in caplog.text and "mode=warn" in caplog.text


# -- 9 -----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paused", "stopped", "completed", "draft"])
async def test_on_the_queue_helper_never_starts_a_campaign_that_is_not_running(monkeypatch, status):
    monkeypatch.setenv(GATE, "on")
    cpc, _tx = _wire(monkeypatch, _Conn(status=status))
    svc = _Service()
    _service_patch(monkeypatch, svc)
    out = await cpc.queue_confirmed_contacts_if_running(
        _db(), campaign_id=CAMPAIGN, tenant_id=TENANT, list_id="L1")
    assert out == ("not_attempted", 0)
    assert svc.calls == []


# -- 10 ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_a_campaign_that_stopped_between_the_read_and_the_start_is_not_attempted_not_failed(
        monkeypatch, caplog):
    from app.domain.services.campaign_service import CampaignStateError

    monkeypatch.setenv(GATE, "on")
    caplog.set_level(logging.WARNING)
    cpc, _tx = _wire(monkeypatch, _Conn(status="running"))
    svc = _Service(error=CampaignStateError("Campaign is not running"))
    _service_patch(monkeypatch, svc)
    out = await cpc.queue_confirmed_contacts_if_running(
        _db(), campaign_id=CAMPAIGN, tenant_id=TENANT, list_id="L1")
    assert out == ("not_attempted", 0) and len(svc.calls) == 1
    assert "contact_permission_queue_failed" not in caplog.text


@pytest.mark.asyncio
async def test_on_the_ungrouped_route_does_not_say_could_not_be_queued_when_the_campaign_just_stopped(
        monkeypatch):
    from app.domain.services.campaign_service import CampaignStateError

    monkeypatch.setenv(GATE, "on")
    conn = _Conn(status="running")
    cpc, tx = _wire(monkeypatch, conn)
    _service_patch(monkeypatch, _Service(error=CampaignStateError("Campaign is not running")))
    out = await cpc.confirm_ungrouped_permission(CAMPAIGN, dict(BODY), _User2(), _db())
    assert tx.committed >= 1 and tx.rolled_back == 0
    assert out["queued_for_calling"] is None
    assert "could not be queued" not in out["message"] and "queued" not in out["message"]
    assert out["message"].startswith("Permission recorded for 3 contacts.")
