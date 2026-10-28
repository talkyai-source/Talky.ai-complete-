"""Keep one terminal outcome for each server-prepared Admin user operation.

Preparation and terminal receipts reuse the mandatory immutable audit chain.
These indexes add no workflow table or alternate source of account state.
The application serializes prepare/cancel/mutate with its existing audit and
administrator locks; uniqueness remains a database-level final safeguard.
"""
from alembic import op

revision = "0076_admin_user_operations"
down_revision = "0075_admin_user_review"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""CREATE UNIQUE INDEX audit_user_operation_terminal_unique
        ON public.audit_logs ((metadata->>'user_operation_id'))
        WHERE resource_type='user'
          AND metadata->'user_operation_version'='1'::jsonb
          AND metadata ? 'user_operation_id'
          AND action IN ('user_created','user_updated','user_deleted',
                         'user_deactivated','user_primary_membership_configured',
                         'user_operation_cancelled')""")
    op.execute("""CREATE INDEX audit_user_operation_actor_prepared
        ON public.audit_logs (actor_id, event_time DESC, event_id DESC)
        WHERE resource_type='user'
          AND action='user_operation_prepared'
          AND metadata->'user_operation_version'='1'::jsonb""")


def downgrade():
    raise RuntimeError("User operation outcomes must be retained; use a reviewed forward migration")
