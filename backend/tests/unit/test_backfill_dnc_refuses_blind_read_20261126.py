"""The DNC backfill must not report a blind read as success.

On the production application role row security applies, so the script's own
connection reads 0 rows. "rows read: 0" with exit 0 would make an operator
believe every do-not-call number is already normalised. 0 rows now exits
non-zero unless ``--allow-empty`` is given.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backfill_dnc_normalized_numbers.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_dnc_under_test", _PATH)
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

    async def fetchval(self, *a, **k):
        return 1

    async def fetch(self, *a, **k):
        return self._rows

    async def execute(self, *a, **k):
        self.writes.append(a)

    def transaction(self):
        return _Tx()

    async def close(self):
        pass


def _args(**kw):
    base = dict(apply=False, tenant=None, delete_duplicates=False, limit=50, allow_empty=False)
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


def test_zero_rows_exits_nonzero_with_message(monkeypatch, capsys):
    code, conn = _run(monkeypatch, [], _args())
    out = capsys.readouterr()
    text = out.out + out.err
    assert code != 0
    assert "0 rows" in text
    assert "row security" in text
    assert "nothing was changed" in text.lower()
    assert conn.writes == []


def test_zero_rows_with_apply_still_refuses_and_writes_nothing(monkeypatch):
    code, conn = _run(monkeypatch, [], _args(apply=True))
    assert code != 0
    assert conn.writes == []


def test_zero_rows_allowed_with_flag(monkeypatch, capsys):
    code, _ = _run(monkeypatch, [], _args(allow_empty=True))
    assert code == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_rows_present_unchanged_behaviour(monkeypatch, capsys):
    rows = [{"id": "1", "tenant_id": None, "normalized_number": "+15551234567",
             "created_at": None, "phone_number": "+15551234567"}]
    code, _ = _run(monkeypatch, rows, _args())
    assert code == 0
    assert "rows read: 1" in capsys.readouterr().out


def test_main_accepts_allow_empty_flag(monkeypatch):
    mod = _load()
    seen = {}

    async def _fake_run(args):
        seen["allow_empty"] = args.allow_empty
        return 0

    monkeypatch.setattr(mod, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["x", "--allow-empty"])
    assert mod.main() == 0
    assert seen["allow_empty"] is True
