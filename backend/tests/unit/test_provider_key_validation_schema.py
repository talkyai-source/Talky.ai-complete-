"""Migration wiring/DDL guards; actual PostgreSQL proof is a separate gate."""
import importlib.util
from pathlib import Path

import pytest


REVISION = Path(__file__).resolve().parents[2] / "Alembic/versions/0089_provider_key_validation.py"


@pytest.fixture
def revision(monkeypatch):
    assert REVISION.is_file(), "F3-06 is missing durable provider validation metadata"
    spec = importlib.util.spec_from_file_location("qa_provider_key_validation_0089", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(module.op, "execute", lambda sql: statements.append(str(sql)))
    return module, statements


def test_validation_revision_is_linear_and_fits_version_column(revision):
    module, _ = revision
    assert module.revision == "0089_provider_key_validation"
    assert module.down_revision == "0088_qa_data_constraints"
    assert len(module.revision) <= 32


def test_upgrade_keeps_existing_keys_unchecked_and_adds_only_nullable_metadata(revision):
    module, statements = revision
    module.upgrade()
    sql = " ".join(statements)
    assert "validation_status" in sql and "last_checked_at" in sql
    assert "NOT VALID" in sql
    assert "'valid'" in sql and "'invalid'" in sql
    assert "DEFAULT 'valid'" not in sql
    assert "UPDATE tenant_ai_credentials" not in sql and "DELETE FROM" not in sql
    assert "IF NOT EXISTS" in sql and "to_regclass" in sql
    assert "lock_timeout" in sql


def test_downgrade_only_removes_metadata_owned_by_this_revision(revision):
    module, statements = revision
    module.downgrade()
    sql = " ".join(statements)
    assert "validation_status" in sql and "last_checked_at" in sql
    assert "col_description" in sql or "obj_description" in sql
    assert "DROP COLUMN" in sql and "DROP TABLE" not in sql
    assert "encrypted_key" not in sql
