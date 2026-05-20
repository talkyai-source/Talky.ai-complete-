# Bearer fallback opt-out (NEXT_PUBLIC_BEARER_FALLBACK)

## What it controls

`Talk-Leee/src/lib/auth-token.ts` writes the JWT to
`localStorage["talklee.auth.token"]` on every successful login. The
shared HTTP client reads it via the AuthContext-backed token provider
and sends it as `Authorization: Bearer <jwt>` on every API request.

This Bearer fallback exists for environments that can't carry the
HttpOnly `talky_at` cookie cross-origin:
  - The admin frontend at `Admin/frontend/`
  - Future native shell wrappers (Electron, Capacitor)
  - Some embedded webviews

For the main `talkleeai.com` browser, cross-origin cookies work fine
(Vercel → Hetzner with `SameSite=None; Secure`), so the Bearer is
**duplicate** — every request carries the cookie AND the header,
backend reads cookie first. The localStorage write exists solely as
a fallback.

The fallback is also pure XSS attack surface: any script on the
origin can read `localStorage["talklee.auth.token"]` and exfiltrate
the JWT. HttpOnly cookies are not reachable from JS — that's the
whole point. The Bearer fallback erases that protection.

## How to opt out

In the Vercel project for `talkleeai.com`, set:

```
NEXT_PUBLIC_BEARER_FALLBACK=false
```

(Literally the string "false". Any other value, including "FALSE" /
"0" / unset, keeps the fallback ON.)

After deploy:
  - `setBrowserAuthToken(token)` becomes a write-skip on the
    canonical key. `setBrowserAuthToken(null)` (logout) still clears
    storage — leaving a stale value would defeat the security intent.
  - `getBrowserAuthToken()` still reads, so existing sessions don't
    forcibly re-login on the deploy.
  - On the next login, the JWT is held only in AuthContext's
    `accessToken` React state — gone on page reload, gone on tab
    close. The user is re-authenticated by the cookie path
    automatically on the next /auth/me call.

## What breaks if you opt out today

**Ask-AI WebSocket auth.** Phase A (commit 1660253) made the WS auth
work by sending the JWT as a first frame after `onopen`. The client
reads the JWT from `accessToken` to build the frame. With the Bearer
fallback OFF, `accessToken` is null on cold load (no localStorage to
hydrate from), so the WS auth frame contains an empty token and the
backend closes with code 1008.

**Mitigation today (interim):** leave `NEXT_PUBLIC_BEARER_FALLBACK`
unset (default ON) on the talkleeai.com Vercel project.

**Real fix (Phase F2 follow-up):** change the assistant_ws.py auth to
read the `talky_at` HttpOnly cookie directly from `websocket.cookies`
instead of expecting a JWT in the first frame. The browser sends
cookies on the WS handshake automatically; no client-side token
needed. Once that's deployed, this Bearer fallback can be turned off
without breaking Ask-AI.

## What stays the same regardless of the flag

  - HttpOnly `talky_at` and `talky_rt` cookies — backend-issued, browser
    sends automatically on every same-domain or SameSite=None+credentials
    request. The canonical session.
  - Refresh-token rotation, family revocation, session-expired latch.
    All cookie-driven.
  - The admin frontend's Bearer flow. Admin/frontend has its own
    Vercel project; leave its `NEXT_PUBLIC_BEARER_FALLBACK` unset (ON)
    until it's migrated to cookies too.
