#!/usr/bin/env python3
"""Cut-over preflight: is the new database server ready, days before the window?

Read-only on both sides. Run it from the application server against the new
server's administrator service and, optionally, today's database (the source).
Every check is ok, warning or fail; any fail exits 1, so a problem is found
during preparation instead of halfway through the maintenance window.

  target   version, TLS, administrator rights, connection capacity against the
           application's pool budget, required extensions (trusted for a
           non-superuser administrator), encoding/collation/time zone,
           target database not yet present, round-trip latency
  source   version, size against the planned capacity, extensions, row-level
           security that would change once the runtime role stops owning
           tables (and that roles.py verify would reject mid-window)
  client   pg_dump/pg_restore on this host are new enough for the source

  python3 scripts/database/preflight.py --service-file /etc/talklee/secrets/pg_service.conf \\
      --target-service talklee_admin --source-service talklee_source --allow-local-plaintext \\
      --database talkyai --capacity-bytes 85899345920 --receipt /var/backups/talklee-evidence/preflight.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import statistics
import sys

import recovery
from recovery import RecoveryError, identifier

SUPPORTED_MAJORS = (15, 16, 17)
DEFAULT_EXTENSIONS = ("pgcrypto", "uuid-ossp", "pg_trgm")
DEFAULT_SYSTEMD_DIR = Path(__file__).resolve().parents[2] / "backend" / "systemd"
POOL_SETTING = re.compile(r"^Environment=PG_POOL_MAX_SIZE=(\d+)\s*$", re.M)
TIMING = re.compile(r"^Time: ([0-9]+(?:\.[0-9]+)?) ms", re.M)

TARGET_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'server_version_num', current_setting('server_version_num')::int,
 'server_version', current_setting('server_version'),
 'tls', (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
 'tls_version', (SELECT version FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
 'superuser', (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
 'create_role', (SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user),
 'create_database', (SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user),
 'max_connections', current_setting('max_connections')::int,
 'superuser_reserved_connections', current_setting('superuser_reserved_connections')::int,
 'reserved_connections', COALESCE(NULLIF(current_setting('reserved_connections', true), ''), '0')::int,
 'connections_in_use', (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'),
 'encoding', (SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = 'template1'),
 'collate', (SELECT datcollate FROM pg_database WHERE datname = 'template1'),
 'ctype', (SELECT datctype FROM pg_database WHERE datname = 'template1'),
 'timezone', current_setting('TimeZone'),
 'databases', (SELECT json_agg(datname ORDER BY datname) FROM pg_database),
 'roles', (SELECT COALESCE(json_agg(rolname ORDER BY rolname), '[]'::json) FROM pg_roles WHERE rolname IN (__ROLES__)),
 'extensions', (SELECT json_object_agg(name, trusted) FROM (
     SELECT name, bool_or(trusted) AS trusted FROM pg_available_extension_versions GROUP BY name) e)
);
ROLLBACK;
"""

