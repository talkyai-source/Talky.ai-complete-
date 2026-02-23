# Day 46: Flux Stabilization, Postgres Completion, and Auth Hardening

> **Date:** February 19, 2026  
> **Focus:** Real-time voice stability, Deepgram Flux/TTS quality, Postgres migration completion, dashboard auth enforcement, logout re-auth guarantee, and middleware hardening for invalid JWT handling  
> **Status:** ✅ Major milestones completed; ⚠️ one backend middleware test run interrupted by user and queued for next execution

---

## Summary

Today was an end-to-end stabilization day across voice, database, and authentication layers.

The system behavior shifted from "mostly working with edge-case failures" to "predictable and production-safe on critical paths".

Main outcomes:

1. Ask AI voice flow now remains conversational and stable through full turns.
2. TTS distortion/fast-forward artifacts were isolated and mitigated via chunking and buffer pipeline fixes.
3. Product-info conversations now remain in the product-assistant lane (no unintended state-machine behavior bleed).
4. Postgres migration was validated operationally (schema/data visibility, runtime connectivity, and adapter behavior).
5. Frontend dashboard access is now strictly gated behind real authentication.
6. Dev-token bypass paths were removed.
7. Explicit logout now forces a mandatory sign-in before returning to dashboard routes.
8. Invalid JWT middleware behavior that surfaced as 500 was patched toward proper 401/public-health-safe behavior.

---

## Context and Starting Point

At start of day, user reported a sequence of production-facing issues:

1. Ask AI repeated short greeting-like outputs and quickly returned to listening state.
2. During package explanations, speech would become silent or unstable.
3. TTS sometimes sounded distorted, with inconsistent playback rate (normal vs ~2x behavior).
4. Agent occasionally did not introduce itself first.
5. Migration from Supabase to Postgres needed complete confidence and no stale assumptions.
6. PgAdmin visibility/registration confusion existed after migration.
7. Dashboard access required stricter auth behavior.
8. Logout needed to be mandatory credential re-entry path.
9. Backend started showing invalid JWT warnings and a `/api/v1/health` request surfaced as 500 due middleware exception flow.

---

## Part 1: Ask AI Voice Flow Stabilization

### 1.1 Observed Runtime Symptoms

From runtime logs and user validation:

1. Turn flow started correctly and Flux connected.
2. Greeting played.
3. User input transcribed correctly on some turns.
4. Package-detail response generation happened, but downstream TTS handling could fail mid-turn.
5. Resulting user experience: occasional abrupt return to listening, missing or broken audio playback.

### 1.2 Key Signal Found in Logs

A critical exception was observed in TTS chunking path:

1. `re.error: look-behind requires fixed-width pattern`
2. Origin: sentence/chunk split logic in `deepgram_tts.py`
3. Trigger: certain punctuation/markdown-heavy LLM output when generating package details

Impact:

1. LLM produced valid response text.
2. TTS synthesis pipeline failed before full audio dispatch.
3. Turn ended with partial/no spoken response.

### 1.3 Result

After fixes on the voice chain, user confirmed listening-state issue resolved and conversation became usable again.

---

## Part 2: Deepgram Flux Alignment and Event Behavior

### 2.1 Applied Documentation Alignment

Deepgram Flux docs were used as operational baseline for:

1. End-of-turn event model (`StartOfTurn`, `Update`, `EndOfTurn`).
2. Threshold tuning (`eot_threshold`, `eot_timeout_ms`, optional eager mode).
3. End-of-turn-only simplicity for reduced complexity.

### 2.2 Active Runtime Pattern

Current runtime pattern observed:

1. End-of-turn-only processing for response generation.
2. Barge-in detection on `StartOfTurn`.
3. STT muting while TTS output is playing.
4. Unmute after TTS completion.

### 2.3 Why This Helped

1. Reduced speculative complexity from eager-turn cancellation logic.
2. Better determinism in when LLM is invoked.
3. Cleaner interruption handling between user speech and bot speech.

---

## Part 3: TTS Distortion / Fast-Forward Investigation

### 3.1 User-Reported Symptoms

