"""A call summary the model could not produce is asked for again, in the background."""
import asyncio
import logging

import pytest

from app.domain.services.call_summary.summarizer import SUMMARY_UNAVAILABLE_HEADLINE
from app.services.scripts import call_summary_retry as retry
from app.services.scripts import call_transcript_persister as persister

CID = "ab12cd34-2222-3333-4444-555555555555"
BAD = {"headline": SUMMARY_UNAVAILABLE_HEADLINE}
GOOD = {"headline": "Wants a demo", "business_details": {}}
LOGGER = "app.services.scripts.call_summary_retry"


def _call_id(i: int) -> str:
    # Shaped like a real call id. A run of digits would be read as a phone number
    # and masked by the product's log redaction when the whole suite runs.
    return f"ab12cd{i:02d}-aaaa-bbbb-cccc-dddddddddddd"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(retry.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(retry, "_pending", 0)
    retry._tasks.clear()


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
        await asyncio.gather(*list(retry._tasks), return_exceptions=True)


async def test_the_crm_record_is_written_at_once_and_completed_when_a_retry_stores_the_summary(monkeypatch):
    state = _wire(monkeypatch, [BAD, GOOD])
    await persister._safe_generate(object(), "t", CID)
    assert state == {"gen": 1, "crm": 1}       # written now, as before this change
    await _drain()
    assert state == {"gen": 2, "crm": 2}       # completed once the summary exists


async def test_when_every_attempt_fails_nothing_more_is_written(monkeypatch):
    state = _wire(monkeypatch, [BAD, BAD, BAD])
    await persister._safe_generate(object(), "t", CID)
    await _drain()
    assert state == {"gen": 3, "crm": 1}


async def test_a_good_first_attempt_is_unchanged(monkeypatch):
    state = _wire(monkeypatch, [GOOD])
    await persister._safe_generate(object(), "t", CID)
    assert not retry._tasks
    assert state == {"gen": 1, "crm": 1}


async def test_a_first_attempt_that_raises_is_not_retried_and_the_record_is_still_written(monkeypatch):
    state = {"crm": 0}

    async def boom(pool, tenant, call):
        raise RuntimeError("model down")

    async def fake_crm(call, **kwargs):
        state["crm"] += 1

    monkeypatch.setattr(persister, "generate_and_store", boom)
    monkeypatch.setattr("app.services.crm_sync_service.run_crm_sync", fake_crm)
    await persister._safe_generate(object(), "t", CID)
    assert not retry._tasks and state == {"crm": 1}


async def test_a_retry_that_stores_nothing_stops_and_writes_nothing_more(monkeypatch):
    state = _wire(monkeypatch, [BAD, None, GOOD])
    await persister._safe_generate(object(), "t", CID)
    await _drain()
    assert state == {"gen": 2, "crm": 1}       # the third result was never asked for


async def test_fifth_retry_dropped_with_log_line(monkeypatch, caplog):
    gate = asyncio.Event()

    async def blocked_sleep(_d):
        await gate.wait()

    monkeypatch.setattr(retry.asyncio, "sleep", blocked_sleep)
    done = []

    async def gen():
        return GOOD

    async def stored():
        done.append(1)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        results = [retry.start_retries(_call_id(i), gen, stored) for i in range(5)]
    assert results == [True, True, True, True, False]
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert any(f"call_id={_call_id(4)[:12]}" in m and "outcome=dropped" in m for m in lines)
    gate.set()
    await _drain()
    assert len(done) == 4 and retry._pending == 0


async def test_exception_in_retry_is_logged_not_raised(monkeypatch, caplog):
    done = []

    async def gen():
        raise RuntimeError("boom secret transcript")

    async def stored():
        done.append(1)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert retry.start_retries(CID, gen, stored)
        await _drain()
    text = " ".join(r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert "RuntimeError" in text and "secret" not in text
    assert done == [] and retry._pending == 0   # nothing stored, so nothing more is written


async def test_a_restart_during_the_wait_frees_the_slot_and_loses_nothing(monkeypatch):
    """The CRM record was already written at once; a cancelled retry only gives its slot back."""
    state = _wire(monkeypatch, [BAD])
    waiting = asyncio.Event()
    never = asyncio.Event()

    async def _wait_until_cancelled(_delay):
        waiting.set()
        await never.wait()

    monkeypatch.setattr(retry.asyncio, "sleep", _wait_until_cancelled)
    await persister._safe_generate(object(), "tenant-1", CID)
    assert state == {"gen": 1, "crm": 1} and retry._pending == 1
    (task,) = list(retry._tasks)
    await waiting.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()                     # the shutdown is not held up
    assert retry._pending == 0
    assert state == {"gen": 1, "crm": 1}        # nothing asked again, nothing written twice


def test_the_waits_are_about_one_and_five_minutes():
    assert retry.RETRY_DELAYS_SECONDS == (60.0, 300.0)
    assert 0 < retry.MAX_JITTER_SECONDS <= 30 and retry.MAX_PENDING == 4
