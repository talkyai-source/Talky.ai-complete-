"""Real SQL for C5: only a disposable localhost database, never production."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.lead_capture_service import LeadCaptureService
from app.domain.services.call_summary.business_details import (
    persist_business_details, save_summary_details, summary_snapshot, transcript_revision,
)
from app.domain.services.call_summary import store


@pytest_asyncio.fixture
async def detail_db(monkeypatch):
    dsn = os.getenv("TALKY_CRM_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TALKY_CRM_TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Only a disposable localhost *_test database is allowed")
    suffix = uuid4().hex
    schema, role = "details_" + suffix, "details_role_" + suffix
    admin = await asyncpg.connect(dsn)
    pool = None
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        await admin.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        await admin.execute(f"SET search_path TO {schema}")
        await admin.execute("""
            CREATE TABLE calls (id UUID PRIMARY KEY, tenant_id UUID, campaign_id UUID, lead_id UUID,
              transcript TEXT, transcript_json JSONB, action_results JSONB, summary_json JSONB,
              summary TEXT, status TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE leads (id UUID PRIMARY KEY, tenant_id UUID, follow_up_note TEXT,
              is_lead BOOLEAN DEFAULT TRUE, updated_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE call_lead_details (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
              call_id UUID, campaign_id UUID, lead_id UUID, field_key TEXT, field_type TEXT,
              value TEXT, source TEXT, confirmed BOOLEAN, is_required BOOLEAN, raw_value TEXT,
              normalized_value TEXT, validation_status TEXT, confirmed_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(call_id, field_key));
            CREATE TABLE campaign_lead_fields (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
              campaign_id UUID, field_key TEXT, label TEXT, field_type TEXT DEFAULT 'text',
              is_required BOOLEAN DEFAULT FALSE, agent_visible BOOLEAN DEFAULT TRUE,
              user_visible BOOLEAN DEFAULT TRUE, options JSONB, sort_order INTEGER DEFAULT 0,
              UNIQUE(campaign_id, field_key));
        """)
        # Seed the historical terminal spellings plus an active call before migration.
        from app.domain.services.call_status import TERMINAL_CALL_STATUSES
        await admin.executemany("INSERT INTO calls(id,tenant_id,status) VALUES($1,$2,$3)",
            [(uuid4(), uuid4(), status) for status in (*TERMINAL_CALL_STATUSES, "in_progress")])
        spec = importlib.util.spec_from_file_location("detail_migration",
            Path(__file__).resolve().parents[2] / "Alembic/versions/0049_lead_detail_evidence.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        monkeypatch.setattr(migration, "op", SimpleNamespace(execute=lambda stmt: statements.append(str(stmt))))
        migration.upgrade()
        async with admin.transaction():
            for statement in statements:
                await admin.execute(statement)
        for table in ("calls", "leads", "call_lead_details", "campaign_lead_fields"):
            await admin.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            await admin.execute(f"CREATE POLICY tenant_scope ON {table} USING (tenant_id=current_setting('app.current_tenant_id',true)::uuid) WITH CHECK (tenant_id=current_setting('app.current_tenant_id',true)::uuid)")
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        await admin.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}")
        async def init(conn):
            await conn.execute(f"SET ROLE {role}")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init, server_settings={"search_path": schema})
        yield admin, pool
    finally:
        if pool:
            await pool.close()
        await admin.execute("SET search_path TO public")
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def seed(admin, *, lead=None, age="0 seconds"):
    tenant = str(uuid4()) if lead is None else lead[0]
    lead_id = str(uuid4()) if lead is None else lead[1]
    call_id = str(uuid4())
    if lead is None:
        await admin.execute("INSERT INTO leads(id,tenant_id,follow_up_note) VALUES($1::uuid,$2::uuid,'Operator note')", lead_id, tenant)
    row = {"transcript": "Caller: I use Stripe.", "transcript_json": [{"role": "user", "content": "I use Stripe."}],
           "action_results": {}, "lead_id": lead_id, "campaign_id": None}
    await admin.execute("""INSERT INTO calls(id,tenant_id,lead_id,transcript,transcript_json,action_results,summary_transcript_hash,status,created_at)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5::jsonb,'{}'::jsonb,$6,'completed',NOW()+($7::text)::interval)""",
        call_id, tenant, lead_id, row["transcript"], json.dumps(row["transcript_json"]), transcript_revision(row), age)
    return tenant, call_id, row


async def test_historical_terminal_calls_are_not_reported_as_queued(detail_db):
    from app.domain.services.call_status import TERMINAL_CALL_STATUSES
    admin, _ = detail_db
    statuses = {row["status"]: row["lead_details_status"]
                for row in await admin.fetch("SELECT status,lead_details_status FROM calls")}
    assert {status: statuses[status] for status in TERMINAL_CALL_STATUSES} == {
        status: "not_processed" for status in TERMINAL_CALL_STATUSES
    }
    assert statuses["in_progress"] == "pending"


def detail(quote="I use Stripe."):
    return {"business_details": [{"field_key": "current_provider", "value": "Stripe", "source_quote": quote}]}


async def test_capture_fences_revisions_tenants_and_operator_edits(detail_db):
    admin, pool = detail_db
    tenant, call, row = await seed(admin)
    assert await persist_business_details(pool, tenant, call, detail(), row) == 1
    service = LeadCaptureService(pool)
    assert await service.details_for_call(str(uuid4()), call) == []
    await service.capture(tenant_id=tenant, call_id=call, field_key="current_provider", value="Operator correction", source="manual_edit", field_type="notes")
    assert await persist_business_details(pool, tenant, call, detail(), row) == 0
    assert (await service.details_for_call(tenant, call))[0]["value"] == "Operator correction"
    await admin.execute("UPDATE calls SET transcript_json='[]'::jsonb WHERE id=$1::uuid", call)
    assert not await service.capture(tenant_id=tenant, call_id=call, field_key="identified_need", value="Stale", source="caller_stated", field_type="notes",
        expected_transcript=row["transcript"], expected_summary_hash=transcript_revision(row), expected_summary_snapshot=summary_snapshot(row))


async def test_revised_extraction_removes_stale_ai_note_but_keeps_manual(detail_db):
    admin, pool = detail_db
    tenant, call, row = await seed(admin)
    await persist_business_details(pool, tenant, call, detail(), row)
    service = LeadCaptureService(pool)
    await service.capture(tenant_id=tenant, call_id=call, field_key="next_owner", value="Operator owns this", source="manual_edit", field_type="notes")
    row["transcript"] += " Actually that is wrong."
    row["transcript_json"].append({"role": "user", "content": "Actually that is wrong."})
    await admin.execute("UPDATE calls SET transcript=$2,transcript_json=$3::jsonb,summary_transcript_hash=$4 WHERE id=$1::uuid",
                        call, row["transcript"], json.dumps(row["transcript_json"]), transcript_revision(row))
    await save_summary_details(pool, tenant, call, {"business_details": []}, row)
    rows = await service.details_for_call(tenant, call)
    assert [(r["field_key"], r["value"]) for r in rows] == [("next_owner", "Operator owns this")]
    assert await service.processing_status(tenant, call_id=call) == "complete"


async def test_latest_analysis_updates_qualified_lead_and_old_jobs_cannot_revert_it(detail_db):
    admin, pool = detail_db
    tenant, old_call, old_row = await seed(admin, age="-1 day")
    _, new_call, new_row = await seed(admin, lead=(tenant, old_row["lead_id"]))
    await store.refresh_latest_analysis(pool, tenant, new_call, {"next_step": "Caller withdrew callback"}, transcript_revision(new_row), snapshot=summary_snapshot(new_row))
    await store.refresh_latest_analysis(pool, tenant, old_call, {"next_step": "Schedule callback"}, transcript_revision(old_row), snapshot=summary_snapshot(old_row))
    lead = await admin.fetchrow("SELECT * FROM leads WHERE id=$1::uuid", old_row["lead_id"])
    assert lead["follow_up_note"] == "Operator note"
    assert lead["latest_analysis_note"] == "Caller withdrew callback"
    assert str(lead["latest_analysis_call_id"]) == new_call


async def test_old_summary_regenerates_then_cache_reuses_actual_revision(detail_db, monkeypatch):
    admin, pool = detail_db
    tenant, call, row = await seed(admin)
    await admin.execute("UPDATE calls SET summary_json=$2::jsonb WHERE id=$1::uuid", call, json.dumps({"headline": "Legacy"}))
    fresh = {"headline": "Current", "next_step": "No further action", "business_details": []}
    summarize = AsyncMock(return_value=fresh)
    monkeypatch.setattr(store, "summarize_transcript", summarize)
    monkeypatch.setattr(store, "mark_lead_from_summary", AsyncMock())
    assert await store.generate_and_store(pool, tenant, call) == fresh
    assert await store.generate_and_store(pool, tenant, call) == fresh
    summarize.assert_awaited_once()
    assert await LeadCaptureService(pool).processing_status(tenant, call_id=call) == "complete"


# ── Post-call capture of the campaign's own fields (2026-10-14) ──────────────

CAMPAIGN_TURNS = ["I'm the operations manager.", "Mornings are best for me.",
                  "He also wants the brochure by post.", "Ring me as soon as possible."]


async def seed_campaign_call(admin, fields):
    """A call under a campaign that defines ``fields`` (key, label, type, options)."""
    tenant, call, row = await seed(admin)
    campaign = str(uuid4())
    row = {**row, "campaign_id": campaign,
           "transcript": "\n".join("User: " + text for text in CAMPAIGN_TURNS),
           "transcript_json": [{"role": "user", "content": text} for text in CAMPAIGN_TURNS]}
    await admin.execute(
        "UPDATE calls SET campaign_id=$2::uuid, transcript=$3, transcript_json=$4::jsonb, summary_transcript_hash=$5 WHERE id=$1::uuid",
        call, campaign, row["transcript"], json.dumps(row["transcript_json"]), transcript_revision(row))
    for key, label, kind, options in fields:
        await admin.execute(
            "INSERT INTO campaign_lead_fields(tenant_id,campaign_id,field_key,label,field_type,options) "
            "VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb)",
            tenant, campaign, key, label, kind, json.dumps(options) if options else None)
    return tenant, call, campaign, row


CAMPAIGN_FIELDS = [("job_title", "Job title", "text", None), ("best_time_to_call", "Best time to call", "text", None),
                   ("calling_notes", "Calling notes", "notes", None), ("email", "Email", "email", None),
                   ("channel", "Channel", "single_select", ["WhatsApp", "Email"])]

CAMPAIGN_SUMMARY = {"business_details": [
    {"field_key": "job_title", "value": "Operations manager", "source_quote": "operations manager"},
    {"field_key": "best_time_to_call", "value": "Mornings", "source_quote": "Mornings are best"},
    {"field_key": "calling_notes", "value": "Wants the brochure by post", "source_quote": "brochure by post"},
    {"field_key": "timeline", "value": "As soon as possible", "source_quote": "as soon as possible"},
    {"field_key": "email", "value": "boss@example.com", "source_quote": "operations manager"},
    {"field_key": "channel", "value": "Carrier pigeon", "source_quote": "Mornings are best"},
]}


async def test_campaign_fields_are_saved_post_call_and_manual_or_live_rows_survive(detail_db):
    admin, pool = detail_db
    tenant, call, _, row = await seed_campaign_call(admin, CAMPAIGN_FIELDS)
    service = LeadCaptureService(pool)
    # What the call already holds that the post-call path does not own.
    await service.capture(tenant_id=tenant, call_id=call, field_key="best_time_to_call",
                          value="After 10am (operator)", source="manual_edit", field_type="text")
    await service.capture(tenant_id=tenant, call_id=call, field_key="calling_notes", value="Live note",
                          source="caller_stated", field_type="notes", evidence={"status": "live"})

    # job_title + timeline are written; the manual and live rows are not touched;
    # the email-typed field and the answer outside the select's options are never saved.
    assert await persist_business_details(pool, tenant, call, CAMPAIGN_SUMMARY, row) == 2
    saved = {r["field_key"]: r for r in await service.details_for_call(tenant, call)}
    assert set(saved) == {"job_title", "best_time_to_call", "calling_notes", "timeline"}
    job = saved["job_title"]
    assert (job["value"], job["field_type"], job["source"], job["confirmed"]) == (
        "Operations manager", "text", "caller_stated", False)
    assert job["evidence"]["source_quote"] == "I'm the operations manager."
    assert job["evidence"]["extraction"] == "post_call" and job["evidence"]["status"] == "needs_review"
    assert (saved["timeline"]["value"], saved["timeline"]["field_type"]) == ("As soon as possible", "notes")
    assert (saved["best_time_to_call"]["value"], saved["best_time_to_call"]["source"]) == ("After 10am (operator)", "manual_edit")
    assert (saved["calling_notes"]["value"], saved["calling_notes"]["source"]) == ("Live note", "caller_stated")

    # Idempotent for the same revision ...
    assert await persist_business_details(pool, tenant, call, CAMPAIGN_SUMMARY, row) == 2
    # ... and a retracted extraction removes only this path's own rows.
    await save_summary_details(pool, tenant, call, {"business_details": []}, row)
    left = {r["field_key"] for r in await service.details_for_call(tenant, call)}
    assert left == {"best_time_to_call", "calling_notes"}
    assert await service.processing_status(tenant, call_id=call) == "complete"


async def test_a_confirmed_row_is_not_replaced_by_the_post_call_note(detail_db):
    admin, pool = detail_db
    tenant, call, _, row = await seed_campaign_call(admin, CAMPAIGN_FIELDS)
    service = LeadCaptureService(pool)
    await service.capture(tenant_id=tenant, call_id=call, field_key="job_title", value="Director",
                          source="caller_stated", field_type="text", confirmed=True, evidence={"status": "confirmed"})
    assert await persist_business_details(pool, tenant, call, CAMPAIGN_SUMMARY, row) == 3
    saved = {r["field_key"]: r for r in await service.details_for_call(tenant, call)}
    assert (saved["job_title"]["value"], saved["job_title"]["confirmed"]) == ("Director", True)


async def test_another_tenants_campaign_definitions_are_unreadable_even_without_rls(detail_db):
    admin, _ = detail_db
    tenant, _, campaign, _ = await seed_campaign_call(admin, CAMPAIGN_FIELDS)
    schema = (await admin.fetchval("SHOW search_path")).split(",")[0].strip().strip('"')
    # A superuser pool: RLS does not apply, so only the SQL predicate protects the tenant.
    superuser = await asyncpg.create_pool(os.environ["TALKY_CRM_TEST_DATABASE_URL"], min_size=1, max_size=1,
                                          server_settings={"search_path": schema})
    try:
        service = LeadCaptureService(superuser)
        assert await service.fields_for_campaign(str(uuid4()), campaign) == []
        assert [f["field_key"] for f in await service.fields_for_campaign(tenant, campaign)] == [
            "best_time_to_call", "calling_notes", "channel", "email", "job_title"]
    finally:
        await superuser.close()
