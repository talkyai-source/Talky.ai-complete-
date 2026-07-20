"""Defect 4 fix — every knowledge WRITE site must invalidate the retrieval
cache (app/services/scripts/knowledge/cache.py) so an operator's edit can't
serve a stale answer to a live caller for up to the 45s TTL.

Covers all four write sites found by grep for campaign_knowledge_nodes /
campaign_knowledge_sources mutations:
  1. ingest_service.ingest_markdown          (upload endpoint's sole write path)
  2. campaign_knowledge.update_node          (PATCH endpoint)
  3. campaign_knowledge.delete_source        (DELETE endpoint)
  4. campaign_admin.apply_or_preview_knowledge_node_changes (assistant tool)

Each test also asserts the invalidation is FAIL-SOFT: a raising
invalidate_campaign must not fail the write itself.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_TENANT = str(uuid.uuid4())
_CAMPAIGN = str(uuid.uuid4())


class _FakeConn:
    """Minimal asyncpg-connection stand-in. fetchval() branches on the query
    text so callers that chain several statements each get a sane value."""

    def __init__(self):
        self._node_seq = 0

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query, *args, **kwargs):
        return "OK"

    async def fetchval(self, query, *args, **kwargs):
        q = query
        if "INSERT INTO campaign_knowledge_sources" in q:
            return str(uuid.uuid4())
        if "INSERT INTO campaign_knowledge_nodes" in q:
            self._node_seq += 1
            return str(uuid.uuid4())
        if "SUM(token_count)" in q:
            return 0
        if "SELECT 1 FROM campaigns" in q:
            return 1
        if "RETURNING id" in q:
            return str(uuid.uuid4())
        return None

    async def fetchrow(self, query, *args, **kwargs):
        return {
            "heading": "Old heading",
            "content": "Old content",
            "keywords": [],
            "example_questions": [],
            "enabled": True,
            "priority": 0,
            "summary": "",
            "voice_answer": "",
        }

    async def fetch(self, query, *args, **kwargs):
        return []


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self, timeout=None):
        conn = self._conn

        @asynccontextmanager
        async def _cm():
            yield conn

        return _cm()


def _fake_acquire_with_tenant(conn: _FakeConn):
    @asynccontextmanager
    async def _acquire_with_tenant(pool, tenant_id, *, timeout=None):
        yield conn

    return _acquire_with_tenant


# ─────────────────────────── 1. ingest_markdown ───────────────────────────

async def test_ingest_markdown_invalidates_cache_on_success():
    from app.services.scripts.knowledge import ingest_service

    conn = _FakeConn()
    pool = _FakePool(conn)

    with patch.object(
        ingest_service, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign"
    ) as mock_inval:
        result = await ingest_service.ingest_markdown(
            pool,
            campaign_id=_CAMPAIGN,
            tenant_id=_TENANT,
            raw_md="# Rates\nOur standard rate is $50/hr.",
            filename="rates.md",
        )

    assert result["node_count"] >= 1
    mock_inval.assert_called_once_with(_TENANT, _CAMPAIGN)


async def test_ingest_markdown_invalidation_failure_does_not_fail_ingest():
    from app.services.scripts.knowledge import ingest_service

    conn = _FakeConn()
    pool = _FakePool(conn)

    with patch.object(
        ingest_service, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign",
        side_effect=RuntimeError("boom"),
    ):
        # Must not raise — invalidation is fail-soft.
        result = await ingest_service.ingest_markdown(
            pool,
            campaign_id=_CAMPAIGN,
            tenant_id=_TENANT,
            raw_md="# Rates\nOur standard rate is $50/hr.",
            filename="rates.md",
        )
    assert result["node_count"] >= 1


# ─────────────────────── 2 & 3. campaign_knowledge endpoints ───────────────

class _User:
    def __init__(self, tenant_id):
        self.tenant_id = tenant_id


async def test_update_node_endpoint_invalidates_cache():
    from app.api.v1.endpoints import campaign_knowledge as ck

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

    with patch.object(ck, "knowledge_enabled", return_value=True), patch.object(
        ck, "_assert_campaign_owned", AsyncMock(return_value=None)
    ), patch.object(
        ck, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign"
    ) as mock_inval:
        result = await ck.update_node(
            campaign_id=_CAMPAIGN,
            node_id=str(uuid.uuid4()),
            payload={"voice_answer": "New answer"},
            current_user=_User(_TENANT),
            db_client=_DB(),
        )

    assert result["updated"] == ["voice_answer"]
    mock_inval.assert_called_once_with(_TENANT, _CAMPAIGN)


async def test_update_node_invalidation_failure_does_not_fail_request():
    from app.api.v1.endpoints import campaign_knowledge as ck

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

    with patch.object(ck, "knowledge_enabled", return_value=True), patch.object(
        ck, "_assert_campaign_owned", AsyncMock(return_value=None)
    ), patch.object(
        ck, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign",
        side_effect=RuntimeError("boom"),
    ):
        result = await ck.update_node(
            campaign_id=_CAMPAIGN,
            node_id=str(uuid.uuid4()),
            payload={"voice_answer": "New answer"},
            current_user=_User(_TENANT),
            db_client=_DB(),
        )
    assert result["updated"] == ["voice_answer"]


async def test_delete_source_endpoint_invalidates_cache():
    from app.api.v1.endpoints import campaign_knowledge as ck

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

    with patch.object(ck, "knowledge_enabled", return_value=True), patch.object(
        ck, "_assert_campaign_owned", AsyncMock(return_value=None)
    ), patch.object(
        ck, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign"
    ) as mock_inval:
        result = await ck.delete_source(
            campaign_id=_CAMPAIGN,
            source_id=str(uuid.uuid4()),
            current_user=_User(_TENANT),
            db_client=_DB(),
        )

    assert result["deleted"]
    mock_inval.assert_called_once_with(_TENANT, _CAMPAIGN)


async def test_delete_source_invalidation_failure_does_not_fail_request():
    from app.api.v1.endpoints import campaign_knowledge as ck

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

    with patch.object(ck, "knowledge_enabled", return_value=True), patch.object(
        ck, "_assert_campaign_owned", AsyncMock(return_value=None)
    ), patch.object(
        ck, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign",
        side_effect=RuntimeError("boom"),
    ):
        result = await ck.delete_source(
            campaign_id=_CAMPAIGN,
            source_id=str(uuid.uuid4()),
            current_user=_User(_TENANT),
            db_client=_DB(),
        )
    assert result["deleted"]


# ───────────────────── 4. assistant tool write path ───────────────────────

async def test_assistant_tool_update_node_invalidates_cache():
    from app.infrastructure.assistant.tools import campaign_admin as ca

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

        def table(self, *_a, **_k):  # pragma: no cover - not used by this path
            raise AssertionError("this write path uses the pool, not .table()")

    with patch.object(
        ca, "_verify_campaign_owned", AsyncMock(return_value=True)
    ), patch.object(
        ca, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign"
    ) as mock_inval:
        result = await ca.update_knowledge_node(
            tenant_id=_TENANT,
            db_client=_DB(),
            campaign_id=_CAMPAIGN,
            node_id=str(uuid.uuid4()),
            changes={"voice_answer": "Updated answer"},
            confirm=True,
        )

    assert result.get("applied") is True
    mock_inval.assert_called_once_with(_TENANT, _CAMPAIGN)


async def test_assistant_tool_invalidation_failure_does_not_fail_request():
    from app.infrastructure.assistant.tools import campaign_admin as ca

    conn = _FakeConn()

    class _DB:
        pool = _FakePool(conn)

    with patch.object(
        ca, "_verify_campaign_owned", AsyncMock(return_value=True)
    ), patch.object(
        ca, "acquire_with_tenant", _fake_acquire_with_tenant(conn)
    ), patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign",
        side_effect=RuntimeError("boom"),
    ):
        result = await ca.update_knowledge_node(
            tenant_id=_TENANT,
            db_client=_DB(),
            campaign_id=_CAMPAIGN,
            node_id=str(uuid.uuid4()),
            changes={"voice_answer": "Updated answer"},
            confirm=True,
        )
    assert result.get("applied") is True
