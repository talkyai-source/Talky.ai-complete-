# Day 47: Frontend Runtime Recovery, Analytics Stability, and Postgres-First Hardening

> **Date:** February 20, 2026  
> **Focus:** Full-stack stability after Supabase-to-Postgres migration, frontend runtime recovery, analytics API reliability, AI options wiring without dummy data, Deepgram voice catalog integrity, and auth/JWT hardening  
> **Status:** ✅ Major recovery and hardening completed, ⏳ final validation pass still in progress at end of session

---

## 1. Executive Summary

1. Today shifted from targeted bug-fixing to platform-wide reliability work.
2. User-reported failures covered voice, auth, AI options, analytics, and frontend boot.
3. The highest-impact blockers were:
4. Frontend `500` on all routes.
5. Missing Next.js runtime manifest (`.next/routes-manifest.json`).
6. Analytics instability tied to Postgres adapter type binding.
7. JWT secret configuration failures causing auth endpoint errors.
8. AI options failing due to schema mismatches and invalid provider/model combinations.
9. Deepgram voice preview failing on unsupported or stale voice IDs.
10. Duplicate/non-target-language voices shown in voice picker.
11. Residual Supabase naming and behavior in a Postgres-first codebase.
12. Work completed today focused on production-safe fixes, not temporary bypasses.

---

## 2. Starting State (User-Observed Failures)

1. User reported the analytics page returning `500 Internal Server Error`.
2. Then user confirmed this was broader than analytics.
3. Root app route `/` also returned `500`.
4. Browser screenshot showed plain `Internal Server Error` page on `localhost:3000`.
5. Frontend logs showed:
6. `ENOENT: no such file or directory, open ... Talk-Leee/.next/routes-manifest.json`.
7. Backend boot logs were largely healthy.
8. Backend warnings about Vonage keys were non-fatal in development.
9. Prior to this, user also reported:
10. JWT auth token errors (`Invalid authentication token`, signature verification issues).
11. `JWT_SECRET is not configured` errors on `/api/v1/auth/me` and `/api/v1/auth/login`.
12. AI options screen was partially non-functional.
13. Some sections were still driven by dummy/static assumptions.
14. Schema validation errors in frontend due to `null` fields (`price`, `context_window`, `gender`).
15. TTS preview failures when trying multiple Deepgram voices.
16. Deepgram config save failure (`Invalid TTS model. Must be one of: ['aura-2']`).
17. Duplicate voice entries in picker.
18. Unknown voice IDs such as `aura-2-angus-en` failing preview.
19. User requested only English (US/UK) voices remain.
20. Voice distortion/`zzzzz` noise and speed inconsistency were reported in demo flow.
21. Guardrails error surfaced:
22. `AttributeError: 'str' object has no attribute 'value'` in fallback path.
23. Postgres adapter error surfaced:
24. `invalid input for query argument ... expected datetime/date, got 'str'`.
25. User requested Supabase removal and Postgres-first correctness across app.

---

## 3. Guiding Principles Applied Today

1. Do not patch symptoms with one-off hacks.
2. Prefer root-cause corrections with minimal side effects.
3. Keep behavior aligned with official provider contracts.
4. Maintain backward compatibility only where necessary and explicit.
5. Preserve predictable auth semantics:
6. Protected routes must be protected.
7. Public health routes must stay robust.
8. Eliminate dev-token bypass behavior from production paths.
9. Ensure frontend schemas reflect actual backend payloads.
10. Keep AI option behavior sourced from real APIs, not placeholders.
11. Favor deterministic startup behavior for Next.js runtime.
12. Make adapter coercion explicit and typed for Postgres.

---

## 4. Workstream A: Frontend Runtime Recovery (`/` returning 500)

### 4.1 Symptom

1. Every page could fail with `500`.
2. Logs consistently pointed to missing file:
3. `Talk-Leee/.next/routes-manifest.json`.

### 4.2 Root Cause

1. Frontend startup path depended on build artifacts.
2. Runtime started while required `.next` artifacts were absent.
3. In this environment, build generation could fail or be skipped.
4. When manifest is missing, `next start` cannot serve routes.

### 4.3 Corrective Actions

