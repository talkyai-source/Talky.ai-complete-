# Plan: Auth-hardening follow-up — close the loopholes the review found

## Context

The universal-auth-state plan finished on 2026-05-20. A code review against
the now-stable system identified 15 residual issues, three of them critical
(all in the WebSocket auth surface). The fixes are independent of the
ua-plan and can ship phase-by-phase without further structural work.

## Severity-ordered phase list

| # | Phase | Severity | Sequence |
|---|-------|----------|---------|
| A | WebSocket hardening — token-in-message, Origin check, unified verification | Critical | now |
| B | Consolidate `backend-api.ts` onto the single `api.ts` HttpClient | High | after A |
| C | JWT signing key rotation (JWT_SECRET + JWT_SECRET_PREVIOUS) | High | after B |
| D | Per-account login lockout on top of per-IP rate limit | Medium | after C |
| E | Logout reliability + tighten fresh-login grace window | Medium | after D |
| F | Opt-in flag for the localStorage Bearer fallback | High (but disruptive) | last |
| G | Hygiene sweep — vestigial types, legacy reader, CSRF coverage test, logout-all rate-limit, dev-token gate | Low | parallel with G |

## Phase A — WebSocket hardening (THIS PHASE)

### Issues addressed
1. **JWT in WS URL query string** — leaks to logs / Sentry / proxies.
2. **No Origin header check** — cross-origin WS hijacking.
3. **Different verification path** — uses `db_client.auth.get_user(token)` (Supabase) instead of the same `decode_and_validate_token` REST endpoints use; the two can drift.

### Critical files
- `backend/app/api/v1/endpoints/assistant_ws.py` — rewrite the auth flow
- `backend/app/core/config.py` — `allowed_origins` already exists, reuse it
- `backend/app/api/v1/dependencies.py` — already exports `decode_and_validate_token`
- `Talk-Leee/src/components/assistant/floating-assistant.tsx` — stop putting JWT in URL, send as first message instead

### Design

**Backend (assistant_ws.py):**
1. Before `manager.connect()`, check `websocket.headers.get("origin")` against `settings.allowed_origins`. If missing or not allowed, close with code 1008 (policy violation).
2. Accept the WS, then wait for the first message with a 5-second timeout. Expect `{"type":"auth","token":"<JWT>"}`.
3. Verify the token via `decode_and_validate_token(token)` — the same function REST endpoints use. Extract `sub` (user_id), look up tenant from `user_profiles`.
4. If anything fails (timeout, wrong message type, invalid token), send `{"type":"error",...}` and close.
5. Drop the `?token=` query parameter from the endpoint signature. Keep `?conversation_id=` for resumption.

**Frontend (floating-assistant.tsx):**
1. Build `wsUrl` without the token query param. URL becomes `wss://…/assistant/chat?conversation_id=…`.
2. After `ws.onopen` fires, send `{"type":"auth","token":<accessToken>}` as the first frame.
3. Wait for the existing `{"type":"connected",…}` response — that's now the post-auth ack.
4. If the server closes with code 1008, treat as auth failure and drop the reconnect loop (show "Session expired, please sign in again" instead of backoff).

### Verification
- Open Network → Frames tab. The WS URL no longer contains a JWT.
- Curl backend logs (`journalctl -u talky-api`) for any line containing `eyJ` — must return zero matches for the WS path.
- Open Ask-AI from `https://evil.example` (or use `wscat -o https://evil.example wss://api.talkleeai.com/api/v1/assistant/chat?conversation_id=…`) — must close with 1008.
- Login normally → click Ask-AI → conversation works (no regression).
- Send a malformed first message (`{"type":"hello"}` instead of `auth`) — server must close, client must not enter backoff.

### Ship
One commit on backend + frontend together. Deploy backend first (it tolerates both old `?token=` URL flow AND new auth-message flow for one release — see fallback below), then frontend. Cut the URL-token fallback in the next phase commit.

**Backwards-compat fallback during the deploy window:** if the WS connects WITHOUT an immediate auth message but WITH a `?token=` query, run the new `decode_and_validate_token` path on it. Log a warning. After 24h soak, delete the fallback. This avoids breaking in-flight WS connections during the rolling deploy.

