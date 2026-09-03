from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import campaign_knowledge as knowledge_api


class _User:
    id = "11111111-1111-1111-1111-111111111111"
    tenant_id = "22222222-2222-2222-2222-222222222222"


CAMPAIGN_ID = "33333333-3333-3333-3333-333333333333"
SOURCE_ID = "44444444-4444-4444-4444-444444444444"


def _request_lease(conn, *, mutate: bool):
    return knowledge_api._KnowledgeMutationLease(
        conn=conn,
        tenant_id=_User.tenant_id,
        direction="outbound",
        current_user=_User(),
        campaign_id=CAMPAIGN_ID,
        mutate=mutate,
    )


@pytest.mark.asyncio
async def test_get_knowledge_uses_one_authorized_connection_and_tenant_predicates(
    monkeypatch,
):
    monkeypatch.setattr(knowledge_api, "knowledge_enabled", lambda: True)
    class _NoPool:
        def acquire(self):
            raise AssertionError("GET opened a second database connection")

    class _Conn:
        async def fetchval(self, query, *args):
            assert "tenant_id" in query
            assert args == (CAMPAIGN_ID, _User.tenant_id)
            return "inline"

        async def fetch(self, query, *args):
            assert "tenant_id" in query
            assert args == (CAMPAIGN_ID, _User.tenant_id)
            return []

    result = await knowledge_api.get_knowledge(
        CAMPAIGN_ID,
        current_user=_User(),
        db_client=SimpleNamespace(pool=_NoPool()),
        lease=_request_lease(_Conn(), mutate=False),
    )

    assert result == {
        "campaign_id": CAMPAIGN_ID,
        "knowledge_mode": "inline",
        "sources": [],
        "tree": [],
    }


