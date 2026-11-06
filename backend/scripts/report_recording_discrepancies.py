#!/usr/bin/env python3
"""Read-only local recording-row/file reconciliation. Never repairs or deletes.

The operator supplies DATABASE_URL; LOCAL_RECORDINGS_DIR defaults to recordings
under the backend working directory. Object storage is outside this report.
The database and filesystem cannot share a snapshot: differences need owner
review, especially while a recording is being written/registered or purged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

import asyncpg


_ROWS_SQL = """
    SELECT id, tenant_id, call_id, s3_key, s3_bucket, status
    FROM recordings_s3 WHERE tenant_id = $1::uuid AND s3_bucket = 'local'
      AND ($2::uuid IS NULL OR id > $2::uuid)
    ORDER BY id LIMIT $3
"""
_LEGACY_SQL = """
    SELECT r.id, r.tenant_id, c.tenant_id AS owner_tenant_id, r.call_id,
           r.storage_path, r.drive_file_id, r.status,
           (r.tenant_id = $1::uuid OR r.tenant_id IS NULL) AS ownership_consistent
    FROM recordings r JOIN calls c ON c.id = r.call_id
    WHERE c.tenant_id = $1::uuid
      AND ($2::uuid IS NULL OR r.id > $2::uuid)
    ORDER BY r.id LIMIT $3
"""


def _relative_path(root, raw):
    """Validate lexical ownership before checking only in-root path components."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    candidate = Path(os.path.abspath(raw if os.path.isabs(raw) else root / raw))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
        # Windows junctions are not necessarily reported as symlinks.
        is_junction = getattr(cursor, "is_junction", None)
        if is_junction and is_junction():
            return None
    return relative.as_posix()


