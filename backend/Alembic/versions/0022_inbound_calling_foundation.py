"""strict inbound calling foundation

Revision ID: 0022_inbound_calling_foundation
Revises: 0021_billing_topup
Create Date: 2026-08-26 00:00:00.000000

This migration is intentionally additive.  It introduces the database
invariants needed to decide whether an inbound carrier leg may be answered:

* campaigns have an explicit direction (legacy rows remain outbound),
* an E.164 DID can have at most one non-archived assignment globally,
* provider call identities are idempotent at the calls table,
* route/config facts are snapshotted on the durable call row,
* reservations and audit events are append-only, and
* every tenant-owned table uses the canonical FORCE-RLS policy from 0013.

The global inbound switch defaults OFF.  Deploying the migration therefore
cannot start accepting inbound calls by itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0022_inbound_calling_foundation"
down_revision: str | None = "0021_billing_topup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_TABLES = (
    "tenant_phone_numbers",
    "tenant_telephony_concurrency_policies",
    "tenant_telephony_concurrency_leases",
    "tenant_telephony_concurrency_events",
    "inbound_campaign_configs",
    "inbound_did_assignments",
    "inbound_usage_transactions",
    "inbound_reassignment_requests",
    "tenant_inbound_controls",
)


def _canonical_rls(table: str) -> None:
    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
    op.execute(
        text(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR tenant_id = NULLIF(
                        current_setting('app.current_tenant_id', TRUE), ''
                    )::uuid
                )
                WITH CHECK (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR tenant_id = NULLIF(
                        current_setting('app.current_tenant_id', TRUE), ''
                    )::uuid
                )
            """
        )
    )


