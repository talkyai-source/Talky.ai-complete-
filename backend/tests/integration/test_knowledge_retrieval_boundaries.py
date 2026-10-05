"""Actual knowledge migration/SQL, isolated schema and non-bypass tenant role.

Only synthetic documents and local PostgreSQL are used. Enrichment is supplied
as data; this does not exercise an external LLM or a live voice call.
"""

import importlib.util
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.api.v1.endpoints import campaign_knowledge as api
from app.core.db_utils import acquire_with_tenant
from app.services.scripts.knowledge.enricher import NodeEnrichment
from app.services.scripts.knowledge.ingest_service import (
    create_processing_source,
    persist_prepared_document,
    prepare_markdown,
)
from app.services.scripts.knowledge.retrieval import (
    compact_tree,
    render_node_answer,
    retrieve_knowledge,
    retrieve_pinned_knowledge,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def knowledge_db(monkeypatch):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL is required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Knowledge tests require a disposable local *_test database")
    suffix = uuid4().hex
    schema, role = "kb_" + suffix, "kb_role_" + suffix
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        await admin.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        await admin.execute(f"SET search_path TO {schema}, public")
        await admin.execute(
            "CREATE TABLE campaigns (id UUID PRIMARY KEY, tenant_id UUID NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW())"
        )
        spec = importlib.util.spec_from_file_location(
            "knowledge_migration",
            Path(__file__).resolve().parents[2] / "Alembic/versions/0010_campaign_knowledge.py",
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        monkeypatch.setattr(
            migration, "op", SimpleNamespace(execute=lambda sql: statements.append(str(sql)))
        )
        migration.upgrade()
        for statement in statements:
            await admin.execute(statement)
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        await admin.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}"
        )

        async def init(conn):
            await conn.execute(f"SET ROLE {role}")

        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=3,
            init=init,
            server_settings={"search_path": f"{schema},public"},
        )
        tenants = [str(uuid4()), str(uuid4())]
        campaigns = [str(uuid4()), str(uuid4())]
        for tenant, campaign in zip(tenants, campaigns):
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id) VALUES ($1,$2)", campaign, tenant
            )
        yield SimpleNamespace(admin=admin, pool=pool, tenants=tenants, campaigns=campaigns)
    finally:
        if pool:
            await pool.close()
        await admin.execute("SET search_path TO public")
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def publish(
    fixture,
    *,
    index=0,
    text="# Monthly pricing\nThe monthly price is GBP 20. Taxes are excluded.",
    enrichment=None,
):
    tenant, campaign = fixture.tenants[index], fixture.campaigns[index]
    prepared = prepare_markdown(text)
    source = await create_processing_source(
        fixture.pool, tenant_id=tenant, campaign_id=campaign, raw_md=text, filename="synthetic.md"
    )
    async with acquire_with_tenant(fixture.pool, tenant) as conn:
        await persist_prepared_document(
            conn,
            tenant_id=tenant,
            campaign_id=campaign,
            source_id=source,
            prepared=prepared,
            enrichments=[enrichment or NodeEnrichment()] * len(prepared.nodes),
        )
    node = await fixture.admin.fetchval(
        "SELECT id FROM campaign_knowledge_nodes WHERE source_id=$1 ORDER BY path LIMIT 1", source
    )
    return source, str(node)


async def lookup(fixture, *, tenant=0, campaign=0):
    return await retrieve_knowledge(
        fixture.pool,
        fixture.tenants[tenant],
        fixture.campaigns[campaign],
        "monthly price",
        k=3,
        raise_on_error=True,
    )


