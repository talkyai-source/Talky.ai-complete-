"""The nightly recording check: skips the waiting folder and exits 3 only for a lost recording.

Exit codes: 0 clean, 4 only stray files, 2 could not complete, 3 a record has no file (wins over 2 and 4).
1 is never returned: Python uses it for a crash.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "report_recording_discrepancies.py"
UNIT = BACKEND / "systemd" / "talky-recording-discrepancy.service"
TENANT = UUID("00000000-0000-0000-0000-000000000092")


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("recording_check_alarm", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Conn:
    def __init__(self, rows):
        self.rows = rows

    @asynccontextmanager
    async def transaction(self, **kwargs):
        yield

    async def execute(self, query, *args):
        return "SET"

    async def fetchval(self, query, *args):
        return None

    async def close(self, timeout=None):
        return None

    async def fetch(self, query, *args):
        flat = " ".join(query.split())
        if "FROM tenants" in flat:
            return [{"id": TENANT}] if args[0] is None else []
        tenant, cursor, limit = args
        return [dict(r) for r in self.rows if r["tenant_id"] == tenant
                and (cursor is None or r["id"] > cursor)][:limit]


def _row(path, number=1):
    return {"id": UUID(int=number), "tenant_id": TENANT, "call_id": UUID(int=number + 100),
            "s3_key": str(path), "s3_bucket": "local", "status": "uploaded"}


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _owned(root):
    path = root / "owned.wav"  # one owned file: rows are visible, so strays are classified
    _write(path)
    return path


def _spool(directory):
    _write(directory / "waiting.wav")
    _write(directory / "waiting.json")
    _write(directory / "live" / "call-1" / "safety.wav")


@pytest.fixture(autouse=True)
def _no_spool_env(monkeypatch):
    monkeypatch.delenv("RECORDING_SPOOL_DIR", raising=False)


@pytest.mark.asyncio
async def test_default_waiting_folder_is_skipped_and_not_listed(script, tmp_path):
    kept = tmp_path / "owned.wav"
    _write(kept)
    conn = Conn([_row(kept)])
    without = await script.report_discrepancies(conn, tmp_path)
    _spool(tmp_path / ".pending")
    result = await script.report_discrepancies(conn, tmp_path)
    assert result["waiting_folder_skipped"] is True
    assert result["counts"]["waiting_files_skipped"] == 3
    assert result["files_without_rows"] == [] and result["counts"]["files"] == 1
    assert ".pending" not in json.dumps(result["files_without_rows"])
    for key in ("waiting_folder_skipped",):
        without.pop(key, None); result.pop(key)
    without["counts"].pop("waiting_files_skipped", None); result["counts"].pop("waiting_files_skipped")
    assert result == without


@pytest.mark.asyncio
async def test_no_waiting_folder_means_not_skipped(script, tmp_path):
    result = await script.report_discrepancies(Conn([]), tmp_path)
    assert result["waiting_folder_skipped"] is False
    assert result["counts"]["waiting_files_skipped"] == 0


@pytest.mark.asyncio
async def test_spool_env_inside_root_is_skipped_and_pending_is_then_a_normal_folder(script, tmp_path, monkeypatch):
    other = tmp_path / "spool-elsewhere"
    _spool(other)
    _write(tmp_path / ".pending" / "stray.wav")
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(other))
    result = await script.report_discrepancies(Conn([_row(_owned(tmp_path))]), tmp_path)
    assert result["waiting_folder_skipped"] is True
    assert result["counts"]["waiting_files_skipped"] == 3
    # the application then does not use .pending as its spool, so it is an ordinary folder
    assert result["files_without_rows"] == [".pending/stray.wav"]


@pytest.mark.asyncio
async def test_spool_env_outside_root_skips_nothing_under_the_root(script, tmp_path, monkeypatch):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    _spool(outside)
    _write(root / ".pending" / "stray.wav")
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(outside))
    result = await script.report_discrepancies(Conn([_row(_owned(root))]), root)
    assert result["waiting_folder_skipped"] is False
    assert result["files_without_rows"] == [".pending/stray.wav"]


@pytest.mark.asyncio
async def test_a_real_stray_file_elsewhere_is_still_reported(script, tmp_path):
    _spool(tmp_path / ".pending")
    _write(tmp_path / "tenant" / "stray.wav")
    result = await script.report_discrepancies(Conn([_row(_owned(tmp_path))]), tmp_path)
    assert result["files_without_rows"] == ["tenant/stray.wav"]


async def _run(script, monkeypatch, tmp_path, rows, capsys):
    async def connect(*args, **kwargs):
        return Conn(rows)
    monkeypatch.setattr(script.asyncpg, "connect", connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    code = await script.run(argparse.Namespace(root=tmp_path))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.asyncio
async def test_exit_zero_when_clean(script, monkeypatch, tmp_path, capsys):
    f = tmp_path / "a.wav"
    _write(f)
    code, out = await _run(script, monkeypatch, tmp_path, [_row(f)], capsys)
    assert code == 0 and out["lost_recordings"] is False


@pytest.mark.asyncio
async def test_exit_four_when_only_a_stray_file(script, monkeypatch, tmp_path, capsys):
    f = tmp_path / "a.wav"
    _write(f)
    _write(tmp_path / "stray.wav")
    code, out = await _run(script, monkeypatch, tmp_path, [_row(f)], capsys)
    assert code == 4 and out["lost_recordings"] is False


@pytest.mark.asyncio
async def test_exit_three_when_a_record_has_no_file(script, monkeypatch, tmp_path, capsys):
    f = tmp_path / "a.wav"
    _write(f)
    code, out = await _run(script, monkeypatch, tmp_path, [_row(f), _row(tmp_path / "gone.wav", 2)], capsys)
    assert code == 3 and out["lost_recordings"] is True


@pytest.mark.asyncio
async def test_exit_three_when_lost_and_stray(script, monkeypatch, tmp_path, capsys):
    f = tmp_path / "a.wav"
    _write(f)
    _write(tmp_path / "stray.wav")
    code, out = await _run(script, monkeypatch, tmp_path, [_row(f), _row(tmp_path / "gone.wav", 2)], capsys)
    assert code == 3 and out["lost_recordings"] is True and out["counts"]["files_without_rows"] == 1


@pytest.mark.asyncio
async def test_exit_two_when_incomplete(script, monkeypatch, tmp_path, capsys):
    code, out = await _run(script, monkeypatch, tmp_path / "missing", [], capsys)
    assert code == 2 and out["complete"] is False and out["lost_recordings"] is False


def test_unit_treats_only_exit_four_as_success():
    lines = UNIT.read_text(encoding="utf-8").splitlines()
    start = lines.index("[Service]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    section = lines[start:end]
    assert "SuccessExitStatus=4" in section
    assert not any(line.strip() == "SuccessExitStatus=1" for line in lines)


@pytest.mark.asyncio
async def test_lost_recording_wins_over_an_incomplete_run(script, monkeypatch, tmp_path, capsys):
    f = tmp_path / "a.wav"
    _write(f)
    rows = [_row(f), _row(tmp_path / "gone.wav", 2), _row("/elsewhere/x.wav", 3)]  # last: invalid path
    code, out = await _run(script, monkeypatch, tmp_path, rows, capsys)
    assert code == 3 and out["lost_recordings"] is True and out["complete"] is False


@pytest.mark.asyncio
async def test_record_whose_file_is_in_the_waiting_folder_is_not_lost(script, monkeypatch, tmp_path, capsys):
    _write(tmp_path / "owned.wav")
    _spool(tmp_path / ".pending")
    rows = [_row(tmp_path / "owned.wav"), _row(tmp_path / ".pending" / "waiting.wav", 2)]
    code, out = await _run(script, monkeypatch, tmp_path, rows, capsys)
    assert code == 0 and out["lost_recordings"] is False
    assert out["counts"]["files"] == 1 and out["files_without_rows"] == []


def _deny(monkeypatch, script, predicate):
    real = script.os.scandir

    def fake(path):
        if predicate(Path(path)):
            raise PermissionError(13, "denied")
        return real(path)
    monkeypatch.setattr(script.os, "scandir", fake)


@pytest.mark.asyncio
async def test_unreadable_waiting_folder_is_not_an_incomplete_run(script, monkeypatch, tmp_path):
    _spool(tmp_path / ".pending")
    owned = _owned(tmp_path)
    _deny(monkeypatch, script, lambda p: p.name == ".pending")
    result = await script.report_discrepancies(Conn([_row(owned)]), tmp_path)
    assert result["waiting_folder_unreadable"] is True and result["issues"] == []
    assert result["complete"] is True and script.exit_code(result) == 0


@pytest.mark.asyncio
async def test_unreadable_folder_under_the_waiting_folder_is_not_an_incomplete_run(script, monkeypatch, tmp_path):
    _spool(tmp_path / ".pending")
    owned = _owned(tmp_path)
    _deny(monkeypatch, script, lambda p: p.name == "live")
    result = await script.report_discrepancies(Conn([_row(owned)]), tmp_path)
    assert result["waiting_folder_unreadable"] is True and result["complete"] is True


@pytest.mark.asyncio
async def test_unreadable_folder_elsewhere_is_still_incomplete(script, monkeypatch, tmp_path):
    _spool(tmp_path / ".pending")
    owned = _owned(tmp_path)
    _write(tmp_path / "tenant" / "x.wav")
    _deny(monkeypatch, script, lambda p: p.name == "tenant")
    result = await script.report_discrepancies(Conn([_row(owned)]), tmp_path)
    assert result["waiting_folder_unreadable"] is False
    assert result["complete"] is False and "directory_scan_failed:PermissionError" in result["issues"]
    assert script.exit_code(result) == 2


@pytest.mark.asyncio
async def test_spool_env_equal_to_the_root_skips_nothing(script, tmp_path, monkeypatch):
    _write(tmp_path / ".pending" / "stray.wav")
    monkeypatch.setenv("RECORDING_SPOOL_DIR", str(tmp_path))
    result = await script.report_discrepancies(Conn([_row(_owned(tmp_path))]), tmp_path)
    assert result["waiting_folder_skipped"] is False
    assert result["files_without_rows"] == [".pending/stray.wav"]


@pytest.mark.asyncio
async def test_spool_env_with_dotdot_and_trailing_slash_resolves_to_the_folder(script, tmp_path, monkeypatch):
    _spool(tmp_path / ".pending")
    (tmp_path / "t").mkdir()
    owned = _owned(tmp_path)
    for value in (str(tmp_path / "t" / ".." / ".pending"), str(tmp_path / ".pending") + "/"):
        monkeypatch.setenv("RECORDING_SPOOL_DIR", value)
        result = await script.report_discrepancies(Conn([_row(owned)]), tmp_path)
        assert result["waiting_folder_skipped"] is True and result["files_without_rows"] == []


@pytest.mark.asyncio
async def test_spool_env_relative_path_follows_the_application_rule(script, tmp_path, monkeypatch):
    # the application takes os.path.abspath(configured): relative to the process's working directory
    root = tmp_path / "root"
    _spool(root / "sp")
    owned = _owned(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("RECORDING_SPOOL_DIR", "sp")
    result = await script.report_discrepancies(Conn([_row(owned)]), root)
    assert result["waiting_folder_skipped"] is True and result["files_without_rows"] == []
    monkeypatch.chdir(tmp_path)
    result = await script.report_discrepancies(Conn([_row(owned)]), root)
    assert result["waiting_folder_skipped"] is False
    assert sorted(result["files_without_rows"]) == ["sp/live/call-1/safety.wav", "sp/waiting.json", "sp/waiting.wav"]
