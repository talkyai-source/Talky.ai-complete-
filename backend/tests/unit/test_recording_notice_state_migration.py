"""0103 adds notice_state and transfer_part to recordings_s3.

No database here: the chain and the statements. Behaviour needs PostgreSQL (CI).
"""
import importlib
from pathlib import Path

from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = ScriptDirectory(str(BACKEND / "Alembic"))
REVISION = "0103_recording_notice_state"


def _module():
    return importlib.import_module("Alembic.versions.0103_recording_notice_state")


def _run(monkeypatch, operation):
    migration = _module()
    statements = []
    monkeypatch.setattr(migration.op, "execute", lambda v, s=statements: s.append(" ".join(str(v).split())))
    getattr(migration, operation)()
    return statements


def test_0103_sits_on_top_of_0102_and_under_0104():
    assert SCRIPTS.get_heads() == ["0107_staff_suspension"]
    assert SCRIPTS.get_revision("0104_paypal_review_dismissed").down_revision == REVISION
    assert SCRIPTS.get_revision(REVISION).down_revision == "0102_paypal_subscriptions"
    assert len(REVISION) <= 32 and _module().revision == REVISION


def test_upgrade_adds_two_nullable_columns_without_default_or_backfill(monkeypatch):
    statements = _run(monkeypatch, "upgrade")
    assert "ALTER TABLE public.recordings_s3 ADD COLUMN IF NOT EXISTS notice_state TEXT" in statements
    assert "ALTER TABLE public.recordings_s3 ADD COLUMN IF NOT EXISTS transfer_part TEXT" in statements
    joined = " ".join(statements)
    assert "NOT NULL" not in joined.replace("IS NOT NULL", "").replace("IF NOT EXISTS", "")
    assert " DEFAULT " not in joined
    assert not any(s.startswith("UPDATE") for s in statements)


def test_upgrade_checks_the_allowed_values(monkeypatch):
    joined = " ".join(_run(monkeypatch, "upgrade"))
    assert "CHECK (notice_state IS NULL OR notice_state IN ('delivered', 'not_required', 'unconfirmed'))" in joined
    assert "CHECK (transfer_part IS NULL OR transfer_part IN ('included', 'missing'))" in joined


def test_downgrade_drops_constraints_then_columns(monkeypatch):
    statements = _run(monkeypatch, "downgrade")[1:]
    assert statements == [
        "ALTER TABLE public.recordings_s3 DROP CONSTRAINT IF EXISTS recordings_s3_transfer_part_check",
        "ALTER TABLE public.recordings_s3 DROP CONSTRAINT IF EXISTS recordings_s3_notice_state_check",
        "ALTER TABLE public.recordings_s3 DROP COLUMN IF EXISTS transfer_part",
        "ALTER TABLE public.recordings_s3 DROP COLUMN IF EXISTS notice_state",
    ]