1. Added resilient startup script:
2. `Talk-Leee/scripts/start-next.mjs`.
3. Script checks if `.next/routes-manifest.json` exists.
4. If missing, it runs a production build first.
5. It validates manifest generation before launching `next start`.
6. It exits with explicit error if build still fails.
7. Updated `Talk-Leee/package.json` start command to use script.
8. This removed dependence on manual pre-build discipline.

### 4.4 Build Reliability Improvement

1. Replaced `next/font/google` dependencies in root layout.
2. Shifted to `next/font/local` with local Satoshi assets.
3. This removes external font fetch dependency at build/runtime.
4. Prevents font-network issues from breaking build artifacts.
5. Preserved CSS variable names to avoid style regressions.

### 4.5 Expected Outcome

1. `npm run start` now self-heals missing-manifest conditions.
2. Route manifest no longer missing due to skipped build.
3. Root app and subroutes should stop returning blanket `500`.

---

## 5. Workstream B: Analytics 500 and Postgres Date/Time Binding

### 5.1 Symptom

1. Analytics endpoint intermittently returned internal errors.
2. Logs captured adapter bind-type mismatch:
3. `expected datetime.date or datetime.datetime, got 'str'`.

### 5.2 Root Cause

1. Filter values parsed as strings were passed directly to asyncpg.
2. Adapter lacked column-aware coercion in some query paths.
3. Timestamp/date columns needed typed bind conversion.

### 5.3 Corrective Actions in API Layer

1. Hardened analytics endpoint date parsing.
2. Converted incoming `from`/`to` to timezone-safe datetime bounds.
3. Used inclusive day window with exclusive upper bound.
4. Added defensive handling for `created_at` string vs datetime values.
5. Ensured adapter error propagation returns clear HTTP errors.
6. Preserved explicit `HTTPException` behavior.

### 5.4 Corrective Actions in Postgres Adapter

1. Added column-type aware bind coercion for where filters.
2. Introspected table metadata for UDT/column type mapping.
3. Coerced ISO strings to `date`/`datetime` when column type requires.
4. Applied typed coercion for:
5. Select query filters.
6. Update query filters.
7. Delete query filters.
8. Relationship/query-builder select paths.
9. Reduced implicit runtime coercion risk in asyncpg.

### 5.5 Validation

1. Added targeted unit regression coverage for timestamp coercion.
2. Confirmed compile integrity for backend modules touched.
3. Focused pytest execution passed for new coercion behavior.
4. Result: analytics date-range calls no longer fail due to string binds.

---

## 6. Workstream C: JWT/Auth Stability and Required Login Flow

### 6.1 Problems Observed

1. Invalid token errors surfaced in middleware and dependencies.
2. `JWT_SECRET is not configured` caused `/auth/me` and login failures.
3. Protected dashboard routes needed strict sign-in enforcement.
4. Logout behavior needed mandatory credential re-entry.

### 6.2 Design Goals

1. No hidden dev-token bypasses.
2. Clear separation of public vs protected routes.
3. Stable auth behavior on stale/invalid tokens.
4. Deterministic logout semantics.

### 6.3 Hardening Steps

1. Removed development token seeding paths in frontend flow.
2. Ensured auth context clears invalid token state deterministically.
3. Enforced redirect to login for protected dashboard routes.
4. Preserved `next` parameter redirect-after-login behavior.
5. Ensured logout path clears token and session state reliably.
6. Redirected explicit logout to login intent state.

### 6.4 Backend Safety Improvements

1. Middleware returns structured `401` JSON for invalid auth.
2. Avoided exception patterns that can bubble as framework `500`.
3. Kept health routes public and robust under stale auth headers.
4. Added/maintained API-scoped health compatibility route behavior.

### 6.5 JWT Secret Configuration Issue

1. User logs showed secret not configured at runtime.
2. This is a deployment/config contract issue, not business logic.
3. Required: set `JWT_SECRET` in backend environment consistently.
4. All auth modules must resolve same secret source.
5. No temporary bypass introduced.
6. Recommended handling stayed standards-aligned:
7. Fail closed for auth when secret is missing.
8. Return explicit startup/runtime configuration error.

---

## 7. Workstream D: AI Options Screen - Real Data, No Dummy Inputs

### 7.1 User Requirement

1. AI options screen must be fully functional.
2. It must not rely on dummy dataset behavior.

### 7.2 Failures Seen

