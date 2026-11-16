"""Create the two tables a clean install lacked (tenant_recording_policy, tenant_provider_cost_events).

Both tables were only ever created by raw SQL files (20260425_add_tenant_recording_policy.sql,
20260505_add_tenant_provider_cost_events.sql), which neither `complete_schema.sql`, the June
baseline nor any earlier revision applies. A database built the documented way
(schema file, `alembic stamp 0008_tenant_voice_tuning`, `alembic upgrade head`) therefore had
no recording policy table (saving the policy failed, recordings stayed off) and no cost
ledger table (the flusher re-queued its batch for ever).

Per table:
* If the table already exists (production has both, applied by hand) NOTHING about it is
  changed: no column, constraint, index, policy, grant or owner. One NOTICE line reports
  what was found (row security, FORCE, policy count, column names) so the release log shows
  production's real state.
* If it is missing it is created with the shape the application uses today, the canonical
  tenant policy of 0013 / 0087 / 0090 (bypass OR tenant, FOR ALL, same WITH CHECK, named
  `<table>_tenant_isolation`), ENABLE and FORCE ROW LEVEL SECURITY, and a table comment that
  marks this migration as the creator.

No GRANT is issued: no earlier revision grants table rights either; the runtime role's rights
come from backend/database/runtime_role_grants.sql (see backend/docs/clean-install-runbook.md).

Downgrade drops a table only when its comment says this migration created it, and refuses
while it holds any row.
"""
from alembic import op

revision = "0099_clean_install_tables"
down_revision = "0098_calls_changed_since_index"
branch_labels = None
depends_on = None

OWNER = "created by 0099_clean_install_tables"

# Identical to SCOPE in 0087_bootstrap_manual_parity / 0090_campaign_alerts and to the
# strict shape of 0013_canonical_rls_policies (tenant_id is NOT NULL in both tables).
SCOPE = """COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"""

POLICY_TABLE = "tenant_recording_policy"
COST_TABLE = "tenant_provider_cost_events"

# Columns checked against: app/api/v1/endpoints/recordings.py INSERT/ON CONFLICT (tenant_id)
# and SELECT *, app/domain/services/recording_policy_service.py row[...] reads,
# app/workers/recording_retention_purge.py JOIN on tenant_id / retention_days.
POLICY_DDL = """
        CREATE TABLE public.tenant_recording_policy (
            tenant_id                UUID PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
            default_consent_mode     TEXT NOT NULL DEFAULT 'two_party'
                                     CHECK (default_consent_mode IN ('one_party', 'two_party', 'disabled')),
            announcement_text        TEXT NOT NULL DEFAULT
                'This call may be recorded for quality and training purposes. Press 9 at any time to opt out of recording.',
            opt_out_dtmf_digit       TEXT CHECK (opt_out_dtmf_digit IS NULL OR opt_out_dtmf_digit ~ '^[0-9*#]$')
                                     DEFAULT '9',
            two_party_country_codes  TEXT[] NOT NULL DEFAULT ARRAY[
                'US-CA', 'US-MA', 'US-IL', 'US-WA', 'US-FL', 'US-MD', 'US-MT',
                'US-NH', 'US-PA', 'US-CT', 'US-DE', 'US-MI', 'US-NV', 'US-OR',
                'CA',
                'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR',
                'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL',
                'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE',
                'GB', 'AU'
            ]::TEXT[],
            retention_days           INTEGER NOT NULL DEFAULT 90
                                     CHECK (retention_days > 0 AND retention_days <= 3650),
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE OR REPLACE FUNCTION public.tenant_recording_policy_touch_updated_at()
        RETURNS TRIGGER AS $fn99$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $fn99$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_tenant_recording_policy_updated_at
            BEFORE UPDATE ON public.tenant_recording_policy
            FOR EACH ROW
            EXECUTE FUNCTION public.tenant_recording_policy_touch_updated_at();
"""

