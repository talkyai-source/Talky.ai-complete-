# PBX extension integration — 2026-09-20

## Scope and approved assignment

Provision sequentially, prove registration, then hand back for user-led calls. No calls are authorized for this setup phase. Preserve existing working connections and do not claim registration proves AI call routing.

| Extension | Approved account | Status |
| --- | --- | --- |
| 940003 | AllStateEstimation | Created and read back; inactive, not registered |
| 940004 | AllStateEstimation.co | Not created; authenticated account access needed |
| 940005 | AllState COnstructions.us | Not created; authenticated account access needed |
| 940006 | Blaze DigiTel | Not created; authenticated account access needed |
| 940007 | User's manual testing phone | Reserved in this plan only; not registered by Talky |

## Evidence and implementation constraints

- The existing AllStateEstimation PBX entries use `sip3.blazedigitel.com:5060`, UDP. The new provider message omitted its registrar. This existing address is a staging assumption, not independent confirmation for the new credentials.
- AllStateEstimation authentication succeeded through the live `/api/v1/auth/login` endpoint; `/api/v1/telephony/sip/trunks` returned HTTP 200. The authenticated role is tenant_admin, not a cross-account provisioning role.
- A read-only production query found no entries for 940003–940007 before this setup.
- Production `tenant_sip_trunks.is_active` defaults to false. Creation and activation are separate operations.
- Existing AllStateEstimation entries 940001 and 940002 are inactive. Its `blaze-primary` connection reports registered and runtime-ready through the API.
- Live `backend/app/domain/services/telephony/trunk_resolver.py` selects the most recently updated active, runtime-ready own trunk ahead of the shared platform connection. Activating a test connection is therefore not isolated from the account's outbound routing.
- Live `/etc/asterisk/extensions.d/talky-inbound.conf` contains an exact mapping for 150001 to +442046132300, followed by a catch-all passing the extension into inbound admission. No explicit 940003–940007 mapping exists in that file. This is not a successful call test, and other included contexts were not exhaustively inspected.
- The same file documents that generated account routes require a same-tenant inbound DID assignment and a verified number. Do not invent a public DID from a six-digit extension or route different tenants onto the existing DID.
- The environment file specifies production Asterisk mode and automatic PJSIP reload. This does not prove that activation or provider registration succeeds.

## Execution board

1. Save 940003 using its authenticated tenant API and an idempotency key; keep it inactive. Done when a fresh GET shows the expected tenant, extension and inactive state.
2. Obtain legitimate authenticated access to the other three approved accounts; never mint another user's token, reuse an unrelated password or bypass the API with direct database writes.
3. Confirm the new registrar and provision isolated inbound extension routing plus an intentional outbound selection before activation. Preserve existing DID and campaign routes.
4. Activate one extension at a time and require fresh Asterisk-derived Registered evidence. A saved row, activation HTTP 200 or an OPTIONS probe is not registration proof.
5. Leave 940007 off the AI server and register it on the user's selected testing phone. No testing-phone setup has been performed.

## Premortem

- New active trunk unexpectedly takes outbound traffic: keep staged entries inactive until route selection is intentional.
- Provider registration succeeds but inbound caller hears unavailable: require a tenant-correct extension-to-agent route, not just a registered credential.
- Shared carrier IP routes one customer's call to another account: preserve exact account routing and tenant admission checks.
- Both server and phone register 940007: keep it exclusive to the manual testing device.
- Wrong registrar receives credentials: confirm the provider's new endpoint before any SIP registration attempt.
- Duplicate entry after retry: use an idempotency key and read back the created entry.
- Credentials leak into evidence: no passwords, tokens or encrypted credential material belong in this report.

## Verification boundary

### Actual provisioning result

- POST `/api/v1/telephony/sip/trunks` returned HTTP 201 for `blaze-pbx-940003` using idempotency key `pbx-940003-staging-20260920`.
- A separate GET returned trunk ID `73d882aa-7474-406f-ac58-8b7249cecd7a`, tenant `1845a165-08aa-4554-bcec-2d31ac523662`, username `940003`, `auth_configured=true`, `is_active=false`, `live_registration_status=null`, `runtime_ready=false`.
- The set of existing active connection IDs was identical before and after creation.
- No activation endpoint was called. The new SIP credentials were saved to the approved Talky account, but no SIP registration with the provider was attempted.
- 940004–940006 remain uncreated; no credentials or sessions for their account APIs were supplied in this setup.
- 940007 remains reserved only by the agreed plan. It is not registered or reserved by an application-level allocation record.

This is operational provisioning, not an application-code change. No canonical code suites have been rerun, no deployment or restart was performed, and no test calls were placed. Registration and end-to-end call acceptance remain open.

## Continuation: production number handling and MicroSIP inspection

The user requested persistence through call readiness and explicitly selected the installed MicroSIP application for 940007. This authorizes configuration of that existing test extension, not creation of substitute account credentials or bypassing tenant authorization.

### Reproduced on the deployed code

Production HEAD remains `2f34c72ecef26827768180019a97b989e4537269`; its checkout is clean. API and Asterisk report active. `sudo -n true` returns `sudo: a password is required`.

Executing the live pure number-handling functions with the proposed extensions produced:

| Input | Inbound `normalize_did` | Outbound canonical `normalize_phone_number` |
| --- | --- | --- |
| 940003 | None | ValueError: Phone number too short (minimum 7 digits for phone numbers) |
| 940004 | None | Same ValueError |
| 940005 | None | Same ValueError |
| 940006 | None | Same ValueError |
| 940007 | None | Same ValueError |

The initial combined diagnostic stopped on the first outbound ValueError. A second diagnostic caught each error and reported all five cases. These were in-process read-only probes, not actual calls or registration attempts.

Live `inbound_router.resolve_inbound_route` rejects a failed DID normalization as `invalid_did` before database lookup. Adding a registered SIP trunk alone cannot make a raw six-digit inbound target pass this path. A verified carrier-account-to-real-DID mapping or a properly implemented tenant-scoped extension addressing path is required. Changing the E.164 minimum length alone would not supply the missing ownership/campaign binding.

Qualification of the outbound-selection finding: campaign-specific trunk assignments and tenant pool assignments take precedence over the own-trunk selector. The newest active own trunk affects the remaining unassigned route path; it does not necessarily replace every campaign's route.

### Desktop inspection

MicroSIP was located at `C:\Users\AL AZIZ TECH\AppData\Local\MicroSIP\microsip.exe` and launched through the desktop automation tool. Its existing account is 150001. The observed status alternated between `Connecting...` and `Request Timeout`; this is not evidence of registration and does not diagnose the cause of the timeout.

No existing account was overwritten or deleted. No call was placed. The account-menu actions did not expose a menu or a settings dialog in the observed state, including after focus recovery; 940007 has not been entered or saved. The app remains open for an operator.

MicroSIP's official account documentation identifies server/domain, username and password as provider-supplied registration settings: https://www.microsip.org/help . The new provider message still does not independently identify the registrar.

### External prerequisites still required

- Legitimate account/admin access for the three remaining approved tenants; only the AllStateEstimation login has been established.
- Confirmation of the new extensions' registrar, port and transport before sending their SIP authentication material to an assumed endpoint.
- Operator-assisted protected deployment access for the supported routing/release work; passwordless sudo is not available.
- A working MicroSIP account-configuration interaction or operator completion of the account form.

No new application fixes, production route changes, trunk activations, migration, deployment or test calls were performed in this continuation. The earlier 940003 inactive staged record is not a completed registration.
