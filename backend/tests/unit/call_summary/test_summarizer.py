"""Unit tests for app.domain.services.call_summary.summarizer.

Tests verify:
1. Schema completeness — model omits keys → they are filled from EMPTY_SUMMARY.
2. Empty transcript → headline "No conversation recorded", all keys present.
3. Malformed JSON on first call → retry → still malformed → fail-soft,
   all keys present, headline "Summary unavailable", no exception raised.
4. _coerce handles type mismatches (scalar instead of list, etc.).
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.call_summary.summarizer import (
    EMPTY_SUMMARY,
    _coerce,
    summarize_transcript,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA_KEYS = set(EMPTY_SUMMARY.keys())

_FULL_RESPONSE = {
    "headline": "Qualified — wants a demo next week",
    "outcome": "qualified — expressed strong interest and budget confirmed",
    "what_happened": "Agent introduced the product. Prospect asked about pricing. Agent provided a quote. Prospect agreed to a demo.",
    "key_points": ["Budget confirmed at $5k/mo", "Decision maker on the call"],
    "objections": [{"objection": "Price too high", "handled": "Offered 20% pilot discount"}],
    "commitments": ["Demo booked for Thursday 3pm"],
    "action_items": [{"item": "Send calendar invite", "owner": "agent"}],
    "sentiment": "positive — enthusiastic throughout",
    "next_step": "Demo call Thursday 3pm",
    "notable_quotes": ["That sounds exactly like what we need"],
}


def _fake_completion(content: str):
    """Build a fake Groq completion object with the given content string."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _make_async_groq(responses: list[str]):
    """Return a mock AsyncGroq whose chat.completions.create cycles through responses."""
    side_effects = [_fake_completion(r) for r in responses]
    mock_create = AsyncMock(side_effect=side_effects)
    mock_completions = SimpleNamespace(create=mock_create)
    mock_chat = SimpleNamespace(completions=mock_completions)
    mock_client = SimpleNamespace(chat=mock_chat)
    return mock_client


# ---------------------------------------------------------------------------
# _coerce unit tests (synchronous)
# ---------------------------------------------------------------------------

class TestCoerce:
    def test_all_keys_present_when_all_provided(self):
        result = _coerce(deepcopy(_FULL_RESPONSE))
        assert set(result.keys()) == _SCHEMA_KEYS

    def test_missing_keys_filled_from_empty(self):
        partial = {"headline": "Test", "outcome": "ok"}
        result = _coerce(partial)
        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["key_points"] == []
        assert result["objections"] == []
        assert result["commitments"] == []
        assert result["action_items"] == []
        assert result["notable_quotes"] == []
        assert result["what_happened"] == ""
        assert result["sentiment"] == ""
        assert result["next_step"] == ""

    def test_scalar_coerced_to_list_for_list_keys(self):
        raw = {**deepcopy(_FULL_RESPONSE), "key_points": "just one point"}
        result = _coerce(raw)
        assert isinstance(result["key_points"], list)
        assert result["key_points"] == ["just one point"]

    def test_extra_keys_dropped(self):
        raw = {**deepcopy(_FULL_RESPONSE), "unknown_field": "should be gone"}
        result = _coerce(raw)
        assert "unknown_field" not in result
        assert set(result.keys()) == _SCHEMA_KEYS

    def test_none_list_value_becomes_empty_list(self):
        raw = {**deepcopy(_FULL_RESPONSE), "commitments": None}
        result = _coerce(raw)
        assert result["commitments"] == []


# ---------------------------------------------------------------------------
# summarize_transcript async tests
# ---------------------------------------------------------------------------

class TestSummarizeTranscript:
    async def test_empty_transcript_returns_no_conversation(self):
        result = await summarize_transcript("")
        assert result["headline"] == "No conversation recorded"
        assert set(result.keys()) == _SCHEMA_KEYS

    async def test_whitespace_only_transcript_returns_no_conversation(self):
        result = await summarize_transcript("   \n\t  ")
        assert result["headline"] == "No conversation recorded"
        assert set(result.keys()) == _SCHEMA_KEYS

    async def test_partial_response_filled_with_empty_keys(self):
        """Model returns only headline + outcome — all 10 keys must appear."""
        partial = json.dumps({"headline": "Qualified", "outcome": "interested"})
        mock_client = _make_async_groq([partial])

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            result = await summarize_transcript("Agent: Hi\nCaller: Hello")

        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["headline"] == "Qualified"
        assert result["outcome"] == "interested"
        # Missing keys filled from EMPTY_SUMMARY
        assert result["key_points"] == []
        assert result["objections"] == []
        assert result["commitments"] == []

    async def test_full_valid_response_all_keys_present(self):
        mock_client = _make_async_groq([json.dumps(_FULL_RESPONSE)])

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            result = await summarize_transcript("Agent: Hi there\nCaller: Hi")

        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["headline"] == _FULL_RESPONSE["headline"]

    async def test_malformed_json_first_call_triggers_retry(self):
        """First call returns garbage JSON → retry → still bad → fail-soft."""
        mock_client = _make_async_groq(["not json at all {{{", "also bad json )))"])

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            result = await summarize_transcript("Agent: Hello\nCaller: Hi")

        # No exception raised
        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["headline"] == "Summary unavailable"

    async def test_malformed_first_valid_second_returns_parsed(self):
        """First call returns bad JSON, retry returns good JSON → use it."""
        mock_client = _make_async_groq([
            "not json {{{",
            json.dumps({"headline": "From retry", "outcome": "ok"}),
        ])

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            result = await summarize_transcript("Agent: Hello\nCaller: Hi")

        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["headline"] == "From retry"

    async def test_network_exception_returns_fail_soft(self):
        """Any exception from the Groq client → fail-soft, never raises."""
        mock_create = AsyncMock(side_effect=RuntimeError("network failure"))
        mock_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=mock_create))
        )

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            result = await summarize_transcript("Agent: Hello\nCaller: Hi")

        assert set(result.keys()) == _SCHEMA_KEYS
        assert result["headline"] == "Summary unavailable"

    async def test_no_exception_raised_on_any_error(self):
        """Critically: summarize_transcript must NEVER raise."""
        mock_create = AsyncMock(side_effect=Exception("anything"))
        mock_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=mock_create))
        )

        with patch(
            "app.domain.services.call_summary.summarizer.AsyncGroq",
            return_value=mock_client,
        ):
            # Should not raise
            result = await summarize_transcript("some transcript")

        assert isinstance(result, dict)
