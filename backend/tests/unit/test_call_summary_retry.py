"""A call summary the model could not produce is asked for again, in the background."""
import asyncio
import logging

import pytest

from app.domain.services.call_summary.summarizer import SUMMARY_UNAVAILABLE_HEADLINE
from app.services.scripts import call_summary_retry as retry
from app.services.scripts import call_transcript_persister as persister

CID = "11111111-2222-3333-4444-555555555555"
BAD = {"headline": SUMMARY_UNAVAILABLE_HEADLINE}
GOOD = {"headline": "Wants a demo", "business_details": {}}
LOGGER = "app.services.scripts.call_summary_retry"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(retry.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(retry, "_pending", 0)
    retry._tasks.clear()
    calls = {"gen": 0, "crm": 0}
    return calls


def _wire(monkeypatch, results):
    state = {"gen": 0, "crm": 0}
    seq = list(results)

    async def fake_generate(pool, tenant, call):
        state["gen"] += 1
        return seq.pop(0)

    async def fake_crm(call, **kwargs):
        state["crm"] += 1

    monkeypatch.setattr(persister, "generate_and_store", fake_generate)
    monkeypatch.setattr("app.services.crm_sync_service.run_crm_sync", fake_crm)
    return state


async def _drain():
    while retry._tasks:
        await asyncio.gather(*list(retry._tasks))


async def test_first_fails_second_succeeds_crm_once(monkeypatch):
    state = _wire(monkeypatch, [BAD, GOOD])
    await persister._safe_generate(object(), "t", CID)
    assert state["crm"] == 0  # deferred to after the final attempt
    await _drain()
    assert state == {"gen": 2, "crm": 1}


async def test_all_attempts_unavailable(monkeypatch):
    state = _wire(monkeypatch, [BAD, BAD, BAD])
    await persister._safe_generate(object(), "t", CID)
    await _drain()
    assert state == {"gen": 3, "crm": 1}


async def test_good_first_attempt_is_unchanged(monkeypatch):
    state = _wire(monkeypatch, [GOOD])
    await persister._safe_generate(object(), "t", CID)
    assert not retry._tasks
    assert state == {"gen": 1, "crm": 1}


async def test_fifth_retry_dropped_with_log_line(monkeypatch, caplog):
    gate = asyncio.Event()

    async def blocked_sleep(_d):
        await gate.wait()

    monkeypatch.setattr(retry.asyncio, "sleep", blocked_sleep)
    done = []

    async def gen():
        return GOOD

    async def fin():
        done.append(1)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        results = [retry.start_retries(f"c{i:011d}", gen, fin) for i in range(5)]
    assert results == [True, True, True, True, False]
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert any("call_id=c00000000004" in m and "outcome=dropped" in m for m in lines)
    gate.set()
    await _drain()
    assert len(done) == 4 and retry._pending == 0


async def test_exception_in_retry_is_logged_not_raised(monkeypatch, caplog):
    done = []

    async def gen():
        raise RuntimeError("boom secret transcript")

    async def fin():
        done.append(1)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert retry.start_retries(CID, gen, fin)
        await _drain()
    text = " ".join(r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert "RuntimeError" in text and "secret" not in text
    assert done == [1] and retry._pending == 0


async def test_a_restart_during_the_wait_releases_the_slot_and_still_syncs_once(monkeypatch):
    """A shutdown cancels the waiting retry: the slot is freed and the CRM record is not lost."""
    state = _wire(monkeypatch, [BAD])
    waiting = asyncio.Event()
    real_event_wait = asyncio.Event().wait  # never set: the wait only ends by cancellation

    async def _wait_until_cancelled(_delay):
        waiting.set()
        await real_event_wait()

    monkeypatch.setattr(retry.asyncio, "sleep", _wait_until_cancelled)
    await persister._safe_generate(object(), "tenant-1", CID)
    assert state == {"gen": 1, "crm": 0} and retry._pending == 1
    (task,) = list(retry._tasks)
    await waiting.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert retry._pending == 0
    assert state == {"gen": 1, "crm": 1}      # asked once, synced once, nothing asked again
