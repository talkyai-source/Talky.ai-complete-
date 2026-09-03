from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services.scripts.knowledge.enricher import NodeEnrichment


pytestmark = pytest.mark.asyncio

TENANT_ID = str(uuid.uuid4())
CAMPAIGN_ID = str(uuid.uuid4())
SOURCE_ID = str(uuid.uuid4())


class _Transaction:
    def __init__(self, conn: "_Connection") -> None:
        self.conn = conn
        self.pending: list[tuple[str, tuple[object, ...]]] = []

    async def __aenter__(self):
        self.conn.transactions.append(self)
        self.conn.active_transaction = self
        self.conn.events.append("transaction:begin")
        return self

    async def __aexit__(self, exc_type, _exc, _tb):
        if exc_type is None:
            self.conn.committed.extend(self.pending)
            self.conn.events.append("transaction:commit")
        else:
            self.conn.events.append("transaction:rollback")
        self.conn.active_transaction = None
        return False


class _Connection:
    def __init__(
        self,
        *,
        fail_on_node: int | None = None,
        failure_started: asyncio.Event | None = None,
        failure_release: asyncio.Event | None = None,
    ) -> None:
        self.fail_on_node = fail_on_node
        self.failure_started = failure_started
        self.failure_release = failure_release
        self.node_number = 0
        self.transactions: list[_Transaction] = []
        self.active_transaction: _Transaction | None = None
        self.committed: list[tuple[str, tuple[object, ...]]] = []
        self.events: list[str] = []
        self.failed_errors: list[str] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    def _record(self, query: str, args: tuple[object, ...]) -> None:
        normalized = " ".join(query.split())
        item = (normalized, args)
        if self.active_transaction is None:
            self.committed.append(item)
        else:
            self.active_transaction.pending.append(item)

    async def fetchval(self, query: str, *args):
        normalized = " ".join(query.split())
        self._record(query, args)
        if "INSERT INTO campaign_knowledge_sources" in normalized:
            return SOURCE_ID
        if "INSERT INTO campaign_knowledge_nodes" in normalized:
            self.node_number += 1
            if self.node_number == self.fail_on_node:
                raise RuntimeError("node insert failed")
            return str(uuid.uuid4())
        if "SET status" in normalized and "'ready'" in normalized:
            return SOURCE_ID
        if "SUM(token_count)" in normalized:
            return 10
        if "stale_processing_recovered" in normalized:
            return 2
        return None

    async def execute(self, query: str, *args):
        normalized = " ".join(query.split())
        self._record(query, args)
        if "SET status" in normalized and "'failed'" in normalized:
            if self.failure_started is not None:
                self.failure_started.set()
            if self.failure_release is not None:
                await self.failure_release.wait()
            self.failed_errors.append(str(args[-1]))
        return "UPDATE 1"


class _Pool:
    pass


def _tenant_acquirer(conn: _Connection, *, events: list[str] | None = None):
    @asynccontextmanager
    async def acquire(_pool, _tenant_id, **_kwargs):
        if events is not None:
            events.append("acquire")
        async with conn.transaction():
            yield conn
        if events is not None:
            events.append("release")

    return acquire


def _two_node_markdown() -> str:
    return "# First\nOne\n# Second\nTwo"