1. Frontend Zod validation failed on `null` fields:
2. `price` expected string but received null.
3. `context_window` expected number but received null.
4. Later voice schema failed:
5. `gender` expected string but received null for many rows.
6. Save config request failed due incompatible model selection payload.

### 7.3 Corrective Approach

1. Enforced schema-safe mapping at API client boundary.
2. Normalized nullable backend values to safe frontend defaults.
3. Removed assumptions that all providers expose same field richness.
4. Kept UI functional even when optional metadata is absent.
5. Preserved strict typing while allowing provider-specific incompleteness.

### 7.4 Backend/Contract Alignment

1. Ensured AI options endpoints are source of truth.
2. Removed fallback dummy generation in critical save/list flows.
3. Standardized save payload validation for provider/model compatibility.
4. Prevented invalid combinations from being silently accepted.

---

## 8. Workstream E: Deepgram TTS Model/Voice Integrity

### 8.1 Problems Reported

1. Saving config failed:
2. `Invalid TTS model. Must be one of: ['aura-2']`.
3. Voice preview failed with `400` for unknown voice IDs.
4. Duplicate voices visible in list.
5. User requested only English UK/US voices remain.

### 8.2 Root Causes

1. Payload mixed unsupported model values (`Chirp3-HD`) under Deepgram provider.
2. Voice list contained stale or non-supported IDs.
3. Catalog likely included cross-provider or legacy entries.
4. Deduplication/normalization was incomplete.
5. Preview route lacked full guard against unknown IDs from UI catalog.

### 8.3 Corrective Actions

1. Enforced model whitelist per provider for save config.
2. For Deepgram provider, constrained to supported `aura-2` model family.
3. Rebuilt/filtered voice list toward official supported IDs.
4. Removed duplicates in catalog transformation path.
5. Restricted displayed voices to English locale set per request.
6. Excluded unknown IDs from preview path.
7. Preserved Google tab behavior untouched per user instruction.

### 8.4 Catalog Quality Rules

1. Every displayed voice must be previewable.
2. Every previewable voice must be valid for selected provider/model.
3. No duplicate IDs.
4. No mixed-provider leakage.
5. No stale IDs not recognized by provider.
6. Locale filters are deterministic and explicit.

---

## 9. Workstream F: Voice Pipeline Quality (Distortion / Speed Variance)

### 9.1 Symptoms

1. User heard unclear speech and buzzing noise (`zzzzz` artifacts).
2. Playback seemed to jump between normal and faster rate.

### 9.2 Potential Technical Contributors Reviewed

1. Chunk sizing mismatch across synth/playback pipeline.
2. Sample-rate conversion behavior in browser media gateway.
3. Incorrect assumptions on PCM format and conversion skip logic.
4. Buffer queue continuity during long multi-chunk responses.
5. Text chunking behavior under long package explanations.

### 9.3 Related Runtime Signals

1. Prior logs showed high-frequency audio validation entries.
2. Some traces showed keepalive/control-message incompatibility in Flux stream.
3. Another trace surfaced guardrails fallback type error halting response path.
4. Adapter JSON binding error also occurred during session-end logging.

### 9.4 Production-Ready Direction Applied

1. Stabilize chunk boundaries and ensure deterministic synthesis flow.
2. Maintain explicit sample-rate contract end-to-end.
3. Keep STT muting windows aligned with TTS playback lifecycle.
4. Validate provider payloads before stream start.
5. Remove known parser-breaking text/regex edge paths.
6. Keep logs actionable but not noisy for operational debugging.

---

## 10. Workstream G: Supabase-to-Postgres Completion

### 10.1 User Requirement

1. Ensure nothing functionally depends on Supabase anymore.
2. Ignore docs/MD for this cleanup.
3. Remove Supabase-related artifacts in active runtime code.

### 10.2 Current State

1. Runtime DB is Postgres-backed.
2. Adapter behavior and query path are Postgres-first.
3. Some naming residue may still reflect historical `supabase_*` identifiers.

### 10.3 Completed in This Cycle

1. Critical operational paths validated on Postgres adapter.
2. Analytics and event write paths examined for Postgres compatibility.
3. Runtime health logs confirm postgres pool initialization.

### 10.4 Remaining Hygiene

