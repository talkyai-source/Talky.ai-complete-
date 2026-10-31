# PBX stability remediation — 2026-09-14

Status: local software gates passed; production release blocked. **Not committed, pushed, deployed, or production-complete.**
Latest verification update: September 15, 2026.

Base: origin/main 4959292097384fb09a571ca42079fc2e65c1a3d4.
Integration base refreshed September 15: origin/main `bf53fef36c0c5da41a6f3d37b706634e55f8bd37`. Two intervening frontend commits changed settings-page logout placement, the billing top-up card and navbar behavior. None overlapped this candidate's edited files. A fast-forward in the isolated worktree preserved them; no local desktop changes were overwritten. Final suites must use this integrated base.
Worktree: codex/pbx-stability-20260913. Existing PBX fixes are preserved.
Owner: primary agent; no parallel writers assigned.
Code and raw evidence are in `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-pbx-audit-20260913`. All source and `output/` paths below refer to that isolated worktree, not the main desktop checkout. A report copy is provided in the main checkout for visibility; the implementation remains isolated to preserve unrelated work.

## Work board

| Issue | Files / boundary | Done condition | Status |
|---|---|---|---|
| Pool authority | SIP schemas, allocation service/API, migration | Tenant cannot self-publish or use an ungranted connection; revocation enforced | Implemented; unit and disposable-PostgreSQL tests pass; production migration pending |
| Stable outbound route | trunk_resolver, assignment API/UI | Test/edit cannot change default; explicit assignment and ambiguity refusal | Implemented; targeted tests pass; two existing tenants require reviewed defaults |
| Fail-closed origination | telephony_bridge, resolver | Lookup failure produces no originate; final caller-ID authorization | Implemented; targeted authorization tests pass; production call proof pending |
| Runtime capability honesty | runtime adapter/API, SIP capabilities/UI | Asterisk rejects legacy apply; production simulation cannot succeed | Implemented; targeted tests pass; flags still require operator verification |
| Durable config apply | PJSIP generation/reconciliation, persistence | Crash/retry/concurrent generations do not create false readiness | Implemented and locally tested; Linux/systemd/Asterisk acceptance pending |
| Proxy/network boundary | PJSIP renderer, target validation | OPTIONS proxy rendered; runtime network policy and private-PBX boundaries verified | Proxy and validation tested; nftables integration is prepared, not Linux-verified |
| UI ownership and status | SIP management | Mode, account, DID, route and readiness clearly distinguished | Implemented; eight component tests pass; latest visual/browser acceptance pending |
| Production acceptance | supported deploy, monitors, real calls | Exact SHA/config, health, two directions, transfers, artifacts and rollback proved | Blocked: operator sudo, approved drain/rollback evidence and test phones missing |

## Verification policy

Each change requires a failing regression followed by a passing regression.
Full backend unit/security, Ruff, Talk-Leee and Admin canonical gates after integration.
No production writes outside the supported deployment path; no test calls to unapproved third parties.
Migration and allocation changes require read-only impact inventory before deployment.

## Premortem

- Tightening fallback can stop tenants whose assignment is implicit: inventory and explicitly approve routes before release; never infer an account from most recent edit.
- Shared metadata is not authority: grants must be operator-issued and rechecked on every new call.
- Revocation affects new calls; this candidate freezes the route in memory and rechecks authorization after warmup. It does not add a durable outbound routing snapshot to the database.
- A file replacement does not prove Asterisk loaded it: readiness must identify the applied generation.
- Credentials must never enter reports, logs, generated test snapshots or frontend responses.
- A green registration cannot prove media, greeting, transfer or billing: real-call gates remain separate.

## Outcome

The candidate removes implicit sharing and timestamp-based allocation, moves production PBX configuration publication out of request transactions, and makes runtime capabilities explicit. These are source-of-truth changes rather than a retry that conceals the original failure.

**Release stop: enforcement cannot be enabled against the current assignments.** The fresh September 14 read-only inventory found 17 active outbound-capable connection rows across 12 tenants, no explicit campaign assignments and no legacy tenant defaults. Eleven rows are shared-endpoint aliases. Thirteen rows have no configured caller ID; three have a configured number that is not verified for their tenant; one has a configured, verified number. Both tenants with own connections have multiple candidates. Thus simply deploying this stricter resolver without reviewed allocation/caller-ID preparation would refuse the currently implicit outbound routes. No automatic backfill or production write was performed.

