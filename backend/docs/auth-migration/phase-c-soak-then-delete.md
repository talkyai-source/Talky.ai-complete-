# Phase C — soak-then-delete cleanup

## Why this is two steps

Phase A (backend cookie issuance + refresh) and Phase B (frontend
cookie-only auth, refresh-on-401) ship side-by-side with the legacy
Bearer / `talky_sid` / `talklee_auth_token` paths. Active sessions
keep working unchanged because every successful auth path issues BOTH
the new cookies AND the old Bearer body.

The deletions below are only safe once every active user has rotated
onto the new cookies. The legacy session lifetime is 24 h, so a 36-h
soak after the Phase A/B deploy is the conservative window.

## What already shipped in Phase C (safe immediately)

- [x] Deleted shadow file `backend/app/api/v1/endpoints/auth.py`
      (the live code is in the `auth/` package; the shadow was dead).
- [x] Removed `FRESH_LOGIN_GRACE_MS` from `Talk-Leee/src/lib/http-client.ts`.
      Refresh-on-401 makes it obsolete. `markFreshLogin()` is now a
      no-op kept only so existing call sites compile.
- [x] Added deprecation banner to `Talk-Leee/src/lib/auth-token.ts`
      pointing future readers at this checklist.

## What to delete after the soak window

Order matters — frontend first (so backend can keep accepting legacy
auth headers until the frontend stops sending them), then backend.

### Frontend

- [ ] Drop `api.setToken(res.access_token)` calls from
      `Talk-Leee/src/lib/auth-context.tsx` (`login`, `register`,
      `setToken`). Cookies carry the token now; the body JWT is
      ignored.
- [ ] Delete `Talk-Leee/src/lib/auth-token.ts` entirely.
- [ ] Remove all `authTokenCookieName`/`getBrowserAuthToken`/
      `setBrowserAuthToken` imports and call sites. `grep -r` for
      each — current consumers:
      - `src/middleware.ts`
      - `src/lib/http-client.ts` (the `defaultTokenStorage` fallback)
      - `src/lib/backend-api.ts`
      - `src/lib/api.ts`
      - `src/lib/server-auth.ts`
      - `src/components/home/navbar.tsx`
      - `src/components/assistant/floating-assistant.tsx`
      - `src/components/ui/voice-agent-popup.tsx`
      - `src/app/settings/page.tsx`
- [ ] Drop `markFreshLogin` everywhere — `http-client.ts` export
      plus `src/app/auth/login/login-client.tsx` import + call site.
- [ ] Delete `src/server/auth-core.ts` and its 5 server-side
      callers (`mfa.ts`, `passkeys.ts`, `session-security.ts`,
      `api-security.ts`, plus the proxy route at
      `src/app/api/v1/[...path]/route.ts`). Replace each with a
      direct call to the backend's `/auth/me` for identity, or just
      pass the cookies through.
- [ ] Drop the parallel `sessions` DB table once the proxy no longer
      reads it (migration to remove).

### Backend

- [ ] Stop returning `access_token` in the JSON body from
      `auth/login.py`, `auth/registration.py`, `auth/signup.py`.
      The cookies are the only credential.
- [ ] Drop the `talky_sid` fallback in
      `app/api/v1/dependencies.py:get_current_user`. The function
      will then read **only** `talky_at` cookie (primary) and
      `Authorization: Bearer` (kept for mobile/CLI/server-to-server
      forever).
- [ ] Drop the `_AUTH_FLOWS` entry for `POST /api/v1/auth/refresh`
      from `tests/unit/test_endpoint_auth_audit.py` only if the
      endpoint is moved behind auth (it shouldn't be — it consumes
      the refresh cookie as its credential).
- [ ] Migration to drop the legacy `sessions` table after the proxy
      auth is gone.

### Verification at each step

After each frontend deletion, run:

```
cd Talk-Leee
node ./node_modules/typescript/bin/tsc --noEmit
npm test
```

After each backend deletion, run:

```
cd backend
source venv/bin/activate
python -m pytest tests/unit -q
```

End-to-end sanity in production after the full cleanup:

1. Hard-refresh `/auth/login`, sign in. DevTools → Application →
   Cookies should show **only** `talky_at` + `talky_rt`. No
   `talky_sid`, no `talklee_auth_token`.
2. Network tab on login response: no `access_token` field in the
   JSON body.
3. Wait 16 min, navigate to any data page — silent
   `POST /auth/refresh` returns 204, original request retries 200.
4. Force a refresh-token replay via curl — backend returns 401 AND
   the live browser session is invalidated on next request (family
   revoked).
