"""Compare supported bootstrap schemas in two owned disposable databases.

Credentials come only from SCHEMA_PARITY_ADMIN_DSN (or TEST_DATABASE_URL).
No production target, password-bearing command argument, .env loading, source
import, manual SQL replay or schema-difference allowlist is used.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from uuid import uuid4


class ParityError(RuntimeError):
    """A sanitized provisioning/restore/migration/dump failure."""


_TEST_NAME = re.compile(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", re.I)
_OWNED_NAME = re.compile(r"qa_schema_parity_[a-f0-9]{32}_(?:clean|lineage)\Z")
_DUMP_VERSION = re.compile(r"^-- Dumped (?:from database|by pg_dump) version .+\s*$")
_RESTRICT = re.compile(r"^\\restrict ([A-Za-z0-9_]+)\s*$")
_UNRESTRICT = re.compile(r"^\\unrestrict ([A-Za-z0-9_]+)\s*$")
# Since 12 Nov 2026 the CI database (PostgreSQL 15.20) dumps a per-column
# ``pg_attver`` option whose value is a per-database counter, so a clean and a
# migrated database always differ there. Only the number of a line that is
# exactly this option is masked; the line itself, and any other option, stay.
_ATTVER_LINE = re.compile(r"^(ALTER TABLE ONLY \S+ ALTER COLUMN \S+ SET \(pg_attver=)\d+(\);\s*)$")
_SSL_ENV = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT",
            "sslkey": "PGSSLKEY", "sslcrl": "PGSSLCRL"}


def normalize_dump(value: str) -> str:
    """Remove only volatile dump metadata; SQL/literal/security bytes remain."""
    lines = value.splitlines(keepends=True)
    excluded = set()
    opening_guard = None
    # pg_dump emits version comments and its opening guard before any SQL.
    # Stop at the first SQL line: metadata-looking text later can be a real
    # function/literal value, so scanning and stripping the whole dump is unsafe.
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("--"):
            if _DUMP_VERSION.fullmatch(line):
                excluded.add(index)
            continue
        match = _RESTRICT.fullmatch(line)
        if match and opening_guard is None:
            opening_guard = (index, match.group(1))
            continue
        break
    last = next((index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()), None)
    if opening_guard is not None and last is not None:
        closing_guard = _UNRESTRICT.fullmatch(lines[last])
        if closing_guard and closing_guard.group(1) == opening_guard[1]:
            excluded.update((opening_guard[0], last))
    return "".join(_ATTVER_LINE.sub(r"\g<1><attver>\g<2>", line)
                   for index, line in enumerate(lines) if index not in excluded)


def compare_dumps(clean: str, lineage: str) -> str:
    return "".join(difflib.unified_diff(
        normalize_dump(clean).splitlines(keepends=True),
        normalize_dump(lineage).splitlines(keepends=True),
        fromfile="clean-schema.sql", tofile="lineage-schema.sql",
    ))


def _connection(value: str):
    try:
        parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
        if parsed.scheme != "postgresql" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError
        if not parsed.username or not _TEST_NAME.search(unquote(parsed.path.lstrip("/"))) or parsed.fragment:
            raise ValueError
        port = parsed.port or 5432
        options = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if any(key not in _SSL_ENV or not val for key, val in options):
            # asyncpg/libpq DSN query keys can override host/database/user.
            raise ValueError
        if len({key for key, _value in options}) != len(options):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ParityError("A loopback, test-named PostgreSQL provisioning DSN without routing overrides is required") from None
    environment = {key: val for key, val in os.environ.items() if not key.startswith("PG")}
    environment.update(PGHOST=parsed.hostname, PGPORT=str(port), PGDATABASE=unquote(parsed.path.lstrip("/")),
                       PGUSER=unquote(parsed.username), PGPASSWORD=unquote(parsed.password or ""),
                       PGCONNECT_TIMEOUT="5")
    for key, val in options:
        environment[_SSL_ENV[key]] = val
    return parsed, environment


def _run(arguments, *, env, cwd, runner, stage):
    try:
        result = runner(arguments, env=env, cwd=cwd, capture_output=True, text=True,
                        timeout=300, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        raise ParityError(f"Schema parity {stage} failed; no credentials or subprocess output are printed") from None
    if result.returncode:
        raise ParityError(f"Schema parity {stage} failed (exit {result.returncode}); inspect the disposable environment privately")
    return result.stdout


def run_parity(admin_dsn: str, *, backend_root: Path, output_dir: Path, runner=subprocess.run,
               psql="psql", pg_dump="pg_dump") -> dict:
    parsed, admin_env = _connection(admin_dsn)
    backend = Path(backend_root).resolve()
    output = Path(output_dir).resolve()
    snapshots = {"clean": backend / "database" / "complete_schema.sql",
                 "lineage": backend / "database" / "schema" / "baseline_2026-06-02.sql"}
    if any(not path.is_file() for path in snapshots.values()) or not (backend / "alembic.ini").is_file():
        raise ParityError("The repository bootstrap snapshots and Alembic config are required")
    output.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    (output / "invocation.json").write_text(json.dumps({
        "databases": [f"qa_schema_parity_{token}_{route}" for route in snapshots],
        "cleanup_policy": "Only successful CREATE identities; no FORCE",
    }, indent=2) + "\n", encoding="utf-8")
    created, dumps = [], {}
    failure = None
    cleanup_failed = False
    try:
        for route, snapshot in snapshots.items():
            name = f"qa_schema_parity_{token}_{route}"
            if not _OWNED_NAME.fullmatch(name):
                raise ParityError("Invalid generated database identity")
            _run([psql, "-X", "-v", "ON_ERROR_STOP=1", "-c", f'CREATE DATABASE "{name}"'],
                 env=admin_env, cwd=backend, runner=runner, stage="database creation")
            created.append(name)
            environment = dict(admin_env, PGDATABASE=name)
            target = urlunsplit(parsed._replace(path=f"/{name}"))
            environment.update(DATABASE_URL=target, MIGRATION_DATABASE_URL=target)
            _run([psql, "-X", "-v", "ON_ERROR_STOP=1", "-f", str(snapshot)],
                 env=environment, cwd=backend, runner=runner, stage=f"{route} restore")
            for operation in (("stamp", "0008_tenant_voice_tuning"), ("upgrade", "head")):
                _run([sys.executable, "-m", "alembic", "-c", str(backend / "alembic.ini"), *operation],
                     env=environment, cwd=backend, runner=runner, stage=f"{route} migration")
            dumps[route] = _run([pg_dump, "--schema-only", "--no-owner"], env=environment,
                                cwd=backend, runner=runner, stage=f"{route} schema dump")
            (output / f"{route}-schema.sql").write_text(dumps[route], encoding="utf-8")
    except ParityError as exc:
        failure = exc
    finally:
        for name in reversed(created):
            try:
                # Only successful CREATE identities from this invocation; no
                # FORCE, arbitrary target name or unrelated session termination.
                if not _OWNED_NAME.fullmatch(name):
                    raise ParityError("Invalid cleanup identity")
                _run([psql, "-X", "-v", "ON_ERROR_STOP=1", "-c", f'DROP DATABASE IF EXISTS "{name}"'],
                     env=admin_env, cwd=backend, runner=runner, stage="owned database cleanup")
            except ParityError:
                cleanup_failed = True
    if failure:
        if cleanup_failed:
            raise ParityError("Schema parity failed and an owned database could not be cleaned up; inspect generated database names in the output directory") from None
        raise failure from None
    if cleanup_failed:
        raise ParityError("Schema parity owned database cleanup failed")
    difference = compare_dumps(dumps["clean"], dumps["lineage"])
    diff_path = output / "schema.diff"
    diff_path.write_text(difference, encoding="utf-8")
    result = {"equal": not difference, "diff_path": str(diff_path), "databases": created,
              "owner_metadata_ignored": True, "structural_allowlist": []}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--psql", default="psql", help="Compatible PostgreSQL client executable (16.14+ for preserved snapshot)")
    parser.add_argument("--pg-dump", default="pg_dump", help="One executable used for both schema dumps")
    args = parser.parse_args(argv)
    dsn = os.getenv("SCHEMA_PARITY_ADMIN_DSN") or os.getenv("TEST_DATABASE_URL") or ""
    try:
        result = run_parity(dsn, backend_root=args.backend_root, output_dir=args.output_dir,
                            psql=args.psql, pg_dump=args.pg_dump)
    except ParityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