@pytest.mark.asyncio
async def test_retrieval_test_reuses_read_lease_and_surfaces_database_failures(
    monkeypatch,
):
    monkeypatch.setattr(knowledge_api, "knowledge_enabled", lambda: True)
    conn = object()
    retrieve = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(
        "app.services.scripts.knowledge.retrieval.retrieve_knowledge",
        retrieve,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await knowledge_api.test_retrieval(
            CAMPAIGN_ID,
            {"query": "coverage"},
            current_user=_User(),
            db_client=SimpleNamespace(pool=object()),
            lease=_request_lease(conn, mutate=False),
        )

    assert retrieve.await_args.kwargs["conn"] is conn
    assert retrieve.await_args.kwargs["raise_on_error"] is True


@pytest.mark.asyncio
async def test_upload_commits_processing_before_unlocked_enrichment_then_reauthorizes(
    monkeypatch,
):
    events: list[str] = []
    active_leases = 0
    connection_number = 0

    class _Conn:
        def __init__(self, number: int) -> None:
            self.number = number

        async def fetchval(self, query, *args):
            assert "knowledge_model" in query
            assert "tenant_id" in query
            assert args == (CAMPAIGN_ID, _User.tenant_id)
            events.append("model")
            return "model-a"

    @asynccontextmanager
    async def access(*, db_client, current_user, campaign_id, mutate):
        nonlocal active_leases, connection_number
        assert current_user.tenant_id == _User.tenant_id
        assert campaign_id == CAMPAIGN_ID
        assert mutate is True
        connection_number += 1
        conn = _Conn(connection_number)
        active_leases += 1
        events.append(f"lease:{connection_number}:enter")
        try:
            yield _request_lease(conn, mutate=True)
        finally:
            events.append(f"lease:{connection_number}:commit")
            active_leases -= 1

    class _Upload:
        filename = "guide.md"

        async def read(self, size=-1):
            assert size == knowledge_api._MAX_UPLOAD_BYTES + 1
            events.append("read")
            return b"# Guide\nAnswer"

    prepared = object()

    def prepare(raw_md):
        assert raw_md == "# Guide\nAnswer"
        events.append("prepare")
        return prepared

    async def recover(_pool, **kwargs):
        assert active_leases == 1
        assert kwargs["conn"].number == 2
        events.append("recover")
        return 0

    async def create(_pool, **kwargs):
        assert active_leases == 1
        assert kwargs["conn"].number == 2
        events.append("create")
        return SOURCE_ID

    async def enrich(value):
        assert value is prepared
        assert active_leases == 0
        events.append("enrich")
        return [object()]

    async def persist(conn, **kwargs):
        assert active_leases == 1
        assert conn.number == 3
        assert kwargs["source_id"] == SOURCE_ID
        assert kwargs["model"] == "model-a"
        events.append("persist")
        return {"source_id": SOURCE_ID, "node_count": 1, "mode": "inline"}

    monkeypatch.setattr(knowledge_api, "knowledge_enabled", lambda: True)
    monkeypatch.setattr(knowledge_api, "_http_knowledge_access", access)
    monkeypatch.setattr(knowledge_api, "prepare_markdown", prepare)
    monkeypatch.setattr(knowledge_api, "recover_stale_processing_sources", recover)
    monkeypatch.setattr(knowledge_api, "create_processing_source", create)
    monkeypatch.setattr(knowledge_api, "enrich_prepared_document", enrich)
    monkeypatch.setattr(knowledge_api, "persist_prepared_document", persist)
    monkeypatch.setattr(
        knowledge_api,
        "_invalidate_campaign_cache_fail_soft",
        lambda *_args: events.append("invalidate"),
    )

    result = await knowledge_api.upload_knowledge(
        CAMPAIGN_ID,
        _Upload(),
        current_user=_User(),
        db_client=SimpleNamespace(pool=object()),
    )

    assert result["source_id"] == SOURCE_ID
    assert events == [
        "lease:1:enter",
        "lease:1:commit",
        "read",
        "prepare",
        "lease:2:enter",
        "recover",
        "create",
        "lease:2:commit",
        "enrich",
        "lease:3:enter",
        "model",
        "persist",
        "lease:3:commit",
        "invalidate",
    ]


@pytest.mark.asyncio
async def test_upload_failure_marks_visible_source_failed_without_holding_lease(
    monkeypatch,
):
    active_leases = 0
    marked: list[tuple[str, int]] = []

    @asynccontextmanager
    async def access(**_kwargs):
        nonlocal active_leases
        active_leases += 1
        try:
            yield _request_lease(SimpleNamespace(fetchval=AsyncMock()), mutate=True)
        finally:
            active_leases -= 1

    class _Upload:
        filename = "guide.md"

        async def read(self, _size=-1):
            return b"# Guide\nAnswer"

    async def create(*_args, **_kwargs):
        return SOURCE_ID

    async def explode(_prepared):
        assert active_leases == 0
        raise RuntimeError("provider exploded")

    async def mark(_pool, **kwargs):
        marked.append((kwargs["source_id"], active_leases))
        return False

    monkeypatch.setattr(knowledge_api, "knowledge_enabled", lambda: True)
    monkeypatch.setattr(knowledge_api, "_http_knowledge_access", access)
    monkeypatch.setattr(knowledge_api, "prepare_markdown", lambda _raw: object())
    monkeypatch.setattr(
        knowledge_api,
        "recover_stale_processing_sources",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(knowledge_api, "create_processing_source", create)
    monkeypatch.setattr(knowledge_api, "enrich_prepared_document", explode)
    monkeypatch.setattr(knowledge_api, "mark_source_failed_durably", mark)

    with pytest.raises(knowledge_api.HTTPException) as exc:
        await knowledge_api.upload_knowledge(
            CAMPAIGN_ID,
            _Upload(),
            current_user=_User(),
            db_client=SimpleNamespace(pool=object()),
        )

    assert exc.value.status_code == 500
    assert marked == [(SOURCE_ID, 0)]
