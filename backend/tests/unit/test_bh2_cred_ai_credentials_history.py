"""bughunt billing-money-credentials-3 (proper fix) - tenant_ai_credentials keeps history.

tenant_ai_credentials carried UNIQUE NULLS NOT DISTINCT
(tenant_id, provider, credential_kind, status): at most ONE 'disabled' row per
credential. The second rotation or revoke of the same credential hit the
constraint (500) and left the key active; the endpoint worked around it by
DELETEing the previous disabled row, which destroyed the audit trail.

Migration 0067 replaces the constraint with a partial unique index on the ACTIVE
row only and the endpoint stops deleting. These tests need no database:

  * the endpoint issues no DELETE (statement-level behaviour with a recording
    connection),
  * the migration is wired into the chain, targets the constraint the baseline
    defines and restores that exact definition on downgrade,
  * no application SQL depends on the dropped constraint or infers a conflict
    target the partial index cannot satisfy.

The behaviour on a real Postgres is in test_bh2_cred_ai_credentials_history_pg.py.
"""
from __future__ import annotations

import importlib.util
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import tenant_ai_credentials as ep

_BACKEND = Path(__file__).resolve().parents[2]
_VERSIONS = _BACKEND / "Alembic" / "versions"
_BASELINE = _BACKEND / "database" / "schema" / "baseline_2026-06-02.sql"
_APP = _BACKEND / "app"

TENANT = "11111111-1111-4111-8111-111111111111"
CREDENTIAL_ID = "33333333-3333-4333-8333-333333333333"


