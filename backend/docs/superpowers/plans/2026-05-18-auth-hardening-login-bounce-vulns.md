# Plan: Auth hardening — fix login bouncing + close 14 audited vulns

## Context

Two problems to solve in one push, prioritized by user-facing pain first:

**A. Production users hit an intermittent "login multiple times" loop.**
Sign in → bounced back to /login → retry → eventually it works. Manual back-button / re-navigation lands them on /dashboard with no login screen, which proves the session IS valid on the backend. The bug is purely client-side: a transient 401 from `api.getMe()` fired by SuspensionStateProvider on dashboard mount races the cookie commit, the http-client's `fireSessionExpired()` is now suppressed during the fresh-login grace window (shipped in commit `8cd5614`) **but the 401 is still thrown to the caller**, and any `.catch()` block that calls `setUser(null)` re-triggers `DashboardLayout`'s redirect-to-login useEffect (`Talk-Leee/src/components/layout/dashboard-layout.tsx:41-51`).

**B. A full OWASP-grade audit of the auth surface (login, register, MFA, passkeys, refresh, sessions) surfaced 14 real vulnerabilities** — 3 CRITICAL with exploit-ready impact (account takeover via /register, MFA brute-force, WebAuthn clone bypass), 5 HIGH (MFA setup race, no `/forgot-password` rate limit, etc.), 6 MEDIUM hardening items.

Intended outcome: ship Phase 1 today to stop the user-facing pain, then ship the security fixes over the following days in severity order.

---

## Phase 1 — Stabilize login (USER-FACING, ship first, ~1.5 hr)

### Root cause confirmed
The fresh-login grace window I shipped in `Talk-Leee/src/lib/http-client.ts:127-156` suppresses `fireSessionExpired()` correctly, but the 401 from `/auth/me` is still **thrown** to callers as an `ApiClientError`. Three downstream `.catch()` blocks then call `setUser(null)`:

| File:line | Where |
|---|---|
| `Talk-Leee/src/lib/auth-context.tsx:74-78` | Bootstrap useEffect catch |
| `Talk-Leee/src/lib/auth-context.tsx:139-141` | `setToken` callback catch |
| `Talk-Leee/src/lib/auth-context.tsx:150-152` | `refreshUser` callback catch |

Any of those firing → `user=null` → `DashboardLayout:41-51` redirects to `/auth/login`.

### Fixes

**P1.1 Export `isWithinFreshLoginGrace` from http-client**
- `Talk-Leee/src/lib/http-client.ts`: `export function isWithinFreshLoginGrace()`. Already private; just expose.

**P1.2 Auth-context catch blocks respect the grace window**
- `Talk-Leee/src/lib/auth-context.tsx:74-78`, `:139-141`, `:150-152`
- In each catch, `if (isWithinFreshLoginGrace()) return;` before `setUser(null)`.
- Reason: transient 401 during the post-login race is not a real session expiry. Outside the window, behavior is unchanged.

**P1.3 Auth-context bootstrap retries `/auth/me` once with backoff inside the grace window**
- Same file, bootstrap useEffect.
- If `getMe()` rejects AND we're inside grace, wait 1500ms and retry once before giving up. Most cookie-commit races resolve in <500ms; 1500ms is generous.

**P1.4 DashboardLayout's redirect waits for grace window to expire**
- `Talk-Leee/src/components/layout/dashboard-layout.tsx:41-51`
- Add `if (isWithinFreshLoginGrace()) return;` to the useEffect.
- Reason: if the auth bootstrap is still racing, don't bounce — let Phase 1.3 settle it.

**P1.5 Extend grace window to 15s**
- `Talk-Leee/src/lib/http-client.ts:147` → `FRESH_LOGIN_GRACE_MS = 15000`.
- 8s was barely enough for the cookie commit + JWT iat skew combo on slow networks; 15s is what Auth0 / Clerk use for the same problem.

**P1.6 Diagnostic console output**
- In dev only (`process.env.NODE_ENV !== "production"`), log every "would-have-bounced but suppressed by grace" event so we can verify in DevTools that the suppression is firing.

### Critical files
- `Talk-Leee/src/lib/http-client.ts` (modify: export helper, extend window)
- `Talk-Leee/src/lib/auth-context.tsx` (modify: 3 catch blocks + retry)
- `Talk-Leee/src/components/layout/dashboard-layout.tsx` (modify: guard the redirect useEffect)

