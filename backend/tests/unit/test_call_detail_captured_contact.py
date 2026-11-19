"""The single-call endpoint must carry the same captured contact as the list.

The list (GET /calls) returns captured_phone / captured_email and their
confirmed flags; the call page (GET /calls/{id}) did not. Both now use
_captured_contact_rows_sql() and _display_captured_contacts(), so they cannot
disagree.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace as NS

import pytest

from tests.unit.test_inbound_identity_effects import CALL, TENANT, Connection, Pool, call_row

FIELDS = ("captured_phone", "captured_email", "captured_phone_confirmed", "captured_email_confirmed")

ROWS = [
    {"field_key": "phone", "field_type": "phone", "source": "manual_edit", "value": "+923085397539",
     "normalized_value": "+923085397539", "confirmed": True, "validation_status": "confirmed",
     "updated_at": "2026-10-05"},
    {"field_key": "email", "field_type": "email", "source": "manual_edit", "value": "a@example.com",
     "normalized_value": "a@example.com", "confirmed": False, "validation_status": "confirmed",
     "updated_at": "2026-10-05"},
]


@pytest.fixture(autouse=True)
def synthetic_settings(monkeypatch):
    from app.core import config, dotenv_compat
    import dotenv
    monkeypatch.setattr(dotenv_compat, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings.model_construct(environment="test"))


class DetailConnection(Connection):
    async def fetch(self, sql, *args):
        assert "FROM call_legs" in sql
        return []


def _row(**extra):
    row = call_row(direction="outbound")
    row.update(status="completed", created_at="2026-11-06T00:00:00+00:00", campaign_id=None,
               billing_status="none", lead_id=None, transcript_json=[])
    row.update(extra)
    return row


async def _detail(row):
    from app.api.v1.endpoints.calls import get_call
    conn = DetailConnection(row)
    return await get_call(CALL, current_user=NS(tenant_id=TENANT), db_client=NS(pool=Pool(conn)))


async def test_detail_returns_captured_contact_with_confirmed_flags():
    detail = await _detail(_row(captured_contact_rows=json.dumps(ROWS)))
    assert detail.captured_phone == "+923085397539"
    assert detail.captured_phone_confirmed is True
    assert detail.captured_email == "a@example.com"
    assert detail.captured_email_confirmed is False


@pytest.mark.parametrize("rows", ["[]", [], None])
async def test_detail_returns_none_for_all_four_without_rows(rows):
    extra = {} if rows is None else {"captured_contact_rows": rows}
    detail = await _detail(_row(**extra))
    for field in FIELDS:
        assert getattr(detail, field) is None


async def test_detail_and_list_projection_agree():
    from app.api.v1.endpoints.calls import _display_captured_contacts
    row = _row(captured_contact_rows=json.dumps(ROWS))
    detail = await _detail(dict(row))
    listed = _display_captured_contacts(row)
    assert {f: getattr(detail, f) for f in FIELDS} == {f: listed[f] for f in FIELDS}


def test_detail_sql_has_captured_subquery_and_keeps_tenant_scope():
    from app.api.v1.endpoints.calls import get_call
    source = " ".join(inspect.getsource(get_call).split())
    assert "{_captured_contact_rows_sql()} AS captured_contact_rows" in source
    assert "WHERE c.id=$1 AND c.tenant_id=$2" in source
    from app.api.v1.endpoints.calls import _captured_contact_rows_sql
    assert "d.tenant_id = c.tenant_id" in " ".join(_captured_contact_rows_sql().split())
