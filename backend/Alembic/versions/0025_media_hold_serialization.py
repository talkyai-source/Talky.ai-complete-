"""serialize compliance holds with irreversible media deletion

Revision ID: 0025_media_hold_serialization
Revises: 0024_recording_permissions
Create Date: 2026-08-26 00:00:00.000000

Object/local media deletion cannot participate in a PostgreSQL transaction.
This trigger makes every tenant or partner COMPLIANCE-hold write acquire the
same per-tenant advisory transaction lock held by the deletion API across its
final hold check, storage delete, and durable ``object_deleted`` transition.
Tenant partner-membership changes take that lock too, so assigning a tenant to
an already-held partner cannot create a second check/delete race. The three
operations therefore have one serial order and no check/delete gap.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0025_media_hold_serialization"
down_revision: str | None = "0024_recording_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION serialize_compliance_media_hold()
            RETURNS TRIGGER AS $$
            DECLARE
                held_tenant_id UUID;
            BEGIN
                IF NEW.suspension_type = 'COMPLIANCE'
                   AND NEW.is_active = TRUE
                   AND NEW.restored_at IS NULL THEN
                    IF NEW.target_type = 'tenant' THEN
                        PERFORM pg_advisory_xact_lock(
                            hashtextextended(
                                'talky:media-hold:' || NEW.target_id::text,
                                0
                            )
                        );
                    ELSIF NEW.target_type = 'partner' THEN
                        FOR held_tenant_id IN
                            SELECT id
                            FROM tenants
                            WHERE white_label_partner_id = NEW.target_id
                            ORDER BY id
                        LOOP
                            PERFORM pg_advisory_xact_lock(
                                hashtextextended(
                                    'talky:media-hold:' || held_tenant_id::text,
                                    0
                                )
                            );
                        END LOOP;
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION serialize_tenant_partner_membership()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.white_label_partner_id IS DISTINCT FROM
                   OLD.white_label_partner_id THEN
                    PERFORM pg_advisory_xact_lock(
                        hashtextextended(
                            'talky:media-hold:' || NEW.id::text,
                            0
                        )
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_serialize_tenant_partner_membership
                ON tenants
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_serialize_tenant_partner_membership
            BEFORE UPDATE OF white_label_partner_id
            ON tenants
            FOR EACH ROW EXECUTE FUNCTION serialize_tenant_partner_membership()
            """
        )
    )
    op.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_serialize_compliance_media_hold
                ON suspension_events
            """
        )
    )
    op.execute(
        text(
            """
            CREATE TRIGGER trg_serialize_compliance_media_hold
            BEFORE INSERT OR UPDATE OF
                target_type, target_id, suspension_type, is_active, restored_at,
                suspended_until
            ON suspension_events
            FOR EACH ROW EXECUTE FUNCTION serialize_compliance_media_hold()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_serialize_tenant_partner_membership "
            "ON tenants"
        )
    )
    op.execute(
        text("DROP FUNCTION IF EXISTS serialize_tenant_partner_membership()")
    )
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_serialize_compliance_media_hold "
            "ON suspension_events"
        )
    )
    op.execute(text("DROP FUNCTION IF EXISTS serialize_compliance_media_hold()"))
