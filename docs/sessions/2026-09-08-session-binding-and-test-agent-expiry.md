# 2026-09-08 — "Your session has expired" on the Test agent, one login behaving like several sessions

Owner report: "Test agent is not accessible sometimes — Your session has expired. Reload the
page and sign in again. Why is there a different session for each page?" Also "calls related
issues are not live".

## What the evidence said (prod, read-only)

| Fact | Evidence |
|---|---|
| The exact message comes from **one** place: the Test-agent WebSocket | `campaign_test_ws.py` sends it on three paths: no token, invalid JWT, and `_check_login_session` failing |
| On Sep 7 the socket found the cookie 14 times and started a session **2** times | journal: 14× `campaign_test_ws auth surface=cookie`, 2× `campaign_test_ws start`; 0× `token verification failed`; the client's refresh-and-retry produced pairs one second apart |
| The failing path had **no log line** | `_check_login_session` → `False` → error frame → close, nothing written |
| REST kept working the whole time | `/auth/me` 424×200, polls 401 only once per 15 min, each followed by `/auth/refresh 204` and a successful retry |
| **Root cause:** `POST /auth/refresh` minted the new access JWT **without `sid`** | `refresh.py` called `encode_access_token(...)` with no `session_id`; the refresh-token row had no way to know its login session (no column) |
| Why REST did not care | `get_current_user` accepts the `talky_at` JWT on its own (no session check on that path); `campaign_test_ws` additionally requires `payload["sid"]` **and** a live `security_sessions` row |
| Second lifetime mismatch | `security_sessions.expires_at = created_at + 24 h`, never extended; refresh tokens live 7 days. So even a token that kept its `sid` would fail the socket after 24 h while pages still worked |
| Not the cause | Refresh-token reuse race: 0 `refresh.reuse_detected` in 72 h. Fingerprint flapping: fixed Sep 7 (0 warnings). The 65 `/auth/refresh 401` are "No refresh token" (logged-out visitors) |

So: within 15 minutes of login every page and the Test agent share one session. At the first
refresh the access token silently loses its session binding; pages keep working, the Test
agent (and anything else that insists on a session-bound token) says the session expired.
That is the "different sessions per page".

## Changes

| Change | Where |
|---|---|
| **Migration 0045**: `refresh_tokens.session_id` + index; **backfill** binds every existing family to the `security_sessions` row created by the same login (nearest session of that user within 60 s of the family's first token). Families with no match stay NULL and behave as before. | `Alembic/versions/0045_refresh_token_session_binding.py` |
| Login/signup/MFA/passkey pass the new session into the refresh family; rotation carries it forward and returns it in the claims | `refresh_tokens.py`, `auth/_shared.py` |
| `/auth/refresh` re-mints the JWT **with `sid`**, slides `last_active_at` and extends `expires_at` (sliding 24 h while the refresh family is used). If the login session is revoked or expired the refresh **fails** (401, cookies cleared, family revoked) — "logout everywhere" now ends refresh too instead of REST out-living it | `auth/refresh.py::bind_refresh_to_session` |
| Test-agent socket logs **why** it refused (`no sid` vs `session revoked/expired`) | `campaign_test_ws.py::_check_login_session` |

No frontend change: the Test-agent button already refreshes and retries on `auth_required`;
after this deploy the retried token carries `sid` and the socket starts.

## "Calls related issues are not live"

Checked two readings:

- **Frontend deployment.** Vercel serves the latest `main`: the live bundle contains both
  yesterday's Call History popover string and today's inbound "Create a new AI campaign" link.
- **Call Issues panel.** It is live data, polled from `dialer_jobs`: unresolved dial failures
  in the last **60 minutes**, excluding self-clearing pacing deferrals. For the owner's tenant
  the last 24 h held 4 job rows total; exactly 1 qualified in the last hour. The panel was
  empty because there was nothing to show, not because it is disconnected. Call-level
  failures (`calls.status='failed'`, 2 in 7 days, `failure_reason` NULL) are **not** part of
  that feed — if that is what "not live from the logs" means, it is a feature to add, not a
  bug here.

## Follow-up (same day, owner: "fix that issue as well") — the session cookie WAS reaching the API

Evidence: prod has 5 live `security_sessions`. The exact join `validate_session` runs
(`security_sessions JOIN user_profiles`) returns **0 rows without `app.bypass_rls`** and 5 with
it — `user_profiles` is under forced RLS since 0038. `SessionSecurityMiddleware` validated the
cookie on a raw `pool.acquire()` (no bypass, no tenant), so every cookie was "invalid", the
middleware deleted it on the first request after login, and nothing was logged. Same defect
class as the assistant socket this morning.

Changes:
- Middleware validates on `acquire_with_tenant(pool, None)` (the bypass path REST's own cookie
  resolution already used) and logs a rejected cookie (`session cookie rejected …`).
- `SESSION_IDLE_TIMEOUT_MINUTES` is now env-overridable (default 30). The idle rule is **live
  for the first time** on REST: 30 minutes without any request (an open tab's polling counts as
  activity) revokes the session; the next refresh then ends the login (0045 makes refresh honour
  revocation). Set the env var if 30 is too short.
- Deploy step 7b resets `last_active_at = now()` on live sessions so nobody is idle-revoked by
  the first request after the deploy.
- Tests: `test_session_middleware_rls_bypass.py` (3).

Not changed: IP/fingerprint binding stays in non-strict mode (`SESSION_STRICT_BINDING=False`):
a mismatch marks the session suspicious and logs, it does not revoke.

## Call History clock (owner request)

The time column is now a **clock icon only**, centred right before the AI summary; date, time,
duration and timezone show on hover/focus, not in the row. `components/calls/call-timestamp.tsx`
(+ test). Grid column shrunk to icon width.

## Beyond brief (found, not changed)

- ~~**The server-side session cookie never reaches the API.**~~ Root-caused and fixed above.


- `refresh_tokens` re-issues the successor with the **client-supplied** IP/UA only; fine.

## Verification

```text
targeted: test_refresh_rotation + test_refresh_session_binding + test_campaign_test_ws
          + test_csrf_middleware + test_endpoint_auth_audit → 50 passed, 1 skipped
          tests/unit -k "alembic or migration or revision" → 149 passed
ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!
```

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8898 passed, 8 skipped in 304.80s (0:05:04)
```

```text
after the middleware fix + clock change:
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q → 8901 passed, 8 skipped in 666.83s
ruff → All checks passed! · Talk-Leee typecheck 0 · lint 0 · tests 467, pass 465, fail 0, skipped 2 · build exit=0
```