SOURCE_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'server_version_num', current_setting('server_version_num')::int,
 'database_bytes', pg_database_size(current_database()),
 'encoding', (SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()),
 'collate', (SELECT datcollate FROM pg_database WHERE datname = current_database()),
 'ctype', (SELECT datctype FROM pg_database WHERE datname = current_database()),
 'timezone', current_setting('TimeZone'),
 'extensions', (SELECT COALESCE(json_agg(extname ORDER BY extname), '[]'::json) FROM pg_extension WHERE extname <> 'plpgsql'),
 'tables', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')),
 'rls_not_forced', (SELECT COALESCE(json_agg(c.relname ORDER BY c.relname), '[]'::json)
     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relrowsecurity AND NOT c.relforcerowsecurity),
 'tenant_tables_without_rls', (SELECT COALESCE(json_agg(c.relname ORDER BY c.relname), '[]'::json)
     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity
       AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped))
);
ROLLBACK;
"""


def _check(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


def _major(version_num: int) -> int:
    return version_num // 10000


def _locale(value: str | None) -> str:
    """en_US.UTF-8, en_US.utf8 and en_US.UTF8 name the same libc locale."""
    return re.sub(r"[-_.]", "", (value or "").lower())


def pool_budget(systemd_dir: Path) -> dict[str, int]:
    """PG_POOL_MAX_SIZE of every Talklee unit that sets one (the long-running services)."""
    budget = {}
    for unit in sorted(systemd_dir.glob("talky-*.service")):
        match = POOL_SETTING.search(unit.read_text(encoding="utf-8"))
        if match:
            budget[unit.name] = int(match.group(1))
    if not budget:
        raise RecoveryError("no unit in the systemd directory declares PG_POOL_MAX_SIZE; pass --pool-budget")
    return budget


def latency(samples: list[float]) -> dict:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"samples": len(ordered), "p50_ms": round(statistics.median(ordered), 3), "p95_ms": round(p95, 3),
            "max_ms": round(ordered[-1], 3)}


def measure_latency(pg: recovery.Postgres, service: recovery.Service, samples: int) -> dict:
    """Client-side round trip of SELECT 1 on one open connection (psql \\timing),
    so connection setup and TLS handshakes are excluded, network is not."""
    if not 5 <= samples <= 1000:
        raise RecoveryError("latency samples must be between 5 and 1000")
    probe = recovery.ServiceOverride(service, LC_ALL="C", LC_MESSAGES="C")
    output = pg.run("psql", ["-X", "-At", "--no-password", "--set", "ON_ERROR_STOP=1", "--dbname", probe.connection()],
                    probe, sql="\\timing on\n" + "SELECT 1;\n" * samples)
    timings = [float(value) for value in TIMING.findall(output)]
    if len(timings) != samples:
        raise RecoveryError("psql did not report a timing for every latency sample")
    return latency(timings)


def evaluate(target: dict, source: dict | None, round_trip: dict | None, client_major: int | None, *,
             database: str, budget: int, headroom: int, required_extensions: list[str],
             capacity_bytes: int | None, max_p95_ms: float) -> tuple[dict, dict]:
    checks: dict[str, dict] = {}
    target_major = _major(target["server_version_num"])

    if target_major not in SUPPORTED_MAJORS:
        checks["target_version"] = _check("fail", f"PostgreSQL {target['server_version']}; supported majors are "
                                                  f"{', '.join(map(str, SUPPORTED_MAJORS))}")
    elif source and target_major < _major(source["server_version_num"]):
        checks["target_version"] = _check("fail", f"target {target_major} is older than the source "
                                                  f"{_major(source['server_version_num'])}; pg_restore cannot go backwards")
    else:
        checks["target_version"] = _check("ok", f"PostgreSQL {target['server_version']}")

    checks["target_tls"] = (_check("ok", f"connected with {target['tls_version']} and a verified certificate")
                            if target["tls"] else _check("fail", "the administrator connection is not encrypted"))

    if target["superuser"]:
        checks["administrator"] = _check("warning", "administrator is a superuser; it works, but the cut-over was "
                                                    "designed for and rehearsed with a CREATEROLE/CREATEDB administrator")
    elif target["create_role"] and target["create_database"]:
        checks["administrator"] = _check("ok", "administrator can create roles and databases (not a superuser)")
    else:
        checks["administrator"] = _check("fail", "administrator needs CREATEROLE and CREATEDB to provision roles and the database")

    reserved = target["superuser_reserved_connections"] + target["reserved_connections"]
    available = target["max_connections"] - reserved
    required = budget + headroom
    detail = (f"{available} usable connections (max_connections {target['max_connections']} minus {reserved} reserved); "
              f"application pools {budget} + headroom {headroom} (timers, backup, monitor, migrations, operators) = {required}")
    if required > available:
        checks["connection_capacity"] = _check("fail", detail + "; choose a larger plan or raise max_connections")
    elif required > 0.8 * available:
        checks["connection_capacity"] = _check("warning", detail + "; under 20 % spare for growth")
    else:
        checks["connection_capacity"] = _check("ok", detail)
    recommendations = {"runtime_connection_limit": max(1, available - max(headroom // 2, 5))}

    available_extensions = target.get("extensions") or {}
    missing = sorted(name for name in required_extensions if name not in available_extensions)
    untrusted = sorted(name for name in required_extensions
                       if name in available_extensions and not available_extensions[name] and not target["superuser"])
    if missing:
        checks["extensions"] = _check("fail", "not available on the target: " + ", ".join(missing))
    elif untrusted:
        checks["extensions"] = _check("fail", "a non-superuser administrator cannot create these untrusted extensions "
                                              "(ask the provider to enable them): " + ", ".join(untrusted))
    else:
        checks["extensions"] = _check("ok", "available: " + ", ".join(sorted(required_extensions)))

    if target["encoding"] != "UTF8":
        checks["encoding_and_collation"] = _check("fail", f"new databases would use {target['encoding']}, not UTF8")
    elif source and (_locale(target["collate"]) != _locale(source["collate"])
                     or _locale(target["ctype"]) != _locale(source["ctype"])):
        checks["encoding_and_collation"] = _check(
            "warning", f"collation differs (source {source['collate']}/{source['ctype']}, target "
                       f"{target['collate']}/{target['ctype']}): indexes are rebuilt on restore, but text ORDER BY may change")
    else:
        checks["encoding_and_collation"] = _check("ok", f"UTF8, {target['collate']}")

    if source and target["timezone"] != source["timezone"]:
        checks["time_zone"] = _check("warning", f"server TimeZone {target['timezone']} differs from the source "
                                                f"{source['timezone']}; timestamptz values are unaffected, text rendering is not")
    else:
        checks["time_zone"] = _check("ok", f"TimeZone {target['timezone']}")

    checks["target_database_absent"] = (
        _check("fail", f"database {database} already exists on the target; cutover.py loads only into a new database")
        if database in (target.get("databases") or []) else _check("ok", f"database {database} does not exist yet"))
    if target.get("roles"):
        checks["existing_roles"] = _check("warning", "roles already exist and will be verified, not altered: "
                                                     + ", ".join(target["roles"]))

    if round_trip:
        detail = f"p50 {round_trip['p50_ms']} ms, p95 {round_trip['p95_ms']} ms over {round_trip['samples']} queries"
        if round_trip["p95_ms"] > max_p95_ms:
            checks["round_trip_latency"] = _check("fail", detail + f"; above {max_p95_ms} ms. Calls make several queries "
                                                                   "per turn: keep the database in the same data centre")
        elif round_trip["p95_ms"] > max_p95_ms / 2:
            checks["round_trip_latency"] = _check("warning", detail)
        else:
            checks["round_trip_latency"] = _check("ok", detail)

    if source:
        source_major = _major(source["server_version_num"])
        if client_major is not None:
            checks["client_tools"] = (
                _check("fail", f"pg_dump {client_major} on this host cannot dump a PostgreSQL {source_major} source")
                if client_major < source_major else
                _check("ok", f"pg_dump/pg_restore {client_major} on this host")
                if client_major >= target_major else
                _check("warning", f"pg_dump {client_major} is older than the target {target_major}; install the "
                                  f"{target_major} client for monitoring and base backups"))
        if capacity_bytes:
            share = source["database_bytes"] / capacity_bytes
            detail = f"source database {source['database_bytes']} bytes is {share:.0%} of the planned {capacity_bytes} bytes"
            checks["storage_capacity"] = _check("fail" if share >= 0.5 else "warning" if share >= 0.3 else "ok",
                                                detail + ("; leave room for growth, WAL and index builds" if share >= 0.3 else ""))
        drift = list(source.get("rls_not_forced") or [])
        if drift:
            checks["row_level_security"] = _check(
                "fail", "row-level security is on but not forced for: " + ", ".join(drift[:20]) + ". Today the "
                        "owning role skips these policies; the restricted runtime role will not, and roles.py verify "
                        "rejects tenant tables without FORCE. Force it (as migration 0013 does) and re-test first")
        else:
            checks["row_level_security"] = _check("ok", "every table with row-level security has it forced, so the "
                                                        "runtime role sees exactly what today's role sees")
        if source.get("tenant_tables_without_rls"):
            checks["tenant_tables_without_rls"] = _check(
                "warning", "tenant tables with no row-level security (isolation relies on query predicates only): "
                           + ", ".join(source["tenant_tables_without_rls"][:20]))
    elif capacity_bytes:
        checks["storage_capacity"] = _check("warning", "no source given; storage was not compared")
    return checks, recommendations


def target_sql(owner: str, runtime: str, backup: str) -> str:
    """The managed roles only: other roles on the server (a replication role) are not ours to verify."""
    names = ", ".join("'" + identifier(name)[1:-1] + "'" for name in (owner, runtime, backup))
    return TARGET_SQL.replace("__ROLES__", names)


def client_version(pg: recovery.Postgres) -> int | None:
    match = re.search(r"(\d+)(?:\.\d+)?", pg.run("pg_dump", ["--version"]))
    return int(match.group(1)) if match else None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--service-file", type=Path, default=os.environ.get("PGSERVICEFILE"))
    result.add_argument("--bin-dir", type=Path)
    result.add_argument("--allow-local-plaintext", action="store_true",
                        help="permit today's loopback source over plaintext (the target is always checked for TLS)")
    result.add_argument("--target-service", required=True, help="new server's administrator service (dbname=postgres)")
    result.add_argument("--source-service", help="today's database, read-only (e.g. talklee_source)")
    result.add_argument("--database", default="talkyai", help="database name the cut-over will create")
    result.add_argument("--owner", default="talklee_owner")
    result.add_argument("--runtime", default="talklee_app")
    result.add_argument("--backup", default="talklee_backup")
    budget = result.add_mutually_exclusive_group()
    budget.add_argument("--pool-budget", type=int, help="sum of the application pools' PG_POOL_MAX_SIZE")
    budget.add_argument("--systemd-dir", type=Path, default=DEFAULT_SYSTEMD_DIR,
                        help="read the pool budget from the talky-*.service units")
    result.add_argument("--headroom", type=int, default=20,
                        help="connections kept for timers, backup, monitor, migrations and operators")
    result.add_argument("--require-extension", action="append", default=[],
                        help="extension the schema needs (default: the source's, else pgcrypto, uuid-ossp, pg_trgm)")
    result.add_argument("--latency-samples", type=int, default=50)
    result.add_argument("--max-p95-ms", type=float, default=2.0)
    result.add_argument("--capacity-bytes", type=int, help="the target plan's storage, to compare with the source size")
    result.add_argument("--receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        identifier(args.database)
        if args.service_file is None:
            raise RecoveryError("--service-file or PGSERVICEFILE is required")
        if args.receipt.exists() or args.receipt.is_symlink():
            raise RecoveryError("receipt already exists")
        if args.headroom < 0 or (args.pool_budget is not None and args.pool_budget < 1):
            raise RecoveryError("pool budget must be positive and headroom not negative")
        pg = recovery.Postgres(args.bin_dir, timeout=300)
        target_service = recovery.Service(args.service_file, args.target_service, args.allow_local_plaintext)
        target = pg.json(target_service, target_sql(args.owner, args.runtime, args.backup))
        source = None
        if args.source_service:
            source = pg.json(recovery.Service(args.service_file, args.source_service, args.allow_local_plaintext),
                             SOURCE_SQL)
        units = {"--pool-budget": args.pool_budget} if args.pool_budget else pool_budget(args.systemd_dir)
        required_extensions = args.require_extension or (source["extensions"] if source else list(DEFAULT_EXTENSIONS))
        round_trip = measure_latency(pg, target_service, args.latency_samples)
        checks, recommendations = evaluate(
            target, source, round_trip, client_version(pg) if source else None, database=args.database,
            budget=sum(units.values()), headroom=args.headroom, required_extensions=required_extensions,
            capacity_bytes=args.capacity_bytes, max_p95_ms=args.max_p95_ms)
        statuses = {check["status"] for check in checks.values()}
        payload = {"operation": "preflight", "observed_at": recovery.utcnow(),
                   "status": "failed" if "fail" in statuses else "passed_with_warnings" if "warning" in statuses else "passed",
                   "checks": checks, "recommendations": recommendations, "pool_budget": units,
                   "observations": {"target": target, "source": source, "round_trip": round_trip},
                   "limits": ["read-only checks; the rehearsal load (cutover.py into a scratch database) measures the "
                              "real duration", "provider-side backups, PITR and alerts are confirmed in the provider console"]}
        recovery.write_json_new(args.receipt, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if payload["status"] == "failed" else 0
    except (RecoveryError, OSError, ValueError, KeyError) as exc:
        message = str(exc) if isinstance(exc, RecoveryError) else "local file/configuration operation failed; inspect protected inputs"
        print("PREFLIGHT FAILED: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
