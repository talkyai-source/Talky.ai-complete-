"""Version the profile and membership reviewed by existing Admin user actions.

Each trigger updates only its own row, so membership writers need no new
cross-table lock. Every update advances the version, including ABA changes
and writers outside the Admin routes. The API combines both row versions.
"""
from alembic import op

revision = "0075_admin_user_review"
down_revision = "0074_contact_enquiry_revision"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in ("user_profiles", "tenant_users"):
        op.execute(f"""ALTER TABLE public.{table}
            ADD COLUMN admin_revision BIGINT NOT NULL DEFAULT 0
            CONSTRAINT {table}_admin_revision_nonnegative CHECK (admin_revision >= 0)""")
    op.execute("""CREATE FUNCTION public.advance_admin_user_revision()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
            NEW.admin_revision := OLD.admin_revision + 1;
            RETURN NEW;
        END;
        $$""")
    for table, trigger in (
        ("user_profiles", "advance_user_profile_admin_revision"),
        ("tenant_users", "advance_tenant_user_admin_revision"),
    ):
        op.execute(f"""CREATE TRIGGER {trigger}
            BEFORE UPDATE ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.advance_admin_user_revision()""")
    # Existing mandatory business audits also retain create-request outcomes.
    # The same actor/request can be created or cancelled exactly once.
    op.execute("""CREATE UNIQUE INDEX audit_user_create_request_unique
        ON public.audit_logs (actor_id, (metadata->>'user_create_request_id'))
        WHERE resource_type='user'
          AND action IN ('user_created','user_create_cancelled')
          AND metadata ? 'user_create_request_id'""")


def downgrade():
    raise RuntimeError("Reviewed user versions must be retained; use a reviewed forward migration")