1. Symbol/file naming cleanup can continue safely.
2. Rename should be staged to avoid import breakage.
3. Compatibility wrappers should be minimized then removed.

---

## 11. Concrete Errors Addressed During Day 47

1. `ENOENT ... .next/routes-manifest.json`.
2. Frontend `GET / 500`.
3. Analytics adapter bind type mismatch (`str` vs datetime/date).
4. JWT secret missing runtime failures on auth endpoints.
5. Invalid provider/model combo for TTS config save.
6. Voice preview unknown ID errors.
7. Zod invalid type errors from nullable backend fields.
8. Duplicate voice catalog rendering.
9. Health/auth interaction reliability issues from invalid tokens.

---

## 12. Key Files Touched (High-Impact)

### Frontend (Talk-Leee)

1. `Talk-Leee/scripts/start-next.mjs` (new startup guard script).
2. `Talk-Leee/package.json` (`start` command hardened).
3. `Talk-Leee/src/app/layout.tsx` (local fonts for build stability).
4. AI options client/server mapping files (schema and payload normalization).
5. Voice picker and preview integration files (catalog + validation path).
6. Auth middleware/context/login/logout flow files (strict gating).

### Backend

1. `backend/app/api/v1/endpoints/analytics.py` (date-window robustness).
2. `backend/app/core/postgres_adapter.py` (typed coercion).
3. Middleware/auth dependency files (invalid token and public route handling).
4. AI options endpoints/services for provider-model validation.
5. TTS preview endpoint/provider mapping for valid voice/model enforcement.

### Tests

1. Adapter unit tests for datetime bind coercion regression.
2. Middleware/auth tests around invalid token behavior and route safety.
3. Existing frontend auth hardening tests retained.

---

## 13. Verification Performed

### 13.1 Static/Build Validation

1. Frontend lint run completed with no hard errors.
2. Backend compile check completed for touched modules.
3. Targeted pytest runs used for high-risk adapter behavior.

### 13.2 Runtime Validation

1. Backend startup observed healthy container initialization.
2. Postgres pool connected and active.
3. Redis connected for session manager.
4. `/health` responses observed as `200`.
5. Analytics call with date range executed after fixes.

### 13.3 Manual Product Checks (As Reported)

1. User confirmed many previously failing voice flows improved.
2. User confirmed visibility of DB objects in pgAdmin once configured.
3. User continued driving edge-case validation on AI options and preview.

---

## 14. Decisions and Why They Were Chosen

### Decision 1: Add startup build guard instead of telling operator to rebuild manually

1. Manual build discipline is brittle.
2. Missing manifest should be self-diagnosed and auto-recovered.
3. This prevents recurring production-like boot mistakes.

### Decision 2: Replace Google-hosted fonts with local fonts in root layout

1. External dependency can break build in restricted/offline environments.
2. Local fonts make build deterministic.
3. Reduced chance of hidden startup failures.

### Decision 3: Implement typed adapter coercion centrally

1. Fixing only analytics endpoint would be incomplete.
2. Central coercion prevents class of bind errors across endpoints.
3. This is safer and more reusable than per-endpoint ad-hoc parsing.

### Decision 4: Keep auth strict when `JWT_SECRET` is missing

1. Temporary bypass for secret would be insecure.
2. Correct behavior is explicit configuration failure.
3. Production-safe posture: fail closed, with clear diagnostics.

### Decision 5: Enforce provider-model-voice compatibility contracts

1. Prevents invalid payloads from reaching synthesis APIs.
2. Eliminates silent mismatch behaviors.
3. Improves UX by failing early with clear messages.

---

## 15. Remaining Open Items at End of Day

1. Final full-stack revalidation after latest frontend runtime hardening.
2. Confirm no residual `500` on `/` once build/start script path is active.
3. End-to-end AI options save + preview test across all retained voices.
4. Additional cleanup pass for Supabase naming residue.
5. Broader integration regression for voice demo flow under long responses.

---

## 16. Risk Register (Current)

### R1: Environment config drift

1. Missing `JWT_SECRET` can hard-fail auth endpoints.
2. Mitigation: enforce startup validation and documented env templates.

### R2: Provider catalog drift

1. Upstream voice/model catalogs can change.
2. Mitigation: central catalog source + validation + periodic refresh.

### R3: Runtime artifact drift in frontend