## Phase B — Consolidate to single HttpClient

`Talk-Leee/src/lib/backend-api.ts` creates its own `createHttpClient` instance. Single-flight refresh dedup is per-instance, so simultaneous 401s from `api.ts` and `backend-api.ts` each fire `/auth/refresh`. The second response can clobber the first's token.

**Fix:** export `apiClient` (the underlying HttpClient instance) from `lib/api.ts`. Make `backend-api.ts` import it instead of constructing its own. The structural test guard against bare-fetch already lets `backend-api.ts` through, but the second-instance issue is invisible to the guard.

Adds the structural-test invariant: "exactly one createHttpClient call in src/".

## Phase C — JWT key rotation

`backend/app/core/config.py` reads one `JWT_SECRET`. If it leaks, every issued token is forgeable forever and the only remedy is global session invalidation.

**Fix:** add `JWT_SECRET_PREVIOUS`. The verifier tries current first, then previous. The signer only uses current. Document a quarterly rotation runbook in `backend/docs/security/`.

Edge: tokens signed under the old key keep working until they expire (15min for access, 7d for refresh). Rotation cadence picks the maximum acceptable exposure window.

## Phase D — Per-account login lockout

`@limiter.limit("10/minute")` on `/auth/login` keys by IP. A distributed brute-force across a botnet sees 10 attempts × N IPs per minute per account.

**Fix:** add a counter keyed by `(email, time-window)` in Redis (or the existing rate-limit store). Lock the account after 10 failed attempts in 15 minutes. Unlock on successful login OR after 1 hour. Surface in the email-verification flow ("Your account is locked due to too many failed login attempts; check your email for a recovery link") with a recovery link signed JWT (5-minute TTL).

## Phase E — Logout reliability + grace tightening

**Logout:** `AuthContext.logout` catches and swallows backend errors. Add: on backend failure, queue a `pendingLogout` flag in localStorage. The next page load (or any subsequent /auth/me) retries the logout call. On success clear the flag.

**Grace window:** currently 15s by wall-clock from any login. Tighten to "only inside 15s of the most recent `applyLoginResult` call". Prevents an attacker from forcing a 401 to ride out the window.

## Phase F — Bearer fallback opt-in flag

Right now `lib/auth-token.ts` always writes the JWT to localStorage. The Bearer fallback is kept for environments that can't carry cross-origin cookies (admin frontend, future native shell). But in the talkleeai.com browser everything works via HttpOnly cookies — the localStorage write is pure XSS attack surface.

**Fix:** gate the localStorage write behind `NEXT_PUBLIC_BEARER_FALLBACK=true`. Default off. Vercel project for talkleeai.com sets it off (cookies work fine). Vercel project for the admin frontend / native shell sets it on.

AuthContext's bootstrap reader still consults localStorage (one-shot, on mount) so a session migrating across the flag-flip doesn't get logged out.

## Phase G — Hygiene sweep

Combined into one commit:
- Delete vestigial `refresh_token: string` from `webauthn-utils.ts:202,215` + `passkey-login.tsx:15` (write-only after Phase 7).
- Delete `cookieName()` and the legacy cookie reader in `backend/app/api/v1/endpoints/server/auth-core.ts` (legacy reader Phase 7 left in place — past its 2026-06-03 deadline → defer this entry until then).
- Add a CSRF coverage test: walk every POST/PUT/PATCH/DELETE route in `backend/app/api/v1/`, fail if any lacks the CSRF dependency.
- Rate-limit `/auth/logout-all-other-sessions` (10/hour/account).
- Gate dev-token in `home/navbar.tsx` behind `NEXT_PUBLIC_ENABLE_DEV_AUTH=1` instead of `NODE_ENV==="development"`.

## Out of scope
- WebAuthn attestation tightening for non-admins. (Trade-off: friction vs. security; user decision needed.)
- Refresh-token TTL reduction from 7d to 72h. (Behavior change requiring product decision.)
- Per-tenant SSO (SAML / OIDC). Separate initiative.
