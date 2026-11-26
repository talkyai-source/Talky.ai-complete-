"""Operator scripts must not report a blind read as success.

The campaign TTS backfill opens its own raw connection. Under row security that
connection reads 0 campaigns, and "0 campaigns need fixing, dry run" would make
an operator believe nothing needs repair. 0 rows now exits 2 unless
``--allow-empty`` is given.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backfill_campaign_tts_provider.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_tts_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return None


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.writes = []

    async def fetch(self, sql, *a, **k):
        if "FROM campaigns" in sql:
            return self._rows
        return []

    async def execute(self, *a, **k):
        self.writes.append(a)
        return "UPDATE 1"

    def transaction(self):
        return _Tx()

    async def close(self):
        pass


def _args(**kw):
    base = dict(apply=False, tenant=None, limit=100, allow_empty=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run(monkeypatch, rows, args):
    import asyncio

    mod = _load()
    conn = _Conn(rows)

    async def _connect(*a, **k):
        return conn

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=_connect))
    monkeypatch.setattr(mod, "dsn", lambda: "postgresql://dummy")
    return asyncio.run(mod.run(args)), conn


def test_tts_backfill_zero_rows_exits_2_with_message(monkeypatch, capsys):
    code, conn = _run(monkeypatch, [], _args())
    out = capsys.readouterr()
    assert code == 2
    assert "0 rows" in out.err
    assert "row security" in out.err
    assert "nothing was changed" in out.err.lower()
    assert conn.writes == []


def test_tts_backfill_zero_rows_with_apply_writes_nothing(monkeypatch):
    code, conn = _run(monkeypatch, [], _args(apply=True))
    assert code == 2
    assert conn.writes == []


def test_tts_backfill_zero_rows_allowed_with_flag(monkeypatch, capsys):
    code, conn = _run(monkeypatch, [], _args(allow_empty=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "NULL tts_provider: 0" in out
    assert "DRY RUN" in out
    assert conn.writes == []


def test_tts_backfill_rows_present_unchanged_behaviour(monkeypatch, capsys):
    rows = [{"id": "c1", "tenant_id": "t1", "voice_id": "aura-asteria-en"}]
    code, _ = _run(monkeypatch, rows, _args())
    out = capsys.readouterr().out
    assert code == 0
    assert "NULL tts_provider: 1" in out
    assert "-> deepgram  [voice_shape]" in out
    assert "DRY RUN" in out


def test_tts_backfill_main_accepts_allow_empty(monkeypatch):
    mod = _load()
    seen = {}

    async def _fake_run(args):
        seen["allow_empty"] = args.allow_empty
        return 0

    monkeypatch.setattr(mod, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["x", "--allow-empty"])
    assert mod.main() == 0
    assert seen["allow_empty"] is True


# --- a verify script whose checks FAIL must not exit 0 ---------------------------------

def _load_rollback_check():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_prompt_rollback.py"
    before = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("verify_prompt_rollback_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # must not connect to anything at import
    finally:
        sys.path[:] = before
    return mod


def test_the_rollback_check_does_not_run_at_import_and_fails_when_a_check_fails(capsys):
    mod = _load_rollback_check()
    assert mod.ok == []  # nothing ran at import
    assert mod.exit_status() == 1  # no check ran: that is not a pass
    mod.check("one", True)
    assert mod.exit_status() == 0
    mod.check("two", False, "0 versions")
    assert mod.exit_status() == 1
    assert "FAIL  two" in capsys.readouterr().out
