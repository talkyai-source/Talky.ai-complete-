"""Retain the verified CRM account owning each durable remote receipt."""
from alembic import op
from sqlalchemy import text

revision = "0051_crm_destination_identity"
down_revision = "0050_assistant_execution_perms"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text("SET LOCAL lock_timeout = '5s'"))
    # No cascading connector FK: disconnecting must not erase evidence of
    # which account owns a remote object. Existing IDs are not backfilled.
    op.execute(text("ALTER TABLE crm_deliveries ADD COLUMN IF NOT EXISTS destination_connector_id UUID"))
    op.execute(text("ALTER TABLE crm_deliveries ADD COLUMN IF NOT EXISTS destination_account_id TEXT"))


def downgrade():
    # Retain identities through application rollback; dropping them would
    # make already performed external writes ambiguous again.
    pass