def _migration():
    path = _VERSIONS / "0067_ai_credentials_history.py"
    spec = importlib.util.spec_from_file_location("m0067_ai_credentials_history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# endpoint: rotation and revocation never delete a disabled row
# --------------------------------------------------------------------------


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Recorder:
    """Records every statement; fetchrow answers with a canned row."""

    def __init__(self, fetchrow_result):
        self.statements: list[tuple[str, tuple]] = []
        self._fetchrow_result = fetchrow_result

    def transaction(self):
        return _Txn()

    async def execute(self, query, *args):
        self.statements.append((" ".join(query.split()), args))
        return "OK"

    async def fetchrow(self, query, *args):
        self.statements.append((" ".join(query.split()), args))
        return self._fetchrow_result


@pytest.fixture
def recorder(monkeypatch):
    holder = {}

    @asynccontextmanager
    async def fake_acquire(pool, tenant_id, **kwargs):
        yield holder["conn"]

    class _Enc:
        def encrypt(self, plaintext):
            return f"ENC:{plaintext}"

    monkeypatch.setattr(ep, "acquire_with_tenant", fake_acquire)
    monkeypatch.setattr(ep, "get_encryption_service", lambda: _Enc())
    return holder


def _user():
    return SimpleNamespace(tenant_id=TENANT)


def _verbs(conn):
    return [sql.split()[0].upper() for sql, _ in conn.statements]


@pytest.mark.asyncio
async def test_rotation_disables_the_active_row_then_inserts_and_never_deletes(recorder):
    inserted = {
        "id": CREDENTIAL_ID,
        "tenant_id": TENANT,
        "provider": "groq",
        "credential_kind": "api_key",
        "last4": "1111",
        "label": None,
        "status": "active",
        "created_at": None,
        "last_used_at": None,
    }
    recorder["conn"] = conn = _Recorder(inserted)

    await ep.create_credential(
        ep.CredentialCreateRequest(provider="groq", api_key="key-aaaa-1111"),
        current_user=_user(),
        db_pool=object(),
    )

    # Was ["DELETE", "UPDATE", "INSERT"]: the DELETE erased the previous
    # disabled version on every rotation.
    assert _verbs(conn) == ["UPDATE", "INSERT"], conn.statements
    update_sql, update_args = conn.statements[0]
    assert "tenant_id = $1" in update_sql and "status = 'active'" in update_sql
    assert update_args[0] == TENANT


@pytest.mark.asyncio
async def test_revoke_only_flips_the_status_and_never_deletes(recorder):
    target = {"provider": "groq", "credential_kind": "api_key", "status": "active"}
    recorder["conn"] = conn = _Recorder(target)

    await ep.disable_credential(CREDENTIAL_ID, current_user=_user(), db_pool=object())

    assert _verbs(conn) == ["SELECT", "UPDATE"], conn.statements
    update_sql, update_args = conn.statements[1]
    assert "SET status = 'disabled'" in update_sql
    assert "tenant_id = $1" in update_sql and "id = $2" in update_sql
    assert update_args == (TENANT, CREDENTIAL_ID)


@pytest.mark.asyncio
async def test_revoking_an_already_disabled_row_writes_nothing(recorder):
    target = {"provider": "groq", "credential_kind": "api_key", "status": "disabled"}
    recorder["conn"] = conn = _Recorder(target)

    await ep.disable_credential(CREDENTIAL_ID, current_user=_user(), db_pool=object())

    assert _verbs(conn) == ["SELECT"]


# --------------------------------------------------------------------------
# migration 0067: chain, target constraint, restored definition
# --------------------------------------------------------------------------


def test_migration_is_wired_into_the_chain_and_fits_alembic_version():
    module = _migration()
    assert module.revision == "0067_ai_credentials_history"
    assert module.down_revision == "0066_crm_skip_test_calls"
    assert len(module.revision) <= 32


def test_migration_replaces_exactly_the_constraint_the_baseline_defines():
    baseline = " ".join(_BASELINE.read_text(encoding="utf-8").split())
    old_definition = (
        "CONSTRAINT tenant_ai_credentials_unique_active "
        "UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)"
    )
    assert old_definition in baseline, "baseline no longer defines the constraint 0067 drops"

    module = _migration()
    drop = " ".join(module.DROP_OLD_CONSTRAINT_SQL.split())
    assert "DROP CONSTRAINT IF EXISTS tenant_ai_credentials_unique_active" in drop

    index = " ".join(module.CREATE_ACTIVE_INDEX_SQL.split())
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_ai_credentials_active" in index
    assert "(tenant_id, provider, credential_kind) WHERE status = 'active'" in index

    # The downgrade must bring back the very definition the baseline has.
    restore = " ".join(module.RESTORE_OLD_CONSTRAINT_SQL.split())
    assert (
        "ADD CONSTRAINT tenant_ai_credentials_unique_active "
        "UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)"
    ) in restore


def test_the_new_index_is_built_before_the_old_constraint_is_dropped():
    module = _migration()
    statements = list(module.UPGRADE_STATEMENTS)
    create_at = next(i for i, s in enumerate(statements) if "CREATE UNIQUE INDEX" in s)
    drop_at = next(i for i, s in enumerate(statements) if "DROP CONSTRAINT" in s)
    assert create_at < drop_at, "uniqueness of the active key must never go unenforced"


def test_downgrade_checks_for_history_before_it_changes_anything():
    module = _migration()
    statements = list(module.DOWNGRADE_STATEMENTS)
    assert "RAISE EXCEPTION" in statements[0]
    assert "Cannot downgrade 0067_ai_credentials_history" in statements[0]
    assert "ADD CONSTRAINT" in statements[1]
    assert statements[-1].lstrip().upper().startswith("DROP INDEX IF EXISTS")


# --------------------------------------------------------------------------
# application SQL must not depend on what 0067 removed
# --------------------------------------------------------------------------


def test_no_application_sql_names_the_dropped_constraint():
    offenders = [
        str(path.relative_to(_BACKEND))
        for path in _APP.rglob("*.py")
        if "tenant_ai_credentials_unique_active" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []


def test_any_on_conflict_on_tenant_ai_credentials_repeats_the_index_predicate():
    """Postgres can infer a PARTIAL unique index only when ON CONFLICT repeats
    its predicate. There is none today (grep of app/); this keeps it that way."""
    insert = re.compile(r"INSERT\s+INTO\s+tenant_ai_credentials\b(.*?)(?:\"\"\"|\Z)", re.I | re.S)
    upsert = re.compile(r"table\(\s*[\"']tenant_ai_credentials[\"']\s*\)\s*\.upsert")
    inspected = 0
    offenders = []
    for path in _APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if upsert.search(text):
            offenders.append(f"{path.relative_to(_BACKEND)}: adapter upsert cannot name the partial index")
        for match in insert.finditer(text):
            inspected += 1
            statement = " ".join(match.group(1).split())
            if "ON CONFLICT" not in statement.upper():
                continue
            clause = statement[statement.upper().index("ON CONFLICT"):]
            if "(tenant_id, provider, credential_kind)" not in clause or "WHERE status = 'active'" not in clause:
                offenders.append(f"{path.relative_to(_BACKEND)}: ON CONFLICT without the index predicate")
    assert inspected >= 1, "the endpoint's INSERT was not found: this guard has gone vacuous"
    assert offenders == []
