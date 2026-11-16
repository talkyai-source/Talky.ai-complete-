"""Store the customer's permission confirmation for each contact list.

One row per import already exists (`contact_lists`, created by 0041 on every
build route; leads point to it through `leads.list_id`), so the confirmation
lives there: six nullable columns and one CHECK, no new table.

Existing lists are NOT backfilled: every consent column stays NULL, which the
CHECK allows. The tenant row-level-security policy on contact_lists is
row-scoped (FOR ALL, tenant_id), so it covers the new columns unchanged.
`consent_confirmed_by` may be NULL in a confirmed row because the user can be
deleted later (ON DELETE SET NULL). The permission date is compared with the
confirmation timestamp in UTC so the CHECK expression is immutable.

Idempotent: ADD COLUMN IF NOT EXISTS, and the constraint is added only when
absent. Downgrade refuses while any confirmation exists.
"""
from alembic import op

revision = "0096_contact_list_permission"
down_revision = "0095_webhook_receipt_dismissed"
branch_labels = None
depends_on = None

_BASES = "'express_consent','existing_customer','made_an_enquiry','other_documented','pre_existing_unverified'"
_CHECK = f"""CHECK (
            (consent_confirmed_at IS NULL AND consent_confirmed_by IS NULL
                AND consent_basis IS NULL AND consent_source IS NULL
                AND consent_permission_date IS NULL AND consent_statement_version IS NULL)
            OR (consent_confirmed_at IS NOT NULL
                AND consent_basis IN ({_BASES})
                AND char_length(btrim(consent_source)) BETWEEN 5 AND 500
                AND consent_permission_date IS NOT NULL
                AND consent_permission_date <= (consent_confirmed_at AT TIME ZONE 'UTC')::date
                AND char_length(consent_statement_version) BETWEEN 1 AND 40)
        )"""


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""ALTER TABLE contact_lists
        ADD COLUMN IF NOT EXISTS consent_confirmed_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS consent_confirmed_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS consent_basis TEXT,
        ADD COLUMN IF NOT EXISTS consent_source TEXT,
        ADD COLUMN IF NOT EXISTS consent_permission_date DATE,
        ADD COLUMN IF NOT EXISTS consent_statement_version TEXT""")
    op.execute(f"""DO $q96$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conrelid = 'public.contact_lists'::regclass
              AND conname = 'contact_lists_consent_complete'
        ) THEN
            ALTER TABLE contact_lists ADD CONSTRAINT contact_lists_consent_complete
            {_CHECK} NOT VALID;
        END IF;
    END $q96$""")
    op.execute("ALTER TABLE contact_lists VALIDATE CONSTRAINT contact_lists_consent_complete")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    # Hidden rows must raise, never masquerade as an empty downgrade target.
    op.execute("SET LOCAL row_security=off")
    op.execute("""DO $q96d$
    BEGIN
        LOCK TABLE public.contact_lists IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.contact_lists'::regclass
                   AND attname='consent_confirmed_at' AND NOT attisdropped) THEN
            IF EXISTS (SELECT 1 FROM public.contact_lists WHERE consent_confirmed_at IS NOT NULL) THEN
                RAISE EXCEPTION '0096 refuses to drop contact list permission confirmations; use code rollback or a reviewed forward migration';
            END IF;
        END IF;
    END $q96d$""")
    op.execute("ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS contact_lists_consent_complete")
    op.execute("""ALTER TABLE contact_lists
        DROP COLUMN IF EXISTS consent_confirmed_at,
        DROP COLUMN IF EXISTS consent_confirmed_by,
        DROP COLUMN IF EXISTS consent_basis,
        DROP COLUMN IF EXISTS consent_source,
        DROP COLUMN IF EXISTS consent_permission_date,
        DROP COLUMN IF EXISTS consent_statement_version""")
