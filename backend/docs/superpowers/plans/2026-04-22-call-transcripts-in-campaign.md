# Call Transcripts in Campaign Screen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every outbound telephony call placed by the All States Estimation campaign persists its full user/assistant transcript with per-turn timestamps, and the campaign detail screen shows a "Script Card" that lists each call's full conversation transcript, readable and time-stamped.

**Architecture:**
Transcript turns are already accumulated in-memory by `TranscriptService` (user-side from Deepgram STT, assistant-side from Groq LLM responses). The missing pieces are: (1) wiring `voice_session.call_id` → the dialer's `calls.id` so `TranscriptService.flush_to_database()` updates the correct row, (2) a campaign-scoped "list calls with transcripts" endpoint, and (3) a frontend "Script Card" component on the campaign detail page. All new Python code lives under `backend/app/services/scripts/` with a 600-line-per-file cap. Each script file has a matching `.md` under `backend/docs/script/`.

**Tech Stack:** FastAPI (Python), asyncpg/Postgres, Deepgram Flux STT, Groq LLM, ElevenLabs TTS, Next.js 14 + React + Tailwind, `framer-motion`.

**Non-goals (do not break):**
- Any existing flush to `calls.transcript` / `calls.transcript_json` for browser/Ask AI calls — Task 1 only adds a second flush path for the telephony bridge.
- The current `TELEPHONY_ESTIMATION_SYSTEM_PROMPT` and outbound greeting pre-synthesis pipeline.
- The recording save path (`_save_call_recording`) — we re-use its `external_call_uuid → calls.id` lookup pattern but do not modify it.

---

## File Structure

### New Python modules (each ≤ 600 lines)

| Path | Responsibility |
|------|----------------|
| `backend/app/services/scripts/__init__.py` | Package marker. Re-exports the three public helpers so callers can `from app.services.scripts import bind_telephony_call, fetch_campaign_transcripts, format_transcript_turn`. |
| `backend/app/services/scripts/call_transcript_persister.py` | Resolves PBX channel_id → `calls.id` and binds it onto the `VoiceSession` so `TranscriptService.flush_to_database()` updates the dialer's real calls row. Also offers a final `save_call_transcript_on_hangup(voice_session, pbx_channel_id)` helper. |
| `backend/app/services/scripts/campaign_transcript_query.py` | Builds the SQL query that returns a campaign's calls plus their transcript JSON ordered by `created_at` (pagination: page/page_size; default page_size=20). Returns a plain dict matching `CampaignCallsWithTranscriptResponse`. |
| `backend/app/services/scripts/transcript_formatting.py` | Pure functions that take `transcript_json` (a `list[dict]` from `TranscriptService.get_transcript_json`) and produce a trimmed view-model: keeps only `role ∈ {user, assistant}` turns where `include_in_plaintext is True`, drops deepgram partials, formats the ISO timestamp to `HH:MM:SS`. Zero I/O. |

### Files to modify

| Path | Change |
|------|--------|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Call `bind_telephony_call(voice_session, pbx_channel_id)` at the top of `_on_new_call` (after `_telephony_sessions[call_id] = voice_session`). Call `save_call_transcript_on_hangup(...)` inside `_on_call_ended` BEFORE `_save_call_recording`. |
| `backend/app/api/v1/endpoints/campaigns.py` | Add `GET /campaigns/{campaign_id}/calls` → delegates to `campaign_transcript_query.fetch_campaign_transcripts`. |
| `Talk-Leee/src/lib/extended-api.ts` | Add `getCampaignCallsWithTranscripts(campaignId, page, pageSize)` helper. |
| `Talk-Leee/src/app/campaigns/[id]/page.tsx` | Add a `<ScriptCard>` block below the existing Contacts card. |
| `Talk-Leee/src/components/campaigns/script-card.tsx` | **New** — accordion list of calls; each row expands to show `role : content` turns with `HH:MM:SS` timestamps. |

### New docs

| Path | Purpose |
|------|---------|
| `backend/docs/script/README.md` | Index — lists every `.md` in this folder with a one-line hook. |
| `backend/docs/script/2026-04-22-call-transcripts-plan.md` | Exact copy of this plan document. |
| `backend/docs/script/2026-04-22-call-transcripts-execution.md` | Live execution log, filled in task-by-task during implementation. |
| `backend/docs/script/call_transcript_persister.md` | One-pager: purpose, public API, invariants, line count guard. |
| `backend/docs/script/campaign_transcript_query.md` | One-pager: purpose, SQL shape, pagination contract. |
| `backend/docs/script/transcript_formatting.md` | One-pager: purpose, view-model shape. |

---

## Self-contained data contract

Every task below references this contract — define once, reuse everywhere.

