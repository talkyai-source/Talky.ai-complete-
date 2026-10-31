#!/usr/bin/env python3
"""Fail closed unless the database and repository Alembic heads match exactly."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Protocol

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


class _RepositoryScripts(Protocol):
    def get_heads(self) -> list[str]: ...


def verify_current_heads(
    connection: Connection, scripts: _RepositoryScripts
) -> tuple[set[str], set[str]]:
    """Return matching (database, repository) heads or raise on any drift."""

    repository_heads = set(scripts.get_heads())
    if not repository_heads:
        raise RuntimeError("repository exposes no Alembic heads")
    if len(repository_heads) != 1:
        raise RuntimeError(
            "repository must expose exactly one Alembic head: "
            f"repository={sorted(repository_heads)!r}"
        )

    database_heads = set(MigrationContext.configure(connection).get_current_heads())
    if database_heads != repository_heads:
        raise RuntimeError(
            "database heads do not match repository heads: "
            f"database={sorted(database_heads)!r} "
            f"repository={sorted(repository_heads)!r}"
        )
    return database_heads, repository_heads


def _repository_scripts(backend_root: Path) -> ScriptDirectory:
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "Alembic"))
    return ScriptDirectory.from_config(config)


def _async_database_url(raw_url: str) -> URL:
    parsed = make_url(raw_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must use PostgreSQL")
    return parsed.set(drivername="postgresql+asyncpg")


async def _verify_database(database_url: str, backend_root: Path) -> tuple[set[str], set[str]]:
    engine = create_async_engine(_async_database_url(database_url), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                verify_current_heads,
                _repository_scripts(backend_root),
            )
    finally:
        await engine.dispose()


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    from app.core.dotenv_compat import load_dotenv

    load_dotenv(dotenv_path=backend_root / ".env", override=False)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ALEMBIC HEAD CHECK FAILED: DATABASE_URL is required", file=sys.stderr)
        return 2

    try:
        database_heads, repository_heads = asyncio.run(_verify_database(database_url, backend_root))
    except Exception as exc:
        print(f"ALEMBIC HEAD CHECK FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "ALEMBIC HEAD CHECK PASSED: "
        f"database={sorted(database_heads)!r} repository={sorted(repository_heads)!r}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
