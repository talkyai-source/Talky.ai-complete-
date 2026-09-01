"""force canonical tenant isolation on legacy tenant-scoped tables

Revision ID: 0038_tenant_table_rls_backfill
Revises: 0037_inbound_duration_quarantine
Create Date: 2026-09-02 00:00:00.000000

The production catalog contained 35 public tables with a ``tenant_id`` column
but no RLS policy at all.  The static acquisition inventory reported 36: two
campaign-knowledge tables were false positives because their policy DDL is
generated in a Python loop, while ``ai_config_migrations`` was a false negative
because its table DDL is generated dynamically.  The explicit ENABLE statements
below keep that static inventory honest without replacing the two already-
canonical campaign policies.

Nullable ``tenant_id`` does not make a row tenant-readable here.  Platform rows
remain available only through the transaction-local service bypass; otherwise a
tenant could create or mutate a globally visible NULL-tenant row.  Code that
needs tenant plus platform rows must use the platform acquisition path and an
explicit tenant predicate.

This is a forward-only security boundary.  Disabling RLS during downgrade would
reopen cross-tenant access, so downgrade is refused.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0038_tenant_table_rls_backfill"
down_revision: str | None = "0037_inbound_duration_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICY_TABLES: tuple[str, ...] = (
    "abuse_detection_rules",
    "abuse_events",
    "action_plans",
    "ai_config_migrations",
    "assistant_actions",
    "assistant_conversations",
    "audit_logs",
    "call_guard_decisions",
    "call_velocity_snapshots",
    "clients",
    "cloned_voices",
    "connector_accounts",
    "dialer_jobs",
    "dnc_entries",
    "invoices",
    "meetings",
    "recordings",
    "refresh_tokens",
    "reminders",
    "secret_access_log",
    "security_events",
    "subscriptions",
    "tenant_ai_configs",
    "tenant_call_limits",
    "tenant_quota_usage",
    "tenant_quotas",
    "tenant_secrets",
    "tenant_settings",
    "tenant_users",
    "transcripts",
    "usage_records",
    "user_permissions",
    "user_profiles",
    "webhook_configs",
    "white_label_partners",
)

# Migration 0033 already installs and validates these policies.  They are named
# here only because the static inventory cannot see its Python-generated DDL.
ALREADY_CANONICAL_TABLES: tuple[str, ...] = (
    "campaign_knowledge_nodes",
    "campaign_knowledge_sources",
)

_TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = "
    "NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)

# Keep these statements literal.  backend/scripts/rls_acquire_inventory.py
# discovers schema protection from literal ENABLE/POLICY SQL and deliberately
# does not import migration modules.
_ENABLE_DDL: tuple[str, ...] = (
    "ALTER TABLE public.abuse_detection_rules ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.abuse_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.action_plans ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.ai_config_migrations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.assistant_actions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.assistant_conversations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.call_guard_decisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.call_velocity_snapshots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.campaign_knowledge_nodes ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.campaign_knowledge_sources ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.cloned_voices ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.connector_accounts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.dialer_jobs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.dnc_entries ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.meetings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.recordings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.refresh_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.reminders ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.secret_access_log ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.security_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_ai_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_call_limits ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_quota_usage ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_quotas ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_secrets ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_settings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.tenant_users ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.transcripts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.usage_records ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.user_permissions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.webhook_configs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE public.white_label_partners ENABLE ROW LEVEL SECURITY",
)


def _sql_array(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _guard_catalog_and_policy_ownership() -> None:
    expected = _sql_array(POLICY_TABLES)
    op.execute(
        text(
            f"""
            DO $ownership$
            DECLARE malformed_tables text;
            DECLARE conflicting_tables text;
            BEGIN
                WITH expected(table_name) AS (
                    SELECT unnest(ARRAY[{expected}]::text[])
                ), catalog_state AS (
                    SELECT
                        e.table_name,
                        c.relkind,
                        format_type(a.atttypid, a.atttypmod) AS tenant_id_type
                    FROM expected AS e
                    LEFT JOIN pg_class AS c
                      ON c.oid = to_regclass('public.' || e.table_name)
                    LEFT JOIN pg_attribute AS a
                      ON a.attrelid = c.oid
                     AND a.attname = 'tenant_id'
                     AND a.attnum > 0
                     AND NOT a.attisdropped
                )
                SELECT string_agg(table_name, ', ' ORDER BY table_name)
                  INTO malformed_tables
                  FROM catalog_state
                 WHERE relkind IS DISTINCT FROM 'r'
                    OR tenant_id_type IS DISTINCT FROM 'uuid';

                IF malformed_tables IS NOT NULL THEN
                    RAISE EXCEPTION
                        '0038 found unexpected table/tenant_id catalog shape on: %. '
                        'Every target must be a physical table with tenant_id UUID.',
                        malformed_tables;
                END IF;

                SELECT string_agg(DISTINCT p.tablename, ', ' ORDER BY p.tablename)
                  INTO conflicting_tables
                  FROM pg_policies AS p
                 WHERE p.schemaname = 'public'
                   AND p.tablename = ANY (ARRAY[{expected}]::text[]);

                IF conflicting_tables IS NOT NULL THEN
                    RAISE EXCEPTION
                        '0038 found unexpected existing RLS policies on: %. '
                        'Refusing to replace unknown policy semantics.',
                        conflicting_tables;
                END IF;
            END;
            $ownership$;
            """
        )
    )


def _validate_postcondition() -> None:
    expected = _sql_array(POLICY_TABLES + ALREADY_CANONICAL_TABLES)
    op.execute(
        text(
            f"""
            DO $validate$
            DECLARE broken_tables text;
            BEGIN
                WITH expected(table_name) AS (
                    SELECT unnest(ARRAY[{expected}]::text[])
                ), policy_state AS (
                    SELECT
                        e.table_name,
                        c.relrowsecurity,
                        c.relforcerowsecurity,
                        count(p.*) AS policy_count,
                        bool_and(
                            p.cmd = 'ALL'
                            AND COALESCE(p.qual, '') LIKE '%app.bypass_rls%'
                            AND COALESCE(p.qual, '') LIKE '%app.current_tenant_id%'
                            AND COALESCE(p.qual, '') NOT LIKE '%tenant_id IS NULL%'
                            AND COALESCE(p.with_check, '') LIKE '%app.bypass_rls%'
                            AND COALESCE(p.with_check, '') LIKE '%app.current_tenant_id%'
                            AND COALESCE(p.with_check, '') NOT LIKE '%tenant_id IS NULL%'
                        ) AS canonical_policy
                    FROM expected AS e
                    LEFT JOIN pg_class AS c
                      ON c.oid = to_regclass('public.' || e.table_name)
                    LEFT JOIN pg_policies AS p
                      ON p.schemaname = 'public'
                     AND p.tablename = e.table_name
                    GROUP BY e.table_name, c.relrowsecurity, c.relforcerowsecurity
                )
                SELECT string_agg(table_name, ', ' ORDER BY table_name)
                  INTO broken_tables
                  FROM policy_state
                 WHERE relrowsecurity IS DISTINCT FROM TRUE
                    OR relforcerowsecurity IS DISTINCT FROM TRUE
                    OR policy_count <> 1
                    OR canonical_policy IS DISTINCT FROM TRUE;

                IF broken_tables IS NOT NULL THEN
                    RAISE EXCEPTION
                        '0038 tenant RLS postcondition failed for: %',
                        broken_tables;
                END IF;
            END;
            $validate$;
            """
        )
    )


def upgrade() -> None:
    connection = op.get_bind()
    prior_bypass = str(
        connection.execute(
            text("SELECT COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), ''), 'off')")
        ).scalar()
        or "off"
    )
    prior_lock_timeout = str(
        connection.execute(
            text("SELECT COALESCE(current_setting('lock_timeout', TRUE), '0')")
        ).scalar()
        or "0"
    )
    connection.execute(text("SELECT set_config('app.bypass_rls', 'on', TRUE)"))
    connection.execute(text("SELECT set_config('lock_timeout', '5s', TRUE)"))

    _guard_catalog_and_policy_ownership()
    for enable_sql in _ENABLE_DDL:
        op.execute(text(enable_sql))
        op.execute(text(enable_sql.replace(" ENABLE ", " FORCE ")))

    for table in POLICY_TABLES:
        op.execute(
            text(
                f"CREATE POLICY {table}_tenant_isolation "
                f"ON public.{table} FOR ALL "
                f"USING ({_TENANT_POLICY}) "
                f"WITH CHECK ({_TENANT_POLICY})"
            )
        )

    _validate_postcondition()
    # These execute only after every DDL/postcondition succeeds.  On failure,
    # PostgreSQL has already aborted the migration transaction; issuing SQL in
    # a finally block would mask the root error.  Because both settings are
    # transaction-local, the outer rollback restores them atomically instead.
    connection.execute(
        text("SELECT set_config('lock_timeout', :prior, TRUE)"),
        {"prior": prior_lock_timeout},
    )
    connection.execute(
        text("SELECT set_config('app.bypass_rls', :prior, TRUE)"),
        {"prior": prior_bypass},
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0038: this is a forward-only tenant-isolation boundary"
    )
