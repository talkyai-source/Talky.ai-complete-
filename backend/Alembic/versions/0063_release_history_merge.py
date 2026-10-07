"""Join the deployed heartbeat fix and the production-readiness history.

Both branches retain their original revision identities and parents. Upgrading
from either existing head applies the missing branch before this marker; no
schema, receipt or historical audit data is changed by the merge itself.
"""

revision = "0063_release_history_merge"
down_revision = ("0062_saved_acknowledgement", "0048_audit_skip_heartbeat")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Retain both release histories; use a reviewed forward migration")
