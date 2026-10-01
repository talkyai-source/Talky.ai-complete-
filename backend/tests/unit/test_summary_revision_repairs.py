"""Cached analysis must describe this revision and never replace human notes."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.call_summary import store
from app.domain.services.call_summary.business_details import transcript_revision
from app.infrastructure.assistant.tools.leads import get_lead_followup


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "plaintext", "structured", "actions", "legacy"])
async def test_summary_cache_requires_current_evidence_and_business_fields(monkeypatch, change):
    old = {"headline": "Old analysis", "business_details": []}
    row = {"transcript": "Caller: I use Stripe.", "transcript_json": [
        {"role": "user", "content": "I use Stripe."}], "action_results": {}, "summary_json": old}
    row["summary_transcript_hash"] = transcript_revision(row)
    if change == "plaintext":
        row["transcript"] += "\nCaller: Actually I use SumUp."
    elif change == "structured":
        row["transcript_json"][0]["include_in_plaintext"] = False
    elif change == "actions":
        row["action_results"] = {"send_email": {"status": "succeeded"}}
    elif change == "legacy":
        row["summary_json"] = {"headline": "Old analysis"}
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=row), execute=AsyncMock(return_value="UPDATE 1"))
    @asynccontextmanager
    async def acquire(*args):
        yield conn
    monkeypatch.setattr(store, "acquire_with_tenant", acquire)
    fresh = {"headline": "Current analysis", "business_details": []}
    summarize = AsyncMock(return_value=fresh)
    monkeypatch.setattr(store, "summarize_transcript", summarize)
    monkeypatch.setattr(store, "_confirmed_contacts_for_call", AsyncMock(return_value={}))
    save = AsyncMock()
    monkeypatch.setattr(store, "save_summary_details", save)
    monkeypatch.setattr(store, "mark_lead_from_summary", AsyncMock())
    refresh = AsyncMock()
    monkeypatch.setattr(store, "refresh_latest_analysis", refresh)
    result = await store.generate_and_store(object(), "tenant", "call")
    assert result == (old if change == "none" else fresh)
    assert summarize.await_count == (0 if change == "none" else 1)
    assert refresh.await_args.args[-1] == transcript_revision(row)
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_summary_cannot_write_details_or_latest_analysis(monkeypatch):
    row = {"transcript": "Caller: Hello", "transcript_json": [], "summary_json": None}
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value=row), execute=AsyncMock(return_value="UPDATE 0"))
    @asynccontextmanager
    async def acquire(*args):
        yield conn
    monkeypatch.setattr(store, "acquire_with_tenant", acquire)
    monkeypatch.setattr(store, "summarize_transcript", AsyncMock(return_value={"headline": "old", "business_details": []}))
    monkeypatch.setattr(store, "_confirmed_contacts_for_call", AsyncMock(return_value={}))
    save, refresh = AsyncMock(), AsyncMock()
    monkeypatch.setattr(store, "save_summary_details", save)
    monkeypatch.setattr(store, "refresh_latest_analysis", refresh)
    assert await store.generate_and_store(object(), "tenant", "call") is None
    save.assert_not_awaited()
    refresh.assert_not_awaited()
    query = conn.execute.await_args.args[0]
    assert "transcript_json IS NOT DISTINCT FROM" in query
    assert "action_results IS NOT DISTINCT FROM" in query


@pytest.mark.asyncio
async def test_latest_analysis_is_separate_and_ordered_by_source_call(monkeypatch):
    conn = SimpleNamespace(execute=AsyncMock())
    @asynccontextmanager
    async def acquire(*args):
        yield conn
    monkeypatch.setattr(store, "acquire_with_tenant", acquire)
    await store.refresh_latest_analysis(object(), "tenant", "call", {"next_step": "Caller withdrew the request."}, "revision")
    query, *args = conn.execute.await_args.args
    assert "follow_up_note" not in query
    assert "latest_analysis_at <= c.created_at" in query
    assert "c.summary_transcript_hash=$4" in query
    assert args == ["call", "tenant", "Caller withdrew the request.", "revision", None]


@pytest.mark.asyncio
async def test_assistant_reads_latest_analysis_call_and_preserves_operator_note():
    lead = {"id": "lead", "is_lead": True, "follow_up_note": "Operator instruction",
            "qualified_call_id": "old-call", "latest_analysis_call_id": "new-call",
            "latest_analysis_note": "Callback withdrawn"}
    client = MagicMock()
    leads, calls = MagicMock(), MagicMock()
    for query in (leads, calls):
        query.select.return_value = query
        query.eq.return_value = query
    leads.execute.return_value = SimpleNamespace(data=[lead])
    calls.execute.return_value = SimpleNamespace(data=[{"summary_json": {"next_step": "Do not call again"}}])
    client.table.side_effect = lambda name: {"leads": leads, "calls": calls}[name]
    result = await get_lead_followup("tenant", client, lead_id="lead")
    assert result["lead"]["follow_up_note"] == "Operator instruction"
    assert result["call_summary"]["call_id"] == "new-call"
    assert result["call_summary"]["source"] == "ai_analysis"
    assert ("id", "new-call") in [c.args for c in calls.eq.call_args_list]
    assert ("tenant_id", "tenant") in [c.args for c in calls.eq.call_args_list]
