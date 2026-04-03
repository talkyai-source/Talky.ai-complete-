# Security Best Practices Report

## Executive Summary

The security layer is **not fully implemented end-to-end yet**.

What is visibly in place:
- Days 1–6 have substantial backend implementation for password hashing, lockout, TOTP, passkeys, session primitives, RBAC/tenant isolation, rate limiting, webhook verification, and idempotency in `../backend/app/core/security/` and are documented as covered by the security test suite in `../backend/docs/security/SECURITY_TESTS_CHANGELOG.md:6`.
- The Next.js frontend has visible MFA, passkey, and session-management UI wiring in `src/lib/api.ts:207`, `src/app/auth/login/page.tsx:256`, and `src/app/settings/page.tsx:40`.

However, several controls described in `../backend/docs/security/` are only partially realized in production paths. The most important gap is that the documented revocable server-side session model is bypassed by bearer-token authentication and browser token storage.

## Critical

### TL-SEC-001 — Revocable session design is bypassed by JWT bearer auth and browser token storage
- **Severity:** Critical
- **Rule/Area:** Session management / auth revocation
- **Locations:** `../backend/docs/security/day_1_core_authentication.md:195`, `../backend/app/api/v1/dependencies.py:84`, `../backend/app/core/session_security_middleware.py:87`, `src/lib/http-client.ts:158`, `src/lib/auth-token.ts:32`
- **Evidence:**
  - The Day 1 security doc says revocable DB-backed sessions are the source of truth because “a JWT cannot be revoked” in `../backend/docs/security/day_1_core_authentication.md:195`.
  - Protected backend dependencies authenticate only from `Authorization: Bearer ...` in `../backend/app/api/v1/dependencies.py:84`.
  - Session middleware skips validation entirely when no session cookie is present in `../backend/app/core/session_security_middleware.py:87`.
  - The frontend automatically injects a bearer token on requests in `src/lib/http-client.ts:158`.
  - The frontend persists the auth token in `localStorage` and a JS-readable cookie in `src/lib/auth-token.ts:35` and `src/lib/auth-token.ts:68`.
- **Impact:** A stolen bearer token can continue to authorize requests even after logout, selective session revocation, or some suspension flows, which breaks the security model promised in the docs.
- **Fix:** Make one server-side session artifact authoritative on protected routes. Either enforce `talky_sid` validation for authenticated requests or bind JWTs to a revocable session ID checked on every request; remove token persistence from `localStorage`/JS-readable cookies.
- **False-positive notes:** If an external gateway already strips bearer auth and enforces session cookies, that protection is not visible in repo code and must be verified at runtime.

## High

### TL-SEC-002 — Day 8 security admin endpoints are still partly stubbed / placeholder-backed
- **Severity:** High
- **Rule/Area:** Audit logging / incident response / admin security operations
- **Locations:** `../backend/app/api/v1/endpoints/security_events.py:58`, `../backend/app/api/v1/endpoints/security_events.py:90`, `../backend/app/api/v1/endpoints/audit_logs.py:245`, `../backend/app/api/v1/endpoints/audit_logs.py:256`
- **Evidence:**
  - `list_security_events()` returns an empty list with “Implementation would query database” at `../backend/app/api/v1/endpoints/security_events.py:70`.
  - `create_security_event()` uses a hard-coded UUID placeholder at `../backend/app/api/v1/endpoints/security_events.py:90`.
  - Audit export/statistics endpoints return notes like “CSV generation would be implemented” and “Statistics aggregation would be implemented” in `../backend/app/api/v1/endpoints/audit_logs.py:245` and `../backend/app/api/v1/endpoints/audit_logs.py:260`.
- **Impact:** Security monitoring, alert triage, and audit workflows described in Day 8 are not fully operational, which weakens detection and response capabilities.
- **Fix:** Replace placeholders with real persistence/query logic, generate real identifiers, and add tests covering audit/event CRUD, exports, and analytics endpoints.
- **False-positive notes:** The underlying `AuditLogger` service exists, but the HTTP/admin workflows are still incomplete in the visible app layer.

### TL-SEC-003 — Secrets management uses demo key wrapping and non-failing defaults
- **Severity:** High
- **Rule/Area:** Secrets at rest / key management
- **Locations:** `../backend/app/domain/services/secrets_manager.py:106`, `../backend/app/domain/services/secrets_manager.py:119`, `../backend/app/api/v1/dependencies.py:420`
- **Evidence:**
  - When no master key is provided, the service falls back to a generated development key in `../backend/app/domain/services/secrets_manager.py:106`.
  - DEK “encryption” is implemented as simple XOR in `../backend/app/domain/services/secrets_manager.py:119`.
  - The dependency factory constructs `SecretsManager(db_pool)` without injecting a stable production key source in `../backend/app/api/v1/dependencies.py:420`.
