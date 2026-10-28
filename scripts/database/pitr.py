#!/usr/bin/env python3
"""Point-in-time recovery for a self-managed PostgreSQL (continuous archiving).

PostgreSQL docs, "Continuous Archiving and Point-in-Time Recovery". Managed
providers include PITR; this tool covers the self-hosted dedicated server.
Passwords are read by libpq from a protected password file, never argv or
receipts. Restores only ever write to a NEW data directory and always disable
archiving and rebind to loopback so a restored copy cannot touch production.
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery  # noqa: E402
from recovery import RecoveryError, digest, sync_file, utcnow, write_json_new  # noqa: E402

SEGMENT = re.compile(r"[0-9A-F]{24}\Z")
BASE_FILES = ("base.tar.gz", "pg_wal.tar.gz", "backup_manifest")
UNSAFE_DIR = re.compile(r"[\"'\x00\r\n%]")


def default_platform() -> str:
    return "windows" if os.name == "nt" else "linux"


def conf_quote(value: str) -> str:
    """Quote a string for postgresql.conf (backslash and quote are escapes)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def checked_dir(directory: str | Path, platform: str) -> str:
    text = str(directory)
    if not text or UNSAFE_DIR.search(text):
        raise RecoveryError("archive directory must not contain quotes, percent signs or control characters")
    if platform not in ("linux", "windows"):
        raise RecoveryError("platform must be linux or windows")
    return text.replace("/", "\\") if platform == "windows" else text.replace("\\", "/")


def archive_command(directory: str | Path, platform: str) -> str:
    """Never overwrites a segment: an existing file makes the command fail."""
    d = checked_dir(directory, platform)
    if platform == "linux":
        return f"test ! -f '{d}/%f' && cp '%p' '{d}/%f.tmp' && mv '{d}/%f.tmp' '{d}/%f'"
    d = d.rstrip("\\")
    return (f'if exist "{d}\\%f" (exit 1) else (copy "%p" "{d}\\%f.tmp" >nul && '
            f'move /Y "{d}\\%f.tmp" "{d}\\%f" >nul)')


def restore_command(directory: str | Path, platform: str) -> str:
    d = checked_dir(directory, platform)
    if platform == "linux":
        return f"test -f '{d}/%f' && cp '{d}/%f' '%p'"
    return f'copy "{d.rstrip(chr(92))}\\%f" "%p" >nul'


def archive_settings(directory: str | Path, platform: str) -> list[str]:
    return ["wal_level = replica", "archive_mode = on", "archive_timeout = 60",
            "archive_command = " + conf_quote(archive_command(directory, platform))]


