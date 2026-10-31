"""Real-PostgreSQL checks for the 0023 dual-bootstrap compatibility guard."""

from __future__ import annotations

import importlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


migration = importlib.import_module("Alembic.versions.0023_admin_media_deletion_safety")


def _engine_or_skip():
    dsn = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    try:
        engine = create_engine(dsn, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return engine
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")


def _require_intent_table(connection) -> None:
    ready = connection.execute(
        text("SELECT to_regclass('public.admin_media_deletion_intents') " "IS NOT NULL")
    ).scalar_one()
    if not ready:
        pytest.skip("database does not have admin_media_deletion_intents")


def test_0023_accepts_only_the_known_complete_schema_uuid_default():
    engine = _engine_or_skip()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _require_intent_table(connection)
                connection.execute(
                    text(
                        "ALTER TABLE public.admin_media_deletion_intents "
                        "ALTER COLUMN id SET DEFAULT uuid_generate_v4()"
                    )
                )

                migration._validate_existing_table(connection, allow_complete_schema_default=True)
                with pytest.raises(RuntimeError, match="id default"):
                    migration._validate_existing_table(
                        connection, allow_complete_schema_default=False
                    )

                connection.execute(
                    text(
                        "ALTER TABLE public.admin_media_deletion_intents "
                        "ALTER COLUMN id SET DEFAULT gen_random_uuid()"
                    )
                )
                migration._validate_existing_table(connection, allow_complete_schema_default=False)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_0023_rejects_unknown_structural_drift_without_repairing_it():
    engine = _engine_or_skip()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                _require_intent_table(connection)
                connection.execute(
                    text(
                        "ALTER TABLE public.admin_media_deletion_intents "
                        "ALTER COLUMN reason DROP NOT NULL"
                    )
                )

                with pytest.raises(RuntimeError, match="reason nullability"):
                    migration._validate_existing_table(
                        connection, allow_complete_schema_default=True
                    )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