This is **not** a certificate that inbound, outbound, PBX provisioning, transfers or billing work in production. No call was originated by this remediation run, no production data was changed, and no production service was restarted.

The isolated worktree protects the unrelated changes in the user's main desktop checkout. No stash/reset/checkout-- was used. The existing `telephony/deploy/keepalived/notify.sh` line-ending difference and regenerated tracked Python bytecode are not release changes and must not be staged.

## Evidence and changes, by root cause

### A. Testing a connection could influence outbound routing

Root cause: multiple active connections were selected using mutable `updated_at` timestamps. The connectivity-test endpoint updated that timestamp.

Changes:

- `backend/app/domain/services/telephony/trunk_resolver.py`: multiple eligible own connections now refuse with `outbound_assignment_required`; ordering cannot pick a winner.
- `backend/app/api/v1/endpoints/telephony_sip/trunks.py`: Test writes only its test result and test timestamp.
- `tenant_outbound_routes`, introduced by migration 0046, stores the explicit tenant default.
- Precedence is campaign assignment → tenant default → exactly one eligible own connection. An unavailable explicit assignment refuses instead of silently selecting another connection.
- Inbound DID assignment is not changed by setting an outbound default.

Proof: `test_ambiguous_own_connections_refuse_regardless_of_test_timestamp` reverses the two candidates and requires the same refusal. The PostgreSQL test proves a connectivity timestamp change creates no configuration event.

Premortem: refusing ambiguity changes behavior for existing tenants. The read-only inventory found two tenants with multiple ready own connections and no explicit assignment. An operator must select their intended defaults before releasing this enforcement. Choosing the newest account would recreate the defect.

### B. Tenant-controlled metadata was treated as sharing authority

Root cause: `metadata.pool` and JSON assignment snapshots were used as authority to access platform/shared connections.

Changes:

- Tenant create/update schemas reject `metadata.pool`.
- `connection_authority.py:9` resolves ownership or an active operator-issued grant with an explicit target tenant predicate.
- `connection_grants.py:30` adds platform-admin grant/revocation handling, binding a caller ID to a specific tenant and connection.
- Migration 0046 adds `sip_connection_grants`, FORCE RLS, tenant-readable/operator-writable grant policies, and a database trigger protecting default assignment.
- Pool/default selection lists only owned or actively granted connections. Existing metadata is retained for compatibility but does not grant cross-tenant authority.
- Assignment and grant mutation use the connection-row lock to serialize conflicting writes.

Proof against disposable PostgreSQL:

- `test_pool_metadata_does_not_authorize_other_tenant`.
- `test_grant_and_revocation_are_enforced_on_real_rows`.
- `test_tenant_role_cannot_create_its_own_grant` uses a NOSUPERUSER/NOBYPASSRLS role.
- `test_database_rejects_unauthorized_default_assignment` expects a real PostgreSQL check violation.

Premortem: migration does not backfill grants from untrusted metadata. Previously implicit consumers need reviewed grants, not automatic grandfathering. Current grant rows retain actor/timestamps but do not constitute an immutable grant-change history.

An HTTP-level follow-up reproduced an incorrect inherited tenant-context guard on the new grant endpoint: a platform administrator without a home tenant received 403 before connection lookup. The tenant-admin dependency is now attached to each tenant resource router; grant handling retains its platform-admin dependency independently. `test_pbx_grant_api_authorization.py` proves both sides: platform/no-home-tenant reaches lookup, ordinary tenant/user roles cannot grant, and tenant-scoped trunk routes still reject missing tenant context or insufficient role. Red: `1 failed, 3 passed in 9.94s`. Green with existing SIP/admin-isolation coverage: `54 passed, 5 warnings in 6.95s`.

### C. Lookup errors could silently authorize a global fallback

Root cause: routing failure could return the platform default endpoint rather than a refusal.

Changes:

- `_fallback_route()` refuses even if the legacy compatibility flag is enabled.
- The flag defaults off; production no-own-connection routing refuses.
- `telephony_bridge.py` resolves before expensive warmup, handles refusal explicitly, and does not substitute a global endpoint on lookup exceptions.
- The call retains its resolved connection rather than selecting again after warmup.

Proof: `test_lookup_failure_never_authorizes_a_shared_connection`, `test_shared_fallback_is_not_default`, and `test_unhealthy_own_connection_does_not_fail_over_to_shared`.

