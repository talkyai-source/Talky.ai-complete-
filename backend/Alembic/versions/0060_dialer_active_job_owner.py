"""Install the existing one-active-job-per-lead contract without discarding jobs.

Unlike historical 20260612_dialer_job_dedup.sql, ambiguous existing owners
stop the migration for operator reconciliation; no survivor is guessed.
"""
from alembic import op

revision = "0060_dialer_active_job_owner"
down_revision = "0059_auth_identity_contract"
branch_labels = None
depends_on = None

ACTIVE_JOB_GUARD = r"""
DO $active_owner$
DECLARE
    target oid := 'public.dialer_jobs'::regclass;
    named_index oid;
    existing record;
    predicate text;
    statuses text[];
BEGIN
    PERFORM set_config('lock_timeout','5s',true);
    -- Prevent a new duplicate between the preflight and index build.
    LOCK TABLE public.dialer_jobs IN SHARE ROW EXCLUSIVE MODE;
    SELECT c.oid INTO named_index
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.oid=(SELECT relnamespace FROM pg_class WHERE oid=target)
       AND c.relname='uq_dialer_jobs_one_active_per_lead';
    IF named_index IS NOT NULL THEN
        SELECT i.*, am.amname, pg_get_expr(i.indpred,i.indrelid) AS predicate_sql,
               pg_get_indexdef(i.indexrelid,1,true) AS first_key
          INTO existing
          FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
          JOIN pg_am am ON am.oid=c.relam WHERE i.indexrelid=named_index;
        IF NOT FOUND OR existing.indrelid<>target OR NOT existing.indisunique
           OR NOT existing.indisvalid OR NOT existing.indisready OR NOT existing.indislive
           OR NOT existing.indimmediate OR existing.indnkeyatts<>1
           OR existing.first_key<>'lead_id' OR existing.amname<>'btree' THEN
            RAISE EXCEPTION '0060: named active-job index is invalid or has incompatible keys; inspect and reconcile it before retrying; no jobs changed';
        END IF;
        -- A valid unconditional unique lead index is stronger: retain it.
        IF existing.predicate_sql IS NULL THEN RETURN; END IF;
        -- Accept the canonical status-IN predicate (including a stronger
        -- superset) only. Unrecognized expressions require explicit review.
        predicate := regexp_replace(existing.predicate_sql,
            '::(character varying|text)(\[\])?', '', 'g');
        predicate := regexp_replace(predicate, '[[:space:]()]', '', 'g');
        SELECT array_agg(m[1]) INTO statuses
          FROM regexp_matches(predicate, '''([a-z_]+)''', 'g') AS m;
        IF regexp_replace(predicate, '''[a-z_]+''', '', 'g') !~ '^status=ANYARRAY\[,*\]$'
           OR statuses IS NULL OR NOT statuses @> ARRAY[
               'pending','queued','retry_scheduled','processing','calling']::text[] THEN
            RAISE EXCEPTION '0060: named active-job index has an unsupported predicate; inspect and reconcile it before retrying; no jobs changed';
        END IF;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM public.dialer_jobs
        WHERE status IN ('pending','queued','retry_scheduled','processing','calling')
        GROUP BY lead_id HAVING count(*)>1) THEN
        RAISE EXCEPTION '0060: multiple active jobs exist for a lead; reconcile their call and queue evidence before retrying; no jobs cancelled or deleted';
    END IF;
    CREATE UNIQUE INDEX uq_dialer_jobs_one_active_per_lead
        ON public.dialer_jobs(lead_id)
        WHERE status IN ('pending','queued','retry_scheduled','processing','calling');
END $active_owner$;
"""


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.execute(ACTIVE_JOB_GUARD)


def downgrade():
    # Code rollback must not remove a duplicate-call safety invariant.
    pass
