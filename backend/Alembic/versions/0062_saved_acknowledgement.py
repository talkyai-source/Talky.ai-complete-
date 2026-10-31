"""Retain the original receipt when restoring a saved Gmail acknowledgement.

This does not authorize a resend or establish email delivery. A resolved action
and its tenant cannot be hard-deleted through SQL while this evidence exists.
User deletion remains unchanged: actor identity is an immutable UUID snapshot.
"""
from alembic import op

revision = "0062_saved_acknowledgement"
down_revision = "0061_dnc_runtime_contract"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Canonical assistant_actions has only its id PK and a partial tenant/key
    # unique index. This composite key binds the event to the saved tenant too.
    op.execute("ALTER TABLE public.assistant_actions ADD CONSTRAINT assistant_actions_id_tenant_unique UNIQUE(id,tenant_id)")
    op.execute("""CREATE TABLE public.assistant_action_resolutions (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL,
        action_id UUID NOT NULL,
        actor_id UUID NOT NULL,
        actor_role TEXT NOT NULL CHECK(actor_role='platform_admin'),
        request_id UUID NOT NULL,
        source_digest TEXT NOT NULL CHECK(source_digest ~ '^[0-9a-f]{64}$'),
        reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500 AND reason !~ '[[:cntrl:]]'),
        kind TEXT NOT NULL DEFAULT 'saved_gmail_acknowledgement' CHECK(kind='saved_gmail_acknowledgement'),
        original_status TEXT NOT NULL CHECK(original_status='unknown'),
        original_output JSONB NOT NULL CHECK(jsonb_typeof(original_output)='object'),
        original_timestamps JSONB NOT NULL CHECK(jsonb_typeof(original_timestamps)='object'),
        recovered_status TEXT NOT NULL CHECK(recovered_status='completed'),
        provider_status TEXT NOT NULL CHECK(provider_status IN ('accepted','provider_accepted')),
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,action_id),
        UNIQUE(actor_id,request_id),
        FOREIGN KEY(action_id,tenant_id) REFERENCES public.assistant_actions(id,tenant_id) ON DELETE RESTRICT
    )""")
    op.execute("ALTER TABLE public.assistant_action_resolutions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.assistant_action_resolutions FORCE ROW LEVEL SECURITY")
    principal = """EXISTS(SELECT 1 FROM public.user_profiles up
        WHERE up.id=NULLIF(current_setting('app.current_user_id',TRUE),'')::uuid
          AND up.role='platform_admin' AND up.is_active IS TRUE AND up.is_verified IS TRUE)"""
    # Worker bypass is deliberately insufficient. The route also rechecks its
    # authenticated session on this same transaction before and after locking
    # the action, so a lock wait cannot extend a revoked session.
    op.execute(f"CREATE POLICY action_resolutions_read ON public.assistant_action_resolutions FOR SELECT USING ({principal})")
    op.execute(f"""CREATE POLICY action_resolutions_insert ON public.assistant_action_resolutions FOR INSERT
        WITH CHECK ({principal} AND actor_id=NULLIF(current_setting('app.current_user_id',TRUE),'')::uuid)""")
    op.execute("""CREATE FUNCTION public.reject_action_resolution_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Action resolutions are append-only'; END $$""")
    op.execute("""CREATE TRIGGER action_resolutions_immutable BEFORE UPDATE OR DELETE
        ON public.assistant_action_resolutions FOR EACH ROW
        EXECUTE FUNCTION public.reject_action_resolution_mutation()""")


def downgrade():
    raise RuntimeError("Saved acknowledgement evidence must be retained; use a reviewed forward migration")