def parse_target_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise RecoveryError("target time must be ISO 8601 with a UTC offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryError("target time must include a UTC offset (for example +00:00 or Z)")
    return parsed.astimezone(timezone.utc)


def render_recovery_conf(archive_dir: str | Path, platform: str, target_time: datetime | None,
                         port: int | None) -> str:
    lines = ["", "# talklee pitr restore",
             "restore_command = " + conf_quote(restore_command(archive_dir, platform))]
    if target_time is not None:
        lines.append("recovery_target_time = " + conf_quote(target_time.strftime("%Y-%m-%d %H:%M:%S.%f+00")))
    lines += ["recovery_target_action = 'promote'",
              # A restored copy must never archive into the production archive.
              "archive_mode = off"]
    if port is not None:
        lines += [f"port = {int(port)}", "listen_addresses = '127.0.0.1'"]
    return "\n".join(lines) + "\n"


def _exec(exe: str, args: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
    """Run with output in temp files (a postmaster must not hold our pipes)."""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen([exe, *args], stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, env=env, **flags)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.kill()
                proc.wait(timeout=15)
                raise RecoveryError(f"{Path(exe).stem} timed out") from exc
            out.seek(0)
            return proc.returncode, out.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise RecoveryError(f"{Path(exe).stem} could not be started") from exc


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not k.startswith("PG") and k not in {"DATABASE_URL", "READ_DATABASE_URL", "MIGRATION_DATABASE_URL"}}


def _label(tar_path: Path) -> dict[str, str]:
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if member.name == "backup_label":
                text = tar.extractfile(member).read().decode("utf-8", errors="replace")
                break
        else:
            raise RecoveryError("base backup has no backup_label")
    start = re.search(r"START WAL LOCATION: ([0-9A-F]+/[0-9A-F]+)", text)
    when = re.search(r"START TIME: (.+)", text)
    if not start or not when:
        raise RecoveryError("base backup label is unreadable")
    return {"start_lsn": start.group(1), "start_time": when.group(1).strip()}


def base_backup(pg: recovery.Postgres, service: recovery.Service, destination: Path) -> dict:
    if destination.exists() or destination.is_symlink():
        raise RecoveryError("refusing to replace an existing base backup")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".talklee-basebackup-", dir=destination.parent))
    started = time.monotonic()
    try:
        env = service.env()
        env.pop("PGOPTIONS", None)  # walsender connections do not take session options
        code, _ = _exec(pg.executable("pg_basebackup"),
                        ["-D", str(staging), "--format=tar", "--gzip", "--wal-method=stream", "--checkpoint=fast",
                         "--manifest-checksums=SHA256", "--no-password"], env, pg.timeout)
        if code:
            raise RecoveryError(f"pg_basebackup failed (exit {code}); the role needs REPLICATION and "
                                "a replication pg_hba entry; inspect privately using the configured service")
        names = sorted(p.name for p in staging.iterdir())
        if set(names) != set(BASE_FILES):
            raise RecoveryError("base backup has unexpected contents (tablespaces are not supported)")
        try:
            manifest = json.loads((staging / "backup_manifest").read_text(encoding="utf-8"))
            ranges = manifest["WAL-Ranges"]
            stop_lsn, timeline = ranges[-1]["End-LSN"], ranges[0]["Timeline"]
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RecoveryError("base backup manifest is unreadable") from exc
        label = _label(staging / "base.tar.gz")
        files = {n: {"bytes": (staging / n).stat().st_size, "sha256": digest(staging / n)} for n in names}
        receipt = {"operation": "base_backup", "status": "verified", "created_at": utcnow(),
                   "bytes": sum(f["bytes"] for f in files.values()), "files": files, "timeline": timeline,
                   "start_lsn": label["start_lsn"], "stop_lsn": stop_lsn, "start_time": label["start_time"],
                   "duration_seconds": round(time.monotonic() - started, 3),
                   "verification": "sha256 of each tar and backup_manifest (pg_verifybackup cannot read tar before PG18)",
                   "source_database": service.values["dbname"], "restored": False}
        write_json_new(staging / "receipt.json", receipt)
        for path in staging.iterdir():
            sync_file(path)
        staging.rename(destination)
        return receipt
    finally:
        if staging.exists():
            if staging.resolve().parent != destination.parent.resolve() or not staging.name.startswith(".talklee-basebackup-"):
                raise RecoveryError("refusing cleanup outside the owned staging directory")
            shutil.rmtree(staging)


def verify_base_backup(base: Path) -> dict:
    if base.is_symlink() or not base.is_dir():
        raise RecoveryError("base backup must be a published directory")
    try:
        receipt = json.loads((base / "receipt.json").read_text(encoding="utf-8"))
        if receipt.get("operation") != "base_backup" or receipt.get("status") != "verified":
            raise RecoveryError("base backup receipt is not a successful published backup")
        files = receipt["files"]
        if set(files) != set(BASE_FILES) or {p.name for p in base.iterdir()} != set(BASE_FILES) | {"receipt.json"}:
            raise RecoveryError("base backup contents differ from its receipt")
        for name, info in files.items():
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise RecoveryError("base backup must contain regular files only")
            if path.stat().st_size != info["bytes"] or digest(path) != info["sha256"]:
                raise RecoveryError(f"base backup checksum mismatch for {name}; refusing to extract")
        return receipt
    except (KeyError, OSError, ValueError, TypeError) as exc:
        raise RecoveryError("base backup is incomplete or unreadable") from exc


def wal_segment_name(timeline: int, lsn: str, segment_bytes: int = 16 * 1024 * 1024) -> str:
    """The WAL segment file holding an LSN such as 0/2000028 (16 MB segments by default)."""
    match = re.fullmatch(r"([0-9A-Fa-f]{1,8})/([0-9A-Fa-f]{1,8})", lsn or "")
    if not match or not isinstance(timeline, int) or timeline < 1:
        raise RecoveryError("base backup receipt has an invalid start position")
    segment = ((int(match.group(1), 16) << 32) | int(match.group(2), 16)) // segment_bytes
    per_log = 0x100000000 // segment_bytes
    return f"{timeline:08X}{segment // per_log:08X}{segment % per_log:08X}"