1. Distortion noise during playback.
2. Perceived speed variability (normal and ~2x sections).
3. More likely during longer package explanations.

### 3.2 Contributing Factors Reviewed

1. Chunk sizing and segmentation behavior.
2. Text sanitization prior to synthesis.
3. Browser media gateway conversion path.
4. Queue/buffer continuity across chunks.
5. STT/TTS mute overlap timing and echo suppression windows.

### 3.3 Additional Runtime Error Found

Flux websocket received unsupported control message:

1. `UNPARSABLE_CLIENT_MESSAGE`
2. Unknown variant `KeepAlive` for Flux v2 message parser in this path

Operational consequence:

1. STT stream terminated unexpectedly during longer response flow.
2. This can create apparent audio/interaction instability.

### 3.4 Result

User confirmed a later build state as "perfect now" for direct listen transitions and stable operation, indicating major audible issues were mitigated.

---

## Part 4: Introduction/Greeting Behavior

### 4.1 Requirement

User requested that when session begins, assistant should introduce itself first rather than waiting silently for user greeting.

### 4.2 Outcome

Greeting-first behavior was restored/confirmed in flow:

1. Session starts.
2. Assistant plays intro greeting.
3. STT unmuted after greeting.
4. User can then continue natural conversation.

---

## Part 5: Postgres Migration Completion and Confidence Checks

### 5.1 Goal

Ensure system is fully oriented around Postgres runtime and does not rely on Supabase runtime assumptions.

### 5.2 Backend Migration Work Completed

Key migration-oriented work already implemented and verified in this cycle:

1. Provider/storage config moved to postgres/local expectations.
2. Container wiring updated for adapter client injection.
3. Tenant middleware env handling aligned to local JWT variables.
4. Adapter behavior improved for relation filtering/sorting compatibility.
5. Supabase-shaped query surfaces kept as compatibility shim while backend storage is PostgreSQL.

### 5.3 Tests Added for Migration Confidence

1. Unit tests for Postgres adapter behaviors.
2. Integration smoke for Postgres connectivity and schema assumptions.

### 5.4 Validation Results

Previously validated in terminal:

1. DB endpoint resolved at `127.0.0.1:5432`.
2. Active DB/user aligned (`talkyai` database and user).
3. Public table count present and non-empty schema confirmed.
4. Seed/business data (e.g., plans rows) present.

---

## Part 6: PgAdmin Operational Support

### 6.1 Problem

User could connect PgAdmin shell but did not initially see expected data objects.

### 6.2 Guidance Flow Provided

1. Register local server in pgAdmin.
2. Expand `Databases -> talkyai -> Schemas -> public -> Tables`.
3. Distinguish `postgres` default DB vs application DB.
4. Verify actual runtime host/port/user used by backend.

### 6.3 End State

User confirmed server registration and object visibility in pgAdmin.

---

## Part 7: Frontend Dashboard Auth Enforcement (Critical)

### 7.1 Requirement

When user clicks Dashboard, sign-in/sign-up flow must be mandatory.

### 7.2 Root Cause

Multiple dev bypass paths still existed in frontend:

1. Middleware auto-issued `dev-token` in non-production.
2. Home navbar seeded a dev token in browser storage.
3. API layer returned dev stub auth responses in development mode.
4. Auth context accepted fallback pseudo-user on failed `/auth/me`.

### 7.3 Fixes Applied

#### A) Middleware hardening

File: `Talk-Leee/middleware.ts`

1. Removed automatic `dev-token` issuance.
2. Enforced redirect of protected routes to `/auth/login?next=...`.
3. Added legacy `dev-token` rejection.

#### B) Navbar hardening

File: `Talk-Leee/src/components/home/navbar.tsx`

1. Removed browser-side dev token seeding logic.

#### C) API hardening

File: `Talk-Leee/src/lib/api.ts`

1. Removed development auth stubs for `login`, `register`, and `getMe`.
2. Ensured auth operations always use backend endpoints.

#### D) Auth context hardening

File: `Talk-Leee/src/lib/auth-context.tsx`

1. Removed fallback "unknown user" acceptance path.
2. Invalid token now clears token and sets `user = null`.
3. Refresh flow clears invalid tokens consistently.

