#!/usr/bin/env python3
"""Run one checked backup with a process lock and comparison to the last bundle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

import recovery
import backup_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-file", type=Path, required=True)
    parser.add_argument("--service", default="talklee_backup")
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--minimum-bytes", type=int, default=1048576)
    parser.add_argument("--minimum-ratio", type=float, default=.5)
    parser.add_argument("--status-directory", type=Path,
                        default=Path(os.getenv("TALKY_BACKUP_STATUS_DIR", "/var/lib/talklee-backup-monitoring")))
    args = parser.parse_args()
    lock = None
    started_at = None
    status_allowed = False
    try:
        if os.name != "posix":
            raise recovery.RecoveryError("scheduled entry point requires Linux advisory file locking")
        import fcntl
        os.umask(0o077)
        args.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (args.backup_root / ".backup.lock").open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Never let a rejected concurrent run overwrite the active run's state.
        started_at = int(time.time())
        if args.status_directory.resolve().is_relative_to(args.backup_root.resolve()):
            raise recovery.RecoveryError("monitoring status must be outside the private archive directory")
        status_allowed = True
        backup_status.publish(args.status_directory, "running", started_at)
        bundles = sorted((path for path in args.backup_root.iterdir() if path.is_dir() and not path.name.startswith(".")),
                         key=lambda path: path.name)
        previous = bundles[-1] if bundles else None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        service = recovery.Service(args.service_file, args.service)
        receipt = recovery.backup(recovery.Postgres(), service, args.backup_root / stamp,
                                  args.minimum_bytes, previous, args.minimum_ratio)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        backup_status.publish(args.status_directory, "succeeded", started_at)
        return 0
    except (OSError, ValueError, recovery.RecoveryError):
        if started_at is not None and status_allowed:
            try:
                backup_status.publish(args.status_directory, "failed", started_at)
            except (OSError, ValueError):
                print("Backup monitoring receipt could not be published.", file=sys.stderr)
        print("Scheduled backup failed; verify lock, configuration, prior archive and size checks privately.", file=sys.stderr)
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
