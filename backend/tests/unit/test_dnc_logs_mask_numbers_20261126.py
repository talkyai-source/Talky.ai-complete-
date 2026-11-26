"""Privacy: the do-not-call log lines show the last four digits of a number only.

The bulk-import row failure also logs the exception's TYPE, never its text
(the text of a database error can carry the number).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import dnc as dnc_ep
from app.domain.services import dnc_service

TENANT = "11111111-1111-4111-8111-111111111111"
NUMBER = "+442079460123"
TAIL = "0123"


def _entry():
    return dnc_service.DNCEntry(
        id="22222222-2222-4222-8222-222222222222", tenant_id=TENANT,
        normalized_number=NUMBER, source="manual_admin", reason=None,
        expires_at=None, created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def _user():
    return SimpleNamespace(id="u1", tenant_id=TENANT, role="admin")


def _patch_service(monkeypatch):
    class _Svc:
        def __init__(self, pool):
            pass

        async def add(self, **kw):
            return _entry()

        async def add_caller_opt_out(self, **kw):
            return _entry()

    monkeypatch.setattr(dnc_ep, "DNCService", _Svc)


def _assert_masked(caplog, marker):
    text = "\n".join(r.getMessage() for r in caplog.records if marker in r.getMessage())
    assert text, f"no log line with {marker}"
    assert NUMBER not in text and "442079460123" not in text, text
    assert "***" + TAIL in text, text


@pytest.mark.asyncio
async def test_entry_added_log_masks_the_number(monkeypatch, caplog):
    _patch_service(monkeypatch)
    caplog.set_level(logging.DEBUG)
    payload = dnc_ep.DNCAddRequest(e164=NUMBER)
    await dnc_ep.add_dnc_entry(payload, current_user=_user(), db_pool=object(), audit=object())
    _assert_masked(caplog, "dnc_entry_added")


@pytest.mark.asyncio
async def test_caller_opt_out_log_masks_the_number(monkeypatch, caplog):
    _patch_service(monkeypatch)
    caplog.set_level(logging.DEBUG)
    payload = dnc_ep.DNCCallerOptOutRequest(e164=NUMBER, call_id="call-1") \
        if hasattr(dnc_ep, "DNCCallerOptOutRequest") else None
    assert payload is not None
    await dnc_ep.caller_opt_out(payload, current_user=_user(), db_pool=object(), audit=object())
    _assert_masked(caplog, "dnc_caller_opt_out")


@pytest.mark.asyncio
async def test_bulk_import_row_failure_logs_masked_number_and_error_type_only(monkeypatch, caplog):
    secret_text = "duplicate key value violates constraint for (" + NUMBER + ")"

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Conn:
        def transaction(self):
            return _Tx()

        async def fetchrow(self, *a, **k):
            raise RuntimeError(secret_text)

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kw):
        yield _Conn()

    async def country(conn, tenant, requested):
        return "GB"

    monkeypatch.setattr(dnc_service, "acquire_with_tenant", scoped)
    monkeypatch.setattr(dnc_service, "_write_country", country)
    caplog.set_level(logging.DEBUG)
    result = await dnc_service.DNCService(object()).bulk_import(
        tenant_id=TENANT, numbers=[NUMBER], source="bulk_import", default_country="GB")
    assert result["skipped_count"] == 1
    text = "\n".join(r.getMessage() for r in caplog.records if "dnc_bulk_import_row_failed" in r.getMessage())
    assert text
    assert NUMBER not in text and "442079460123" not in text, text
    assert "duplicate key" not in text, text
    assert "***" + TAIL in text and "RuntimeError" in text, text
