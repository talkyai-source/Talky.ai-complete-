# 2026-04-22 Call Transcripts — Execution Log

Detailed record of *what* was built, *how* it works, *why* each decision was
taken, and *why we are confident there are no regressions*. Read top-to-bottom
to understand the change; skip to §3 (“Why there are no bugs”) if you only
want the correctness argument.

Related documents:
- [Plan](./2026-04-22-call-transcripts-plan.md)
- [README / conventions](./README.md)

---

## 1. What was built

A "Script Card" on the campaign detail page that lists every call the
campaign placed, each expandable into a timestamped transcript of user speech
and agent replies. Implemented across six layers:

| Layer | File | Purpose |
|-------|------|---------|
| Scripts package | `backend/app/services/scripts/__init__.py` | Re-export public helpers |
| Persister | `backend/app/services/scripts/call_transcript_persister.py` | Bind voice session to dialer's `calls` row; persist transcript on hangup |
| Formatter | `backend/app/services/scripts/transcript_formatting.py` | Drop partial STT frames; normalise turn shape |
| Query | `backend/app/services/scripts/campaign_transcript_query.py` | Paginated `calls + transcripts` read for one campaign |
| Bridge wiring | `backend/app/api/v1/endpoints/telephony_bridge.py` | Hook the persister into `_on_new_call` and `_on_call_ended` |
| API endpoint | `backend/app/api/v1/endpoints/campaigns.py` | `GET /campaigns/{id}/calls` with tenant enforcement |
| Frontend client | `Talk-Leee/src/lib/extended-api.ts` | `getCampaignCallsWithTranscripts` + types |
| Frontend view | `Talk-Leee/src/components/campaigns/script-card.tsx` | Expandable transcript rows |
| Mount | `Talk-Leee/src/app/campaigns/[id]/page.tsx` | Place `<ScriptCard />` on the detail page |

Unit tests:
- `backend/tests/unit/test_call_transcript_persister.py` — 7 tests
- `backend/tests/unit/test_campaign_transcript_query.py` — 9 tests

All 16 tests pass in 0.12 s.

---

## 2. How it works — data flow

### 2.1 Outbound dial lifecycle (existing, unchanged)

1. `dialer_worker.py` inserts a row into `calls` with a *new* UUID
   (`internal_call_id`) and stores the PBX channel UUID as
   `external_call_uuid`.
2. PBX adapter answers, `_on_new_call(call_id=<pbx-channel-uuid>)` fires in
   `telephony_bridge.py`.
3. `voice_orchestrator.create_voice_session(config)` mints *another* UUID
   (`voice_session.call_id`, distinct from both of the above) and wires it
   into:
   - Deepgram Flux `_pre_connections[call_id] = ws`
   - ElevenLabs/Google TTS `connect_for_call(call_id)` pools
   - Media gateway session registry
   - `TranscriptService` class-level buffer

Before this PR, `TranscriptService.flush_to_database` attempted
`UPDATE calls WHERE id = voice_session.call_id`, which matched **zero rows**
— the dialer's row is keyed on a different UUID entirely. Transcripts were
silently discarded.

### 2.2 New binding — `bind_telephony_call`

Hooked directly after the session is registered
(`telephony_bridge.py:_on_new_call`, right after
`_telephony_sessions[call_id] = voice_session`):

- Query `calls` by `external_call_uuid = <pbx channel id>` using the existing
  supabase-style `db_client.table(...).select(...).execute()` pattern
  (identical to `_save_call_recording`, so there is no new auth surface).
- Stash three fields on the voice session as **private attributes**:
  `voice_session._dialer_call_id`, `_dialer_tenant_id`, `_dialer_campaign_id`.
- **Never** touch `voice_session.call_id` or
  `voice_session.call_session.call_id`.

### 2.3 Final persist — `save_call_transcript_on_hangup`

Hooked inside `_on_call_ended`, *before* `_save_call_recording` so a
transcript write failure cannot silently block recording persistence.

1. Read the in-memory buffer keyed on the session's original `call_id`:
   `transcript_service.get_transcript_json / get_transcript_text / get_metrics`.
2. If no dialer binding exists (non-campaign test call) or buffer is empty,
   just clear the buffer and return. No DB writes.
3. Otherwise run two SQL statements against the asyncpg pool:
   - `UPDATE calls SET transcript = $1, transcript_json = $2::jsonb, updated_at = NOW() WHERE id = $3`
   - `INSERT INTO transcripts (...) VALUES (...) ON CONFLICT DO NOTHING`
4. **Always** clear the buffer in a `finally` block.

The helper swallows every exception — DB outages, JSON encoding errors,
stale buffers — and only logs. A single broken call cannot tear down the
telephony pipeline.

### 2.4 Read path — `fetch_campaign_transcripts`

One round-trip against asyncpg:

