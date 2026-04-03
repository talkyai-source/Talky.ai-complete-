# Security Fix Report

## Summary

I fixed the highest-priority security gaps found in the earlier audit and reduced the largest runtime risks in both the backend and frontend.

Primary outcomes:
- Browser/API auth is now tied back to revocable server-side sessions instead of trusting bearer JWTs alone.
- Day 8 security admin endpoints now perform real database work instead of returning placeholders.
- Audit signing and secrets management no longer rely on random-per-request or XOR-based defaults.
- Frontend route protection now follows the backend session cookie, and cross-window connector messaging now checks origin.

## What Was Fixed

### 1. Session-bound auth and safer browser flow
- Added optional JWT session binding via `sid` in `../backend/app/core/jwt_security.py`.
- Updated auth, MFA, and passkey login flows to create a DB session and bind the issued JWT to that session in:
  - `../backend/app/api/v1/endpoints/auth.py`
  - `../backend/app/api/v1/endpoints/mfa.py`
  - `../backend/app/api/v1/endpoints/passkeys.py`
- Updated `../backend/app/api/v1/dependencies.py` so authenticated requests now:
  - accept valid session-cookie auth,
  - validate `sid` for bearer tokens,
  - fall back to cookie-backed validation for legacy browser tokens during transition.
- Updated `../backend/app/core/session_security_middleware.py` to carry validated session metadata forward for dependencies.
- Updated frontend fetches to include cookies in `src/lib/http-client.ts`.
- Switched frontend route protection to the backend session cookie in `src/middleware.ts`.
- Removed the JS-readable auth cookie as an active auth source in `src/lib/auth-token.ts`.

### 2. Stable audit signing and stronger secret wrapping
- Replaced random audit signing fallback with stable configured/derived key handling in `../backend/app/domain/services/audit_logger.py`.
- Added singleton-style dependency reuse in `../backend/app/api/v1/dependencies.py` for audit/secrets/suspension/emergency services.
- Replaced XOR key wrapping with AES key wrap in `../backend/app/domain/services/secrets_manager.py`.
- Added fail-closed production behavior for missing `SECRETS_MASTER_KEY` / `AUDIT_LOG_SIGNING_KEY` class of config.

### 3. Day 8 admin endpoints moved from placeholder to working DB-backed behavior
- Implemented real CRUD/query behavior for security events in `../backend/app/api/v1/endpoints/security_events.py`.
- Implemented CSV export and aggregate audit stats in `../backend/app/api/v1/endpoints/audit_logs.py`.
- Added compatibility accessors to `CurrentUser` in `../backend/app/api/v1/dependencies.py` so existing Day 8 endpoints using `current_user["id"]` keep working safely.

### 4. Frontend cross-window messaging tightened
- Restricted connector callback `postMessage` target origin in:
  - `src/app/connectors/callback/page.tsx`
  - `src/app/connectors/[type]/callback/page.tsx`
- Added origin checks on message listeners in:
  - `src/app/settings/connectors/page.tsx`
  - `src/app/reminders/page.tsx`

## Files Changed

| File | Why changed | Confidence |
|---|---|---|
| `../backend/app/core/jwt_security.py` | Added `sid` claim support so JWTs can be tied to revocable sessions | High |
| `../backend/app/core/security/sessions.py` | Returned session IDs from session creation and added session lookup helper for auth binding | High |
| `../backend/app/core/session_security_middleware.py` | Preserved validated session metadata for downstream auth and matched cookie security handling | High |
| `../backend/app/api/v1/dependencies.py` | Enforced session-aware auth, added cookie auth support, stabilized service factories, added compatibility accessors | Medium-High |
| `../backend/app/api/v1/endpoints/auth.py` | Bound login/register JWTs to created sessions and made cookie `Secure` env-aware for local/prod correctness | High |
| `../backend/app/api/v1/endpoints/mfa.py` | Bound MFA-completed logins to session IDs | High |
| `../backend/app/api/v1/endpoints/passkeys.py` | Bound passkey logins to session IDs | High |
| `../backend/app/domain/services/audit_logger.py` | Removed random signing fallback and required stable signing material | High |
| `../backend/app/domain/services/secrets_manager.py` | Replaced XOR wrapping with AES key wrap and hardened master-key resolution | High |
| `../backend/app/api/v1/endpoints/security_events.py` | Replaced placeholder responses with DB-backed list/get/create/update/resolve/escalate paths | Medium |
| `../backend/app/api/v1/endpoints/audit_logs.py` | Replaced placeholder export/stats responses with CSV export and aggregate queries | Medium |
| `src/lib/auth-token.ts` | Stopped using JS-readable cookie as an auth source; preserved local token storage for existing websocket-dependent flows | Medium |
| `src/lib/http-client.ts` | Included cookies on requests so browser sessions actually work end-to-end | High |
| `src/lib/auth-context.tsx` | Switched profile bootstrap to cookie-backed `getMe()` instead of local-token presence check | High |
| `src/middleware.ts` | Protected routes using backend session cookie instead of frontend-readable auth cookie | High |
| `src/app/connectors/callback/page.tsx` | Restricted `postMessage` target origin | High |
| `src/app/connectors/[type]/callback/page.tsx` | Restricted `postMessage` target origin | High |
| `src/app/settings/connectors/page.tsx` | Added `event.origin` verification before accepting connector updates | High |
| `src/app/reminders/page.tsx` | Added `event.origin` verification before accepting window messages | High |

## Validation Performed

### Passed
- Python syntax validation on changed backend files via `python3 -m py_compile`
- Focused backend security tests via `../backend/venv/bin/python -m pytest ../backend/tests/security/test_sessions.py ../backend/tests/unit/test_jwt_security.py -q`
  - Result: **17 passed**

### Blocked by pre-existing repo issues
- Full frontend typecheck is currently not clean for the repo as a whole.
- `node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit` fails because of existing unrelated test/type issues in files like:
  - `src/app/ai-voices/page.test.tsx`
  - `src/components/ui/confirm-dialog.test.ts`
  - `src/lib/passkeys.ts`

I did **not** see the typecheck fail on the frontend files I changed in this pass.

## Remaining Risk / Honest Caveats

- The frontend still keeps a browser token in storage for existing token-driven flows such as websocket usage. The high-severity revocation problem is reduced because backend bearer auth now validates session binding, but a complete removal of browser-stored tokens would require a broader websocket/auth refactor.
- The Day 8 endpoint implementations are now functional, but they would benefit from dedicated integration tests around the real database schema and permission model.

## Confidence

- **Backend auth/session changes:** ~85%
- **Audit/secrets hardening:** ~85%
- **Day 8 endpoint productionization:** ~75%
- **Frontend messaging/middleware/auth flow changes:** ~80%

Overall, I’m **reasonably confident (~80–85%)** these changes improve security without introducing major regressions, with the biggest remaining uncertainty coming from broader app integration paths that were not fully exercised in a running end-to-end stack yet.
