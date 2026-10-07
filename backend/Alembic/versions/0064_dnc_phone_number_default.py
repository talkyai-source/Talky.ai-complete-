"""Keep do-not-call writes working for code that does not send phone_number.

0061 added ``dnc_entries.phone_number`` as a NOT NULL mirror of
``normalized_number`` with no default. The release code fills it, but the code
production ran before this release (ea2b83a6) inserts only
``normalized_number``. Without this trigger, switching the application back
after 0061 has run would make every opt-out INSERT fail on NOT NULL: a caller's
"do not call me" would silently not be recorded.

The trigger fills ``phone_number`` from ``normalized_number`` whenever a writer
leaves it NULL. Postgres checks NOT NULL after BEFORE ROW triggers, so both the
release code and the previous code keep recording opt-outs. Writers that do
send ``phone_number`` are untouched.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0064_dnc_phone_number_default"
down_revision: Union[str, None] = "0063_release_history_merge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.dnc_entries_fill_phone_number()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.phone_number IS NULL THEN
        NEW.phone_number := NEW.normalized_number;
    END IF;
    RETURN NEW;
END
$function$
"""

TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_dnc_entries_fill_phone_number ON public.dnc_entries;
CREATE TRIGGER trg_dnc_entries_fill_phone_number
    BEFORE INSERT OR UPDATE OF phone_number, normalized_number ON public.dnc_entries
    FOR EACH ROW EXECUTE FUNCTION public.dnc_entries_fill_phone_number();
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(FUNCTION_SQL)
    op.execute(TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_dnc_entries_fill_phone_number ON public.dnc_entries")
    op.execute("DROP FUNCTION IF EXISTS public.dnc_entries_fill_phone_number()")
