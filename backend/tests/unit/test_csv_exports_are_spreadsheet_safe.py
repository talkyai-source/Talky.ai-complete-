"""QA F14-01: CSV exports never carry live spreadsheet formulas, and the audit
export says when it stopped at its row limit.

A tenant-controlled business name, note or description starting with = + -
or @ became a live formula when staff opened the audit or top-up export; the
audit export also stopped at 10,000 rows without saying so.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

FORMULA = "=HYPERLINK(\"http://evil.example\",\"click\")"


@pytest.mark.parametrize("raw,safe", [
    (FORMULA, "'" + FORMULA),
    ("+1+1", "'+1+1"),
    ("-2+3", "'-2+3"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("\tcmd", "'\tcmd"),
    ("\rcmd", "'\rcmd"),
    ("Acme Plumbing", "Acme Plumbing"),
    ("", ""),
    (None, None),
    (-5, -5),
])
def test_csv_safe_keeps_formula_starts_as_text(raw, safe):
    from app.utils.csv_safe import csv_safe

    assert csv_safe(raw) == safe


def _log(description):
    from app.domain.services.audit_logger import AuditEvent

    return SimpleNamespace(
        event_id=uuid4(), event_time=datetime(2026, 11, 1, tzinfo=timezone.utc),
        event_type=AuditEvent.CONFIG_CHANGED, severity="LOW", actor_id=None,
        tenant_id=None, resource_type="dnc_entry", resource_id=None,
        action="dnc.entry.add", description=description,
    )


async def _export(monkeypatch, logs):
    from app.api.v1.endpoints import audit_logs

    monkeypatch.setattr(audit_logs, "resolve_tenant_scope", lambda user, tenant, **kw: tenant)

    class _Audit:
        async def query(self, *, limit, **kwargs):
            return logs[:limit]

        async def log(self, **kwargs):
            return None

    response = await audit_logs.export_audit_logs(
        request=None, start_date=datetime(2026, 11, 1), end_date=datetime(2026, 11, 2),
        tenant_id=None, format="csv", current_user={"id": uuid4()}, audit_logger=_Audit(),
    )
    return list(csv.reader(io.StringIO(response.body.decode())))


@pytest.mark.asyncio
async def test_the_audit_export_neutralises_formula_cells(monkeypatch):
    rows = await _export(monkeypatch, [_log(FORMULA)])
    assert rows[1][9] == "'" + FORMULA


@pytest.mark.asyncio
async def test_the_audit_export_says_when_it_stopped_at_the_limit(monkeypatch):
    from app.api.v1.endpoints import audit_logs

    monkeypatch.setattr(audit_logs, "_EXPORT_ROW_LIMIT", 2)
    rows = await _export(monkeypatch, [_log("a"), _log("b"), _log("c")])
    assert len(rows) == 1 + 2 + 1
    assert "truncated" in rows[-1][0].lower() and "2" in rows[-1][0]
    complete = await _export(monkeypatch, [_log("a"), _log("b")])
    assert len(complete) == 1 + 2


@pytest.mark.asyncio
async def test_the_topup_export_neutralises_tenant_text(monkeypatch):
    from app.api.v1.endpoints import billing_topups

    row = {"created_at": None, "tenant_id": uuid4(), "business_name": "=1+1", "kind": "purchase",
           "minutes_delta": 100, "amount_cents": 1000, "currency": "usd", "package_code": "p100",
           "provider_event_id": "", "provider_payment_id": "", "order_status": "paid",
           "note": "@SUM(A1)"}

    class _Svc:
        async def reconciliation(self, **kwargs):
            return [row]

    monkeypatch.setattr(billing_topups, "_service", lambda: _Svc())
    response = await billing_topups.reconciliation(since=None, until=None, limit=10, fmt="csv", _admin=None)
    body = "".join([chunk if isinstance(chunk, str) else chunk.decode()
                    async for chunk in response.body_iterator])
    exported = list(csv.DictReader(io.StringIO(body)))
    assert exported[0]["business_name"] == "'=1+1"
    assert exported[0]["note"] == "'@SUM(A1)"
    assert exported[0]["minutes_delta"] == "100"
