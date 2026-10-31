"""Restart recovery uses saved evidence, never reconstructed confirmations."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import calls
from app.domain.services.call_summary import store
from app.domain.services.call_summary.business_details import transcript_revision
from app.domain.services.transcript_service import TranscriptService


TENANT = "00000000-0000-0000-0000-000000000001"
CALL = "00000000-0000-0000-0000-000000000002"
QUOTE = "I need reliable customer support."


class DurablePort:
    """Persisted rows survive discarded process memory; SQL transport is fake."""
    def __init__(self, state="complete", *, manual=False):
        self.row = {"transcript": "User: " + QUOTE,
                    "transcript_json": [{"role": "user", "content": QUOTE, "is_final": True}],
                    "transcript_save_state": state, "campaign_id": None, "lead_id": None,
                    "action_results": {}, "summary_json": {"headline": "Support discussed",
                        "business_details": [{"field_key": "identified_need", "value": QUOTE,
                                              "source_quote": QUOTE}]}}
        self.row["summary_transcript_hash"] = transcript_revision(self.row)
        self.notes = {"identified_need": "Operator's reviewed note"} if manual else {}
        self.manual, self.writes, self.reads = manual, [], []
        self.processing = "failed"

    async def fetchrow(self, query, *args):
        self.reads.append((query, args))
        if "INSERT INTO call_lead_details" in query:
            if self.manual:
                return None
            self.notes[args[4]] = args[6]
            self.writes.append(args)
            return {"id": CALL}
        if "FROM call_lead_details" in query:
            return None
        return deepcopy(self.row)

    async def fetch(self, *_args):
        return []

    async def execute(self, query, *args):
        if "lead_details_status=$3" in query:
            self.processing = args[2]
        return "UPDATE 1"


@pytest.fixture
def saved(monkeypatch):
    port = DurablePort()
    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant == TENANT
        yield port
    monkeypatch.setattr(store, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquire)
    model = AsyncMock(side_effect=AssertionError("A current saved summary needs no model call"))
    monkeypatch.setattr(store, "summarize_transcript", model)
    return port, model


async def route():
    return await calls.get_call_summary(CALL, current_user=SimpleNamespace(tenant_id=TENANT),
                                        db_client=SimpleNamespace(pool=object()))


@pytest.mark.asyncio
async def test_saved_summary_recovers_missing_notes_after_process_memory_is_gone(saved):
    port, model = saved
    TranscriptService.clear_all_buffers()
    assert not TranscriptService().get_turns(CALL)
    result = await route()
    assert result["available"] and port.notes == {"identified_need": QUOTE}
    assert port.processing == "complete"
    assert port.writes[0][8] is False  # Recovery does not create a confirmed contact.
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["partial", "failed", "unknown", "complete"])
async def test_summary_reports_completeness_of_its_current_durable_source(saved, state):
    port, model = saved
    port.row["transcript_save_state"] = state
    result = await route()
    assert result["available"] and result["summary"] == port.row["summary_json"]
    assert result["source_evidence"] == {
        "transcript_save_state": state, "summary_current": True,
        "review_required": state != "complete", "revision": transcript_revision(port.row),
    }
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_recovery_preserves_authoritative_manual_disposition(saved):
    port, model = saved
    port.manual = True
    port.notes["identified_need"] = "Operator's reviewed note"
    result = await route()
    assert result["available"] and port.notes == {"identified_need": "Operator's reviewed note"}
    assert port.processing == "complete" and not port.writes
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["transcript", "summary", "missing"])
async def test_late_source_or_summary_change_never_exposes_a_stale_summary(monkeypatch, saved, change):
    port, _ = saved
    original = store.generate_and_store
    async def generate(*args, **kwargs):
        summary = await original(*args, **kwargs)
        if change == "transcript":
            port.row["transcript_json"][0]["content"] = "Actually, I no longer need support."
        elif change == "summary":
            port.row["summary_json"] = {"headline": "A newer analysis", "business_details": []}
        else:
            port.row = None
        return summary
    monkeypatch.setattr(store, "generate_and_store", generate)
    result = await route()
    assert result["available"] is False and result["summary"] is None
    assert result["source_evidence"]["summary_current"] is False
    assert result["source_evidence"]["review_required"] is True