#### E) Token cleanup

File: `Talk-Leee/src/lib/auth-token.ts`

1. Added legacy `dev-token` purge from localStorage/cookie reads.

#### F) Redirect continuity

Files:

1. `Talk-Leee/src/app/auth/login/page.tsx`
2. `Talk-Leee/src/app/auth/register/page.tsx`

Behavior:

1. Honors `next` query parameter safely.
2. Redirects authenticated user to intended protected route post-login/register.

### 7.4 Validation

1. Typecheck passed (`npm run typecheck`).
2. Auth hardening tests passed.

---

## Part 8: Mandatory Credential Re-entry After Logout

### 8.1 Requirement

When user intentionally logs out, system must ask credentials again before any dashboard access.

### 8.2 Root Cause

Sidebar logout button previously only redirected to home (`/`) and did not execute full auth logout path.

### 8.3 Fixes Applied

#### A) Sidebar logout wiring

File: `Talk-Leee/src/components/layout/sidebar.tsx`

1. Logout now calls `useAuth().logout()`.
2. Redirects with `router.replace('/auth/login?logged_out=1')`.
3. Prevents duplicate clicks while logging out.
4. Keeps fallback redirect path for robustness.

#### B) Auth context resilience

File: `Talk-Leee/src/lib/auth-context.tsx`

1. `logout()` now always clears local session state in `finally` block.
2. Even if backend logout request fails, local token is cleared.

#### C) Regression tests

File: `Talk-Leee/src/lib/auth-hardening.test.ts`

1. Added assertions that sidebar uses real logout and sign-in redirect path.

### 8.4 Validation

1. Typecheck passed.
2. Auth hardening test suite passed.
3. Behavior confirmed: dashboard requires fresh sign-in after explicit logout.

---

## Part 9: Backend JWT Middleware Incident (Current Final Issue)

### 9.1 Symptom Reported

User reported backend log errors:

1. `Invalid JWT token: Signature verification failed`
2. `HTTPException: 401 Invalid authentication token`
3. Request to `/api/v1/health` resulting in `500 Internal Server Error`

### 9.2 Root Cause Analysis

Two issues were identified:

1. Middleware raised `HTTPException` directly from `BaseHTTPMiddleware.dispatch`, which can surface as unhandled ASGI exception groups and appear as 500 in this setup.
2. Public path allowlist omitted API-scoped health routes (`/api/v1/health`), so stale/invalid auth headers could trigger auth handling on a route expected to stay public.

### 9.3 Patches Applied

#### A) Middleware response handling hardening

File: `backend/app/core/tenant_middleware.py`

1. Replaced `raise HTTPException(...)` branches with explicit `JSONResponse(status_code=401, ...)`.
2. Preserved `WWW-Authenticate: Bearer` headers.

#### B) Public-path allowlist correction

File: `backend/app/core/tenant_middleware.py`

Added public paths:

1. `/api/v1/health`
2. `/api/v1/health/detailed`

#### C) API health route compatibility

File: `backend/app/api/v1/endpoints/health.py`

1. Added `GET /api/v1/health` basic health endpoint.
2. Kept existing `GET /api/v1/health/detailed` endpoint.

### 9.4 Test Coverage Added

File: `backend/tests/unit/test_tenant_middleware.py`

New tests include:

1. Invalid token does not break `/api/v1/health`.
2. Invalid token on protected path returns 401 (not 500).

### 9.5 Execution Status

1. Patch implementation completed.
2. Test execution was started.
3. User intentionally interrupted test run to switch tasks.
4. Re-run remains pending next cycle.

---

## Part 10: Files Touched (High-Impact)

### Backend

1. `backend/app/core/tenant_middleware.py`
2. `backend/app/api/v1/endpoints/health.py`
3. `backend/tests/unit/test_tenant_middleware.py`

### Frontend (Talk-Leee)