async def test_published_current_sources_are_tenant_scoped_and_versioned(knowledge_db):
    db = knowledge_db
    source, node = await publish(db)
    await publish(db, index=1, text="# Monthly pricing\nForeign tenant secret price is GBP 777.")
    hits = await lookup(db)
    assert [str(row["id"]) for row in hits] == [node]
    assert hits[0]["content"] == "The monthly price is GBP 20. Taxes are excluded."
    assert str(hits[0]["source_id"]) == source
    assert hits[0]["source_version"] == 1 and hits[0]["version"]
    assert await lookup(db, tenant=0, campaign=1) == []
    assert "777" not in await compact_tree(db.pool, db.tenants[0], db.campaigns[1])


@pytest.mark.parametrize("status", ["processing", "failed"])
async def test_unpublished_source_nodes_are_never_live_evidence(knowledge_db, status):
    db = knowledge_db
    source, _ = await publish(db)
    await db.admin.execute(
        "UPDATE campaign_knowledge_sources SET status=$2 WHERE id=$1", source, status
    )
    assert await lookup(db) == []
    assert await compact_tree(db.pool, db.tenants[0], db.campaigns[0]) == ""


async def test_legacy_cross_tenant_source_reference_is_not_evidence(knowledge_db):
    db = knowledge_db
    _, node = await publish(db)
    other_source, _ = await publish(db, index=1)
    # Historical single-column foreign keys permit this corrupt relationship.
    await db.admin.execute(
        "UPDATE campaign_knowledge_nodes SET source_id=$2 WHERE id=$1", node, other_source
    )
    assert await lookup(db) == []
    assert await compact_tree(db.pool, db.tenants[0], db.campaigns[0]) == ""


async def test_source_edit_clears_stale_enrichment_and_advances_version(knowledge_db, monkeypatch):
    db = knowledge_db
    source, node = await publish(
        db,
        enrichment=NodeEnrichment(
            summary="Monthly price GBP 20",
            voice_answer="It costs GBP 20.",
            keywords=["oldsecretprice"],
            example_questions=["What is the oldsecretprice?"],
        ),
    )
    before = await lookup(db)
    monkeypatch.setattr(api, "knowledge_enabled", lambda: True)
    async with acquire_with_tenant(db.pool, db.tenants[0]) as conn:
        lease = api._KnowledgeMutationLease(
            conn, db.tenants[0], "outbound", SimpleNamespace(), db.campaigns[0]
        )
        await api.update_node(
            db.campaigns[0],
            node,
            {"content": "The monthly price is GBP 30. Taxes are excluded."},
            SimpleNamespace(),
            SimpleNamespace(pool=db.pool),
            lease,
        )
    hits = await lookup(db)
    assert "GBP 20" not in render_node_answer(hits[0])
    assert hits[0]["summary"] is None and hits[0]["voice_answer"] is None
    assert hits[0]["version"] != before[0]["version"]
    assert hits[0]["source_version"] == 2
    saved = await db.admin.fetchrow(
        "SELECT search_text,keywords,example_questions FROM campaign_knowledge_nodes WHERE id=$1",
        node,
    )
    assert "oldsecretprice" not in saved["search_text"]
    assert not saved["keywords"] and not saved["example_questions"]
    # Admission snapshots deliberately retain their old fact, never mix revisions.
    pinned = retrieve_pinned_knowledge(before, "monthly price")
    assert pinned[0]["content"].startswith("The monthly price is GBP 20.")
    await db.admin.execute("UPDATE campaign_knowledge_nodes SET enabled=FALSE WHERE id=$1", node)
    assert await lookup(db) == []
    assert (
        await db.admin.fetchval(
            "SELECT version FROM campaign_knowledge_sources WHERE id=$1", source
        )
        == 2
    )


async def test_compact_missing_tenant_never_acquires_a_bypass_connection(monkeypatch):
    from app.services.scripts.knowledge import retrieval

    def forbidden(*args, **kwargs):
        raise AssertionError("must not acquire an RLS bypass connection")

    monkeypatch.setattr(retrieval, "acquire_with_tenant", forbidden)
    # The existing fail-soft wrapper must not mask an attempted bypass either.
    called = []

    def tracked(*args, **kwargs):
        called.append(True)
        return forbidden(*args, **kwargs)

    monkeypatch.setattr(retrieval, "acquire_with_tenant", tracked)
    assert await compact_tree(None, None, str(uuid4())) == ""
    assert not called


