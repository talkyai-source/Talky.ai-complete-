"""Bind internal PBX extensions to inbound campaigns, separately from public DIDs.

Revision ID: 0046_inbound_extension_bindings  (<= 32 chars: alembic_version.version_num is varchar(32))
Revises: 0045_refresh_session_binding

Why a new table rather than a column on ``inbound_did_assignments``:

* ``inbound_did_assignments.canonical_did`` carries
  ``CHECK (canonical_did ~ '^\\+[1-9][0-9]{6,14}$')``.  An extension cannot be
  stored there without widening a constraint whose whole job is to keep public
  numbers strict.
* ``phone_number_id`` is NOT NULL with an FK to ``tenant_phone_numbers``.  An
  extension has no phone-number row, and giving it one would be worse than
  cosmetic: ``trunk_resolver._select_caller_id`` reads **every**
  ``tenant_phone_numbers`` row of a tenant to choose an outbound caller-ID, so
  an ``ext:`` row parked there could surface as a presented caller-ID.

So extensions get their own narrow table.  It mirrors the DID table's isolation
guarantees exactly:

* tenant-paired composite FKs to campaigns / configs / trunks, so a binding can
  never straddle two tenants even if application code slips.
* ``uq_inbound_active_extension`` is **global**, not per-tenant.  A DID list is
  per tenant; a carrier account namespace is not — ``940005`` is one account on
  sip3.blazedigitel.com and exactly one tenant may answer it.  Note
  ``tenant_sip_trunks`` has no unique index on ``auth_username``, so without
  this index two tenants could each create a 940005 trunk and both claim the
  extension.
* forced RLS with the repository's canonical tenant-isolation policy.

Additive and reversible: no existing table is altered, so the currently
deployed code is unaffected by this revision landing before it.
"""
from alembic import op
from sqlalchemy import text

revision = "0046_inbound_extension_bindings"
down_revision = "0045_refresh_session_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.inbound_extension_assignments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL
                    REFERENCES public.tenants(id) ON DELETE RESTRICT,
                extension TEXT NOT NULL,
                sip_trunk_id UUID NOT NULL,
                campaign_id UUID NOT NULL,
                config_id UUID NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'paused',
                version BIGINT NOT NULL DEFAULT 1,
                valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_to TIMESTAMPTZ,
                last_error TEXT,
                created_by UUID REFERENCES public.user_profiles(id) ON DELETE SET NULL,
                updated_by UUID REFERENCES public.user_profiles(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT inbound_extension_digits_check
                    CHECK (extension ~ '^[0-9]{3,8}$'),
                CONSTRAINT inbound_extension_status_check
                    CHECK (status IN ('active', 'paused', 'archived')),
                CONSTRAINT inbound_extension_version_check
                    CHECK (version > 0),
                CONSTRAINT inbound_extension_valid_window
                    CHECK (valid_to IS NULL OR valid_to > valid_from),
                CONSTRAINT inbound_extension_id_tenant_unique
                    UNIQUE (id, tenant_id),
                CONSTRAINT inbound_extension_campaign_tenant_fk
                    FOREIGN KEY (campaign_id, tenant_id)
                    REFERENCES public.campaigns(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_extension_config_tenant_fk
                    FOREIGN KEY (config_id, tenant_id)
                    REFERENCES public.inbound_campaign_configs(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED,
                CONSTRAINT inbound_extension_trunk_tenant_fk
                    FOREIGN KEY (sip_trunk_id, tenant_id)
                    REFERENCES public.tenant_sip_trunks(id, tenant_id)
                    DEFERRABLE INITIALLY DEFERRED
            )
            """
        )
    )

    # Global, not per-tenant: the carrier account namespace is global.
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_active_extension "
            "ON public.inbound_extension_assignments (extension) "
            "WHERE status = 'active'"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_live_extension "
            "ON public.inbound_extension_assignments (extension) "
            "WHERE status <> 'archived'"
        )
    )
    # One live binding per inbound config, mirroring uq_inbound_live_config_assignment.
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_live_extension_config "
            "ON public.inbound_extension_assignments (config_id) "
            "WHERE status <> 'archived'"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_inbound_extension_tenant_status "
            "ON public.inbound_extension_assignments "
            "(tenant_id, status, updated_at DESC)"
        )
    )

    op.execute(
        text(
            "ALTER TABLE public.inbound_extension_assignments "
            "ENABLE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_extension_assignments "
            "FORCE ROW LEVEL SECURITY"
        )
    )
    op.execute(
        text(
            """
            DO $extension_policy$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.pg_policy
                     WHERE polrelid =
                           'public.inbound_extension_assignments'::regclass
                       AND polname =
                           'inbound_extension_assignments_tenant_isolation'
                ) THEN
                    CREATE POLICY inbound_extension_assignments_tenant_isolation
                        ON public.inbound_extension_assignments
                        FOR ALL
                        USING (
                            COALESCE(
                                NULLIF(
                                    current_setting('app.bypass_rls', TRUE), ''
                                )::boolean,
                                FALSE
                            )
                            OR tenant_id = NULLIF(
                                current_setting('app.current_tenant_id', TRUE), ''
                            )::uuid
                        )
                        WITH CHECK (
                            COALESCE(
                                NULLIF(
                                    current_setting('app.bypass_rls', TRUE), ''
                                )::boolean,
                                FALSE
                            )
                            OR tenant_id = NULLIF(
                                current_setting('app.current_tenant_id', TRUE), ''
                            )::uuid
                        );
                END IF;
            END
            $extension_policy$;
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text("DROP TABLE IF EXISTS public.inbound_extension_assignments CASCADE")
    )
