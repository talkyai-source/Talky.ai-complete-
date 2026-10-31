#!/usr/bin/env python3
"""Fail-closed application/schema smoke for an isolated Postgres restore.

The script intentionally accepts only a ``talky_restore_*`` database name and
derives its connection URL from ``DATABASE_URL``.  It never prints the URL or
credentials, never runs migrations, and executes only read-only queries using
the application's current asyncpg pool implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_RESTORE_NAME = re.compile(r"^talky_restore_[A-Za-z0-9_]+$")
_PROTECTED_DATABASES = {"postgres", "talky", "talkyai", "template0", "template1"}


def _target_dsn(source_dsn: str, target_database: str) -> tuple[str, str]:
    """Return (target DSN, source database) after strict isolation checks."""

    if not _RESTORE_NAME.fullmatch(target_database):
        raise ValueError("target database must match talky_restore_[A-Za-z0-9_]+")
    if target_database.lower() in _PROTECTED_DATABASES:
        raise ValueError("refusing a protected or production-like target database")

    parsed = urlsplit(source_dsn)
    scheme = parsed.scheme.replace("postgresql+asyncpg", "postgresql")
    if scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("DATABASE_URL must be a PostgreSQL URL with a host")

    source_database = unquote(parsed.path.lstrip("/"))
    if not source_database:
        raise ValueError("DATABASE_URL must name its source database explicitly")
    if source_database == target_database:
        raise ValueError("restore target must differ from the configured source database")

    target = parsed._replace(scheme=scheme, path="/" + quote(target_database, safe=""))
    return urlunsplit(target), source_database


def _repository_heads(backend_root: Path) -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "Alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def _smoke(target_dsn: str, target_database: str, backend_root: Path) -> None:
    # Set the isolated target before importing application modules.  Clear the
    # replica override so no compatibility query can escape to another DB.
    os.environ["DATABASE_URL"] = target_dsn
    os.environ.pop("READ_DATABASE_URL", None)
    os.environ["PG_POOL_MIN_SIZE"] = "1"
    os.environ["PG_POOL_MAX_SIZE"] = "1"

    from app.core.db import close_db_pool, init_db_pool

    expected_heads = _repository_heads(backend_root)
    if not expected_heads:
        raise RuntimeError("repository exposes no Alembic head")

    pool = await init_db_pool()
    try:
        async with pool.acquire() as conn, conn.transaction(readonly=True):
            actual_database = await conn.fetchval("SELECT current_database()")
            if actual_database != target_database:
                raise RuntimeError(
                    f"connected to {actual_database!r}, expected isolated target "
                    f"{target_database!r}"
                )

            restored_heads = {
                str(row["version_num"])
                for row in await conn.fetch("SELECT version_num FROM alembic_version")
            }
            if restored_heads != expected_heads:
                raise RuntimeError(
                    "restored Alembic heads do not match this application: "
                    f"restored={sorted(restored_heads)!r} "
                    f"application={sorted(expected_heads)!r}"
                )

            # Compile representative current application read contracts.
            # LIMIT 0 proves table/column presence and role permissions
            # without reading or printing tenant data.
            for query in (
                "SELECT id, tenant_id, status FROM campaigns LIMIT 0",
                "SELECT id, tenant_id, direction, status FROM calls LIMIT 0",
                "SELECT id, tenant_id, campaign_id, status, transfer_policy "
                "FROM inbound_campaign_configs LIMIT 0",
                "SELECT id, tenant_id, call_id, transaction_type, quantity_seconds "
                "FROM inbound_usage_transactions LIMIT 0",
            ):
                await conn.fetch(query)

        # Import the real ASGI entry point only after the isolated DATABASE_URL
        # is installed. Importing exercises current route/provider wiring but
        # does not run lifespan, contact Redis/providers, or serve traffic.
        app_module = importlib.import_module("app.main")
        if getattr(app_module, "app", None) is None:
            raise RuntimeError("app.main did not expose the ASGI application")
    finally:
        await close_db_pool()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify current application compatibility with an isolated restored DB."
    )
    parser.add_argument(
        "--database",
        required=True,
        help="Isolated target name; must start with talky_restore_ (never production).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    # Match the application's own configuration loading without asking an
    # operator to ``source`` a secrets file into an interactive shell.
    from app.core.dotenv_compat import load_dotenv

    load_dotenv(dotenv_path=backend_root / ".env", override=False)
    source_dsn = os.environ.get("DATABASE_URL", "").strip()
    if not source_dsn:
        print("ERROR: DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        target_dsn, source_database = _target_dsn(source_dsn, args.database)
        asyncio.run(_smoke(target_dsn, args.database, backend_root))
    except Exception as exc:
        print(f"RESTORE COMPATIBILITY FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "RESTORE COMPATIBILITY PASSED: "
        f"isolated_database={args.database} source_database={source_database}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
