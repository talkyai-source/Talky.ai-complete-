# JWT signing key rotation — runbook

Cadence: **quarterly** by default, **immediately** on any suspected leak.

The verifier supports two-key graceful rotation via `JWT_SECRET` (current,
signs + verifies) and `JWT_SECRET_PREVIOUS` (verifies only). Tokens
signed under the previous key keep working until they expire naturally:
15 minutes for access tokens, 7 days for refresh tokens. Across both
windows, the maximum exposure window from any rotation is the refresh
TTL.

## Quarterly rotation procedure

```bash
# 1. Generate a new high-entropy secret (32+ bytes).
NEW_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')

# 2. SSH to the API host (Hetzner) and edit /etc/talky-api.env:
#       JWT_SECRET_PREVIOUS=<value previously in JWT_SECRET>
#       JWT_SECRET=<NEW_SECRET>
#    Order matters — write PREVIOUS first so a partial deploy can never
#    invalidate active sessions.

# 3. Restart the api service.
sudo systemctl restart talky-api

# 4. Confirm health.
curl -sS https://api.talkleeai.com/health | jq

# 5. Smoke-test verification both ways.
#    (a) New login: gets a token signed with NEW_SECRET, /auth/me works.
#    (b) Old session: existing browser tabs (signed with the PREVIOUS
#        key) keep working until /auth/refresh fires — at which point
#        the rotated refresh issues a new access JWT signed with NEW.

# 6. Wait the full rotation window before removing the previous secret.
#    Access tokens age out in 15 minutes; refresh tokens in 7 days.
#    Wait 7 days + 1 hour safety margin before step 7.

# 7. Remove JWT_SECRET_PREVIOUS from /etc/talky-api.env and restart.
#    From this point any token signed with the old secret is rejected.
```

## Emergency rotation (suspected leak)

The procedure is the same EXCEPT: do not wait 7 days. Set
`JWT_SECRET_PREVIOUS` to a sentinel-or-empty value and accept that
every active session is forcibly logged out as their next request hits
the new secret. The trade-off is one user-visible session outage vs.
extending the attacker's window.

For the most-severe leaks (operator credentials compromised, key in
git history, etc.), pair the rotation with:

  - Revoke all rows in `refresh_tokens` (`UPDATE refresh_tokens SET revoked_at = NOW() WHERE revoked_at IS NULL`).
  - Reset all `talky_at` / `talky_rt` cookies on the next request from
    each browser via the existing session-expired latch.
  - Alert tenant_admins via email so they re-verify nothing critical
    happened in the suspected leak window.

## Why two keys, not three+

PyJWT decodes with one key at a time. A two-key window covers every
in-flight token (max age 7 days) and keeps the code simple. Three-key
or more would let the previous-previous key float around for nothing —
once a token's expired, the key that signed it has no value.

## What this DOES NOT do

- Rotate refresh tokens themselves. Those rotate per-use via the
  existing reuse-detection family path (`auth/refresh.py`). The signing
  key rotation only changes *what* signs the next JWT, not the
  refresh-token identity.
- Re-encrypt at-rest data. JWT_SECRET is for signing, not for
  encrypting stored secrets. The MFA TOTP secrets, the SIP-trunk creds,
  etc. use a separate Fernet key (`MASTER_KEY`) with its own rotation
  policy.
- Invalidate Supabase service-role tokens or third-party API keys.
  Those are out of scope; rotate them on their own cadences.

## Verification checklist (after a rotation)

- [ ] `journalctl -u talky-api --since '10 minutes ago' | grep -i "Invalid authentication token"` shows zero spikes
- [ ] `/auth/refresh` 5xx rate is unchanged in Grafana
- [ ] One known account: log out → log back in → /dashboard renders
- [ ] One known long-lived session (open before rotation): /dashboard
  still renders without a re-login prompt (cookies still valid against
  PREVIOUS secret)
- [ ] After the 7-day wait: removing `JWT_SECRET_PREVIOUS` from env
  does not regress any of the above
