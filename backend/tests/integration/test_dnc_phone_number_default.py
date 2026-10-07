"""Migration 0064 against a real Postgres: an opt-out written without
phone_number (the pre-release code's INSERT shape) is still recorded.

Runs only with an explicit TEST_DATABASE_URL, inside one rolled-back
transaction.
"""
from __future__ import annotations

import importlib
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text

MIGRATION = importlib.import_module("Alembic.versions.0064_dnc_phone_number_default")
# Point the trigger at the probe table; the word boundary keeps
# "FUNCTION public.dnc_entries_fill_phone_number" untouched.
_ON_TABLE = re.compile(r"\bON public\.dnc_entries\b")


def _dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


def _run(with_trigger: bool):
    engine = create_engine(_dsn_or_skip())
    table = f"dnc_probe_{uuid.uuid4().hex[:10]}"
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            # The post-0061 shape: phone_number NOT NULL, no default.
            conn.execute(text(f"""
                CREATE TABLE public.{table} (
                    id BIGSERIAL PRIMARY KEY,
                    tenant_id UUID,
                    normalized_number TEXT NOT NULL,
                    phone_number VARCHAR(50) NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT
                )
            """))
            if with_trigger:
                conn.execute(text(MIGRATION.FUNCTION_SQL))
                conn.execute(text(_ON_TABLE.sub(f"ON public.{table}", MIGRATION.TRIGGER_SQL)))
            # Exactly the column list dnc_service.py used before this release.
            conn.execute(text(
                f"INSERT INTO public.{table} (tenant_id, normalized_number, source, reason) "
                "VALUES (:t, '447700900123', 'caller_request', 'asked not to be called')"
            ), {"t": str(uuid.uuid4())})
            # The release code's shape still wins when it sends phone_number.
            conn.execute(text(
                f"INSERT INTO public.{table} (tenant_id, phone_number, normalized_number, source, reason) "
                "VALUES (:t, '+44 7700 900456', '447700900456', 'caller_request', 'x')"
            ), {"t": str(uuid.uuid4())})
            return conn.execute(text(
                f"SELECT normalized_number, phone_number FROM public.{table} ORDER BY id"
            )).all()
        finally:
            tx.rollback()
            engine.dispose()


def test_pre_release_insert_shape_is_recorded_with_the_trigger():
    rows = _run(with_trigger=True)
    assert rows == [("447700900123", "447700900123"), ("447700900456", "+44 7700 900456")]


def test_without_the_trigger_the_pre_release_insert_fails():
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="phone_number"):
        _run(with_trigger=False)