Premortem: a database outage must stop new calls, not send them through another tenant's account. Existing development-only compatibility behavior remains distinguishable from production behavior.

### D. The final caller ID could differ from the authorized number

Root cause: route resolution could overwrite an earlier validated/requested caller ID, or prefer another tenant DID over the connection's configured number.

Changes:

- `connection_authority.py:45` validates the final connection/caller-ID pair.
- Owned connections require a matching configured number and verified tenant number; shared use requires an exact active operator grant.
- Production origination checks before warmup and immediately before originate.
- Connection caller ID takes precedence over unrelated tenant-wide DID selection.
- SIP metadata now normalizes explicitly international numbers through the existing canonical phone module and rejects national-format caller IDs without a country code.

Red proof for the additional normalization defect:

```text
E AssertionError: assert '+44 20 4613 2300' == '+442046132300'
E Failed: DID NOT RAISE <class 'ValueError'>
2 failed, 2 passed, 11 deselected in 3.94s
```

Green proof: the 120-test integrated targeted run includes both normalization tests, the configured-number precedence test, and final authorization tests. PostgreSQL tests verify wrong-number and revoked-number refusal.

Premortem: stored legacy numbers are not silently rewritten. Inventory noncanonical/unverified caller IDs before deploy. Authorization lookup exceptions currently deny generically; denial cannot be interpreted as proof of malicious use rather than an infrastructure failure.

### E. Simulated or unsupported PBX controls could look functional

Root cause: the legacy runtime adapter could simulate success or target OpenSIPS/FreeSWITCH artifacts while production uses Asterisk.

Changes:

- `runtime_policy_adapter.py` rejects legacy engine application on Asterisk.
- Production simulation cannot produce a successful apply/verify result.
- `capabilities.py` and `/capabilities` publish operator-approved transports and SRTP availability.
- Production defaults to UDP only; unsupported transport/SRTP settings are rejected at write time and disabled in the UI.
- UI distinguishes registration **to** a remote PBX from providing a registrar. Talky does not provide the latter through these controls.

Proof: `test_production_simulation_cannot_report_applied`, `test_asterisk_cannot_apply_other_engine_artifacts`, `test_production_rejects_unapproved_transport`, `test_production_rejects_unapproved_srtp`, and the UI transport/registration-direction test.

Premortem: an environment flag is operator approval, not evidence that a TLS listener/certificate exists. Do not enable TCP/TLS/SRTP until live listener and call tests prove them. Unsupported runtime controls fail honestly; they have not been reimplemented for every PBX engine.

### F. Database, file publication and reload were not one durable workflow

Root cause: request cancellation, failed reload or process death could separate database state from Asterisk files; a reload request being accepted did not prove the runtime consumed it.

Changes:

- Production `_sync_trunk_pjsip_config` does not write files inside an uncommitted request.
- Migration 0047 inserts credential-free `telephony_config_changes` rows in the same database transaction as trunk or inbound binding changes.
- Trunk changes increment `config_generation` and invalidate readiness.
- `config_reconciliation.py:5` takes a transaction-scoped advisory lock, captures pending IDs, invokes the canonical reconciler and acknowledges only those captured IDs after a verified digest is returned.
- `reconcile_pbx_outbox.py` reuses `reconcile_asterisk_release.sh`; there is not a second production PJSIP writer.
- New systemd service/timer schedule durable retries.
- Status updater keeps pending changes at `checking` and no longer removes production files independently.
- The generated endpoint contains `TALKY_CONFIG_GENERATION`; runtime proof rejects the previous generation even if the endpoint name/context exists.
- The existing development reload path now serializes requests and kills/reaps timed-out or cancelled reload children.

Proof:

- PostgreSQL executes both actual migration modules inside a disposable schema and proves queue/config rollback together.
- Outbox unit tests prove failure does not acknowledge and newly arriving work is not acknowledged by an older batch.
- Generation tests reproduced a stale endpoint incorrectly passing proof, then passed after the runtime-generation check.
- Cancellation/reload tests cover failure, timeout and child cleanup.

Premortem:

- Reconciliation respects the existing zero-active-channel gate; a continuously busy node can defer indefinitely until a controlled drain. This is not a guarantee of immediate hot application.
- Asterisk CLI formatting for the generation marker still needs live Linux verification.
- Existing rows begin at generation zero; one reviewed canonical reconciliation is necessary during rollout.
- A full migration chain on a production-shaped restored database was not run; the new migrations were tested on minimum parent-table fixtures.
- Timer enablement, boot ordering, rollback and failure injection on actual systemd remain deployment gates.