# Columns checked against app/domain/services/provider_cost_ledger.py copy_records_to_table
# (13 columns; id, occurred_at and recorded_at come from their defaults).
COST_DDL = """
        CREATE TABLE public.tenant_provider_cost_events (
            id              BIGSERIAL PRIMARY KEY,
            tenant_id       UUID NOT NULL,
            call_id         UUID,
            provider        TEXT NOT NULL,
            provider_role   TEXT NOT NULL,
            api_key_fp      TEXT,
            unit            TEXT NOT NULL,
            quantity        NUMERIC(20, 6) NOT NULL,
            unit_price_usd  NUMERIC(20, 10),
            cost_usd        NUMERIC(20, 6),
            model           TEXT,
            voice_id        TEXT,
            latency_ms      INTEGER,
            status          TEXT NOT NULL DEFAULT 'ok',
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_tpce_tenant_occurred
            ON public.tenant_provider_cost_events (tenant_id, occurred_at DESC);
        CREATE INDEX idx_tpce_provider_occurred
            ON public.tenant_provider_cost_events (provider, occurred_at DESC);
        CREATE INDEX idx_tpce_call
            ON public.tenant_provider_cost_events (call_id) WHERE call_id IS NOT NULL;
        CREATE INDEX idx_tpce_keyfp
            ON public.tenant_provider_cost_events (provider, api_key_fp);
"""


def _block(table, ddl, requires):
    requirement = "".join(f"""
        IF to_regclass('public.{name}') IS NULL THEN
            RAISE EXCEPTION '0099 expected {name} table is missing';
        END IF;""" for name in requires)
    return f"""
    DO $q99$
    DECLARE target oid; rls boolean; forced boolean; policies integer; names text;
    BEGIN{requirement}
        target := to_regclass('public.{table}');
        IF target IS NOT NULL THEN
            IF (SELECT relkind FROM pg_class WHERE oid = target) <> 'r' THEN
                RAISE EXCEPTION '0099 refuses incompatible relation {table}';
            END IF;
            SELECT relrowsecurity, relforcerowsecurity INTO rls, forced FROM pg_class WHERE oid = target;
            SELECT count(*) INTO policies FROM pg_policy WHERE polrelid = target;
            SELECT string_agg(attname::text, ',' ORDER BY attnum) INTO names
              FROM pg_attribute WHERE attrelid = target AND attnum > 0 AND NOT attisdropped;
            RAISE NOTICE '0099 {table} exists, left unchanged: row_security=% forced=% policies=% columns=%',
                rls, forced, policies, names;
        ELSE
            {ddl}
            ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {table}_tenant_isolation ON public.{table}
                FOR ALL USING ({SCOPE}) WITH CHECK ({SCOPE});
            COMMENT ON TABLE public.{table} IS '{OWNER}';
            RAISE NOTICE '0099 {table} was missing and has been created';
        END IF;
    END $q99$"""


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    op.execute(_block(POLICY_TABLE, POLICY_DDL, ("tenants",)))
    op.execute(_block(COST_TABLE, COST_DDL, ()))


def _drop_block(table, extra=""):
    return f"""
    DO $d99$
    DECLARE present oid; has_rows boolean;
    BEGIN
        present := to_regclass('public.{table}');
        IF present IS NOT NULL
           AND obj_description(present, 'pg_class') IS NOT DISTINCT FROM '{OWNER}' THEN
            LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE;
            SELECT EXISTS (SELECT 1 FROM public.{table} LIMIT 1) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION '0099 refuses to drop {table} while it holds rows; use code rollback or a reviewed forward migration';
            END IF;
            DROP TABLE public.{table};{extra}
        END IF;
    END $d99$"""


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    # Hidden rows must raise, never masquerade as an empty table.
    op.execute("SET LOCAL row_security=off")
    op.execute(_drop_block(
        POLICY_TABLE,
        "\n            DROP FUNCTION IF EXISTS public.tenant_recording_policy_touch_updated_at();",
    ))
    op.execute(_drop_block(COST_TABLE))