- **Impact:** The Day 8 “envelope encryption” claim is not production-grade, and key handling may be inconsistent or unrecoverable across restarts if configuration is missing.
- **Fix:** Fail closed when the master key/KMS configuration is absent outside development, and replace XOR wrapping with a real KMS/HSM or standard AES key-wrap mechanism.
- **False-positive notes:** If deployments always inject `SECRETS_MASTER_KEY`, restart breakage risk is reduced, but the XOR wrapper is still not an acceptable production control.

### TL-SEC-004 — Audit-log signatures are not backed by a stable signing key
- **Severity:** High
- **Rule/Area:** Audit log integrity / tamper evidence
- **Locations:** `../backend/app/domain/services/audit_logger.py:223`, `../backend/app/api/v1/dependencies.py:406`
- **Evidence:**
  - `AuditLogger` generates a random signing key when none is supplied in `../backend/app/domain/services/audit_logger.py:223`.
  - The FastAPI dependency returns a new `AuditLogger(db_pool)` instance per request in `../backend/app/api/v1/dependencies.py:406`.
- **Impact:** HMAC signatures on audit entries are not reliably verifiable across requests/processes, undermining the tamper-evident design documented for Day 8.
- **Fix:** Inject a configured signing key from secure config and reuse a singleton/service-container managed logger.
- **False-positive notes:** If another code path replaces this dependency in production, that is not visible here and should be verified explicitly.

## Medium

### TL-SEC-005 — Connector callback messaging trusts any window origin
- **Severity:** Medium
- **Rule/Area:** Cross-window messaging / frontend integrity
- **Locations:** `src/app/connectors/callback/page.tsx:47`, `src/app/connectors/[type]/callback/page.tsx:47`, `src/app/settings/connectors/page.tsx:78`
- **Evidence:**
  - Callback windows use `window.opener.postMessage(payload, "*")` in both callback pages.
  - The settings page consumes `message` events without checking `event.origin` in `src/app/settings/connectors/page.tsx:78`.
- **Impact:** Another window can spoof “connector connected/failed” events and influence UI state or operator decisions.
- **Fix:** Send messages to an explicit trusted origin and verify `event.origin` before accepting the payload.
- **False-positive notes:** `BroadcastChannel` fallback is origin-scoped, but the `postMessage("*")` path is still overly permissive.

### TL-SEC-006 — Backend response-header posture does not match the documented baseline
- **Severity:** Medium
- **Rule/Area:** Security headers / deployment hardening
- **Locations:** `../backend/docs/security/SECURITY_LAYER_PLAN.md:233`, `../backend/app/core/api_security_middleware.py:102`
- **Evidence:**
  - The plan expects `Strict-Transport-Security` and `Content-Security-Policy` on responses in `../backend/docs/security/SECURITY_LAYER_PLAN.md:233`.
  - The visible API middleware sets `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, and `Referrer-Policy`, but not HSTS or CSP, in `../backend/app/core/api_security_middleware.py:102`.
- **Impact:** The documented baseline is not fully enforced in app code; HSTS/CSP may be missing unless added by a reverse proxy/CDN.
- **Fix:** Either implement these controls at the edge and document that deployment dependency, or add them in the app where appropriate.
- **False-positive notes:** This may be intentionally handled by infra; I cannot verify edge headers from repository code alone.

## Coverage Notes

Visible, substantive implementation exists for:
- **Password hashing:** `../backend/app/core/security/password.py:1`
- **TOTP secret encryption:** `../backend/app/core/security/totp.py:89`
- **Server-side session primitives:** `../backend/app/core/security/sessions.py:1`
- **Rate limiting:** `../backend/app/core/security/api_security.py:78`
- **Security-focused tests for Days 1–6:** `../backend/docs/security/SECURITY_TESTS_CHANGELOG.md:6`

This means the repo is **not missing security work entirely**; rather, the biggest problems are:
1. the runtime auth path does not consistently honor the documented session model, and
2. the Day 8 administrative security surface is only partly productionized.

## Recommended Next Order

1. Fix `TL-SEC-001` first before stack testing.
2. Finish real Day 8 endpoint persistence and verification.
3. Replace demo crypto/signing defaults with fail-closed production config.
4. Tighten frontend cross-window messaging.