1. `Talk-Leee/middleware.ts`
2. `Talk-Leee/src/components/home/navbar.tsx`
3. `Talk-Leee/src/lib/api.ts`
4. `Talk-Leee/src/lib/auth-context.tsx`
5. `Talk-Leee/src/lib/auth-token.ts`
6. `Talk-Leee/src/app/auth/login/page.tsx`
7. `Talk-Leee/src/app/auth/register/page.tsx`
8. `Talk-Leee/src/components/layout/sidebar.tsx`
9. `Talk-Leee/src/lib/auth-hardening.test.ts`

### Documentation

1. `backend/docs/day_forty_six_postgres_auth_stability.md` (this report)

---

## Part 11: Verification and Diagnostics Performed

### Voice/Runtime

1. Session lifecycle logs analyzed for Ask AI flow.
2. Flux turn events observed (`Update`, `StartOfTurn`, `EndOfTurn`).
3. TTS metadata and chunk behavior inspected.
4. Barge-in mute/unmute transitions validated from logs.

### Database/Postgres

1. Active DB endpoint/user/database verified via terminal.
2. Table presence and baseline row checks executed.
3. PgAdmin registration and object-tree validation guided and confirmed.

### Frontend Auth

1. Static code scan for dev-token bypasses.
2. TypeScript typecheck execution.
3. Focused auth-hardening test execution.

### Backend Middleware

1. Middleware code path review for JWT exception handling.
2. Public route path audit.
3. API v1 health route compatibility check.
4. Middleware regression tests authored (execution pending full rerun after interruption).

---

## Part 12: Key Technical Decisions and Rationale

### Decision 1: Remove all dev auth bypasses from runtime paths

Why:

1. Dev convenience logic leaked into behavior that contradicted product auth requirements.
2. Dashboard gate could be bypassed with stale local token state.

Tradeoff:

1. Slightly less convenience in local demos.
2. Strongly improved parity with production auth behavior.

### Decision 2: Treat explicit logout as hard boundary

Why:

1. User expectation is explicit and security-critical.
2. UI-level redirect-only logout is insufficient.

Tradeoff:

1. Additional redirect handling complexity.
2. Better security semantics and consistent UX.

### Decision 3: Return JSON 401 directly in middleware

Why:

1. Prevent ASGI exception-group escalations from middleware raise path.
2. Preserve API behavior clarity and client observability.

Tradeoff:

1. Slightly more verbose middleware code.
2. Avoids 500 masking on auth failures.

### Decision 4: Keep `/api/v1/health` public and explicit

Why:

1. Health probes should remain robust even if clients send stale auth headers.
2. Frontend/backend route contract expected this endpoint.

Tradeoff:

1. Public endpoint surface expands by one route.
2. Better operability and monitoring resilience.

---

## Part 13: Operational Notes for Team

1. If stale browser token exists from old builds, login path now clears and normalizes state.
2. Health checks should target:
   1. `/health` (root)
   2. `/api/v1/health` (API-scoped)
3. For any future middleware auth logic, prefer response returns over raising framework exceptions from middleware dispatch.
4. Keep JWT secret/algo values synchronized across:
   1. token creation (`auth.py`)
   2. dependency validation (`dependencies.py`)
   3. tenant middleware validation (`tenant_middleware.py`)
5. Current architecture still uses Supabase-shaped adapter interfaces in places, but runtime storage/auth is now Postgres + local JWT.

---

## Part 14: Risks / Follow-Up Items

### Open Follow-Up A: Complete backend middleware test run

Status:

1. Test file added.
2. Execution interrupted by user request reprioritization.

Next action:

1. Run `./venv/bin/pytest -q tests/unit/test_tenant_middleware.py`
2. Confirm green and include in CI target list.

### Open Follow-Up B: JWT config normalization

Observation:

1. Some modules read `JWT_ALGORITHM` from env.
2. Other auth modules currently hardcode `HS256`.

Next action:

1. Standardize to one source of truth from env-config module.
2. Add unit test proving token minted by auth endpoint validates in middleware/dependencies with same settings.

### Open Follow-Up C: Legacy Supabase naming cleanup

Observation:

1. Compatibility shim still uses `get_supabase` naming in dependency layer.

Next action:

1. Rename internal symbols to postgres-neutral naming.
2. Keep compatibility wrappers only where strictly needed.

### Open Follow-Up D: Flux keepalive policy consistency

Observation:

1. One runtime trace showed unsupported KeepAlive message for Flux stream path.

Next action:

1. Verify current provider sends only supported control messages (`CloseStream`/`Configure`) for this endpoint.
2. Add integration assertion to prevent reintroduction.

---

## Part 15: Acceptance Criteria Snapshot

### Voice

1. Ask AI answers product questions naturally: ✅
2. Intro greeting before user speech: ✅
3. No recurring fast-forward distortion in current tested path: ✅ (user-confirmed)
4. Stable listen/speak transitions: ✅

### Database

1. Postgres runtime active and reachable: ✅
2. App schema visible in pgAdmin: ✅
3. Migration confidence tests added: ✅

### Auth

1. Dashboard requires sign-in: ✅
2. Dev token bypass removed: ✅
3. Logout requires fresh sign-in: ✅

### Middleware/Health

1. Invalid JWT no longer intended to surface as 500 from middleware path: ✅ (patched)
2. API-scoped health endpoint exists and is public: ✅ (patched)
3. Final pytest verification for new middleware tests: ⏳ pending rerun

---

## Part 16: Commands Used (Representative)

### Frontend

1. `npm run typecheck`
2. `npm run test -- src/lib/auth-hardening.test.ts`
3. Static scans with `rg` on auth and middleware codepaths.

### Backend

1. Middleware/auth route code inspections.
2. Health endpoint/router scans.
3. Added targeted middleware test module.
4. Attempted focused pytest run (interrupted per user reprioritization).

### Database

1. Postgres connectivity checks via `psql`.
2. DB/schema/table count checks.
3. Docker-vs-local runtime verification.

---

## Part 17: Day 47 Plan

1. Re-run and finalize backend middleware tests (`test_tenant_middleware.py`).
2. Normalize JWT algorithm/secret configuration usage across auth modules.
3. Add an integration test that hits `/api/v1/health` with invalid bearer token and confirms 200 response.
4. Add a protected endpoint integration test that verifies invalid bearer returns clean 401 JSON.
5. Continue removing old Supabase naming artifacts where non-functional.
6. Keep voice pipeline instrumentation focused on:
   1. turn boundaries
   2. TTS chunk latencies
   3. interruption correctness

---

## Appendix A: High-Level Architecture After Day 46

```mermaid
flowchart LR
    A[Browser UI] --> B[Frontend Auth Guard]
    B -->|Bearer JWT| C[FastAPI Backend]

    C --> D[TenantMiddleware]
    D --> E[API Routes]

    E --> F[Auth Dependencies]
    F --> G[(PostgreSQL)]

    E --> H[Voice Orchestrator]
    H --> I[Deepgram Flux STT]
    H --> J[LLM Provider]
    H --> K[Deepgram Aura TTS]

    E --> L[/api/v1/health]
```

---

## Appendix B: Practical Operator Checklist

### If dashboard opens without login

1. Check `Talk-Leee/middleware.ts` for token bypass logic.
2. Check browser storage for stale `talklee.auth.token`.
3. Confirm `AuthProvider` sets `user = null` on `/auth/me` failure.

### If logout seems ineffective

1. Confirm sidebar calls `await logout()`.
2. Confirm redirect target is `/auth/login?logged_out=1`.
3. Confirm middleware rejects legacy `dev-token`.

### If `/api/v1/health` fails with auth warnings

1. Confirm route exists in `app/api/v1/endpoints/health.py`.
2. Confirm middleware public paths include `/api/v1/health`.
3. Confirm middleware returns JSON 401 on invalid token (not raised exception).

### If JWT invalid signature appears repeatedly

1. Verify frontend token issuance source is current backend login.
2. Clear browser token/cookie and sign in again.
3. Validate `JWT_SECRET` alignment across backend components.
4. Confirm no old tokens from previous secret are being replayed.

---

## Final Note

Day 46 closed the largest reliability gaps reported by users in active use:

1. Voice interaction quality became stable.
2. Postgres migration confidence became operationally grounded.
3. Authentication now behaves like production expectations.
4. Logout semantics now match strict security UX requirements.
5. Middleware health/auth edge case has been patched and only awaits final pytest rerun confirmation.