### G. Proxy routing and DNS/private-network boundaries

Root cause: AOR OPTIONS qualification omitted the outbound proxy; an API DNS check alone cannot constrain a later PJSIP DNS lookup.

Changes:

- Generator puts the outbound proxy on endpoint, AOR and registration.
- Shared network validation accepts public peers and exact operator-approved RFC1918/ULA addresses, not a broad production private-network bypass.
- A prepared root-only nftables helper checks syntax before atomically replacing only the dedicated Talky table, scoped to Asterisk's non-root UID.
- Local DNS and the configured loopback media port range are explicitly permitted.
- Desired and observed rule fingerprints are checked before canonical config apply.
- Supported deployment includes the guard and reconciliation timer.

Proof: unit tests verify AOR proxy output, rejection of metadata/private targets despite the old broad flag, exact private-IP approval and bounded/scoped rule generation.

Not proved: Linux nft parser/runtime, DNS-rebinding packet rejection, real RTP compatibility, firewall recovery and Asterisk boot ordering. The candidate must not be deployed until these are tested. In particular, the new egress unit is enabled, but no Asterisk dependency drop-in currently guarantees Asterisk waits for it on boot. Network hardening is therefore **prepared, not complete**.

The design follows [Asterisk's PJSIP proxy documentation](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/PJSIP-with-Proxies/) and [OWASP's SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html). Neither source substitutes for a host-specific packet test.

### H. Frontend clarity and safety

Changes in `Talk-Leee/src/components/settings/sip-trunks-list.tsx` and `src/lib/telephony-api.ts`:

- Editing without a new password preserves saved credentials.
- Username changes require a replacement password; password-only edits cannot be silently discarded.
- Platform-managed rows do not offer ineffective editing.
- Unsupported deletion is not offered; deactivation remains explicit.
- Header/help/action stack responsively.
- Default outbound connection is labeled separately from inbound DID assignment.
- A revoked/removed assigned connection stays visible as unavailable and can be cleared/replaced.
- Unsupported transport and SRTP options are disabled.
- Saved/pending configuration, registration and real-call readiness are not described as equivalent.

The enclosing `telephony-providers-section.tsx` also carried obsolete instructions promising platform fallback and selection of whichever trunk was active. Two rendering tests reproduced these false promises. The banner, clear-selection feedback/button and SIP note now describe authorization and explicit assignment without claiming that clearing provider preference disables all telephony. Both provider-message tests and the eight SIP component tests pass together: **10 passed, 0 failed** (`output/pbx-routing-copy-green.log`).

Proof: the revoked-default test first failed because the selector disappeared; the corrected component test run reports:

```text
tests 8
pass 8
fail 0
skipped 0
```

The frontend-polish skill influenced responsive layout and unavailable-state behavior. Latest browser visual verification is incomplete: the mocked Next.js session redirected during navigation and the local preview was stopped during host memory pressure. Component tests are not screenshot or real-browser evidence.

## Verification ledger

Commands ran in the isolated worktree using the existing backend virtualenv; production secrets were not used in the disposable database.

| Gate | Observed result | Evidence |
|---|---|---|
| Targeted PBX/backend suite | `120 passed in 9.40s` | `output/pbx-targeted-integrated.log` |
| New migrations, authority and wiring | `16 passed in 9.17s` (7 PostgreSQL + 9 wiring) | `output/pbx-database-and-wiring.log` |
| Final targeted SIP/provider UI | 10 passed, 0 failed | `output/pbx-routing-copy-green.log` |
| Outbound lifecycle boundaries | `45 passed in 5.98s` | `output/pbx-boundaries-integrated.log` |
| Grant HTTP authorization + existing SIP/admin isolation | `54 passed, 5 warnings in 6.95s` | `output/pbx-grant-api-green.log` |
| Backend Ruff F gate | `All checks passed!` | `output/pbx-ruff-final.log` |
| Alembic heads | `0047_durable_pbx_reconcile (head)` | Command output this turn |
| Final Talk-Leee canonical on refreshed main base | **typecheck/lint exit 0; 483 passed, 0 failed, 2 skipped** | `output/pbx-frontend-final-typecheck.log`, `pbx-frontend-final-lint.log`, `pbx-frontend-final-test.log` |
| Expanded RLS guard | `1114 passed in 52.90s` | `output/pbx-rls-guard-green.log` |
| Final integrated backend unit/security | **`8991 passed, 8 skipped, 1475 warnings in 422.28s (0:07:02)`** | `output/pbx-backend-canonical.log` |
| Admin lint/test/build | Exit 0; 13 tests passed | Command output this turn |
| Linux/Asterisk/nftables | Not run with required privilege | Production sudo refuses noninteractive use |
| Real inbound/outbound/transfer | Not run | No approved independent test destination/caller coordination |