def prune(base_root: Path, archive_dir: Path, keep: int, dry_run: bool) -> dict:
    """Keep the newest `keep` base backups and the WAL needed to replay from the
    oldest of them; delete older base backups and older WAL segments.

    Nothing is deleted unless the newest base backup verifies. Like
    pg_archivecleanup, segments are compared without their timeline, history
    and .backup files are kept, and unknown files and directories are never
    touched."""
    if keep < 1:
        raise RecoveryError("retention requires keep >= 1")
    base_root, archive_dir = base_root.resolve(strict=True), archive_dir.resolve(strict=True)
    published = []
    for path in base_root.iterdir():
        if path.name.startswith(".") or path.is_symlink() or not path.is_dir():
            continue
        try:
            receipt = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
            if receipt.get("operation") == "base_backup" and receipt.get("status") == "verified":
                published.append((datetime.fromisoformat(receipt["created_at"]), path, receipt))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not published:
        raise RecoveryError("no published base backups exist")
    published.sort(key=lambda item: item[0], reverse=True)
    verify_base_backup(published[0][1])
    kept, expired = published[:keep], published[keep:]
    oldest = kept[-1][2]
    boundary = wal_segment_name(oldest.get("timeline"), oldest.get("start_lsn"))
    old_segments = sorted(path.name for path in archive_dir.iterdir()
                          if path.is_file() and not path.is_symlink() and SEGMENT.fullmatch(path.name)
                          and path.name[8:] < boundary[8:])
    if not dry_run:
        for _, path, _ in expired:
            if path.resolve().parent != base_root:
                raise RecoveryError("refusing to delete outside the base backup root")
            shutil.rmtree(path)
        for name in old_segments:
            (archive_dir / name).unlink()
    return {"operation": "pitr_prune", "status": "passed", "checked_at": utcnow(), "dry_run": dry_run, "keep": keep,
            "kept": [path.name for _, path, _ in kept], "oldest_needed_segment": boundary,
            "deleted_base_backups" if not dry_run else "would_delete_base_backups": [path.name for _, path, _ in expired],
            "deleted_segments" if not dry_run else "would_delete_segments": len(old_segments)}


def safe_extract(tar_path: Path, dest: Path) -> int:
    """Extract regular files and directories only, all strictly inside dest."""
    root = dest.resolve()
    count = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            name = member.name
            parts = name.replace("\\", "/").split("/")
            if (not name or name.startswith(("/", "\\")) or ".." in parts or re.match(r"[A-Za-z]:", name)
                    or "\x00" in name):
                raise RecoveryError("archive member has an unsafe path")
            if not (member.isreg() or member.isdir()):
                raise RecoveryError("archive contains links or special files; refusing to extract")
            target = (root / name).resolve()
            if root != target and root not in target.parents:
                raise RecoveryError("archive member escapes the target directory")
        for member in members:
            tar.extract(member, root, filter="data")
            count += 1
    return count


def _poll_promoted(pg: recovery.Postgres, port: int, target: Path, deadline: float) -> None:
    # The server deletes recovery.signal when it promotes; pg_isready then
    # confirms it accepts connections. Neither needs a login, so this works
    # on a TLS-only, password-only copy without loosening its pg_hba.conf.
    args = ["-q", "-h", "127.0.0.1", "-p", str(port), "-d", "postgres", "-t", "5"]
    while time.monotonic() < deadline:
        if not (target / "recovery.signal").exists():
            code, _ = _exec(pg.executable("pg_isready"), args, clean_env(), 30)
            if code == 0:
                return
        time.sleep(0.5)
    raise RecoveryError("restored server did not finish recovery in time; inspect its log privately")


def restore(pg: recovery.Postgres, base: Path, archive_dir: Path, target: Path, *,
            target_time: datetime | None, port: int | None, start: bool, platform: str,
            wait_seconds: float, receipt_path: Path | None) -> dict:
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise RecoveryError("target data directory exists and is not empty; restore never overwrites")
    receipt_path = receipt_path or target.with_name(target.name + ".restore-receipt.json")
    if receipt_path.exists():
        raise RecoveryError("restore receipt already exists")
    if start and port is None:
        raise RecoveryError("--start requires --port so a restored copy cannot collide with production")
    if port is not None and not 1 <= port <= 65535:
        raise RecoveryError("invalid port")
    if not archive_dir.is_dir():
        raise RecoveryError("archive directory does not exist")
    base_receipt = verify_base_backup(base)  # before anything is written
    config = render_recovery_conf(archive_dir, platform, target_time, port)
    began, started_at = time.monotonic(), utcnow()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        safe_extract(base / "base.tar.gz", target)
        (target / "pg_wal").mkdir(exist_ok=True)
        safe_extract(base / "pg_wal.tar.gz", target / "pg_wal")
        if os.name != "nt":
            target.chmod(0o700)
        (target / "recovery.signal").write_bytes(b"")
        with (target / "postgresql.auto.conf").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(config)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    extract_seconds = round(time.monotonic() - began, 3)
    result = {"operation": "pitr_restore", "status": "prepared", "started_at": started_at,
              "mode": "target_time" if target_time else "latest",
              "target_time": target_time.isoformat() if target_time else None,
              "base_backup_sha256": base_receipt["files"]["base.tar.gz"]["sha256"],
              "base_backup_start_lsn": base_receipt["start_lsn"], "port": port,
              "extract_seconds": extract_seconds, "recovery_seconds": None, "started": False}
    if start:
        log = target.with_name(target.name + ".log")
        mark = time.monotonic()
        code, _ = _exec(pg.executable("pg_ctl"), ["-D", str(target), "-l", str(log), "-w", "-t",
                                                   str(int(wait_seconds)), "start"], clean_env(), wait_seconds + 30)
        if code:
            raise RecoveryError("pg_ctl could not start the restored server (recovery target may be "
                                "unreachable in the archive); inspect its log privately")
        _poll_promoted(pg, port, target, mark + wait_seconds)
        result.update(status="recovered", started=True, recovery_seconds=round(time.monotonic() - mark, 3))
    result.update(completed_at=utcnow(), elapsed_seconds=round(time.monotonic() - began, 3))
    write_json_new(receipt_path, result)
    return result


