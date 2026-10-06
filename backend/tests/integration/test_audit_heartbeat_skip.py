"""Migration 0048 against a real Postgres: heartbeat-only updates are not
audited, every real change still is.

Production, 2026-10-07: 3,663,315 of 3,663,542 audit rows were trunk updates
whose only changed columns were live_status_checked_at and updated_at, written
every 15 seconds by the trunk status updater.

Runs only with an explicit TEST_DATABASE_URL. Everything happens inside one
transaction that is rolled back, so it is safe against a shared database.
"""
from __future__ import annotations

import importlib
import os
import uuid

import pytest
from sqlalchemy import create_engine, text

MIGRATION = importlib.import_module("Alembic.versions.0048_audit_skip_heartbeat")


def _dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


_AUDIT_TABLE_IF_MISSING = """
CREATE TABLE IF NOT EXISTS public.tenant_policy_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    record_id UUID NOT NULL,
    action VARCHAR(16) NOT NULL,
    actor_user_id UUID,
    actor_type VARCHAR(16) NOT NULL,
    request_id VARCHAR(128),
    correlation_id VARCHAR(128),
    before_payload JSONB,
    after_payload JSONB,
    changed_fields TEXT[] NOT NULL DEFAULT '{}',
    source VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _exercise(function_sql: str) -> list[tuple[str, list[str]]]:
    """Install ``function_sql``, run one row through a trunk's lifecycle, and
    return the (action, changed_fields) rows the trigger wrote. Rolled back."""
    engine = create_engine(_dsn_or_skip())
    probe = f"audit_probe_{uuid.uuid4().hex[:12]}"
    tenant = str(uuid.uuid4())
    row = str(uuid.uuid4())
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET LOCAL app.bypass_rls = 'true'"))
            conn.execute(text(_AUDIT_TABLE_IF_MISSING))
            conn.execute(text(function_sql))
            conn.execute(text(f"""
                CREATE TABLE public.{probe} (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    live_registration_status TEXT,
                    live_status_checked_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                f"CREATE TRIGGER trg_{probe} AFTER INSERT OR UPDATE OR DELETE ON public.{probe} "
                "FOR EACH ROW EXECUTE FUNCTION public.log_tenant_policy_mutation()"
            ))
            p = {"id": row, "t": tenant}
            conn.execute(text(f"INSERT INTO public.{probe} (id, tenant_id) VALUES (:id, :t)"), p)
            # The updater's heartbeat, twice, and a no-op update.
            for _ in range(2):
                conn.execute(text(
                    f"UPDATE public.{probe} SET live_status_checked_at = clock_timestamp(), "
                    "updated_at = clock_timestamp() WHERE id = :id"
                ), p)
            conn.execute(text(f"UPDATE public.{probe} SET is_active = is_active WHERE id = :id"), p)
            # Real events: a status flip (with its heartbeat stamp) and a disable.
            conn.execute(text(
                f"UPDATE public.{probe} SET live_registration_status = 'registered', "
                "live_status_checked_at = clock_timestamp() WHERE id = :id"
            ), p)
            conn.execute(text(f"UPDATE public.{probe} SET is_active = FALSE WHERE id = :id"), p)
            conn.execute(text(f"DELETE FROM public.{probe} WHERE id = :id"), p)
            rows = conn.execute(text(
                "SELECT action, changed_fields FROM public.tenant_policy_audit_log "
                "WHERE table_name = :tbl ORDER BY created_at, action"
            ), {"tbl": probe}).all()
            return [(r[0], list(r[1])) for r in rows]
        finally:
            tx.rollback()
            engine.dispose()


def test_heartbeat_only_updates_are_not_audited_and_real_changes_are():
    rows = _exercise(MIGRATION.FUNCTION_SQL)
    actions = [a for a, _ in rows]
    assert actions.count("INSERT") == 1
    assert actions.count("DELETE") == 1
    # created_at is the transaction time for every row, so match by content.
    updates = [set(fields) for a, fields in rows if a == "UPDATE"]
    assert len(updates) == 2, rows  # the status flip and the disable, nothing else
    flip = next(f for f in updates if "live_registration_status" in f)
    assert "live_status_checked_at" in flip  # still recorded alongside a real change
    disable = next(f for f in updates if f is not flip)
    assert "is_active" in disable


def test_the_previous_body_audited_every_heartbeat():
    """The production body before 0048: the same lifecycle writes 5 UPDATE rows
    (two heartbeats, one no-op, two real changes)."""
    rows = _exercise(MIGRATION.PREVIOUS_FUNCTION_SQL)
    assert [a for a, _ in rows].count("UPDATE") == 5
