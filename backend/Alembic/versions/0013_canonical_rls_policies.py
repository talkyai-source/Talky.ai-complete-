"""one canonical tenant-isolation policy per table, and FORCE it

Stage 1 of making row-level security real (goals.md §12, task #80).

WHY
---
Production's app role is a superuser with BYPASSRLS, so every policy in this
database has been decorative since it was written. An audit on 2026-08-22 found
65 policies across 29 tables in 9 different shapes, with three problems that
would surface the moment enforcement became real:

  * Only 11 of 29 tables honour ``app.bypass_rls``. ``db_utils.acquire_with_tenant``
    sets that GUC for genuinely cross-tenant work (admin tooling, workers,
    reapers) and ``calls.py`` sets it for the call list. On the other 18 tables
    those callers would suddenly see zero rows.

  * Two incompatible comparison styles. ``(tenant_id)::text = current_setting(...)``
    compares a uuid's text form against a raw setting string, so it fails on any
    case or whitespace difference. ``tenant_id = current_setting(...)::uuid`` is
    semantically right but RAISES on an empty string — the bug the archived
    ``20260507_fix_rls_empty_setting_cast`` was written for, still present in 14
    policies.

  * ``talkyai`` owns all 88 tables, and an owner bypasses RLS unless the table
    has FORCE ROW LEVEL SECURITY. Only 12 of 29 did.

THIS MIGRATION IS A NO-OP IN PRODUCTION TODAY, ON PURPOSE
---------------------------------------------------------
A superuser bypasses row security unconditionally — including FORCE. So every
statement below changes exactly nothing about live behaviour until the app's
database role is switched to a non-superuser without BYPASSRLS (stage 3). That
is what makes it safe to correct all 29 tables at once: the policies can be
made right, reviewed and tested against a throwaway role while production
continues to behave precisely as it does now.

THE CANONICAL SHAPE
-------------------
    bypass OR tenant_id = <current tenant>          (+ OR tenant_id IS NULL
                                                       where the column allows it)

  * ``NULLIF(current_setting(...), '')`` turns the empty string into NULL before
    the cast, so an unset or blank GUC yields NULL — comparison is false, no
    exception. Never remove the NULLIF.
  * The comparison is uuid-to-uuid, so formatting differences cannot cause a
    silent mismatch.
  * WITH CHECK mirrors USING on every table, so a tenant cannot INSERT or UPDATE
    a row into someone else's scope. Several existing policies had no WITH CHECK
    at all.

Discovery is dynamic — every public table that has RLS enabled *and* a
``tenant_id`` column — so a table added later cannot quietly keep a hand-rolled
policy that disagrees with this one.

Revision ID: 0013_canonical_rls_policies
Revises: 0012_call_feedback
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0013_canonical_rls_policies"
down_revision: str | None = "0012_call_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        DO $rls$
        DECLARE
            tbl        record;
            pol        record;
            nullable   boolean;
            null_claus text;
            using_expr text;
        BEGIN
            FOR tbl IN
                SELECT c.oid, c.relname
                FROM pg_class c
                JOIN pg_attribute a
                  ON a.attrelid = c.oid
                 AND a.attname  = 'tenant_id'
                 AND a.attnum   > 0
                 AND NOT a.attisdropped
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relkind      = 'r'
                  AND c.relrowsecurity
                ORDER BY c.relname
            LOOP
                -- A nullable tenant_id means the table legitimately holds
                -- platform-global rows (webhook_deliveries does). Denying those
                -- to everyone would be a new outage, not a fix.
                SELECT NOT a.attnotnull INTO nullable
                FROM pg_attribute a
                WHERE a.attrelid = tbl.oid AND a.attname = 'tenant_id';

                null_claus := CASE WHEN nullable THEN ' OR tenant_id IS NULL' ELSE '' END;

                using_expr :=
                    'COALESCE(NULLIF(current_setting(''app.bypass_rls'', TRUE), '''')::boolean, FALSE)'
                    || ' OR tenant_id = NULLIF(current_setting(''app.current_tenant_id'', TRUE), '''')::uuid'
                    || null_claus;

                -- Replace whatever was there. Dropping first keeps this
                -- idempotent and stops per-command leftovers (the 12 telephony
                -- tables each had four) from co-existing with the new one.
                FOR pol IN
                    SELECT policyname FROM pg_policies
                    WHERE schemaname = 'public' AND tablename = tbl.relname
                LOOP
                    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                                   pol.policyname, tbl.relname);
                END LOOP;

                EXECUTE format(
                    'CREATE POLICY %I ON public.%I FOR ALL USING (%s) WITH CHECK (%s)',
                    tbl.relname || '_tenant_isolation', tbl.relname,
                    using_expr, using_expr
                );

                -- Without this the owner (talkyai owns all 88 tables) bypasses
                -- the policy even after losing superuser.
                EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',
                               tbl.relname);
            END LOOP;
        END
        $rls$;
    """)
    )


def downgrade() -> None:
    # Lift FORCE so a table owner can read its own rows again, and drop the
    # canonical policies. The previous hand-rolled policies are NOT restored —
    # they were inconsistent by definition, and re-creating 65 policies in 9
    # shapes from memory would be worse than having none while RLS is bypassed
    # anyway. Recover them from the schema baseline if ever genuinely needed.
    op.execute(
        text("""
        DO $rls_down$
        DECLARE tbl record;
        BEGIN
            FOR tbl IN
                SELECT c.relname FROM pg_class c
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relkind = 'r'
                  AND c.relrowsecurity
            LOOP
                EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY',
                               tbl.relname);
                EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                               tbl.relname || '_tenant_isolation', tbl.relname);
            END LOOP;
        END
        $rls_down$;
    """)
    )
