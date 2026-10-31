"""Live-risk telephony fixes (audit 2026-10-20): telephony-1/2/3/6/7 and tenant-1."""
from __future__ import annotations

import importlib.util
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.test_trunk_delete_flow import delete, env  # noqa: F401  (env is a fixture)

BACKEND = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "trunk_live_status_updater_lr", BACKEND / "scripts" / "trunk_live_status_updater.py"
)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


# ---------------------------------------------------------------- telephony-1
def _row(**kw):
    row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "trunk_name": "tenant-byo",
        "is_active": False,
        "metadata": {},
        "updated_at": datetime.now(timezone.utc),
    }
    row.update(kw)
    return row


def test_freshly_updated_inactive_row_keeps_its_file(tmp_path, monkeypatch):
    """An activation still in its transaction looks inactive to the updater."""
    monkeypatch.setenv("TELEPHONY_PJSIP_CONF_DIR", str(tmp_path))
    row = _row()
    target = tmp_path / f"trunk-{row['id']}.conf"
    target.write_text("cfg", encoding="utf-8")
    calls = []
    monkeypatch.setattr(updater, "_asterisk_cli", lambda c: calls.append(c) or ("", True))
    assert updater.reconcile_inactive_config_files([row], {f"trunk-{row['id']}"}) == 0
    assert target.exists()
    assert calls == []


def test_orphan_file_cleaned_even_though_updater_bumps_updated_at(tmp_path, monkeypatch):
    """The updater's own UPDATE fires the updated_at trigger on every inactive
    row each pass, so updated_at must not be the freshness signal: an old
    orphan file must still be removed when updated_at is 'now'."""
    import os

    monkeypatch.setenv("TELEPHONY_PJSIP_CONF_DIR", str(tmp_path))
    row = _row(updated_at=datetime.now(timezone.utc))
    target = tmp_path / f"trunk-{row['id']}.conf"
    target.write_text("cfg", encoding="utf-8")
    old = datetime.now().timestamp() - 3600
    os.utime(target, (old, old))
    monkeypatch.setattr(updater, "_asterisk_cli", lambda c: ("", True))
    assert updater.reconcile_inactive_config_files([row], set()) == 1
    assert not target.exists()


# ---------------------------------------------------------------- telephony-3
@pytest.mark.asyncio
async def test_failed_delete_db_work_restores_removed_pjsip_file(env, monkeypatch):  # noqa: F811
    from app.api.v1.endpoints.telephony_sip import trunks

    trunk = env.db.add_trunk(active=True)
    restore = AsyncMock()
    monkeypatch.setattr(trunks, "_restore_trunk_config_file", restore)
    env.stored.side_effect = RuntimeError("idempotency store down")
    with pytest.raises(RuntimeError):
        await delete(trunk)
    env.remove.assert_awaited_once()
    restore.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_delete_of_inactive_line_does_not_write_a_file(env, monkeypatch):  # noqa: F811
    from app.api.v1.endpoints.telephony_sip import trunks

    trunk = env.db.add_trunk(active=False)
    restore = AsyncMock()
    monkeypatch.setattr(trunks, "_restore_trunk_config_file", restore)
    env.stored.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await delete(trunk)
    restore.assert_not_awaited()


# ---------------------------------------------------------------- telephony-2
@pytest.mark.asyncio
async def test_failed_outbound_stasis_setup_hangs_up_the_channel():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter._alloc_rtp_port = AsyncMock(return_value=31000)
    adapter._release_rtp_port = AsyncMock()
    adapter.hangup_confirmed = AsyncMock(return_value=True)

    async def ari(method, path, **kw):
        if method == "POST" and path == "/bridges":
            raise TimeoutError("ARI busy")
        return {}

    adapter._ari = ari
    await adapter._on_outbound_stasis_start("chan-1")
    adapter.hangup_confirmed.assert_awaited_once()
    assert adapter.hangup_confirmed.await_args.args[0] == "chan-1"


@pytest.mark.asyncio
async def test_rtp_port_exhaustion_hangs_up_the_channel():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter._alloc_rtp_port = AsyncMock(side_effect=RuntimeError("no free RTP port available"))
    adapter._release_rtp_port = AsyncMock()
    adapter.hangup_confirmed = AsyncMock(return_value=True)
    adapter._ari = AsyncMock(return_value={})
    await adapter._on_outbound_stasis_start("chan-2")
    adapter.hangup_confirmed.assert_awaited_once()
    adapter._release_rtp_port.assert_not_awaited()


# ---------------------------------------------------------------- telephony-7
@pytest.mark.asyncio
async def test_ari_handler_failure_is_logged_above_debug(caplog):
    from app.infrastructure.telephony import asterisk_adapter as mod

    adapter = mod.AsteriskAdapter()
    adapter._handle_ari_event = AsyncMock(side_effect=KeyError("channel"))
    with caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        await adapter._dispatch_ari_text('{"type": "StasisStart", "channel": {"id": "c1"}}')
        await adapter._dispatch_ari_text("{not json")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("StasisStart" in r.getMessage() for r in errors)


# ---------------------------------------------------------------- telephony-6
@pytest.mark.asyncio
async def test_lead_context_query_is_tenant_scoped(monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as tb
    from app.core import db_utils

    seen = {}

    class Conn:
        async def fetchrow(self, sql, *args):
            seen["sql"], seen["args"] = " ".join(sql.split()), args
            return None

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    tenant = "33333333-3333-3333-3333-333333333333"
    await tb._load_agent_lead_context("11111111-1111-1111-1111-111111111111", tenant)
    assert "tenant_id = $2" in seen["sql"]
    assert seen["args"][1] == tenant


# ------------------------------------------------------------------ tenant-1
class _CM:
    def __init__(self, v):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *a):
        return False


def _db_returning(row, seen):
    conn = MagicMock()
    conn.execute = AsyncMock()

    async def fetchrow(sql, *args):
        seen["sql"] = " ".join(sql.split())
        return row

    conn.fetchrow = fetchrow
    conn.transaction = MagicMock(return_value=_CM(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_CM(conn))
    return SimpleNamespace(pool=pool)


@pytest.mark.asyncio
async def test_bind_orders_newest_first():
    from app.services.scripts.call_transcript_persister import bind_telephony_call

    seen = {}
    row = {"id": "a", "tenant_id": "t1", "campaign_id": "c", "lead_id": "l",
           "phone_number": "+1", "tenant_count": 1}
    vs = SimpleNamespace(call_id="v", call_session=None)
    b = await bind_telephony_call(voice_session=vs, pbx_channel_id="chan", db_client=_db_returning(row, seen))
    assert b is not None
    assert "ORDER BY" in seen["sql"].upper()


@pytest.mark.asyncio
async def test_bind_refuses_when_channel_id_spans_tenants():
    from app.services.scripts.call_transcript_persister import bind_telephony_call

    seen = {}
    row = {"id": "a", "tenant_id": "t1", "campaign_id": "c", "lead_id": "l",
           "phone_number": "+1", "tenant_count": 2}
    vs = SimpleNamespace(call_id="v", call_session=None)
    b = await bind_telephony_call(voice_session=vs, pbx_channel_id="chan", db_client=_db_returning(row, seen))
    assert b is None
    assert not hasattr(vs, "_dialer_tenant_id")
