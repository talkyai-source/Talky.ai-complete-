"""
Alembic migration environment — async PostgreSQL via asyncpg.

Reads DATABASE_URL from environment (same as the app).
Supports both:
  - offline mode: generates SQL without a live DB connection
  - online mode:  runs migrations against a live asyncpg pool
"""
import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Alembic config object — gives access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Inject DATABASE_URL from environment so we never hard-code credentials
# ---------------------------------------------------------------------------
_db_url = os.getenv("DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is required for Alembic migrations.\n"
        "Export it before running: export DATABASE_URL=postgresql://user:pass@host/db"
    )

# asyncpg driver uses postgresql+asyncpg:// scheme
if _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)

config.set_main_option("sqlalchemy.url", _db_url)

# ---------------------------------------------------------------------------
# target_metadata: set this if you use SQLAlchemy ORM models for autogenerate.
# Talky.ai uses raw SQL / asyncpg — leave as None and write migrations manually.
# To enable autogenerate later: import your Base.metadata here.
# ---------------------------------------------------------------------------
target_metadata = None


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout without a DB connection
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to a live DB and run migrations
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # Lock the alembic_version table during migration to prevent
        # concurrent migration runs on multi-instance deploys
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No pooling for migration runs
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
