"""Keep every disabled tenant AI credential; enforce one ACTIVE row per key.

Bug hunt 2026-10-10 (billing-money-credentials-3). tenant_ai_credentials carried

    CONSTRAINT tenant_ai_credentials_unique_active
        UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)

whose comment says "only one ACTIVE credential per (tenant, provider, kind)".
Because status is part of the key it also allows exactly ONE 'disabled' row: the
second rotation or revoke of the same credential flipped a second row to
'disabled', hit the constraint, answered 500 and left the key being rotated out
active. The application worked around it by DELETEing the previous disabled row
first, which erased the rotation history.

The rule that was meant is a partial unique index over the active rows only. This
builds it first (uniqueness of the active key is never unenforced), then drops the
constraint. Existing rows already satisfy it (at most one active row per credential
under the old constraint), so no data is touched. Any ON CONFLICT written against
the new index must repeat its predicate (WHERE status = 'active'); none exists in
the application today.

Idempotent, and a no-op where the table does not exist: databases built from
complete_schema.sql never had it (it was applied to production by hand).

Downgrade refuses while any (tenant, provider, kind, status) group holds more than
one row. Those rows are the history this migration allows; the old constraint
cannot hold them, and deleting them is not the migration's decision.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0067_ai_credentials_history"
down_revision: Union[str, None] = "0066_crm_skip_test_calls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement per op.execute: Alembic runs through SQLAlchemy's asyncpg dialect,
# whose prepared statements refuse several commands at once.
CREATE_ACTIVE_INDEX_SQL = """
DO $$
BEGIN
    IF to_regclass('public.tenant_ai_credentials') IS NOT NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_ai_credentials_active
            ON public.tenant_ai_credentials (tenant_id, provider, credential_kind)
            WHERE status = 'active';
    END IF;
END $$
"""
DROP_OLD_CONSTRAINT_SQL = """
ALTER TABLE IF EXISTS public.tenant_ai_credentials
    DROP CONSTRAINT IF EXISTS tenant_ai_credentials_unique_active
"""
UPGRADE_STATEMENTS = (CREATE_ACTIVE_INDEX_SQL, DROP_OLD_CONSTRAINT_SQL)

# Taken under SHARE ROW EXCLUSIVE so no history can appear between the check and
# the ADD CONSTRAINT below. The whole alembic run is one transaction, so a refusal
# (or any later failure) leaves the schema exactly as it was.
REFUSE_IF_HISTORY_SQL = """
DO $$
DECLARE
    duplicate_groups integer;
BEGIN
    IF to_regclass('public.tenant_ai_credentials') IS NULL THEN
        RETURN;
    END IF;
    LOCK TABLE public.tenant_ai_credentials IN SHARE ROW EXCLUSIVE MODE;
    SELECT count(*) INTO duplicate_groups FROM (
        SELECT 1 FROM public.tenant_ai_credentials
        GROUP BY tenant_id, provider, credential_kind, status
        HAVING count(*) > 1
    ) AS duplicated;
    IF duplicate_groups > 0 THEN
        RAISE EXCEPTION 'Cannot downgrade 0067_ai_credentials_history: % group(s) of tenant_ai_credentials rows share (tenant, provider, kind, status). They are the rotation history 0067 keeps and the pre-0067 unique constraint cannot hold them. Archive or delete the older rows deliberately, then retry.', duplicate_groups;
    END IF;
END $$
"""
# The early RETURN matters: SQL gives no short-circuit guarantee for AND, and
# 'public.tenant_ai_credentials'::regclass raises when the table is absent.
RESTORE_OLD_CONSTRAINT_SQL = """
DO $$
BEGIN
    IF to_regclass('public.tenant_ai_credentials') IS NULL THEN
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.tenant_ai_credentials'::regclass
          AND conname = 'tenant_ai_credentials_unique_active'
    ) THEN
        ALTER TABLE public.tenant_ai_credentials
            ADD CONSTRAINT tenant_ai_credentials_unique_active
            UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status);
    END IF;
END $$
"""
DROP_ACTIVE_INDEX_SQL = "DROP INDEX IF EXISTS public.uq_tenant_ai_credentials_active"
DOWNGRADE_STATEMENTS = (REFUSE_IF_HISTORY_SQL, RESTORE_OLD_CONSTRAINT_SQL, DROP_ACTIVE_INDEX_SQL)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
