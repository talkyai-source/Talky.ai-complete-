#!/usr/bin/env python3
"""Read-only database health and capacity monitor with webhook/Prometheus output.

Reuses recovery.py's named-service connection rules (verify-full TLS, passwords
only through a protected password file). Output holds check names and numbers
only: never row contents, credentials, service-file contents or DB error text.
Exit codes: 0 ok, 1 warning, 2 critical, 3 tool/configuration error.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery  # noqa: E402

RANK = {"ok": 0, "warning": 1, "critical": 2}
NAMES = {0: "ok", 1: "warning", 2: "critical"}
XID_LIMIT = 2 ** 31

# One read-only round trip. Only counts, sizes and ages leave the database.
MONITOR_SQL = """
SELECT json_build_object(
 'ssl', (SELECT s.ssl FROM pg_stat_ssl s WHERE s.pid = pg_backend_pid()),
 'superuser', r.rolsuper, 'bypass_rls', r.rolbypassrls,
 'create_role', r.rolcreaterole, 'create_database', r.rolcreatedb,
 'owned_relations', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                      WHERE n.nspname = 'public' AND c.relowner = r.oid
                        AND c.relkind IN ('r','p','v','m','S','f')),
 'database_bytes', pg_database_size(current_database()),
 'connections', (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'),
 'max_connections', current_setting('max_connections')::int,
 'oldest_xact_seconds', (SELECT max(extract(epoch FROM now() - xact_start)) FROM pg_stat_activity
                          WHERE pid <> pg_backend_pid() AND xact_start IS NOT NULL
                            AND backend_type = 'client backend'),
 'max_xid_age', (SELECT max(age(datfrozenxid)) FROM pg_database),
 'archive_mode', current_setting('archive_mode'),
 'in_recovery', pg_is_in_recovery(),
 'wal_segment_bytes', pg_size_bytes(current_setting('wal_segment_size')),
 'current_wal', CASE WHEN pg_is_in_recovery() THEN NULL ELSE pg_walfile_name(pg_current_wal_lsn()) END,
 'last_archived_wal', a.last_archived_wal,
 'failed_count', a.failed_count,
 'last_archived_age_seconds', extract(epoch FROM now() - a.last_archived_time),
 'failing', (a.last_failed_time IS NOT NULL AND (a.last_archived_time IS NULL OR a.last_failed_time > a.last_archived_time))
) FROM pg_roles r, pg_stat_archiver a WHERE r.rolname = current_user;
"""


def check(name: str, status: str, value, threshold, detail: str) -> dict:
    return {"name": name, "status": status, "value": value, "threshold": threshold, "detail": detail}


def classify(value: float, warning: float, critical: float) -> str:
    """Higher is worse; thresholds are inclusive lower bounds."""
    return "critical" if value >= critical else "warning" if value >= warning else "ok"


def worst(checks: list[dict]) -> str:
    return NAMES[max((RANK[c["status"]] for c in checks), default=0)]


def segment_number(name, per_log: int):
    if not isinstance(name, str) or len(name) < 24:
        return None
    try:
        return int(name[8:16], 16) * per_log + int(name[16:24], 16)
    except ValueError:
        return None


def evaluate(data: dict, latency_ms: float, o) -> list[dict]:
    """Turn one database snapshot into checks. `o` carries the option values."""
    out = [check("connect", "warning" if latency_ms > o.latency_warning_ms else "ok", round(latency_ms, 1),
                 {"warning_ms": o.latency_warning_ms}, "round trip including client start-up")]
    ssl = bool(data.get("ssl"))
    out.append(check("tls", "ok" if ssl or o.local_plaintext else "critical", ssl, "ssl required",
                     "encrypted session" if ssl else "plaintext loopback fixture" if o.local_plaintext
                     else "session is not encrypted"))
    if o.skip_role_check:
        out.append(check("restricted_role", "ok", None, "skipped", "role check skipped for an admin/backup service"))
    else:
        flags = [k for k in ("superuser", "bypass_rls", "create_role", "create_database") if data.get(k)]
        owned = int(data.get("owned_relations") or 0)
        bad = bool(flags) or owned > 0
        out.append(check("restricted_role", "critical" if bad else "ok", {"privileges": flags, "owned_public_relations": owned},
                         "no superuser/bypassrls/createrole/createdb and no owned public relations",
                         "role is over-privileged or owns relations" if bad else "role is restricted"))
    size = int(data["database_bytes"])
    ratio = size / o.capacity_bytes
    out.append(check("database_size", classify(ratio, o.capacity_warning, o.capacity_critical),
                     {"bytes": size, "capacity_bytes": o.capacity_bytes, "ratio": round(ratio, 4)},
                     {"warning": o.capacity_warning, "critical": o.capacity_critical},
                     "database size against provisioned storage"))
    used, limit = int(data["connections"]), int(data["max_connections"])
    cr = used / limit if limit else 1.0
    out.append(check("connections", classify(cr, o.connections_warning, o.connections_critical),
                     {"count": used, "max_connections": limit, "ratio": round(cr, 4)},
                     {"warning": o.connections_warning, "critical": o.connections_critical}, "client backends against max_connections"))
    oldest = data.get("oldest_xact_seconds")
    age = float(oldest) if oldest is not None else 0.0
    out.append(check("long_transactions", classify(age, o.long_tx_warning_seconds, o.long_tx_critical_seconds), round(age, 1),
                     {"warning_seconds": o.long_tx_warning_seconds, "critical_seconds": o.long_tx_critical_seconds},
                     "oldest open transaction age (other sessions)"))
    xid = int(data["max_xid_age"])
    xr = xid / XID_LIMIT
    out.append(check("wraparound", classify(xr, o.wraparound_warning, o.wraparound_critical),
                     {"max_xid_age": xid, "ratio": round(xr, 4)},
                     {"warning": o.wraparound_warning, "critical": o.wraparound_critical}, "transaction id age against 2^31"))
    out.append(archive_check(data, o))
    return out


def archive_check(data: dict, o) -> dict:
    limit = {"max_age_minutes": o.archive_max_age_minutes}
    if data.get("archive_mode") not in ("on", "always"):
        if o.require_archiving:
            return check("wal_archiving", "critical", "off", "archiving required", "archive_mode is off but archiving is required")
        return check("wal_archiving", "ok", "off", limit, "not configured (provider-managed PITR or disabled)")
    if data.get("failing"):
        return check("wal_archiving", "critical", {"failed_count": int(data.get("failed_count") or 0)}, limit,
                     "archive command is failing since the last success")
    age = data.get("last_archived_age_seconds")
    segment_bytes = data.get("wal_segment_bytes")
    if o.require_archiving and (
        not isinstance(segment_bytes, int) or isinstance(segment_bytes, bool) or segment_bytes <= 0
        or age is None or not isinstance(age, (int, float)) or not math.isfinite(age) or age < -60
    ):
        return check("wal_archiving", "critical", None, limit, "required WAL archive evidence is unavailable or invalid")
    per_log = max(1, 0x100000000 // int(segment_bytes or 16 * 1024 * 1024))
    current, last = segment_number(data.get("current_wal"), per_log), segment_number(data.get("last_archived_wal"), per_log)
    if o.require_archiving and (current is None or last is None or current < last):
        return check("wal_archiving", "critical", None, limit, "required WAL positions are unavailable or invalid")
    # Segments are only archived when full; one open segment is not a backlog.
    backlog = 0 if current is None else (current - last) if last is not None else current + 1
    stale = age is None or float(age) > o.archive_max_age_minutes * 60
    if stale and backlog > 1:
        return check("wal_archiving", "warning", {"backlog_segments": backlog, "age_seconds": None if age is None else round(float(age))},
                     limit, "WAL is being generated but nothing has been archived recently")
    return check("wal_archiving", "ok", {"backlog_segments": max(backlog, 0)}, limit, "archiving is current")


def backup_check(root: Path | None, max_hours: float) -> dict | None:
    if root is None:
        return None
    try:
        result = recovery.health(root, max_hours)
    except (recovery.RecoveryError, OSError):
        return check("backup_freshness", "critical", None, {"max_age_hours": max_hours}, "latest backup missing, stale or failed integrity check")
    return check("backup_freshness", "ok", result["age_hours"], {"max_age_hours": max_hours}, "latest backup is fresh and verified")


def options_ns(args, local_plaintext: bool):
    return argparse.Namespace(**{**vars(args), "local_plaintext": local_plaintext})


def collect(pg, service, args) -> tuple[dict, float]:
    started = time.monotonic()
    data = pg.json(service, MONITOR_SQL)
    return data, (time.monotonic() - started) * 1000


def failed_report(args, latency_ms: float | None = None) -> list[dict]:
    return [check("connect", "critical", None, {"warning_ms": args.latency_warning_ms}, "database connection or monitor query failed")]


def build_report(checks: list[dict], service_name: str) -> dict:
    return {"operation": "monitor", "service": service_name, "checked_at": recovery.utcnow(),
            "status": worst(checks), "checks": checks}


def decide_alert(previous: dict | None, status: str, now: float, realert_minutes: float) -> str | None:
    """Return 'alert', 'resolved' or None. Pure state machine, no I/O."""
    prev = (previous or {}).get("status", "ok")
    if prev not in RANK:
        prev = "ok"
    if status == "ok":
        return "resolved" if prev != "ok" else None
    if prev == "ok" or RANK[status] > RANK[prev]:
        return "alert"
    last = (previous or {}).get("last_alert_at")
    if not isinstance(last, (int, float)) or now - last >= realert_minutes * 60:
        return "alert"
    return None


def alert_payload(kind: str, report: dict) -> dict:
    bad = [c for c in report["checks"] if c["status"] != "ok"]
    if kind == "resolved":
        text = f"RESOLVED: Talklee database monitor ({report['service']}) is back to ok"
    else:
        lines = "; ".join(f"{c['name']}={c['status']} ({c['detail']})" for c in bad)
        text = f"{report['status'].upper()}: Talklee database monitor ({report['service']}): {lines}"
    summary = [{"name": c["name"], "status": c["status"], "value": c["value"], "threshold": c["threshold"]} for c in report["checks"]]
    return {"text": text, "talklee": {"status": report["status"], "kind": kind, "service": report["service"],
                                       "checked_at": report["checked_at"], "checks": summary}}


def post_webhook(url: str, payload: dict, timeout: float = 10) -> None:
    if not url.lower().startswith(("https://", "http://")):
        raise recovery.RecoveryError("alert webhook must be an http(s) URL")
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise recovery.RecoveryError("alert webhook rejected the message")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise recovery.RecoveryError("alert webhook could not be reached") from exc


def read_state(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def prometheus_text(report: dict, up: bool) -> str:
    lines = ["# HELP talklee_db_up 1 if the monitor could query the database.", "# TYPE talklee_db_up gauge",
             f"talklee_db_up {1 if up else 0}",
             "# HELP talklee_db_check_status Check status: 0 ok, 1 warning, 2 critical.", "# TYPE talklee_db_check_status gauge"]
    lines += [f'talklee_db_check_status{{check="{c["name"]}"}} {RANK[c["status"]]}' for c in report["checks"]]
    by = {c["name"]: c["value"] for c in report["checks"]}
    size, conns = by.get("database_size"), by.get("connections")
    if isinstance(size, dict):
        lines += ["# TYPE talklee_db_size_bytes gauge", f"talklee_db_size_bytes {size['bytes']}",
                  "# TYPE talklee_db_capacity_ratio gauge", f"talklee_db_capacity_ratio {size['ratio']}"]
    if isinstance(conns, dict):
        lines += ["# TYPE talklee_db_connections gauge", f"talklee_db_connections {conns['count']}"]
    return "\n".join(lines) + "\n"


def handle_alerts(report: dict, args, now: float) -> None:
    url = args.alert_webhook_url or os.environ.get("TALKLEE_ALERT_WEBHOOK_URL")
    if not url:
        return
    state = read_state(args.state_file) if args.state_file else None
    kind = decide_alert(state, report["status"], now, args.realert_minutes)
    if kind is None:
        if args.state_file and report["status"] == "ok":
            atomic_write(args.state_file, json.dumps({"status": "ok", "last_alert_at": (state or {}).get("last_alert_at")}))
        return
    try:
        post_webhook(url, alert_payload(kind, report))
    except recovery.RecoveryError as exc:
        # Keep the previous state so the next run retries; the DB status still drives the exit code.
        print(f"Alert delivery failed: {exc}", file=sys.stderr)
        return
    if args.state_file:
        atomic_write(args.state_file, json.dumps({"status": report["status"], "last_alert_at": now}))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    result.add_argument("--service", required=True)
    result.add_argument("--bin-dir", type=Path, help="directory containing PostgreSQL client executables")
    result.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    commands = result.add_subparsers(dest="command", required=True)
    c = commands.add_parser("check", help="run all health and capacity checks once")
    c.add_argument("--capacity-bytes", type=int, required=True, help="provisioned storage of the database server/plan")
    c.add_argument("--capacity-warning", type=float, default=0.80)
    c.add_argument("--capacity-critical", type=float, default=0.90)
    c.add_argument("--connections-warning", type=float, default=0.80)
    c.add_argument("--connections-critical", type=float, default=0.95)
    c.add_argument("--latency-warning-ms", type=float, default=250)
    c.add_argument("--long-tx-warning-seconds", type=float, default=300)
    c.add_argument("--long-tx-critical-seconds", type=float, default=1800)
    c.add_argument("--wraparound-warning", type=float, default=0.50)
    c.add_argument("--wraparound-critical", type=float, default=0.75)
    c.add_argument("--archive-max-age-minutes", type=float, default=30)
    c.add_argument("--require-archiving", action="store_true")
    c.add_argument("--skip-role-check", action="store_true")
    c.add_argument("--backup-root", type=Path)
    c.add_argument("--backup-max-age-hours", type=float, default=26)
    c.add_argument("--receipt", type=Path, help="write a new JSON receipt (refuses to overwrite)")
    c.add_argument("--prometheus-textfile", type=Path)
    c.add_argument("--alert-webhook-url", default=None)
    c.add_argument("--state-file", type=Path, help="alert state, atomically overwritten each run")
    c.add_argument("--realert-minutes", type=float, default=60)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.capacity_bytes <= 0 or not 0 < args.capacity_warning < args.capacity_critical <= 1:
            raise recovery.RecoveryError("capacity bytes must be positive and thresholds ordered within (0, 1]")
        if args.service_file is None:
            raise recovery.RecoveryError("a service file is required")
        service = recovery.Service(Path(args.service_file), args.service, args.allow_local_plaintext)
        pg = recovery.Postgres(args.bin_dir, timeout=60)
        pg.executable("psql")
        up = True
        try:
            data, latency = collect(pg, service, args)
            checks = evaluate(data, latency, options_ns(args, service.local_plaintext))
        except (recovery.RecoveryError, KeyError, TypeError, ValueError, ZeroDivisionError):
            up, checks = False, failed_report(args)
        backup = backup_check(args.backup_root, args.backup_max_age_hours)
        if backup:
            checks.append(backup)
        report = build_report(checks, service.name)
        print(json.dumps(report, indent=2, sort_keys=True))
        code = RANK[report["status"]]
        try:
            if args.prometheus_textfile:
                atomic_write(args.prometheus_textfile, prometheus_text(report, up))
            if args.receipt:
                recovery.write_json_new(args.receipt, report)
        except OSError:
            print("Could not write receipt or Prometheus file; check paths and permissions.", file=sys.stderr)
            code = 3
        handle_alerts(report, args, time.time())
        return code
    except (recovery.RecoveryError, OSError) as exc:
        print(f"Monitor configuration error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