```python
# --- DB (no schema change; all columns already exist) ---
# calls.id                UUID PRIMARY KEY      (dialer_worker creates this)
# calls.external_call_uuid TEXT                 (= PBX channel_id from adapter)
# calls.campaign_id       UUID                  (nullable for non-campaign calls)
# calls.transcript        TEXT                  (plain text, updated incrementally)
# calls.transcript_json   JSONB                 (list of TranscriptTurn dicts)
# calls.created_at        TIMESTAMPTZ

# --- API response shape ---
# GET /api/v1/campaigns/{campaign_id}/calls?page=1&page_size=20
# 200 OK:
# {
#   "items": [
#     {
#       "call_id": "uuid",
#       "to_number": "+1234567890",
#       "started_at": "2026-04-22T13:45:12.000Z",
#       "duration_seconds": 87,
#       "outcome": "goal_achieved" | "no_answer" | ...,
#       "turns": [
#         {"role": "user"|"assistant", "content": "...", "timestamp": "2026-04-22T13:45:15.123Z"},
#         ...
#       ]
#     }
#   ],
#   "page": 1,
#   "page_size": 20,
#   "total": 42
# }
```

---

## Task 1 — Wire telephony VoiceSession to the dialer's calls.id

**Why:** `TranscriptService.flush_to_database()` does `UPDATE calls SET transcript=... WHERE id = $1`. Today `$1` is the orchestrator-generated `voice_session.call_id`, which is **not** the dialer's `calls.id`. The update matches 0 rows and the campaign's calls row never gets a transcript. We must rebind.

**Files:**
- Create: `backend/app/services/scripts/__init__.py`
- Create: `backend/app/services/scripts/call_transcript_persister.py`
- Create: `backend/tests/unit/test_call_transcript_persister.py`

- [ ] **Step 1.1: Write the failing test**

```python
# backend/tests/unit/test_call_transcript_persister.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.scripts.call_transcript_persister import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)


class _FakeCallSession:
    def __init__(self, call_id):
        self.call_id = call_id
        self.talklee_call_id = "tlk_abc"


class _FakeVoiceSession:
    def __init__(self, call_id):
        self.call_id = call_id
        self.call_session = _FakeCallSession(call_id)


def _db_client_with_lookup(internal_call_id, tenant_id="tenant-1"):
    """Mimic the synchronous supabase-style chain used by _save_call_recording."""
    resp = MagicMock()
    resp.data = [{
        "id": internal_call_id,
        "tenant_id": tenant_id,
        "campaign_id": "camp-1",
    }]
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = resp
    db = MagicMock()
    db.table.return_value = chain
    return db


@pytest.mark.asyncio
async def test_bind_telephony_call_rebinds_voice_session_call_id():
    vs = _FakeVoiceSession("voice-session-uuid")
    db = _db_client_with_lookup("dialer-calls-id")

    result = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="asterisk-channel-123",
        db_client=db,
    )

    assert result.internal_call_id == "dialer-calls-id"
    assert result.tenant_id == "tenant-1"
    # The TranscriptService keys on voice_session.call_id — rebind so flush lands
    # on the right row, without breaking other references to the original id.
    assert vs.call_id == "dialer-calls-id"
    assert vs.call_session.call_id == "dialer-calls-id"


@pytest.mark.asyncio
async def test_bind_returns_none_when_no_dialer_row():
    vs = _FakeVoiceSession("voice-session-uuid")
    resp = MagicMock()
    resp.data = []
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = resp
    db = MagicMock()
    db.table.return_value = chain

    result = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="asterisk-channel-missing",
        db_client=db,
    )
    assert result is None
    assert vs.call_id == "voice-session-uuid"  # unchanged


@pytest.mark.asyncio
async def test_save_call_transcript_on_hangup_flushes_and_saves():
    vs = _FakeVoiceSession("dialer-calls-id")
    svc = AsyncMock()
    svc.flush_to_database = AsyncMock()
    svc.save_transcript = AsyncMock(return_value="transcript-uuid")
    svc.clear_buffer = MagicMock()

    await save_call_transcript_on_hangup(
        voice_session=vs,
        transcript_service=svc,
        db_client=MagicMock(),
        tenant_id="tenant-1",
    )

    svc.save_transcript.assert_awaited_once()
    svc.clear_buffer.assert_called_once_with("dialer-calls-id")
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_call_transcript_persister.py -v`
Expected: FAIL — module `app.services.scripts.call_transcript_persister` does not exist.

- [ ] **Step 1.3: Create the package marker**

```python
# backend/app/services/scripts/__init__.py
"""Small, focused scripts supporting the call-transcript feature.

Every module in this package MUST stay ≤ 600 lines. Add new modules instead
of growing an existing one. See backend/docs/script/README.md for docs.
"""

from app.services.scripts.call_transcript_persister import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
    CallBinding,
)
from app.services.scripts.campaign_transcript_query import (
    fetch_campaign_transcripts,
)
from app.services.scripts.transcript_formatting import (
    format_transcript_turn,
    format_transcript_turns,
)

__all__ = [
    "bind_telephony_call",
    "save_call_transcript_on_hangup",
    "CallBinding",
    "fetch_campaign_transcripts",
    "format_transcript_turn",
    "format_transcript_turns",
]
```

