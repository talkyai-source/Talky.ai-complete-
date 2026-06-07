"""Unit tests for app.domain.services.call_summary.store.

Tests verify:
1. Idempotency — row already has summary_json → return it, summarizer NOT called.
   Both asyncpg shapes: dict-valued JSONB and str-valued JSONB.
2. Row with transcript + no summary_json → summarizer called, UPDATE issued.
3. Row with empty transcript → returns None, summarizer NOT called.
4. Row not found → returns None.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.call_summary.store import generate_and_store
from app.domain.services.call_summary.summarizer import SUMMARY_UNAVAILABLE_HEADLINE

# ---------------------------------------------------------------------------
# Fake DB infrastructure
# ---------------------------------------------------------------------------

_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_CALL_ID = "00000000-0000-0000-0000-000000000002"

_EXISTING_SUMMARY = {
    "headline": "Already summarized",
    "outcome": "qualified",
    "what_happened": "Quick intro call",
    "key_points": [],
    "objections": [],
    "commitments": [],
    "action_items": [],
    "sentiment": "positive",
    "next_step": "Follow up",
    "notable_quotes": [],
}

_FAKE_SUMMARY = {
    "headline": "Fresh summary",
    "outcome": "qualified",
    "what_happened": "Discussed the product features.",
    "key_points": ["interested in API"],
    "objections": [],
    "commitments": ["Trial signup"],
    "action_items": [{"item": "Send trial link", "owner": "agent"}],
    "sentiment": "positive",
    "next_step": "Send trial link",
    "notable_quotes": [],
}

# What the summarizer returns when Groq errors (network/SDK/429) or output
# won't parse — the transient-failure sentinel that must NOT be persisted.
_FAILED_SUMMARY = {
    "headline": SUMMARY_UNAVAILABLE_HEADLINE,
    "outcome": "",
    "what_happened": "",
    "key_points": [],
    "objections": [],
    "commitments": [],
    "action_items": [],
    "sentiment": "",
    "next_step": "",
    "notable_quotes": [],
}


def _make_conn(row):
    """Return a minimal async-compatible connection fake."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value=None)
    return conn


@asynccontextmanager
async def _fake_acquire(conn):
    """Replacement for acquire_with_tenant that yields the given conn."""
    yield conn


def _patch_acquire(conn):
    """Return a patch context for acquire_with_tenant that yields *conn*."""
    return patch(
        "app.domain.services.call_summary.store.acquire_with_tenant",
        side_effect=lambda pool, tenant_id: _fake_acquire(conn),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGenerateAndStore:
    async def test_idempotent_dict_summary_json(self):
        """summary_json already set as dict → return immediately, no summarizer."""
        conn = _make_conn({
            "transcript": "Agent: Hi\nCaller: Hello",
            "summary_json": _EXISTING_SUMMARY,  # dict (asyncpg codec decoded)
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result == _EXISTING_SUMMARY
        mock_summarize.assert_not_called()
        conn.execute.assert_not_called()  # no UPDATE issued

    async def test_idempotent_str_summary_json(self):
        """summary_json already set as JSON string (no codec) → parse + return."""
        conn = _make_conn({
            "transcript": "Agent: Hi\nCaller: Hello",
            "summary_json": json.dumps(_EXISTING_SUMMARY),  # str shape
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result == _EXISTING_SUMMARY
        mock_summarize.assert_not_called()
        conn.execute.assert_not_called()

    async def test_force_re_summarizes_even_if_summary_exists(self):
        """force=True → summarizer called + UPDATE issued even when summary_json set."""
        conn = _make_conn({
            "transcript": "Agent: Hi\nCaller: Hello",
            "summary_json": _EXISTING_SUMMARY,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID, force=True)

        assert result == _FAKE_SUMMARY
        mock_summarize.assert_awaited_once()
        conn.execute.assert_awaited_once()  # UPDATE was issued

    async def test_no_summary_json_calls_summarizer_and_writes(self):
        """Transcript present, no summary_json → summarize + UPDATE."""
        conn = _make_conn({
            "transcript": "Agent: Hello there!\nCaller: Hi, I'm interested.",
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result == _FAKE_SUMMARY
        mock_summarize.assert_awaited_once_with("Agent: Hello there!\nCaller: Hi, I'm interested.")
        conn.execute.assert_awaited_once()
        # Verify the UPDATE call contains the JSON-serialized summary
        call_args = conn.execute.await_args
        sql_arg = call_args[0][0]
        assert "UPDATE calls" in sql_arg
        json_arg = call_args[0][2]
        assert json.loads(json_arg) == _FAKE_SUMMARY

    async def test_failed_summary_not_persisted(self):
        """Summarizer fail-soft sentinel → return it but DON'T write the row.

        A transient Groq error returns {"headline": "Summary unavailable", ...}.
        Persisting it would poison the row: the idempotency check would then skip
        this call forever, leaving it permanently stuck. The summary is returned
        to the caller (so the UI can show something) but NOT written, so the next
        view / backfill retries.
        """
        conn = _make_conn({
            "transcript": "Agent: Hello\nCaller: Hi there",
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAILED_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result == _FAILED_SUMMARY      # caller still gets a dict to show
        mock_summarize.assert_awaited_once()  # it DID attempt summarization
        conn.execute.assert_not_called()      # but NO UPDATE was issued

    async def test_empty_transcript_returns_none(self):
        """transcript is empty string → return None, no summarizer call."""
        conn = _make_conn({
            "transcript": "",
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result is None
        mock_summarize.assert_not_called()

    async def test_whitespace_transcript_returns_none(self):
        """transcript is whitespace → return None, no summarizer call."""
        conn = _make_conn({
            "transcript": "   \n\t  ",
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result is None
        mock_summarize.assert_not_called()

    async def test_none_transcript_returns_none(self):
        """transcript is None → return None."""
        conn = _make_conn({
            "transcript": None,
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result is None
        mock_summarize.assert_not_called()

    async def test_row_not_found_returns_none(self):
        """fetchrow returns None → return None."""
        conn = _make_conn(None)

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                result = await generate_and_store(None, _TENANT_ID, _CALL_ID)

        assert result is None
        mock_summarize.assert_not_called()

    async def test_headline_written_to_summary_column(self):
        """The text summary column receives the headline string."""
        conn = _make_conn({
            "transcript": "Agent: Hi\nCaller: Hi",
            "summary_json": None,
        })

        mock_summarize = AsyncMock(return_value=_FAKE_SUMMARY)
        with _patch_acquire(conn):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                await generate_and_store(None, _TENANT_ID, _CALL_ID)

        call_args = conn.execute.await_args
        headline_arg = call_args[0][3]  # $3 in the UPDATE
        assert headline_arg == _FAKE_SUMMARY["headline"]
