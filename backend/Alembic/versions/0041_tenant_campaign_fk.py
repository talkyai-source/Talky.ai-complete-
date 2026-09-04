"""Enforce tenant-safe campaign and authoritative ownership relationships.

Revision ID: 0041_tenant_campaign_fk
Revises: 0040_calls_campaign_nullable
Create Date: 2026-09-03

``down_revision`` extends the sole 0040 head; this revision deliberately does
not create a branch. The upgrade changes no row values: it first adopts the
contact-list objects that older installations received from a manual SQL file,
then rejects existing violations, adds supporting redundant unique keys and
validates NOT VALID foreign keys.  A catalog snapshot makes this safe on both
paths: downgrade removes a contact-list table/column/index/policy only when
0041 created it, and retains every object and row that pre-dated Alembic.

``billable_calls`` is a view over ``calls`` and PostgreSQL views cannot own
foreign keys. The campaign keys on ``call_lead_details`` and ``recordings_s3``
therefore enforce only that a non-null snapshot names a campaign in the same
tenant; they deliberately do not claim it equals the authoritative call's
campaign.

The row triggers close the guard/write race with outbound-to-inbound
conversion. Their locking SELECT is intentionally ``FOR SHARE``: it conflicts
with the conversion's campaign-row ``FOR UPDATE`` lock. Whichever transaction
wins becomes visible before the other can decide. The predicates preserve
real inbound calls (only test calls are outbound-only) and retained deleted
lead history.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0041_tenant_campaign_fk"
down_revision: str | None = "0040_calls_campaign_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FOREIGN_KEYS: tuple[tuple[str, str, str], ...] = (
    (
        "leads",
        "leads_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    (
        "calls",
        "calls_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    (
        "dialer_jobs",
        "dialer_jobs_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    (
        "assistant_actions",
        "assistant_actions_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    (
        "contact_lists",
        "contact_lists_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id) ON DELETE CASCADE",
    ),
    (
        "leads",
        "leads_list_campaign_tenant_fk",
        "FOREIGN KEY (list_id, campaign_id, tenant_id) "
        "REFERENCES public.contact_lists (id, campaign_id, tenant_id)",
    ),
    (
        "call_lead_details",
        "call_lead_details_call_tenant_fk",
        "FOREIGN KEY (call_id, tenant_id) "
        "REFERENCES public.calls (id, tenant_id)",
    ),
    (
        "call_lead_details",
        "call_lead_details_lead_tenant_fk",
        "FOREIGN KEY (lead_id, tenant_id) "
        "REFERENCES public.leads (id, tenant_id)",
    ),
    (
        "call_lead_details",
        "call_lead_details_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    (
        "recordings_s3",
        "recordings_s3_call_tenant_fk",
        "FOREIGN KEY (call_id, tenant_id) "
        "REFERENCES public.calls (id, tenant_id)",
    ),
    (
        "recordings_s3",
        "recordings_s3_campaign_tenant_fk",
        "FOREIGN KEY (campaign_id, tenant_id) "
        "REFERENCES public.campaigns (id, tenant_id)",
    ),
    # Ownership chain, keyed on TENANT and deliberately not on campaign.
    # "A job's campaign equals its lead's current campaign" is false of
    # history: on 2026-09-03 production held 2749 dialer_jobs and 15 calls
    # whose lead had since been re-pointed to another campaign — every one
    # terminal, same tenant, one campaign. Including campaign_id would abort
    # the preflight and, because talky-migrate runs on every deploy, block
    # every future release to enforce an invariant the product never had.
    # Each row's own campaign_id is already tied to a real same-tenant
    # campaign by the *_campaign_tenant_fk entries above.
    (
        "dialer_jobs",
        "dialer_jobs_lead_tenant_fk",
        "FOREIGN KEY (lead_id, tenant_id) "
        "REFERENCES public.leads (id, tenant_id)",
    ),
    (
        "calls",
        "calls_lead_tenant_fk",
        "FOREIGN KEY (lead_id, tenant_id) "
        "REFERENCES public.leads (id, tenant_id)",
    ),
    (
        "calls",
        "calls_dialer_job_tenant_fk",
        "FOREIGN KEY (dialer_job_id, tenant_id) "
        "REFERENCES public.dialer_jobs (id, tenant_id)",
    ),
)


_OUTBOUND_GUARD_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    (
        "leads",
        "leads_outbound_campaign_guard",
        "WHEN (NEW.status IS DISTINCT FROM 'deleted')",
    ),
    (
        "contact_lists",
        "contact_lists_outbound_campaign_guard",
        "",
    ),
    (
        "dialer_jobs",
        "dialer_jobs_outbound_campaign_guard",
        "",
    ),
    (
        "calls",
        "calls_outbound_campaign_guard",
        # Keyed off direction, not is_test. A genuine inbound call is inserted
        # with an explicit direction='inbound' (inbound_admission.py), so it is
        # never caught here; scoping to is_test instead would have let a REAL
        # outbound call target an inbound campaign — the very hole this guard
        # exists to close. Verified on production: 0 outbound-direction calls
        # currently reference an inbound campaign.
        "WHEN (NEW.direction = 'outbound')",
    ),
)


def _repair_recordings_s3_schema() -> None:
    """Restore the 0001-owned table skipped by the supported 0008 floor.

    ``database/complete_schema.sql`` followed by ``alembic stamp 0008`` is the
    documented fresh-bootstrap path.  That snapshot does not contain the one
    table created by 0001, so a fresh database otherwise reaches this
    migration without ``recordings_s3`` and fails inside the preflight UNION.
    This is a forward repair of historical baseline ownership: downgrade must
    retain the table and any recording metadata written after the repair.
    """

    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.recordings_s3 (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                call_id UUID NOT NULL
                    REFERENCES public.calls(id) ON DELETE CASCADE,
                tenant_id UUID NOT NULL
                    REFERENCES public.tenants(id) ON DELETE CASCADE,
                campaign_id UUID,
                s3_bucket VARCHAR(255) NOT NULL,
                s3_key VARCHAR(1024) NOT NULL,
                s3_region VARCHAR(64) NOT NULL DEFAULT 'us-east-1',
                storage_provider VARCHAR(32) NOT NULL DEFAULT 's3',
                file_size_bytes BIGINT,
                duration_seconds INTEGER,
                mime_type VARCHAR(64) NOT NULL DEFAULT 'audio/wav',
                status VARCHAR(32) NOT NULL DEFAULT 'uploaded'
                    CHECK (status IN (
                        'uploading', 'uploaded', 'failed', 'deleted'
                    )),
                upload_started_at TIMESTAMPTZ,
                upload_finished_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (s3_bucket, s3_key)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_recordings_s3_call_id "
            "ON public.recordings_s3 (call_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_recordings_s3_tenant_id "
            "ON public.recordings_s3 (tenant_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_recordings_s3_status "
            "ON public.recordings_s3 (status)"
        )
    )
    op.execute(text("ALTER TABLE public.recordings_s3 ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.recordings_s3 FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            """
            DO $recordings_policy$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.pg_policy
                     WHERE polrelid = 'public.recordings_s3'::regclass
                       AND polname = 'recordings_s3_tenant_isolation'
                ) THEN
                    CREATE POLICY recordings_s3_tenant_isolation
                        ON public.recordings_s3
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
            $recordings_policy$;
            """
        )
    )


def _adopt_contact_list_schema() -> None:
    """Bring the manually-installed contact-list feature under Alembic.

    Production received these objects from
    ``database/migrations/20260703_add_contact_lists.sql``.  A canonical fresh
    install (``complete_schema.sql`` followed by Alembic), however, reaches
    0040 without them.  Record the pre-upgrade catalog before using idempotent
    DDL so downgrade can remove only objects this revision actually created.
    """

    op.execute(
        text(
            """
            CREATE TABLE public.talky_0041_contact_lists_adoption (
                singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                contact_lists_preexisting BOOLEAN NOT NULL,
                leads_list_id_preexisting BOOLEAN NOT NULL,
                contact_campaign_index_preexisting BOOLEAN NOT NULL,
                leads_list_index_preexisting BOOLEAN NOT NULL,
                contact_policy_preexisting BOOLEAN NOT NULL,
                contact_rls_enabled_preexisting BOOLEAN NOT NULL,
                contact_rls_forced_preexisting BOOLEAN NOT NULL,
                contact_tenant_not_null_preexisting BOOLEAN NOT NULL,
                prior_bypass_rls TEXT NOT NULL,
                prior_lock_timeout TEXT NOT NULL
            )
            """
        )
    )
    op.execute(
        text(
            """
            INSERT INTO public.talky_0041_contact_lists_adoption (
                contact_lists_preexisting,
                leads_list_id_preexisting,
                contact_campaign_index_preexisting,
                leads_list_index_preexisting,
                contact_policy_preexisting,
                contact_rls_enabled_preexisting,
                contact_rls_forced_preexisting,
                contact_tenant_not_null_preexisting,
                prior_bypass_rls,
                prior_lock_timeout
            )
            SELECT
                to_regclass('public.contact_lists') IS NOT NULL,
                EXISTS (
                    SELECT 1
                      FROM pg_catalog.pg_attribute
                     WHERE attrelid = 'public.leads'::regclass
                       AND attname = 'list_id'
                       AND attnum > 0
                       AND NOT attisdropped
                ),
                to_regclass('public.idx_contact_lists_campaign') IS NOT NULL,
                to_regclass('public.idx_leads_list_id') IS NOT NULL,
                EXISTS (
                    SELECT 1
                      FROM pg_catalog.pg_policy AS policy
                      JOIN pg_catalog.pg_class AS relation
                        ON relation.oid = policy.polrelid
                      JOIN pg_catalog.pg_namespace AS namespace
                        ON namespace.oid = relation.relnamespace
                     WHERE namespace.nspname = 'public'
                       AND relation.relname = 'contact_lists'
                       AND policy.polname = 'contact_lists_tenant_isolation'
                ),
                COALESCE((
                    SELECT relrowsecurity
                      FROM pg_catalog.pg_class
                     WHERE oid = to_regclass('public.contact_lists')
                ), FALSE),
                COALESCE((
                    SELECT relforcerowsecurity
                      FROM pg_catalog.pg_class
                     WHERE oid = to_regclass('public.contact_lists')
                ), FALSE),
                COALESCE((
                    SELECT attnotnull
                      FROM pg_catalog.pg_attribute
                     WHERE attrelid = to_regclass('public.contact_lists')
                       AND attname = 'tenant_id'
                       AND attnum > 0
                       AND NOT attisdropped
                ), FALSE),
                COALESCE(
                    NULLIF(current_setting('app.bypass_rls', TRUE), ''),
                    'off'
                ),
                COALESCE(current_setting('lock_timeout', TRUE), '0')
            """
        )
    )
    op.execute(text("SELECT set_config('lock_timeout', '5s', TRUE)"))
    op.execute(text("SELECT set_config('app.bypass_rls', 'on', TRUE)"))

    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.contact_lists (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                campaign_id UUID NOT NULL,
                tenant_id UUID,
                name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.leads "
            "ADD COLUMN IF NOT EXISTS list_id UUID"
        )
    )
    op.execute(
        text(
            """
            DO $shape$
            DECLARE broken text;
            BEGIN
                WITH expected(relation_name, column_name, data_type, nullable) AS (
                    VALUES
                        ('contact_lists', 'id', 'uuid', FALSE),
                        ('contact_lists', 'campaign_id', 'uuid', FALSE),
                        -- Existing installations may already have tightened
                        -- tenant_id.  Both states are accepted here; 0041
                        -- makes it NOT NULL after the data preflight.
                        ('contact_lists', 'tenant_id', 'uuid', NULL),
                        ('contact_lists', 'name', 'text', FALSE),
                        ('contact_lists', 'source', 'text', FALSE),
                        ('contact_lists', 'is_active', 'boolean', FALSE),
                        ('contact_lists', 'created_at',
                         'timestamp with time zone', FALSE),
                        ('leads', 'list_id', 'uuid', TRUE)
                ), actual AS (
                    SELECT
                        expected.relation_name,
                        expected.column_name,
                        format_type(attribute.atttypid, attribute.atttypmod) AS data_type,
                        NOT attribute.attnotnull AS nullable
                    FROM expected
                    LEFT JOIN pg_catalog.pg_class AS relation
                      ON relation.oid = to_regclass(
                          'public.' || expected.relation_name
                      )
                     AND relation.relkind = 'r'
                    LEFT JOIN pg_catalog.pg_attribute AS attribute
                      ON attribute.attrelid = relation.oid
                     AND attribute.attname = expected.column_name
                     AND attribute.attnum > 0
                     AND NOT attribute.attisdropped
                )
                SELECT string_agg(
                           relation_name || '.' || column_name,
                           ', ' ORDER BY relation_name, column_name
                       )
                  INTO broken
                  FROM actual
                  JOIN expected USING (relation_name, column_name)
                 WHERE actual.data_type IS DISTINCT FROM expected.data_type
                    OR (
                        expected.nullable IS NOT NULL
                        AND actual.nullable IS DISTINCT FROM expected.nullable
                    );

                IF broken IS NOT NULL THEN
                    RAISE EXCEPTION
                        '0041 found incompatible contact-list schema on: %',
                        broken;
                END IF;
            END
            $shape$;
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_contact_lists_campaign "
            "ON public.contact_lists (campaign_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_leads_list_id "
            "ON public.leads (list_id) WHERE list_id IS NOT NULL"
        )
    )

    op.execute(text("ALTER TABLE public.contact_lists ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.contact_lists FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            """
            DO $policy$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.pg_policy AS policy
                     WHERE policy.polrelid = 'public.contact_lists'::regclass
                       AND policy.polname = 'contact_lists_tenant_isolation'
                ) THEN
                    CREATE POLICY contact_lists_tenant_isolation
                        ON public.contact_lists
                        FOR ALL
                        USING (
                            COALESCE(
                                NULLIF(
                                    current_setting('app.bypass_rls', TRUE),
                                    ''
                                )::boolean,
                                FALSE
                            )
                            OR tenant_id = NULLIF(
                                current_setting(
                                    'app.current_tenant_id', TRUE
                                ),
                                ''
                            )::uuid
                        )
                        WITH CHECK (
                            COALESCE(
                                NULLIF(
                                    current_setting('app.bypass_rls', TRUE),
                                    ''
                                )::boolean,
                                FALSE
                            )
                            OR tenant_id = NULLIF(
                                current_setting(
                                    'app.current_tenant_id', TRUE
                                ),
                                ''
                            )::uuid
                        );
                END IF;
            END
            $policy$;
            """
        )
    )
    op.execute(
        text(
            """
            DO $policy_postcondition$
            DECLARE policy_count integer;
            DECLARE canonical boolean;
            BEGIN
                SELECT
                    count(*),
                    bool_and(
                        cmd = 'ALL'
                        AND COALESCE(qual, '') LIKE '%app.bypass_rls%'
                        AND COALESCE(qual, '') LIKE '%app.current_tenant_id%'
                        AND COALESCE(qual, '') NOT LIKE '%tenant_id IS NULL%'
                        AND COALESCE(with_check, '') LIKE '%app.bypass_rls%'
                        AND COALESCE(with_check, '') LIKE '%app.current_tenant_id%'
                        AND COALESCE(with_check, '') NOT LIKE '%tenant_id IS NULL%'
                    )
                  INTO policy_count, canonical
                  FROM pg_catalog.pg_policies
                 WHERE schemaname = 'public'
                   AND tablename = 'contact_lists';

                IF policy_count <> 1 OR canonical IS DISTINCT FROM TRUE THEN
                    RAISE EXCEPTION
                        '0041 contact_lists RLS policy is missing or incompatible';
                END IF;
                IF NOT (
                    SELECT relrowsecurity AND relforcerowsecurity
                      FROM pg_catalog.pg_class
                     WHERE oid = 'public.contact_lists'::regclass
                ) THEN
                    RAISE EXCEPTION
                        '0041 contact_lists must have ENABLE and FORCE RLS';
                END IF;
            END
            $policy_postcondition$;
            """
        )
    )


def _preflight() -> None:
    op.execute(
        text(
            """
            DO $preflight$
            DECLARE broken text;
            BEGIN
                WITH checks(relation_name, violation_count) AS (
                    SELECT 'leads.campaign', count(*)
                      FROM public.leads AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.id IS NULL
                    UNION ALL
                    SELECT 'calls.campaign', count(*)
                      FROM public.calls AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.campaign_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'dialer_jobs.campaign', count(*)
                      FROM public.dialer_jobs AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.id IS NULL
                    UNION ALL
                    SELECT 'assistant_actions.campaign', count(*)
                      FROM public.assistant_actions AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.campaign_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'contact_lists.campaign_or_null_tenant', count(*)
                      FROM public.contact_lists AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.tenant_id IS NULL
                        OR parent.id IS NULL
                    UNION ALL
                    SELECT 'leads.contact_list', count(*)
                      FROM public.leads AS child
                      LEFT JOIN public.contact_lists AS parent
                        ON parent.id = child.list_id
                       AND parent.campaign_id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.list_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'call_lead_details.call', count(*)
                      FROM public.call_lead_details AS child
                      LEFT JOIN public.calls AS parent
                        ON parent.id = child.call_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.id IS NULL
                    UNION ALL
                    SELECT 'call_lead_details.lead', count(*)
                      FROM public.call_lead_details AS child
                      LEFT JOIN public.leads AS parent
                        ON parent.id = child.lead_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.lead_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'call_lead_details.campaign', count(*)
                      FROM public.call_lead_details AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.campaign_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'recordings_s3.call', count(*)
                      FROM public.recordings_s3 AS child
                      LEFT JOIN public.calls AS parent
                        ON parent.id = child.call_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.id IS NULL
                    UNION ALL
                    SELECT 'recordings_s3.campaign', count(*)
                      FROM public.recordings_s3 AS child
                      LEFT JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.campaign_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'leads.live_inbound_campaign', count(*)
                      FROM public.leads AS child
                      JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.status IS DISTINCT FROM 'deleted'
                       AND parent.direction IS DISTINCT FROM 'outbound'
                    UNION ALL
                    SELECT 'contact_lists.inbound_campaign', count(*)
                      FROM public.contact_lists AS child
                      JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.direction IS DISTINCT FROM 'outbound'
                    UNION ALL
                    SELECT 'dialer_jobs.inbound_campaign', count(*)
                      FROM public.dialer_jobs AS child
                      JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE parent.direction IS DISTINCT FROM 'outbound'
                    UNION ALL
                    -- Ownership chain. Keyed on tenant only, exactly like the
                    -- constraints it gates; see _FOREIGN_KEYS for why campaign
                    -- is excluded. All three are 0 on production 2026-09-03.
                    SELECT 'dialer_jobs.lead_ownership', count(*)
                      FROM public.dialer_jobs AS child
                      LEFT JOIN public.leads AS parent
                        ON parent.id = child.lead_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.lead_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'calls.lead_ownership', count(*)
                      FROM public.calls AS child
                      LEFT JOIN public.leads AS parent
                        ON parent.id = child.lead_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.lead_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    SELECT 'calls.dialer_job_ownership', count(*)
                      FROM public.calls AS child
                      LEFT JOIN public.dialer_jobs AS parent
                        ON parent.id = child.dialer_job_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.dialer_job_id IS NOT NULL
                       AND parent.id IS NULL
                    UNION ALL
                    -- Mirrors calls_outbound_campaign_guard exactly: an
                    -- outbound-DIRECTION call may never sit on an inbound
                    -- campaign. A genuine inbound call carries
                    -- direction='inbound' and is untouched. Verified on
                    -- production 2026-09-03: 0 existing rows violate this.
                    SELECT 'calls.outbound_inbound_campaign', count(*)
                      FROM public.calls AS child
                      JOIN public.campaigns AS parent
                        ON parent.id = child.campaign_id
                       AND parent.tenant_id = child.tenant_id
                     WHERE child.direction = 'outbound'
                       AND parent.direction IS DISTINCT FROM 'outbound'
                )
                SELECT string_agg(
                           format('%s=%s', relation_name, violation_count),
                           ', ' ORDER BY relation_name
                       )
                  INTO broken
                  FROM checks
                 WHERE violation_count > 0;

                IF broken IS NOT NULL THEN
                    RAISE EXCEPTION
                        '0041 tenant/campaign preflight failed: %', broken;
                END IF;
            END
            $preflight$;
            """
        )
    )


def upgrade() -> None:
    _repair_recordings_s3_schema()
    _adopt_contact_list_schema()
    _preflight()

    # contact_lists is tenant-owned by definition. Null was only a legacy DDL
    # omission; the preflight proves no row relies on it before tightening.
    op.execute(
        text(
            "ALTER TABLE public.contact_lists "
            "ALTER COLUMN tenant_id SET NOT NULL"
        )
    )
    # The leading IDs are already primary keys. These redundant keys exist only
    # as legal composite-FK targets and do not change logical uniqueness.
    op.execute(
        text(
            "ALTER TABLE public.leads "
            "ADD CONSTRAINT leads_id_tenant_unique UNIQUE (id, tenant_id)"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.dialer_jobs "
            "ADD CONSTRAINT dialer_jobs_id_tenant_unique UNIQUE (id, tenant_id)"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.contact_lists "
            "ADD CONSTRAINT contact_lists_id_campaign_tenant_unique "
            "UNIQUE (id, campaign_id, tenant_id)"
        )
    )

    for table, name, definition in _FOREIGN_KEYS:
        op.execute(
            text(
                f"ALTER TABLE public.{table} ADD CONSTRAINT {name} "
                f"{definition} NOT VALID"
            )
        )
    for table, name, _definition in _FOREIGN_KEYS:
        op.execute(
            text(f"ALTER TABLE public.{table} VALIDATE CONSTRAINT {name}")
        )

    op.execute(
        text(
            """
            CREATE FUNCTION public.talky_require_outbound_campaign_artifact()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = pg_catalog, public
            AS $guard$
            DECLARE
                campaign_direction text;
            BEGIN
                -- FOR SHARE conflicts with the FOR UPDATE held by campaign
                -- conversion, making the direction check and artifact write
                -- one serialized decision instead of a stale pre-check.
                SELECT campaign.direction
                  INTO campaign_direction
                  FROM public.campaigns AS campaign
                 WHERE campaign.id = NEW.campaign_id
                   AND campaign.tenant_id = NEW.tenant_id
                   FOR SHARE;

                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'outbound artifact campaign is missing or not visible'
                        USING ERRCODE = '23503',
                              CONSTRAINT = TG_TABLE_NAME || '_campaign_tenant_fk';
                END IF;
                IF campaign_direction IS DISTINCT FROM 'outbound' THEN
                    RAISE EXCEPTION
                        '%_outbound_campaign_guard: outbound artifact cannot target an inbound campaign',
                        TG_TABLE_NAME
                        USING ERRCODE = '23514',
                              CONSTRAINT = TG_TABLE_NAME || '_outbound_campaign_guard';
                END IF;
                RETURN NEW;
            END
            $guard$;
            """
        )
    )
    for table, name, predicate in _OUTBOUND_GUARD_TRIGGERS:
        predicate_sql = f" {predicate}" if predicate else ""
        op.execute(
            text(
                f"CREATE TRIGGER {name} "
                f"BEFORE INSERT OR UPDATE ON public.{table} "
                f"FOR EACH ROW{predicate_sql} "
                "EXECUTE FUNCTION public.talky_require_outbound_campaign_artifact()"
            )
        )

    op.execute(
        text(
            """
            SELECT
                set_config('app.bypass_rls', prior_bypass_rls, TRUE),
                set_config('lock_timeout', prior_lock_timeout, TRUE)
              FROM public.talky_0041_contact_lists_adoption
             WHERE singleton
            """
        )
    )


def downgrade() -> None:
    for table, name, _predicate in reversed(_OUTBOUND_GUARD_TRIGGERS):
        op.execute(
            text(f"DROP TRIGGER IF EXISTS {name} ON public.{table}")
        )
    op.execute(
        text(
            "DROP FUNCTION IF EXISTS "
            "public.talky_require_outbound_campaign_artifact()"
        )
    )
    for table, name, _definition in reversed(_FOREIGN_KEYS):
        op.execute(
            text(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")
        )
    op.execute(
        text(
            "ALTER TABLE public.contact_lists "
            "DROP CONSTRAINT IF EXISTS contact_lists_id_campaign_tenant_unique"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.leads "
            "DROP CONSTRAINT IF EXISTS leads_id_tenant_unique"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.dialer_jobs "
            "DROP CONSTRAINT IF EXISTS dialer_jobs_id_tenant_unique"
        )
    )
    op.execute(
        text(
            """
            DO $owned_cleanup$
            DECLARE adoption public.talky_0041_contact_lists_adoption%ROWTYPE;
            BEGIN
                SELECT * INTO adoption
                  FROM public.talky_0041_contact_lists_adoption
                 WHERE singleton;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        '0041 adoption state is missing; refusing destructive downgrade';
                END IF;

                IF adoption.contact_lists_preexisting THEN
                    IF NOT adoption.contact_tenant_not_null_preexisting THEN
                        ALTER TABLE public.contact_lists
                            ALTER COLUMN tenant_id DROP NOT NULL;
                    END IF;
                    IF NOT adoption.contact_policy_preexisting THEN
                        DROP POLICY IF EXISTS contact_lists_tenant_isolation
                            ON public.contact_lists;
                    END IF;
                    IF NOT adoption.contact_rls_forced_preexisting THEN
                        ALTER TABLE public.contact_lists NO FORCE ROW LEVEL SECURITY;
                    END IF;
                    IF NOT adoption.contact_rls_enabled_preexisting THEN
                        ALTER TABLE public.contact_lists DISABLE ROW LEVEL SECURITY;
                    END IF;
                    IF NOT adoption.contact_campaign_index_preexisting THEN
                        DROP INDEX IF EXISTS public.idx_contact_lists_campaign;
                    END IF;
                ELSE
                    DROP TABLE public.contact_lists;
                END IF;

                IF NOT adoption.leads_list_id_preexisting THEN
                    IF NOT adoption.leads_list_index_preexisting THEN
                        DROP INDEX IF EXISTS public.idx_leads_list_id;
                    END IF;
                    ALTER TABLE public.leads DROP COLUMN list_id;
                ELSIF NOT adoption.leads_list_index_preexisting THEN
                    DROP INDEX IF EXISTS public.idx_leads_list_id;
                END IF;
            END
            $owned_cleanup$;
            """
        )
    )
    op.execute(
        text("DROP TABLE public.talky_0041_contact_lists_adoption")
    )