Earlier backend attempts are retained as failed/incomplete evidence, not counted as green: one was stopped after source integration; one stalled at 43% and produced a faulthandler stack. An overloaded concurrent run also hit Windows memory exhaustion and a CLI-help timeout. The 120-test targeted rerun passed without loosening that timeout. Full-suite completion is still mandatory.

The verbose rerun located the 43% stall in outbound boundary fixtures: the mocked route lacked `trunk_id`, so it raised `AttributeError` before reaching the cleanup test's event. The fixture now supplies the real route contract and mocks the final authorization boundary; existing cancellation/settlement assertions remain intact. The old refusal test now asserts the stronger invariant of **no warmup at all** instead of cleanup after unnecessary warmup. Two additional endpoint tests deny before warmup and after warmup, asserting zero originates and no retained warmup. All 45 boundary tests pass. The affected full run was stopped and must not be reported as a full-suite pass.

The next full run reached 64% and correctly failed the schema/security inventory invariant: `missing=['sip_connection_grants', 'telephony_config_changes', 'tenant_outbound_routes'], stale=[]`. Its result was `1 failed, 5832 passed, 6 skipped, 867 warnings in 577.83s`. The sorted protected-table inventory has been extended to cover the new tables; its equality check and no-grandfathering policy remain unchanged. All 1,114 tests in that guard file passed afterward. That partial full run is not counted as a passing canonical run.

A remaining-files run spanned `16:24:09` wall time and failed `test_canary_evidence_scope_is_exact_and_stably_hashed` because its module-level timestamp had expired (`canary evidence run is stale`). The production freshness rule was not modified. An immediate isolated rerun of the entire observability file reported `18 passed in 1.03s` (`output/pbx-canary-clock-retry.log`). The long interrupted run remains a failed run, not a pass.

The first completed integrated run on `bf53fef3` reported `1 failed, 8990 passed, 8 skipped, 1475 warnings in 368.95s`. Its only failure was the old SIP-router structural assumption that every group requires a home tenant. The new platform-grant group now explicitly inherits `require_platform_admin`, while the four tenant-resource groups inherit `require_admin_tenant`. The invariant test checks these exact five authority choices rather than permitting an unguarded group. Its tests plus the real HTTP dependency tests reported `7 passed in 1.30s` (`output/pbx-router-authority-green.log`). A fresh canonical run is required after this correction.

That fresh canonical run completed successfully on September 15: **8991 passed, 8 skipped, 1475 warnings in 422.28s (0:07:02)**. No failing test was excluded. The eight skips and warnings remain visible; Windows checks do not substitute for the Linux/Asterisk deployment gates.

The final Talk-Leee run also completed successfully after the provider-copy edits and integration of `bf53fef3`: **485 tests, 483 passed, 0 failed, 2 skipped**, with typecheck and lint exit 0. The main branch was rechecked and still pointed to `bf53fef36c0c5da41a6f3d37b706634e55f8bd37` at handoff. The disposable PostgreSQL server and the earlier local browser/preview were stopped; no test service was left running intentionally.

## Fresh production observation

The final read-only command at **2026-09-15 00:38:55 UTC** returned:

```text
HEAD 2f34c72ecef26827768180019a97b989e4537269
git status --porcelain: empty
talky-api: active
talky-voice-worker: active
talky-dialer-worker: active
talky-voice-gateway: active
asterisk: active
ready=true, draining=false, active_sessions=0, at_capacity=false
sudo: a password is required
```

Source: `output/pbx-production-final-20260915.log`. The earlier September 13 snapshot is retained in `output/pbx-production-access-final.log`. Service liveness and zero current sessions do not prove traffic remains drained or calls work.

### September 14 allocation impact check

`output/pbx-release-production-inventory.log` records a new read-only PostgreSQL transaction with an explicit service RLS context. The status updater evidence was three seconds old for every returned connection. It contains no SIP passwords or API keys.