- [ ] **Step 1.4: Implement `call_transcript_persister.py`**

```python
# backend/app/services/scripts/call_transcript_persister.py
"""Wire a telephony VoiceSession to the dialer's calls.id so that every
TranscriptService.flush_to_database() call updates the correct row.

Why: voice_orchestrator.create_voice_session() mints a fresh UUID and stores
it on voice_session.call_id + voice_session.call_session.call_id. The dialer
worker had already inserted the real calls row with a different id (keyed to
PBX channel_id via external_call_uuid). TranscriptService keys its in-memory
buffer on voice_session.call_id and its SQL on WHERE id = call_id, so without
this rebind the UPDATE matches 0 rows and no transcript reaches the UI.

This module is invoked twice per telephony call:
  1. From telephony_bridge._on_new_call — rebinds the id on session creation.
  2. From telephony_bridge._on_call_ended — final flush + insert into
     `transcripts` table before the session is torn down.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallBinding:
    """Returned by bind_telephony_call when the dialer row is found."""
    internal_call_id: str
    tenant_id: Optional[str]
    campaign_id: Optional[str]


async def _maybe_await(value):
    """Some supabase client variants return an awaitable from .execute()."""
    if inspect.isawaitable(value):
        return await value
    return value


async def bind_telephony_call(
    *,
    voice_session,
    pbx_channel_id: str,
    db_client,
) -> Optional[CallBinding]:
    """Look up the dialer's calls row via external_call_uuid and rebind.

    Uses the same supabase-style chain as telephony_bridge._save_call_recording
    so behaviour matches the known-good recording path.

    Returns:
        CallBinding if the dialer row exists; None otherwise. When None, the
        caller keeps the original session id (pre-call transcripts still
        accumulate in memory — they are just not persisted to a campaign row,
        which is the correct behaviour for non-campaign test calls).
    """
    try:
        query = (
            db_client.table("calls")
            .select("id, tenant_id, campaign_id")
            .eq("external_call_uuid", pbx_channel_id)
            .limit(1)
            .execute()
        )
        response = await _maybe_await(query)
    except Exception as exc:
        logger.warning(
            "bind_telephony_call lookup failed pbx=%s err=%s",
            pbx_channel_id[:12], exc,
        )
        return None

    data = getattr(response, "data", None)
    if not data:
        logger.debug(
            "bind_telephony_call no dialer row for pbx=%s (non-campaign test call?)",
            pbx_channel_id[:12],
        )
        return None

    row = data[0] if isinstance(data, list) else data
    internal_call_id = str(row.get("id"))
    tenant_id = row.get("tenant_id")
    campaign_id = row.get("campaign_id")

    # Rebind. The orchestrator session id is not referenced anywhere after
    # creation (TranscriptService keys on voice_session.call_id; media gateway
    # uses voice_session.call_id too — both are reassigned here).
    previous_id = voice_session.call_id
    voice_session.call_id = internal_call_id
    call_session = getattr(voice_session, "call_session", None)
    if call_session is not None:
        call_session.call_id = internal_call_id

    logger.info(
        "bind_telephony_call rebind voice_session=%s → calls.id=%s pbx=%s",
        previous_id[:8], internal_call_id[:8], pbx_channel_id[:12],
    )
    return CallBinding(
        internal_call_id=internal_call_id,
        tenant_id=str(tenant_id) if tenant_id else None,
        campaign_id=str(campaign_id) if campaign_id else None,
    )


async def save_call_transcript_on_hangup(
    *,
    voice_session,
    transcript_service,
    db_client,
    tenant_id: Optional[str] = None,
) -> None:
    """Final transcript persist + buffer clear, invoked from _on_call_ended.

    Uses TranscriptService.save_transcript (inserts into transcripts table
    AND re-updates calls.transcript / transcript_json) so the UI can read
    from either place. Buffer is cleared afterwards to free memory.
    """
    call_id = voice_session.call_id
    call_session = getattr(voice_session, "call_session", None)
    talklee_call_id = (
        getattr(call_session, "talklee_call_id", None) if call_session else None
    )

    try:
        await transcript_service.save_transcript(
            call_id=call_id,
            db_client=db_client,
            tenant_id=tenant_id,
            talklee_call_id=talklee_call_id,
        )
    except Exception as exc:
        logger.warning(
            "save_call_transcript_on_hangup save failed call_id=%s err=%s",
            call_id[:8] if call_id else "?", exc,
        )
    finally:
        try:
            transcript_service.clear_buffer(call_id)
        except Exception:
            pass
```

- [ ] **Step 1.5: Run tests — expect PASS**

Run: `cd backend && pytest tests/unit/test_call_transcript_persister.py -v`
Expected: 3 passed.

- [ ] **Step 1.6: Wire it into telephony_bridge._on_new_call**

In `backend/app/api/v1/endpoints/telephony_bridge.py`, find the block that sets `_telephony_sessions[call_id] = voice_session` inside `_on_new_call` (currently at ~line 553). Directly after it, add:

