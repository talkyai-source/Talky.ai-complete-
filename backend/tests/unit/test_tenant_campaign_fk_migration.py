"""Contract tests for the tenant-safe campaign relationship migration."""

from __future__ import annotations

import importlib

import pytest


def _migration():
    return importlib.import_module("Alembic.versions.0041_tenant_campaign_fk")


def test_0041_preflights_then_installs_only_supported_tenant_safe_fks(monkeypatch):
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    assert migration.down_revision == "0040_calls_campaign_nullable"
    preflight = next(
        statement
        for statement in statements
        if "0041 tenant/campaign preflight failed" in statement
    )
    assert sql.index(preflight) < sql.index("ADD CONSTRAINT leads_id_tenant_unique")

    for table in ("leads", "calls", "dialer_jobs", "assistant_actions"):
        assert (
            f"ALTER TABLE public.{table} ADD CONSTRAINT {table}_campaign_tenant_fk "
            "FOREIGN KEY (campaign_id, tenant_id) "
            "REFERENCES public.campaigns (id, tenant_id) NOT VALID"
        ) in sql
        assert f"VALIDATE CONSTRAINT {table}_campaign_tenant_fk" in sql

    assert "contact_lists_campaign_tenant_fk" in sql
    assert (
        "FOREIGN KEY (campaign_id, tenant_id) REFERENCES public.campaigns "
        "(id, tenant_id) ON DELETE CASCADE NOT VALID"
    ) in sql
    assert "leads_list_campaign_tenant_fk" in sql
    assert "call_lead_details_call_tenant_fk" in sql
    assert "call_lead_details_lead_tenant_fk" in sql
    assert "call_lead_details_campaign_tenant_fk" in sql
    assert "recordings_s3_call_tenant_fk" in sql
    assert "recordings_s3_campaign_tenant_fk" in sql
    # A view cannot own a foreign key. The campaign constraints establish only
    # tenant-safe campaign existence; they deliberately do not claim that a
    # denormalized campaign snapshot equals the authoritative call campaign.
    assert "ALTER TABLE public.billable_calls" not in sql
    assert "call_lead_details.campaign" in preflight
    assert "recordings_s3.campaign" in preflight

    # Trigger guards cannot repair rows that already exist.  Upgrade must
    # refuse every outbound-only artifact already attached to an inbound
    # campaign, while deliberately retaining deleted-lead history.
    for relation in (
        "leads.live_inbound_campaign",
        "contact_lists.inbound_campaign",
        "dialer_jobs.inbound_campaign",
        "calls.outbound_inbound_campaign",
    ):
        assert relation in preflight
    assert "child.status IS DISTINCT FROM 'deleted'" in preflight
    assert "child.direction = 'outbound'" in preflight
    assert "parent.direction IS DISTINCT FROM 'outbound'" in preflight


def test_0041_adopts_contact_lists_for_a_canonical_fresh_bootstrap(monkeypatch):
    """0040 has no Alembic-owned contact-list schema; 0041 must supply it.

    The legacy installation SQL was applied manually on production, so all
    creation DDL must also be idempotent.  Ownership is recorded before any
    object is created so downgrade can retain a table/column that pre-dated
    this Alembic revision.
    """

    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    marker_at = sql.index("talky_0041_contact_lists_adoption")
    table_at = sql.index("CREATE TABLE IF NOT EXISTS public.contact_lists")
    column_at = sql.index(
        "ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS list_id UUID"
    )
    preflight_at = sql.index("0041 tenant/campaign preflight failed")

    assert marker_at < table_at < preflight_at
    assert marker_at < column_at < preflight_at
    assert "CREATE INDEX IF NOT EXISTS idx_contact_lists_campaign" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_leads_list_id" in sql
    assert "ALTER TABLE public.contact_lists ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE public.contact_lists FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY contact_lists_tenant_isolation" in sql
    assert "app.bypass_rls" in sql
    assert "app.current_tenant_id" in sql


def test_0041_repairs_the_recordings_table_skipped_by_the_documented_floor(
    monkeypatch,
):
    """The supported complete_schema + stamp-0008 path skips 0001's DDL."""

    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    create_at = sql.index("CREATE TABLE IF NOT EXISTS public.recordings_s3")
    preflight_at = sql.index("0041 tenant/campaign preflight failed")
    assert create_at < preflight_at
    assert "ALTER TABLE public.recordings_s3 ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE public.recordings_s3 FORCE ROW LEVEL SECURITY" in sql

    statements.clear()
    migration.downgrade()
    downgrade_sql = " ".join(statements)
    # This is a forward repair of the historical baseline, not revision-owned
    # business data; downgrading relationship constraints must retain it.
    assert "DROP TABLE public.recordings_s3" not in downgrade_sql


