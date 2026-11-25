"""The discrepancy report must run on the production role (not superuser, no BYPASSRLS).

`SET LOCAL row_security = off` makes PostgreSQL RAISE for any policy-covered query on such a
role, so the report never completed. The row policies (migrations 0013/0077) accept
`COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)`; 'on' casts to true.
"""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "report_recording_discrepancies.py"
TENANT = UUID("00000000-0000-0000-0000-000000000091")
ACCEPTED = {"on", "true", "t", "yes", "1"}  # what ::boolean accepts as true


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("recording_discrepancy_app_role", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RoleConn:
    """Behaves like the production role: raises on row_security=off, and the row-security
    policy shows rows only while app.bypass_rls was set to a value ::boolean accepts."""

    def __init__(self, rows, *, honour_bypass=True):
        self.rows = rows
        self.statements = []
        self.bypass = False
        self.honour_bypass = honour_bypass

    @asynccontextmanager
    async def transaction(self, **kwargs):
        yield

    async def execute(self, query, *args):
        flat = " ".join(query.split())
        self.statements.append(flat)
        if "row_security" in flat.lower():
            raise RuntimeError("query would be affected by row-level security policy")
        if "app.bypass_rls" in flat:
            self.bypass = flat.split("=")[-1].strip().strip("'").lower() in ACCEPTED
        return "SET"

    async def fetchval(self, query, *args):
        return None  # no legacy recordings table

    async def fetch(self, query, *args):
        flat = " ".join(query.split())
        if "FROM tenants" in flat:
            cursor, limit = args
            return [{"id": TENANT}] if cursor is None else []
        visible = self.bypass or not self.honour_bypass
        tenant, cursor, limit = args
        return [dict(r) for r in self.rows if visible and r["tenant_id"] == tenant
                and (cursor is None or r["id"] > cursor)][:limit]


def _row(path, number=1):
    return {"id": UUID(int=number), "tenant_id": TENANT, "call_id": UUID(int=number + 100),
            "s3_key": str(path), "s3_bucket": "local", "status": "uploaded"}


@pytest.mark.asyncio
async def test_no_statement_turns_row_security_off_and_the_bypass_value_is_accepted(script, tmp_path):
    conn = RoleConn([])
    await script.report_discrepancies(conn, tmp_path)
    assert not any("row_security" in s.lower() for s in conn.statements)
    assert "SET LOCAL app.bypass_rls = 'on'" in conn.statements


@pytest.mark.asyncio
async def test_the_report_completes_on_the_production_role_and_finds_a_real_discrepancy(script, tmp_path):
    present = tmp_path / "owned.wav"
    present.write_bytes(b"a")
    (tmp_path / "orphan.wav").write_bytes(b"b")
    conn = RoleConn([_row(present), _row(tmp_path / "gone.wav", 2)])
    result = await script.report_discrepancies(conn, tmp_path)
    assert result["complete"] is True
    assert result["files_without_rows"] == ["orphan.wav"]
    assert result["counts"]["rows_without_files"] == 1


@pytest.mark.asyncio
async def test_a_database_that_hides_every_row_is_incomplete_and_lists_no_orphans(script, tmp_path):
    (tmp_path / "a.wav").write_bytes(b"a")
    (tmp_path / "b.wav").write_bytes(b"b")
    conn = RoleConn([_row(tmp_path / "a.wav")], honour_bypass=True)
    conn.bypass = False

    async def execute(query, *args):  # a bypass the policy does not accept: nothing is visible
        conn.statements.append(" ".join(query.split()))
        return "SET"
    conn.execute = execute
    result = await script.report_discrepancies(conn, tmp_path)
    assert result["complete"] is False
    assert "recording_rows_not_visible" in result["issues"]
    assert result["files_without_rows"] == [] and result["counts"]["files_without_rows"] == 0


@pytest.mark.asyncio
async def test_an_empty_folder_with_no_rows_stays_complete(script, tmp_path):
    result = await script.report_discrepancies(RoleConn([]), tmp_path)
    assert result["complete"] is True and "recording_rows_not_visible" not in result["issues"]
