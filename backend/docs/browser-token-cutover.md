# Browser token cutover (QA F4-08)

`AUTH_SHORT_LIVED_TOKENS_REQUIRED=false` is the compatibility default. With it
enabled, the central signer caps every access JWT at 15 minutes, including the
login/signup/MFA/passkey JSON body, explicit longer TTLs and refresh responses.
Shorter tokens, including the two-minute assistant handshake token, stay shorter.
Validation rejects previously issued longer-lived JWTs and does not extend their
expiration with `JWT_LEEWAY_SECONDS`. Existing HttpOnly access cookies already
last 15 minutes; opaque rotating refresh cookies remain HttpOnly for seven days.

The web application currently uses `talklee.auth.token` in localStorage when its
Bearer fallback is enabled (`Talk-Leee/src/lib/auth-token.ts`). Its HTTP client
already retries after rotating the HttpOnly refresh cookie. Admin stores
`admin_token` (`Admin/frontend/src/lib/api.ts`), and legacy passkey helpers also
use `access_token`. Neither app receives the opaque refresh token in JSON.

Both transports resolve the server-side session and current principal on every
HTTP authentication (`backend/app/api/v1/dependencies.py`); a revoked session is
rejected immediately, even with a fresh signed JWT. Refresh also refuses a
revoked session. Shortening a JWT is not a substitute for those checks. Existing
long-lived WebSocket connections require Claude's separate connection-lifetime
and revocation work; this backend cutover does not prove their termination.

Owner rollout: verify cookie refresh, sign-in, MFA and passkeys for both apps in
staging, then set `AUTH_SHORT_LIVED_TOKENS_REQUIRED=true` in the owner-managed
service configuration and restart the API using the deployment runbook. Existing
long-lived body JWTs will be rejected immediately and must refresh or sign in.
Keep accurate clocks. The default remains false until this rollout is approved;
no production configuration was changed by this fix.

For Hisham: turn off the web Bearer storage fallback after cookie-only auth is
verified, remove Admin's `admin_token` and legacy passkey `access_token` writes,
and keep refresh single-flight/identity-fenced. Never expose `talky_rt` to page
scripts. Show a session-expired sign-in prompt if refresh is rejected; retain the
current session when password confirmation returns a validation error (F1-28).

Verification uses real signed synthetic JWTs and a mounted current-user route
with fake session storage, plus existing frontend auth tests. It does not send
provider traffic or read real environment files.