1. Missing `.next` artifacts can crash app startup.
2. Mitigation: startup guard script and CI build checks.

### R4: Hidden typed bind mismatches in less-used queries

1. Some endpoints may still pass ambiguous values.
2. Mitigation: expand adapter coercion tests and integration coverage.

---

## 17. Operational Runbook Updates

### 17.1 If frontend shows `ENOENT routes-manifest`

1. Use `npm run start` from `Talk-Leee` with new guarded start script.
2. Confirm `.next/routes-manifest.json` is generated.
3. Check build output for failing imports/assets.

### 17.2 If auth endpoints return `JWT_SECRET is not configured`

1. Set `JWT_SECRET` in backend runtime environment.
2. Restart backend process.
3. Recheck `/api/v1/auth/me` and login flow.

### 17.3 If analytics fails with date-range filters

1. Verify adapter is on latest typed-coercion code.
2. Confirm endpoint receives valid ISO dates.
3. Check backend logs for adapter `.error` details.

### 17.4 If Deepgram preview fails

1. Verify selected provider is Deepgram.
2. Verify model is `aura-2`.
3. Verify voice ID exists in filtered supported list.
4. Ensure no stale cached catalog in browser state.

---

## 18. What Was Explicitly Avoided (On Purpose)

1. No auth bypasses for missing JWT secret.
2. No hardcoded fake success responses for save/preview flows.
3. No dummy data fallback to hide broken API contracts.
4. No broad try/except swallowing runtime errors silently.
5. No forceful disabling of schema checks to “make UI pass”.

---

## 19. Production-Readiness Indicators Improved Today

1. Better startup determinism for Next.js runtime.
2. Better typed correctness for Postgres queries.
3. Stronger auth boundary behavior.
4. Cleaner provider-model contract enforcement.
5. Reduced catalog inconsistency risk for voices.
6. Better route-level resilience for health endpoints.
7. More actionable logs around config and adapter failures.

---

## 20. Day 48 Plan (Proposed)

1. Execute full end-to-end smoke:
2. Login.
3. Dashboard summary.
4. Analytics charts.
5. AI options save.
6. Voice preview.
7. Ask AI live turn.
8. Add integration tests for:
9. Frontend startup script behavior with missing `.next`.
10. Voice preview request contract with valid/invalid IDs.
11. Analytics date filters on multiple date ranges.
12. Continue Supabase naming cleanup (code-only scope).
13. Add catalog contract test against approved Deepgram voice set.
14. Close remaining open risk items from Section 16.

---

## Appendix A: Incident-to-Fix Mapping

1. Incident: `/` route `500` with manifest ENOENT.
2. Fix: startup guard + local font migration.
3. Incident: analytics internal error on date filters.
4. Fix: endpoint date parsing + adapter typed coercion.
5. Incident: `JWT_SECRET` missing runtime failures.
6. Fix: strict env validation and auth flow hardening.
7. Incident: AI options schema null crashes.
8. Fix: schema-safe normalization and contract alignment.
9. Incident: invalid TTS model in config save.
10. Fix: enforce Deepgram `aura-2` model compatibility.
11. Incident: unknown voice preview IDs and duplicates.
12. Fix: deduped, validated English-only voice catalog.

---

## Appendix B: User-Visible Improvements

1. App startup path is more reliable.
2. Protected routes behave more predictably.
3. Analytics requests are less fragile around date ranges.
4. AI options page is closer to fully live-data behavior.
5. Voice list quality is cleaner and less confusing.
6. Invalid configuration choices are blocked earlier.
7. System errors are clearer and easier to troubleshoot.

---

## Appendix C: Notes for Future Contributors

1. Keep auth strictness decisions centralized.
2. Avoid introducing provider-specific assumptions into shared schemas.
3. Validate catalog entries at ingestion, not only at preview time.
4. Extend adapter-level tests whenever query builder behavior changes.
5. Do not reintroduce mock auth behavior into production path.
6. Preserve explicit startup checks that prevent silent boot failure.

---

## Final Note

1. Day 47 focused on rebuilding trust in runtime behavior after broad migration and config churn.
2. The work emphasized stable contracts across frontend, backend, and providers.
3. The remaining path is mainly verification and cleanup, not architectural rework.
4. Current direction remains aligned with production-ready standards.
