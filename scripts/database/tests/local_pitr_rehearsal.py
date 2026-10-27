#!/usr/bin/env python3
"""Explicit, synthetic local PITR rehearsal on private loopback clusters.

Does not use DATABASE_URL, existing servers, real customers or providers.
Requires initdb, pg_ctl and client binaries; run as an unprivileged account.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from local_postgres_rehearsal import run  # noqa: E402  (reuse the hardened process helper)

spec = importlib.util.spec_from_file_location("talklee_pitr", TESTS.parent / "pitr.py")
pitr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pitr)
recovery = pitr.recovery


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pitr.main(argv)
    return code, out.getvalue(), err.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = TESTS.parents[2]
    output = root / "test-output"
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="pitr-rehearsal-", dir=output)).resolve()
    clusters, checks, timings = [], [], {}
    bin_dir = args.bin_dir
    try:
        platform = pitr.default_platform()
        archive = work / "wal-archive"
        archive.mkdir()
        source = work / "source"
        clusters.append(source)
        run(bin_dir, "initdb", ["-D", str(source), "-U", "fixture_owner", "--auth=trust", "--encoding=UTF8", "--locale=C"])
        port = free_port()
        with (source / "postgresql.conf").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n" + "\n".join(pitr.archive_settings(archive, platform)) + "\n")
        run(bin_dir, "pg_ctl", ["-D", str(source), "-l", str(work / "source.log"),
                                "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"])
        ports = {"restore_time": free_port(), "restore_latest": free_port()}
        sections = {"admin": (port, "fixture_owner"), "backup": (port, "pitr_backup"),
                    "restore_time": (ports["restore_time"], "fixture_owner"),
                    "restore_latest": (ports["restore_latest"], "fixture_owner")}
        service_file = work / "services.conf"
        service_file.write_text("\n".join(
            f"[{n}]\nhost=127.0.0.1\nport={p}\ndbname=postgres\nuser={u}\nsslmode=disable\n"
            for n, (p, u) in sections.items()), encoding="utf-8")
        service_file.chmod(0o600)
        pg = recovery.Postgres(bin_dir, timeout=120)
        svc = lambda name: recovery.Service(service_file, name, True)  # noqa: E731
        admin = svc("admin")
        sql = lambda service, text: pg.sql(service, text, readonly=False)  # noqa: E731
        sql(admin, "CREATE ROLE pitr_backup LOGIN REPLICATION;")
        sql(admin, "CREATE TABLE pitr_rows(label text PRIMARY KEY); INSERT INTO pitr_rows VALUES ('A');")
        sql(admin, "SELECT pg_switch_wal();")

        base = work / "base" / "first"
        code, _, err = cli(["base-backup", "--service-file", str(service_file), "--service", "backup",
                            "--destination", str(base), "--bin-dir", str(bin_dir), "--allow-local-plaintext"])
        assert code == 0, "base-backup failed: " + err
        base_receipt = json.loads((base / "receipt.json").read_text())
        assert base_receipt["status"] == "verified" and base_receipt["start_lsn"] and base_receipt["stop_lsn"]
        assert "password" not in json.dumps(base_receipt).lower()
        checks.append("base backup published with sha256 receipt, start/stop LSN and start time")

        sql(admin, "INSERT INTO pitr_rows VALUES ('B');")
        sql(admin, "SELECT pg_switch_wal();")
        time.sleep(2)
        target_time = pg.sql(admin, "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', "
                                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00';")
        time.sleep(2)
        sql(admin, "INSERT INTO pitr_rows VALUES ('C');")
        segment = pg.sql(admin, "SELECT pg_walfile_name(pg_current_wal_insert_lsn() - 1);")
        sql(admin, "SELECT pg_switch_wal();")
        deadline = time.monotonic() + 90
        while not (archive / segment).exists():
            if time.monotonic() > deadline:
                raise AssertionError("segment containing row C was not archived in 90 s")
            time.sleep(0.5)
        assert cli(["archive-health", "--archive-dir", str(archive), "--max-age-minutes", "5"])[0] == 0
        checks.append("archive_command archived the segment containing row C; archive-health passes")

        def rows(name):
            return pg.sql(svc(name), "SELECT string_agg(label, ',' ORDER BY label) FROM pitr_rows;")

        def restore(name, extra):
            data = work / name
            clusters.append(data)
            code, out, err = cli(["restore", "--base-backup", str(base), "--archive-dir", str(archive),
                                  "--target-data-dir", str(data), "--port", str(ports[name]), "--start",
                                  "--bin-dir", str(bin_dir),
                                  "--receipt", str(work / (name + ".json")), *extra])
            assert code == 0, f"restore {name} failed: " + err
            return json.loads(out), data

        timed, data = restore("restore_time", ["--target-time", target_time])
        assert rows("restore_time") == "A,B", rows("restore_time")
        timings["restore_target_time_seconds"] = timed["elapsed_seconds"]
        timings["recovery_target_time_seconds"] = timed["recovery_seconds"]
        checks.append(f"restore to {target_time} has rows A,B and not C")
        assert pg.sql(svc("restore_time"), "SHOW archive_mode;") == "off"
        checks.append("restored copy has archive_mode=off and its own loopback port")
        run(bin_dir, "pg_ctl", ["-D", str(data), "-m", "immediate", "-w", "stop"])

        latest, data2 = restore("restore_latest", ["--target-latest"])
        assert rows("restore_latest") == "A,B,C", rows("restore_latest")
        timings["restore_latest_seconds"] = latest["elapsed_seconds"]
        timings["recovery_latest_seconds"] = latest["recovery_seconds"]
        checks.append("restore --target-latest has rows A,B,C")

        code, _, err = cli(["restore", "--base-backup", str(base), "--archive-dir", str(archive),
                            "--target-data-dir", str(data2), "--target-latest"])
        assert code == 1 and "not empty" in err and (data2 / "PG_VERSION").exists()
        checks.append("restore refused an existing non-empty data directory without touching it")

        bad = work / "base" / "corrupt"
        shutil.copytree(base, bad)
        with (bad / "base.tar.gz").open("ab") as stream:
            stream.write(b"corruption")
        victim = work / "victim"
        code, _, err = cli(["restore", "--base-backup", str(bad), "--archive-dir", str(archive),
                            "--target-data-dir", str(victim), "--target-latest"])
        assert code == 1 and "checksum" in err and not victim.exists()
        checks.append("corrupted base backup failed checksum verification before any extraction")

        payload = {"status": "passed", "completed_at": recovery.utcnow(), "checks": checks, "timings": timings,
                   "target_time": target_time, "base_backup_start_lsn": base_receipt["start_lsn"],
                   "base_backup_stop_lsn": base_receipt["stop_lsn"], "platform": platform,
                   "postgres_client": pg.run("psql", ["--version"]), "production_accessed": False,
                   "scope": "synthetic loopback PITR rehearsal; not production data or sizing"}
        recovery.write_json_new(args.receipt, payload)
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        cleanup_errors = []
        for data in reversed(clusters):
            if not (data / "postmaster.pid").exists():
                continue
            try:
                run(bin_dir, "pg_ctl", ["-D", str(data), "-m", "immediate", "-w", "stop"])
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise RuntimeError("owned cluster cleanup failed; retained " + str(work) + ": " + "; ".join(cleanup_errors))
        if work.parent == output.resolve() and work.name.startswith("pitr-rehearsal-"):
            shutil.rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())