async def test_ingestion_refuses_cross_tenant_campaign_before_creating_source(knowledge_db):
    db = knowledge_db
    with pytest.raises(RuntimeError):
        await create_processing_source(
            db.pool,
            tenant_id=db.tenants[0],
            campaign_id=db.campaigns[1],
            raw_md="# Wrong\nForeign campaign",
        )
    assert await db.admin.fetchval("SELECT COUNT(*) FROM campaign_knowledge_sources") == 0


async def test_concurrent_source_patches_index_the_committed_combination(knowledge_db, monkeypatch):
    db = knowledge_db
    source, node = await publish(db)
    monkeypatch.setattr(api, "knowledge_enabled", lambda: True)
    pid = asyncio.get_running_loop().create_future()

    async def patch(conn, changes):
        lease = api._KnowledgeMutationLease(
            conn, db.tenants[0], "outbound", SimpleNamespace(), db.campaigns[0]
        )
        return await api.update_node(
            db.campaigns[0], node, changes, SimpleNamespace(), SimpleNamespace(pool=db.pool), lease
        )

    async def second_patch():
        async with acquire_with_tenant(db.pool, db.tenants[0]) as conn:
            pid.set_result(await conn.fetchval("SELECT pg_backend_pid()"))
            await patch(conn, {"content": "The revised monthly price is GBP 30."})

    async with acquire_with_tenant(db.pool, db.tenants[0]) as first:
        await patch(first, {"heading": "New monthly platinum tariff"})
        task = asyncio.create_task(second_patch())
        second_pid = await pid
        # Observe actual PostgreSQL contention, not a guessed scheduling sleep.
        for _ in range(200):
            waiting = await db.admin.fetchval(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=$1", second_pid
            )
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting, "second patch must be waiting on the uncommitted first patch"
    await asyncio.wait_for(task, 2)
    row = await db.admin.fetchrow(
        "SELECT heading,content,search_text FROM campaign_knowledge_nodes WHERE id=$1", node
    )
    assert row["search_text"] == f"{row['heading']} {row['content']}"
    assert "platinum" in row["search_text"] and "GBP 30" in row["search_text"]
    assert (
        await db.admin.fetchval(
            "SELECT version FROM campaign_knowledge_sources WHERE id=$1", source
        )
        == 3
    )


async def test_actual_postgres_gold_matrix_and_excluded_controls(knowledge_db):
    from scripts.evaluate_ag02_knowledge import (
        load_matrix,
        effective_query,
        evaluate_case,
        summarize,
    )

    db = knowledge_db
    matrix = load_matrix()
    aliases = {}
    for item in matrix["nodes"] + matrix["excluded_controls"]:
        index = int(item.get("tenant_id") == "tenant-other")
        source, node = await publish(
            db, index=index, text=f"# {item['heading']}\n{item['content']}"
        )
        await db.admin.execute(
            "UPDATE campaign_knowledge_sources SET version=$2 WHERE id=$1",
            source,
            item["source_version"],
        )
        await db.admin.execute(
            "UPDATE campaign_knowledge_nodes SET enabled=$2,priority=$3 WHERE id=$1",
            node,
            item.get("enabled", True),
            item.get("priority", 0),
        )
        aliases[node] = item["id"]
    excluded = {item["id"] for item in matrix["excluded_controls"]}
    results = []
    for case in matrix["cases"]:
        hits = await retrieve_knowledge(
            db.pool, db.tenants[0], db.campaigns[0], effective_query(case), k=3, raise_on_error=True
        )
        assert not excluded.intersection(aliases[str(hit["id"])] for hit in hits)
        results.append(evaluate_case(case, hits, aliases=aliases))
    report = summarize(
        matrix,
        results,
        path="actual PostgreSQL FTS/trigram + shared evidence; migration0010 isolated schema/NOBYPASSRLS role",
    )
    report["cross_tenant_or_disabled_hits"] = 0
    report["postgres_version"] = await db.admin.fetchval("SHOW server_version")
    report["limitations"] = [
        text for text in report["limitations"] if not text.startswith("Pinned evaluation")
    ]
    report["limitations"].append(
        "Synthetic per-question tenant-scoped reads, not live call latency, model fidelity or customer-approved acceptance."
    )
    output = os.environ.get("AG02_POSTGRES_GOLD_OUTPUT")
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "question_count",
                    "answerable_count",
                    "recall_at_3",
                    "sufficient_passage_rate",
                    "retrieval_targets_met",
                    "cross_tenant_or_disabled_hits",
                )
            }
        )
    )
    # Quality targets are reported, never silently converted into SQL-test success.
    assert len(results) == len(matrix["cases"])


