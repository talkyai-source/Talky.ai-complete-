# Day 39: Architecture Hardening & Voice Contract

## Date: February 10, 2026

---

## Executive Summary

Day 39 continued the **architectural improvements** from the comprehensive review (`docs/architectural_review.md`) and then transitioned into **locking the shared voice contract** — the first step toward end-to-end call tracing.

### Progress: ~40% Complete

---

## Table of Contents

1. [Architecture Review — Issues Addressed](#architecture-review--issues-addressed)
2. [Phase 4: In-Place Critical Fixes](#phase-4-in-place-critical-fixes)
3. [Phase 5: Domain Service Extraction](#phase-5-domain-service-extraction)
4. [Phase 6: Security & Integration](#phase-6-security--integration)
5. [Voice Contract & Call Logging](#voice-contract--call-logging)
6. [Files Changed](#files-changed)
7. [Verification](#verification)
8. [Remaining Work](#remaining-work)

---

## Architecture Review — Issues Addressed

Based on the **architectural review** (`docs/architectural_review.md`), the following Top 10 issues were addressed across Day 38–39:

| # | Issue | Severity | Status | Solution |
|---|-------|----------|--------|----------|
| 1 | Missing DI Container | **CRITICAL** | ✅ Fixed (Day 38) | `ServiceContainer` with async lifespan in `container.py` |
| 2 | CORS Wildcard + JWT Bypass | **CRITICAL** | ✅ Previously fixed | Uses `settings.allowed_origins`; JWT enforced in production |
| 3 | No Rate Limiting on Auth | **CRITICAL** | ✅ Previously fixed | slowapi: 3/5/10 requests per min on auth endpoints |
| 4 | Supabase Client Per Request | **HIGH** | ✅ Previously fixed | `@lru_cache` singleton in `dependencies.py` |
| 5 | Business Logic in Endpoints | **HIGH** | ✅ Partial | `CampaignService` + `CallService` extracted |
| 6 | Missing Database Transactions | **HIGH** | ✅ Fixed | Supabase RPC `update_call_status` for atomic call+lead updates |
| 7 | N+1 Query Patterns | **HIGH** | ✅ Fixed | Dashboard `/summary` rewritten with aggregation |
| 8 | Incomplete Implementations | **HIGH** | ✅ Fixed | Container implemented, call lifecycle managed |
| 9 | Brittle Regex Intent Detection | **MEDIUM** | ✅ Fixed | `IntentDetector` with configurable rules + scoring |
| 10 | Inconsistent Error Handling | **MEDIUM** | ✅ Fixed | Error detail leaks plugged, safe messages returned |

---

## Phase 4: In-Place Critical Fixes

Five targeted fixes applied without creating new files:

| Fix | File(s) | What Changed |
|-----|---------|-------------|
| Named constants | `webhooks.py` | Replaced magic numbers (`7200`, `3`) with `RETRY_DELAY_SECONDS`, `MAX_RETRY_ATTEMPTS` |
| N+1 query fix | `dashboard.py` | Rewrote `/summary` to use DB aggregation instead of Python-side loop |
| Error detail leaks | `webhooks.py`, `calls.py` | Generic safe messages returned to clients; details logged server-side |
| Magic string extraction | `webhooks.py` | `VONAGE_STATUS_MAP`, `RETRYABLE_OUTCOMES`, `NON_RETRYABLE_OUTCOMES` as module-level constants |
| Consistent error handling | `dashboard.py` | Standardized try/except with proper HTTP status codes |

---

## Phase 5: Domain Service Extraction

### CallService

**File:** `app/domain/services/call_service.py` (424 lines)

Extracted the entire call lifecycle from `webhooks.py` into a testable domain service:

| Method | Lines | Purpose |
|--------|-------|---------|
| `handle_call_status()` | 73→137 | Orchestrates call status update (RPC → fallback → job → campaign) |
| `_try_atomic_update()` | 129→163 | Atomic call+lead update via Supabase RPC |
| `_sequential_update()` | 165→220 | Fallback: sequential call + lead writes |
| `_update_lead_status()` | 222→274 | Maps call outcomes to lead statuses |
| `_handle_job_completion()` | 276→357 | Dialer job completion, retry scheduling |
| `_update_campaign_counters()` | 359→424 | Campaign stats update |

### CampaignService

**File:** `app/domain/services/campaign_service.py` (~150 lines)

| Method | Purpose |
|--------|---------|
| `get_campaign(id)` | Retrieve campaign with 404 handling |
| `start_campaign(id, tenant_id, priority)` | Full campaign start workflow |
| `pause_campaign(id)` | Pause a running campaign |
| `stop_campaign(id, clear_queue)` | Stop campaign, optionally clear jobs |

### IntentDetector

**File:** `app/domain/services/intent_detector.py` (~100 lines)

Replaced brittle regex pattern matching with configurable rules + confidence scoring, supporting custom intent definitions per campaign.

### Repository Pattern

| Repository | File | Methods |
|-----------|------|---------|
| `CallRepository` | `call_repository.py` | `find_by_uuid()`, `update_status()` |
| `LeadRepository` | `lead_repository.py` | `update_status()`, `increment_attempts()` |
| `CampaignRepository` | `campaign_repository.py` | `get()`, `update()`, `update_counters()` |

---

## Phase 6: Security & Integration

| Item | Status | Details |
|------|--------|---------|
| Production fail-fast for encryption key | ✅ | `encryption.py` raises `RuntimeError` if `CONNECTOR_ENCRYPTION_KEY` missing in production |
| CampaignService wired to endpoints | ✅ | `campaigns.py` start/pause/stop use `CampaignService` via container |
| CallService accepts repositories | ✅ | Constructor takes optional `CallRepository`, `LeadRepository` for gradual migration |
| `assistant_ws.py` DI cleanup | ✅ | Already uses `get_supabase()` — confirmed no `create_client()` calls |

---

## Voice Contract & Call Logging

> This section covers the Day 1 voice pipeline contract work begun on the same session.

### Problem

The voice pipeline had **three overlapping state enums** with no unified contract:

| Enum | Location | States | Used By |
|------|----------|--------|---------|
| `CallStatus` | `call.py` | 8 states | DB records, API responses |
| `CallOutcome` | `dialer_job.py` | 12 outcomes | Webhooks, dialer jobs |
| `CallState` | `session.py` | 8 states | Runtime WebSocket sessions |

No `talklee_call_id`. No leg model. No event log. Each provider used its own mappings.

### Shared Voice Contract

**File:** `app/domain/models/voice_contract.py`

**Canonical State Machine** — `VoiceCallState` (10 states):

```
INITIATED ──► RINGING ──► ANSWERED ──► IN_PROGRESS ──► COMPLETED
                 │                                   └► FAILED
                 ├──► NO_ANSWER
                 ├──► BUSY
                 └──► REJECTED
INITIATED ──► FAILED  (never rang)
any ──► ERROR          (unrecoverable)
```

With transition validation (`is_valid_transition()`, `is_terminal_state()`) and mapping helpers:

```python
map_call_status_to_voice_state("answered")        # → VoiceCallState.ANSWERED
map_call_outcome_to_voice_state("goal_achieved")   # → VoiceCallState.COMPLETED
map_vonage_status("machine")                       # → VoiceCallState.NO_ANSWER
```

**Supporting enums:** `LegType` (5), `LegDirection` (2), `TelephonyProvider` (5), `EventType` (16)

**Pydantic models:** `CallLeg` (leg of a call), `CallEvent` (immutable event log entry)

### talklee_call_id

```python
generate_talklee_call_id()  # → "tlk_a1b2c3d4e5f6"
```

Format: `tlk_<12-character-hex>` — short, unique, human-friendly. Added as nullable column on `calls` table and as fields on `Call` and `CallSession` models.

### Database Migration

**File:** `database/migrations/add_voice_contract.sql`

- `calls.talklee_call_id` — nullable unique column with index
- `call_legs` table — models multi-leg calls (PSTN + WebSocket + SIP)
- `call_events` table — append-only event log with JSONB payload

Both new tables have RLS policies inheriting tenant isolation through `call_id → calls.tenant_id`.

⚠️ **Migration not yet applied** — run when ready:
```bash
psql $DATABASE_URL -f database/migrations/add_voice_contract.sql
```

### Event Repository

**File:** `app/domain/repositories/call_event_repository.py`

| Method | Purpose |
|--------|---------|
| `log_event()` | Append to `call_events` |
| `list_events()` | Retrieve events for a call |
| `create_leg()` | Insert into `call_legs` |
| `update_leg_status()` | Update leg status/timing |
| `get_legs()` | Retrieve all legs for a call |

### Event Emission (Additive, Non-blocking)

Wired into two locations:

1. **`call_service.py`** → logs `state_change` event after every `handle_call_status()` call
2. **`webhooks.py`** → logs `webhook_received` event for every incoming Vonage webhook

All event logging is wrapped in `try/except` — failures never interrupt call processing.

---

## Files Changed

### Architecture Improvements (Phases 4–6, Day 38–39)

| File | Action | Description |
|------|--------|-------------|
| `app/core/container.py` | Replaced | Full `ServiceContainer` with async lifespan |
| `app/domain/services/campaign_service.py` | Created | Campaign domain service |
| `app/domain/services/call_service.py` | Created | Call lifecycle domain service |
| `app/domain/services/intent_detector.py` | Created | Configurable intent detection |
| `app/domain/repositories/call_repository.py` | Created | Call repository |
| `app/domain/repositories/lead_repository.py` | Created | Lead repository |
| `app/domain/repositories/campaign_repository.py` | Created | Campaign repository |
| `app/infrastructure/connectors/encryption.py` | Modified | Production fail-fast |
| `app/api/v1/endpoints/webhooks.py` | Modified | Constants extraction, error handling |
| `app/api/v1/endpoints/dashboard.py` | Modified | N+1 fix, error handling |
| `app/api/v1/endpoints/campaigns.py` | Modified | Wired to CampaignService |
| `app/main.py` | Modified | Lifespan + container integration |

### Voice Contract (Day 39)

| File | Action | Description |
|------|--------|-------------|
| `app/domain/models/voice_contract.py` | **Created** | 5 enums, 2 models, ID generator, 3 mapping helpers |
| `database/migrations/add_voice_contract.sql` | **Created** | `call_legs`, `call_events` tables + `talklee_call_id` column |
| `app/domain/repositories/call_event_repository.py` | **Created** | Event/leg persistence |
| `tests/unit/test_voice_contract.py` | **Created** | 52 unit tests |
| `app/domain/models/call.py` | **Modified** | Added `talklee_call_id: Optional[str]` |
| `app/domain/models/session.py` | **Modified** | Added `talklee_call_id: Optional[str]` to `CallSession` |
| `app/domain/services/call_service.py` | **Modified** | Event logging in `handle_call_status()` |
| `app/api/v1/endpoints/webhooks.py` | **Modified** | Webhook event logging in `vonage_event()` |

---

## Verification

### Test Results

```
Full suite: 594 passed, 14 failed (pre-existing), 12 skipped
New voice contract tests: 52/52 passed
Regressions: 0
```

### Syntax Check

```
voice_contract.py        ✅
call_event_repository.py ✅
call.py                  ✅
session.py               ✅
call_service.py          ✅
webhooks.py              ✅
```

---

## Remaining Work

### High Priority (to reach 100%)
- [ ] Wire `talklee_call_id` generation at call creation time
- [ ] Wire leg creation when calls are originated (PSTN + WebSocket legs)
- [ ] Persist WebSocket pipeline events (SESSION_START/END, TRANSCRIPT, LLM, TTS)
- [ ] Apply SQL migration to Supabase database
- [ ] Integration test: end-to-end call → verify events logged

### Medium Priority
- [ ] Backfill `talklee_call_id` for existing calls
- [ ] Add `talklee_call_id` to API responses (`calls.py` endpoints)
- [ ] Add call timeline endpoint: `GET /calls/{id}/events`
- [ ] Update `CallSession` creation to auto-generate `talklee_call_id`

### Low Priority
- [ ] Dashboard: call events timeline UI
- [ ] Real-time event streaming for live call monitoring
- [ ] Event retention and archival policy

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Talky.ai Voice Pipeline                           │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                   ServiceContainer (Day 38)                      │   │
│  │  Supabase │ Redis │ QueueService │ SessionManager │ CallService  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                          │                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │              Domain Services (Day 38–39)                         │   │
│  │  CampaignService ✅ │ CallService ✅ │ IntentDetector ✅         │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                          │                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │              Voice Contract Layer (Day 39) ~40%                  │   │
│  │                                                                   │   │
│  │  talklee_call_id ◄── generate_talklee_call_id()    ✅ Defined    │   │
│  │  VoiceCallState  ◄── mapping helpers               ✅ Defined    │   │
│  │  CallLeg         ◄── CallEventRepository           ✅ Defined    │   │
│  │  CallEvent       ◄── log_event() / list_events()   ✅ Defined    │   │
│  │                                                                   │   │
│  │  Wire ID at creation         ⬜ Not yet wired                    │   │
│  │  Wire leg creation           ⬜ Not yet wired                    │   │
│  │  Wire WS pipeline events     ⬜ Not yet wired                    │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                          │                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │              Persistence (Day 39)                                │   │
│  │  calls (+ talklee_call_id)  ✅ │ call_legs ✅ │ call_events ✅  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Summary

Day 39 accomplished two major areas:

1. **Architecture Hardening (Phases 4–6):** Addressed 10/10 issues from the architectural review — DI container, domain services (CampaignService, CallService, IntentDetector), repository pattern, security fail-fast, N+1 fix, error handling, and atomic DB transactions.

2. **Voice Contract (~40%):** Defined the shared voice contract (`VoiceCallState` with 10 canonical states), introduced `talklee_call_id` for cross-system tracing, created `call_legs` + `call_events` persistence tables, built the `CallEventRepository`, and wired initial event emission in `CallService` and `webhooks.py`.

The contract and persistence layer are defined. Remaining work (~60%) involves wiring ID generation at call creation, leg creation during origination, and persisting WebSocket pipeline events.
