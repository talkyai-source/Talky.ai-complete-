"""Dry-run retention purge reports the real expired total, not just the batch."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints.admin import media
from app.workers import recording_retention_purge as purge

ACTOR = str(uuid4())


class _Conn:
    """Honours the LIMIT bind like the real statement does."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.calls.append(("fetch", args))
        return self.rows[: args[0]]

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", args))
        return args[0]


def _rows(n):
    return [
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "call_id": uuid4(),
            "s3_bucket": "local",
            "s3_key": f"rec-{i}.wav",
            "retention_days": 30,
        }
        for i in range(n)
    ]


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(rows=[], conn=None, claims=[])

    @asynccontextmanager
    async def acquire(_pool, tenant):
        state.conn = _Conn(state.rows)
        yield state.conn

    async def claim(_db, **kwargs):
        state.claims.append(kwargs)
        return None  # "already_gone"; nothing is erased

    monkeypatch.setattr(purge, "acquire_with_tenant", acquire)
    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    return state


def _purge_logs(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "recording_retention_purge"]


@pytest.mark.asyncio
async def test_dry_run_reports_real_total_beyond_batch(env, caplog):
    env.rows = _rows(250)
    caplog.set_level(logging.DEBUG)
    summary = await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=200)
    assert summary["would_delete"] == 200
    assert summary["would_delete_total"] == 250
    assert summary["would_delete_total_capped"] is False
    assert any("would_delete=200 would_delete_total=250 " in m for m in _purge_logs(caplog))
    assert env.claims == []


@pytest.mark.asyncio
async def test_dry_run_total_reaching_cap_is_reported_as_at_least(env, caplog, monkeypatch):
    monkeypatch.setattr(purge, "DRY_RUN_TOTAL_CAP", 300)
    env.rows = _rows(400)
    caplog.set_level(logging.DEBUG)
    summary = await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=200)
    assert summary["would_delete"] == 200
    assert summary["would_delete_total"] == 300
    assert summary["would_delete_total_capped"] is True
    assert any("would_delete_total=at_least_300 " in m for m in _purge_logs(caplog))


@pytest.mark.asyncio
async def test_dry_run_total_read_uses_the_cap_as_limit(env):
    env.rows = _rows(5)
    await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=2)
    limits = [a[0] for kind, a in env.conn.calls if kind == "fetch"] if env.conn else []
    # conn is recreated per acquire; the last one saw the cap read
    assert limits == [purge.DRY_RUN_TOTAL_CAP]


@pytest.mark.asyncio
async def test_enabled_branch_makes_the_same_database_calls(env):
    env.rows = _rows(3)
    summary = await purge.purge_expired_recordings(object(), enabled=True, actor_id=ACTOR, limit=2)
    # one acquire only (a single batch read), same batch size, no total keys
    assert env.conn.calls == [
        ("fetchval", ("public.recordings_s3",)),
        ("fetchval", ("public.tenant_recording_policy",)),
        ("fetchval", ("public.suspension_events",)),
        ("fetchval", ("public.tenants",)),
        ("fetch", (2,)),
    ]
    assert "would_delete_total" not in summary
    assert summary["already_gone"] == 2


@pytest.mark.asyncio
async def test_dry_run_deletes_nothing(env):
    env.rows = _rows(250)
    summary = await purge.purge_expired_recordings(object(), enabled=False, actor_id=None, limit=200)
    assert summary["deleted"] == 0 and env.claims == []
