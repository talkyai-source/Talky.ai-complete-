"""Join the published billing and Ubuntu bootstrap repair histories.

Both branches keep their original revision identities and parents. Upgrading
from either published head applies only its missing branch before this marker.
The merge itself changes no schema, purchase terms, incident rows or event data.
"""

revision = "0075_release_history_merge"
down_revision = ("0074_incidents_bootstrap", "0073_payment_test_plan")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Retain both published histories; use a reviewed forward migration")
