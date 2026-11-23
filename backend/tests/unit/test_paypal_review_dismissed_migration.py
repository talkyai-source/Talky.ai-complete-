"""0104 adds the `dismissed` PayPal receipt state and the PayPal review log.

No database here: the chain and the statements. Table behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0104_paypal_review_dismissed"
BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"


def _module():
    return importlib.import_module("Alembic.versions.0104_paypal_review_dismissed")


def _sql(monkeypatch, operation):
    statements = []
    monkeypatch.setattr(_module().op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(_module(), operation)()
    return statements


def test_0104_sits_on_top_of_0103_and_under_0105():
    assert SCRIPTS.get_heads() == ["0107_staff_suspension"]
    assert SCRIPTS.get_revision("0105_list_consent_complete").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0103_recording_notice_state"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_replaces_the_state_check_and_creates_the_log(monkeypatch):
    s = _sql(monkeypatch, "upgrade")
    assert s[0] == "SET LOCAL lock_timeout='5s'"
    assert "pg_get_constraintdef(oid) LIKE '%needs_review%'" in s[1] and "DROP CONSTRAINT %I" in s[1]
    assert s[2] == (
        "ALTER TABLE public.paypal_webhook_events ADD CONSTRAINT paypal_webhook_events_state_check "
        "CHECK (state IN ('received', 'completed', 'ignored', 'needs_review', 'failed', 'dismissed'))")
    create = s[3]
    assert create.startswith("CREATE TABLE IF NOT EXISTS public.paypal_webhook_review_log (")
    assert "event_id TEXT NOT NULL REFERENCES public.paypal_webhook_events(event_id)" in create
    assert "operator TEXT NOT NULL CHECK (length(operator) BETWEEN 1 AND 200)" in create
    assert "decision TEXT NOT NULL CHECK (decision IN ('dismiss'))" in create
    assert "reason TEXT NOT NULL CHECK (length(reason) BETWEEN 12 AND 2000)" in create
    assert "attempt_count_seen INTEGER NOT NULL CHECK (attempt_count_seen >= 0)" in create
    assert any(x.startswith("CREATE INDEX IF NOT EXISTS idx_paypal_webhook_review_log_event")
               and "(event_id, created_at)" in x for x in s)


def test_row_security_is_enabled_forced_then_platform_only_and_nothing_is_granted(monkeypatch):
    s = _sql(monkeypatch, "upgrade")
    enable = "ALTER TABLE public.paypal_webhook_review_log ENABLE ROW LEVEL SECURITY"
    force = "ALTER TABLE public.paypal_webhook_review_log FORCE ROW LEVEL SECURITY"
    policy = ("CREATE POLICY paypal_webhook_review_log_platform_only ON public.paypal_webhook_review_log "
              f"FOR ALL USING ({BYPASS}) WITH CHECK ({BYPASS})")
    assert s.index(enable) < s.index(force) < s.index(policy)
    assert not any("GRANT" in x for x in s)


def test_downgrade_order_reopens_rows_before_the_old_check_and_drops_the_log(monkeypatch):
    s = _sql(monkeypatch, "downgrade")
    update = "UPDATE public.paypal_webhook_events SET state = 'needs_review' WHERE state = 'dismissed'"
    old = ("ALTER TABLE public.paypal_webhook_events ADD CONSTRAINT paypal_webhook_events_state_check "
           "CHECK (state IN ('received', 'completed', 'ignored', 'needs_review', 'failed'))")
    drop_policy = ("DROP POLICY IF EXISTS paypal_webhook_review_log_platform_only "
                   "ON public.paypal_webhook_review_log")
    drop_table = "DROP TABLE IF EXISTS public.paypal_webhook_review_log"
    assert s.index(update) < next(i for i, x in enumerate(s) if "DROP CONSTRAINT %I" in x) < s.index(old)
    assert s.index(old) < s.index(drop_policy) < s.index(drop_table)