def _call_child_rls(table: str) -> None:
    """Tenant-isolate a call-owned table that has no tenant_id column."""

    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
    op.execute(
        text(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM calls tenant_call
                        WHERE tenant_call.id = {table}.call_id
                          AND tenant_call.tenant_id = NULLIF(
                              current_setting('app.current_tenant_id', TRUE), ''
                          )::uuid
                    )
                )
                WITH CHECK (
                    COALESCE(
                        NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                        FALSE
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM calls tenant_call
                        WHERE tenant_call.id = {table}.call_id
                          AND tenant_call.tenant_id = NULLIF(
                              current_setting('app.current_tenant_id', TRUE), ''
                          )::uuid
                    )
                )
            """
        )
    )


def upgrade() -> None:
    # Keep database-effective RBAC aligned with Permission.INBOUND_*.
    # Application role defaults are not an authorization source of truth:
    # operators must be able to revoke a role grant or add a bounded direct
    # user grant and have the request path honor it immediately.
    op.execute(
        text(
            """
            INSERT INTO permissions (
                name, description, resource, action, is_system
            )
            VALUES
                ('inbound:read', 'View inbound campaign configuration',
                    'inbound', 'read', TRUE),
                ('inbound:manage', 'Create and edit inbound campaigns',
                    'inbound', 'manage', TRUE),
                ('inbound:assign', 'Assign tenant-owned DIDs and trunks',
                    'inbound', 'assign', TRUE),
                ('inbound:controls', 'Manage tenant inbound runtime controls',
                    'inbound', 'controls', TRUE)
            ON CONFLICT (name) DO UPDATE
            SET description = EXCLUDED.description,
                resource = EXCLUDED.resource,
                action = EXCLUDED.action,
                is_system = TRUE
            """
        )
    )
    op.execute(
        text(
            """
            WITH inbound_grants(role_name, permission_name) AS (
                VALUES
                    ('readonly', 'inbound:read'),
                    ('user', 'inbound:read'),
                    ('tenant_admin', 'inbound:read'),
                    ('tenant_admin', 'inbound:manage'),
                    ('tenant_admin', 'inbound:assign'),
                    ('tenant_admin', 'inbound:controls'),
                    ('partner_admin', 'inbound:read'),
                    ('partner_admin', 'inbound:manage'),
                    ('partner_admin', 'inbound:assign'),
                    ('partner_admin', 'inbound:controls'),
                    ('platform_admin', 'inbound:read'),
                    ('platform_admin', 'inbound:manage'),
                    ('platform_admin', 'inbound:assign'),
                    ('platform_admin', 'inbound:controls')
            )
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM inbound_grants g
            JOIN roles r ON r.name = g.role_name
            JOIN permissions p ON p.name = g.permission_name
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )
    )

    # Existing rows are outbound by construction.  The explicit predicate is
    # also used by the dialer so an inbound campaign can never enter an
    # outbound job scan after it is activated.
    op.execute(
        text(
            "ALTER TABLE campaigns ADD COLUMN direction VARCHAR(12) "
            "NOT NULL DEFAULT 'outbound'"
        )
    )
    op.execute(
        text(
            "ALTER TABLE campaigns ADD CONSTRAINT campaigns_direction_valid "
            "CHECK (direction IN ('outbound', 'inbound'))"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_campaigns_tenant_direction_status "
            "ON campaigns (tenant_id, direction, status)"
        )
    )

    # These tenant AI fields were historically shipped as standalone SQL
    # migrations, so a database created from complete_schema.sql and stamped
    # at 0021 may not have them.  Inbound admission must pin every setting
    # used to build the voice session; otherwise a post-answer re-read can
    # drift from the admitted snapshot.  Keep each statement separate for
    # asyncpg/Alembic prepared-statement compatibility.
    op.execute(
        text(
            "ALTER TABLE tenant_ai_configs ADD COLUMN IF NOT EXISTS "
            "stt_engine TEXT NOT NULL DEFAULT 'deepgram_flux'"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_ai_configs ADD COLUMN IF NOT EXISTS "
            "pipeline_mode TEXT NOT NULL DEFAULT 'cascaded'"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_ai_configs ADD COLUMN IF NOT EXISTS "
            "realtime_model TEXT"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_ai_configs ADD COLUMN IF NOT EXISTS "
            "realtime_voice TEXT"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_ai_configs ADD COLUMN IF NOT EXISTS "
            "realtime_settings JSONB"
        )
    )

    # Some fresh-install baselines were stamped past the archived raw-SQL
    # migration that originally created this table.  Production databases
    # already have it; CREATE IF NOT EXISTS is a no-op there.  Keeping the
    # canonical definition here makes Alembic head sufficient on a fresh DB.
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_phone_numbers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                e164 TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'manual_admin',
                status TEXT NOT NULL DEFAULT 'pending_verification'
                    CHECK (status IN (
                        'pending_verification','verified','suspended','revoked'
                    )),
                verification_method TEXT
                    CHECK (
                        verification_method IS NULL OR verification_method IN (
                            'sms_code','carrier_api','manual_admin',
                            'letter_of_authorization'
                        )
                    ),
                verification_sent_at TIMESTAMPTZ,
                verified_at TIMESTAMPTZ,
                verified_by TEXT,
                stir_shaken_token TEXT,
                label TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_tenant_phone_numbers_tenant_e164_0022 "
            "ON tenant_phone_numbers (tenant_id, e164)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_phone_numbers_verified_0022 "
            "ON tenant_phone_numbers (tenant_id, e164) WHERE status='verified'"
        )
    )
    op.execute(
        text(
            "COMMENT ON TABLE tenant_phone_numbers IS "
            "'Tenant-owned DIDs. Verification is required before outbound use "
            "or inbound assignment. Alembic 0022 supplies this table on fresh "
            "baselines that skipped its archived raw-SQL migration.'"
        )
    )

    # These PostgreSQL lease tables were introduced through the same archived
    # schema path and are absent from some stamped fresh baselines. Admission
    # depends on them for its atomic tenant cap, so supply the canonical shape.
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_policies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                policy_name VARCHAR(100) NOT NULL,
                max_active_calls INTEGER NOT NULL DEFAULT 10
                    CHECK (max_active_calls BETWEEN 1 AND 1000),
                max_transfer_inflight INTEGER NOT NULL DEFAULT 2
                    CHECK (max_transfer_inflight BETWEEN 1 AND 500),
                lease_ttl_seconds INTEGER NOT NULL DEFAULT 120
                    CHECK (lease_ttl_seconds BETWEEN 10 AND 3600),
                heartbeat_grace_seconds INTEGER NOT NULL DEFAULT 30
                    CHECK (heartbeat_grace_seconds BETWEEN 5 AND 600),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT telephony_policy_heartbeat_window_safe
                    CHECK (lease_ttl_seconds + heartbeat_grace_seconds >= 90),
                UNIQUE (tenant_id, policy_name)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_tenant_telephony_concurrency_policy_active_unique "
            "ON tenant_telephony_concurrency_policies (tenant_id) WHERE is_active=TRUE"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_policy_tenant_active "
            "ON tenant_telephony_concurrency_policies (tenant_id,is_active,updated_at DESC)"
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_leases (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                policy_id UUID REFERENCES tenant_telephony_concurrency_policies(id)
                    ON DELETE SET NULL,
                call_id UUID NOT NULL,
                talklee_call_id VARCHAR(64) NOT NULL,
                lease_kind VARCHAR(16) NOT NULL
                    CHECK (lease_kind IN ('call','transfer')),
                state VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (state IN ('active','releasing','released','expired')),
                acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                released_at TIMESTAMPTZ,
                release_reason VARCHAR(64),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CHECK (
                    (state IN ('released','expired') AND released_at IS NOT NULL)
                    OR state IN ('active','releasing')
                )
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_tenant_telephony_concurrency_leases_active_unique "
            "ON tenant_telephony_concurrency_leases (tenant_id,call_id,lease_kind) "
            "WHERE released_at IS NULL AND state IN ('active','releasing')"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_leases_tenant_active "
            "ON tenant_telephony_concurrency_leases (tenant_id,state,acquired_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_leases_tenant_heartbeat "
            "ON tenant_telephony_concurrency_leases (tenant_id,last_heartbeat_at DESC)"
        )
    )
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS tenant_telephony_concurrency_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                policy_id UUID REFERENCES tenant_telephony_concurrency_policies(id)
                    ON DELETE SET NULL,
                lease_id UUID REFERENCES tenant_telephony_concurrency_leases(id)
                    ON DELETE SET NULL,
                event_type VARCHAR(16) NOT NULL
                    CHECK (event_type IN ('acquire','reject','release','expire','heartbeat')),
                lease_kind VARCHAR(16) NOT NULL
                    CHECK (lease_kind IN ('call','transfer')),
                call_id UUID,
                talklee_call_id VARCHAR(64),
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                request_id VARCHAR(128),
                created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_events_tenant_created "
            "ON tenant_telephony_concurrency_events (tenant_id,created_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_tenant_telephony_concurrency_events_event_type "
            "ON tenant_telephony_concurrency_events (tenant_id,event_type,created_at DESC)"
        )
    )

    # Composite unique keys exist only to make the tenant-safe foreign keys
    # below possible.  The first column is already a primary key, so these do
    # not change logical uniqueness.
    op.execute(
        text(
            "ALTER TABLE campaigns ADD CONSTRAINT campaigns_id_tenant_unique "
            "UNIQUE (id, tenant_id)"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_phone_numbers "
            "ADD CONSTRAINT tenant_phone_numbers_id_tenant_unique "
            "UNIQUE (id, tenant_id)"
        )
    )
    op.execute(
        text(
            "ALTER TABLE tenant_sip_trunks "
            "ADD CONSTRAINT tenant_sip_trunks_id_tenant_unique "
            "UNIQUE (id, tenant_id)"
        )
    )

    op.execute(
        text(
            """
            CREATE TABLE inbound_campaign_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                campaign_id UUID NOT NULL,
                name VARCHAR(255) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'active', 'paused', 'archived')),
                version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
                opening_mode VARCHAR(16) NOT NULL DEFAULT 'caller_first'
                    CHECK (opening_mode IN ('caller_first', 'agent_first')),
                greeting TEXT,
                timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
                business_hours JSONB NOT NULL DEFAULT '{}'::jsonb,
                after_hours_action VARCHAR(16) NOT NULL DEFAULT 'hangup'
                    CHECK (after_hours_action IN ('hangup', 'voicemail', 'transfer')),
                transfer_number TEXT,
                recording_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                consent_message TEXT,
                recording_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
                transfer_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
                qualification_config JSONB NOT NULL DEFAULT '{}'::jsonb,
                config_checksum CHAR(64) NOT NULL,
                active_at TIMESTAMPTZ,
                created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT inbound_campaign_configs_campaign_tenant_fk
                    FOREIGN KEY (campaign_id, tenant_id)
                    REFERENCES campaigns(id, tenant_id) ON DELETE CASCADE,
                CONSTRAINT inbound_campaign_configs_campaign_unique
                    UNIQUE (campaign_id),
                CONSTRAINT inbound_campaign_configs_id_tenant_unique
                    UNIQUE (id, tenant_id),
                CONSTRAINT inbound_campaign_configs_after_hours_transfer
                    CHECK (
                        after_hours_action <> 'transfer'
                        OR NULLIF(BTRIM(transfer_number), '') IS NOT NULL
                    ),
                CONSTRAINT inbound_campaign_configs_recording_consent
                    CHECK (
                        recording_enabled = FALSE
                        OR NULLIF(BTRIM(consent_message), '') IS NOT NULL
                    )
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_configs_tenant_status "
            "ON inbound_campaign_configs (tenant_id, status, updated_at DESC)"
        )
    )

    op.execute(
        text(
            r"""
            CREATE TABLE inbound_did_assignments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                phone_number_id UUID NOT NULL,
                campaign_id UUID NOT NULL,
                config_id UUID NOT NULL,
                sip_trunk_id UUID NOT NULL,
                canonical_did TEXT NOT NULL
                    CHECK (canonical_did ~ '^\+[1-9][0-9]{6,14}$'),
                status VARCHAR(16) NOT NULL DEFAULT 'paused'
                    CHECK (status IN ('active', 'paused', 'quarantined', 'archived')),
                status_before_quarantine VARCHAR(16)
                    CHECK (
                        status_before_quarantine IS NULL
                        OR status_before_quarantine IN ('active', 'paused')
                    ),
                version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
                valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_to TIMESTAMPTZ,
                quarantine_reason TEXT,
                last_error TEXT,
                created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT inbound_assignments_valid_window
                    CHECK (valid_to IS NULL OR valid_to > valid_from),
                CONSTRAINT inbound_assignments_phone_tenant_fk
                    FOREIGN KEY (phone_number_id, tenant_id)
                    REFERENCES tenant_phone_numbers(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_assignments_campaign_tenant_fk
                    FOREIGN KEY (campaign_id, tenant_id)
                    REFERENCES campaigns(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_assignments_config_tenant_fk
                    FOREIGN KEY (config_id, tenant_id)
                    REFERENCES inbound_campaign_configs(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_assignments_trunk_tenant_fk
                    FOREIGN KEY (sip_trunk_id, tenant_id)
                    REFERENCES tenant_sip_trunks(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_assignments_id_tenant_unique
                    UNIQUE (id, tenant_id)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_active_canonical_did "
            "ON inbound_did_assignments (canonical_did) WHERE status = 'active'"
        )
    )
    # Availability is reserved as soon as an assignment is created, not only
    # when it becomes active.  The service-side lookup provides a friendly
    # error, while this partial unique index is the race-safe authority for
    # simultaneous paused/quarantined/active assignment transactions.
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_live_canonical_did "
            "ON inbound_did_assignments (canonical_did) "
            "WHERE status <> 'archived'"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_live_config_assignment "
            "ON inbound_did_assignments (config_id) WHERE status <> 'archived'"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_assignments_tenant_status "
            "ON inbound_did_assignments (tenant_id, status, updated_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_assignments_phone "
            "ON inbound_did_assignments (phone_number_id)"
        )
    )

    # Global, tenant and DID controls are independent: platform controls are
    # global, this row is tenant-wide, and assignment status is DID-specific.
    op.execute(
        text(
            """
            CREATE TABLE tenant_inbound_controls (
                tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                inbound_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
                reason TEXT,
                updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # The same stamped-baseline issue affected migration 0014 on some fresh
    # installs. Supply its singleton table when absent, then add each new
    # column independently so a partially provisioned environment is safe.
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS platform_runtime_controls (
                id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                outbound_calls_paused BOOLEAN NOT NULL DEFAULT FALSE,
                paused_at TIMESTAMPTZ,
                paused_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                pause_reason TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        text(
            "INSERT INTO platform_runtime_controls (id, outbound_calls_paused) "
            "VALUES (1,FALSE) ON CONFLICT (id) DO NOTHING"
        )
    )
    for statement in (
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_recording_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_transfer_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_settlement_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_controls_version BIGINT NOT NULL DEFAULT 1",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_controls_reason TEXT",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_controls_updated_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL",
        "ALTER TABLE platform_runtime_controls ADD COLUMN IF NOT EXISTS "
        "inbound_controls_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    ):
        op.execute(text(statement))
    op.execute(
        text(
            "ALTER TABLE platform_runtime_controls "
            "ADD CONSTRAINT platform_inbound_controls_version_positive "
            "CHECK (inbound_controls_version > 0)"
        )
    )

    # A call row is committed in the same admission transaction as the lease
    # and reservation, before telephony is allowed to answer.
    op.execute(
        text(
            """
            ALTER TABLE calls
                ADD COLUMN direction VARCHAR(12) NOT NULL DEFAULT 'outbound',
                ADD COLUMN provider VARCHAR(32),
                ADD COLUMN provider_call_id VARCHAR(255),
                ADD COLUMN provider_event_id VARCHAR(255),
                ADD COLUMN called_did TEXT,
                ADD COLUMN called_did_id UUID,
                ADD COLUMN assignment_id UUID,
                ADD COLUMN ingress VARCHAR(64),
                ADD COLUMN route_version BIGINT,
                ADD COLUMN config_version BIGINT,
                ADD COLUMN route_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                ADD COLUMN admission_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                ADD COLUMN admission_reason VARCHAR(64),
                ADD COLUMN caller_ani TEXT,
                ADD COLUMN caller_ani_private BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN consent_status VARCHAR(16) NOT NULL DEFAULT 'unknown',
                ADD COLUMN processing_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                ADD COLUMN billing_status VARCHAR(16) NOT NULL DEFAULT 'none',
                ADD COLUMN reserved_seconds INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN concurrency_lease_id UUID
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE calls
                ADD CONSTRAINT calls_direction_valid
                    CHECK (direction IN ('outbound', 'inbound')),
                ADD CONSTRAINT calls_admission_status_valid
                    CHECK (admission_status IN ('pending', 'allowed', 'denied', 'released')),
                ADD CONSTRAINT calls_consent_status_valid
                    CHECK (consent_status IN ('unknown', 'not_required', 'pending', 'granted', 'declined')),
                ADD CONSTRAINT calls_processing_status_valid
                    CHECK (processing_status IN ('pending', 'active', 'completed', 'failed', 'released')),
                ADD CONSTRAINT calls_billing_status_valid
                    CHECK (billing_status IN ('none', 'reserved', 'held', 'finalized', 'released', 'reversed')),
                ADD CONSTRAINT calls_reserved_seconds_nonnegative
                    CHECK (reserved_seconds >= 0),
                ADD CONSTRAINT calls_id_tenant_unique
                    UNIQUE (id, tenant_id),
                ADD CONSTRAINT calls_assignment_tenant_fk
                    FOREIGN KEY (assignment_id, tenant_id)
                    REFERENCES inbound_did_assignments(id, tenant_id),
                ADD CONSTRAINT calls_called_did_tenant_fk
                    FOREIGN KEY (called_did_id, tenant_id)
                    REFERENCES tenant_phone_numbers(id, tenant_id)
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_calls_provider_call_identity "
            "ON calls (provider, provider_call_id) "
            "WHERE provider IS NOT NULL AND provider_call_id IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_calls_provider_event_identity "
            "ON calls (provider, provider_event_id) "
            "WHERE provider IS NOT NULL AND provider_event_id IS NOT NULL"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_calls_tenant_direction_created "
            "ON calls (tenant_id, direction, created_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_calls_inbound_assignment "
            "ON calls (assignment_id, created_at DESC) WHERE direction = 'inbound'"
        )
    )

    # Signed deltas make the ledger append-only: reserve is positive;
    # finalize is actual-minus-reserved; release/reverse is negative reserved.
    op.execute(
        text(
            """
            CREATE TABLE inbound_usage_transactions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                call_id UUID NOT NULL REFERENCES calls(id) ON DELETE RESTRICT,
                transaction_type VARCHAR(16) NOT NULL
                    CHECK (transaction_type IN ('reserve', 'finalize', 'release', 'reverse')),
                quantity_seconds INTEGER NOT NULL,
                amount NUMERIC(12, 6),
                currency VARCHAR(3),
                idempotency_key VARCHAR(255) NOT NULL,
                related_transaction_id UUID REFERENCES inbound_usage_transactions(id),
                policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT inbound_usage_call_tenant_fk
                    FOREIGN KEY (call_id, tenant_id)
                    REFERENCES calls(id, tenant_id) ON DELETE RESTRICT,
                CONSTRAINT inbound_usage_idempotency_unique
                    UNIQUE (tenant_id, idempotency_key)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_reserve_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type = 'reserve'"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_settlement_per_call "
            "ON inbound_usage_transactions (call_id) "
            "WHERE transaction_type IN ('finalize', 'release')"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_inbound_usage_reverse_per_settlement "
            "ON inbound_usage_transactions (related_transaction_id) "
            "WHERE transaction_type = 'reverse'"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_usage_tenant_created "
            "ON inbound_usage_transactions (tenant_id, created_at DESC)"
        )
    )

    op.execute(
        text(
            """
            CREATE TABLE inbound_reassignment_requests (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                -- The source assignment is immutable history. Approval
                -- archives it and creates approved_assignment_id for the
                -- target tenant; neither historical calls nor this FK move.
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                source_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                assignment_id UUID NOT NULL,
                approved_assignment_id UUID,
                target_tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                target_campaign_id UUID NOT NULL,
                target_config_id UUID NOT NULL,
                expected_assignment_version BIGINT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
                reason TEXT NOT NULL,
                decision_reason TEXT,
                requested_by UUID NOT NULL REFERENCES user_profiles(id) ON DELETE RESTRICT,
                approved_by UUID REFERENCES user_profiles(id) ON DELETE RESTRICT,
                idempotency_key VARCHAR(255) NOT NULL,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                decided_at TIMESTAMPTZ,
                CONSTRAINT inbound_reassignment_requester_approver_distinct
                    CHECK (approved_by IS NULL OR approved_by <> requested_by),
                CONSTRAINT inbound_reassignment_source_tenant_stable
                    CHECK (source_tenant_id = tenant_id),
                CONSTRAINT inbound_reassignment_assignment_tenant_fk
                    FOREIGN KEY (assignment_id, tenant_id)
                    REFERENCES inbound_did_assignments(id, tenant_id),
                CONSTRAINT inbound_reassignment_approved_assignment_tenant_fk
                    FOREIGN KEY (approved_assignment_id, target_tenant_id)
                    REFERENCES inbound_did_assignments(id, tenant_id),
                CONSTRAINT inbound_reassignment_target_campaign_tenant_fk
                    FOREIGN KEY (target_campaign_id, target_tenant_id)
                    REFERENCES campaigns(id, tenant_id),
                CONSTRAINT inbound_reassignment_target_config_tenant_fk
                    FOREIGN KEY (target_config_id, target_tenant_id)
                    REFERENCES inbound_campaign_configs(id, tenant_id),
                CONSTRAINT inbound_reassignment_actor_key_unique
                    UNIQUE (requested_by, idempotency_key)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_reassignments_status_requested "
            "ON inbound_reassignment_requests (status, requested_at DESC)"
        )
    )

    # Durable idempotency is used for tenant and platform mutations.  Global
    # rows (tenant_id NULL) are visible only with the service bypass flag.
    op.execute(
        text(
            """
            CREATE TABLE inbound_operation_idempotency (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
                scope_key VARCHAR(128) NOT NULL,
                operation VARCHAR(128) NOT NULL,
                idempotency_key VARCHAR(255) NOT NULL,
                request_hash CHAR(64) NOT NULL,
                response_body JSONB,
                status_code INTEGER CHECK (status_code BETWEEN 100 AND 599),
                resource_type VARCHAR(64),
                resource_id UUID,
                actor_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours',
                CONSTRAINT inbound_operation_idempotency_unique
                    UNIQUE (scope_key, operation, idempotency_key)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_operation_idempotency_expiry "
            "ON inbound_operation_idempotency (expires_at)"
        )
    )

    op.execute(
        text(
            """
            CREATE TABLE inbound_audit_events (
                id BIGSERIAL PRIMARY KEY,
                tenant_id UUID REFERENCES tenants(id) ON DELETE RESTRICT,
                event_type VARCHAR(64) NOT NULL,
                actor_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
                actor_role VARCHAR(32),
                resource_type VARCHAR(64) NOT NULL,
                resource_id UUID,
                reason TEXT,
                before_state JSONB,
                after_state JSONB,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                idempotency_key VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_audit_tenant_created "
            "ON inbound_audit_events (tenant_id, created_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_audit_resource "
            "ON inbound_audit_events (resource_type, resource_id, created_at DESC)"
        )
    )

    for table in _TENANT_TABLES:
        _canonical_rls(table)

    # Transfer legs and their event log are tenant-owned through calls. They
    # deliberately do not duplicate tenant_id, so their policy resolves the
    # parent call under the same transaction-scoped tenant context.
    for table in ("call_legs", "call_events"):
        _call_child_rls(table)

    # Nullable tenant rows are platform-global and must not be exposed to an
    # ordinary tenant.  Platform-admin code uses acquire_with_tenant(pool,None).
    for table in ("inbound_operation_idempotency", "inbound_audit_events"):
        op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
        op.execute(
            text(
                f"""
                CREATE POLICY {table}_tenant_isolation ON {table}
                    USING (
                        COALESCE(
                            NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                            FALSE
                        )
                        OR (
                            tenant_id IS NOT NULL
                            AND tenant_id = NULLIF(
                                current_setting('app.current_tenant_id', TRUE), ''
                            )::uuid
                        )
                    )
                    WITH CHECK (
                        COALESCE(
                            NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                            FALSE
                        )
                        OR (
                            tenant_id IS NOT NULL
                            AND tenant_id = NULLIF(
                                current_setting('app.current_tenant_id', TRUE), ''
                            )::uuid
                        )
                    )
                """
            )
        )

    # Database-enforced immutability. Corrections are compensating rows.
    op.execute(
        text(
            """
            CREATE FUNCTION prevent_inbound_immutable_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only; insert a compensating event',
                    TG_TABLE_NAME;
            END;
            $$
            """
        )
    )
    op.execute(
        text(
            "CREATE TRIGGER inbound_usage_transactions_immutable "
            "BEFORE UPDATE OR DELETE ON inbound_usage_transactions "
            "FOR EACH ROW EXECUTE FUNCTION prevent_inbound_immutable_mutation()"
        )
    )
    op.execute(
        text(
            "CREATE TRIGGER inbound_audit_events_immutable "
            "BEFORE UPDATE OR DELETE ON inbound_audit_events "
            "FOR EACH ROW EXECUTE FUNCTION prevent_inbound_immutable_mutation()"
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION prevent_call_event_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' AND COALESCE(
                    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
                    FALSE
                ) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'call_events is append-only; insert a new event';
            END;
            $$
            """
        )
    )
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS call_events_immutable ON call_events"
        )
    )
    op.execute(
        text(
            "CREATE TRIGGER call_events_immutable "
            "BEFORE UPDATE OR DELETE ON call_events "
            "FOR EACH ROW EXECUTE FUNCTION prevent_call_event_mutation()"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Freeze every mutable source inspected by the guard. A SELECT-only guard
    # is vulnerable to a writer committing retained inbound state after the
    # count passes but before the destructive DDL acquires its table locks.
    conn.execute(
        text(
            """
            LOCK TABLE
                inbound_campaign_configs,
                inbound_did_assignments,
                tenant_inbound_controls,
                inbound_usage_transactions,
                inbound_reassignment_requests,
                inbound_operation_idempotency,
                inbound_audit_events,
                campaigns,
                calls,
                platform_runtime_controls
            IN ACCESS EXCLUSIVE MODE
            """
        )
    )
    retained_rows = {
        "configs": conn.execute(text("SELECT count(*) FROM inbound_campaign_configs")).scalar(),
        "assignments": conn.execute(text("SELECT count(*) FROM inbound_did_assignments")).scalar(),
        "tenant_controls": conn.execute(text("SELECT count(*) FROM tenant_inbound_controls")).scalar(),
        "usage": conn.execute(text("SELECT count(*) FROM inbound_usage_transactions")).scalar(),
        "reassignments": conn.execute(text("SELECT count(*) FROM inbound_reassignment_requests")).scalar(),
        "idempotency": conn.execute(text("SELECT count(*) FROM inbound_operation_idempotency")).scalar(),
        "audit": conn.execute(text("SELECT count(*) FROM inbound_audit_events")).scalar(),
        "inbound_campaigns": conn.execute(
            text("SELECT count(*) FROM campaigns WHERE direction = 'inbound'")
        ).scalar(),
        "calls_with_0022_state": conn.execute(
            text(
                """
                SELECT count(*) FROM calls
                WHERE direction <> 'outbound'
                   OR provider IS NOT NULL
                   OR provider_call_id IS NOT NULL
                   OR provider_event_id IS NOT NULL
                   OR called_did IS NOT NULL
                   OR called_did_id IS NOT NULL
                   OR assignment_id IS NOT NULL
                   OR ingress IS NOT NULL
                   OR route_version IS NOT NULL
                   OR config_version IS NOT NULL
                   OR route_snapshot <> '{}'::jsonb
                   OR admission_status <> 'pending'
                   OR admission_reason IS NOT NULL
                   OR caller_ani IS NOT NULL
                   OR caller_ani_private
                   OR consent_status <> 'unknown'
                   OR processing_status <> 'pending'
                   OR billing_status <> 'none'
                   OR reserved_seconds <> 0
                   OR concurrency_lease_id IS NOT NULL
                """
            )
        ).scalar(),
        "platform_control_changes": conn.execute(
            text(
                """
                SELECT count(*) FROM platform_runtime_controls
                WHERE inbound_enabled
                   OR inbound_recording_enabled
                   OR inbound_transfer_enabled
                   OR inbound_settlement_enabled
                   OR inbound_controls_version <> 1
                   OR inbound_controls_reason IS NOT NULL
                   OR inbound_controls_updated_by IS NOT NULL
                """
            )
        ).scalar(),
    }
    nonempty = {name: count for name, count in retained_rows.items() if count}
    if nonempty:
        raise RuntimeError(
            "Refusing inbound-foundation downgrade with retained data: "
            + ", ".join(f"{name}={count}" for name, count in nonempty.items())
            + ". Export/archive or explicitly reconcile every row first."
        )

    # ``call_events_immutable`` and ``prevent_call_event_mutation`` are
    # baseline objects from complete_schema.sql.  Upgrade normalizes them but
    # downgrade must retain them; dropping either would silently remove the
    # pre-existing append-only protection.

    # ``call_events`` and ``call_legs`` already have canonical forced-RLS
    # policies in complete_schema.sql. Upgrade reasserts that baseline safety;
    # downgrade must not remove it.

    op.execute(text("DROP TABLE IF EXISTS inbound_audit_events"))
    op.execute(text("DROP TABLE IF EXISTS inbound_operation_idempotency"))
    op.execute(text("DROP TABLE IF EXISTS inbound_reassignment_requests"))
    op.execute(text("DROP TABLE IF EXISTS inbound_usage_transactions"))

    op.execute(text("ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_called_did_tenant_fk"))
    op.execute(text("ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_assignment_tenant_fk"))
    op.execute(text("ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_id_tenant_unique"))
    op.execute(text("DROP INDEX IF EXISTS idx_calls_inbound_assignment"))
    op.execute(text("DROP INDEX IF EXISTS idx_calls_tenant_direction_created"))
    op.execute(text("DROP INDEX IF EXISTS uq_calls_provider_event_identity"))
    op.execute(text("DROP INDEX IF EXISTS uq_calls_provider_call_identity"))
    for column in (
        "concurrency_lease_id",
        "reserved_seconds",
        "billing_status",
        "processing_status",
        "consent_status",
        "caller_ani_private",
        "caller_ani",
        "admission_reason",
        "admission_status",
        "route_snapshot",
        "config_version",
        "route_version",
        "ingress",
        "assignment_id",
        "called_did_id",
        "called_did",
        "provider_event_id",
        "provider_call_id",
        "provider",
        "direction",
    ):
        op.execute(text(f"ALTER TABLE calls DROP COLUMN IF EXISTS {column}"))

    op.execute(text("DROP TABLE IF EXISTS tenant_inbound_controls"))
    op.execute(text("DROP TABLE IF EXISTS inbound_did_assignments"))
    op.execute(text("DROP TABLE IF EXISTS inbound_campaign_configs"))
    op.execute(text("DROP FUNCTION IF EXISTS prevent_inbound_immutable_mutation()"))

    op.execute(
        text(
            """
            ALTER TABLE platform_runtime_controls
                DROP CONSTRAINT IF EXISTS platform_inbound_controls_version_positive,
                DROP COLUMN IF EXISTS inbound_controls_updated_at,
                DROP COLUMN IF EXISTS inbound_controls_updated_by,
                DROP COLUMN IF EXISTS inbound_controls_reason,
                DROP COLUMN IF EXISTS inbound_controls_version,
                DROP COLUMN IF EXISTS inbound_settlement_enabled,
                DROP COLUMN IF EXISTS inbound_transfer_enabled,
                DROP COLUMN IF EXISTS inbound_recording_enabled,
                DROP COLUMN IF EXISTS inbound_enabled;
            """
        )
    )
    op.execute(text("ALTER TABLE tenant_sip_trunks DROP CONSTRAINT IF EXISTS tenant_sip_trunks_id_tenant_unique"))
    op.execute(text("ALTER TABLE tenant_phone_numbers DROP CONSTRAINT IF EXISTS tenant_phone_numbers_id_tenant_unique"))
    op.execute(text("ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_id_tenant_unique"))
    op.execute(text("DROP INDEX IF EXISTS idx_campaigns_tenant_direction_status"))
    op.execute(text("ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_direction_valid"))
    op.execute(text("ALTER TABLE campaigns DROP COLUMN IF EXISTS direction"))
    # Deliberately retain the tenant_ai_configs compatibility columns above.
    # They predate inbound calling in installations that ran the standalone
    # SQL migrations, and removing them would break the existing AI Options
    # API.  Their ADD COLUMN IF NOT EXISTS origin cannot be distinguished
    # safely across deployments during downgrade.
    # Deliberately retain tenant_phone_numbers. Older installations owned it
    # before 0022, while fresh installations may have received it here; there
    # is no reliable cross-deployment marker proving which is true. Preserving
    # verified DID ownership is safer than deleting it during schema rollback.
    # Likewise retain the four inbound permission definitions and role grants.
    # complete_schema.sql now owns them for fresh baselines, and deleting them
    # here could cascade user-specific grants created after 0022 was applied.