@pytest.mark.parametrize("field", ["keywords", "example_questions"])
async def test_generated_routing_aliases_do_not_authorize_unrelated_source(knowledge_db, field):
    from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence

    db = knowledge_db
    values = ["orbital", "guidance"] if field == "keywords" else ["What is orbital guidance?"]
    source, node = await publish(db, text="# Support\nSupport is available during business hours.",
                                 enrichment=NodeEnrichment(**{field: values}))
    hits = await retrieve_knowledge(db.pool, db.tenants[0], db.campaigns[0], "orbital guidance",
                                   k=3, raise_on_error=True)
    assert [str(hit["id"]) for hit in hits] == [node]  # Metadata still routes.
    evidence = prepare_knowledge_evidence(hits, "orbital guidance")
    assert evidence["status"] == "weak_match", evidence
    assert hits[0]["coverage"] == 0
    assert evidence["passages"][0]["source_id"] == source
    assert evidence["passages"][0]["source_version"] == 1
    assert evidence["passages"][0]["version"]


async def test_authored_source_coverage_stays_matched_with_irrelevant_metadata(knowledge_db):
    from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence

    db = knowledge_db
    _, node = await publish(db, text="# Orbital guidance\nA calibrated antenna is required.",
                            enrichment=NodeEnrichment(keywords=["gardening"],
                                                      example_questions=["How are flowers grown?"]))
    hits = await retrieve_knowledge(db.pool, db.tenants[0], db.campaigns[0], "orbital guidance",
                                   k=3, raise_on_error=True)
    assert [str(hit["id"]) for hit in hits] == [node]
    assert hits[0]["coverage"] == 1
    evidence = prepare_knowledge_evidence(hits, "orbital guidance")
    assert evidence["status"] == "matched" and "calibrated antenna" in evidence["text"]


async def test_other_nodes_generated_alias_frequency_cannot_change_source_confidence(knowledge_db):
    db = knowledge_db
    _, support = await publish(db, text="# Support\nSupport is available on weekdays.")
    _, orbital = await publish(db, text="# Orbital\nOrbital navigation uses an antenna.")

    async def source_coverage():
        hits = await retrieve_knowledge(db.pool, db.tenants[0], db.campaigns[0], "support orbital",
                                       k=3, raise_on_error=True)
        return next(hit["coverage"] for hit in hits if str(hit["id"]) == support)

    before = await source_coverage()
    # Synthetic private fixture only: aliases change, authored source does not.
    await db.admin.execute("""UPDATE campaign_knowledge_nodes
      SET search_text=search_text || ' support',
          search_tsv=to_tsvector('english', search_text || ' support'), keywords=ARRAY['support']
      WHERE id=$1""", orbital)
    after = await source_coverage()
    # Equal authored document frequencies imply equal weight, independently of aliases.
    assert before == pytest.approx(0.5)
    assert after == pytest.approx(before)