### Verification
1. Open `https://talkleeai.com` in incognito → log in → land on /dashboard cleanly. Do this 5 times.
2. DevTools console should show `[auth] session-expired swallowed inside fresh-login grace window` 0 or 1 times per login; never a hard redirect to /login.
3. After 15s + 1 minute idle, normal session-expiry behavior is preserved: revoking the session via DB causes the next request to bounce to /login.
4. `tsc --noEmit` clean.

---

## Phase 2 — Critical security (ship within 24h, ~1.5 hr)

Three vulns with exploit-ready impact today.

### P2.1 — MFA verify brute-force gate (C2)
**File:** `backend/app/api/v1/endpoints/mfa.py:478` (verify_mfa_challenge)

Add per-challenge-token attempt cap: 5 wrong codes ⇒ challenge invalidated (delete the row), force user to start over. Backed by a `attempts INT DEFAULT 0` column on the `mfa_challenges` table (new migration `0007_mfa_challenge_attempts`).

Also: rate-limit by `challenge_token_hash` in Redis (10 attempts / 5min) as belt-and-braces.

Effect: closes the 100-IP botnet brute-force vector. Real attestation/SMS-style step-up isn't needed.

### P2.2 — Stop issuing sessions on `/register` until email verified (C1 + H3)
**File:** `backend/app/api/v1/endpoints/auth/registration.py:148-178` + the JWT issuance lines around `create_session(...)`.

Two-option fix (recommend (a)):

**(a) Don't issue a session at all.** Return 201 with `{verification_required: true, email: <email>}`. User flow: register → check email → click link → land on /auth/login with email pre-filled.

**(b) Issue a restricted-scope session.** Add `email_verified: false` claim to the JWT; gate every tenant-scoped endpoint in `get_current_user` on `if not user.email_verified: raise 403`.

Option (a) is simpler and matches the two-step `/auth/signup/start` + `/auth/signup/complete` flow that already works correctly. The `/register` endpoint is legacy; new clients should use `/signup`. We can delete `/register` after a soak.

### P2.3 — Block WebAuthn clone detection (C3)
**File:** `backend/app/core/security/passkeys.py:644-650`

```python
# CURRENT (broken):
if current_sign_count > 0 and new_sign_count <= current_sign_count:
    logger.critical("CLONE DETECTED: ...")
    # Still allow login but flag for security review

# FIXED:
if current_sign_count > 0 and new_sign_count <= current_sign_count:
    logger.critical("CLONE DETECTED: credential_id=%s ...", credential_id)
    await disable_credential(conn, credential_id)  # mark disabled in DB
    raise ValueError("Authenticator integrity check failed. This passkey has been disabled.")
```

5-line change. Matches W3C §6.1.3.

### Critical files
- `backend/app/api/v1/endpoints/mfa.py` (per-challenge attempts)
- `backend/Alembic/versions/0007_mfa_challenge_attempts.py` (new)
- `backend/app/api/v1/endpoints/auth/registration.py` (no session)
- `backend/app/core/security/passkeys.py` (block clones)

### Verification
- `pytest backend/tests/unit/test_mfa_verify_brute_force.py` (new) — submits 6 wrong codes, asserts 6th returns 401 AND challenge_token is now invalid.
- Manual: register a new user via `/auth/register`, observe the response no longer contains `access_token`. Verify email → log in normally.
- Manual: with a real authenticator, modify the stored `sign_count` in DB to be higher than the next auth response's `new_sign_count` → next login attempt should fail with the clone error AND disable the credential.

---

## Phase 3 — High severity (ship within the week, ~3 hr)

### P3.1 — `/forgot-password` rate limit by email (H2)
**File:** `backend/app/api/v1/endpoints/auth/password_reset.py:96`

Add Redis-backed limit: 3 reset codes per email per hour. Generic 200 response unchanged (no enumeration).

### P3.2 — Revoke other sessions on password change + MFA disable (H4)
**Files:**
- `backend/app/api/v1/endpoints/auth/password.py` (password change handler)
- `backend/app/api/v1/endpoints/mfa.py:716+` (disable_mfa)

