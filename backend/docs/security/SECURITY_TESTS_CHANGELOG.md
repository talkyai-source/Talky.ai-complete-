# Security Layer Test Suite — Changelog

## 2026-03-26: Initial Test Suite Created

### Summary
Created comprehensive test suite at `backend/tests/security/` covering all 8 phases of the Security Layer Plan with **243 passing tests**.

### Files Created
| File | Coverage Area | Day | Tests |
|------|--------------|-----|-------|
| `conftest.py` | Shared fixtures (Fernet key, mock DB, mock Redis, mock Request) | — | — |
| `test_password.py` | Argon2id hashing, bcrypt compat, strength validation, rehash | Day 1 | 17 |
| `test_lockout.py` | Progressive lockout thresholds, DB queries, unlock timing | Day 1 | 14 |
| `test_totp.py` | TOTP generation, Fernet encryption, QR code, replay prevention | Day 2 | 19 |
| `test_recovery.py` | Recovery code generation, formatting, SHA-256, DB operations | Day 2 | 16 |
| `test_passkeys.py` | WebAuthn config, data classes, challenge lifecycle | Day 3 | 13 |
| `test_rbac.py` | Role hierarchy, normalisation, permissions, check_permission | Day 4 | 20 |
| `test_device_fingerprint.py` | UA parsing, IP subnets, fingerprint comparison | Day 5 | 21 |
| `test_sessions.py` | Session tokens, hashing, OWASP constants, Day 5 config | Day 5 | 11 |
| `test_webhook_verification.py` | HMAC-SHA256, timestamps, replay protection, round-trip | Day 6 | 18 |
| `test_api_security.py` | Rate limit tiers, Redis keys, fail-open, throttle/block | Day 6 | 15 |
| `test_idempotency.py` | Key generation, caching, conflict detection, singleton | Day 6 | 16 |

### Test Approach
- **Pure unit tests** for all logic functions (no external dependencies needed)
- **Mocked DB/Redis** for async database and cache operations
- **Circular import handling**: `test_rbac.py` uses a stub for `app.api.v1.dependencies` to break the `rbac.py ↔ dependencies.py` circular import chain
- **OWASP/NIST verification**: Tests verify that security parameters (entropy, timeouts, thresholds) meet documented standards

### Known Issues
- `rbac.py` has a circular import with `app.api.v1.dependencies` at module level — tests work around this with a mock stub
- `check_permission()` in `rbac.py` raises `ValueError` when checking permissions for resources without a `resource:admin` enum variant (e.g., `users:delete` fails because `users:admin` doesn't exist). This is a production code concern, not a test issue.

### Running Tests
```bash
cd backend
python -m pytest tests/security/ -v
```

---

## 2026-03-26: Frontend Security Integration

### Summary
Integrated the backend Security Layer endpoints into the Next.js frontend (`Talk-Leee/`), wiring MFA, passkey, and session management to the UI.

### Files Modified

| File | Changes |
|------|---------|
| `src/lib/api.ts` | Added MFA lifecycle methods (`getMfaStatus`, `setupMfa`, `confirmMfa`, `verifyMfaChallenge`, `disableMfa`, `regenerateRecoveryCodes`), session management (`getActiveSessions`, `revokeSession`, `getSessionSecurityStatus`), and passkey API methods (`checkUserHasPasskeys`, `beginPasskeyLogin`, `completePasskeyLogin`, `beginPasskeyRegistration`, `completePasskeyRegistration`, `listPasskeys`, `updatePasskey`, `deletePasskey`). Updated `LoginResponseSchema` to include `mfa_required` and `mfa_challenge_token`. |
| `src/app/auth/login/page.tsx` | Implemented two-step login flow: if backend returns `mfa_required`, UI transitions to an MFA verification form. Added "Sign in with Passkey" button (conditionally rendered via `isWebAuthnSupported()`). |
| `src/app/settings/page.tsx` | Replaced placeholder "2FA coming soon" with three dedicated Security sub-components: `MfaSetupSection` (enable/disable MFA with QR code + recovery codes), `PasskeysSection` (list/register/delete passkeys), and `ActiveSessionsSection` (view/revoke active sessions). |

### Design Decisions
- **Two-step auth flow**: The login page uses a state machine (`idle` → `mfa_required`) to keep the UI simple while supporting the backend's challenge-token pattern.
- **Recovery codes**: Shown once during MFA setup with copy-to-clipboard and a strong warning. Never displayed again.
- **Passkey conditional rendering**: The "Sign in with Passkey" button and "Add passkey" button only appear when `window.PublicKeyCredential` is available, avoiding confusion on unsupported browsers.
- **Session revocation UX**: The current session is badged and cannot be revoked, preventing self-lockout.
