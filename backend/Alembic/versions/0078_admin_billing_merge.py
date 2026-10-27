"""Join the deployed paid-billing and Admin release histories.

Both predecessors retain their published revision identities and parents.
Alembic applies only the missing branch before this marker. In particular, an
existing paid-billing stamp must never replay catalog changes while installing
the Admin schema. This merge itself changes no schema or business data.
"""

revision = "0078_admin_billing_merge"
down_revision = ("0077_legacy_tenant_rls_repair", "0071_paid_only_billing")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Retain both released histories; use a reviewed forward migration")
