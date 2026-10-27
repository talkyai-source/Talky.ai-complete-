"""Fence connector refresh dispatch and retain truthful revocation outcomes.

Additive: existing authorizations start idle at generation zero. Deploy all
connector writers together after this migration; old writers do not honor claims.
An unfinished refresh is never taken over automatically: the provider may already
have rotated its token. Reauthorization creates a new account instead.

Downgrade deliberately refuses while a refresh is unresolved. Drain writers and
resolve/re-authorize these accounts first; removing the fence cannot be a rollback
strategy for an uncertain provider effect.
"""
from alembic import op

revision = "0071_connector_lifecycle"
down_revision = "0070_trunk_soft_delete"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = (
    """ALTER TABLE connector_accounts
       ADD COLUMN credential_generation bigint NOT NULL DEFAULT 0 CHECK (credential_generation >= 0),
       ADD COLUMN refresh_claim_id uuid,
       ADD COLUMN refresh_claimed_at timestamptz,
       ADD COLUMN refresh_state text NOT NULL DEFAULT 'idle'
         CHECK (refresh_state IN ('idle','in_flight','uncertain')),
       ADD COLUMN refresh_error text,
       ADD CONSTRAINT connector_refresh_claim_consistent CHECK (
         (refresh_state='idle' AND refresh_claim_id IS NULL AND refresh_claimed_at IS NULL)
         OR (refresh_state IN ('in_flight','uncertain') AND refresh_claim_id IS NOT NULL
             AND refresh_claimed_at IS NOT NULL))""",
    """ALTER TABLE connectors ADD COLUMN provider_revocation_status text NOT NULL DEFAULT 'not_requested'
       CHECK (provider_revocation_status IN ('not_requested','pending','confirmed','unconfirmed'))""",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE connectors, connector_accounts IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM connector_accounts WHERE refresh_state <> 'idle')
           OR EXISTS (SELECT 1 FROM connectors WHERE provider_revocation_status = 'pending') THEN
            RAISE EXCEPTION 'Cannot remove connector lifecycle fences while provider outcomes are unresolved';
        END IF;
    END $$""")
    op.execute("ALTER TABLE connectors DROP COLUMN provider_revocation_status")
    op.execute("""ALTER TABLE connector_accounts
       DROP CONSTRAINT connector_refresh_claim_consistent,
       DROP COLUMN credential_generation, DROP COLUMN refresh_claim_id,
       DROP COLUMN refresh_claimed_at, DROP COLUMN refresh_state, DROP COLUMN refresh_error""")
