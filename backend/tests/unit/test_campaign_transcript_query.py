"""Unit tests for campaign_transcript_query + transcript_formatting."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scripts.campaign_transcript_query import (
    _coerce_turns,
    fetch_campaign_transcripts,
)
from app.services.scripts.transcript_formatting import (
    format_transcript_turn,
    format_transcript_turns,
)


# --- formatter --------------------------------------------------------------


def test_format_turn_normalises_shape():
    out = format_transcript_turn({
        "role": "user",
        "content": "  hi  ",
        "timestamp": "2026-04-22T00:00:00Z",
        "event_type": "end_of_turn",
    })
    assert out == {
        "role": "user",
        "content": "hi",
        "timestamp": "2026-04-22T00:00:00Z",
    }


def test_format_turn_defaults_to_assistant_when_role_missing():
    out = format_transcript_turn({"content": "hi"})
    assert out["role"] == "assistant"
    assert out["timestamp"] == ""


def test_format_turns_drops_partials_and_empties():
    raw = [
        {"role": "user", "content": "hello", "timestamp": "t1", "include_in_plaintext": True},
        {"role": "user", "content": "partial...", "timestamp": "t2", "include_in_plaintext": False},
        {"role": "assistant", "content": "hi there", "timestamp": "t3"},
        {"role": "user", "content": "   ", "timestamp": "t4"},
        {"role": "system", "content": "should drop", "timestamp": "t5"},
        "not a dict",
    ]
    out = format_transcript_turns(raw)
    assert [t["content"] for t in out] == ["hello", "hi there"]


def test_format_turns_handles_none_and_empty():
    assert format_transcript_turns(None) == []
    assert format_transcript_turns([]) == []


# --- coercion --------------------------------------------------------------


def test_coerce_turns_accepts_list():
    raw = [{"role": "user", "content": "hi"}]
    assert _coerce_turns(raw) == raw


def test_coerce_turns_parses_string():
    raw = json.dumps([{"role": "user", "content": "hi"}])
    assert _coerce_turns(raw) == [{"role": "user", "content": "hi"}]


def test_coerce_turns_returns_empty_on_garbage():
    assert _coerce_turns(None) == []
    assert _coerce_turns("") == []
    assert _coerce_turns("not json") == []
    assert _coerce_turns(12345) == []


# --- fetch_campaign_transcripts --------------------------------------------


class _PoolCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_fetch_returns_paginated_calls_with_turns():
    tenant_id = "00000000-0000-0000-0000-0000000000b1"
    campaign_id = "00000000-0000-0000-0000-0000000000c1"
    call_id = "00000000-0000-0000-0000-000000000001"

    row = SimpleNamespace()

    def _getitem(self, key):
        return self.__dict__[key]

    # dict-style access (asyncpg Records support [])
    row_data = {
        "id": call_id,
        "phone_number": "+15551234",
        "created_at": datetime(2026, 4, 22, 13, 45, tzinfo=timezone.utc),
        "duration_seconds": 87,
        "outcome": "goal_achieved",
        "transcript_json": [
            {
                "role": "user", "content": "Hi", "timestamp": "2026-04-22T13:45:05Z",
                "include_in_plaintext": True, "event_type": "end_of_turn",
            },
            {
                "role": "assistant", "content": "Hello!", "timestamp": "2026-04-22T13:45:06Z",
                "include_in_plaintext": True, "event_type": "utterance",
            },
            {
                "role": "user", "content": "partial", "timestamp": "2026-04-22T13:45:07Z",
                "include_in_plaintext": False, "event_type": "update",
            },
        ],
    }

    class _Row(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    r = _Row(row_data)

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[r])
    conn.fetchval = AsyncMock(return_value=1)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_PoolCM(conn))

    result = await fetch_campaign_transcripts(
        pool=pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        page=1,
        page_size=20,
    )

    assert result["total"] == 1
    assert result["page"] == 1
    assert result["page_size"] == 20
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["to_number"] == "+15551234"
    assert item["outcome"] == "goal_achieved"
    assert item["duration_seconds"] == 87
    # Partial dropped; only user + assistant final turns.
    assert len(item["turns"]) == 2
    assert item["turns"][0]["role"] == "user"
    assert item["turns"][1]["role"] == "assistant"
    # timestamp survives through formatting.
    assert item["turns"][0]["timestamp"] == "2026-04-22T13:45:05Z"


@pytest.mark.asyncio
async def test_fetch_raises_on_invalid_campaign_id():
    pool = MagicMock()
    with pytest.raises(ValueError):
        await fetch_campaign_transcripts(
            pool=pool,
            tenant_id="00000000-0000-0000-0000-000000000001",
            campaign_id="not-a-uuid",
            page=1,
            page_size=20,
        )
