"""Real child-process failure/lifetime checks; no database or Docker required."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "deploy/backup_snapshot.py"
spec = importlib.util.spec_from_file_location("backup_snapshot", PATH)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
PRINCIPAL = "00000000-0000-4000-8000-000000000001"

CLIENT = r'''
import os, pathlib, sys, time
mode, folder = sys.argv[1:3]
folder = pathlib.Path(folder)
(folder / (mode + '.pid')).write_text(str(os.getpid()))
if mode == 'keeper':
    for line in sys.stdin:
        if 'pg_export_snapshot' in line:
            (folder / 'setup.sql').write_text(line)
            print('INVALID_PRINCIPAL' if (folder / 'invalid').exists() else '00000003-0000001B-1', flush=True)
            if (folder / 'dead').exists():
                raise SystemExit(1)
        elif 'SNAPSHOT_OK' in line:
            print('SNAPSHOT_OK', flush=True)
        elif '\q' in line:
            raise SystemExit(0)
else:
    (folder / 'dump.args').write_text(repr(sys.argv))
    (folder / 'dump.options').write_text(os.environ['PGOPTIONS'])
    if (folder / 'slow').exists():
        time.sleep(60)
    time.sleep(.1)
    sys.stdout.buffer.write(b'archive-bytes')
    raise SystemExit(1 if (folder / 'failed').exists() else 0)
'''


def run(tmp_path, monkeypatch, *, timeout=5, cancel=False):
    client = tmp_path / "client.py"
    client.write_text(CLIENT)
    spawned = []
    original = subprocess.Popen

    def tracked(*args, **kwargs):
        child = original(*args, **kwargs)
        spawned.append(child)
        if cancel and len(spawned) == 2:
            wait = child.wait

            def interrupt_once(*args, **kwargs):
                child.wait = wait
                raise backup.BackupError("backup interrupted")

            child.wait = interrupt_once
        return child

    monkeypatch.setattr(backup.subprocess, "Popen", tracked)
    try:
        with (tmp_path / "partial").open("wb") as output:
            backup.snapshot_dump(
                [sys.executable, str(client), "keeper", str(tmp_path)],
                [sys.executable, str(client), "dump", str(tmp_path)], PRINCIPAL, output, timeout=timeout)
    finally:
        assert all(process.poll() is not None for process in spawned)


def test_snapshot_and_principal_reach_dump_before_keeper_completion(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch)
    assert (tmp_path / "partial").read_bytes() == b"archive-bytes"
    assert "--snapshot=00000003-0000001B-1" in (tmp_path / "dump.args").read_text()
    assert (tmp_path / "dump.options").read_text() == f"-c app.bypass_rls=true -c app.current_user_id={PRINCIPAL}"
    assert "is_active IS TRUE AND is_verified IS TRUE" in (tmp_path / "setup.sql").read_text()


@pytest.mark.parametrize("condition", ["invalid", "dead", "failed", "slow", "cancel"])
def test_failed_or_interrupted_clients_cannot_report_a_publishable_archive(tmp_path, monkeypatch, condition):
    (tmp_path / condition).touch()
    with pytest.raises(backup.BackupError):
        run(tmp_path, monkeypatch, timeout=.2 if condition == "slow" else 5, cancel=condition == "cancel")
    if condition == "invalid":
        assert not (tmp_path / "dump.args").exists()


@pytest.mark.parametrize("value", ["", "not-a-uuid", "a'; SELECT 1;--"])
def test_invalid_principal_never_starts_clients(tmp_path, value):
    with pytest.raises(backup.BackupError):
        backup.snapshot_dump(["must-not-run"], ["must-not-run"], value, None)
