"""Let an account choose where the recording notice is said on outgoing calls.

One column on `tenant_recording_policy`: `outbound_notice_placement`, either
`own_sentence_first` (the account's own notice is spoken first as its own
sentence, which is what every account does today) or `in_opening` (the opening
sentence carries the recording words and the account's own sentence is not
spoken on outgoing calls). The default is `own_sentence_first`, so every
existing row keeps today's behaviour. A constant default is a metadata-only
change on PostgreSQL 11 and later: no table rewrite, no backfill.

Code that does not know the column ignores it (the old INSERT takes the
default), so rolling the code back without this downgrade is safe.
"""
from alembic import op

revision = "0106_outbound_notice_placement"
down_revision = "0105_list_consent_complete"
branch_labels = None
depends_on = None

_CHECK = "tenant_recording_policy_outbound_notice_placement_check"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("""ALTER TABLE public.tenant_recording_policy
        ADD COLUMN IF NOT EXISTS outbound_notice_placement TEXT NOT NULL DEFAULT 'own_sentence_first'""")
    op.execute(f"""DO $q106$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conname = '{_CHECK}'
              AND conrelid = 'public.tenant_recording_policy'::regclass
        ) THEN
            ALTER TABLE public.tenant_recording_policy ADD CONSTRAINT {_CHECK}
            CHECK (outbound_notice_placement IN ('own_sentence_first', 'in_opening'));
        END IF;
    END $q106$""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"ALTER TABLE public.tenant_recording_policy DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute("ALTER TABLE public.tenant_recording_policy DROP COLUMN IF EXISTS outbound_notice_placement")
