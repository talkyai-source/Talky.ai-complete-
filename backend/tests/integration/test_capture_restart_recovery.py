"""Recovery from committed evidence on the existing disposable AG05 database.

Uses the canonical synthetic-row/RLS fixture. Discards local transcript state;
does not claim to recover never-committed audio or restart a live call.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import calls as calls_api
from app.domain.services.call_summary import store
from app.domain.services.call_summary.business_details import transcript_revision
from app.domain.services.lead_capture_service import LeadCaptureService
from app.domain.services.transcript_service import TranscriptService
from tests.integration import test_ag05_lead_evidence as ag05


pytestmark = pytest.mark.integration
lead_db = ag05.lead_db


@pytest.mark.parametrize("save_state", ["complete", "partial"])
async def test_durable_summary_recovers_after_failed_note_write_and_discarded_memory(lead_db, monkeypatch, save_state):
    tenant, call = str(lead_db.tenants[0]), str(lead_db.calls[0])
    quote = "I need reliable customer support."
    row = {"transcript": "User: " + quote,
           "transcript_json": [{"role": "user", "content": quote, "is_final": True}],
           "action_results": {}}
    summary = {"headline": "Support discussed", "business_details": [
        {"field_key": "identified_need", "value": quote, "source_quote": quote}]}
    await lead_db.admin.execute(
        "UPDATE calls SET transcript=$1,transcript_json=$2::jsonb,action_results=$3::jsonb,"
        "summary_json=$4::jsonb,summary_transcript_hash=$5,transcript_save_state=$6 WHERE id=$7",
        row["transcript"], json.dumps(row["transcript_json"]), "{}", json.dumps(summary),
        transcript_revision(row), save_state, lead_db.calls[0],
    )
    model = AsyncMock(side_effect=AssertionError("Saved current summary must avoid a new model request"))
    monkeypatch.setattr(store, "summarize_transcript", model)
    real_capture = LeadCaptureService.capture
    monkeypatch.setattr(LeadCaptureService, "capture", AsyncMock(side_effect=RuntimeError("Synthetic interrupted write")))
    await store.generate_and_store(lead_db.pool, tenant, call)
    assert await lead_db.admin.fetchval("SELECT lead_details_status FROM calls WHERE id=$1", lead_db.calls[0]) == "failed"
    assert not await lead_db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM call_lead_details WHERE call_id=$1)", lead_db.calls[0])

    # A later request has only durable rows, not the previous caller/session.
    monkeypatch.setattr(LeadCaptureService, "capture", real_capture)
    TranscriptService.clear_all_buffers()
    result = await calls_api.get_call_summary(call, current_user=SimpleNamespace(tenant_id=tenant),
                                              db_client=SimpleNamespace(pool=lead_db.pool))
    saved = await lead_db.admin.fetchrow("SELECT value,confirmed,evidence FROM call_lead_details WHERE call_id=$1 AND field_key='identified_need'", lead_db.calls[0])
    assert saved["value"] == quote and saved["confirmed"] is False
    evidence = json.loads(saved["evidence"]) if isinstance(saved["evidence"], str) else saved["evidence"]
    assert evidence["source_quote"] == quote and evidence["status"] == "needs_review"
    assert evidence["transcript_revision"] == transcript_revision(row)
    assert result["available"] and result["source_evidence"]["summary_current"]
    assert result["source_evidence"]["transcript_save_state"] == save_state
    assert result["source_evidence"]["review_required"] is (save_state != "complete")
    assert await lead_db.admin.fetchval("SELECT lead_details_status FROM calls WHERE id=$1", lead_db.calls[0]) == "complete"
    model.assert_not_awaited()

    foreign = await store.summary_response(lead_db.pool, str(lead_db.tenants[1]), call, summary)
    assert foreign["available"] is False and foreign["summary"] is None
    assert foreign["source_evidence"]["revision"] is None


async def test_summary_observation_withholds_a_later_durable_transcript_revision(lead_db):
    tenant, call = str(lead_db.tenants[0]), str(lead_db.calls[0])
    row = {"transcript": "User: Original request", "transcript_json": [
        {"role": "user", "content": "Original request"}], "action_results": {}}
    summary = {"headline": "Original analysis", "business_details": []}
    await lead_db.admin.execute(
        "UPDATE calls SET transcript=$1,transcript_json=$2::jsonb,action_results='{}'::jsonb,"
        "summary_json=$3::jsonb,summary_transcript_hash=$4,transcript_save_state='partial' WHERE id=$5",
        row["transcript"], json.dumps(row["transcript_json"]), json.dumps(summary),
        transcript_revision(row), lead_db.calls[0],
    )
    assert (await store.summary_response(lead_db.pool, tenant, call, summary))["available"]
    await lead_db.admin.execute("UPDATE calls SET transcript_json=$1::jsonb WHERE id=$2",
                               json.dumps([{"role": "user", "content": "Actually, cancel that request."}]), lead_db.calls[0])
    result = await store.summary_response(lead_db.pool, tenant, call, summary)
    assert result["available"] is False and result["summary"] is None
    assert result["source_evidence"]["summary_current"] is False
    assert result["source_evidence"]["review_required"] is True