```sql
SELECT c.id, c.phone_number, c.created_at, c.duration_seconds, c.outcome,
       c.transcript_json
FROM calls c
WHERE c.tenant_id = $1 AND c.campaign_id = $2
ORDER BY c.created_at DESC
LIMIT $3 OFFSET $4
```

Plus one `COUNT(*)` for pagination totals. Each row's `transcript_json` is
coerced from JSON string or native list (asyncpg behaviour depends on JSON
codec config), then passed through `format_transcript_turns` which:

- Keeps only `role in {"user", "assistant"}`
- Drops turns where `include_in_plaintext is False` (Deepgram interim /
  eager / update frames)
- Drops turns whose `content` is empty after `.strip()`
- Returns `{role, content, timestamp}` — nothing else reaches the UI

### 2.5 API endpoint

`GET /campaigns/{id}/calls?page=1&page_size=20`:

- Requires authenticated user and non-null `tenant_id`.
- Verifies the campaign belongs to the caller's tenant via a `SELECT id
  FROM campaigns WHERE id = ? AND tenant_id = ?` style supabase query.
- Returns 404 if the campaign is not owned by the caller; 400 on invalid
  UUIDs; 500 on unexpected errors (logged).
- Response shape: `{items, page, page_size, total}`.

### 2.6 Frontend

`ScriptCard` uses framer-motion (already in the codebase) for the expand
animation. Each call row shows `phone_number`, `started_at`, duration,
outcome badge, and turn count. Expanded rows render turns grouped by
role with monospaced timestamps. Pagination is simple prev/next; it only
shows up when `total > page_size`.

---

## 3. Why there are no bugs — invariants and evidence

### 3.1 STT/TTS/media-gateway connection keys are preserved

**Invariant:** `voice_session.call_id` and `voice_session.call_session.call_id`
MUST NOT change after session creation.

**Why it matters:** Deepgram Flux keeps `self._pre_connections[call_id] = ws`
(see `deepgram_flux.py:132`). TTS providers key `connect_for_call(call_id)`
pools the same way. Media gateway registers sessions by this id. If we
rewrite `voice_session.call_id` to the dialer's `calls.id`, every one of
those lookups breaks mid-call — audio stops flowing, TTS hangs.

**How this PR honours it:** The persister writes only to *new* private
attributes (`_dialer_call_id`, `_dialer_tenant_id`, `_dialer_campaign_id`).
The existing fields are read, never written. Verified by test
`test_bind_is_non_destructive` — it asserts `vs.call_id == original_call_id`
and `vs.call_session.call_id == original_cs_call_id` after the bind call
completes successfully.

### 3.2 Every failure path is safe

**Invariant:** A transcript-persist failure must never tear down a
telephony call.

**How this PR honours it:** Both public functions are wrapped in
broad-except `try/except` blocks. Every early-out path still calls
`_safe_clear(transcript_service, session_call_id)` to stop in-memory
buffers leaking. `_safe_clear` itself is wrapped in `try/except`. The
final persist sits in a `try/…/finally: _safe_clear(...)` block so even a
DB connection failure in the middle of the transaction leaves the buffer
cleared. Verified by:

- `test_save_clears_buffer_even_when_db_fails` — forces `conn.execute` to
  raise, asserts `svc.clear_buffer` was still called exactly once.
- `test_bind_swallows_lookup_exception` — forces `db_client.table(...)` to
  raise `RuntimeError`, asserts the function returns `None` without
  propagating.

### 3.3 No writes for non-campaign or empty calls

**Invariant:** A test call (no dialer row) or a call that never produced
an utterance must not INSERT rows into `transcripts` or `calls`.

**How this PR honours it:** `save_call_transcript_on_hangup` early-returns
when `_dialer_call_id` is absent or `turns_json` is empty, *before*
acquiring a DB connection. Verified by:

- `test_save_skips_when_no_dialer_binding` — asserts `pool.acquire` is
  never called when the binding is missing.
- `test_save_skips_when_buffer_empty` — same assertion when the buffer is
  empty despite a binding being present.
- `test_bind_returns_none_when_no_dialer_row` — when the `calls` row
  lookup returns empty data, no stash attributes are set.

### 3.4 Input validation at the boundary

**Invariant:** UUID-shaped inputs must be validated at the API boundary,
not passed raw into SQL.

**How this PR honours it:**

- `fetch_campaign_transcripts` parses `campaign_id` and `tenant_id` through
  `UUID(str(...))` and raises `ValueError` on failure. The FastAPI endpoint
  maps that to HTTP 400.
- `save_call_transcript_on_hangup` validates `_dialer_call_id` the same
  way; a bad value skips the write and still clears the buffer.
- Tests `test_fetch_raises_on_invalid_campaign_id` and
  `test_coerce_turns_returns_empty_on_garbage` pin these paths.

### 3.5 Partial STT frames never reach the UI

**Invariant:** Users only see final, finalised turns — never Deepgram
interim / eager transcripts.

**How this PR honours it:** `format_transcript_turns` drops any turn with
`include_in_plaintext` explicitly `False`. The DB stores the full
`TranscriptTurn.to_dict()` shape (which includes the flag), so the filter
is authoritative. Default-True for older records that predate the flag.
Verified by `test_format_turns_drops_partials_and_empties` which mixes
partial + final + empty + non-user/assistant rows and asserts only two
survive.

### 3.6 Tenant isolation at the endpoint

**Invariant:** User A must never read transcripts for a campaign owned by
user B.

**How this PR honours it:** The endpoint performs two checks in series:

1. `campaigns` row lookup is filtered by both `id = $1` and
   `tenant_id = <current_user.tenant_id>`. If that returns empty we emit 404
   — deliberately not 403, to avoid leaking campaign existence.
2. The subsequent `fetch_campaign_transcripts` query also filters
   `WHERE c.tenant_id = $1 AND c.campaign_id = $2`, so even if step 1 were
   bypassed (it isn't) the data read would still be tenant-scoped.

Both tenant and campaign ids are UUID-validated before touching SQL.

### 3.7 No regressions to recording save path

**Invariant:** `_save_call_recording` must continue to run exactly as
before this PR.

**How this PR honours it:** The new call is inserted *before*
`_save_call_recording` in `_on_call_ended`, never after. Both blocks live
under the same `if voice_session:` branch and each is wrapped in its own
`try/except` so a failure in one cannot propagate into the other.
`_save_call_recording` itself is untouched.

### 3.8 No N+1 query

**Invariant:** Listing `page_size` calls must execute O(1) queries, not
O(page_size).

**How this PR honours it:** `fetch_campaign_transcripts` runs exactly two
queries regardless of page size: one `SELECT` with `LIMIT/OFFSET` and one
`COUNT(*)`. Turn formatting is done in Python from the already-fetched
`transcript_json` JSONB column — no secondary lookup.

---

## 4. Deviations from the original plan

| Item | Plan said | Execution did | Reason |
|------|-----------|---------------|--------|
| Task 1 | Rewrite `voice_session.call_id = internal_call_id` | Stash `_dialer_call_id` private attribute; leave `call_id` untouched | STT/TTS/media-gateway pools are keyed on `call_id`. Rewriting would break in-flight audio. Caught during plan self-review before implementation. |
| Task 1 test | Assert `vs.call_id == "dialer-calls-id"` | Assert `vs.call_id == original_call_id`; assert `vs._dialer_call_id == "dialer-id"` | Same reason — aligned with the non-destructive approach. |
| Task 1 final persist | `TranscriptService.flush_to_database(call_id=internal_call_id)` | Direct asyncpg `UPDATE calls` + `INSERT transcripts` keyed on `_dialer_call_id`, reading the buffer keyed on session `call_id` | Avoids modifying `TranscriptService`'s public signature (used in several places); keeps the scope contained. |

---

## 5. Task checklist

| Task | Status | Commit | Notes |
|------|--------|--------|-------|
| 1. Persister + bridge wiring | complete | _pending_ | Non-destructive binding approach |
| 2. Query + formatter | complete | _pending_ | 9 unit tests, all passing |
| 3. GET /campaigns/{id}/calls | complete | _pending_ | Tenant-scoped at endpoint + query |
| 4. Frontend API client | complete | _pending_ | `getCampaignCallsWithTranscripts` + types |
| 5. ScriptCard component + mount | complete | _pending_ | Expandable rows, framer-motion, pagination |
| 6. Per-script docs + execution log | complete | _pending_ | This document + three one-pagers |
| 7. Run unit tests | complete | _pending_ | 16/16 pass in 0.12s |

---

## 6. Verification

Commands run in this workspace:

- `cd backend && pytest tests/unit/test_call_transcript_persister.py tests/unit/test_campaign_transcript_query.py -v`
  → **16 passed in 0.12s**.
- `python3 -c "import ast; ast.parse(open('app/api/v1/endpoints/telephony_bridge.py').read())"` → OK.
- `python3 -c "import ast; ast.parse(open('app/api/v1/endpoints/campaigns.py').read())"` → OK.
- `cd Talk-Leee && node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit` (filtered
  for changed files) → no errors in `script-card.tsx`, `extended-api.ts`, or
  `campaigns/[id]/page.tsx`. Pre-existing errors in `*.test.ts[x]` files are
  unrelated.

Manual smoke test (to run after deploying):

1. Create or pick a campaign tied to the All States Estimation agent.
2. Add a contact and start the campaign.
3. Answer the call, speak for a few seconds, let the agent reply, hang up.
4. Refresh the campaign detail page.
5. Script Card should show one new row. Expand it: turns alternate between
   Caller / Agent with `HH:MM:SS` timestamps. No partial transcripts leak.
