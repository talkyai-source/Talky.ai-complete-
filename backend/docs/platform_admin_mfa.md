# Platform-admin MFA and Admin-origin activation

`PLATFORM_ADMIN_MFA_REQUIRED=false` is the default. It preserves existing sessions
while owners enrol administrators and rehearse the full login flow. Setting it
to `true` requires the **current database role** `platform_admin` to have strict
`security_sessions.mfa_verified = TRUE` on the active session for that user.
Enrolment (`mfa_enabled`), a JWT claim, a client-side flag or a truthy string is
not sufficient evidence.

The shared decision applies at current-user HTTP authentication, the existing
session-principal boundary used by ongoing user activities, and the user-bearer
fallback on internal-shaped routes. Legacy cookies reload their exact active
session because middleware lookup hints contain no MFA evidence. A rejected
HTTP user receives 403 `platform_admin_mfa_required` before privilege scope is
published. Storage outages remain 503, revoked sessions remain 401. Authenticated
internal service-token identities retain their existing server-only behavior;
an arbitrary internal-token header cannot choose that path.

The successful `/auth/mfa/verify` ceremony already writes `mfa_verified=TRUE`
when TOTP or a single-use recovery code succeeds. That write and session creation
remain atomic. This change does not modify factor verification, keys, recovery
codes, session creation or token claims.

## Owner rollout

1. Leave the flag false. Ensure the existing protected TOTP encryption key and
   recovery procedure are configured. Enrol every platform administrator and
   escrow recovery codes privately.
2. Each administrator must perform a **new password+TOTP/recovery-code login**
   and confirm Admin access. Enrolment alone does not mark an already-open
   session verified. Current passkey login does not stamp the MFA session flag;
   passkey-only sessions therefore do not satisfy this rollout. Do not infer
   second-factor evidence merely from owning a passkey.
3. Rehearse with at least two authorised administrators and an owner recovery
   procedure. Enabling the flag also blocks unverified admin sessions from
   authenticated MFA setup/account/logout routes; login and MFA challenge
   verification remain available. Complete enrolment before activation and use
   a fresh full login for existing sessions. No path-based privileged bypass
   or client-controlled exemption is added.
4. Set `PLATFORM_ADMIN_MFA_REQUIRED=true` in the approved backend process
   configuration and restart the applicable API/worker processes through the
   normal coordinated deployment. Verify a password-only/old admin session is
   refused, a new completed MFA login succeeds, nonplatform users retain normal
   access, and promotion of an ordinary session to platform-admin requires MFA.
5. Verify the real Admin UI, refresh, session revocation, current-user and
   protected Admin actions. Backend synthetic tests do not establish production
   browser readiness or recoverability.

If activation locks out administrators, an authorised operator may restore the
flag to false through the same protected configuration/restart procedure,
investigate and re-enrol before a second attempt. That rollback relaxes the new
policy; it is not a substitute for completing the production MFA rollout.

## Documented production CORS origin

The backend's approved `CORS_ORIGINS` must include the exact origin:

```text
https://admin.talkleeai.com
```

Append it to the complete existing comma-separated list or JSON array. Preserve
the customer site, contact form and every other currently approved origin; do
not replace the production list with a one-origin example. Do not use a wildcard
for credentialed Admin requests. This documentation does not change live CORS.

Owner verification after applying configuration:

```bash
curl -i -X OPTIONS https://api.talkleeai.com/api/v1/admin/dashboard/stats \
  -H 'Origin: https://admin.talkleeai.com' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

Use a real mounted Admin route if the deployed path differs. Expect the exact
Admin `Access-Control-Allow-Origin`, permitted credentials and the required
headers, then verify an unrelated origin is refused. Preflight success is not
authentication or permission proof.

## DNS owner steps

The 5 Nov QA report records missing DNS/CORS; no new DNS query or production
activation was performed for this change. In the **existing Admin Vercel
project**, obtain its current domain records, then add them at the authoritative
GoDaddy zone for `talkleeai.com`:

| Type | Record name | Value |
| --- | --- | --- |
| CNAME | `admin` | Exact current target shown for `admin.talkleeai.com` by that Admin project |
| TXT | `_vercel.admin` (or the exact verification name shown there) | Exact ownership-verification value shown by the same project |

The repository's release notes do not contain the record values. Do not invent
a shared Vercel CNAME target or reuse another project's verification value.
These two current values are owner-supplied inputs. Preserve unrelated records;
resolve an existing conflicting `admin` record through the DNS owner's change
procedure. Wait for authoritative resolution and Vercel domain verification,
then verify the certificate, SPA deep links, configured API origin and actual
MFA-protected Admin workflows. DNS/hosting activation and Phase 3 Admin acceptance
remain owner work; no deployment or outbound message was performed here.
