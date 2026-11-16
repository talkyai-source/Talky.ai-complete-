"""A campaign's own `address` field keeps working; a live address is never overwritten post-call."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.endpoints import lead_details as api
from app.domain.services.call_summary import business_details as bd
from app.domain.services.lead_capture_service import LeadCaptureService, project_contact_evidence
from tests.unit.test_model_contact_recording import SQLPort

TENANT, CALL = str(uuid4()), str(uuid4())


class WritePort(SQLPort):
    """Records every call_lead_details write; no identity read is expected for generic rows."""
    async def fetchrow(self, sql, *args):
        assert "call_lead_details" in sql
        self.writes.append((sql, args))
        return {"id": str(uuid4())}


def _detail(field_type="text", value="12 Elm Road"):
    return {"field_key": "address", "value": value, "field_type": field_type,
            "evidence": {"extraction": "post_call", "status": "needs_review"}}


def _run_post_call(monkeypatch, *, protected, field_type="text"):
    """persist_business_details with the DB protection read answered by `protected`."""
    captured = []

    class Conn:
        async def execute(self, *a):
            pass

        async def fetch(self, sql, *a):
            return [{"field_key": "address"}] if protected else []

    @asynccontextmanager
    async def acquire(pool, tenant):
        yield Conn()

    async def fields(*a):
        return [{"field_key": "address", "label": "Address", "field_type": field_type}]

    async def capture(self, **kw):
        captured.append(kw)
        return True

    import app.core.db_utils as db_utils
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    monkeypatch.setattr(bd, "load_campaign_extract_fields", fields)
    monkeypatch.setattr(bd, "verified_details", lambda *a, **k: [_detail(field_type)])
    monkeypatch.setattr(LeadCaptureService, "capture", capture)
    row = {"transcript": "t", "transcript_json": [], "campaign_id": str(uuid4()), "lead_id": str(uuid4())}
    return captured, lambda: bd.persist_business_details(object(), TENANT, CALL, {"business_details": []}, row)


@pytest.mark.asyncio
async def test_post_call_candidate_for_a_campaign_address_field_is_written_as_before(monkeypatch):
    captured, run = _run_post_call(monkeypatch, protected=False)
    assert await run() == 1
    [kw] = captured
    assert (kw["field_key"], kw["field_type"], kw["source"], kw["confirmed"]) == ("address", "text", "caller_stated", False)
    assert kw["evidence"]["extraction"] == "post_call" and kw["evidence"]["status"] == "needs_review"
    assert "validation_status" not in kw and "expected_contact" not in kw


@pytest.mark.asyncio
async def test_post_call_candidate_never_touches_an_address_row_the_live_call_saved(monkeypatch):
    """The live row (pending or confirmed, no extraction=post_call evidence) is `protected`."""
    captured, run = _run_post_call(monkeypatch, protected=True)
    assert await run() == 0 and captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type", ["notes", "text"])
async def test_generic_address_capture_post_call_and_manual_edit_and_clear_work(field_type):
    pool = WritePort()
    service = LeadCaptureService(pool)
    assert await service.capture(tenant_id=TENANT, call_id=CALL, field_key="address", value="12 Elm Road",
        source="caller_stated", field_type=field_type, confirmed=False, evidence={"extraction": "post_call"})
    assert await service.capture(tenant_id=TENANT, call_id=CALL, field_key="address", value="40 Oak Lane",
        source="manual_edit", field_type=field_type, confirmed=True)
    assert await service.capture(tenant_id=TENANT, call_id=CALL, field_key="address", value=None,
        source="manual_edit", field_type=field_type, confirmed=False)
    assert len(pool.writes) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("field_type", ["notes", "text"])
async def test_manual_edit_and_clear_of_address_keep_generic_arguments(monkeypatch, field_type):
    seen = []

    class Service:
        async def capture(self, **kwargs):
            seen.append(kwargs)
            return True
    monkeypatch.setattr(api, "_service", Service)
    user = SimpleNamespace(tenant_id="tenant")
    await api.correct_lead_detail("call", "address", api.ManualEdit(value=None, field_type=field_type), user)
    await api.correct_lead_detail("call", "address", api.ManualEdit(value="x", field_type=field_type), user)
    assert [s["validation_status"] for s in seen] == [None, None]
    assert [s["confirmed"] for s in seen] == [True, True]


def test_an_old_post_call_address_row_reads_back_unchanged():
    row = {"field_key": "address", "field_type": "text", "source": "caller_stated", "value": "12 Elm Road",
           "confirmed": False, "validation_status": None,
           "evidence": {"extraction": "post_call", "status": "needs_review"}}
    assert project_contact_evidence(dict(row), []) == row
