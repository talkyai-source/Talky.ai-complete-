# AI Call Summary — Design & Plan

**Date:** 2026-06-07
**Status:** Approved (design) → ready to implement
**Decisions (from the user):** structured JSON sections · headline + expand in the list · generate at call-end + lazy backfill.

## Goal
Every row in the call-history page shows an AI-generated summary so the user never has to read the full transcript — and the summary **never skips a point** (outcome, objections, commitments, action items, etc. are always captured).

## What already exists (build on, don't duplicate)
- `calls.summary` column exists but is **never written**; `CallListItem`/`CallDetail` already expose a `summary` field.
- Transcripts persist at call-end: `save_call_transcript_on_hangup()` (`backend/app/services/scripts/call_transcript_persister.py`) writes `calls.transcript` + `transcript_json` + the `transcripts` table. **This is the generation hook point.**
- `transcript_service.get_transcript_json(call_id)` returns the conversation turns (summary input).
- Endpoints: `GET /calls` (list), `GET /calls/{id}` (detail, returns transcript+summary), `GET /calls/{id}/transcript`.
- Frontend: `Talk-Leee/src/app/calls/page.tsx` → `CallRow` already expands to the transcript (`useCallTranscript`).

## Architecture

### 1. Storage — structured JSON
- **Migration:** add `calls.summary_json JSONB` (new) for the full structured summary. Keep `calls.summary` (text) for the **1-line headline** (already returned by the list endpoint → instant per-row headline, no extra query).
- Structured shape (every field always present so nothing is skipped):
  ```json
  {
    "headline": "Qualified — wants a demo next week",
    "outcome": "qualified | disqualified | callback | no_interest | voicemail | ...  + one-line why",
    "what_happened": "2-4 sentence chronological gist",
    "key_points": ["caller's needs/questions/context, each as a bullet"],
    "objections": [{"objection": "...", "handled": "..."}],
    "commitments": ["what either side agreed to"],
    "action_items": [{"item": "...", "owner": "agent|caller|user"}],
    "sentiment": "positive | neutral | negative + note",
    "next_step": "the single concrete next action",
    "notable_quotes": ["short verbatim lines that matter"]
  }
  ```

### 2. Generation — Groq `llama-3.3-70b-versatile`, structured + comprehensive
- A dedicated summarizer (NOT the chat agent): one completion, transcript turns in, the JSON above out (request JSON output; validate + repair).
- **"Never skip a point" is enforced by the prompt + schema:** the prompt enumerates every dimension and instructs "if a dimension is absent, return an empty list / 'none' — never omit it." A fixed schema is what prevents free-form summarization from dropping points.
- Not latency-critical (runs at call-end, async). Fail-soft: a summary error never blocks call teardown.

### 3. When — at call-end + lazy backfill
- **At call-end:** after `save_call_transcript_on_hangup`, fire-and-forget the summarizer → store `summary_json` + `summary` (headline). Only when a transcript exists (skip no-answer/failed).
- **Lazy backfill (existing calls):** `GET /calls/{id}/summary` → if `summary_json` is null but a transcript exists, generate + store + return. So old calls fill in on first view and persist.

### 4. Display — headline + expand
- List row: show `summary` (headline) always (scannable).
- Expand: fetch `GET /calls/{id}/summary` → render a structured **summary card** (outcome chip, what-happened, key points, objections, action items, sentiment, next step). The raw-transcript toggle stays as a secondary option.

## Files

**Backend — create**
- `backend/database/migrations/20260607_add_calls_summary_json.sql` — `ALTER TABLE calls ADD COLUMN IF NOT EXISTS summary_json JSONB;`
- `backend/app/domain/services/call_summary/__init__.py`
- `backend/app/domain/services/call_summary/summarizer.py` — prompt + Groq call + JSON validate/repair → dict. `async def summarize_transcript(turns) -> dict`.
- `backend/app/domain/services/call_summary/store.py` — `async def generate_and_store(pool, tenant_id, call_id) -> dict` (reads transcript, summarizes, writes summary_json + headline; idempotent).

**Backend — modify**
- `backend/app/services/scripts/call_transcript_persister.py` — after the transcript UPDATE, fire-and-forget `generate_and_store(...)` (fail-soft).
- `backend/app/api/v1/endpoints/calls.py` — add `GET /{call_id}/summary` (generate-if-missing); add `summary_json` to `CallDetail` + its SELECT; ensure the list SELECT returns `summary` (headline).

**Frontend — create/modify**
- `Talk-Leee/src/lib/calls-api.ts` (or existing calls hooks) — `getCallSummary(callId)`.
- `Talk-Leee/src/components/calls/CallSummaryCard.tsx` — renders the structured summary.
- `Talk-Leee/src/app/calls/page.tsx` — `CallRow`: show headline; expand → `CallSummaryCard` (+ keep transcript toggle).

## Build slices (each shippable)
1. Migration + `summarizer.py` + `store.py` + unit tests (schema completeness, empty-dimension handling, idempotency).
2. Call-end hook (auto-generate + store), fail-soft.
3. `GET /calls/{id}/summary` (lazy backfill) + `CallDetail.summary_json` + list returns headline.
4. Frontend: headline per row + expandable `CallSummaryCard`.
5. (Optional) one-shot backfill of the most recent N calls.

## Safety / edge cases
- Tenant-scoped (RLS via `acquire_with_tenant` / `.table().eq(tenant_id)`).
- No transcript → no summary (row shows the outcome).
- Very short call → short summary (empty lists for absent dimensions, never omitted).
- JSON parse failure → one repair retry, then store a minimal `{headline, outcome}` fallback so the row still shows something (logged).
- Generation is idempotent + cached: never re-summarize a call that already has `summary_json`.
