"""Join supplier evidence and the deployed allowance-anchor history.

Both published branches and the previous0078 merge retain their identities.
This marker changes no schema or business data; only missing predecessors run.
"""

revision = "0080_supplier_allowance_merge"
down_revision = ("0079_supplier_evidence", "0072_allowance_anchor")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Retain both released histories; use a reviewed forward migration")
