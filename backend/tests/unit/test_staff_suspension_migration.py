"""0107 adds the staff suspension mark to tenants.

No database here: the chain and the statements. Behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0107_staff_suspension"


def _module():
    return importlib.import_module("Alembic.versions.0107_staff_suspension")


def _sql(monkeypatch, operation):
    statements = []
    monkeypatch.setattr(_module().op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(_module(), operation)()
    return statements


def test_0107_sits_on_0106_and_0108_sits_on_it():
    assert SCRIPTS.get_heads() == ["0108_telephony_number_requests"]
    assert SCRIPTS.get_revision("0108_telephony_number_requests").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0106_outbound_notice_placement"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_adds_the_two_nullable_columns_in_order(monkeypatch):
    s = _sql(monkeypatch, "upgrade")
    assert s == [
        "SET LOCAL lock_timeout='5s'",
        "ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS staff_suspended_at TIMESTAMPTZ",
        "ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS staff_suspended_by UUID",
    ]


def test_upgrade_changes_no_row_and_adds_no_constraint(monkeypatch):
    joined = " ".join(_sql(monkeypatch, "upgrade")).upper()
    for word in ("UPDATE ", "INSERT ", "DELETE ", "DROP ", "CHECK", "CONSTRAINT", "DEFAULT", "NOT NULL",
                 "ENABLE ROW", "CREATE POLICY"):
        assert word not in joined


def test_downgrade_drops_both_columns(monkeypatch):
    assert _sql(monkeypatch, "downgrade") == [
        "SET LOCAL lock_timeout='5s'",
        "ALTER TABLE public.tenants DROP COLUMN IF EXISTS staff_suspended_by",
        "ALTER TABLE public.tenants DROP COLUMN IF EXISTS staff_suspended_at",
    ]