After commit, run `DELETE FROM security_sessions WHERE user_id = $1 AND id <> $2` (id <> current session). Same for `refresh_tokens` — revoke all in the same user/family scope except the current one.

### P3.3 — Reject `/mfa/setup` when MFA already enabled (H5)
**File:** `backend/app/api/v1/endpoints/mfa.py:335-351`

Change the `INSERT ON CONFLICT DO UPDATE` to first SELECT `enabled`. If `enabled=TRUE`, return 409 "Disable existing MFA first". Closes the silent-downgrade race.

### P3.4 — Passkey attestation INDIRECT for tenant_admin (H1)
**File:** `backend/app/core/security/passkeys.py:390`

Change `AttestationConveyancePreference.NONE` → `INDIRECT` for users with role `tenant_admin` or `platform_admin`. Keep `NONE` for regular users (UX cost).

Add an MDS (FIDO Metadata Service) lookup — `webauthn.helpers.parse_attestation_object` then check the AAGUID against a small allowlist of known-good vendors (YubiKey, Apple, Google, 1Password, Bitwarden). Reject unknown AAGUIDs for tenant_admin enrollments only.

### P3.5 — Passkey enumeration fix on `/login/begin` (M4 → bumped to H)
**File:** `backend/app/api/v1/endpoints/passkeys.py:357-368`

Always return a response shape compatible with WebAuthn — even when no passkeys exist, return a list with random-looking opaque credential descriptors. Only the actual WebAuthn challenge sign step reveals the truth.

### Critical files
- `backend/app/api/v1/endpoints/auth/password_reset.py`
- `backend/app/api/v1/endpoints/auth/password.py`
- `backend/app/api/v1/endpoints/mfa.py`
- `backend/app/core/security/passkeys.py`
- `backend/app/api/v1/endpoints/passkeys.py`
- `backend/app/core/security/refresh_tokens.py` (revoke-on-password-change helper, reused from existing service)

### Verification
- `pytest backend/tests/unit/test_password_change_revokes_sessions.py` (new)
- `pytest backend/tests/unit/test_mfa_setup_409_when_enabled.py` (new)
- `pytest backend/tests/unit/test_forgot_password_rate_limit.py` (new)
- Manual: pen-test the enumeration — call `/login/begin` with `email=does-not-exist@x.com` and `email=uzairdevelops@gmail.com`. Responses must be structurally identical.

---

## Phase 4 — Medium hardening (ship within 2 weeks, ~2 hr)

### P4.1 — Refresh-token family revocation on expiry (M1)
**File:** `backend/app/core/security/refresh_tokens.py:135`

When an expired refresh is presented, mark the whole family `revoked_at=NOW(), reason='expiry_with_subsequent_use'`. Existing reuse detection already handles fresh tokens; this covers the expired-then-leaked window.

### P4.2 — Tighten cookie paths (M2)
**File:** `backend/app/core/security/cookies.py:36`

`talky_at` Path=`/api/v1` (was `/`). Defense-in-depth against XSS on unrelated paths (impossible today, future-proofing).

### P4.3 — Recovery code consumption transactional (M5)
**File:** `backend/app/api/v1/endpoints/mfa.py:605-626`

Wrap recovery code consume + session create in `async with conn.transaction():`. Today: code consumed → session create exception → code is gone, user locked out.

### P4.4 — Explicit `expected_origin` in passkey verify (M6)
**Files:** `backend/app/api/v1/endpoints/passkeys.py:287, 500`

Pass `expected_origin=request.headers["origin"]` after validating against `PASSKEY_RP_ORIGIN` allowlist. Closes the silent-env-degradation hole (the one that bit us with the localhost RP_ORIGIN).

### P4.5 — Consolidate session endpoints (M3)
**Files:** `backend/app/api/v1/endpoints/auth/sessions.py` and `backend/app/api/v1/endpoints/sessions.py`

Both are currently mounted. Either consolidate into one file or rename to make the boundary obvious (`sessions_lifecycle.py` for logout/logout-all vs `sessions_management.py` for list/revoke).

### Verification
- Manual matrix test:
  1. Login → manually expire JWT (set iat backwards in DB) → next request triggers refresh → refresh succeeds → session keeps working
  2. Steal a refresh token from DB, wait for it to expire naturally, then present it → reuse detection fires AND family is now marked revoked (check the DB row)
  3. Use recovery code while simulating an outage on the session table (drop privileges mid-flight) → recovery code stays unconsumed
