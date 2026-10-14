"""Let a tenant delete any PBX line: retire it in place when history points at it.

Owner report 2026-10-14 ("I am not able to delete any PBX"). Production, 2026-10-13:
the Settings page deactivates a line (200) and then DELETEs it, and the DELETE
answered 409 "Trunk In Use" for every line that ever carried an inbound number.
``inbound_did_assignments`` keeps archived rows as call history and holds a
DEFERRABLE foreign key to ``tenant_sip_trunks(id, tenant_id)``, so such a line can
never be hard-deleted without erasing that history.

``deleted_at`` retires the row instead. The API treats a row with ``deleted_at`` as
absent (not listed, 404 on every action, never assignable) and the delete endpoint
hard-deletes only when nothing references the line. A retired line is always
inactive, so routing and call admission already ignore it.

The per-tenant name index becomes partial (``WHERE deleted_at IS NULL``) under the
same name, so the name of a retired line can be used again. Any ``ON CONFLICT``
written against it must repeat that predicate; none exists in the application.

Idempotent, and a no-op where ``tenant_sip_trunks`` does not exist. Existing rows
keep ``deleted_at`` NULL, so no data changes. Dropping and re-creating the index
happens inside the migration transaction: DROP INDEX holds an ACCESS EXCLUSIVE lock
on the table until commit, so no write can slip in between.

Downgrade refuses while two rows (live or retired) share a tenant and a name: the
old unique index covered every row and, once the column is gone, retired lines are
ordinary rows again. They come back inactive and visible; deleting or renaming them
is the operator's decision, not the migration's.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0070_trunk_soft_delete"
down_revision: Union[str, None] = "0069_assemblyai_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement per op.execute: Alembic runs through SQLAlchemy's asyncpg dialect,
# whose prepared statements refuse several commands at once.
ADD_COLUMN_SQL = """
ALTER TABLE IF EXISTS public.tenant_sip_trunks
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz
"""
# The early RETURN matters: SQL gives no short-circuit guarantee for AND/OR, and
# pg_get_indexdef(NULL) is NULL, so a missing index must be tested on its own.
PARTIAL_NAME_INDEX_SQL = """
DO $$
BEGIN
    IF to_regclass('public.tenant_sip_trunks') IS NULL THEN
        RETURN;
    END IF;
    IF to_regclass('public.idx_tenant_sip_trunks_tenant_name_unique') IS NULL
       OR pg_get_indexdef(to_regclass('public.idx_tenant_sip_trunks_tenant_name_unique'))
          NOT LIKE '%deleted_at IS NULL%' THEN
        DROP INDEX IF EXISTS public.idx_tenant_sip_trunks_tenant_name_unique;
        CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique
            ON public.tenant_sip_trunks (tenant_id, lower(trunk_name))
            WHERE deleted_at IS NULL;
    END IF;
    COMMENT ON COLUMN public.tenant_sip_trunks.deleted_at IS
        'Set when the tenant deleted the line while inbound assignment history still references it. NULL = live. A row with deleted_at is invisible to every API and is always inactive.';
END $$
"""
UPGRADE_STATEMENTS = (ADD_COLUMN_SQL, PARTIAL_NAME_INDEX_SQL)

# Taken under SHARE ROW EXCLUSIVE so no duplicate can appear between the check and
# the CREATE UNIQUE INDEX below. The whole alembic run is one transaction, so a
# refusal (or any later failure) leaves the schema exactly as it was.
REFUSE_IF_DUPLICATE_NAMES_SQL = """
DO $$
DECLARE
    duplicate_groups integer;
BEGIN
    IF to_regclass('public.tenant_sip_trunks') IS NULL THEN
        RETURN;
    END IF;
    LOCK TABLE public.tenant_sip_trunks IN SHARE ROW EXCLUSIVE MODE;
    SELECT count(*) INTO duplicate_groups FROM (
        SELECT 1 FROM public.tenant_sip_trunks
        GROUP BY tenant_id, lower(trunk_name)
        HAVING count(*) > 1
    ) AS duplicated;
    IF duplicate_groups > 0 THEN
        RAISE EXCEPTION 'Cannot downgrade 0070_trunk_soft_delete: % tenant/name group(s) in tenant_sip_trunks hold more than one row (a live line and one or more deleted lines with the same name). The pre-0070 unique index covers every row and deleted lines become ordinary rows again. Rename or remove the older deleted rows deliberately, then retry.', duplicate_groups;
    END IF;
END $$
"""
RESTORE_FULL_NAME_INDEX_SQL = """
DO $$
BEGIN
    IF to_regclass('public.tenant_sip_trunks') IS NULL THEN
        RETURN;
    END IF;
    DROP INDEX IF EXISTS public.idx_tenant_sip_trunks_tenant_name_unique;
    CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique
        ON public.tenant_sip_trunks (tenant_id, lower(trunk_name));
END $$
"""
DROP_COLUMN_SQL = """
ALTER TABLE IF EXISTS public.tenant_sip_trunks
    DROP COLUMN IF EXISTS deleted_at
"""
DOWNGRADE_STATEMENTS = (REFUSE_IF_DUPLICATE_NAMES_SQL, RESTORE_FULL_NAME_INDEX_SQL, DROP_COLUMN_SQL)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
