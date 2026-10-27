#!/usr/bin/env python3
"""Stream a dump whose RLS principal was validated in its exact snapshot.

Only the caller publishes the archive. A dead keeper, failed dump or interrupted
run cannot return success. No application import, password lookup or role change.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import signal
import subprocess
import sys
import threading
from typing import BinaryIO, Sequence
from uuid import UUID


class BackupError(RuntimeError):
    pass


def principal_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise BackupError("TALKY_BACKUP_PRINCIPAL_ID must be an existing administrator UUID") from exc


def snapshot_dump(psql: Sequence[str], dump: Sequence[str], principal: str,
                  output: BinaryIO, *, timeout: float = 3600) -> None:
    """Commands must address the same database/role; the CLI fixes both below."""
    principal = principal_id(principal)
    env = dict(os.environ, PGOPTIONS=f"-c app.bypass_rls=true -c app.current_user_id={principal}")
    keeper = worker = None
    lines: queue.Queue[str] = queue.Queue()
    try:
        keeper = subprocess.Popen(
            [*psql, "-X", "-q", "-A", "-t", "-w", "-v", "ON_ERROR_STOP=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env,
        )

        def read_lines():
            assert keeper is not None and keeper.stdout is not None
            try:
                for line in keeper.stdout:
                    lines.put(line.strip())
            finally:
                lines.put("")

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        assert keeper.stdin is not None
        keeper.stdin.write(
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SET LOCAL statement_timeout='30s';\n"
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM public.user_profiles "
            f"WHERE id='{principal}'::uuid AND role='platform_admin' "
            "AND is_active IS TRUE AND is_verified IS TRUE) "
            "THEN pg_export_snapshot() ELSE 'INVALID_PRINCIPAL' END;\n"
        )
        keeper.stdin.flush()
        try:
            snapshot = lines.get(timeout=35)
        except queue.Empty as exc:
            raise BackupError("snapshot keeper did not become ready") from exc
        if snapshot == "INVALID_PRINCIPAL":
            raise BackupError("backup principal is absent, inactive, unverified or not platform_admin")
        if not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", snapshot):
            raise BackupError("snapshot keeper failed before export")
        worker = subprocess.Popen(
            [*dump, "--format=custom", "--compress=6", "--enable-row-security", "--snapshot=" + snapshot],
            stdout=output, stderr=subprocess.DEVNULL, env=env,
        )
        if worker.wait(timeout=timeout) != 0:
            raise BackupError("pg_dump failed; archive not publishable")
        if keeper.poll() is not None:
            raise BackupError("snapshot keeper exited during dump")
        keeper.stdin.write("SELECT 'SNAPSHOT_OK';\nROLLBACK;\n\\q\n")
        keeper.stdin.flush()
        try:
            acknowledged = lines.get(timeout=35)
        except queue.Empty as exc:
            raise BackupError("snapshot keeper did not acknowledge completion") from exc
        if acknowledged != "SNAPSHOT_OK" or keeper.wait(timeout=10) != 0:
            raise BackupError("snapshot keeper completion failed")
    except (subprocess.TimeoutExpired, BrokenPipeError, OSError) as exc:
        raise BackupError("backup process failed or exceeded its deadline") from exc
    finally:
        for process in (worker, keeper):
            if process is None:
                continue
            if process.stdin:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout:
                reader.join(timeout=1)
                process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()

    def interrupted(_signum, _frame):
        raise BackupError("backup interrupted")

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        principal = principal_id(os.environ.get("TALKY_BACKUP_PRINCIPAL_ID", ""))
        # Container-side deadlines also bound clients if the host CLI disappears.
        options = f"-c app.bypass_rls=true -c app.current_user_id={principal}"
        prefix = ["docker", "exec", "-i", "-e", "PGOPTIONS=" + options, args.container]
        connection = ["-h", "/var/run/postgresql", "-U", args.user, "-d", args.database]
        snapshot_dump(
            [*prefix, "timeout", "-k", "5", "3700", "psql", *connection],
            [*prefix, "timeout", "-k", "5", "3600", "pg_dump", *connection],
            principal, sys.stdout.buffer,
        )
    except BackupError as exc:
        print("DB-BACKUP CRITICAL: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
