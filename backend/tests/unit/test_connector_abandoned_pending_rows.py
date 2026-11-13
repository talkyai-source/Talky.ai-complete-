"""D3-6: abandoned OAuth attempts do not stay pending forever.

Each Connect/Reconnect click inserts a pending connector row. A sign-in window
closed without any callback left that row pending for good (a denied or
cancelled one is now rejected by the callback). When an attempt for the same
tenant/type/provider activates, every OLDER pending attempt of that provider
can never complete (activation already refuses an older attempt), so it is
closed in the same transaction and named in the activation audit. A newer
pending attempt stays open: its callback still supersedes this one.

The redirect_uri the frontend sends to authorize was required but never used:
it is now optional and documented as ignored.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.api.v1.endpoints import connectors as connectors_ep
from app.services import connector_lifecycle as lifecycle_module
from app.services.connector_lifecycle import ConnectorActor, ConnectorLifecycle

TENANT = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


def _parent(status, minutes_ago):
    return {"id": uuid4(), "tenant_id": UUID(TENANT), "type": "calendar", "provider": "google_calendar",
            "status": status, "config": {}, "created_at": NOW - timedelta(minutes=minutes_ago)}


class Conn:
    def __init__(self, parents):
        self.parents = parents
        self.executed = []

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        if " ".join(sql.split()).startswith("SELECT * FROM connectors"):
            return [dict(p) for p in self.parents]
        return []

    async def fetchrow(self, sql, *args):
        return {"id": uuid4(), "credential_generation": 1}


class Audit:
    def __init__(self):
        self.records = []

    async def lock_transaction(self, conn):
        return None

    async def log_in_transaction(self, conn, **entry):
        self.records.append(entry)
        return uuid4()


@pytest.fixture
def lifecycle(monkeypatch):
    audit = Audit()
    life = ConnectorLifecycle(type("DB", (), {"pool": object()})(), actor=ConnectorActor.oauth("u-1"),
                              audit_logger=audit)

    async def begin(conn, tenant_id, permission=None):
        return life.actor

    life._begin = begin
    return life, audit


async def _activate(monkeypatch, life, parents, target):
    conn = Conn(parents)

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_kwargs):
        yield conn

    monkeypatch.setattr(lifecycle_module, "acquire_with_tenant", acquire)
    await life.activate(TENANT, str(target["id"]), "google_calendar", "calendar",
                        {"access_token_encrypted": "enc"}, {})
    return conn


async def test_activation_closes_the_providers_other_abandoned_attempts(monkeypatch, lifecycle):
    life, audit = lifecycle
    older_abandoned = _parent("pending", 30)
    target = _parent("pending", 5)
    conn = await _activate(monkeypatch, life, [older_abandoned, target], target)

    closed = [args for sql, args in conn.executed
              if sql.startswith("UPDATE connectors SET status='error'") and "status='pending'" in sql]
    assert closed, "abandoned pending attempts must be closed"
    assert [str(i) for i in closed[0][1]] == [str(older_abandoned["id"])]
    activated = audit.records[-1]
    assert activated["action"] == "connector.oauth_activated"
    assert activated["metadata"]["abandoned_pending_ids"] == [str(older_abandoned["id"])]


async def test_a_newer_pending_attempt_stays_open_when_an_older_one_activates(monkeypatch, lifecycle):
    # The newer sign-in can still complete, and then it supersedes this one; closing it
    # would make the older authorization win (concurrent callbacks keep the newest).
    life, audit = lifecycle
    older_abandoned = _parent("pending", 30)
    target = _parent("pending", 5)
    newer = _parent("pending", 1)
    conn = await _activate(monkeypatch, life, [older_abandoned, target, newer], target)

    closed = [args for sql, args in conn.executed
              if sql.startswith("UPDATE connectors SET status='error'") and "status='pending'" in sql]
    assert [str(i) for i in closed[0][1]] == [str(older_abandoned["id"])]
    assert audit.records[-1]["metadata"]["abandoned_pending_ids"] == [str(older_abandoned["id"])]


async def test_activation_with_no_other_pending_attempt_closes_nothing(monkeypatch, lifecycle):
    life, audit = lifecycle
    target = _parent("pending", 5)
    conn = await _activate(monkeypatch, life, [target], target)
    assert not [sql for sql, _ in conn.executed if "status='pending'" in sql and sql.startswith("UPDATE connectors SET status='error'")]
    assert audit.records[-1]["metadata"]["abandoned_pending_ids"] == []


def test_authorize_no_longer_requires_the_unused_redirect_uri():
    import inspect

    default = inspect.signature(connectors_ep.authorize_connector_by_type).parameters["redirect_uri"].default
    assert default.default is None
    assert "ignored" in (default.description or "").lower()