| Tenant | Current own candidates | Operator decision required |
|---|---|---|
| AllStateEstimation (`1845a165…`) | `blaze-pbx-940001` (`6b01062c…`), `blaze-pbx-940002` (`8b5fa0f1…`); both lack configured caller ID | Select intended connection and prove its carrier-approved, tenant-verified caller ID |
| AllStateEstimation.co (`790ca2db…`) | `blaze-allstate` (`44b41a0d…`) with verified `+442046132300`; three other own connections present `+442046132301`, unverified for this tenant | Confirm intended default; do not infer it solely from verification status or most recent edit |
| Shared-alias consumers | Eleven `blaze-primary` rows across eleven tenants | Review whether each tenant should retain platform service; issue explicit allocation/number authority for intended consumers |

There are zero stored campaign trunk assignments in the inventory. An alias marked registered is not a separately registered carrier account and does not prove caller-ID authority. These counts are release-impact evidence, not a claim that all 12 tenants currently place calls.

## Rollout and rollback gates — not bypassed

1. Canonical local suites are now passed. Complete the remaining visual checks and Linux integration checks; rerun affected suites after any further changes.
2. Review both ambiguous tenants' intended outbound defaults and caller-ID verification. Review any required cross-tenant grants.
3. Test candidate migrations and rollback compatibility on a restored production-shaped database.
4. Complete Linux firewall syntax, boot-order, recovery and packet/media tests. Verify the generation marker in actual Asterisk CLI output.
5. Freeze named-file commits, with the required author and no trailers. Do not stage unrelated files or generated artifacts. Deploy from a fresh clean worktree of the reviewed commit, not by cleaning/resetting this worktree or the user's desktop checkout.
6. Coordinate backend and Vercel release order. Do not publish a frontend that expects the new capability/default API while the backend still implements the legacy sharing contract.
7. Obtain the supported deploy's candidate-bound drain manifest/hash and two independent approval records. Do not invent these from a zero-session health response.
8. Run `deploy_to_server.sh` with the operator present for sudo; retain complete output and the exact candidate SHA.
9. Prove schema head, generated configuration digest, loaded generation, gateway build identity, service/timer state and every expected registration.
10. Use an approved external phone for inbound +442046132300 and an approved outbound/transfer destination. Prove greeting and two-way audio, successful/failed transfer, recording consent, transcript, summary, billing and outbound regression.
11. Keep the prior compatible application/gateway/configuration artifacts and a tested traffic-disable/rollback procedure. Migration 0046/0047 downgrade deliberately refuses; git checkout alone is not a demonstrated rollback.

The current deployment documentation itself requires topology-specific ingress-disable evidence and compatible rollback artifacts. This run did not produce them. The proposed firewall table also needs an independently reviewed rollback procedure; removing the table while leaving public PBX configuration active is not automatically a safe rollback.

## Required operator inputs

- An operator-assisted sudo session for the supported deployment. Do **not** send a sudo password in chat or store it in the repository.
- The approved maintenance/drain and rollback evidence required by `docs/DEPLOYMENT.md`.
- Intended default connection for each ambiguous tenant, plus any deliberate operator grants.
- A user-controlled outbound/transfer test phone and availability to call the inbound DID externally.

## Architectural rationale

Tenant ownership/grants determine **who may use a connection**. An explicit default/campaign assignment determines **which connection outbound uses**. Verified number bindings determine **which caller ID may be presented**. Inbound DID assignments independently determine **which tenant/campaign receives an incoming call**. Registration is connectivity evidence, not any of those permissions.

The database queue follows the [transactional outbox principle](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html): desired state and pending work commit together, while the external side effect is retried idempotently. No AWS service was added. PJSIP object roles follow [Asterisk's configuration relationships](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/PJSIP-Configuration-Sections-and-Relationships/).

## Explicitly not accomplished

- No production deployment or data change.
- No new production C++ binary built/installed by this run; no C++ source was changed.
- No new production registration, greeting, media, transfer, recording, summary or billing proof.
- No final browser visual acceptance of the latest UI.
- No durable outbound route-snapshot schema added.
- No immutable grant event history added.
- No new platform-admin grant-management screen; grant issuance/revocation is API-only in this candidate. Tenant default selection is available in the existing SIP settings UI.
- No verified firewall/Asterisk reboot ordering or firewall rollback.
- No universal PBX compatibility claim, no TLS/SRTP enablement, no inbound registrar implementation.
- No claim that all historical goals or all product features are complete.
