"""Join the published Admin release and team payment-check history.

Both predecessor identities remain unchanged. This marker changes no schema
or business data; only missing predecessors run when upgrading either branch.
"""

revision = "0081_admin_payment_merge"
down_revision = ("0080_supplier_allowance_merge", "0073_payment_test_plan")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Retain both released histories; use a reviewed forward migration")
