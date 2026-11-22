"""0105 makes the contact list permission record all-or-nothing in the database.

No database here: the chain and the statements. Behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0105_list_consent_complete"
NAME = "contact_lists_consent_complete"


def _module():
    return importlib.import_module("Alembic.versions.0105_contact_list_consent_complete")


def _sql(monkeypatch, operation):
    statements = []
    monkeypatch.setattr(_module().op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(_module(), operation)()
    return statements


def _norm(text):
    return " ".join(text.split())


def test_0105_is_the_single_head_on_top_of_0104():
    assert SCRIPTS.get_heads() == [REVISION]
    assert SCRIPTS.get_revision(REVISION).down_revision == "0104_paypal_review_dismissed"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_the_constraint_name_is_the_one_0096_created():
    source = (BACKEND / "Alembic/versions/0096_contact_list_permission.py").read_text(encoding="utf-8")
    assert f"ADD CONSTRAINT {NAME}" in source


def test_upgrade_drops_by_name_adds_not_valid_and_validates_in_a_guarded_block(monkeypatch):
    s = _sql(monkeypatch, "upgrade")
    assert s[0] == "SET LOCAL lock_timeout='5s'"
    assert f"ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS {NAME}" in s
    add = next(x for x in s if x.startswith(f"ALTER TABLE contact_lists ADD CONSTRAINT {NAME}"))
    assert add.endswith("NOT VALID")
    for column in ("consent_basis", "consent_source", "consent_statement_version"):
        assert f"{column} IS NOT NULL" in add
    assert s.index(f"ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS {NAME}") < s.index(add)
    block = next(x for x in s if x.startswith("DO $q105$"))
    assert s.index(add) < s.index(block)
    assert f"VALIDATE CONSTRAINT {NAME}" in block
    assert "EXCEPTION WHEN check_violation THEN" in block
    assert "RAISE WARNING" in block and "count(*)" in block
    assert not any(x.startswith("ALTER TABLE") and "VALIDATE" in x for x in s)


def test_the_warning_carries_only_a_number(monkeypatch):
    block = next(x for x in _sql(monkeypatch, "upgrade") if x.startswith("DO $q105$"))
    warning = block[block.index("RAISE WARNING"):]
    assert "%" in warning and "incomplete" in warning
    assert "consent_source" not in warning


def test_downgrade_restores_the_0096_text_exactly(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "m0096", BACKEND / "Alembic/versions/0096_contact_list_permission.py")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    s = _sql(monkeypatch, "downgrade")
    assert s[0] == "SET LOCAL lock_timeout='5s'"
    assert f"ALTER TABLE contact_lists DROP CONSTRAINT IF EXISTS {NAME}" in s
    block = next(x for x in s if x.startswith("DO $q105d$"))
    assert _norm(old._CHECK) in block
    assert "IF NOT EXISTS" in block and block.rstrip().endswith("$q105d$")
    assert f"ALTER TABLE contact_lists VALIDATE CONSTRAINT {NAME}" in s
    assert "consent_basis IS NOT NULL" not in block