- `tsc --noEmit` + `pytest backend/tests` green

---

## Phase 5 — Hygiene (ship anytime, ~30 min)

### P5.1 — Delete dead code (L1)
- `backend/app/api/v1/endpoints/auth/passkey.py` (not mounted; duplicate of `passkeys.py`)
- `backend/app/api/v1/endpoints/mfa/` directory (not mounted; duplicate of `mfa.py`)

### P5.2 — Generic login audit reasons (L2)
**File:** `backend/app/api/v1/endpoints/auth/login.py:118-125`

Change `failed_login_user_not_found` and `failed_login_wrong_password` to the unified `failed_login` with the granular reason stored only in a separate analytics table that requires elevated read perms.

### P5.3 — Absolute session lifetime cap (L3)
**File:** `backend/app/core/security/sessions.py:69`

Add `absolute_expires_at = created_at + 12h` next to the sliding `last_active_at`. If `now() > absolute_expires_at` → reject regardless of activity. 12h covers a workday; refresh token (7d) still lets users log back in without entering creds, gated by talky_rt.

---

## Critical files (full list)

### Modified (frontend)
- `Talk-Leee/src/lib/http-client.ts`
- `Talk-Leee/src/lib/auth-context.tsx`
- `Talk-Leee/src/components/layout/dashboard-layout.tsx`

### Modified (backend)
- `backend/app/api/v1/endpoints/auth/registration.py`
- `backend/app/api/v1/endpoints/auth/password_reset.py`
- `backend/app/api/v1/endpoints/auth/password.py`
- `backend/app/api/v1/endpoints/auth/login.py`
- `backend/app/api/v1/endpoints/mfa.py`
- `backend/app/api/v1/endpoints/passkeys.py`
- `backend/app/core/security/passkeys.py`
- `backend/app/core/security/cookies.py`
- `backend/app/core/security/refresh_tokens.py`
- `backend/app/core/security/sessions.py`

### New (backend)
- `backend/Alembic/versions/0007_mfa_challenge_attempts.py` (Phase 2)
- A handful of unit tests as listed in each phase's Verification section

### Deleted (Phase 5)
- `backend/app/api/v1/endpoints/auth/passkey.py`
- `backend/app/api/v1/endpoints/mfa/` (whole directory)

---

## Verification matrix (cross-phase, run before each ship)

| Scenario | Phase covered | Expected |
|---|---|---|
| Cold incognito → login → /dashboard | P1 | Lands cleanly, no bounce, 5/5 runs |
| Cold incognito → register → API call | P2.2 | Register returns no token; API call 401 |
| 6 wrong TOTP codes against one challenge | P2.1 | 6th call 401, challenge invalidated |
| Force `sign_count` regression in DB | P2.3 | Next passkey auth fails, credential disabled |
| Spam `/forgot-password` for one email | P3.1 | 4th call within hour → 429 |
| Change password while logged in on 2 devices | P3.2 | Device 1 stays in, Device 2 next request → 401 |
| Call `/mfa/setup` when MFA already enabled | P3.3 | 409 with "Disable existing MFA first" |
| Enroll passkey as `tenant_admin` with unknown AAGUID | P3.4 | 400 with "Authenticator not in approved list" |
| `/passkeys/login/begin` for non-existent email | P3.5 | Response shape identical to existing-user case |
| Steal expired refresh token + replay | P4.1 | Reuse detected, family revoked |
| Idle session for 13h | P5.3 | Next request → 401 even if active |

---

## What this plan deliberately is NOT

- **Not a complete cookie-only migration.** The dual Bearer + cookie path stays for now; eliminating localStorage is a separate larger refactor.
- **Not a refactor of the http-client.** Just adds grace-window helpers and tightens the catch paths.
- **Not new sign-in methods.** Magic links, SAML, OIDC are explicitly out of scope.
- **No CAPTCHA on registration.** Considered but rate limits + email verification gate (Phase 2.2) are sufficient for now.
- **No SMS-based 2FA.** TOTP + recovery codes + passkeys cover the auth-factor matrix already.
