"""durable pre-row inbound rejection log

Revision ID: 0036_inbound_rejection_log
Revises: 0035_user_profiles_role_widen
Create Date: 2026-08-31 00:00:00.000000

Calls rejected after a durable ``calls`` row exists already retain their
``admission_reason``. Earlier failures (unknown DID, disabled tenant, broken
runtime dependency, admission timeout) previously left no queryable trace.
This append-only table closes that gap without manufacturing a billable call.

Rows whose DID resolves to exactly one active assignment are tenant-owned and
may be read through the normal tenant RLS context. Unassigned/ambiguous DIDs
remain platform-only (``tenant_id IS NULL``); their ANI is never stored.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0036_inbound_rejection_log"
down_revision: str | None = "0035_user_profiles_role_widen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_POLICY = """
COALESCE(
    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
    FALSE
)
OR tenant_id = NULLIF(
    current_setting('app.current_tenant_id', TRUE), ''
)::uuid
""".strip()

_BYPASS_POLICY = """
COALESCE(
    NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean,
    FALSE
)
""".strip()


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE TABLE public.inbound_rejections (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                provider VARCHAR(64) NOT NULL,
                provider_call_id VARCHAR(255) NOT NULL,
                tenant_id UUID REFERENCES public.tenants(id) ON DELETE SET NULL,
                campaign_id UUID REFERENCES public.campaigns(id) ON DELETE SET NULL,
                inbound_config_id UUID
                    REFERENCES public.inbound_campaign_configs(id) ON DELETE SET NULL,
                assignment_id UUID
                    REFERENCES public.inbound_did_assignments(id) ON DELETE SET NULL,
                called_did VARCHAR(32),
                caller_ani VARCHAR(32),
                caller_ani_private BOOLEAN NOT NULL DEFAULT TRUE,
                ingress VARCHAR(64) NOT NULL DEFAULT 'asterisk',
                reason VARCHAR(96) NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                retention_until TIMESTAMPTZ NOT NULL
                    DEFAULT (NOW() + INTERVAL '90 days'),
                CONSTRAINT inbound_rejections_provider_call_unique
                    UNIQUE (provider, provider_call_id),
                CONSTRAINT inbound_rejections_provider_nonempty
                    CHECK (BTRIM(provider) <> ''),
                CONSTRAINT inbound_rejections_provider_call_nonempty
                    CHECK (BTRIM(provider_call_id) <> ''),
                CONSTRAINT inbound_rejections_reason_nonempty
                    CHECK (BTRIM(reason) <> ''),
                CONSTRAINT inbound_rejections_retention_valid
                    CHECK (retention_until >= occurred_at),
                CONSTRAINT inbound_rejections_unowned_ani_private
                    CHECK (
                        tenant_id IS NOT NULL
                        OR (caller_ani IS NULL AND caller_ani_private = TRUE)
                    )
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_rejections_tenant_occurred "
            "ON public.inbound_rejections (tenant_id, occurred_at DESC)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_rejections_retention "
            "ON public.inbound_rejections (retention_until, id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX idx_inbound_rejections_tenant_reason "
            "ON public.inbound_rejections (tenant_id, reason, occurred_at DESC)"
        )
    )

    op.execute(text("ALTER TABLE public.inbound_rejections ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE public.inbound_rejections FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY inbound_rejections_select ON public.inbound_rejections "
            f"FOR SELECT USING ({_TENANT_POLICY})"
        )
    )
    op.execute(
        text(
            "CREATE POLICY inbound_rejections_insert ON public.inbound_rejections "
            f"FOR INSERT WITH CHECK ({_TENANT_POLICY})"
        )
    )
    op.execute(
        text(
            "CREATE POLICY inbound_rejections_update ON public.inbound_rejections "
            "FOR UPDATE USING (FALSE)"
        )
    )
    # Only platform-internal retention cleanup may delete. Tenant requests can
    # read their rows but can neither rewrite nor erase the audit trail.
    op.execute(
        text(
            "CREATE POLICY inbound_rejections_delete ON public.inbound_rejections "
            f"FOR DELETE USING ({_BYPASS_POLICY})"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE public.inbound_rejections"))
