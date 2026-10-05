"""Preserved pre-repair reproduction against committed source; local synthetic PG only.

Run from backend with explicit TEST_DATABASE_URL using pytest on this file.
The expected red assertion follows the completed fake queue handoff. It loads
only the two named baseline modules in memory; it does not replace source files.
"""
import asyncio
import json
import os
import subprocess
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core import postgres_adapter
from app.core.security.tenant_isolation import set_bypass_rls
from tests.integration.test_ag05_lead_evidence import lead_db  # noqa: F401
from tests.integration.test_ag06_action_cancellation import cancel_db, seed_action  # noqa: F401

BASELINE = "71cdeefa59a0f786f236d3a3d25ba592470ddf05"


def original(path, name):
    source = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], text=True)
    module = types.ModuleType(name)
    module.__package__ = name.rpartition(".")[0]
    exec(compile(source, path, "exec"), module.__dict__)
    return module


async def test_baseline_callback_cancellation_during_queue_handoff(cancel_db, monkeypatch):
    db = cancel_db
    callbacks = original("backend/app/services/voice_callback_service.py", "app.services.ag06_baseline_callback")
    admin = original("backend/app/api/v1/endpoints/admin/actions.py", "app.api.v1.endpoints.admin.ag06_baseline_actions")
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    action, _ = await seed_action(db, callback=True)
    entered, release = asyncio.Event(), asyncio.Event()
    jobs = []

    async def publish(job, **_kwargs):
        jobs.append(job.job_id)
        entered.set()
        await release.wait()
        return True

    queue = SimpleNamespace(schedule_job_once=publish, confirm_retry_once=AsyncMock())
    worker = asyncio.create_task(callbacks.drain_voice_callbacks(db.pool, queue))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        set_bypass_rls(True)  # Existing platform-admin adapter context; local test DB only.
        result = await admin.cancel_action(str(action), db.actor, postgres_adapter.Client(db.pool))
    finally:
        set_bypass_rls(False)
        release.set()
        await asyncio.wait_for(worker, timeout=5)
    receipt = await db.admin.fetchval("SELECT status FROM assistant_actions WHERE id=$1", action)
    job_status = await db.admin.fetchval("SELECT status FROM dialer_jobs WHERE id=$1::uuid", jobs[0])
    print(json.dumps({"baseline_commit": BASELINE, "cancel_reply": result["new_status"],
                      "receipt_status": receipt, "job_status": job_status, "synthetic_queue_submissions": len(jobs)}))
    assert not (result["new_status"] == "cancelled" and job_status == "queued"), "Cancellation was falsely reported after dispatch reservation"