def archive_health(archive_dir: Path, max_age_minutes: float) -> dict:
    if max_age_minutes <= 0:
        raise RecoveryError("maximum age must be positive")
    if not archive_dir.is_dir():
        raise RecoveryError("archive directory does not exist")
    segments = [p for p in archive_dir.iterdir() if SEGMENT.fullmatch(p.name) and p.is_file()]
    if not segments:
        raise RecoveryError("no archived WAL segments exist")
    newest = max(segments, key=lambda p: p.stat().st_mtime)
    age = (time.time() - newest.stat().st_mtime) / 60
    if age > max_age_minutes:
        raise RecoveryError("newest archived WAL segment is stale; archiving may be failing")
    return {"operation": "archive_health", "status": "passed", "checked_at": utcnow(),
            "newest_segment": newest.name, "age_minutes": round(age, 2), "segments": len(segments)}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    a = commands.add_parser("archive-settings", help="print the postgresql.conf archiving lines")
    a.add_argument("--archive-dir", required=True)
    a.add_argument("--platform", choices=("linux", "windows"), default=default_platform())
    b = commands.add_parser("base-backup", help="publish a verified pg_basebackup")
    b.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    b.add_argument("--service", required=True)
    b.add_argument("--destination", type=Path, required=True)
    b.add_argument("--bin-dir", type=Path)
    b.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    r = commands.add_parser("restore", help="restore a base backup plus archive into a NEW data directory")
    r.add_argument("--base-backup", type=Path, required=True)
    r.add_argument("--archive-dir", type=Path, required=True)
    r.add_argument("--target-data-dir", type=Path, required=True)
    when = r.add_mutually_exclusive_group(required=True)
    when.add_argument("--target-time", help="ISO 8601 with UTC offset")
    when.add_argument("--target-latest", action="store_true")
    r.add_argument("--port", type=int)
    r.add_argument("--start", action="store_true")
    r.add_argument("--bin-dir", type=Path)
    r.add_argument("--platform", choices=("linux", "windows"), default=default_platform())
    r.add_argument("--wait-seconds", type=float, default=600)
    r.add_argument("--receipt", type=Path)
    h = commands.add_parser("archive-health", help="fail when the WAL archive is empty or stale")
    h.add_argument("--archive-dir", type=Path, required=True)
    h.add_argument("--max-age-minutes", type=float, required=True)
    k = commands.add_parser("prune", help="keep the newest base backups and the WAL they need")
    k.add_argument("--base-root", type=Path, required=True)
    k.add_argument("--archive-dir", type=Path, required=True)
    k.add_argument("--keep", type=int, default=7, help="base backups to keep (daily backups: the PITR window in days)")
    k.add_argument("--dry-run", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "archive-settings":
            print("\n".join(archive_settings(args.archive_dir, args.platform)))
            return 0
        if args.command == "base-backup":
            if args.service_file is None:
                raise RecoveryError("--service-file or PGSERVICEFILE is required")
            service = recovery.Service(Path(args.service_file), args.service, args.allow_local_plaintext)
            payload = base_backup(recovery.Postgres(args.bin_dir), service, args.destination)
        elif args.command == "restore":
            when = parse_target_time(args.target_time) if args.target_time else None
            payload = restore(recovery.Postgres(args.bin_dir), args.base_backup, args.archive_dir, args.target_data_dir,
                              target_time=when, port=args.port, start=args.start, platform=args.platform,
                              wait_seconds=args.wait_seconds, receipt_path=args.receipt)
        elif args.command == "prune":
            payload = prune(args.base_root, args.archive_dir, args.keep, args.dry_run)
        else:
            payload = archive_health(args.archive_dir, args.max_age_minutes)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (RecoveryError, OSError, configparser.Error, ValueError, tarfile.TarError) as exc:
        message = str(exc) if isinstance(exc, RecoveryError) else "local file/configuration operation failed; inspect protected inputs"
        print("PITR FAILED: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
