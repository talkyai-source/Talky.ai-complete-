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

## Beyond brief (found, not changed)

- **The server-side session cookie never reaches the API.** 0 session-middleware validations
  in 72 h and `last_active_at == created_at` on every session since Sep 1. The `talky_sid`
  cookie is set host-only (no `domain=`) while `talky_at`/`talky_rt` use `AUTH_COOKIE_DOMAIN`;
  whatever the exact reason, the 30-minute idle timeout, IP/fingerprint binding and the
  "suspicious session" handling are all inert for REST today (same shape as RLS-was-decorative).
  Fixing it would start enforcing the 30-minute idle logout — a product decision first.
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
