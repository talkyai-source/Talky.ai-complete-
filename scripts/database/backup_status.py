#!/usr/bin/env python3
"""Publish one atomic, nonsecret backup observation for authenticated metrics."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

DEFAULT_DIRECTORY = "/var/lib/talky/monitoring"
FIELDS = {"started_at", "completed_at", "result", "last_success_at"}


def read_receipt(directory: Path) -> dict | None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(directory / "backup-attempt.json", flags)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Invalid backup status file")
        raw = source.read(1025)
    if len(raw) > 1024:
        raise ValueError("Backup status exceeds limit")
    receipt = json.loads(raw)
    if not isinstance(receipt, dict) or set(receipt) != FIELDS:
        raise ValueError("Invalid backup status shape")
    return receipt


def publish(directory: Path, result: str, started_at: int, *, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    if result not in {"running", "succeeded", "failed"} or not 0 < started_at <= now:
        raise ValueError("Invalid backup status")
    existed = directory.exists()
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not existed:
        directory.chmod(0o755)
    previous = read_receipt(directory)
    last_success = previous["last_success_at"] if previous else None
    if last_success is not None and (isinstance(last_success, bool) or
            not isinstance(last_success, (int, float)) or not math.isfinite(last_success) or
            not 0 < last_success <= now):
        raise ValueError("Invalid previous backup success")
    receipt = {"started_at": started_at, "completed_at": None if result == "running" else now,
               "result": result, "last_success_at": now if result == "succeeded" else last_success}
    # Same-filesystem rename publishes outcome and success timestamp together.
    # No fallible operation follows replace; a failed pre-publication write
    # leaves the previous complete receipt untouched.
    descriptor, name = tempfile.mkstemp(prefix=".backup-status-", dir=directory)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as target:
            json.dump(receipt, target, separators=(",", ":"))
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        staged.chmod(0o644)
        os.replace(staged, directory / "backup-attempt.json")
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(os.getenv("TALKY_BACKUP_STATUS_DIR", DEFAULT_DIRECTORY)))
    parser.add_argument("--result", choices=("running", "succeeded", "failed"), required=True)
    parser.add_argument("--started-at", type=int, required=True)
    args = parser.parse_args()
    try:
        publish(args.directory, args.result, args.started_at)
    except (OSError, ValueError):
        parser.exit(1, "Backup monitoring receipt could not be published.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