def test_0041_downgrade_keeps_preexisting_contact_list_objects(monkeypatch):
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.downgrade()

    sql = " ".join(statements)
    assert "talky_0041_contact_lists_adoption" in sql
    assert "IF adoption.contact_lists_preexisting THEN" in sql
    assert "DROP TABLE public.contact_lists" in sql
    assert "IF NOT adoption.leads_list_id_preexisting THEN" in sql
    assert "ALTER TABLE public.leads DROP COLUMN list_id" in sql


def test_0041_serializes_outbound_artifacts_against_conversion(monkeypatch):
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    assert "CREATE FUNCTION public.talky_require_outbound_campaign_artifact()" in sql
    assert "FROM public.campaigns" in sql
    assert "FOR SHARE" in sql
    assert "CREATE TRIGGER leads_outbound_campaign_guard" in sql
    assert "CREATE TRIGGER contact_lists_outbound_campaign_guard" in sql
    assert "CREATE TRIGGER dialer_jobs_outbound_campaign_guard" in sql
    assert "CREATE TRIGGER calls_outbound_campaign_guard" in sql
    # Real inbound calls and retained deleted leads must not be rejected.
    assert "WHEN (NEW.direction = 'outbound')" in sql
    assert "WHEN (NEW.status IS DISTINCT FROM 'deleted')" in sql
    # PostgresAdapter intentionally returns str(asyncpg_error), so the stable
    # trigger identifier must be present in the human message as well as in
    # asyncpg's separate constraint_name field.
    assert "'%_outbound_campaign_guard: outbound artifact cannot target an inbound campaign'" in sql
    assert "TG_TABLE_NAME" in sql


def test_0041_downgrade_removes_only_its_reversible_constraints(monkeypatch):
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.downgrade()

    sql = " ".join(statements)
    assert "DROP CONSTRAINT IF EXISTS leads_campaign_tenant_fk" in sql
    assert "DROP CONSTRAINT IF EXISTS contact_lists_id_campaign_tenant_unique" in sql
    assert "ALTER COLUMN tenant_id DROP NOT NULL" in sql
    assert "DROP TRIGGER IF EXISTS calls_outbound_campaign_guard" in sql
    assert "DROP TRIGGER IF EXISTS contact_lists_outbound_campaign_guard" in sql
    assert "DROP TRIGGER IF EXISTS leads_outbound_campaign_guard" in sql
    assert "DROP TRIGGER IF EXISTS dialer_jobs_outbound_campaign_guard" in sql
    assert "DROP FUNCTION IF EXISTS public.talky_require_outbound_campaign_artifact()" in sql
    # The revision-owned marker is removed, and a contact_lists table created
    # by 0041 is conditionally removed.  No pre-existing business table is
    # dropped unconditionally.
    assert "DROP TABLE public.talky_0041_contact_lists_adoption" in sql
    assert "ELSE DROP TABLE public.contact_lists" in sql
    assert "DELETE FROM" not in sql


def test_0041_installs_the_lead_and_job_ownership_chain(monkeypatch):
    """Ownership is keyed on tenant, deliberately NOT on campaign.

    A job/call must belong to a real lead of the SAME TENANT — that is the
    isolation property worth enforcing, and production satisfies it exactly
    (0 violations across all three relations on 2026-09-03).

    Including campaign_id in the key was specified first and abandoned on
    evidence: 2749 dialer_jobs and 15 calls reference a lead that now sits on a
    different campaign — every one of them terminal (skipped/cancelled/failed/
    completed, zero active), same tenant, confined to a single campaign whose
    leads were re-pointed. "A job's campaign equals its lead's CURRENT campaign"
    is simply not true of history, and a lead may legitimately move. Asserting
    it would abort the preflight and, because talky-migrate runs on every
    deploy, block every future release to fix nothing.

    Campaign integrity is not lost: dialer_jobs_campaign_tenant_fk and
    calls_campaign_tenant_fk already tie each row's own campaign_id to a real
    campaign in the same tenant.
    """
    migration = _migration()
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda value: statements.append(" ".join(str(value).split())),
    )

    migration.upgrade()

    sql = " ".join(statements)
    assert "dialer_jobs_id_tenant_unique" in sql
    assert "dialer_jobs_lead_tenant_fk" in sql
    assert "calls_lead_tenant_fk" in sql
    assert (
        "FOREIGN KEY (lead_id, tenant_id) REFERENCES public.leads (id, tenant_id)"
    ) in sql
    assert "calls_dialer_job_tenant_fk" in sql
    assert (
        "FOREIGN KEY (dialer_job_id, tenant_id) "
        "REFERENCES public.dialer_jobs (id, tenant_id)"
    ) in sql
    # Campaign must NOT be part of the ownership key (see docstring).
    assert "REFERENCES public.leads (id, campaign_id, tenant_id)" not in sql

    preflight = next(
        statement
        for statement in statements
        if "0041 tenant/campaign preflight failed" in statement
    )
    for relation in (
        "dialer_jobs.lead_ownership",
        "calls.lead_ownership",
        "calls.dialer_job_ownership",
    ):
        assert relation in preflight
