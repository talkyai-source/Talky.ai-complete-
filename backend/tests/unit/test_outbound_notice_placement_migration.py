"""0106 adds the outbound notice placement column.

No database here: the chain and the statements. Behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0106_outbound_notice_placement"
CHECK = "tenant_recording_policy_outbound_notice_placement_check"


def _module():
    return importlib.import_module("Alembic.versions.0106_outbound_notice_placement")


def _sql(monkeypatch, operation):
    statements = []
    monkeypatch.setattr(_module().op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(_module(), operation)()
    return statements


def test_0106_sits_on_0105_and_0107_sits_on_it():
    assert SCRIPTS.get_heads() == ["0108_telephony_number_requests"]
    assert SCRIPTS.get_revision("0107_staff_suspension").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0105_list_consent_complete"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_adds_the_column_with_the_constant_default_then_the_guarded_check(monkeypatch):
    s = _sql(monkeypatch, "upgrade")
    assert s[0] == "SET LOCAL lock_timeout='5s'"
    assert s[1] == ("ALTER TABLE public.tenant_recording_policy ADD COLUMN IF NOT EXISTS "
                    "outbound_notice_placement TEXT NOT NULL DEFAULT 'own_sentence_first'")
    assert len(s) == 3 and s[2].startswith("DO $q106$")
    assert f"IF NOT EXISTS ( SELECT 1 FROM pg_catalog.pg_constraint WHERE conname = '{CHECK}'" in s[2]
    assert "CHECK (outbound_notice_placement IN ('own_sentence_first', 'in_opening'))" in s[2]


def test_upgrade_changes_no_row(monkeypatch):
    joined = " ".join(_sql(monkeypatch, "upgrade")).upper()
    for word in ("UPDATE ", "INSERT ", "DELETE ", "DROP ", "ENABLE ROW", "CREATE POLICY"):
        assert word not in joined


def test_downgrade_drops_the_constraint_then_the_column(monkeypatch):
    s = _sql(monkeypatch, "downgrade")
    assert s[1] == f"ALTER TABLE public.tenant_recording_policy DROP CONSTRAINT IF EXISTS {CHECK}"
    assert s[2] == ("ALTER TABLE public.tenant_recording_policy DROP COLUMN IF EXISTS "
                    "outbound_notice_placement")
