"""F2-03: a provider-ID bootstrap cannot guess between distinct tenant owners.

Execute the real discovery SELECT in SQLite with narrow PostgreSQL cast
adaptation. The existing state writer is recorded at its call boundary; no
real provider, PostgreSQL RLS or live call is touched.
"""
from contextlib import asynccontextmanager
import re
import sqlite3
from unittest.mock import AsyncMock

import pytest

from app.domain.services import call_status


OWNER = "a1111111-bbbb-4111-8111-ccccccccccc1"
OTHER = "a2222222-bbbb-4222-8222-ccccccccccc2"
CALL = "c3333333-bbbb-4333-8333-ddddddddddd3"
NEWER_CALL = "f6666666-bbbb-4666-8666-aaaaaaaaaaa6"


class Connection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE calls (id TEXT, tenant_id TEXT, campaign_id TEXT, provider_call_id TEXT, external_call_uuid TEXT, provider TEXT, created_at TEXT)")
        self.entries = self.releases = 0

    def add(self, call_id, tenant, *, channel="shared", external="external", created="2026-01-01"):
        self.db.execute("INSERT INTO calls VALUES (?, ?, NULL, ?, ?, 'asterisk', ?)", (call_id, tenant, channel, external, created))

    async def fetchrow(self, query, *args):
        query = re.sub(r"::(?:text|uuid)\b", "", query)
        bindings = {}

        def parameter(match):
            index = int(match.group(1))
            bindings[f"p{index}"] = args[index - 1]
            return f":p{index}"

        row = self.db.execute(re.sub(r"\$(\d+)", parameter, query), bindings).fetchone()
        return dict(row) if row else None


@pytest.fixture
def runtime(monkeypatch):
    conn = Connection()

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        assert pool is conn and tenant_id is None
        conn.entries += 1
        try:
            yield conn
        finally:
            conn.releases += 1

    writer = AsyncMock()
    monkeypatch.setattr(call_status, "acquire_with_tenant", acquire)
    monkeypatch.setattr(call_status, "record_call_state", writer)
    try:
        yield conn, writer
    finally:
        conn.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["provider", "external"])
async def test_shared_provider_identity_never_projects_state_into_an_arbitrary_tenant(runtime, identity):
    conn, writer = runtime
    if identity == "provider":
        conn.add(CALL, OWNER, external="own-external")
        conn.add(NEWER_CALL, OTHER, external="other-external", created="2026-02-01")
    else:
        conn.add(CALL, OWNER, channel="own-channel", external="shared")
        conn.add(NEWER_CALL, OTHER, channel="other-channel", external="shared", created="2026-02-01")

    await call_status.record_call_state_by_provider_id(conn, provider_call_id="shared", provider="asterisk", new_state="answered")
    writer.assert_not_awaited()
    assert conn.entries == conn.releases == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_same_tenant", [False, True])
async def test_single_tenant_discovery_preserves_latest_call_selection(runtime, newer_same_tenant):
    conn, writer = runtime
    conn.add(CALL, OWNER)
    if newer_same_tenant:
        conn.add(NEWER_CALL, OWNER, created="2026-02-01")
    await call_status.record_call_state_by_provider_id(conn, provider_call_id="shared", provider="asterisk", new_state="answered")
    writer.assert_awaited_once()
    assert writer.await_args.kwargs["tenant_id"] == OWNER
    assert writer.await_args.kwargs["call_id"] == (NEWER_CALL if newer_same_tenant else CALL)
    assert writer.await_args.kwargs["new_state"] == "answered"
    assert conn.entries == conn.releases == 1


@pytest.mark.asyncio
async def test_unknown_provider_identity_has_no_state_write(runtime):
    conn, writer = runtime
    await call_status.record_call_state_by_provider_id(conn, provider_call_id="absent", provider="asterisk", new_state="answered")
    writer.assert_not_awaited()
    assert conn.entries == conn.releases == 1
