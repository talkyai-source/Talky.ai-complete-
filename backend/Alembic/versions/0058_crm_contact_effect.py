"""Retain the original account-bound CRM contact creation intent."""
from alembic import op

revision = "0058_crm_contact_effect"
down_revision = "0057_transcript_save_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""ALTER TABLE crm_deliveries ADD COLUMN contact_effect JSONB
        CHECK (contact_effect IS NULL OR
          (jsonb_typeof(contact_effect) = 'object' AND octet_length(contact_effect::text) <= 32768))""")
    op.execute("""COMMENT ON COLUMN crm_deliveries.contact_effect IS
        'Immutable original contact-create arguments and source/account identity, committed before the provider write. NULL legacy evidence must not be reconstructed from current lead data.'""")


def downgrade():
    raise RuntimeError("Refusing to discard original CRM contact effect evidence during rollback")
