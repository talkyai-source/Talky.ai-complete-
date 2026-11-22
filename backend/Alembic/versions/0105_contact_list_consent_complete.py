"""Make the contact list permission record all-or-nothing in the database too.

0096 meant: either every consent column is NULL, or a confirmation time exists
together with a known basis, a source of 5 to 500 characters, a permission date
not after the confirmation, and a statement version of 1 to 40 characters.
A CHECK passes when its expression is NULL, and `consent_basis IN (...)`,
`char_length(btrim(consent_source)) BETWEEN ...` and
`char_length(consent_statement_version) BETWEEN ...` are NULL when the column is
NULL, so 0096 accepted a confirmation time with no basis, no source or no
statement version. The call check reads only `consent_confirmed_at`, so the
database must never hold a confirmation time without its statement. This
migration replaces the constraint with the same rule plus explicit
`IS NOT NULL` for those three columns.

Why it is done this way: the new constraint is added NOT VALID first, so new and
changed rows are checked from that moment whatever the table already holds; it
is then validated in its own guarded block. (Both run in this migration's one
transaction, so the table stays locked for the scan; it holds one row per
import and the scan takes milliseconds.) If an existing row breaks the rule, a
failing migration would undo a whole release, so the block catches
check_violation, raises a WARNING with only the number of incomplete rows, and
leaves the constraint enforced for new writes but not validated.
If that WARNING appears: find the rows (confirmation time set, basis, source or
statement version NULL), have the customer re-confirm or clear the whole
statement on those lists, then run
`ALTER TABLE contact_lists VALIDATE CONSTRAINT contact_lists_consent_complete`.

The downgrade restores the 0096 constraint text.
"""
from alembic import op

revision = "0105_list_consent_complete"
down_revision = "0104_paypal_review_dismissed"
branch_labels = None
depends_on = None

_BASES = "'express_consent','existing_customer','made_an_enquiry','other_documented','pre_existing_unverified'"
_RULE = f"""(consent_confirmed_at IS NULL AND consent_confirmed_by IS NULL
                AND consent_basis IS NULL AND consent_source IS NULL
                AND consent_permission_date IS NULL AND consent_statement_version IS NULL)
            OR (consent_confirmed_at IS NOT NULL
                AND consent_basis IS NOT NULL
                AND consent_basis IN ({_BASES})
                AND consent_source IS NOT NULL
                AND char_length(btrim(consent_source)) BETWEEN 5 AND 500
                AND consent_permission_date IS NOT NULL
                AND consent_permission_date <= (consent_confirmed_at AT TIME ZONE 'UTC')::date
                AND consent_statement_version IS NOT NULL
                AND char_length(consent_statement_version) BETWEEN 1 AND 40)"""
_NEW_CHECK = f"CHECK ({_RULE})"
# The 0096 text, copied from 0096_contact_list_permission.py.
_OLD_CHECK = f"""CHECK (
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
    op.execute("ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS contact_lists_consent_complete")
    op.execute(f"""ALTER TABLE contact_lists ADD CONSTRAINT contact_lists_consent_complete
        {_NEW_CHECK} NOT VALID""")
    op.execute(f"""DO $q105$
    DECLARE incomplete bigint;
    BEGIN
        BEGIN
            ALTER TABLE contact_lists VALIDATE CONSTRAINT contact_lists_consent_complete;
        EXCEPTION WHEN check_violation THEN
            SELECT count(*) INTO incomplete FROM contact_lists
            WHERE NOT COALESCE(({_RULE}), false);
            RAISE WARNING '0105: % contact list(s) hold an incomplete permission confirmation; contact_lists_consent_complete is enforced for new writes but left NOT VALIDATED', incomplete;
        END;
    END $q105$""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS contact_lists_consent_complete")
    op.execute(f"""DO $q105d$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conrelid = 'public.contact_lists'::regclass
              AND conname = 'contact_lists_consent_complete'
        ) THEN
            ALTER TABLE contact_lists ADD CONSTRAINT contact_lists_consent_complete
            {_OLD_CHECK} NOT VALID;
        END IF;
    END $q105d$""")
    op.execute("ALTER TABLE contact_lists VALIDATE CONSTRAINT contact_lists_consent_complete")
