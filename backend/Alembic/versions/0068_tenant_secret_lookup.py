"""Index API-key validation: tenant_secrets.lookup_hash.

Bug hunt 2026-10-10 (billing-money-credentials-7). The validate-api-key route is
unauthenticated, and SecretsManager.validate_api_key unwrapped the DEK and
AES-GCM-decrypted EVERY active TENANT_API_KEY row, per request, to find the one
whose stored hash matched: CPU that grows with the number of tenants and that
anyone can trigger.

lookup_hash holds the hex SHA-256 of the issued API key so the row can be found
through an index. Keys are 48 random characters from a 62-symbol alphabet (about
285 bits), so an unsalted fast hash cannot be reversed or precomputed and is a
safe lookup key; it is NOT a password hash and must not be used for low-entropy
secrets. The application still verifies the decrypted value with a constant-time
comparison; lookup_hash only narrows the candidates.

The column is nullable and this migration decrypts nothing: rows created before
it (and every non-API-key secret) keep NULL. validate_api_key falls back to the
NULL rows only and fills lookup_hash as it reads them, so the fallback shrinks to
nothing.

Idempotent, and a no-op where tenant_secrets does not exist. Downgrade drops the
index and the column; both are derived data, re-created lazily after an upgrade.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0068_tenant_secret_lookup"
down_revision: Union[str, None] = "0067_ai_credentials_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement per op.execute: Alembic runs through SQLAlchemy's asyncpg dialect,
# whose prepared statements refuse several commands at once.
ADD_COLUMN_SQL = """
ALTER TABLE IF EXISTS public.tenant_secrets
    ADD COLUMN IF NOT EXISTS lookup_hash text
"""
# Partial on the rows validate_api_key can ever return, so the index stays as
# small as the number of live API keys. Both of its lookups (lookup_hash = $1 for
# the presented key, lookup_hash IS NULL for keys that predate the column) carry
# the predicate in their WHERE clause, and a btree indexes NULLs, so one index
# serves both: the legacy pass never scans the whole table.
CREATE_INDEX_SQL = """
DO $$
BEGIN
    IF to_regclass('public.tenant_secrets') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_tenant_secrets_lookup_hash
            ON public.tenant_secrets (lookup_hash)
            WHERE secret_type = 'TENANT_API_KEY' AND is_active = TRUE;
        COMMENT ON COLUMN public.tenant_secrets.lookup_hash IS
            'Hex SHA-256 of the issued TENANT_API_KEY, used only to find the row (random high-entropy keys, so no salt). NULL for rows created before 0068 and for other secret types; filled on first validation.';
    END IF;
END $$
"""
UPGRADE_STATEMENTS = (ADD_COLUMN_SQL, CREATE_INDEX_SQL)

DROP_INDEX_SQL = "DROP INDEX IF EXISTS public.idx_tenant_secrets_lookup_hash"
DROP_COLUMN_SQL = """
ALTER TABLE IF EXISTS public.tenant_secrets
    DROP COLUMN IF EXISTS lookup_hash
"""
DOWNGRADE_STATEMENTS = (DROP_INDEX_SQL, DROP_COLUMN_SQL)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