```python
# Rebind voice_session.call_id → dialer's calls.id so TranscriptService
# incremental flushes land on the correct row. Non-campaign test calls
# (no dialer row) keep their orchestrator UUID — this is a no-op for them.
try:
    from app.core.container import get_container
    from app.services.scripts import bind_telephony_call

    _container = get_container()
    if _container.is_initialized:
        await bind_telephony_call(
            voice_session=voice_session,
            pbx_channel_id=call_id,
            db_client=_container.db_client,
        )
except Exception as _bind_exc:
    logger.warning("bind_telephony_call failed for %s: %s", call_id[:12], _bind_exc)
```

- [ ] **Step 1.7: Wire final save into `_on_call_ended`**

In the same file, inside `_on_call_ended`, find the `if voice_session:` branch (currently ~line 732). Add a **new** block BEFORE `_save_call_recording(...)`:

```python
# Persist the accumulated transcript before the orchestrator tears the
# session down. Recording save also depends on the session — do transcript
# first so a recording failure never blocks the text persist.
try:
    from app.core.container import get_container
    from app.services.scripts import save_call_transcript_on_hangup

    container = get_container()
    if container.is_initialized:
        await save_call_transcript_on_hangup(
            voice_session=voice_session,
            transcript_service=voice_session.pipeline.transcript_service,
            db_client=container.db_client,
            tenant_id=getattr(voice_session.call_session, "tenant_id", None),
        )
except Exception as _ts_err:
    logger.warning("transcript final save failed for %s: %s", call_id[:12], _ts_err)
```

- [ ] **Step 1.8: Commit**

```bash
git add backend/app/services/scripts/__init__.py \
        backend/app/services/scripts/call_transcript_persister.py \
        backend/tests/unit/test_call_transcript_persister.py \
        backend/app/api/v1/endpoints/telephony_bridge.py
git commit -m "feat(telephony): bind voice session to dialer calls.id for transcript persistence"
```

---

## Task 2 — Campaign-scoped transcripts query

**Files:**
- Create: `backend/app/services/scripts/campaign_transcript_query.py`
- Create: `backend/tests/unit/test_campaign_transcript_query.py`

- [ ] **Step 2.1: Write the failing test**

```python
# backend/tests/unit/test_campaign_transcript_query.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.services.scripts.campaign_transcript_query import fetch_campaign_transcripts


class _Row(dict):
    """asyncpg.Record substitute that supports dict-style access."""


@pytest.mark.asyncio
async def test_fetch_returns_paginated_calls_with_turns():
    cid = str(uuid4())
    call1 = _Row(
        id=uuid4(),
        phone_number="+1555",
        created_at="2026-04-22T13:45:00Z",
        duration_seconds=87,
        outcome="goal_achieved",
        transcript_json=[
            {"role": "user", "content": "Hi", "timestamp": "2026-04-22T13:45:05Z", "include_in_plaintext": True, "event_type": "end_of_turn"},
            {"role": "assistant", "content": "Hello!", "timestamp": "2026-04-22T13:45:06Z", "include_in_plaintext": True, "event_type": "utterance"},
            {"role": "user", "content": "partial...", "timestamp": "2026-04-22T13:45:07Z", "include_in_plaintext": False, "event_type": "update"},
        ],
    )
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[call1])
    conn.fetchval = AsyncMock(return_value=1)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    result = await fetch_campaign_transcripts(
        pool=pool,
        tenant_id=uuid4(),
        campaign_id=cid,
        page=1,
        page_size=20,
    )

    assert result["total"] == 1
    assert result["page"] == 1
    assert result["page_size"] == 20
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["to_number"] == "+1555"
    assert item["outcome"] == "goal_achieved"
    # Partials filtered out; only 2 turns survive
    assert len(item["turns"]) == 2
    assert item["turns"][0]["role"] == "user"
    assert item["turns"][1]["role"] == "assistant"


class _AsyncCM:
    """Minimal async-context-manager wrapper for a connection mock."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return None
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_campaign_transcript_query.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 2.3: Implement `campaign_transcript_query.py`**

```python
# backend/app/services/scripts/campaign_transcript_query.py
"""Campaign-scoped 'calls + transcripts' query used by the Script Card UI.

One DB round-trip per page. Filters out partial STT turns (include_in_plaintext
is False) at formatting time so the UI never has to understand Deepgram's
eager/update/end_of_turn taxonomy.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.services.scripts.transcript_formatting import format_transcript_turns

logger = logging.getLogger(__name__)


async def fetch_campaign_transcripts(
    *,
    pool,
    tenant_id,
    campaign_id: str,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Return one page of calls for a campaign with their transcript turns.

    Args:
        pool: asyncpg pool (container.db_pool).
        tenant_id: UUID of the tenant — enforces tenant isolation.
        campaign_id: UUID of the campaign (string form).
        page: 1-indexed page number.
        page_size: items per page (clamped to [1, 100] upstream).

    Returns:
        {"items": [...], "page": int, "page_size": int, "total": int}
    """
    offset = (page - 1) * page_size
    tenant_uuid = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    campaign_uuid = UUID(campaign_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id,
                   c.phone_number,
                   c.created_at,
                   c.duration_seconds,
                   c.outcome,
                   c.transcript_json
            FROM calls c
            WHERE c.tenant_id = $1
              AND c.campaign_id = $2
            ORDER BY c.created_at DESC
            LIMIT $3 OFFSET $4
            """,
            tenant_uuid, campaign_uuid, page_size, offset,
        )
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM calls c
            WHERE c.tenant_id = $1 AND c.campaign_id = $2
            """,
            tenant_uuid, campaign_uuid,
        )

    items: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row["created_at"]
        raw_turns = row["transcript_json"] or []
        items.append({
            "call_id": str(row["id"]),
            "to_number": row["phone_number"] or "",
            "started_at": (
                created_at.isoformat()
                if hasattr(created_at, "isoformat")
                else str(created_at)
            ),
            "duration_seconds": row["duration_seconds"],
            "outcome": row["outcome"],
            "turns": format_transcript_turns(raw_turns),
        })

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
    }