def _scan_files(root, max_files):
    files, issues, pending = set(), [], [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    junction = getattr(path, "is_junction", None)
                    if entry.is_symlink() or (junction and junction()):
                        issues.append("symlink_or_junction_skipped")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        if len(files) >= max_files:
                            return files, list(dict.fromkeys([*issues, "file_limit_reached"]))
                        files.add(path.relative_to(root).as_posix())
        except OSError as exc:
            issues.append(f"directory_scan_failed:{type(exc).__name__}")
    return files, list(dict.fromkeys(issues))


async def report_discrepancies(conn, root, *, page_size=500, max_rows=100000, max_files=100000, sample_limit=1000):
    if not 1 <= page_size <= 1000 or not 1 <= max_rows <= 1000000 or not 1 <= max_files <= 1000000 or not 1 <= sample_limit <= 10000:
        raise ValueError("Report limits are outside the supported range")
    result = {"scope": "local_recordings_s3_and_legacy", "read_only": True, "complete": True,
              "snapshot_consistent": False, "issues": [], "files_without_rows": [],
              "rows_without_files": [], "invalid_paths": [], "samples_truncated": False,
              "counts": {"files": 0, "local_rows": 0, "files_without_rows": 0,
                         "rows_without_files": 0, "invalid_paths": 0, "nonuploaded_rows": 0,
                         "legacy_local_rows": 0, "legacy_remote_rows": 0,
                         "legacy_ownership_conflicts": 0}}
    try:
        root = Path(root).resolve(strict=True)
        available = root.is_dir()
    except (OSError, ValueError):
        available = False
    if not available:
        result.update(complete=False, issues=["recording_root_unavailable"])
        return result

    owned, uploaded = set(), []
    inventory_complete = True
    orphan_inventory_safe = True
    scanned_rows = 0
    # A read-only repeatable DB snapshot, with explicit tenant predicates on
    # every recording read. Only the operator system-directory discovery is
    # global. row_security=off refuses a deceptively filtered orphan report.
    async with conn.transaction(readonly=True, isolation="repeatable_read"):
        await conn.execute("SET LOCAL statement_timeout = '30s'")
        await conn.execute("SET LOCAL lock_timeout = '5s'")
        await conn.execute("SET LOCAL app.bypass_rls = 'on'")
        await conn.execute("SET LOCAL row_security = off")
        legacy_present = bool(await conn.fetchval("SELECT to_regclass($1)", "public.recordings"))
        tenant_cursor = None
        tenant_count = 0
        while inventory_complete:
            tenants = await conn.fetch(
                "SELECT id FROM tenants WHERE ($1::uuid IS NULL OR id > $1::uuid) ORDER BY id LIMIT $2",
                tenant_cursor, 100,
            )
            if not tenants:
                break
            for tenant in tenants:
                tenant_count += 1
                if tenant_count > 10000:
                    inventory_complete = False
                    result["issues"].append("tenant_limit_reached")
                    break
                cursor = None
                while True:
                    rows = await conn.fetch(_ROWS_SQL, tenant["id"], cursor, page_size)
                    if not rows:
                        break
                    for row in rows:
                        if scanned_rows >= max_rows:
                            inventory_complete = False
                            result["issues"].append("row_limit_reached")
                            break
                        if cursor is not None and row["id"] <= cursor:
                            raise RuntimeError("Recording pagination did not advance")
                        cursor = row["id"]
                        scanned_rows += 1
                        result["counts"]["local_rows"] += 1
                        if row["status"] != "uploaded":
                            result["counts"]["nonuploaded_rows"] += 1
                        try:
                            relative = _relative_path(root, row["s3_key"])
                        except OSError:
                            relative = None
                        identity = {"recording_id": str(row["id"]), "tenant_id": str(row["tenant_id"]), "table": "recordings_s3"}
                        if relative is None:
                            result["counts"]["invalid_paths"] += 1
                            if len(result["invalid_paths"]) < sample_limit:
                                result["invalid_paths"].append(identity)
                            continue
                        owned.add(relative)
                        if row["status"] == "uploaded":
                            uploaded.append((relative, identity))
                    if not inventory_complete:
                        break
                if not inventory_complete:
                    break
                if legacy_present:
                    cursor = None
                    while True:
                        # Nullable legacy tenant IDs are owned through their
                        # call. Conflicting explicit ownership is flagged as
                        # incomplete; excluding it silently would falsely label
                        # its still-registered file an orphan.
                        # Any metadata/query error propagates before files
                        # can be labelled unregistered.
                        rows = await conn.fetch(_LEGACY_SQL, tenant["id"], cursor, page_size)
                        if not rows:
                            break
                        for row in rows:
                            if scanned_rows >= max_rows:
                                inventory_complete = False
                                result["issues"].append("row_limit_reached")
                                break
                            if cursor is not None and row["id"] <= cursor:
                                raise RuntimeError("Legacy recording pagination did not advance")
                            cursor = row["id"]
                            scanned_rows += 1
                            if not row["ownership_consistent"]:
                                orphan_inventory_safe = False
                                result["counts"]["legacy_ownership_conflicts"] += 1
                                result["issues"].append("legacy_ownership_conflict")
                                continue
                            raw_path = row["storage_path"]
                            if isinstance(raw_path, str) and "://" in raw_path and not raw_path.lower().startswith("file://"):
                                # A URL is not a local storage path. No remote
                                # lookup is attempted, including Drive/S3.
                                result["counts"]["legacy_remote_rows"] += 1
                                continue
                            result["counts"]["local_rows"] += 1
                            result["counts"]["legacy_local_rows"] += 1
                            if row["status"] != "uploaded":
                                result["counts"]["nonuploaded_rows"] += 1
                            try:
                                relative = _relative_path(root, raw_path)
                            except OSError:
                                relative = None
                            identity = {"recording_id": str(row["id"]),
                                        "tenant_id": str(row["owner_tenant_id"]), "table": "recordings"}
                            if relative is None:
                                result["counts"]["invalid_paths"] += 1
                                if len(result["invalid_paths"]) < sample_limit:
                                    result["invalid_paths"].append(identity)
                                continue
                            owned.add(relative)
                            if row["status"] == "uploaded":
                                uploaded.append((relative, identity))
                        if not inventory_complete:
                            break
                if not inventory_complete:
                    break
            tenant_cursor = tenants[-1]["id"]

    files, scan_issues = await asyncio.to_thread(_scan_files, root, max_files)
    result["issues"].extend(scan_issues)
    result["counts"]["files"] = len(files)
    if result["counts"]["invalid_paths"]:
        result["issues"].append("invalid_recording_paths")
    # A partial DB read cannot establish that a file has no row. A partial
    # directory scan cannot establish that an uploaded row has no file.
    if inventory_complete and orphan_inventory_safe:
        orphans = sorted(files - owned)
        result["counts"]["files_without_rows"] = len(orphans)
        result["files_without_rows"] = orphans[:sample_limit]
    if not scan_issues:
        missing = [{**identity, "path": relative} for relative, identity in uploaded if relative not in files]
        result["counts"]["rows_without_files"] = len(missing)
        result["rows_without_files"] = missing[:sample_limit]
    result["issues"] = list(dict.fromkeys(result["issues"]))
    result["complete"] = not result["issues"]
    result["samples_truncated"] = any(result["counts"][key] > len(result[key])
                                      for key in ("files_without_rows", "rows_without_files", "invalid_paths"))
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Report local recording discrepancies without changing rows or files.")
    parser.add_argument("--root", type=Path, help="Override LOCAL_RECORDINGS_DIR for an operator-selected recording root.")
    return parser


async def run(args):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("Set DATABASE_URL in the process environment")
    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=10, command_timeout=30)
    try:
        result = await report_discrepancies(conn, args.root or os.environ.get("LOCAL_RECORDINGS_DIR", "./recordings"))
        print(json.dumps(result, sort_keys=True), flush=True)
        if not result["complete"]:
            return 2
        return 1 if result["counts"]["files_without_rows"] or result["counts"]["rows_without_files"] else 0
    finally:
        await conn.close(timeout=5)


def main():
    try:
        return asyncio.run(run(build_parser().parse_args()))
    except Exception as exc:
        # Driver errors can contain DSNs or local paths; log only the type.
        print(json.dumps({"read_only": True, "complete": False, "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