async def test_prepare_rejects_documents_above_configured_node_cap(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    monkeypatch.setenv("KNOWLEDGE_INGEST_MAX_NODES", "1")

    with pytest.raises(ingest_service.KnowledgeNodeLimitError, match="1 nodes"):
        ingest_service.prepare_markdown(_two_node_markdown())


async def test_enrichment_has_one_absolute_timeout_and_bounded_batches(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    prepared = ingest_service.prepare_markdown(_two_node_markdown(), max_nodes=2)
    calls: list[int] = []

    async def never_finishes(batch):
        calls.append(len(batch))
        await asyncio.Event().wait()

    monkeypatch.setattr(ingest_service, "enrich_nodes", never_finishes)

    with pytest.raises(
        ingest_service.KnowledgeEnrichmentTimeoutError,
        match="absolute timeout",
    ):
        await ingest_service.enrich_prepared_document(
            prepared,
            timeout_seconds=0.01,
            batch_size=1,
            max_batches=2,
        )

    assert calls == [1]


async def test_enrichment_refuses_work_above_batch_cap(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    prepared = ingest_service.prepare_markdown(_two_node_markdown(), max_nodes=2)
    called = False

    async def should_not_run(_batch):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(ingest_service, "enrich_nodes", should_not_run)

    with pytest.raises(ingest_service.KnowledgeNodeLimitError, match="batch cap"):
        await ingest_service.enrich_prepared_document(
            prepared,
            timeout_seconds=1,
            batch_size=1,
            max_batches=1,
        )

    assert called is False


async def test_atomic_persist_rolls_back_every_node_when_a_late_insert_fails():
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection(fail_on_node=2)
    prepared = ingest_service.prepare_markdown(_two_node_markdown(), max_nodes=2)
    enrichments = [NodeEnrichment(), NodeEnrichment()]

    with pytest.raises(RuntimeError, match="node insert failed"):
        await ingest_service.persist_prepared_document(
            conn,
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            prepared=prepared,
            enrichments=enrichments,
        )

    assert conn.events == ["transaction:begin", "transaction:rollback"]
    assert not any("campaign_knowledge_nodes" in query for query, _ in conn.committed)
    assert not any("status='ready'" in query for query, _ in conn.committed)
    assert not any("UPDATE campaigns" in query for query, _ in conn.committed)


async def test_ingest_marks_committed_source_failed_when_enrichment_errors(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()
    events: list[str] = []

    async def broken_enrichment(_prepared, **_kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn, events=events),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        broken_enrichment,
    )

    with pytest.raises(RuntimeError, match="provider exploded"):
        await ingest_service.ingest_markdown(
            _Pool(),
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            raw_md="# One\nBody",
        )

    assert any("provider exploded" in error for error in conn.failed_errors)
    assert events.count("release") >= 2


async def test_processing_source_commit_finishes_before_enrichment_starts(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()
    events: list[str] = []

    async def observe_committed_source(prepared, **_kwargs):
        assert events[-1] == "release"
        assert events.count("release") == 2  # stale recovery, then source create
        assert any(
            "INSERT INTO campaign_knowledge_sources" in query
            for query, _args in conn.committed
        )
        return [NodeEnrichment() for _ in prepared.nodes]

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn, events=events),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        observe_committed_source,
    )

    await ingest_service.ingest_markdown(
        _Pool(),
        campaign_id=CAMPAIGN_ID,
        tenant_id=TENANT_ID,
        raw_md="# One\nBody",
        invalidate_cache=False,
    )


async def test_timeout_is_persisted_as_stable_failure_code(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()

    async def timed_out(_prepared, **_kwargs):
        raise ingest_service.KnowledgeEnrichmentTimeoutError("provider detail")

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        timed_out,
    )

    with pytest.raises(ingest_service.KnowledgeEnrichmentTimeoutError):
        await ingest_service.ingest_markdown(
            _Pool(),
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            raw_md="# One\nBody",
        )

    assert conn.failed_errors == ["knowledge_enrichment_timeout"]


async def test_ingest_marks_committed_source_failed_before_propagating_cancellation(
    monkeypatch,
):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()
    started = asyncio.Event()

    async def blocked_enrichment(_prepared, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        blocked_enrichment,
    )

    task = asyncio.create_task(
        ingest_service.ingest_markdown(
            _Pool(),
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            raw_md="# One\nBody",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert conn.failed_errors == ["knowledge_ingest_cancelled"]


async def test_second_cancellation_cannot_interrupt_failure_status_commit(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    enrich_started = asyncio.Event()
    failure_started = asyncio.Event()
    failure_release = asyncio.Event()
    conn = _Connection(
        failure_started=failure_started,
        failure_release=failure_release,
    )

    async def blocked_enrichment(_prepared, **_kwargs):
        enrich_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        blocked_enrichment,
    )

    task = asyncio.create_task(
        ingest_service.ingest_markdown(
            _Pool(),
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            raw_md="# One\nBody",
        )
    )
    await enrich_started.wait()
    task.cancel()
    await failure_started.wait()
    task.cancel()
    failure_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert conn.failed_errors == ["knowledge_ingest_cancelled"]


async def test_caller_connection_path_never_acquires_from_pool(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()

    async def unexpected_acquire(*_args, **_kwargs):
        raise AssertionError("provided-connection path acquired from pool")

    async def no_enrichment(prepared, **_kwargs):
        return [NodeEnrichment() for _ in prepared.nodes]

    monkeypatch.setattr(ingest_service, "acquire_with_tenant", unexpected_acquire)
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        no_enrichment,
    )

    result = await ingest_service.ingest_markdown(
        _Pool(),
        campaign_id=CAMPAIGN_ID,
        tenant_id=TENANT_ID,
        raw_md="# One\nBody",
        conn=conn,
        invalidate_cache=False,
    )

    assert result["source_id"] == SOURCE_ID
    assert result["node_count"] == 1


async def test_stale_processing_recovery_is_tenant_and_campaign_scoped(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()
    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn),
    )

    recovered = await ingest_service.recover_stale_processing_sources(
        _Pool(),
        tenant_id=TENANT_ID,
        campaign_id=CAMPAIGN_ID,
        stale_after_seconds=123,
    )

    assert recovered == 2
    query, args = next(
        (query, args)
        for query, args in conn.committed
        if "stale_processing_recovered" in query
    )
    assert "tenant_id = $1" in query
    assert "campaign_id = $2" in query
    assert "status = 'processing'" in query
    assert args[:3] == (TENANT_ID, CAMPAIGN_ID, 123)


async def test_cache_invalidation_happens_after_owned_commit(monkeypatch):
    from app.services.scripts.knowledge import ingest_service

    conn = _Connection()
    events: list[str] = []

    async def no_enrichment(prepared, **_kwargs):
        return [NodeEnrichment() for _ in prepared.nodes]

    def invalidate(_tenant_id, _campaign_id):
        events.append("invalidate")

    monkeypatch.setattr(
        ingest_service,
        "acquire_with_tenant",
        _tenant_acquirer(conn, events=events),
    )
    monkeypatch.setattr(
        ingest_service,
        "enrich_prepared_document",
        no_enrichment,
    )

    with patch(
        "app.services.scripts.knowledge.cache.invalidate_campaign",
        side_effect=invalidate,
    ):
        await ingest_service.ingest_markdown(
            _Pool(),
            campaign_id=CAMPAIGN_ID,
            tenant_id=TENANT_ID,
            raw_md="# One\nBody",
        )

    assert events[-2:] == ["release", "invalidate"]
