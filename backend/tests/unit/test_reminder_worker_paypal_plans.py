"""The reminder worker's PayPal plans loop: sweeps only when PayPal is set up, survives errors."""
from __future__ import annotations

import importlib
import logging

import pytest

from app.domain.services import paypal_mode, paypal_subscriptions


@pytest.fixture
def world(monkeypatch):
    from app.core import dotenv_compat

    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *_args, **_kwargs: False)
    module = importlib.import_module("app.workers.reminder_worker")
    worker = module.ReminderWorker()
    worker._db_pool = object()
    worker.running = True

    state = {"mode": "test", "result": {"ended": 0, "checked": 0, "failed": 0}, "error": None,
             "sweeps": 0, "pools": [], "sleeps": []}

    class FakeService:
        def __init__(self, pool):
            state["pools"].append(pool)

        async def sweep(self, limit=50):
            state["sweeps"] += 1
            if state["error"]:
                raise state["error"]
            return state["result"]

    async def fake_sleep(delay):
        state["sleeps"].append(delay)
        worker.running = False

    monkeypatch.setattr(paypal_mode, "paypal_provider_mode", lambda: state["mode"])
    monkeypatch.setattr(paypal_subscriptions, "PayPalSubscriptionService", FakeService)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    return worker, state


async def test_loop_sweeps_with_the_worker_pool_when_paypal_is_set_up(world, caplog):
    worker, state = world
    state["result"] = {"ended": 1, "checked": 3, "failed": 0}
    caplog.set_level(logging.INFO)
    await worker._paypal_plans_loop()
    assert state["sweeps"] == 1 and state["pools"] == [worker._db_pool]
    assert state["sleeps"] == [300]
    assert "PayPal plans sweep" in caplog.text


async def test_loop_stays_quiet_when_a_sweep_did_nothing(world, caplog):
    worker, state = world
    caplog.set_level(logging.INFO)
    await worker._paypal_plans_loop()
    assert state["sweeps"] == 1 and "PayPal plans sweep:" not in caplog.text


async def test_loop_does_not_sweep_when_paypal_is_not_configured(world):
    worker, state = world
    state["mode"] = None
    await worker._paypal_plans_loop()
    assert state["sweeps"] == 0 and state["pools"] == [] and state["sleeps"] == [300]


async def test_loop_survives_an_exception_and_logs_only_its_type(world, caplog):
    worker, state = world
    secret = "".join(["db", "-", "password", "-", "leak"])
    state["error"] = RuntimeError(secret)
    caplog.set_level(logging.INFO)
    await worker._paypal_plans_loop()
    assert state["sweeps"] == 1 and state["sleeps"] == [300]
    assert "PayPal plans sweep failed: RuntimeError" in caplog.text
    assert secret not in caplog.text