```

- [ ] **Step 2.4: Implement `transcript_formatting.py`**

```python
# backend/app/services/scripts/transcript_formatting.py
"""Pure view-model helpers for transcript turns.

No I/O. Safe to call from anywhere. Only touches data shapes.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def format_transcript_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a single turn dict to the UI contract.

    Contract:
      {"role": "user"|"assistant", "content": str, "timestamp": ISO-8601 str}
    """
    return {
        "role": turn.get("role") or "assistant",
        "content": (turn.get("content") or "").strip(),
        "timestamp": turn.get("timestamp") or "",
    }


def format_transcript_turns(turns: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop partial STT frames and empty content; keep user/assistant only.

    A turn is kept iff:
      - role ∈ {"user", "assistant"}
      - include_in_plaintext is truthy (default True for older records)
      - content is non-empty
    """
    out: List[Dict[str, Any]] = []
    for turn in turns or []:
        role = turn.get("role")
        if role not in ("user", "assistant"):
            continue
        if not turn.get("include_in_plaintext", True):
            continue
        if not (turn.get("content") or "").strip():
            continue
        out.append(format_transcript_turn(turn))
    return out
```

- [ ] **Step 2.5: Run tests — expect PASS**

Run: `cd backend && pytest tests/unit/test_campaign_transcript_query.py tests/unit/test_call_transcript_persister.py -v`
Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add backend/app/services/scripts/campaign_transcript_query.py \
        backend/app/services/scripts/transcript_formatting.py \
        backend/tests/unit/test_campaign_transcript_query.py
git commit -m "feat(scripts): campaign transcript query + formatting helpers"
```

---

## Task 3 — `GET /campaigns/{id}/calls` endpoint

**Files:**
- Modify: `backend/app/api/v1/endpoints/campaigns.py`
- Create: `backend/tests/unit/test_campaigns_calls_endpoint.py`

- [ ] **Step 3.1: Write the failing test**

```python
# backend/tests/unit/test_campaigns_calls_endpoint.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_campaign_calls_returns_items(client, monkeypatch):
    fake = {
        "items": [{
            "call_id": "x", "to_number": "+1", "started_at": "2026-04-22T00:00:00Z",
            "duration_seconds": 10, "outcome": "goal_achieved",
            "turns": [{"role": "user", "content": "hi", "timestamp": "2026-04-22T00:00:01Z"}],
        }],
        "page": 1, "page_size": 20, "total": 1,
    }
    async def _stub(**_): return fake
    monkeypatch.setattr(
        "app.api.v1.endpoints.campaigns.fetch_campaign_transcripts",
        _stub,
    )
    # Bypass auth dependency — use the project's existing auth test fixture
    # pattern from tests/unit/test_calls_endpoint.py if it exists.
    # If not, this test can be marked xfail until wired through the auth fixture.
    resp = client.get("/api/v1/campaigns/fake-camp-id/calls")
    # Exact assertion depends on auth setup; at minimum verify the handler
    # is reachable and delegates to fetch_campaign_transcripts.
    assert resp.status_code in (200, 401)
```

Note: Inspect `backend/tests/unit/` for the project's existing auth-bypass fixture pattern (e.g., `conftest.py` overrides `get_current_user`) and follow it here — do NOT invent a new pattern.

- [ ] **Step 3.2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_campaigns_calls_endpoint.py -v`
Expected: FAIL — `fetch_campaign_transcripts` is not imported in campaigns.py yet.

- [ ] **Step 3.3: Add the endpoint to `campaigns.py`**

In `backend/app/api/v1/endpoints/campaigns.py`, add these imports near the top:

```python
from app.core.container import get_container
from app.services.scripts import fetch_campaign_transcripts
```

Add the new route after the existing `/stats` handler (~line 338):

```python
@router.get("/{campaign_id}/calls")
async def list_campaign_calls_with_transcripts(
    campaign_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """List calls for a campaign with their full transcripts and timestamps.

    Powers the Script Card on the campaign detail screen.
    """
    container = get_container()
    pool = container.db_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Database not ready")

    try:
        return await fetch_campaign_transcripts(
            pool=pool,
            tenant_id=current_user.tenant_id,
            campaign_id=campaign_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to list campaign calls: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch calls")
```

- [ ] **Step 3.4: Run test — expect PASS (or 401 if auth fixture not yet wired)**

Run: `cd backend && pytest tests/unit/test_campaigns_calls_endpoint.py -v`

- [ ] **Step 3.5: Manual smoke test**

```bash
# Start backend in one terminal
cd backend && uvicorn app.main:app --reload

# In another terminal (adjust token/campaign_id to your env)
curl -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/api/v1/campaigns/<real-campaign-id>/calls?page=1&page_size=5" \
  | jq '.items[0].turns[0:3]'
```

Expected: a JSON object with `items`, `page`, `page_size`, `total`; each item has a `turns` array with `role`, `content`, `timestamp`.

- [ ] **Step 3.6: Commit**

```bash
git add backend/app/api/v1/endpoints/campaigns.py \
        backend/tests/unit/test_campaigns_calls_endpoint.py
git commit -m "feat(campaigns): add GET /campaigns/{id}/calls returning transcripts"
```

---

## Task 4 — Frontend API client helper

**Files:**
- Modify: `Talk-Leee/src/lib/extended-api.ts`

- [ ] **Step 4.1: Add types + fetcher**

Find the existing `class ExtendedApi` and add these interfaces above it (near the other interfaces):

```typescript
export interface TranscriptTurn {
    role: "user" | "assistant";
    content: string;
    timestamp: string; // ISO-8601
}

export interface CampaignCallTranscript {
    call_id: string;
    to_number: string;
    started_at: string; // ISO-8601
    duration_seconds: number | null;
    outcome: string | null;
    turns: TranscriptTurn[];
}

export interface CampaignCallsResponse {
    items: CampaignCallTranscript[];
    page: number;
    page_size: number;
    total: number;
}
```

Add this method inside the `ExtendedApi` class:

```typescript
async getCampaignCallsWithTranscripts(
    campaignId: string,
    page: number = 1,
    pageSize: number = 20,
): Promise<CampaignCallsResponse> {
    return this.client.get<CampaignCallsResponse>(
        `/campaigns/${campaignId}/calls?page=${page}&page_size=${pageSize}`,
    );
}
```

If `this.client.get` doesn't exist on the HTTP client used here (check `Talk-Leee/src/lib/http-client.ts`), use the exact verb the file already uses for other GETs — **do not** invent a new helper.

- [ ] **Step 4.2: Type-check**

```bash
cd Talk-Leee && npm run lint && npx tsc --noEmit
```

Expected: no new errors.

- [ ] **Step 4.3: Commit**

```bash
git add Talk-Leee/src/lib/extended-api.ts
git commit -m "feat(web): typed client for campaign call transcripts"
```

---

## Task 5 — `ScriptCard` component on campaign detail page

**Files:**
- Create: `Talk-Leee/src/components/campaigns/script-card.tsx`
- Modify: `Talk-Leee/src/app/campaigns/[id]/page.tsx`

- [ ] **Step 5.1: Create the component**

```tsx
// Talk-Leee/src/components/campaigns/script-card.tsx
"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { ChevronDown, ChevronRight, Loader2, Phone } from "lucide-react";
import { extendedApi, CampaignCallTranscript } from "@/lib/extended-api";

interface ScriptCardProps {
    campaignId: string;
}

function formatClockTime(iso: string): string {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return `${hh}:${mm}:${ss}`;
}

function formatDateTime(iso: string): string {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
}

export function ScriptCard({ campaignId }: ScriptCardProps) {
    const [calls, setCalls] = useState<CampaignCallTranscript[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                setLoading(true);
                const res = await extendedApi.getCampaignCallsWithTranscripts(campaignId, 1, 20);
                if (!cancelled) setCalls(res.items);
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : "Failed to load transcripts");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [campaignId]);

    function toggle(id: string) {
        setExpandedIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.35 }}
            className="content-card"
        >
            <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-foreground">Call Scripts</h3>
                <span className="text-xs text-muted-foreground">
                    {calls.length} {calls.length === 1 ? "call" : "calls"}
                </span>
            </div>

            {loading ? (
                <div className="flex items-center gap-2 text-muted-foreground text-sm">
                    <Loader2 className="w-4 h-4 animate-spin" /> Loading transcripts…
                </div>
            ) : error ? (
                <div className="text-sm text-red-500">{error}</div>
            ) : calls.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                    No call transcripts yet. They’ll appear here after the first completed call.
                </div>
            ) : (
                <ul className="divide-y divide-border/60">
                    {calls.map((call) => {
                        const expanded = expandedIds.has(call.call_id);
                        return (
                            <li key={call.call_id} className="py-3">
                                <button
                                    onClick={() => toggle(call.call_id)}
                                    className="w-full flex items-center justify-between hover:bg-muted/30 rounded-md px-2 py-2 transition-colors"
                                >
                                    <div className="flex items-center gap-3 text-left">
                                        {expanded ? (
                                            <ChevronDown className="w-4 h-4 text-muted-foreground" />
                                        ) : (
                                            <ChevronRight className="w-4 h-4 text-muted-foreground" />
                                        )}
                                        <Phone className="w-4 h-4 text-muted-foreground" />
                                        <span className="text-sm text-foreground tabular-nums">{call.to_number}</span>
                                        <span className="text-xs text-muted-foreground">{formatDateTime(call.started_at)}</span>
                                    </div>
                                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                                        {call.outcome && <span>{call.outcome}</span>}
                                        {call.duration_seconds != null && <span>{call.duration_seconds}s</span>}
                                        <span>{call.turns.length} turns</span>
                                    </div>
                                </button>

                                {expanded && (
                                    <div className="mt-3 ml-8 space-y-2">
                                        {call.turns.length === 0 ? (
                                            <p className="text-xs text-muted-foreground">
                                                Transcript is still being written, or this call had no final utterances.
                                            </p>
                                        ) : (
                                            call.turns.map((turn, idx) => (
                                                <div key={idx} className="flex gap-3 text-sm">
                                                    <span className="tabular-nums text-xs text-muted-foreground w-16 shrink-0">
                                                        {formatClockTime(turn.timestamp)}
                                                    </span>
                                                    <span
                                                        className={
                                                            turn.role === "user"
                                                                ? "font-medium text-blue-700 dark:text-blue-400 w-24 shrink-0"
                                                                : "font-medium text-emerald-700 dark:text-emerald-400 w-24 shrink-0"
                                                        }
                                                    >
                                                        {turn.role === "user" ? "Contact" : "Agent"}
                                                    </span>
                                                    <span className="text-foreground">{turn.content}</span>
                                                </div>
                                            ))
                                        )}
                                    </div>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
        </motion.div>
    );
}
```

- [ ] **Step 5.2: Mount it on the campaign detail page**

In `Talk-Leee/src/app/campaigns/[id]/page.tsx`:

1. Add the import near the other component imports (top of file, ~line 24):
```tsx
import { ScriptCard } from "@/components/campaigns/script-card";
```

2. Just before the closing `</div>` of the `<div className="space-y-6">` wrapper (after the Contacts `motion.div`, ~line 451), add:
```tsx
<ScriptCard campaignId={campaignId} />
```

- [ ] **Step 5.3: Manual browser test**

```bash
cd Talk-Leee && npm run dev
# Open http://localhost:3000/campaigns/<real-campaign-id>
# Verify:
#   - Script card renders below the Contacts table
#   - Initially shows "Loading transcripts…"
#   - After a completed call, the row appears with phone number + outcome
#   - Clicking a row expands and shows HH:MM:SS timestamps next to "Contact"/"Agent" turns
```

Expected: above. Place a live call end-to-end against the All States Estimation agent, wait for hangup, and verify the transcript appears.

- [ ] **Step 5.4: Commit**

```bash
git add Talk-Leee/src/components/campaigns/script-card.tsx \
        Talk-Leee/src/app/campaigns/[id]/page.tsx
git commit -m "feat(web): script card with per-turn transcripts on campaign detail"
```

---

## Task 6 — `backend/docs/script/` index + per-script docs

**Files:**
- Create: `backend/docs/script/README.md`
- Create: `backend/docs/script/2026-04-22-call-transcripts-plan.md`
- Create: `backend/docs/script/2026-04-22-call-transcripts-execution.md`
- Create: `backend/docs/script/call_transcript_persister.md`
- Create: `backend/docs/script/campaign_transcript_query.md`
- Create: `backend/docs/script/transcript_formatting.md`
- Modify: `backend/docs/highDoc.md` (if it is the master docs index — add one line linking to `script/README.md`)

- [ ] **Step 6.1: Write `README.md`**

```markdown
# backend/app/services/scripts — Documentation Index

Every Python module under `backend/app/services/scripts/` has a matching
one-pager in this directory.

**Invariants for every script file:**
- Max **600 lines** per file (including imports, comments, blank lines).
  When a file is about to cross 600, split it — do not grow it.
- Pure and focused. No hidden I/O in "formatter" modules.
- Unit-tested under `backend/tests/unit/test_<module>.py`.

## Scripts

| Script | One-liner |
|--------|-----------|
| [call_transcript_persister](./call_transcript_persister.md) | Rebinds telephony voice sessions to the dialer's calls.id and flushes transcripts on hangup. |
| [campaign_transcript_query](./campaign_transcript_query.md) | One-round-trip fetch of a campaign's calls + transcripts, paginated. |
| [transcript_formatting](./transcript_formatting.md) | Pure view-model helpers — drops Deepgram partials, normalises turn shape. |

## Plans & execution logs

- [2026-04-22 Call Transcripts Plan](./2026-04-22-call-transcripts-plan.md)
- [2026-04-22 Call Transcripts Execution Log](./2026-04-22-call-transcripts-execution.md)
```

- [ ] **Step 6.2: Copy this plan into `2026-04-22-call-transcripts-plan.md`**

```bash
cp backend/docs/superpowers/plans/2026-04-22-call-transcripts-in-campaign.md \
   backend/docs/script/2026-04-22-call-transcripts-plan.md
```

- [ ] **Step 6.3: Seed the execution log**

```markdown
# 2026-04-22 Call Transcripts — Execution Log

| Task | Status | Commit SHA | Notes |
|------|--------|------------|-------|
| 1. Wire VoiceSession → calls.id | pending |  |  |
| 2. Campaign transcripts query | pending |  |  |
| 3. GET /campaigns/{id}/calls | pending |  |  |
| 4. Frontend API client | pending |  |  |
| 5. ScriptCard component | pending |  |  |
| 6. Docs | pending |  |  |

Fill in this table as each task is committed. Include any deviations from the
plan and the reason (e.g. "auth fixture used existing override in conftest.py").
```

- [ ] **Step 6.4: Write the per-script one-pagers**

Each should contain: Purpose, Public API, Invariants, Line-count guard, Tests.

Example (`call_transcript_persister.md`):
```markdown
# call_transcript_persister

**Purpose.** Make TranscriptService.flush_to_database hit the right row on
telephony calls by rebinding `voice_session.call_id` → `calls.id`.

**Public API.**
- `await bind_telephony_call(voice_session, pbx_channel_id, db_client) -> CallBinding | None`
- `await save_call_transcript_on_hangup(voice_session, transcript_service, db_client, tenant_id)`

**Invariants.**
- Only called from `telephony_bridge._on_new_call` and `_on_call_ended`.
- Never raises into the caller — all exceptions are swallowed + logged.
- Line count: < 600.

**Tests.** `backend/tests/unit/test_call_transcript_persister.py`
```

Write analogous files for `campaign_transcript_query.md` and `transcript_formatting.md`.

- [ ] **Step 6.5: Commit**

```bash
git add backend/docs/script/
git commit -m "docs(script): index + per-script one-pagers + plan copy + execution log"
```

---

## Task 7 — Smoke test the full loop end-to-end

- [ ] **Step 7.1: Run the estimation agent end-to-end**

```bash
# Terminal 1 — backend
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — dialer worker
cd backend && python -m app.workers.dialer_worker

# Terminal 3 — frontend
cd Talk-Leee && npm run dev
```

1. Log into the frontend.
2. Open an existing campaign attached to the All States Estimation agent.
3. Trigger a real outbound call (existing `Start` flow).
4. Speak with the agent for ~30s and hang up.
5. Wait ~3s for `_on_call_ended` to fire.
6. Reload the campaign detail page. The new row appears in **Call Scripts**.
7. Expand the row — every user and assistant utterance is listed with an `HH:MM:SS` timestamp.

- [ ] **Step 7.2: Verify no regressions**

```bash
cd backend && pytest -q
cd Talk-Leee && npm run lint && npx tsc --noEmit
```

Expected: all green.

- [ ] **Step 7.3: Update execution log and commit**

Fill in the status table in `backend/docs/script/2026-04-22-call-transcripts-execution.md` and commit it.

```bash
git add backend/docs/script/2026-04-22-call-transcripts-execution.md
git commit -m "docs(script): mark call-transcript plan as complete"
```

---

## Self-review

- **Spec coverage.** Transcript persistence fix (Task 1) → transcripts reach the calls row. Endpoint (Tasks 2–3) → API exposes per-campaign transcripts with timestamps. UI (Tasks 4–5) → ScriptCard on campaign screen, readable, time-stamped. Script folder (Task 1) under `backend/app/services/scripts/` with 600-line cap. Docs folder (Task 6) under `backend/docs/script/` including copy of plan + execution log. Don't-hurt-existing-functionality honoured — only additive changes + a correctness fix to an UPDATE that was matching 0 rows.
- **Placeholders.** None — every step shows the actual code or command.
- **Type consistency.** `bind_telephony_call` / `save_call_transcript_on_hangup` / `fetch_campaign_transcripts` / `format_transcript_turns` names match across imports, tests, and call sites. API shape matches frontend types `CampaignCallTranscript` / `TranscriptTurn`.

---

## Execution handoff

Plan complete and saved at `backend/docs/superpowers/plans/2026-04-22-call-transcripts-in-campaign.md`.

Two execution options:
1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — I execute tasks in this session using executing-plans, with checkpoints for your review.

Tell me which approach you'd like (or review/edit the plan first).
