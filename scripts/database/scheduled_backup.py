#!/usr/bin/env python3
"""Run one checked backup with a process lock and comparison to the last bundle,
then copy it to the off-host replica root and apply the retention policy.

Retention runs only after a successful backup, and the replica is written
before pruning, so a failing schedule never deletes the last good archive.
The replica leaves this server encrypted (--replica-passphrase-file) unless
--allow-unencrypted-replica is given for storage that encrypts by itself."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import recovery


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--service-file", type=Path, required=True)
    result.add_argument("--service", default="talklee_backup")
    result.add_argument("--backup-root", type=Path, required=True)
    result.add_argument("--minimum-bytes", type=int, default=1048576)
    result.add_argument("--minimum-ratio", type=float, default=.5)
    result.add_argument("--rls-bypass-guc", action="store_true")
    result.add_argument("--rls-setting", action="append", default=[], metavar="app.NAME=VALUE")
    result.add_argument("--replica-root", type=Path, help="off-host mount that receives a verified copy")
    recovery.add_encryption_arguments(result, "--replica-passphrase-file", "--allow-unencrypted-replica")
    result.add_argument("--keep-days", type=float, default=30)
    result.add_argument("--keep-minimum", type=int, default=7)
    return result


def main() -> int:
    args = parser().parse_args()
    lock = None
    try:
        # Checked before the backup runs, so a misconfigured copy never costs a dump.
        gpg = recovery.replica_encryption(args) if args.replica_root else None
        if os.name != "posix":
            raise recovery.RecoveryError("scheduled entry point requires Linux advisory file locking")
        import fcntl
        os.umask(0o077)
        args.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (args.backup_root / ".backup.lock").open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        bundles = sorted((path for path in args.backup_root.iterdir() if path.is_dir() and not path.name.startswith(".")),
                         key=lambda path: path.name)
        previous = bundles[-1] if bundles else None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        service = recovery.Service(args.service_file, args.service)
        receipt = recovery.backup(recovery.Postgres(), service, args.backup_root / stamp,
                                  args.minimum_bytes, previous, args.minimum_ratio, args.rls_bypass_guc,
                                  recovery.parse_rls_settings(args.rls_setting))
        result = {"backup": receipt}
        if args.replica_root:
            result["replica"] = recovery.replicate(args.backup_root / stamp, args.replica_root, gpg)
            result["replica_retention"] = recovery.prune(args.replica_root, args.keep_days, args.keep_minimum, False)
        result["retention"] = recovery.prune(args.backup_root, args.keep_days, args.keep_minimum, False)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, recovery.RecoveryError):
        print("Scheduled backup failed; verify lock, configuration, prior archive and size checks privately.", file=sys.stderr)
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
