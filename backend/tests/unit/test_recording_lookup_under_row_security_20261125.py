"""The recording save's lookup of its call, under the database's row security.

Written by the independent reviewer of the fix of 25 November 2026 and adopted
into the suite. It runs the REAL _acquire_recording_connection /
acquire_with_tenant / get_db against a fake connection that applies the
canonical calls policy (migrations 0013 / 0077: bypass OR tenant_id =
current_tenant) to the lookup. The other recording tests replace the connection
helper, so they cannot fail the way the database does; this one can.

The fault it guards: an incoming call's recording was looked up on a connection
with no account set, the row was hidden, and the recording never left the
pending folder (two recordings, retried 55 times each, on 24 November 2026).
"""
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core import db as core_db
from app.domain.services import recording_service as rs
from app.domain.services import recording_spool as spool
from app.domain.services.telephony import recording as tr

TENANT = "20000000-0000-4000-8000-000000000004"
OTHER = "20000000-0000-4000-8000-000000000099"
SESSION = "a1b2c3d4-e5f6-47a8-8b9c-d0e1f2a3b4c5"
INTERNAL = "b1b2c3d4-e5f6-47a8-8b9c-d0e1f2a3b4c5"
PBX = "1795516403.2"


class RlsConn:
    def __init__(self, rows):
        self.rows = rows  # list of dicts: id, tenant_id, campaign_id, external_call_uuid
        self.guc = {}
        self.lookups = []  # (guc snapshot, args)

    @asynccontextmanager
    async def transaction(self, **_kw):
        saved = dict(self.guc)
        try:
            yield
        finally:
            self.guc = saved  # SET LOCAL ends with the transaction

    async def execute(self, sql, *args):
        m = re.match(r"SET LOCAL ([\w.]+) = '([^']*)'", sql)
        if m:
            self.guc[m.group(1)] = m.group(2)
            return "SET"
        m = re.match(r"SELECT set_config\('([\w.]+)', (\$1|'[^']*'), true\)", sql)
        if m:
            value = args[0] if m.group(2) == "$1" else m.group(2).strip("'")
            self.guc[m.group(1)] = value
            return "SELECT 1"
        raise AssertionError(f"unexpected execute: {sql}")

    async def fetchrow(self, sql, *args):
        assert sql.startswith("SELECT id, tenant_id, campaign_id FROM calls"), sql
        self.lookups.append((dict(self.guc), args))
        bypass = str(self.guc.get("app.bypass_rls", "")).lower() in {"on", "true"}
        cur = self.guc.get("app.current_tenant_id") or None
        visible = [
            r for r in self.rows
            if bypass or (cur is not None and str(r["tenant_id"]) == str(UUID(cur)))
        ]
        hits = [r for r in visible
                if r["external_call_uuid"] == args[0] and str(r["tenant_id"]) == str(args[1])]
        return hits[0] if hits else None


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, timeout=None):
        conn = self.conn

        class _Acq:
            def __await__(self_inner):
                async def _c():
                    return conn
                return _c().__await__()

            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *exc):
                return False

        return _Acq()

    async def release(self, conn):
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path / "spool"))
    row = {"id": UUID(INTERNAL), "tenant_id": UUID(TENANT), "campaign_id": None,
           "external_call_uuid": PBX}
    conn = RlsConn([row])
    pool = Pool(conn)
    saves = []

    class Svc:
        def __init__(self, _pool):
            pass

        async def save_and_link_outcome(self, **kw):
            saves.append(kw)
            return rs.SaveOutcome(rs.SAVE_SAVED, "rec-1")

    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
        is_initialized=True, db_pool=pool, db_client=SimpleNamespace(pool=pool)))
    monkeypatch.setattr(rs, "RecordingService", Svc)
    monkeypatch.setattr(core_db, "_pool", pool)

    async def gate_open(_pool):
        return True

    monkeypatch.setattr(
        "app.domain.services.telephony.modes.caller_first._live_inbound_recording_state", gate_open)
    tr._PROCESSING.clear()
    return SimpleNamespace(conn=conn, saves=saves)


def _entry(**kw):
    base = dict(
        key=SESSION, pbx_call_id=PBX, session_call_id=SESSION, tenant_id=TENANT,
        campaign_id=None, phone_hint="x", sample_rate=16000, duration_seconds=1.0,
        disclosure_state=None, callee_country=None, callee_country_known=False,
        inbound=True, strict_notice=False, awaiting=None, transfer_recording_name=None,
        transfer_part=None, created_at=spool.utc_now_iso(),
    )
    base.update(kw)
    e = spool.SpoolEntry(**base)
    spool.write_entry(e, b"RIFFwav")
    return e


async def test_retry_loop_inbound_entry_saved_under_rls(env):
    # Stuck-entry shape: written by old code, retried ~55 times.
    e = _entry(attempts=55, last_error="stub_insert_failed")
    assert await tr.process_spool_entry(e) == "saved"
    [(guc, args)] = env.conn.lookups
    assert guc.get("app.current_tenant_id") == TENANT
    assert "app.bypass_rls" not in guc
    assert args == (PBX, UUID(TENANT))
    assert env.saves[0]["call_id"] == INTERNAL and env.saves[0]["tenant_id"] == TENANT
    assert env.saves[0]["mark_failure"] is False
    assert spool.list_entries() == []


async def test_old_seam_get_db_without_context_hides_the_row(env):
    # The pre-commit connection, in a task with no tenant/bypass context.
    async with core_db.get_db() as conn:
        row = await conn.fetchrow(
            "SELECT id, tenant_id, campaign_id FROM calls WHERE external_call_uuid = $1 "
            "AND tenant_id = $2 ORDER BY created_at DESC LIMIT 1", PBX, UUID(TENANT))
    assert row is None


async def test_other_tenant_entry_cannot_bind(env):
    e = _entry(tenant_id=OTHER)
    assert await tr.process_spool_entry(e) == "retry"
    assert spool.read_entry(SESSION).last_error == "inbound_call_row_missing"
    assert env.saves == []
