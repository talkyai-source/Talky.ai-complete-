"""Join published Ubuntu bootstrap and Admin/payment histories.

This marker adds no business SQL. Each missing predecessor runs once while
published revision identities and parent relationships remain addressable.
"""

revision = "0082_ubuntu_admin_merge"
down_revision = ("0081_admin_payment_merge", "0075_release_history_merge")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Retain both published histories; use a reviewed forward migration")
