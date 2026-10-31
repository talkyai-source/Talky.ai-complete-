# Report 14 — Inbound Calling Production-Hardening and Goals Audit

**Prepared:** 2026-08-31  
**Previous report:** `report13.md` at `2576a4a6ea016be2fee4d6a1d8e8bc3a02b9c2ae`  
**Implementation branch:** `codex/inbound-hardening-20260831`  
**Implementation head before this report:** `12991153`  
**Historical production baseline supplied for this work:** application `69e607e9`, schema `0035`  
**First answered inbound call supplied as evidence:** `245a32de`, 83 seconds  
**New schema head:** `0036`

## Executive verdict

The inbound repository candidate is substantially hardened and the planned
implementation waves in scope for this session are complete. It is **not yet
honest to call the production service 100% achieved**. Code readiness and live
production acceptance are different gates.

| Question | Verdict | Proof / reason |
|---|---|---|
| Is the inbound implementation present? | **Yes** | Routing, admission, billing, campaign UI, operator views, gateway, failure audio, deployment controls and monitoring are in the repository. |
| Is the original `goals.md` inbound implementation complete? | **29 of 31 items verified** | Section 5 is updated; the two open items require live greeting and enabled-transfer evidence. |
| Is the candidate committed? | **Yes** | Eight focused commits through `12991153` were added during this hardening pass. |
| Is it pushed or deployed? | **No** | This branch is local and no production mutation was made. |
| Is production 100% accepted? | **No / NO-GO today** | Carrier DID mapping, migration/deploy, alert destination, frozen live canary, second real DID/tenant, transfer, billing, recording/privacy and soak evidence remain open. |
| Can code simply be pushed and tested directly on production? | **No** | Production-only configuration and pre-answer safety gates must be installed and verified before ingress is enabled. |

This is deliberate fail-closed behavior. A missing approval or live proof is
recorded as pending; it is never converted into a green checkbox by inference.

## What changed after report 13

Report 13 ended at commit `2576a4a6`. From that point through implementation
head `12991153`, there are 20 commits and a repository delta of 548 files,
108,155 insertions and 17,831 deletions. The material post-report work is:

| Commit | Achievement |
|---|---|
| `e6ffc36c` | Added the inbound schema sequence `0022`–`0035`, validated against a production restore. |
| `69fff6f4` | Added deterministic pre-answer admission, billing holds and lease safety. |
| `f763f9e9` | Corrected billing, do-not-call, permission, media and tenant-boundary defects found by audit. |
| `c2fe07af` | Added extensive inbound, RBAC, recovery, validation-tenant, isolation, load and release-safety tests/tooling. |
| `79e46464` | Passed the dialled DID through Asterisk where the adapter reads it and enabled `pjsip.d`. |
| `bd93b6d3` | Made user/admin interfaces truthful, including inbound campaign and live-call controls. |
| `b6c0eee3` | Added the release gate, deployment and runbook documentation and relevant CI coverage. |
| `1bcfb9cc` | Repaired the Next.js proxy-path regression test. |
| `a3e6d955` | Kept the trunk status updater visible after loss of `BYPASSRLS`. |
| `820e1e91` | Bound the inbound Answer timestamp as a database datetime. |
| `69e607e9` | Gave the C++ gateway client a dedicated `aiohttp` session. |
| `fa64d54f` | Scoped pooled database reads correctly after production RLS changed. |
| `6c9f7ded` | Hardened the C++ media gateway control and startup contract. |
| `72cd445b` | Swept tenant-sensitive pooled DB access and added the static RLS invariant. |
| `c3dd6ffb` | Made deploy preflight occur before checkout and protected the working dialplan. |
| `c5b2ef95` | Added denial cause signaling and answer-to-first-audio measurement. |
| `e7b89245` | Persisted rejected inbound calls and exposed live/rejected operator views. |
| `94935a80` | Added provider-independent emergency caller audio and terminal failure handling. |
| `9ee5817a` | Added carrier-hairpin synthetic liveness and the no-success alert contract. |
| `12991153` | Proved two DIDs resolve independently to two tenants/campaigns and synchronized `goals.md`. |

## A–Z delivery and evidence ledger

| Letter | Area | Result |
|---|---|---|
| A | Admission | Pre-answer tenant, campaign, subscription, minutes, concurrency, schedule, AI config and trunk checks fail closed. |
| B | Billing | Inbound reservations, settlement, overage holds, replay protection and transfer-leg accounting are covered by migrations and tests. |
| C | C++ gateway | Token-authenticated control API, callback pinning, bounded sessions, codec validation, build identity and negative startup checks replace the former permissive artifact. |
| D | Deployment | Toolchain and candidate preflight now happen before checkout; migration, service and timer stages cannot silently half-apply. |
| E | Emergency audio | Two checksum-pinned PCMU clips bypass a failed TTS provider; the second failure apologizes and ends the call. |
| F | Fail-closed behavior | Unknown/ambiguous DID, missing policy, stale trunk, invalid config and dependency failures cannot fall through to a default tenant. |
| G | Goals audit | `goals.md` section 5 moved from 0/31 to 29/31 based on code and automated proof; two live-only criteria remain unchecked. |
| H | Hangup signaling | Denials map to Q.850/SIP semantics with a bare-hangup retry if the reasoned ARI request is rejected. |
| I | Isolation | RLS acquisition is tenant-aware, an AST guard rejects new bare acquisitions, and two-DID/two-tenant routing is tested. |
| J | Caller journey | Local ringback begins before Stasis; admission timing and answer-to-first-agent-audio are measurable. |
| K | Key/credential safety | Internal gateway authentication is mandatory and tenant BYOK resolution is no longer silently hidden by RLS. |
| L | Live calls | The user panel carries direction, ANI, DID, admission and consent; the backend live endpoint is no longer RLS-blind. |
| M | Metrics | Admission latency, first accepted agent audio, successful inbound total and last-success timestamp are exported. |
| N | Number routing | Canonical DID lookup requires one complete active binding; context can confirm but never select a tenant. |
| O | Operator history | Durable rejection records plus after-hours call rows feed a tenant-scoped rejected-calls view. |
| P | PCMU media | PSTN-facing media stays PCMU/8 kHz to match Asterisk/carrier behavior and avoid unnecessary transcoding. |
| Q | Quotas | The repaired quota reads re-arm tenant limits that RLS previously made fail open. |
| R | RLS | The blind-read/write class is fixed and made detectable; zero-row call-state updates are no longer silently accepted as ordinary duplicates. |
| S | Synthetic liveness | An hourly carrier-hairpin originate and a stale-success alert detect the failure class that previously hid for weeks. |
| T | Transfer | Allowlist, idempotency, leg usage, restart reconciliation and failure paths exist, but production execution remains deliberately gated. |
| U | Unknown DID | Rejected before answer with no unowned ANI retained; operators receive durable privacy-safe evidence. |
| V | Version identity | Deploy can compare the running gateway build SHA to the application candidate. |
| W | WhatsApp codec decision | A WhatsApp/Opus codec is not a drop-in PSTN improvement; use Opus only on endpoints that negotiate it, otherwise transcode at a controlled boundary. |
| X | Cross-tenant proof | The router test resolves US and UK DIDs to separate tenants and campaigns and asserts different phone-row identities. |
| Y | Yield/rollback safety | Ingress freeze, controlled drain, reconciliation and code-forward/restore rules are documented; unsafe schema downgrade is not improvised. |
| Z | Zero silent failure | Rejections, state changes, first audio and synthetic success now have queryable/logged/alertable evidence rather than HTTP-200 empty views. |

## C++ media gateway structure and working

The gateway is the real-time media boundary between Asterisk RTP and the Python
agent runtime. It does not decide the tenant, campaign, prompt, billing or
business policy. Those remain backend responsibilities.

```text
Carrier SIP/RTP
    -> Asterisk/PJSIP + ARI controller
        -> externalMedia RTP (PCMU, 8 kHz)
            -> C++ gateway session
                -> authenticated control/callback bridge
                    -> Python voice pipeline
                        -> STT -> LLM -> TTS
                    <- accepted agent PCMU frames
            <- paced RTP/PCMU
        <- bridge/channel lifecycle events
    <- caller audio
```

Inbound and outbound share the media transport. They differ before media starts:

- inbound derives identity from the exact DID assignment and runs admission
  before answer;
- outbound begins from an already tenant-bound campaign/lead job;
- after binding, both create an Asterisk media bridge, allocate an external
  media channel, send caller RTP toward STT, and return synthesized audio;
- the gateway owns packet/session mechanics; Python owns conversation state and
  policy; Asterisk owns SIP/channel state.

The production hardening is not a workaround. It is a legitimate boundary:
Asterisk handles carrier telephony, the C++ process handles low-latency RTP,
and Python handles provider orchestration. The previous **deployment** was a
workaround because a manually compiled, unversioned binary and host-only
dialplan could not be reproduced. The new build identity, negative startup
test, repo-managed config and deploy assertions remove that operational
workaround.

## Codec decision

WhatsApp commonly uses wideband codecs such as Opus within its own negotiated
media system. That cannot simply be forced onto an ordinary PSTN leg. The
carrier and Asterisk trunk determine the negotiated codec; many PSTN paths
remain G.711 PCMU/PCMA at 8 kHz. Advertising an unsupported codec produces
failed negotiation, one-way audio, or extra transcoding—not higher quality.

The production choice here is:

1. keep Asterisk-to-gateway RTP in PCMU where the carrier path is PCMU;
2. decode once and resample internally only when STT/TTS requires another rate;
3. negotiate Opus only for an actual WebRTC/SIP endpoint that supports it end
   to end;
4. measure packet loss, jitter, clipping and answer-to-first-audio before
   changing codecs;
5. never label internal 16/24 kHz synthesis as wideband caller audio when the
   final PSTN hop is narrowband.

## Caller experience implemented

| Moment/failure | Implemented behavior |
|---|---|
| INVITE | Asterisk sends Ringing before Stasis, giving the caller an immediate pickup cue. |
| Admission | The call remains unanswered while no-charge eligibility checks run. |
| Unknown/unverified/conflicting DID | Terminal not-found semantics; no default tenant. |
| Infrastructure unavailable | Temporary-failure semantics suitable for carrier retry/reroute. |
| Tenant at capacity | Busy semantics, preventing a retry storm into the same cap. |
| Inactive/disabled/unfunded account | Terminal decline semantics. |
| Answer | `answered_at` and answer-to-first-agent-audio become observable. |
| First TTS failure | Pre-rendered “voice trouble / please hold” clip, independent of the TTS provider. |
| Second TTS failure | Pre-rendered terminal apology followed by hangup. |
| Caller silence | Existing hello/re-prompt/polite-close behavior remains unchanged. |
| After hours | Current supported behaviors are hangup, AI message intake, or gated transfer. “AI message intake” is not falsely advertised as a voicemail mailbox. |

## Operator experience implemented

- live calls no longer disappear because a pooled connection lacks a tenant
  GUC;
- call-state transitions can persist `initiated`, `in_progress` and `ended`;
- direction, ANI, DID, admission status and consent status appear in the live
  campaign panel;
- durable early-denial rows make unknown DID, policy, capacity and dependency
  failures queryable;
- the rejected-inbound view unions early denials with call rows such as
  after-hours admissions, so after-hours callers are not omitted;
- unowned rejected calls do not persist caller ANI;
- the hourly synthetic path and last-success timestamp create an operational
  signal even when no human happens to inspect the UI.

## RLS sweep proof

The production symptom was proven before remediation: a connection without the
tenant GUC returned zero `calls` rows, while the tenant-aware/bypass context
returned 1,041. A bare pooled acquire therefore converted authorization failure
into an apparently successful empty result.

The correction:

- uses `acquire_with_tenant(pool, tenant_id)` for tenant work and the explicit
  platform-internal mode for internal work;
- repairs live calls, call status, stream events, quota, call guard, BYOK
  credential and recording-policy access;
- adds `inbound_rejections` to the RLS-protected table set;
- adds an AST invariant test with an honest zero-entry allowlist;
- distinguishes a visible duplicate from an RLS-invisible zero-row state update
  and warns on the latter.

This change intentionally re-arms quota enforcement and tenant BYOK behavior.
Operations must reconcile existing usage and validate stored tenant keys before
deploy so the repaired enforcement does not surprise a customer.

## Deployment durability proof

The former sequence could check out new code and then fail because `cmake` was
missing, leaving new disk state with old processes and old schema. The hardened
sequence now:

1. checks `cmake`, `ctest` and the C++ compiler before checkout;
2. builds/imports the candidate in isolation;
3. validates the gateway's fail-closed startup contract;
4. refuses to replace the managed inbound dialplan unless the generated
   candidate matches the reviewed live block;
5. carries DID metadata through PJSIP/Asterisk;
6. runs migration and service stages only after preflight;
7. checks gateway build identity;
8. treats trunk-status refresh as timer-retryable rather than corrupting an
   otherwise completed deploy;
9. requires the synthetic-call environment file before a production deploy and
   restarts/checks the timer.

The C++ binary could not be compiled on this Windows workstation because no
local C++/CMake toolchain or Linux container runtime is available. The release
host preflight is therefore a mandatory proof, not an optional follow-up.

## Goals.md cross-verification

After the evidence-backed update in `12991153`, the literal checkbox inventory
is:

| Section | Checked | Unchecked | Honest status |
|---|---:|---:|---|
| 2 — Priority/scope | 8 | 13 | Mixed scope and deliberate deferrals. |
| 3 — Review/reward | 44 | 7 | Mostly implemented; remaining identity, monitoring, API, admin and AI-loop work/approvals are open. |
| 4 — Security sidebar | 21 | 1 | UI/backend/security acceptance verified; unsupported Allowed IPs remains explicit. |
| 5 — Inbound MVP | **29** | **2** | Repository complete; live greeting and live enabled-transfer criteria open. |
| 6 — Generic lead prompt | 10 | 36 | Versioned composition/identity/rollback is verified; the required frozen 30-call scored matrix is not recorded. |
| 7 — Lead form | 22 | 2 | Schema/runtime/UI are verified; separate manual-edit audit and approved CRM proof remain open. |
| 8 — Tooltips | 20 | 2 | Mostly complete. |
| 9 — Minute top-up | 21 | 0 | Checklist complete in repository; live payment/canary proof remains a global release gate. |
| 10 — Salesforce MVP | 0 | 23 | Not implemented; HubSpot is not Salesforce and marketing copy is not connector proof. |
| 11 — Contact fields | 38 | 2 | Canonical model/import/runtime behavior is verified; full contact-change audit and campaign field allowlist remain open. |
| 12 — 200-tenant validation | 0 | 143 | Deterministic seeder/isolation tooling exists; the actual approved 200-tenant run and evidence package do not. |
| 13 — Delivery calendar | 0 | 69 | Historical management milestones are not technical proof and cannot be retroactively checked. |
| 14 — Definition of done | 0 | 11 | Live staging/canary and owner acceptance keep the global definition open. |
| 15 — Daily management | 0 | 8 | Human process items require team records. |
| **Total** | **213** | **319** | The file is not fully accomplished. |

This report therefore does not claim the whole `goals.md` is finished. The
unchecked items fall into four classes:

1. **external evidence:** carrier calls, payments, Salesforce account,
   staging/canary, 200 sessions/tenants, soak and restore drills;
2. **owner decisions/approvals:** product, carrier, billing, security/privacy,
   legal/compliance, support and change authorization;
3. **explicit deferrals:** Salesforce beyond MVP and automatic AI/cash-reward
   behavior are allowed to defer by the document's own final delivery decision;
4. **remaining product work:** true voicemail, enabled production transfer,
   and any checklist item for which no end-to-end artifact exists.

## Automated proof

### Completed in this hardening pass

| Command/scope | Result |
|---|---|
| Consolidated inbound, Asterisk, gateway, RLS and emergency-audio unit suite | **1,511 passed**, 23 deprecation warnings, 38.06 s |
| Inbound router including new two-DID/two-tenant test | **20 passed**, 2.37 s |
| Full backend unit + security suites (from documented `backend/` directory) | **8,026 passed, 7 skipped, 0 failed**, 1,312 warnings, 6m 58s |
| Post-format RLS-fake/router regression | **68 passed, 0 failed**, 1 warning, 62.00 s |
| Talk-Leee TypeScript typecheck + full unit suite | **Typecheck passed; 302 passed, 2 DB-dependent skipped, 0 failed** |
| Admin TypeScript/Vite production build | **Passed**; 1,764 modules transformed, Vite build completed in 2m 56s |

The first broad invocation was run from repository root and reported 8,006
passes, 7 skips and 20 failures. Four failures disappeared when rerun from the
documented `backend/` directory because those tests intentionally open
`app/...` relative to their working directory. The remaining 16 were old fake
database connections that did not implement the transaction and valid UUID
contract now used by `acquire_with_tenant`. Their fakes were corrected; the
production RLS helper was not relaxed. The affected tests passed, followed by
the clean 8,026-test final sweep above.

## Premortem: assume production failed

| Failure | Earliest signal | Prevention now present | Remaining release action | Rollback/containment |
|---|---|---|---|---|
| Deploy half-applies | Preflight/build error before migration | Toolchain checks before checkout; isolated candidate | Run on the actual Linux host and retain logs | Abort before mutation; if later, keep ingress frozen and deploy compatible code forward |
| Dialplan loses DID mapping | Candidate/live diff | Repo-managed candidate and zero-diff gate | Import/review the exact carrier account-to-DID map | Restore reviewed block, reload Asterisk, prove unknown and known DID behavior |
| Live calls UI is empty while calls exist | Synthetic success but empty `/calls/live`; RLS warning | Tenant-aware acquisition + AST invariant | Smoke the endpoint under real tenant auth | Freeze ingress, repair context, reconcile calls directly |
| Wrong tenant answers | Route decision tenant/config does not match manifest | Exact canonical DID binding; ambiguity fails closed | Prove two real DIDs/two tenants from carrier | Disable offending DID and platform inbound immediately |
| Carrier sends an unexpected destination form | `invalid_did`/`unknown_did` spike | Multiple trusted metadata sources normalize to E.164 | Capture real INVITEs for each number/account | Leave unknown route unanswered; correct carrier/dialplan mapping |
| Caller gets dead air after answer | `answer_to_first_audio_ms` missing/high | First-audio metric; provider-independent clips | Approve latency threshold and live noisy/cold tests | Play terminal clip and hang up; roll back agent-first if needed |
| TTS provider rejects voice | TTS error plus emergency clip metric/log | Raw checksum-pinned PCMU fallback path | Validate clips through actual RTP/recording | Switch voice/provider after freeze; terminal clip prevents stranded call |
| Quota suddenly blocks tenants | Reconciled usage exceeds allocation | RLS fix restores correct quota enforcement | Audit month-to-date balances before deploy | Correct ledger/plan under four-eye process; never disable metering globally |
| Stale BYOK starts failing | Credential/provider error by tenant | Resolver now sees the tenant key truthfully | Validate/rotate tenant keys before canary | Quarantine affected tenant or choose approved platform credential policy |
| Rejection history leaks ANI | Unowned rejection includes caller field | Unowned unknown-DID rows omit ANI; RLS | Privacy test on production-shaped DB | Disable view/ingress, preserve audit, follow incident process |
| Transfer enables toll fraud | Any destination outside pinned allowlist | Platform/runtime/config gates, allowlist, attempts/hops and idempotency | Keep production transfer off until signed live scope | Disable transfer gates and terminate/reconcile child legs |
| Asterisk rejects reasoned hangup | ARI DELETE error | Immediate bare-hangup retry | Verify Asterisk version behavior in staging | Bare hangup; watchdog/reconciliation handles uncertainty |
| Synthetic says healthy without media | Originate accepted but no success timestamp | Alert uses first accepted agent packet, not originate response | Provision dedicated always-open agent-first synthetic campaign | Treat stale timestamp as outage; freeze public ingress |
| Synthetic creates false outage after hours | Timer fires against a closed campaign | Required dedicated always-open synthetic DID/campaign contract | Provision exactly that campaign | Disable only noisy timer during correction; retain no-success alert awareness |
| Alert has nowhere to go | Alertmanager has no approved receiver | Rule/config are present | Add and test real on-call destination | Production stays NO-GO; dashboard-only alert is insufficient |
| Process restart strands leases/legs | Lease/usage mismatch; nonzero cluster count | Heartbeats, expiry, PBX proof and restart reconciliation | Restart drill with in-flight calls | Freeze ingress, drain/end per runbook, reconcile every call/leg |
| Migration cannot roll back safely | Old code incompatible with schema | Forward-compatible/code-forward and restore policy documented | Prove backup restore and rollback candidate | Restore verified backup or code forward; do not improvise downgrade |
| Codec change makes audio worse | Negotiation failure, one-way audio, jitter/clipping | PCMU boundary retained | A/B only on a codec-capable endpoint | Revert codec advertisement and remove transcoding change |
| Outbound regresses | Baseline/candidate smoke differs | Shared paths protected by regression suite and direction tests | Run frozen outbound smoke before and after deploy | Roll code forward/back per compatibility proof; keep inbound frozen |

## Mandatory production release sequence

1. Obtain carrier confirmation for every advertised DID and its terminating
   account; provision a dedicated synthetic DID.
2. Create a separate always-open, agent-first synthetic tenant/campaign and
   populate `/etc/talky/inbound-synthetic.env` with the real DID/trunk endpoint.
3. Configure and test an approved Alertmanager receiver and escalation owner.
4. Reconcile tenant usage allocations and validate every tenant BYOK key that
   will become visible after the RLS correction.
5. Freeze candidate commit, environment, image/build identity, dialplan,
   frontend artifact and rollback artifact; hash the manifest.
6. Back up the database and prove restore compatibility in an isolated target.
7. Run deploy preflight on the Linux host (`cmake`, `ctest`, C++ compiler,
   gateway negative startup and candidate dialplan comparison) before checkout.
8. Deploy schema `0036`, application/frontend and commit-matched gateway;
   assert process/timer health and migration head.
9. Keep public ingress frozen. Run known-DID, unknown-DID, after-hours,
   capacity, dependency-failure and synthetic calls.
10. Prove the dashboard/live/rejected views and database/ledger records against
    carrier, Asterisk and application IDs.
11. Prove the configured greeting and its answer-to-first-audio target with a
    cold provider path and with TTS failure clips.
12. Prove tenant #2 and DID #2 using real carrier delivery; automated routing
    proof alone is insufficient.
13. If transfer is in the release scope, obtain approval, enable only the exact
    staging scope, prove success/failure/billing/restart behavior, then decide
    whether production gates may open. Otherwise keep every transfer gate off.
14. Obtain recording/privacy wording and retention approval before recording is
    enabled; prove consent ordering.
15. Run the disjoint controlled-call batches, the required 300-call carrier
    reconciliation and the approved soak/restart/failure drills.
16. Obtain engineering, operations, security/privacy, billing, support,
    carrier, legal/compliance and change-owner sign-off.
17. Activate the dedicated DID only through the documented canary controller;
    pause at every cohort and reconcile all attempts.
18. Maintain a staffed observation window and execute immediate rollback on
    wrong-tenant route, billing error, privacy breach, missing audio, leaked
    legs/leases, unauthorized transfer or material outbound regression.

## Explicit external blockers

The following cannot be manufactured by repository code:

- production SSH/server access and a successful deploy log;
- carrier confirmation for accounts/DIDs and real inbound provisioning;
- a second real tenant/DID/campaign route;
- a real synthetic DID and trunk endpoint;
- an approved on-call alert destination and tested notification;
- live greeting, after-hours, recording, billing and transfer calls;
- carrier/PBX/database/ledger/dashboard reconciliation;
- a 300-call inbound batch and approved two-hour soak/failure drills;
- restore evidence and signed rollback approval;
- security/privacy, billing, support, carrier, legal and product acceptance;
- Salesforce credentials/account and its required end-to-end duplicate/error
  tests;
- the frozen 30-call prompt scorecard and its recordings/transcripts.

## Evidence index

| Evidence | Repository location |
|---|---|
| Master production release gate | `docs/INBOUND_CALLING_RELEASE_GATE.md` |
| Deployment procedure | `deploy_to_server.sh` |
| Managed Asterisk setup/dialplan | `telephony/scripts/setup-asterisk.sh` and managed dialplan assets |
| Gateway build/identity | `backend/scripts/build_voice_gateway_release.sh`, gateway source and systemd unit |
| Inbound routing | `backend/app/domain/services/telephony/inbound_router.py` |
| Admission/billing | `backend/app/domain/services/telephony/inbound_admission.py` and migrations `0032`/`0034` |
| Campaign configuration/runtime | `backend/app/domain/services/inbound_campaign_service.py` and inbound API/UI |
| Rejection history | migration `0036`, calls API and rejected-inbound panel |
| Emergency audio | `backend/app/infrastructure/telephony/emergency_audio.py` and `backend/app/assets/telephony/*.ulaw` |
| Synthetic monitor | `backend/deploy/inbound-synthetic-call.sh`, systemd timer/service and Prometheus rule |
| RLS invariant | `backend/tests/unit/test_rls_set_local_invariant.py` |
| Two-DID/two-tenant proof | `backend/tests/unit/test_inbound_router.py` |
| Validation tooling | `backend/scripts/seed_validation_tenants.py` and `backend/scripts/isolation_matrix.py` |
| Current project checklist | `goals.md` |

## Final statement

The work after report 13 converts inbound from a one-off successful call held
together by host state into a reproducible, fail-closed, observable release
candidate. It removes the known RLS blindness, half-deploy risk, host-only
dialplan risk, silent denial/dead-air failure modes and lack of synthetic
liveness.

That is a strong engineering result, but it is not the same as a completed
production acceptance. The next legitimate milestone is a frozen Linux-host
deploy with carrier and operator evidence—not a blind Git push. Until the
mandatory sequence and external blockers above are closed, production remains
**NO-GO** and the whole `goals.md` remains honestly incomplete.

## Detailed implementation dossier

This section expands the executive report into the full engineering record requested by the project owner. It is intentionally exhaustive. The file ledger and test-contract catalogs are generated from commit 7d19c725, not reconstructed from memory.

### Completion language used in this dossier

- **Implemented** means the repository contains the behavior and automated proof.
- **Repository verified** means the cited automated suite passed on the isolated candidate.
- **Historically observed live** means a production-shaped or live fact was supplied or directly observed earlier, but was not repeated against this candidate.
- **External pending** means the carrier, deployed server, payment provider, live pager, legal owner, product owner or another external authority must supply evidence.
- **NO-GO** means ingress must remain disabled until the external evidence exists.
- An unchecked goal is never silently converted into “done.”
- A deferred goal is never described as shipped.
- A test name is evidence of a contract, not evidence of a live carrier call.
- A successful build is evidence of artifact construction, not evidence of deployment.
- A successful originate request is not evidence of two-way media.
- A database row is not proof that a PBX channel is absent.
- A provider control response is not proof that billing settlement is correct.
- A dashboard render is not proof of cross-tenant isolation without backend enforcement.
- A production observation from an older candidate is not attributed to the new candidate.
- No secrets, keys, tokens, environment-file values or unredacted credentials are reproduced here.

### Inbound plan: requirement-by-requirement traceability

#### RLS-blind access remediation

- The live calls endpoint no longer uses an unscoped pooled acquisition.
- Call-state updates now execute with explicit tenant context.
- Stream-event writes now execute with explicit tenant context.
- Stream-event reads now execute with explicit tenant context.
- Month-to-date minutes reads now execute with explicit tenant context.
- Call-guard usage reads now execute with explicit tenant context.
- BYOK credential resolution now executes with explicit tenant context.
- Recording-policy resolution now executes with explicit tenant context.
- Platform-internal work uses an explicit bypass context rather than accidental privilege.
- SET LOCAL is wrapped in a transaction.
- Tenant context cannot leak when the pooled connection is returned.
- The tenant identifier is validated before it reaches the database context.
- A nil UUID sentinel is used only for explicit internal work.
- The protected-table inventory includes inbound rejection records.
- The AST test rejects new bare pool acquisition patterns.
- The AST allowlist is intentionally empty.
- The stale-allowlist test prevents permanent exceptions from accumulating.
- Zero-row state updates are rechecked on the same connection.
- A visible row is classified as an ordinary duplicate/replay.
- An invisible row is treated as an RLS warning condition.
- The quota repair intentionally restores enforcement that had failed open.
- The credential repair intentionally exposes stale tenant BYOK failures.
- Operations must reconcile existing tenant allocations before release.
- Operations must validate stored BYOK credentials before release.
- Production evidence supplied for the original fault was zero rows without context versus 1,041 rows with context.
- That production observation proves enforcement was active; it does not prove this branch was deployed.

#### Deploy preflight and atomicity

- CMake presence is checked before checkout.
- CTest presence is checked before checkout.
- A C++ compiler is checked before checkout.
- Candidate Python imports are checked from isolated candidate code.
- Candidate gateway build occurs before the release is promoted.
- The negative startup proof requires the gateway to fail closed without its token.
- The build does not silently fall back to an older compiler path.
- A missing toolchain aborts before the production checkout changes.
- Migration execution remains ordered after candidate validation.
- Service restart remains ordered after migration.
- Gateway build identity is compared to the candidate commit.
- The deploy does not treat a hand-built unknown binary as current.
- Trunk status refresh is non-fatal after service restart.
- The trunk status timer remains responsible for retry.
- Synthetic timer configuration is required before production deploy.
- The synthetic timer is restarted after deployment.
- The synthetic timer must report active after deployment.
- A failed final assertion leaves ingress frozen.
- New disk state with old processes is no longer accepted as a successful release.
- A later reboot cannot be relied upon as a deployment mechanism.
- The release host must retain the complete preflight output as evidence.
- This Windows workstation did not provide native C++ compilation proof.
- Linux-host CMake, CTest and compiler execution remain mandatory.

#### Dialplan durability

- The working inbound block is represented by repository-managed configuration.
- Provisioning does not overwrite extensions.conf wholesale.
- A candidate dialplan is generated before replacement.
- Candidate versus reviewed-live comparison is fail closed.
- Per-tenant routing context is preserved.
- Carrier-account-to-DID mapping is treated as load-bearing configuration.
- PJSIP metadata carries the dialled DID to the adapter.
- The adapter reads the DID from the trusted metadata path.
- Ringing is sent before Stasis.
- The broad underscore-dot route remains because it catches the Asterisk s extension.
- The underscore-X-dot replacement is not made without carrier evidence.
- Cosmetic duplicate contexts are not mixed into the production-critical change.
- Running setup cannot erase the only working DID route.
- A second tenant does not require a host-only transcription.
- The actual carrier mapping still requires carrier/server confirmation.
- A byte-for-byte candidate match does not prove that the carrier sends the expected destination.
- Real INVITE capture remains part of the live gate.

#### DID routing and tenant selection

- DID normalization produces one canonical E.164 representation.
- SIP URI wrappers are removed before lookup.
- Telephone URI wrappers are removed before lookup.
- Formatting characters do not create a second identity.
- Invalid short numbers are rejected.
- Overlong numbers are rejected.
- Anonymous destinations are rejected.
- Missing destinations are rejected.
- The route query addresses inbound DID assignments.
- The route query joins the active inbound configuration.
- The route query joins the tenant-owned phone number.
- The route query requires the tenant inbound switch.
- The route query requires an active campaign status.
- The query reads at most two candidates to detect ambiguity.
- Zero candidates return unknown DID.
- Two candidates return ambiguous DID.
- Context can confirm a tenant but cannot choose one.
- A context-versus-DID tenant mismatch returns tenant conflict.
- An incomplete binding is rejected.
- No latest-campaign fallback exists.
- No default-tenant fallback exists.
- No backup AI tenant fallback exists.
- Database dependency failure returns routing dependency unavailable.
- Raw DID digits are not placed in ordinary route logs.
- Stable redacted DID identifiers support correlation.
- Route/config versions are pinned.
- The two-DID test resolves a US DID to tenant A.
- The two-DID test resolves a UK DID to tenant B.
- The test asserts different campaign identities.
- The test asserts different phone-row identities.
- Automated two-tenant proof does not replace real carrier delivery.

#### Pre-answer admission

- Routing completes before answer.
- Platform inbound enablement is checked before answer.
- Tenant inbound enablement is checked before answer.
- Tenant subscription status is checked before answer.
- Tenant usage allocation is checked before answer.
- Campaign maximum duration is checked before answer.
- The reservation duration is bounded to remaining quota.
- Global concurrency is checked before answer.
- Tenant concurrency is checked before answer.
- Trunk health freshness is checked before answer.
- AI configuration completeness is checked before answer.
- Business schedule validity is checked before answer.
- Timezone validity is checked before answer.
- After-hours action validity is checked before answer.
- Transfer readiness is checked before answer when transfer is selected.
- Recording policy is pinned before answer.
- Opening mode is pinned before answer.
- Opening message is pinned before answer.
- Prompt identity is pinned before answer.
- Voice identity is pinned before answer.
- Knowledge content is pinned before answer.
- Public ANI is retained only after tenant ownership is known.
- Private/invalid ANI does not create an identity.
- Unknown unowned rejection does not retain caller ANI.
- Provider call identity is idempotently replayed.
- Terminal provider replay is rejected.
- Admission writes the durable call row before answer.
- Admission reserves quota before answer.
- Admission owns a renewable lease.
- Lease and heartbeat limits are validated.
- Missing required policy fails closed.
- Database/Redis dependency uncertainty fails closed for inbound admission.
- Denied calls remain unanswered and unbilled.
- Speaking a custom denial would require answering and billing the leg.
- Standard SIP cause signaling is therefore used for pre-answer denials.

#### Caller signaling

- Ringback starts immediately before Stasis.
- Ringback continues while admission runs.
- Answer ends ringback and supplies the pickup cue.
- Unknown DID uses not-found semantics.
- Tenant conflict uses not-found semantics.
- Unverified DID uses not-found semantics.
- Transient infrastructure failure uses temporary-failure semantics.
- Stale trunk readiness uses temporary-failure semantics.
- Admission timeout uses temporary-failure semantics.
- Tenant capacity uses busy semantics.
- Capacity does not use temporary failure, avoiding carrier retry storms.
- Inactive subscription uses terminal decline semantics.
- Tenant-disabled inbound uses terminal decline semantics.
- Insufficient minutes uses terminal decline semantics.
- Invalid account configuration uses terminal decline semantics.
- Reasoned ARI hangup is attempted first.
- Any reasoned-hangup ARI error triggers immediate bare hangup.
- Bare hangup remains the fail-closed absence action.
- Outbound and post-answer teardown keep their prior default behavior.
- Denial timing is measured as admission_elapsed_ms.
- Answer-to-first-audio timing is measured.
- First audio is counted only after the gateway accepts an agent packet.
- Queueing bytes locally is not reported as caller-heard audio.
- The live canary must establish the approved latency threshold.
- A cold provider path must be included in the live latency proof.

#### Opening behavior

- Caller-first remains supported.
- Agent-first remains supported.
- Caller-first never receives outbound framing.
- Agent-first uses the configured inbound opening.
- The current live successful call was caller-first.
- Agent-first is therefore treated as a separately reversible release change.
- The configured greeting is carried in the immutable admission snapshot.
- An agent-first opening can be interrupted.
- Interruption flushes stale opening audio.
- The opening policy is direction-aware.
- AI message intake uses receptionist language rather than sales framing.
- AI message intake is not called a voicemail mailbox.
- Recovered inbound outcomes remain deterministic without a live session.
- The final live configured-greeting criterion remains open.
- Answer-to-first-audio must be proven against the production carrier path.

#### Provider-independent voice failure handling

- Ordinary TTS uses the configured provider.
- Pre-first-audio TTS is bounded rather than waiting indefinitely.
- First provider failure reaches an independent raw-audio path.
- The first emergency clip apologizes and asks the caller to hold.
- The second emergency clip apologizes and asks the caller to call back.
- The second occurrence terminates the call.
- Emergency clips are pre-rendered.
- Production does not synthesize emergency clips at runtime.
- Emergency clips are checksum pinned.
- Emergency clips use raw PCMU.
- Emergency clip frame size is validated.
- Emergency clip pacing follows the media clock.
- Emergency playback is barge-in aware.
- Emergency playback waits for gateway drain.
- Emergency audio can be included in a recording only when recording is allowed.
- The emergency path bypasses the failed provider’s output format.
- The emergency path bypasses the failed provider’s send function.
- A provider-format mismatch cannot reinterpret the clip bytes.
- The first clip is 43,200 bytes.
- The first clip represents approximately 5.4 seconds at 8 kHz PCMU.
- The second clip is 40,800 bytes.
- The second clip represents approximately 5.1 seconds at 8 kHz PCMU.
- Asset hashes are verified before use.
- Missing/corrupt emergency assets fail visibly.
- Live RTP/recording proof remains required.

#### Media gateway architecture

- Carrier SIP/RTP terminates at Asterisk.
- Asterisk owns PJSIP and channel state.
- ARI controls bridge and externalMedia lifecycle.
- The C++ gateway owns the real-time RTP boundary.
- Python owns tenant, campaign, prompt and conversation policy.
- The gateway does not choose a tenant.
- The gateway does not choose a campaign.
- The gateway does not perform billing settlement.
- The gateway does not decide business hours.
- The gateway receives PCMU from the Asterisk external-media leg.
- The gateway forwards caller audio to the authenticated Python bridge.
- The Python pipeline sends accepted agent audio back to the gateway.
- The gateway paces RTP toward Asterisk.
- Asterisk returns audio to the carrier.
- Control access requires the internal token.
- Callback origin is pinned.
- Session allocation is bounded.
- Codec declarations are validated.
- Invalid startup configuration exits nonzero.
- The binary exposes build identity.
- Deployment compares running identity with the release candidate.
- Shared inbound/outbound media code retains direction-specific policy above it.
- The architecture is not a workaround.
- The old manually built, unversioned artifact was an operational workaround.
- Repository build identity and deployment checks remove that workaround.
- Native Linux execution remains a release-host proof.

#### Codec decision

- PCMU remains the PSTN-facing default when the carrier negotiates PCMU.
- PCMA can be handled only when the carrier/trunk contract requires it.
- Opus is not forced onto a non-Opus PSTN leg.
- WhatsApp’s codec behavior is not assumed to apply to ordinary SIP/PSTN.
- Wideband synthesis does not make an 8 kHz PSTN hop wideband.
- Internal resampling occurs only at a controlled provider boundary.
- Unnecessary decode/encode cycles are avoided.
- Codec changes require negotiation evidence.
- Codec changes require two-way audio evidence.
- Codec changes require packet-loss/jitter/clipping measurements.
- One-way audio is an immediate rollback trigger.
- Unsupported advertisement is considered a failure, not an optimization.
- Opus remains appropriate for a real WebRTC/SIP endpoint that negotiates it end to end.
- Transcoding cost and latency must be measured before any Opus bridge is enabled.

#### Call-state persistence

- Admitted INVITE creates an initiated call row.
- Answer transitions the row to in progress.
- Answered time is persisted as a database datetime.
- End transitions the row to ended.
- Ended time is persisted.
- Duration is calculated from evidence.
- Completed-versus-ended drift remains an operator canary.
- RLS cannot silently convert state updates into ordinary duplicate handling.
- Provider terminal events are idempotent.
- Reordered terminal events remain monotonic.
- Operator hangup requires authoritative absence proof.
- A control request alone is not a terminal event.
- An unconfirmed hangup leaves the call retryable.
- Confirmed absence precedes settlement.
- Confirmed absence precedes final projection.
- Terminal replay still checks PBX proof.
- A terminal database label is not PBX proof.
- Transfer child state is reconciled before parent completion.
- Settlement uncertainty is exposed rather than guessed.

#### Billing and quota

- Inbound reserves a maximum billable window before answer.
- Reservation is tenant scoped.
- Reservation is call scoped.
- Final settlement is idempotent.
- Release is idempotent.
- Reversal is a compensating ledger entry.
- Ledger rows are not silently rewritten.
- Duration beyond reservation is held.
- Ambiguous Answer evidence is held.
- Settlement-switch closure creates a hold.
- Held rows require explicit reconciliation.
- Manual charge resolution is platform-admin scoped.
- Charge approval uses four-eye controls.
- Evidence identifiers are required.
- Parent and transfer-child usage are distinct.
- Every billable transfer leg has typed linkage.
- Unknown carrier cost stays unknown rather than becoming zero.
- Duplicate/reordered PBX events do not duplicate minutes.
- Stale reservation recovery requires PBX evidence.
- Repaired RLS usage reads re-arm quota enforcement.
- One tenant cannot deduct another tenant’s minutes through the scoped path.
- A production billing reconciliation remains mandatory.
- Month-to-date allocations must be reviewed before deployment.

#### Transfer

- Transfer is code-owned off in production by default.
- Runtime capability is a separate gate.
- Platform enablement is a separate gate.
- Campaign configuration is a separate gate.
- Staging proof scope requires an exact tenant UUID.
- Staging proof scope requires an exact config UUID.
- Transfer destination must be explicitly allowlisted.
- Arbitrary destination injection is rejected.
- Non-blind modes outside scope are rejected.
- Transfer authorization is persisted before adapter use.
- Idempotency replay cannot create a second child leg.
- Changed request with the same key is rejected.
- Completed replay returns the stored result.
- Child reservation is bounded to the parent deadline/quota.
- Over-reservation is rejected.
- Cleanup-pending state preserves live ownership.
- Restart reconciliation does not guess a pre-answer result.
- Provider-proved failure releases once.
- Provider-proved answer preserves billable state.
- Parent finalization handles answered child legs.
- Transfer status projection is tenant scoped.
- The production live success criterion remains open.
- Transfer must remain disabled unless the signed live proof is in release scope.

#### After-hours behavior

- Business windows are start inclusive.
- Business windows are end exclusive.
- Overnight windows carry into the following day.
- Campaign timezone is applied before weekday/time evaluation.
- Closed holidays override the weekly window.
- Invalid timezone fails closed.
- Invalid schedule fails closed.
- Hangup action remains supported.
- AI message intake remains supported.
- Transfer action remains supported only behind gates.
- Hangup remains unanswered.
- AI message intake is a normal AI conversation.
- AI message intake stores the ordinary transcript/outcome.
- AI message intake has no beep.
- AI message intake has no one-way mailbox recording.
- AI message intake has no voicemail inbox.
- AI message intake has no playback workflow.
- A true voicemail feature remains separate future work.
- After-hours routing is included in operator rejection/history logic.
- Live after-hours carrier proof remains required.

#### Recording and consent

- Recording defaults off.
- Recording policy is tenant scoped.
- Recording policy is read under tenant context.
- Unreadable policy does not silently enable recording.
- Recording requires the disclosure/consent contract.
- A schema check prevents recording without required consent wording.
- Audio buffers remain closed until policy permits recording.
- Emergency clips respect recording policy.
- Consent state appears in the live operator view.
- Recording storage remains call/tenant linked.
- Recording deletion/retention controls remain permission guarded.
- Legal/privacy wording is an owner decision.
- Jurisdiction-specific approval is external pending.
- Live consent ordering must be proven before recording enablement.

#### Rejected inbound records

- Early denials are persisted.
- Rejection records are tenant scoped when ownership is known.
- Unowned unknown-DID records do not retain ANI.
- Provider/provider-call identity is idempotent.
- Rejection reason is durable.
- Rejection stage is durable.
- Redacted DID correlation is durable.
- Rejection rows are immutable except approved retention deletion.
- RLS applies to rejection rows.
- The rejected-calls API is tenant scoped.
- The rejected-calls result includes early denials.
- The rejected-calls result includes admitted after-hours calls.
- After-hours calls are not incorrectly treated as pre-row denials.
- Operator UI displays a healthy empty state.
- Operator UI displays backend failure rather than an empty success.
- Private ANI stays hidden.
- The view does not expose cross-tenant rows.
- Migration 0036 creates the durable structure.
- Production migration 0036 remains pending.

#### Live operator view

- Direction is returned by the backend.
- Caller ANI is returned only under tenant/privacy rules.
- Called DID is returned.
- Admission status is returned.
- Consent status is returned.
- The frontend type carries these fields.
- The live panel displays these fields.
- The live panel distinguishes inbound and outbound.
- Hangup UI remains confirmation aware.
- The UI does not optimistically mark a call ended.
- Unconfirmed hangup exposes a retryable error.
- Terminal polling clears the pending state.
- Empty success and error states are distinct.
- RLS repair makes the endpoint visible under tenant context.
- A live production call must still prove the row appears and ends correctly.

#### Frontend inbound campaign work

- The primary dashboard has an Inbound navigation entry.
- Inbound list route exists.
- Inbound create route exists.
- Inbound detail route exists.
- Inbound edit route exists.
- List page distinguishes loading, empty, success and error.
- Create page uses the confirmed server contract.
- Edit page carries the optimistic version token.
- Detail page exposes lifecycle/readiness information.
- Inbound form requires a verified DID.
- Inbound form requires an AI campaign/agent binding.
- Inbound form requires an eligible inbound trunk.
- Inbound form includes campaign name.
- Inbound form includes DID/SIP assignment.
- Inbound form includes agent/prompt selection.
- Inbound form includes voice selection.
- Inbound form includes timezone.
- Inbound form includes business hours.
- Inbound form includes after-hours behavior.
- Inbound form includes opening mode.
- Inbound form includes greeting/opening message.
- Inbound form includes transfer destination.
- Inbound form includes recording enablement.
- Inbound form includes recording disclosure.
- Inbound form includes qualification/outcome configuration.
- Form validation requires E.164 transfer destination.
- Form validation requires disclosure when recording is enabled.
- UI exposes only runtime-backed after-hours actions.
- AI message intake fails closed without its pinned opening.
- Transfer policy cannot be newly enabled when runtime proof is incomplete.
- Neutral qualification defaults do not block readiness.
- Create requests carry idempotency keys.
- Ambiguous retry reuses the same key.
- A new operation receives a new key.
- Expired ambiguous retry is blocked before claim rollover.
- Update uses the server PUT contract.
- Assignment uses the explicit audited endpoint.
- Archive uses the explicit lifecycle endpoint.
- Active campaign must be paused before archive.
- Archived inventory is fetched only for archived view.
- Permission discovery is fail closed.
- Platform admin bypass is explicit.
- Recording permissions remain independent of broader call permissions.
- DID inventory excludes unverified numbers.
- DID inventory excludes assigned numbers.
- Trunk inventory exposes only runtime-ready inbound trunks.
- Transfer capability refresh failure closes the cached capability.
- Query cache updates do not rewrite capability objects.
- Server conflict messages remain actionable.
- Loading state is not rendered as empty data.
- Error state is not rendered as zero data.
- Healthy empty state remains an ordinary empty result.

#### Frontend operator work

- Campaign live-calls panel shows direction.
- Campaign live-calls panel shows caller ANI.
- Campaign live-calls panel shows called DID.
- Campaign live-calls panel shows admission state.
- Campaign live-calls panel shows consent state.
- Rejected inbound panel exists.
- Rejected panel displays durable denials.
- Rejected panel displays after-hours call records.
- Rejected panel omits private ANI.
- Rejected panel has loading state.
- Rejected panel has error state.
- Rejected panel has empty state.
- Call list preserves inbound direction.
- Call detail preserves caller/DID parties.
- Live hangup waits for confirmation.
- Live hangup prevents duplicate action while pending.
- Structured provider errors reach the operator.
- A failed permission lookup is not shown as an ordinary refusal.
- Frontend request parsing validates the production response envelope.
- DID masking is applied in client display helpers.
- Dashboard query invalidation follows confirmed server mutations.
- User-visible text avoids unproven voicemail/transfer/reward claims.

#### Admin frontend work

- Admin has an inbound control page.
- Platform/tenant control state is visible.
- Live call data is truthful.
- Call-history data is truthful.
- Inbound/outbound direction is represented.
- Call cost does not invent inbound carrier cost.
- Termination UI is confirmation aware.
- Termination uses a reason and idempotency contract.
- Admin role guard remains explicit.
- Usage views preserve counts while excluding unknown inbound cost.
- Recording controls respect permission and legal-hold states.
- The production TypeScript build passes.
- The Vite artifact build passes.
- Admin live deployment validation remains pending.

#### Synthetic liveness and alerting

- Successful inbound counter increments only after accepted agent audio.
- Last-success timestamp records accepted agent audio.
- Originate acceptance alone does not mark the system healthy.
- The Prometheus alert checks timestamp staleness.
- The threshold is 5,400 seconds.
- The alert has a 15-minute for duration.
- The synthetic timer runs hourly.
- The script validates DID shape.
- The script validates the trunk endpoint.
- The script does not eval environment content.
- The script does not source untrusted configuration.
- Asterisk originates through the configured PJSIP endpoint.
- The synthetic path requires a dedicated DID.
- The synthetic campaign must be always open.
- The synthetic campaign must be agent first.
- Correct after-hours behavior must not look like an outage.
- The deploy checks the synthetic environment file.
- The deploy restarts the timer.
- The deploy checks timer activity.
- A real Alertmanager receiver is not yet configured.
- A real pager-delivery test is external pending.
- A real carrier-provisioned synthetic DID is external pending.
- Dashboard-only alert visibility is insufficient for production acceptance.

#### Test and build evidence

- Consolidated inbound/Asterisk/gateway/RLS/emergency suite passed 1,511 tests.
- Router suite passed 20 tests after adding the second-DID proof.
- Full backend unit/security suite passed 8,026 tests.
- Full backend suite skipped 7 environment-dependent tests.
- Full backend suite had zero failures.
- Full backend suite reported 1,312 non-failing warnings.
- Post-format affected suite passed 68 tests.
- Talk-Leee TypeScript typecheck passed.
- Talk-Leee unit runner passed 302 tests.
- Talk-Leee skipped 2 database-dependent tests.
- Talk-Leee had zero test failures.
- Admin TypeScript/Vite production build passed.
- Admin transformed 1,764 modules.
- Native C++ build was not available on this workstation.
- Linux-host C++ build remains mandatory.
- Live carrier SIP/RTP proof was not run.
- Live deployment proof was not run.
- Live pager proof was not run.
- Live 300-call proof was not run.
- Live two-hour soak was not run.
- Test counts overlap where focused suites are subsets of full suites.
- Counts are not added together to invent a larger unique total.

#### Goals cross-verification outcome

- The literal audited checklist contains 532 items.
- 213 items are evidence backed.
- 319 items remain unchecked.
- Inbound MVP is 29 of 31.
- Generic prompt backend is 10 of 10.
- Interested-lead capture is 22 of 24.
- Expanded contacts is 38 of 40.
- Billing top-up checklist is 21 of 21.
- Security sidebar is 21 of 22.
- Review/reward is 44 of 51.
- Tooltips are 20 of 22.
- Salesforce remains 0 of 23.
- The 200-tenant validation remains 0 of 143 because the live evidence run was not supplied.
- Historical delivery-calendar items are not retroactively checked.
- Human daily-management items require human records.
- Product-owner acceptance is not inferred.
- Legal/compliance approval is not inferred.
- Carrier approval is not inferred.
- A complete code candidate does not complete external acceptance goals.
- The exact audited checklist snapshot follows below.

## Appendix A — complete audited goals.md snapshot

The following is the complete candidate checklist as audited on 2026-08-31. It is embedded verbatim so every checked and unchecked goal is carried inside this report.

<!-- BEGIN AUDITED GOALS SNAPSHOT -->

# Talk-lee Product Delivery Checklist

**Delivery target:** September 10, 2026
**Planning start:** August 22, 2026
**Team:** Two developers, with agent testing and prompt tuning running in parallel
**Release rule:** Freeze the release candidate on September 8; September 9 is validation and rollback rehearsal; September 10 is controlled release.

> **Execution audit — 2026-08-31:** 213 of 532 literal checklist items are
> evidence-backed; 319 remain open. Inbound MVP is 29/31, prompt backend is
> 10/10, interested-lead capture is 22/24, and expanded contacts is 38/40.
> Unchecked items must not be treated as implicit passes: they include live
> carrier/payment/CRM tests, the 200-tenant and soak evidence, owner approvals,
> historical team-process milestones and remaining product work. The proof,
> premortem and exact production gates are in
> `docs/sessions/reports/report14.md`.

## 1. Ownership

### Developer A — Backend, Voice and Integrations

- Campaign, call and review APIs
- Feedback/reward ledger and abuse controls
- Inbound call routing and session creation
- Generic lead-generation prompt rendering
- Lead-information capture and structured call outcomes
- Contact schema and campaign-variable mapping
- Billing top-up APIs and payment-provider webhook handling
- Salesforce OAuth and MVP synchronization
- Security-page backend endpoints
- Database migrations, audit logs, tests and observability

### Developer B — Frontend and Product Experience

- Conversation review panel
- Reward display and review history
- Security section moved into the main sidebar
- Inbound campaign creation and management pages
- Interested-lead details panel/form
- Information tooltips and popovers
- Token/creativity explanations in AI Options
- AI Summary explanation popovers
- Billing top-up interface
- Salesforce connection interface
- Expanded contacts form, table, import template and validation
- Frontend tests, loading states, empty states and error handling

### Parallel QA/Prompt-Tuning Track

- Freeze one prompt version for every test batch
- Run controlled lead-generation calls
- Review recordings and transcripts
- Label conversation problems
- Score the agent against an agreed rubric
- Change only one important prompt behavior per experiment
- Record prompt version, runtime configuration and test result

---

## 2. Priority and Scope

### P0 — Must be ready by September 10

- [x] Generic lead-generation prompt connected to live campaign runtime
- [x] Prompt version and hash visible in call logs
- [x] Expanded contact fields available to the agent
- [x] Structured interested-lead information capture
- [x] Per-conversation review and feedback storage
- [x] Security moved from Settings to the main sidebar
- [ ] Inbound campaign MVP
- [x] AI Options and AI Summary information tooltips
- [x] Billing minute top-up MVP
- [ ] Client-management and multi-tenant validation with 200 test clients
- [ ] End-to-end test and controlled release

### P1 — Deliver as MVP if P0 remains healthy

- [ ] Review reward points/credits
- [ ] Salesforce OAuth connection
- [ ] One-way Talk-lee-to-Salesforce lead/contact synchronization
- [ ] Review analytics dashboard

### P2 — Do not block September 10

- [ ] Automatic AI fine-tuning from feedback
- [ ] Cash rewards or withdrawable rewards
- [ ] Full bidirectional Salesforce synchronization
- [ ] Salesforce opportunity, task and campaign synchronization
- [ ] Advanced inbound IVR and multi-level call flows
- [ ] Fully automated prompt deployment based only on user reviews

---

## 3. Conversation Review and Reward System

### Backend

- [x] Create a `conversation_reviews` table.
- [x] Store `review_id`, `tenant_id`, `user_id`, `call_id`, `campaign_id`, `rating`, `review_tags`, `comment`, `created_at` and `updated_at`.
- [x] Add structured tags:
  - [x] Agent did not understand
  - [x] Agent interrupted caller
  - [x] Agent did not answer the question
  - [x] Response was too long
  - [x] Response was too slow
  - [x] Agent repeated itself
  - [x] Wrong qualification question
  - [x] Wrong call outcome
  - [x] Poor objection handling
  - [x] Incorrect information
  - [x] Good conversation
- [x] Allow one active review per user per call; edits update the same review.
- [x] Verify the reviewer belongs to the call's tenant.
- [x] Prevent users from reviewing calls they cannot access.
- [ ] Record prompt version, model, campaign and call trace with the review.
- [x] Create a review-reward ledger rather than directly changing balances.
- [x] Make rewards idempotent so repeat submissions cannot create duplicate credits.
- [ ] Add daily reward limits and suspicious-activity monitoring.
- [x] Do not reward an empty review unless a simple rating is intentionally eligible.
- [ ] Add admin controls to enable, disable and configure reward amounts.
- [ ] Add API tests for tenant isolation, duplicate rewards and unauthorized calls.

> ### ⚠️ TICK AUDIT, 2026-08-24 — four ticks were wrong and have been reversed
>
> Re-verified every tick against the code rather than against memory. Four did
> not survive. They are listed here rather than quietly flipped, because a
> checklist nobody can trust is worse than no checklist.
>
> - **"Record prompt version, model, campaign and call trace"** — reversed.
>   `prompt_template`, `prompt_version`, `prompt_hash` and `campaign_id` ARE
>   written. **`llm_model` is not.** The column exists (migration 0015 line 123)
>   but the `INSERT` never populates it, and `calls` has no model column to
>   source it from. It cannot be honestly completed by reading the tenant's
>   *current* model at review time: the model may have changed since the call,
>   so that would attribute an old call to a model it never ran on. Completing
>   this needs `calls.llm_model` captured at call time — a migration plus a
>   write-path change.
> - **"Add API tests for tenant isolation, duplicate rewards and unauthorized
>   calls"** — reversed. `test_conversation_reviews.py` has 24 tests and they
>   are all pure-function validation: tag vocabulary, rating bounds, comment
>   handling, reward eligibility. **None of the three things this line names is
>   tested.** True API-level tests are also blocked by the httpx/starlette
>   TestClient mismatch (#79).
> - **"Display confidence or 'needs review'"** (§8) — reversed. There is no
>   `confidence` field anywhere in the summary payload. A provenance banner was
>   added, which is a different thing; inventing a confidence number from
>   nothing would be worse than showing none.
> - **"Popovers do not cover save buttons or critical fields"** (§8) — reversed.
>   Radix collision handling makes this *likely*, but it was never checked on a
>   real narrow screen, and "likely" is not "done".
>
> **What was fixed rather than reversed:** "loading, retry and permission-error
> states" was ticked with no retry anywhere in the panel. A **Try again** button
> now sits in the error state — the form still holds every word, so the only
> thing missing was a way to send them again. That tick now stands.
>
> **Status of the two unticked items (2026-08-23).** Both are partly built and
> deliberately not ticked:
>
> - *Daily limits and suspicious-activity monitoring* — the daily cap is
>   enforced (`reward_daily_cap()`, per user per UTC day, logged as
>   `review_reward_daily_cap_reached`). There is no separate monitoring or
>   alerting beyond that log line.
> - *Admin controls for reward amounts* — configurable, but through environment
>   variables (`REVIEW_REWARDS_ENABLED`, `REVIEW_REWARD_POINTS`,
>   `REVIEW_REWARD_DAILY_MAX`), not a UI. Rewards are OFF by default. Both are
>   P1 concerns and neither blocks review capture, which works with rewards
>   disabled.

### Frontend

- [x] Add a **Review conversation** section to every completed call page/drawer.
- [x] Show recording and transcript beside the review form when available.
- [x] Add 1–5 rating or thumbs-up/thumbs-down control.
- [x] Add multi-select problem tags.
- [x] Add an optional written comment.
- [x] Show reward eligibility before submission.
- [x] Show a clear confirmation after successful submission.
- [x] Allow review editing without issuing a second reward.
- [x] Add loading, retry and permission-error states.
- [x] Add accessibility labels and keyboard navigation.
- [x] Put the feedback controls **beside the recording's play button**, not only
      on the call page.
- [x] Offer all three response types on every recording: **thumbs up/down, a
      voice note, and typed text**.
- [x] Hard-cap a feedback voice note at **30 seconds**.
- [x] Surface submitted reviews in the **admin panel** (`/admin/reviews`).

> **Where reviewing actually happens (2026-08-24).** Three ways to answer a
> recording, sitting on the recording itself, because they cost different
> amounts and carry different amounts of information — force one shape and
> people use none:
>
> - **Thumbs up / down** — one click, says only better/worse, but it is the one
>   people will actually give while working down a list. Thumbs-down writes
>   rating 2, which puts the call in the "needs listening" queue (1s and 2s);
>   1 stays available to mean something worse, chosen deliberately in the panel.
> - **A voice note, capped at 30 seconds** — the fastest way to say something
>   nuanced ("she'd already said no twice and it kept pitching"), which is
>   exactly the feedback nobody types. The recorder stops itself at 30s, so it
>   is a cutoff rather than a warning, and the server validates it too.
> - **Typed text** — the only option when you are somewhere you cannot talk.
>
> Thumb and text write `conversation_reviews` (rating + comment, one review per
> user per call). The voice note writes `call_feedback` — one note per call,
> stored durably then transcribed. Kept separate because a voice note has a
> lifecycle (upload, transcribe, retry) and a review is a structured judgement.
>
> **None of them overwrites another.** `submitReview` is a PUT of the whole
> review, so a thumb resends the existing comment and tags untouched, and saving
> a comment resends the existing rating. Without that, whichever control you
> used last would silently erase the other.
>
> Live on the **Recordings** page (full bar under each player) and the **calls
> list** (thumbs only — the row is a fixed grid, and an expanding panel in an
> `auto` column would squash the other columns; the full panel is one click away
> on the call page).
>
> Reading everyone's reviews is a different job from leaving one, so the
> management view moved from a top-level `/reviews` route to `/admin/reviews`.
> Its endpoint was always `require_admin_tenant`; only the navigation disagreed,
> which meant a non-admin who clicked it got a bare 403. `/reviews` now
> redirects, preserving existing links.

### Safe Improvement Loop

- [x] Do not let a single review automatically rewrite the production prompt.
- [x] Aggregate reviews by prompt version and failure category.
- [x] Manually verify low-rated calls against recordings/transcripts.
- [ ] Convert verified problems into evaluation cases.
- [ ] Test candidate prompts against the evaluation set.
- [ ] Deploy a prompt only after human approval and canary testing.
- [x] Retain rollback access to the previous prompt version.

### Acceptance Criteria

- [x] A valid user can review an accessible completed call.
- [x] Unauthorized users receive no call or review data.
- [x] A call cannot generate duplicate rewards for the same reviewer.
- [x] Review edits preserve the original reward transaction.
- [x] Admin can filter results by campaign, prompt version, rating and tag.
- [x] Review submission does not change production prompts automatically.

---

## 4. Security as a Main Sidebar Section

### Frontend

- [x] Add **Security** to the main left navigation.
- [x] Remove or redirect the old Security entry inside Settings.
- [x] Preserve deep links and bookmarks with a route redirect.
- [x] Display only security controls the current role can manage.
- [x] Include sections for:
  - [x] Password/account security
  - [x] Multi-factor authentication status
  - [x] Active sessions
  - [x] API keys/tokens
  - [x] Audit activity
  - [ ] Allowed IPs, if supported
  - [x] Data retention and recording controls, if supported

> **Built 2026-08-24 — `/security`, reachable from the sidebar.**
>
> Password change (which signs out every other session), passkeys, 2FA status
> with turn-off and recovery-code rotation, and active sessions. API keys and
> audit activity appear only for admins — and the backend enforces that
> independently, so hiding them is convenience, not the boundary.
>
> **"Allowed IPs" is left unticked because it is not supported.** There is no IP
> allow-list anywhere in the backend. The page says so in as many words rather
> than showing an empty panel that implies a control exists — a security page
> that overstates what it enforces is worse than one that admits a gap.
>
> The Settings → Security tab is now a pointer to `/security`. The controls were
> *moved*, not copied: MFA disable and recovery-code regeneration went with
> them, so the same panel cannot exist in two files and drift apart.
>
> Retention is shown read-only, because it is set by plan rather than per user.

### Backend/Security

- [x] Reuse existing endpoints where possible.
- [x] Apply tenant and role authorization to every security endpoint.
- [x] Never return raw API secrets after creation.
- [x] Audit key creation, rotation, revocation and security-setting changes.
- [x] Add rate limiting to sensitive actions.
- [x] Add tests for viewer, partner-admin, tenant-admin and master-admin roles.

### Acceptance Criteria

- [x] Security is directly accessible from the left sidebar.
- [x] Old URLs redirect correctly.
- [x] Unauthorized controls are hidden and rejected by the backend.
- [x] Sensitive mutations appear in the audit log.

> **2026-08-31 verification note:** endpoint-auth, RBAC, admin-tenant-isolation,
> API-security/rate-limit and audit-log suites cover the checked backend items.
> “Allowed IPs” remains the sole unsupported control and is still presented as
> unavailable rather than as an empty or misleading security feature.

---

## 5. Inbound Campaign MVP

### Campaign Configuration

- [x] Add campaign type: `outbound` or `inbound`.
- [x] Add an **Inbound** section to the sidebar.
- [x] Allow users to create an inbound campaign.
- [x] Required settings:
  - [x] Campaign name
  - [x] Assigned phone number/SIP route
  - [x] Agent/prompt selection
  - [x] Voice selection
  - [x] Business hours and timezone
  - [x] After-hours behavior
  - [x] Greeting/opening message
  - [x] Human transfer destination
  - [x] Voicemail/fallback behavior
  - [x] Recording and disclosure policy
  - [x] Call outcome rules
- [x] Prevent the same inbound number from being actively assigned to conflicting campaigns.
- [x] Add activate, pause and archive actions.

### Runtime

- [x] Resolve incoming DID/SIP destination to tenant and inbound campaign.
- [x] Create the session with the correct tenant, campaign, prompt and voice.
- [x] Pass caller phone number as contact context where permitted.
- [x] Apply business-hours logic before starting the normal agent flow.
- [x] Support transfer failure and after-hours fallback.
- [x] Persist inbound direction, DID, caller ID, campaign ID and outcome.
- [x] Apply concurrency, quota and billing checks.
- [x] Add structured logs for routing decisions.

### Acceptance Criteria

- [x] A test number routes to exactly one correct tenant/campaign.
- [ ] The correct inbound agent answers with the configured greeting.
- [x] Calls outside business hours follow the configured fallback.
- [ ] Transfer success and failure are handled clearly.
- [x] Inbound minutes appear correctly in usage/billing.
- [x] Tenant A cannot see or route Tenant B's calls.

> **2026-08-31 verification note:** checked items above are backed by repository
> migrations, tenant-scoped services/UI, and automated tests (including the
> two-DID/two-tenant routing proof). They do not substitute for the frozen live
> carrier canary. The two unchecked criteria deliberately require that live
> evidence: first configured greeting audio on the production route, and an
> approved transfer success/failure exercise with transfer gates enabled.

---

## 6. Generic Lead-Generation Prompt: Implementation and Testing

### Backend Integration

- [x] Store the master template as `generic_lead_generation` with a version.
- [x] Do not use `You are a helpful AI assistant` for campaign calls.
- [x] Load the selected campaign's prompt and configuration from the database.
- [x] Render campaign variables before session creation.
- [x] Fail validation when required variables are missing.
- [x] Never send unresolved `{{variable}}` placeholders to the LLM.
- [x] Add lead-specific context separately from stable system instructions.
- [x] Log `campaign_id`, `prompt_template`, `prompt_version` and `prompt_hash`.
- [x] Keep prompt versions immutable after use; create a new version for changes.
- [x] Add rollback to the previous approved prompt version.

> **2026-08-31 verification note:** the prompt registry/composer, strict slot
> validation, durable prompt identity and archived-body rollback are covered by
> `test_prompt_versions.py`, `test_prompt_identity_persist.py` and
> `test_prompt_rollback.py`. The controlled-call matrix and release scorecard
> below remain open because no immutable 30-call evidence set was supplied.

### Prompt Test Matrix

- [ ] Opening and reason for calling
- [ ] Prospect says they are busy
- [ ] Prospect asks, "Why are you calling?"
- [ ] Prospect asks, "What does your company do?"
- [ ] Prospect asks about price
- [ ] Interested prospect
- [ ] Not interested
- [ ] Existing provider
- [ ] Send information by email
- [ ] Callback request
- [ ] Human transfer request
- [ ] Wrong number
- [ ] Do-not-call request
- [ ] Prospect interrupts the agent
- [ ] Prospect gives several business details in one answer
- [ ] Prospect provides incomplete information
- [ ] Normal successful booking
- [ ] Tool/booking/transfer failure

### Conversation Scorecard

- [ ] Direct questions answered before qualification
- [ ] One question asked at a time
- [ ] Responses normally limited to one or two sentences
- [ ] No repeated pitch after a clear rejection
- [ ] No fabricated facts or tool success
- [ ] Correct details captured
- [ ] Correct outcome selected
- [ ] Natural closing
- [ ] No talking over the caller
- [ ] No stale audio after interruption

### Release Gate

- [ ] At least 30 controlled calls on one frozen prompt/runtime version.
- [ ] At least five speakers and two accents.
- [ ] At least 95% of direct questions answered correctly.
- [ ] At least 95% correct call outcome classification.
- [ ] Zero ignored do-not-call requests.
- [ ] Zero fabricated bookings, transfers or prices.
- [ ] No old prompt used in any test call.
- [ ] Every call has a call ID, recording/transcript, prompt version and score.

---

## 7. Interested-Lead Information Form

### Data Model

- [x] Create a structured `lead_capture` or `call_lead_details` record linked to call, campaign, contact and tenant.
- [x] Support campaign-defined custom fields.
- [x] Field types: text, number, email, phone, date/time, single select, multi-select and notes.
- [x] Mark fields as required, optional, agent-visible and user-visible.
- [x] Record the source of each value: imported contact, caller statement, agent inference or manual edit.
- [x] Do not treat inferred values as confirmed facts.

### Agent Behavior

- [x] Supply required field definitions to the agent.
- [x] Let the agent extract fields from natural conversation.
- [x] Do not force the agent to ask for information already provided.
- [x] Confirm important contact and appointment information.
- [x] Use `unknown` when information was not provided.
- [x] Update structured fields after each confirmed detail or at call completion.

### Frontend

- [x] Show an **Interested lead** badge when interest is detected/confirmed.
- [x] Open a compact lead-information panel from the call page.
- [x] Display captured business/customer details in a readable form.
- [x] Highlight missing required fields.
- [x] Allow authorized users to correct or complete details.
- [x] Show who/what supplied each value.
- [x] Add save, validation and conflict handling.

### Acceptance Criteria

- [x] Information spoken once is captured without being asked again.
- [x] The form is linked to the correct tenant, call and contact.
- [x] Missing details remain visibly missing rather than being invented.
- [ ] Manual corrections are audited.
- [ ] Captured information is available to approved CRM synchronization.

> **2026-08-31 verification note:** migration `0020`, the tenant-scoped capture
> service, per-turn/teardown persistence, lead-details API and call-page panel
> prove the checked behavior. Manual edits retain `source=manual_edit`, but no
> separate actor/action audit event was found; CRM availability is also not an
> approved connector synchronization proof. Those two items stay unchecked.

---

## 8. Tooltips and Information Popovers

### Component

- [x] Build one reusable tooltip/popover component.
- [x] Support mouse hover, keyboard focus and mobile tap.
- [x] Add a short label plus optional "Learn more" content.
- [x] Avoid hiding essential warnings only inside tooltips.
- [x] Ensure the popup stays inside the viewport.

### AI Options

- [x] Add information help for **Tokens** explaining:
  - [x] Tokens are pieces of input/output text.
  - [x] Higher limits allow longer replies but may increase latency and cost.
  - [x] Voice-agent replies should normally remain short.
- [x] Add information help for **Creativity/Temperature** explaining:
  - [x] Lower values are more consistent and predictable.
  - [x] Higher values are more varied but may increase mistakes.
  - [x] Recommended lead-generation range is shown without silently changing it.

### AI Summary

- [x] Add hover/focus information for every main metric or conclusion.
- [x] Explain how the summary was generated.
- [x] Distinguish transcript facts from AI-inferred conclusions.
- [ ] Display confidence or "needs review" where appropriate.
- [x] Explain key terms such as qualified, interested, callback and unsuccessful.

### Acceptance Criteria

- [x] Every requested help icon works with mouse and keyboard.
- [x] Mobile users can open and close the same information.
- [x] Explanations use simple language.
- [ ] Popovers do not cover save buttons or critical fields.

---

## 9. Billing Minute Top-Up

### Backend

- [x] Define approved top-up packages and currency.
- [x] Create a top-up order before payment.
- [x] Use the payment provider's hosted checkout or secure payment flow.
- [x] Verify signed payment webhooks.
- [x] Make webhook processing idempotent.
- [x] Credit minutes only after verified successful payment.
- [x] Record money and minutes in an immutable billing ledger.
- [x] Handle failed, cancelled, duplicate, refunded and disputed payments.
- [x] Send receipt/confirmation according to configured channel.
- [x] Add admin reconciliation view or export.

### Frontend

- [x] Add **Top up minutes** to Billing.
- [x] Show current minute balance.
- [x] Show package minutes, price, currency and expiry rules.
- [x] Show payment status and top-up history.
- [x] Prevent double submission while checkout is starting.
- [x] Show clear failure and retry guidance.

### Acceptance Criteria

- [x] Successful verified payment credits minutes once.
- [x] Duplicate webhook does not duplicate minutes.
- [x] Failed/cancelled payment adds no minutes.
- [x] Tenant billing records remain isolated. — The 2026-08-31 production proof
      confirmed the application role no longer has `BYPASSRLS` (a bare
      connection saw zero call rows while the explicit tenant/bypass context
      saw 1,041). Tenant-aware pooled acquisition, RLS policies and the static
      invariant now protect billing/usage paths; live canary reconciliation is
      still required by the global release gate.
- [x] New balance is reflected in call quota enforcement.

---

## 10. Salesforce MVP

### September 10 Scope

- [ ] Add Salesforce as a connector.
- [ ] Implement OAuth authorization with secure state validation.
- [ ] Store tokens encrypted and tenant-scoped.
- [ ] Refresh access tokens safely.
- [ ] Allow the tenant to disconnect Salesforce.
- [ ] Map Talk-lee contact/lead fields to Salesforce Lead or Contact fields.
- [ ] Push qualified/interested leads to Salesforce.
- [ ] Store Salesforce object ID and synchronization status.
- [ ] Retry transient failures with bounded backoff.
- [ ] Send failures to a dead-letter/reconciliation queue.
- [ ] Prevent duplicate Salesforce records with a documented matching strategy.
- [ ] Add audit logs without exposing access tokens.

### Explicitly Deferred

- [ ] Bidirectional synchronization
- [ ] Salesforce opportunity creation
- [ ] Salesforce campaign membership
- [ ] Activity/task synchronization
- [ ] Complex custom-object mapping
- [ ] Historical bulk synchronization

### Acceptance Criteria

- [ ] Tenant can connect and disconnect Salesforce safely.
- [ ] One qualified test lead reaches the correct Salesforce account.
- [ ] Retrying the same event does not create an unintended duplicate.
- [ ] Authentication and API failures are visible to the tenant/admin.
- [ ] One tenant cannot access another tenant's Salesforce connection.

---

## 11. Expanded Contact Fields

### Canonical Contact Model

- [x] `first_name`
- [x] `last_name`
- [x] `full_name` as display/derived field where possible
- [x] `mobile_number`
- [x] `business_number`
- [x] `email`
- [x] `company_name`
- [x] `job_title` or role
- [x] `best_time_to_call`
- [x] `timezone`
- [x] `calling_notes`
- [x] `preferred_contact_method`, if needed
- [x] `do_not_call`
- [x] `custom_fields`

### Data Rules

- [x] Avoid storing duplicate conflicting `phone_number` and `mobile_number` values without defining a canonical calling number.
- [x] Add `primary_phone_type` or a clear priority rule.
- [x] Normalize phone numbers to E.164 while preserving display formatting if needed.
- [x] Validate email without rejecting legitimate formats.
- [x] Interpret `best_time_to_call` together with timezone.
- [x] Do not call when `do_not_call=true`.
- [x] Encrypt or protect sensitive contact data according to platform policy.
- [ ] Audit imports, edits and deletions.

### Frontend and Import

- [x] Update add/edit contact form.
- [x] Update contact details view and table columns.
- [x] Update CSV import template.
- [x] Add column mapping during import.
- [x] Show row-level validation failures.
- [x] Add duplicate detection and merge/skip decision.
- [ ] Let campaign creation select which contact fields the agent may use.

### Agent Context

- [x] Pass only necessary fields into the call prompt/context.
- [x] Use the preferred calling number.
- [x] Respect best time to call and timezone in dialer scheduling.
- [x] Supply calling notes without allowing them to override system/security rules.
- [x] Clearly delimit imported notes as untrusted data.
- [x] Avoid reading internal notes aloud unless explicitly required.

### Acceptance Criteria

- [x] Manual and CSV-created contacts produce the same schema.
- [x] Dialer chooses the correct phone number.
- [x] Agent receives approved name, business and call notes.
- [x] Best-time scheduling respects timezone.
- [x] Do-not-call contacts cannot be queued.

> **2026-08-31 verification note:** migration `0020`, the canonical field
> registry, CSV mapper, contact page, phone normalizer, timezone precedence,
> prompt-safety fences and DNC guard cover the checked items. A complete
> import/edit/delete audit trail and per-campaign field allowlist were not
> found, so those two items remain open.

---

## 12. Client Management and 200-Tenant Validation

### Test Objective

Prove that Talk-lee can manage at least 200 separate client organizations without mixing their data, permissions, files, calls, usage or billing. This is a multi-tenant correctness and platform-capacity test—not only a login test.

Use synthetic test clients and synthetic contact information. Do not use real client data for this test.

### Test Population

- [ ] Create 200 synthetic tenant/client organizations.
- [ ] Give every tenant a unique tenant ID, company name and subscription.
- [ ] Create at least one tenant administrator for every tenant.
- [ ] Create additional role samples across the population:
  - [ ] Tenant admin
  - [ ] Campaign manager
  - [ ] Agent/operator
  - [ ] Billing user
  - [ ] Read-only user
  - [ ] Partner/reseller user, where supported
- [ ] Create a master-admin account that can manage all 200 tenants.
- [ ] Seed different plans, balances, quotas and feature permissions.
- [ ] Seed active, trial, suspended, cancelled and overdue client states.
- [ ] Generate contacts, campaigns, calls, transcripts, recordings, attachments, reviews and billing records for every tenant.
- [ ] Keep a deterministic seed/manifest so failed records can be traced and the test can be repeated.

### Authentication and Session Tests

- [ ] Sign in successfully as a user from each of the 200 tenants.
- [ ] Confirm every login resolves the correct tenant and role.
- [ ] Test 200 sequential sign-ins.
- [ ] Test 200 concurrent active authenticated sessions.
- [ ] Test repeated login, logout, token refresh and session expiry.
- [ ] Confirm a user switching browser tabs cannot inherit another tenant's context.
- [ ] Confirm cached API responses are tenant-scoped.
- [ ] Confirm password reset and invitation links are tenant/user specific.
- [ ] Confirm disabled or suspended users cannot create new sessions.
- [ ] Confirm revoked sessions stop working.
- [ ] Check that session cookies/tokens use secure settings and are not exposed in logs.

### Tenant Isolation Matrix

For selected tenant pairs—and through automated tests across all 200 tenants—attempt to read or mutate another tenant's resources by changing IDs in URLs and API requests.

- [ ] Tenant profile and settings
- [ ] Users, invitations and roles
- [ ] Contacts and imported lead lists
- [ ] Campaigns and campaign configurations
- [ ] Phone numbers and SIP configurations
- [ ] Inbound routing and transfer destinations
- [ ] Calls, recordings and transcripts
- [ ] Conversation reviews and rewards
- [ ] Interested-lead forms and captured details
- [ ] Attachments and generated download links
- [ ] Meetings, reminders, email and SMS records
- [ ] Connectors and Salesforce credentials
- [ ] Usage, quotas, invoices, payments and minute balances
- [ ] API keys, audit logs and security settings

Every unauthorized cross-tenant request must be rejected without revealing whether the target resource exists.

### Client Management/Admin Tests

- [ ] Master admin can search, filter and paginate 200 tenants.
- [ ] Master admin can open a tenant without loading unrelated tenant data.
- [ ] Create a new tenant and verify default plan, roles, quotas and settings.
- [ ] Edit tenant profile and subscription safely.
- [ ] Suspend a tenant and verify its users/campaigns cannot continue prohibited activity.
- [ ] Reactivate a tenant without corrupting historical data.
- [ ] Archive/cancel a tenant according to retention policy.
- [ ] Verify impersonation/support-access features, if present, are authorized, time-limited and audited.
- [ ] Verify bulk actions require confirmation and cannot silently affect the wrong clients.
- [ ] Confirm tenant list totals, status counts and pagination remain correct after changes.

### Attachments, Recordings and File Storage

- [ ] Upload permitted file types for all 200 tenants.
- [ ] Reject prohibited file types and oversized files.
- [ ] Validate MIME type/content rather than trusting the filename.
- [ ] Confirm object/storage keys contain safe tenant scoping.
- [ ] Confirm download URLs cannot be reused to access another tenant's file.
- [ ] Test attachment preview, download, replacement and deletion.
- [ ] Confirm deleting a database row does not leave sensitive files indefinitely without a cleanup policy.
- [ ] Confirm deleting/replacing a file does not break another tenant's file.
- [ ] Scan uploads for malware if the product accepts arbitrary attachments.
- [ ] Verify encryption, retention and backup/restore behavior.
- [ ] Check quotas for total storage, per-file size and file count.
- [ ] Verify recordings and transcripts remain linked to the correct call and tenant.

### Billing, Plans and Minute Balances

- [ ] Seed different plans and limits across the 200 tenants.
- [ ] Confirm every tenant sees only its own subscription, invoices and payments.
- [ ] Confirm plan features and quotas are enforced independently.
- [ ] Test minute deductions for inbound and outbound calls.
- [ ] Test top-up purchases and balance updates.
- [ ] Confirm duplicate payment webhooks do not duplicate minutes.
- [ ] Confirm failed, cancelled, refunded and disputed payments adjust access/balance correctly.
- [ ] Confirm one tenant's call cannot deduct another tenant's minutes.
- [ ] Reconcile call-duration records against billed minutes.
- [ ] Test zero balance, low balance, quota exceeded and unlimited/enterprise conditions.
- [ ] Confirm currency, taxes and invoice numbering follow the configured billing rules.
- [ ] Confirm billing administrators can see billing while unauthorized roles cannot.
- [ ] Confirm every balance change has an immutable ledger/audit event.

### Campaigns, Calls and Contacts

- [ ] Create outbound and inbound campaigns under multiple tenants.
- [ ] Confirm campaign lists, counts and dashboards are tenant-scoped.
- [ ] Import contacts for all tenants using expanded contact fields.
- [ ] Confirm duplicate detection runs only within the intended tenant scope.
- [ ] Confirm best-time-to-call and timezone rules are applied per contact.
- [ ] Confirm do-not-call rules block queueing and calling.
- [ ] Start campaigns for several tenants simultaneously.
- [ ] Confirm concurrency and quotas are enforced per tenant and globally.
- [ ] Confirm incoming numbers route to the correct tenant/campaign.
- [ ] Confirm prompts, voices, transfer numbers and connectors never cross tenants.
- [ ] Confirm call outcomes, interested-lead details and reviews attach to the correct tenant.

### Connector and Credential Isolation

- [ ] Connect different Salesforce/test integrations for selected tenants.
- [ ] Confirm each connector uses only its owning tenant's encrypted credentials.
- [ ] Confirm disconnecting Tenant A does not affect Tenant B.
- [ ] Test token refresh and expired/revoked credential behavior.
- [ ] Confirm connector jobs and retry queues preserve tenant ID.
- [ ] Confirm dead-letter/retry records contain no raw secrets.
- [ ] Confirm webhook events resolve the correct tenant before processing.

### Performance and Capacity

- [ ] Measure tenant-list page with 200 clients.
- [ ] Measure dashboard/API response time with seeded tenant data.
- [ ] Test 200 active user sessions with realistic navigation and API requests.
- [ ] Test concurrent contact imports, attachment uploads and report views.
- [ ] Test simultaneous campaign activity within the safe call-capacity limit.
- [ ] Monitor application CPU, memory, database connections, query latency, cache hit rate, queue depth and storage errors.
- [ ] Identify N+1 queries and missing indexes.
- [ ] Verify pagination is used instead of loading all tenants/calls/contacts into memory.
- [ ] Define and record P50, P95 and maximum response times for critical endpoints.
- [ ] Run a soak test for at least two hours to detect memory, connection or queue leaks.

### Failure and Recovery Tests

- [ ] Restart backend services while 200 sessions exist and verify safe recovery.
- [ ] Simulate database timeout and connection-pool exhaustion.
- [ ] Simulate Redis/cache unavailability.
- [ ] Simulate object-storage upload/download failure.
- [ ] Simulate payment-webhook retry and duplication.
- [ ] Simulate connector/API failure.
- [ ] Confirm failures do not mix tenants or corrupt balances.
- [ ] Confirm retry jobs remain idempotent and tenant-scoped.
- [ ] Confirm monitoring identifies the affected tenant without exposing another tenant's data.
- [ ] Test backup restore in a non-production environment and verify tenant/file relationships.

### Audit, Privacy and Data Lifecycle

- [ ] Audit tenant creation, suspension, deletion and subscription changes.
- [ ] Audit user invitations, role changes and sensitive access.
- [ ] Audit attachment, recording, billing and connector actions.
- [ ] Redact tokens, passwords, payment data and unnecessary personal information from logs.
- [ ] Verify retention and deletion rules for calls, recordings, transcripts, attachments and reviews.
- [ ] Verify tenant export contains only that tenant's data.
- [ ] Verify tenant deletion/anonymization does not delete shared platform configuration or another tenant's records.

### Required Evidence

- [ ] Test run ID, environment and timestamp
- [ ] Exact application/runtime version and configuration
- [ ] Synthetic tenant seed/manifest version
- [ ] Total tenants/users/sessions created
- [ ] Total checks passed, failed and skipped
- [ ] Cross-tenant access attempt results
- [ ] Billing reconciliation report
- [ ] Attachment/storage reconciliation report
- [ ] Performance P50/P95/max results
- [ ] CPU, memory, database, cache and queue graphs
- [ ] Every failed scenario with request/trace ID
- [ ] Retest evidence after fixes
- [ ] Cleanup confirmation for synthetic accounts and stored files

### Release Acceptance Criteria

- [ ] All 200 tenants can be created, authenticated and managed.
- [ ] Zero successful cross-tenant data-access attempts.
- [ ] Zero attachments, calls, contacts, credentials or billing records assigned to the wrong tenant.
- [ ] Zero incorrect minute deductions or duplicate top-up credits.
- [ ] All role restrictions behave as designed.
- [ ] No unresolved P0/P1 security or data-integrity defects.
- [ ] Critical dashboard/API P95 response time meets the agreed target under the 200-session test.
- [ ] No sustained memory, connection or queue leak during the soak test.
- [ ] Backup/restore and rollback procedures are proven in a safe environment.
- [ ] Synthetic test data is removed or clearly isolated after validation.

---

## 13. Delivery Calendar

### August 22–24 — Design and Contracts

**Developer A**

- [ ] Finalize database migrations and API contracts.
- [ ] Define prompt template schema/versioning.
- [ ] Define inbound routing and billing rules.
- [ ] Define review reward ledger and Salesforce MVP boundary.
- [ ] Define 200-tenant synthetic seed, tenant-isolation matrix and billing/file reconciliation tests.

**Developer B**

- [ ] Produce page/component wireframes.
- [ ] Define sidebar changes and routes.
- [ ] Define shared tooltip/popover and form components.
- [ ] Confirm frontend API payloads with Developer A.

**Joint gate**

- [ ] API contracts frozen before parallel implementation.
- [ ] Migration rollback plan reviewed.

### August 25–29 — Core P0 Build

**Developer A**

- [ ] Contact migration and agent-context mapping.
- [ ] Generic prompt runtime integration and logging.
- [ ] Conversation review APIs.
- [ ] Interested-lead structured capture.
- [ ] Inbound routing foundation.

**Developer B**

- [ ] Expanded contacts UI/import mapping.
- [ ] Conversation review UI.
- [ ] Interested-lead panel.
- [ ] Security sidebar/page move.
- [ ] Tooltip/popover component.

**Parallel QA**

- [ ] Establish baseline calls using the old prompt.
- [ ] Prepare 30-call test scripts and scoring rubric.
- [ ] Prepare the 200-tenant test environment and synthetic accounts.

### August 30–September 3 — Inbound, Billing and UX Completion

**Developer A**

- [ ] Complete inbound campaign MVP.
- [ ] Complete top-up order, webhook and ledger flow.
- [ ] Add reward idempotency and abuse controls.
- [ ] Add backend tests and audit events.

**Developer B**

- [ ] Complete inbound campaign pages.
- [ ] Complete minute top-up UI.
- [ ] Complete AI Options and AI Summary help content.
- [ ] Complete reward display and review analytics basics.

**Parallel QA**

- [ ] Run prompt experiment batch 1.
- [ ] Review failures and approve prompt version 1.1 only if evidence supports it.

### September 4–6 — Salesforce MVP and Integration Testing

**Developer A**

- [ ] Salesforce OAuth and one-way lead/contact push.
- [ ] Retry, deduplication and reconciliation handling.
- [ ] End-to-end tenant/security tests.

**Developer B**

- [ ] Salesforce connector and field-mapping UI.
- [ ] Integration status/error interface.
- [ ] Complete responsive and accessibility checks.

**Joint gate**

- [ ] If any P0 item is unstable, pause Salesforce and finish P0.

### September 7 — Integrated Release Candidate

- [ ] Merge only reviewed changes.
- [ ] Apply migrations in staging/canary.
- [ ] Run backend, frontend and C++ builds/tests.
- [ ] Verify runtime configuration and secrets.
- [ ] Verify inbound and outbound dashboard routing.
- [ ] Verify billing in payment-provider test mode.
- [ ] Seed and smoke-test the 200 synthetic tenant/client accounts.

### September 8 — Freeze and Controlled Validation

- [ ] Freeze code, prompt and runtime configuration.
- [ ] Run 30 controlled lead-generation calls.
- [ ] Run inbound call matrix.
- [ ] Run review/reward abuse tests.
- [ ] Run top-up duplicate-webhook tests.
- [ ] Run tenant isolation/security tests.
- [ ] Run Salesforce duplicate/retry tests if included.
- [ ] Run the complete 200-tenant isolation, billing, attachment and session test.
- [ ] Run the two-hour multi-tenant soak test.

### September 9 — Fix Only Release Blockers

- [ ] No new features.
- [ ] Fix only confirmed release-blocking defects.
- [ ] Repeat affected regression tests.
- [ ] Rehearse code, database and configuration rollback.
- [ ] Prepare release notes and known limitations.

### September 10 — Controlled Release

- [ ] Deploy exact approved commit/images.
- [ ] Apply verified migrations.
- [ ] Confirm effective prompt version and configuration.
- [ ] Run one inbound and one outbound smoke call.
- [ ] Verify billing and review submission.
- [ ] Monitor errors, call failures, latency and payment webhooks.
- [ ] Increase traffic gradually only if metrics remain healthy.

---

## 14. Definition of Done

A feature is not "done" merely because code is pushed.

- [ ] Product behavior matches acceptance criteria.
- [ ] Tenant authorization is enforced in backend tests.
- [ ] Database migration and rollback are tested.
- [ ] Frontend handles loading, empty, success and failure states.
- [ ] Audit and operational logs contain useful identifiers but no secrets.
- [ ] Automated tests pass in a reproducible environment.
- [ ] Feature is tested in staging/canary.
- [ ] Documentation and configuration are updated.
- [ ] Monitoring and error reporting exist.
- [ ] A rollback version and procedure are recorded.
- [ ] Product owner accepts the feature using a real end-to-end scenario.

---

## 15. Daily Management Checklist

- [ ] 15-minute morning stand-up.
- [ ] Each developer states yesterday's evidence, today's goal and blocker.
- [ ] No task remains "90% done" without a named missing acceptance item.
- [ ] Pull requests stay small enough to review.
- [ ] Database/API contract changes are communicated before frontend work continues.
- [ ] Production is not used as the development test environment.
- [ ] Prompt changes are versioned and tested separately from code changes.
- [ ] End-of-day tracker shows completed, blocked, failed-test and ready-for-review items.

## Final Delivery Decision

The September 10 release should prioritize reliable P0 behavior. If the team falls behind, defer Salesforce beyond the connector MVP and defer automatic AI training. Do not sacrifice inbound routing correctness, tenant security, billing integrity, prompt runtime correctness or contact-data safety to claim that every requested feature shipped.

<!-- END AUDITED GOALS SNAPSHOT -->

## Appendix B — complete post-report-13 commit ledger

This ledger begins after report 13 commit `2576a4a6ea016be2fee4d6a1d8e8bc3a02b9c2ae` and ends at the report-14 branch head used to generate this dossier.

| Commit | Timestamp | Subject |
|---|---|---|
| `e6ffc36c` | 2026-08-30T02:05:00+05:00 | feat(db): inbound calling schema 0022-0035, proven against a production restore |
| `69fff6f4` | 2026-08-30T02:07:19+05:00 | feat(inbound): deterministic pre-answer admission, billing holds and lease safety |
| `f763f9e9` | 2026-08-30T02:07:39+05:00 | fix(billing,compliance,rbac): money, do-not-call and permission defects found by audit |
| `c2fe07af` | 2026-08-30T02:09:28+05:00 | test(backend): cover the audit fixes, plus the validation tooling for section 12 |
| `79e46464` | 2026-08-30T02:09:42+05:00 | fix(asterisk): pass the dialled DID where the adapter reads it, and load pjsip.d |
| `bd93b6d3` | 2026-08-30T02:09:56+05:00 | fix(ui): stop the product claiming things that are not true |
| `b6c0eee3` | 2026-08-30T02:12:10+05:00 | docs(ops,ci): describe the production that exists, and run the suites that matter |
| `1bcfb9cc` | 2026-08-30T03:02:47+05:00 | fix(web): repoint the trailing-slash test at proxy.ts after the Next 16 rename |
| `a3e6d955` | 2026-08-30T14:00:14+05:00 | fix(telephony): keep the trunk status updater sighted once the app role loses BYPASSRLS |
| `820e1e91` | 2026-08-30T14:50:20+05:00 | fix(inbound): bind the Answer timestamp to asyncpg as a datetime, not an ISO string |
| `69e607e9` | 2026-08-30T21:54:57+05:00 | fix(inbound): give the C++ gateway its own aiohttp session |
| `fa64d54f` | 2026-08-31T00:13:05+05:00 | fix(rls): scope pooled DB reads to a tenant after the app role lost BYPASSRLS |
| `6c9f7ded` | 2026-08-30T13:05:01+05:00 | Harden inbound media gateway for production |
| `72cd445b` | 2026-08-31T01:20:25+05:00 | fix(rls): make pooled database access tenant-safe |
| `c3dd6ffb` | 2026-08-31T01:39:20+05:00 | fix(deploy): preflight releases and preserve inbound dialplan |
| `c5b2ef95` | 2026-08-31T01:55:18+05:00 | feat(inbound): signal denials and measure first audio |
| `e7b89245` | 2026-08-31T02:41:40+05:00 | feat(inbound): persist denials and expose operator history |
| `94935a80` | 2026-08-31T02:53:13+05:00 | fix(voice): bypass failed TTS with emergency audio |
| `9ee5817a` | 2026-08-31T03:00:38+05:00 | feat(inbound): add carrier synthetic liveness gate |
| `12991153` | 2026-08-31T03:09:19+05:00 | test(inbound): prove two-tenant DID isolation |
| `7d19c725` | 2026-08-31T03:38:50+05:00 | docs(inbound): publish production hardening report |

## Appendix C — complete changed-file line ledger after report 13

Additions and deletions are Git numstat values. A dash represents binary content. This table includes backend, frontend, Admin, deployment, documentation, tests, migrations and assets, including every file that changed between report 13 and this report’s branch head.

| # | Added | Deleted | File |
|---:|---:|---:|---|
| 1 | 0 | 1 | `.claude/worktrees/laughing-ramanujan-6e8820` |
| 2 | 0 | 1 | `.claude/worktrees/nostalgic-lamarr-ba5925` |
| 3 | 3 | 27 | `.github/workflows/backend-voice-tests.yml` |
| 4 | 44 | 0 | `.github/workflows/ci.yml` |
| 5 | 86 | 0 | `.github/workflows/voice-gateway-cpp.yml` |
| 6 | 3 | 0 | `.gitignore` |
| 7 | 149 | 146 | `Admin/frontend/package-lock.json` |
| 8 | 4 | 0 | `Admin/frontend/package.json` |
| 9 | 18 | 7 | `Admin/frontend/src/App.tsx` |
| 10 | 7 | 5 | `Admin/frontend/src/components/AdminRouteGuard.tsx` |
| 11 | 162 | 31 | `Admin/frontend/src/components/CallDetailDrawer.tsx` |
| 12 | 46 | 9 | `Admin/frontend/src/components/CallHistoryTable.tsx` |
| 13 | 182 | 0 | `Admin/frontend/src/components/CallTerminationAction.tsx` |
| 14 | 47 | 4 | `Admin/frontend/src/components/ConfirmationModal.css` |
| 15 | 54 | 15 | `Admin/frontend/src/components/ConfirmationModal.tsx` |
| 16 | 58 | 7 | `Admin/frontend/src/components/FeedbackTable.tsx` |
| 17 | 84 | 54 | `Admin/frontend/src/components/LiveCalls.tsx` |
| 18 | 75 | 82 | `Admin/frontend/src/components/LiveCallsTable.tsx` |
| 19 | 7 | 11 | `Admin/frontend/src/components/QuotaUsage.tsx` |
| 20 | 58 | 7 | `Admin/frontend/src/components/RecordingsTable.tsx` |
| 21 | 13 | 7 | `Admin/frontend/src/components/Sidebar.tsx` |
| 22 | 24 | 12 | `Admin/frontend/src/components/UsageBreakdownCard.tsx` |
| 23 | 427 | 0 | `Admin/frontend/src/index.css` |
| 24 | 388 | 56 | `Admin/frontend/src/lib/api.ts` |
| 25 | 21 | 0 | `Admin/frontend/src/lib/call-cost.ts` |
| 26 | 113 | 0 | `Admin/frontend/src/lib/call-termination.ts` |
| 27 | 11 | 3 | `Admin/frontend/src/pages/CallsPage.tsx` |
| 28 | 470 | 0 | `Admin/frontend/src/pages/InboundControlPage.tsx` |
| 29 | 20 | 5 | `Admin/frontend/src/pages/UsageCostPage.tsx` |
| 30 | 30 | 0 | `Admin/frontend/tests/call-cost.test.mjs` |
| 31 | 118 | 0 | `Admin/frontend/tests/call-termination.test.mjs` |
| 32 | 79 | 0 | `CLAUDE.md` |
| 33 | 479 | 0 | `PROJECT-SPEC.md` |
| 34 | 9 | 0 | `Talk-Leee/AGENTS.md` |
| 35 | 1 | 0 | `Talk-Leee/CLAUDE.md` |
| 36 | 37 | 8 | `Talk-Leee/eslint.config.mjs` |
| 37 | 2460 | 6919 | `Talk-Leee/package-lock.json` |
| 38 | 6 | 7 | `Talk-Leee/package.json` |
| 39 | 7 | 341 | `Talk-Leee/src/app/admin/abuse-detection/page.tsx` |
| 40 | 8 | 366 | `Talk-Leee/src/app/admin/api-keys/page.tsx` |
| 41 | 176 | 67 | `Talk-Leee/src/app/admin/audit-logs/page.tsx` |
| 42 | 14 | 122 | `Talk-Leee/src/app/admin/billing/page.tsx` |
| 43 | 16 | 107 | `Talk-Leee/src/app/admin/billing/tenants/page.tsx` |
| 44 | 13 | 331 | `Talk-Leee/src/app/admin/rate-limiting/page.tsx` |
| 45 | 7 | 304 | `Talk-Leee/src/app/admin/secrets/page.tsx` |
| 46 | 13 | 448 | `Talk-Leee/src/app/admin/voice-security/page.tsx` |
| 47 | 7 | 457 | `Talk-Leee/src/app/admin/webhooks/page.tsx` |
| 48 | 45 | 8 | `Talk-Leee/src/app/ai-options/page.tsx` |
| 49 | 45 | 18 | `Talk-Leee/src/app/ai-voices/page.test.tsx` |
| 50 | 3 | 0 | `Talk-Leee/src/app/assistant/actions/page.tsx` |
| 51 | 23 | 0 | `Talk-Leee/src/app/billing/invoices/[id]/page.tsx` |
| 52 | 15 | 0 | `Talk-Leee/src/app/billing/invoices/page.tsx` |
| 53 | 12 | 486 | `Talk-Leee/src/app/billing/page.tsx` |
| 54 | 27 | 1 | `Talk-Leee/src/app/billing/plans/page.tsx` |
| 55 | 134 | 28 | `Talk-Leee/src/app/calls/[id]/page.tsx` |
| 56 | 161 | 42 | `Talk-Leee/src/app/calls/page.tsx` |
| 57 | 25 | 9 | `Talk-Leee/src/app/campaigns/[id]/page.tsx` |
| 58 | 19 | 7 | `Talk-Leee/src/app/campaigns/page.tsx` |
| 59 | 510 | 115 | `Talk-Leee/src/app/contacts/page.tsx` |
| 60 | 10 | 5 | `Talk-Leee/src/app/dashboard/page.tsx` |
| 61 | 9 | 0 | `Talk-Leee/src/app/globals.css` |
| 62 | 65 | 0 | `Talk-Leee/src/app/inbound-campaigns/[id]/edit/page.tsx` |
| 63 | 115 | 0 | `Talk-Leee/src/app/inbound-campaigns/[id]/page.tsx` |
| 64 | 38 | 0 | `Talk-Leee/src/app/inbound-campaigns/new/page.tsx` |
| 65 | 142 | 0 | `Talk-Leee/src/app/inbound-campaigns/page.tsx` |
| 66 | 9 | 0 | `Talk-Leee/src/app/meetings/page.tsx` |
| 67 | 83 | 210 | `Talk-Leee/src/app/recordings/page.tsx` |
| 68 | 47 | 11 | `Talk-Leee/src/app/security/page.tsx` |
| 69 | 83 | 0 | `Talk-Leee/src/app/security/session-revoke-copy.test.ts` |
| 70 | 1 | 0 | `Talk-Leee/src/app/settings/page.tsx` |
| 71 | 29 | 4 | `Talk-Leee/src/app/white-label/[partner]/tenants/[tenant]/agent-settings/page.tsx` |
| 72 | 18 | 7 | `Talk-Leee/src/app/white-label/[partner]/tenants/tenants-client.tsx` |
| 73 | 3 | 0 | `Talk-Leee/src/app/white-label/dashboard/page.tsx` |
| 74 | 58 | 0 | `Talk-Leee/src/components/admin/feature-unavailable.tsx` |
| 75 | 10 | 0 | `Talk-Leee/src/components/admin/suspension-state-provider.tsx` |
| 76 | 65 | 0 | `Talk-Leee/src/components/ai-options/controls.test.tsx` |
| 77 | 6 | 4 | `Talk-Leee/src/components/ai-options/controls.tsx` |
| 78 | 1 | 1 | `Talk-Leee/src/components/ai-options/voice-clone-modal.tsx` |
| 79 | 4 | 0 | `Talk-Leee/src/components/assistant/conversation-history.tsx` |
| 80 | 61 | 36 | `Talk-Leee/src/components/assistant/floating-assistant.tsx` |
| 81 | 6 | 1 | `Talk-Leee/src/components/assistant/voice-mode.tsx` |
| 82 | 1 | 0 | `Talk-Leee/src/components/auth/mfa-setup.tsx` |
| 83 | 4 | 0 | `Talk-Leee/src/components/auth/passkey-list.tsx` |
| 84 | 172 | 0 | `Talk-Leee/src/components/billing/billing-overview.test.tsx` |
| 85 | 709 | 0 | `Talk-Leee/src/components/billing/billing-overview.tsx` |
| 86 | 72 | 3 | `Talk-Leee/src/components/billing/topup-card.tsx` |
| 87 | 4 | 4 | `Talk-Leee/src/components/calls/call-issues-banner.tsx` |
| 88 | 300 | 0 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx` |
| 89 | 125 | 75 | `Talk-Leee/src/components/calls/conversation-review-panel.tsx` |
| 90 | 57 | 57 | `Talk-Leee/src/components/calls/quick-review-buttons.tsx` |
| 91 | 4 | 0 | `Talk-Leee/src/components/calls/voice-feedback-recorder.tsx` |
| 92 | 9 | 1 | `Talk-Leee/src/components/campaigns/agent-name-gender.tsx` |
| 93 | 1 | 1 | `Talk-Leee/src/components/campaigns/alert-timeline.tsx` |
| 94 | 3 | 0 | `Talk-Leee/src/components/campaigns/apply-to-campaigns-modal.tsx` |
| 95 | 8 | 1 | `Talk-Leee/src/components/campaigns/campaign-form.tsx` |
| 96 | 0 | 1 | `Talk-Leee/src/components/campaigns/campaign-performance-table.stories.tsx` |
| 97 | 3 | 2 | `Talk-Leee/src/components/campaigns/campaign-performance-table.tsx` |
| 98 | 2 | 2 | `Talk-Leee/src/components/campaigns/campaign-wizard.tsx` |
| 99 | 8 | 2 | `Talk-Leee/src/components/campaigns/contact-lists.tsx` |
| 100 | 1 | 3 | `Talk-Leee/src/components/campaigns/event-stream.tsx` |
| 101 | 5 | 0 | `Talk-Leee/src/components/campaigns/knowledge-panel.tsx` |
| 102 | 348 | 0 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx` |
| 103 | 361 | 48 | `Talk-Leee/src/components/campaigns/live-calls-panel.tsx` |
| 104 | 69 | 0 | `Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.test.tsx` |
| 105 | 159 | 0 | `Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.tsx` |
| 106 | 1 | 0 | `Talk-Leee/src/components/campaigns/script-card.tsx` |
| 107 | 5 | 5 | `Talk-Leee/src/components/campaigns/test-agent-button.tsx` |
| 108 | 34 | 4 | `Talk-Leee/src/components/campaigns/voice-provider-picker.tsx` |
| 109 | 8 | 0 | `Talk-Leee/src/components/email/send-email-modal.tsx` |
| 110 | 6 | 0 | `Talk-Leee/src/components/home/navbar.tsx` |
| 111 | 0 | 2 | `Talk-Leee/src/components/home/secondary-hero.tsx` |
| 112 | 129 | 0 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts` |
| 113 | 307 | 0 | `Talk-Leee/src/components/inbound/inbound-campaign-form.tsx` |
| 114 | 47 | 0 | `Talk-Leee/src/components/inbound/inbound-page-state.tsx` |
| 115 | 127 | 0 | `Talk-Leee/src/components/inbound/inbound-status.tsx` |
| 116 | 1 | 0 | `Talk-Leee/src/components/layout/breadcrumbs.tsx` |
| 117 | 8 | 1 | `Talk-Leee/src/components/layout/dashboard-layout.tsx` |
| 118 | 11 | 1 | `Talk-Leee/src/components/layout/sidebar.tsx` |
| 119 | 94 | 0 | `Talk-Leee/src/components/notifications/qualified-lead-alerts.test.ts` |
| 120 | 19 | 5 | `Talk-Leee/src/components/notifications/qualified-lead-alerts.tsx` |
| 121 | 3 | 1 | `Talk-Leee/src/components/providers/app-providers.tsx` |
| 122 | 4 | 0 | `Talk-Leee/src/components/providers/theme-provider.tsx` |
| 123 | 158 | 0 | `Talk-Leee/src/components/recordings/recording-media-controls.test.tsx` |
| 124 | 388 | 0 | `Talk-Leee/src/components/recordings/recording-media-controls.tsx` |
| 125 | 15 | 12 | `Talk-Leee/src/components/settings/sip-trunks-list.tsx` |
| 126 | 4 | 0 | `Talk-Leee/src/components/settings/telephony-providers-section.tsx` |
| 127 | 75 | 2 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts` |
| 128 | 35 | 10 | `Talk-Leee/src/components/ui/confirm-dialog.tsx` |
| 129 | 7 | 1 | `Talk-Leee/src/components/ui/helix-hero.tsx` |
| 130 | 0 | 72 | `Talk-Leee/src/components/ui/info-tip.test.ts` |
| 131 | 287 | 0 | `Talk-Leee/src/components/ui/info-tip.test.tsx` |
| 132 | 15 | 2 | `Talk-Leee/src/components/ui/morphing-cursor.tsx` |
| 133 | 9 | 0 | `Talk-Leee/src/components/ui/select.tsx` |
| 134 | 10 | 6 | `Talk-Leee/src/components/ui/voice-agent-popup.tsx` |
| 135 | 0 | 1 | `Talk-Leee/src/lib/ai-options-api.ts` |
| 136 | 5 | 5 | `Talk-Leee/src/lib/api-hooks.ts` |
| 137 | 94 | 33 | `Talk-Leee/src/lib/api.ts` |
| 138 | 23 | 3 | `Talk-Leee/src/lib/auth-context.tsx` |
| 139 | 115 | 0 | `Talk-Leee/src/lib/billing-api.test.ts` |
| 140 | 47 | 27 | `Talk-Leee/src/lib/billing-api.ts` |
| 141 | 0 | 129 | `Talk-Leee/src/lib/billing-mock-data.ts` |
| 142 | 45 | 7 | `Talk-Leee/src/lib/contact-csv.ts` |
| 143 | 52 | 0 | `Talk-Leee/src/lib/dashboard-api.inbound.test.ts` |
| 144 | 155 | 17 | `Talk-Leee/src/lib/dashboard-api.ts` |
| 145 | 89 | 0 | `Talk-Leee/src/lib/extended-api.recordings.test.ts` |
| 146 | 50 | 8 | `Talk-Leee/src/lib/extended-api.ts` |
| 147 | 0 | 18 | `Talk-Leee/src/lib/http-client.session-expired.test.ts` |
| 148 | 60 | 0 | `Talk-Leee/src/lib/http-client.test.ts` |
| 149 | 30 | 8 | `Talk-Leee/src/lib/http-client.ts` |
| 150 | 298 | 0 | `Talk-Leee/src/lib/inbound-api.test.ts` |
| 151 | 721 | 0 | `Talk-Leee/src/lib/inbound-api.ts` |
| 152 | 42 | 0 | `Talk-Leee/src/lib/inbound-permissions.test.ts` |
| 153 | 99 | 0 | `Talk-Leee/src/lib/inbound-permissions.ts` |
| 154 | 113 | 0 | `Talk-Leee/src/lib/inbound-validation.ts` |
| 155 | 50 | 0 | `Talk-Leee/src/lib/media-permissions.test.ts` |
| 156 | 39 | 0 | `Talk-Leee/src/lib/media-permissions.ts` |
| 157 | 57 | 0 | `Talk-Leee/src/lib/queries/inbound-queries.test.ts` |
| 158 | 256 | 0 | `Talk-Leee/src/lib/queries/inbound-queries.ts` |
| 159 | 77 | 0 | `Talk-Leee/src/lib/review-permissions.test.ts` |
| 160 | 57 | 0 | `Talk-Leee/src/lib/review-permissions.ts` |
| 161 | 5 | 0 | `Talk-Leee/src/lib/telephony-api.ts` |
| 162 | 2 | 2 | `Talk-Leee/src/{middleware.trailing-slash.test.ts => proxy.trailing-slash.test.ts}` |
| 163 | 2 | 2 | `Talk-Leee/src/{middleware.ts => proxy.ts}` |
| 164 | 5 | 0 | `Talk-Leee/src/test-utils/dom.ts` |
| 165 | 3 | 3 | `Talk-Leee/tests/responsive.overlap.spec.ts` |
| 166 | 1 | 1 | `Talk-Leee/tsconfig.json` |
| 167 | 6 | 53 | `alertmanager.yml` |
| 168 | 74 | 1 | `backend/.env.example` |
| 169 | 45 | 19 | `backend/Alembic/versions/0001_baseline.py` |
| 170 | 15 | 4 | `backend/Alembic/versions/0013_canonical_rls_policies.py` |
| 171 | 53 | 6 | `backend/Alembic/versions/0019_ai_config_migration_records.py` |
| 172 | 1201 | 0 | `backend/Alembic/versions/0022_inbound_calling_foundation.py` |
| 173 | 643 | 0 | `backend/Alembic/versions/0023_admin_media_deletion_safety.py` |
| 174 | 81 | 0 | `backend/Alembic/versions/0024_recording_permissions.py` |
| 175 | 147 | 0 | `backend/Alembic/versions/0025_media_hold_serialization.py` |
| 176 | 193 | 0 | `backend/Alembic/versions/0026_media_delete_recovery.py` |
| 177 | 608 | 0 | `backend/Alembic/versions/0027_media_delete_request_keys.py` |
| 178 | 258 | 0 | `backend/Alembic/versions/0028_call_terminal_settlement_cas.py` |
| 179 | 81 | 0 | `backend/Alembic/versions/0029_trunk_runtime_status.py` |
| 180 | 259 | 0 | `backend/Alembic/versions/0030_inbound_transfer_leg_usage.py` |
| 181 | 89 | 0 | `backend/Alembic/versions/0031_inbound_lease_safety.py` |
| 182 | 134 | 0 | `backend/Alembic/versions/0032_inbound_billing_hold.py` |
| 183 | 2044 | 0 | `backend/Alembic/versions/0033_bootstrap_contract_repair.py` |
| 184 | 441 | 0 | `backend/Alembic/versions/0034_inbound_billing_four_eye.py` |
| 185 | 320 | 0 | `backend/Alembic/versions/0035_user_profiles_role_widen.py` |
| 186 | 139 | 0 | `backend/Alembic/versions/0036_inbound_rejection_log.py` |
| 187 | 0 | 3 | `backend/app/api/v1/dependencies.py` |
| 188 | 2 | 0 | `backend/app/api/v1/endpoints/admin/__init__.py` |
| 189 | 557 | 158 | `backend/app/api/v1/endpoints/admin/calls.py` |
| 190 | 322 | 0 | `backend/app/api/v1/endpoints/admin/inbound.py` |
| 191 | 894 | 76 | `backend/app/api/v1/endpoints/admin/media.py` |
| 192 | 558 | 119 | `backend/app/api/v1/endpoints/admin/tenants.py` |
| 193 | 156 | 100 | `backend/app/api/v1/endpoints/admin/usage.py` |
| 194 | 13 | 1 | `backend/app/api/v1/endpoints/blocked_entities.py` |
| 195 | 11 | 6 | `backend/app/api/v1/endpoints/call_limits.py` |
| 196 | 955 | 194 | `backend/app/api/v1/endpoints/calls.py` |
| 197 | 32 | 1 | `backend/app/api/v1/endpoints/campaign_test_ws.py` |
| 198 | 262 | 36 | `backend/app/api/v1/endpoints/campaigns.py` |
| 199 | 183 | 77 | `backend/app/api/v1/endpoints/contacts.py` |
| 200 | 33 | 9 | `backend/app/api/v1/endpoints/dashboard.py` |
| 201 | 383 | 0 | `backend/app/api/v1/endpoints/inbound_campaigns.py` |
| 202 | 490 | 124 | `backend/app/api/v1/endpoints/recordings.py` |
| 203 | 6 | 1 | `backend/app/api/v1/endpoints/stream_events.py` |
| 204 | 1334 | 161 | `backend/app/api/v1/endpoints/telephony_bridge.py` |
| 205 | 37 | 10 | `backend/app/api/v1/endpoints/telephony_concurrency.py` |
| 206 | 50 | 8 | `backend/app/api/v1/endpoints/telephony_runtime/activate.py` |
| 207 | 8 | 1 | `backend/app/api/v1/endpoints/telephony_runtime/metrics.py` |
| 208 | 14 | 2 | `backend/app/api/v1/endpoints/telephony_runtime/preview.py` |
| 209 | 38 | 6 | `backend/app/api/v1/endpoints/telephony_runtime/rollback.py` |
| 210 | 14 | 2 | `backend/app/api/v1/endpoints/telephony_runtime/versions.py` |
| 211 | 11 | 2 | `backend/app/api/v1/endpoints/telephony_sip/__init__.py` |
| 212 | 55 | 9 | `backend/app/api/v1/endpoints/telephony_sip/codec_policies.py` |
| 213 | 14 | 2 | `backend/app/api/v1/endpoints/telephony_sip/quotas.py` |
| 214 | 55 | 9 | `backend/app/api/v1/endpoints/telephony_sip/route_policies.py` |
| 215 | 110 | 3 | `backend/app/api/v1/endpoints/telephony_sip/schemas.py` |
| 216 | 88 | 11 | `backend/app/api/v1/endpoints/telephony_sip/trunk_probe.py` |
| 217 | 387 | 105 | `backend/app/api/v1/endpoints/telephony_sip/trunks.py` |
| 218 | 12 | 4 | `backend/app/api/v1/endpoints/tenant_ai_credentials.py` |
| 219 | 75 | 22 | `backend/app/api/v1/endpoints/tenant_phone_numbers.py` |
| 220 | 26 | 0 | `backend/app/api/v1/endpoints/twilio_bridge.py` |
| 221 | 30 | 7 | `backend/app/api/v1/endpoints/vonage_bridge.py` |
| 222 | 2 | 0 | `backend/app/api/v1/routes.py` |
| 223 | 21 | 0 | `backend/app/api/v1/schemas/campaigns.py` |
| 224 | 357 | 0 | `backend/app/api/v1/schemas/inbound_campaigns.py` |
| 225 | 22 | 3 | `backend/app/api/v1/schemas/telephony_bridge.py` |
| 226 | 1 | 0 | `backend/app/assets/telephony/voice_hold.ulaw` |
| 227 | 1 | 0 | `backend/app/assets/telephony/voice_terminal.ulaw` |
| 228 | 15 | 0 | `backend/app/core/db_utils.py` |
| 229 | 227 | 0 | `backend/app/core/inbound_startup.py` |
| 230 | 6 | 1 | `backend/app/core/legacy_campaign_audit.py` |
| 231 | 10 | 42 | `backend/app/core/postgres_adapter.py` |
| 232 | 165 | 2 | `backend/app/core/prod_gate.py` |
| 233 | 12 | 0 | `backend/app/core/security/internal_auth.py` |
| 234 | 390 | 19 | `backend/app/core/security/rbac.py` |
| 235 | 372 | 110 | `backend/app/core/telephony_observability.py` |
| 236 | 7 | 3 | `backend/app/core/tenant_rls.py` |
| 237 | 17 | 2 | `backend/app/domain/interfaces/call_control_adapter.py` |
| 238 | 188 | 156 | `backend/app/domain/services/abuse_detection.py` |
| 239 | 408 | 310 | `backend/app/domain/services/billing_service.py` |
| 240 | 29 | 7 | `backend/app/domain/services/call_feedback_service.py` |
| 241 | 187 | 130 | `backend/app/domain/services/call_guard.py` |
| 242 | 607 | 199 | `backend/app/domain/services/call_service.py` |
| 243 | 109 | 23 | `backend/app/domain/services/call_status.py` |
| 244 | 133 | 20 | `backend/app/domain/services/campaign_service.py` |
| 245 | 59 | 9 | `backend/app/domain/services/contact_fields.py` |
| 246 | 22 | 2 | `backend/app/domain/services/credential_resolver.py` |
| 247 | 42 | 0 | `backend/app/domain/services/dialer/bulk_ingest.py` |
| 248 | 29 | 8 | `backend/app/domain/services/dialer/job_lifecycle.py` |
| 249 | 4 | 0 | `backend/app/domain/services/dialer/job_states.py` |
| 250 | 89 | 15 | `backend/app/domain/services/dialer/lead_timezone.py` |
| 251 | 21 | 9 | `backend/app/domain/services/dialer/stuck_job_reaper.py` |
| 252 | 46 | 26 | `backend/app/domain/services/dnc_service.py` |
| 253 | 25 | 9 | `backend/app/domain/services/event_emitter.py` |
| 254 | 70 | 6 | `backend/app/domain/services/global_concurrency.py` |
| 255 | 3111 | 0 | `backend/app/domain/services/inbound_campaign_service.py` |
| 256 | 4 | 0 | `backend/app/domain/services/lead_capture_service.py` |
| 257 | 38 | 11 | `backend/app/domain/services/minutes_quota.py` |
| 258 | 211 | 1 | `backend/app/domain/services/phone_number_normalizer.py` |
| 259 | 128 | 0 | `backend/app/domain/services/queue_service.py` |
| 260 | 68 | 33 | `backend/app/domain/services/recording_policy_service.py` |
| 261 | 239 | 46 | `backend/app/domain/services/recording_service.py` |
| 262 | 117 | 0 | `backend/app/domain/services/subscription_status.py` |
| 263 | 156 | 0 | `backend/app/domain/services/telephony/business_hours.py` |
| 264 | 58 | 17 | `backend/app/domain/services/telephony/config.py` |
| 265 | 2968 | 0 | `backend/app/domain/services/telephony/inbound_admission.py` |
| 266 | 118 | 0 | `backend/app/domain/services/telephony/inbound_overrides.py` |
| 267 | 219 | 197 | `backend/app/domain/services/telephony/inbound_router.py` |
| 268 | 2034 | 0 | `backend/app/domain/services/telephony/inbound_transfer.py` |
| 269 | 3772 | 477 | `backend/app/domain/services/telephony/lifecycle.py` |
| 270 | 23 | 3 | `backend/app/domain/services/telephony/modes/agent_first.py` |
| 271 | 164 | 0 | `backend/app/domain/services/telephony/modes/caller_first.py` |
| 272 | 82 | 1 | `backend/app/domain/services/telephony/recording.py` |
| 273 | 332 | 14 | `backend/app/domain/services/telephony/session_registry.py` |
| 274 | 685 | 59 | `backend/app/domain/services/telephony/state_backend.py` |
| 275 | 422 | 0 | `backend/app/domain/services/telephony/termination.py` |
| 276 | 392 | 0 | `backend/app/domain/services/telephony/transfer_provider_identity.py` |
| 277 | 255 | 0 | `backend/app/domain/services/telephony/transfer_restart_recovery.py` |
| 278 | 157 | 0 | `backend/app/domain/services/telephony/transfer_validation.py` |
| 279 | 96 | 14 | `backend/app/domain/services/telephony/trunk_resolver.py` |
| 280 | 142 | 0 | `backend/app/domain/services/telephony/trunk_runtime.py` |
| 281 | 107 | 35 | `backend/app/domain/services/telephony_concurrency_limiter.py` |
| 282 | 70 | 5 | `backend/app/domain/services/telephony_session_config.py` |
| 283 | 144 | 11 | `backend/app/domain/services/tenant_phone_number_service.py` |
| 284 | 37 | 0 | `backend/app/domain/services/topup_service.py` |
| 285 | 98 | 24 | `backend/app/domain/services/voice_orchestrator.py` |
| 286 | 10 | 1 | `backend/app/domain/services/voice_pipeline/audio_ingest.py` |
| 287 | 80 | 0 | `backend/app/domain/services/voice_pipeline/emergency_audio.py` |
| 288 | 28 | 21 | `backend/app/domain/services/voice_pipeline/knowledge_tool.py` |
| 289 | 381 | 0 | `backend/app/domain/services/voice_pipeline/lead_slot_capture.py` |
| 290 | 17 | 2 | `backend/app/domain/services/voice_pipeline/machine_detection.py` |
| 291 | 29 | 18 | `backend/app/domain/services/voice_pipeline/realtime_bridge.py` |
| 292 | 124 | 38 | `backend/app/domain/services/voice_pipeline/tts_playback.py` |
| 293 | 27 | 0 | `backend/app/domain/services/voice_pipeline/turn_ender.py` |
| 294 | 45 | 39 | `backend/app/domain/services/voice_pipeline/turn_streamer.py` |
| 295 | 26 | 2 | `backend/app/domain/services/voice_pipeline/voicemail_detector.py` |
| 296 | 43 | 0 | `backend/app/infrastructure/metrics/gateway_metrics.py` |
| 297 | 313 | 0 | `backend/app/infrastructure/metrics/inbound_metrics.py` |
| 298 | 3624 | 326 | `backend/app/infrastructure/telephony/asterisk_adapter.py` |
| 299 | 34 | 19 | `backend/app/infrastructure/telephony/freeswitch_adapter.py` |
| 300 | 226 | 154 | `backend/app/infrastructure/telephony/freeswitch_esl.py` |
| 301 | 74 | 17 | `backend/app/infrastructure/telephony/pjsip_config_generator.py` |
| 302 | 260 | 40 | `backend/app/infrastructure/telephony/telephony_media_gateway.py` |
| 303 | 323 | 52 | `backend/app/main.py` |
| 304 | 145 | 128 | `backend/app/services/email_service.py` |
| 305 | 63 | 0 | `backend/app/services/scripts/call_transcript_persister.py` |
| 306 | 69 | 0 | `backend/app/services/scripts/knowledge/retrieval.py` |
| 307 | 39 | 0 | `backend/app/services/scripts/knowledge/session_inject.py` |
| 308 | 41 | 0 | `backend/app/services/scripts/realtime_instructions.py` |
| 309 | 309 | 185 | `backend/app/workers/dialer_worker.py` |
| 310 | 93 | 82 | `backend/app/workers/reminder_worker.py` |
| 311 | 53 | 11 | `backend/database/MIGRATIONS.md` |
| 312 | 632 | 32 | `backend/database/complete_schema.sql` |
| 313 | 6 | 0 | `backend/database/schema/baseline_2026-06-02.sql` |
| 314 | 81 | 0 | `backend/deploy/inbound-synthetic-call.sh` |
| 315 | 2 | 0 | `backend/requirements-dev.txt` |
| 316 | 3 | 2 | `backend/requirements.in` |
| 317 | 6 | 4 | `backend/requirements.txt` |
| 318 | 429 | 0 | `backend/scripts/backfill_dnc_normalized_numbers.py` |
| 319 | 51 | 0 | `backend/scripts/build_voice_gateway_release.sh` |
| 320 | 103 | 0 | `backend/scripts/generate_emergency_voice_clips.py` |
| 321 | 1440 | 0 | `backend/scripts/isolation_matrix.py` |
| 322 | 567 | 60 | `backend/scripts/loadtest_calls.py` |
| 323 | 98 | 11 | `backend/scripts/report_frozen_batch.py` |
| 324 | 647 | 0 | `backend/scripts/rls_acquire_inventory.py` |
| 325 | 7 | 8 | `backend/scripts/seed_rbac.py` |
| 326 | 842 | 0 | `backend/scripts/seed_rbac_standalone.py` |
| 327 | 1447 | 0 | `backend/scripts/seed_validation_tenants.py` |
| 328 | 383 | 29 | `backend/scripts/soak_runner.sh` |
| 329 | 178 | 25 | `backend/scripts/trunk_live_status_updater.py` |
| 330 | 257 | 0 | `backend/scripts/verify_deploy_drain_manifest.py` |
| 331 | 157 | 0 | `backend/scripts/verify_restore_compatibility.py` |
| 332 | 8 | 0 | `backend/systemd/inbound-synthetic.env.example` |
| 333 | 3 | 1 | `backend/systemd/install-services.sh` |
| 334 | 15 | 0 | `backend/systemd/talky-inbound-synthetic.service` |
| 335 | 11 | 0 | `backend/systemd/talky-inbound-synthetic.timer` |
| 336 | 13 | 34 | `backend/systemd/talky-voice-gateway.service` |
| 337 | 91 | 0 | `backend/tests/integration/test_admin_media_deletion_migration_compat.py` |
| 338 | 900 | 0 | `backend/tests/integration/test_bootstrap_contract_repair.py` |
| 339 | 451 | 0 | `backend/tests/integration/test_inbound_hold_resolution.py` |
| 340 | 403 | 0 | `backend/tests/integration/test_inbound_reassignment_history.py` |
| 341 | 30 | 2 | `backend/tests/security/test_idor_tenant_scoping.py` |
| 342 | 886 | 0 | `backend/tests/security/test_isolation_matrix_runner.py` |
| 343 | 4 | 0 | `backend/tests/security/test_kb_tool_injection.py` |
| 344 | 4 | 0 | `backend/tests/security/test_kms_master_key_gate.py` |
| 345 | 819 | 0 | `backend/tests/security/test_rbac.py` |
| 346 | 190 | 0 | `backend/tests/unit/test_admin_call_currency.py` |
| 347 | 145 | 0 | `backend/tests/unit/test_admin_inbound_api.py` |
| 348 | 701 | 1 | `backend/tests/unit/test_admin_operational_media_controls.py` |
| 349 | 536 | 0 | `backend/tests/unit/test_admin_tenant_lifecycle.py` |
| 350 | 125 | 0 | `backend/tests/unit/test_admin_usage_currency_scope.py` |
| 351 | 73 | 0 | `backend/tests/unit/test_ai_config_migration_fresh_bootstrap.py` |
| 352 | 179 | 0 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py` |
| 353 | 109 | 0 | `backend/tests/unit/test_asterisk_inbound_meta.py` |
| 354 | 1833 | 0 | `backend/tests/unit/test_asterisk_preanswer_admission.py` |
| 355 | 739 | 0 | `backend/tests/unit/test_asterisk_supervised_transfer.py` |
| 356 | 423 | 0 | `backend/tests/unit/test_asterisk_transfer_observability.py` |
| 357 | 188 | 0 | `backend/tests/unit/test_backfill_dnc_normalized_numbers.py` |
| 358 | 17 | 3 | `backend/tests/unit/test_barge_in_audibility.py` |
| 359 | 249 | 0 | `backend/tests/unit/test_billing_plan_change_preserves_topup_minutes.py` |
| 360 | 133 | 0 | `backend/tests/unit/test_billing_topup_webhook_routing.py` |
| 361 | 283 | 0 | `backend/tests/unit/test_bootstrap_contract_repair_migration.py` |
| 362 | 202 | 0 | `backend/tests/unit/test_bootstrap_contract_repair_normalization.py` |
| 363 | 51 | 0 | `backend/tests/unit/test_bridge_passes_lead_id_to_guard.py` |
| 364 | 184 | 0 | `backend/tests/unit/test_call_feedback_service.py` |
| 365 | 16 | 3 | `backend/tests/unit/test_call_guard_dnc_fail_closed.py` |
| 366 | 14 | 2 | `backend/tests/unit/test_call_guard_subscription_cache.py` |
| 367 | 3 | 1 | `backend/tests/unit/test_call_metrics_persist.py` |
| 368 | 251 | 8 | `backend/tests/unit/test_call_service_pooled_teardown.py` |
| 369 | 148 | 0 | `backend/tests/unit/test_call_status_terminal_monotonic.py` |
| 370 | 170 | 0 | `backend/tests/unit/test_call_terminal_settlement_migration.py` |
| 371 | 221 | 0 | `backend/tests/unit/test_calls_inbound_projection.py` |
| 372 | 1 | 1 | `backend/tests/unit/test_campaign_test_ws.py` |
| 373 | 200 | 0 | `backend/tests/unit/test_company_name_fallback.py` |
| 374 | 433 | 0 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py` |
| 375 | 477 | 0 | `backend/tests/unit/test_confirmation_aware_hangup.py` |
| 376 | 80 | 4 | `backend/tests/unit/test_contact_fields.py` |
| 377 | 247 | 0 | `backend/tests/unit/test_contact_import_validation.py` |
| 378 | 23 | 6 | `backend/tests/unit/test_credential_resolver.py` |
| 379 | 41 | 0 | `backend/tests/unit/test_dashboard_summary.py` |
| 380 | 159 | 0 | `backend/tests/unit/test_day10_sip_harness_contract.py` |
| 381 | 34 | 7 | `backend/tests/unit/test_dialer_lifecycle_and_reaper.py` |
| 382 | 8 | 0 | `backend/tests/unit/test_dialer_worker_block_visibility.py` |
| 383 | 9 | 0 | `backend/tests/unit/test_dialer_worker_call_record.py` |
| 384 | 39 | 3 | `backend/tests/unit/test_disposition_retry_bounds.py` |
| 385 | 57 | 0 | `backend/tests/unit/test_dnc_service.py` |
| 386 | 19 | 0 | `backend/tests/unit/test_emergency_voice_clips.py` |
| 387 | 500 | 10 | `backend/tests/unit/test_freeswitch_transfer_api.py` |
| 388 | 128 | 4 | `backend/tests/unit/test_freeswitch_transfer_control.py` |
| 389 | 128 | 0 | `backend/tests/unit/test_global_concurrency.py` |
| 390 | 1908 | 0 | `backend/tests/unit/test_inbound_admission.py` |
| 391 | 189 | 0 | `backend/tests/unit/test_inbound_alerting_contract.py` |
| 392 | 236 | 0 | `backend/tests/unit/test_inbound_amd_isolation.py` |
| 393 | 46 | 0 | `backend/tests/unit/test_inbound_billing_four_eye_migration.py` |
| 394 | 38 | 0 | `backend/tests/unit/test_inbound_billing_hold_migration.py` |
| 395 | 95 | 0 | `backend/tests/unit/test_inbound_business_hours.py` |
| 396 | 1999 | 0 | `backend/tests/unit/test_inbound_campaign_service.py` |
| 397 | 1104 | 0 | `backend/tests/unit/test_inbound_hold_resolution.py` |
| 398 | 31 | 0 | `backend/tests/unit/test_inbound_lease_safety_migration.py` |
| 399 | 1432 | 0 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py` |
| 400 | 34 | 0 | `backend/tests/unit/test_inbound_rejection_log_migration.py` |
| 401 | 227 | 128 | `backend/tests/unit/test_inbound_router.py` |
| 402 | 256 | 0 | `backend/tests/unit/test_inbound_startup_guards.py` |
| 403 | 41 | 0 | `backend/tests/unit/test_inbound_synthetic_monitor.py` |
| 404 | 530 | 0 | `backend/tests/unit/test_inbound_transfer_billing.py` |
| 405 | 1130 | 0 | `backend/tests/unit/test_inbound_transfer_controls.py` |
| 406 | 47 | 0 | `backend/tests/unit/test_knowledge_session_inject.py` |
| 407 | 463 | 0 | `backend/tests/unit/test_lead_do_not_call_suppression.py` |
| 408 | 319 | 0 | `backend/tests/unit/test_lead_timezone_precedence.py` |
| 409 | 21 | 3 | `backend/tests/unit/test_legacy_campaign_audit.py` |
| 410 | 7 | 1 | `backend/tests/unit/test_lifecycle_teardown_reliability.py` |
| 411 | 354 | 0 | `backend/tests/unit/test_lifecycle_wrap_up_nudge.py` |
| 412 | 723 | 0 | `backend/tests/unit/test_live_call_lead_capture.py` |
| 413 | 69 | 0 | `backend/tests/unit/test_main_ownership_fail_open.py` |
| 414 | 124 | 0 | `backend/tests/unit/test_main_telephony_shutdown.py` |
| 415 | 387 | 0 | `backend/tests/unit/test_media_delete_recovery_migration.py` |
| 416 | 498 | 0 | `backend/tests/unit/test_media_delete_request_keys_migration.py` |
| 417 | 204 | 0 | `backend/tests/unit/test_media_hold_serialization_migration.py` |
| 418 | 18 | 0 | `backend/tests/unit/test_minutes_quota.py` |
| 419 | 81 | 0 | `backend/tests/unit/test_phone_normalization_intl.py` |
| 420 | 241 | 0 | `backend/tests/unit/test_phone_normalizer_equivalence.py` |
| 421 | 59 | 1 | `backend/tests/unit/test_pjsip_config_generator.py` |
| 422 | 21 | 1 | `backend/tests/unit/test_postgres_adapter.py` |
| 423 | 99 | 0 | `backend/tests/unit/test_prod_fail_closed.py` |
| 424 | 181 | 0 | `backend/tests/unit/test_production_migration_deploy_contract.py` |
| 425 | 283 | 0 | `backend/tests/unit/test_prompt_identity_persist.py` |
| 426 | 73 | 0 | `backend/tests/unit/test_queue_retry_idempotency.py` |
| 427 | 310 | 0 | `backend/tests/unit/test_realtime_inbound_opening.py` |
| 428 | 8 | 2 | `backend/tests/unit/test_realtime_quality_pass.py` |
| 429 | 158 | 13 | `backend/tests/unit/test_recording_disclosure.py` |
| 430 | 24 | 0 | `backend/tests/unit/test_recording_offload.py` |
| 431 | 168 | 0 | `backend/tests/unit/test_recording_permission_migration.py` |
| 432 | 65 | 17 | `backend/tests/unit/test_recording_policy.py` |
| 433 | 237 | 0 | `backend/tests/unit/test_recording_storage_permanent_delete.py` |
| 434 | 808 | 0 | `backend/tests/unit/test_recordings_endpoint_contract.py` |
| 435 | 659 | 0 | `backend/tests/unit/test_release_load_tools.py` |
| 436 | 413 | 0 | `backend/tests/unit/test_release_operational_safety.py` |
| 437 | 101 | 0 | `backend/tests/unit/test_report_frozen_batch.py` |
| 438 | 252 | 20 | `backend/tests/unit/test_rls_set_local_invariant.py` |
| 439 | 579 | 0 | `backend/tests/unit/test_seed_rbac_standalone.py` |
| 440 | 599 | 0 | `backend/tests/unit/test_seed_validation_tenants.py` |
| 441 | 2 | 0 | `backend/tests/unit/test_session_scratch_attrs.py` |
| 442 | 351 | 0 | `backend/tests/unit/test_setup_asterisk_contract.py` |
| 443 | 42 | 1 | `backend/tests/unit/test_sip_trunk_metadata.py` |
| 444 | 435 | 0 | `backend/tests/unit/test_subscription_status_vocabulary.py` |
| 445 | 412 | 0 | `backend/tests/unit/test_telephony_bridge_auth.py` |
| 446 | 237 | 5 | `backend/tests/unit/test_telephony_bridge_first_speaker.py` |
| 447 | 445 | 19 | `backend/tests/unit/test_telephony_concurrency_limiter.py` |
| 448 | 88 | 20 | `backend/tests/unit/test_telephony_concurrency_races.py` |
| 449 | 290 | 0 | `backend/tests/unit/test_telephony_gateway_audio_validation.py` |
| 450 | 52 | 3 | `backend/tests/unit/test_telephony_lifecycle_watchdog_hangup.py` |
| 451 | 256 | 0 | `backend/tests/unit/test_telephony_media_gateway.py` |
| 452 | 261 | 0 | `backend/tests/unit/test_telephony_media_reconcile.py` |
| 453 | 321 | 5 | `backend/tests/unit/test_telephony_observability.py` |
| 454 | 1611 | 0 | `backend/tests/unit/test_telephony_orphan_recovery_confirmation.py` |
| 455 | 636 | 16 | `backend/tests/unit/test_telephony_redis_state_backend.py` |
| 456 | 2 | 0 | `backend/tests/unit/test_telephony_runtime_api.py` |
| 457 | 2 | 0 | `backend/tests/unit/test_telephony_sip_api.py` |
| 458 | 73 | 0 | `backend/tests/unit/test_telephony_sip_rbac.py` |
| 459 | 120 | 0 | `backend/tests/unit/test_telephony_start_ownership.py` |
| 460 | 108 | 0 | `backend/tests/unit/test_tenant_phone_number_verification_security.py` |
| 461 | 6 | 6 | `backend/tests/unit/test_tenant_rls.py` |
| 462 | 105 | 1 | `backend/tests/unit/test_topup_service.py` |
| 463 | 298 | 0 | `backend/tests/unit/test_transfer_restart_recovery.py` |
| 464 | 502 | 0 | `backend/tests/unit/test_transfer_restart_recovery_wiring.py` |
| 465 | 91 | 0 | `backend/tests/unit/test_trunk_live_status_updater.py` |
| 466 | 61 | 0 | `backend/tests/unit/test_trunk_probe_security.py` |
| 467 | 104 | 1 | `backend/tests/unit/test_trunk_resolver.py` |
| 468 | 40 | 0 | `backend/tests/unit/test_trunk_runtime_status_migration.py` |
| 469 | 40 | 6 | `backend/tests/unit/test_tts_empty_stream.py` |
| 470 | 525 | 0 | `backend/tests/unit/test_user_profiles_role_widen_migration.py` |
| 471 | 8 | 0 | `backend/uv.lock` |
| 472 | 181 | 22 | `deploy_to_server.sh` |
| 473 | 43 | 10 | `docs/ARCHITECTURE.md` |
| 474 | 288 | 123 | `docs/DEPLOYMENT.md` |
| 475 | 867 | 0 | `docs/INBOUND_CALLING_RELEASE_GATE.md` |
| 476 | 275 | 111 | `docs/RUNBOOK.md` |
| 477 | 71 | 0 | `docs/VOICE_GATEWAY_CODEC_DECISION.md` |
| 478 | 89 | 0 | `docs/sessions/2026-08-30-phase1-voice-gateway-hardening.md` |
| 479 | 130 | 0 | `docs/sessions/2026-08-30-voice-gateway-go-live-evidence.md` |
| 480 | 394 | 0 | `docs/sessions/reports/report14.md` |
| 481 | 3 | 3 | `docs/v2/00-ground-truth.md` |
| 482 | 11 | 6 | `docs/v2/09-known-issues.md` |
| 483 | 151 | 111 | `goals.md` |
| 484 | 9 | 0 | `services/voice-gateway-cpp/CMakeLists.txt` |
| 485 | 41 | 0 | `services/voice-gateway-cpp/README.md` |
| 486 | 14 | 0 | `services/voice-gateway-cpp/include/voice_gateway/http_server.h` |
| 487 | 6 | 8 | `services/voice-gateway-cpp/include/voice_gateway/session.h` |
| 488 | 5 | 0 | `services/voice-gateway-cpp/include/voice_gateway/session_registry.h` |
| 489 | 499 | 102 | `services/voice-gateway-cpp/src/http_server.cpp` |
| 490 | 10 | 1 | `services/voice-gateway-cpp/src/main.cpp` |
| 491 | 30 | 17 | `services/voice-gateway-cpp/src/session.cpp` |
| 492 | 26 | 7 | `services/voice-gateway-cpp/src/session_registry.cpp` |
| 493 | 14 | 1 | `services/voice-gateway-cpp/tests/run_gate.sh` |
| 494 | 366 | 34 | `services/voice-gateway-cpp/tests/test_gateway_fixes.cpp` |
| 495 | 75 | 22 | `setup-asterisk.sh` |
| 496 | 18 | 17 | `telephony/asterisk/conf/extensions.conf` |
| 497 | 10 | 2 | `telephony/asterisk/conf/pjsip.conf` |
| 498 | 13 | 0 | `telephony/asterisk/conf/talky-inbound.conf` |
| 499 | 12 | 1 | `telephony/deploy/docker/.env.telephony.example` |
| 500 | 3 | 0 | `telephony/deploy/docker/docker-compose.observability.yml` |
| 501 | 24 | 5 | `telephony/deploy/docker/docker-compose.telephony.yml` |
| 502 | 103 | 10 | `telephony/docs/phase_3/04_ws_l_stage_controller_runbook.md` |
| 503 | 51 | 33 | `telephony/docs/phase_3/28_day10_concurrency_soak_validation_execution_plan.md` |
| 504 | 10 | 2 | `telephony/docs/phase_3/day10_concurrency_soak_evidence.md` |
| 505 | 6 | 3 | `telephony/docs/phase_3/evidence/day10/day10_bargein_load_report.json` |
| 506 | 17 | 4 | `telephony/docs/phase_3/evidence/day10/day10_go_no_go.json` |
| 507 | 16 | 11 | `telephony/docs/phase_3/evidence/day10/day10_go_no_go_checklist.md` |
| 508 | 3 | 4 | `telephony/kamailio/conf/dispatcher.list` |
| 509 | 90 | 3 | `telephony/observability/README.md` |
| 510 | 13 | 53 | `telephony/observability/alertmanager/alertmanager.yml` |
| 511 | 13 | 4 | `telephony/observability/prometheus/prometheus.yml` |
| 512 | 117 | 0 | `telephony/observability/prometheus/rules/telephony_ws_k_rules.yml` |
| 513 | 6 | 9 | `telephony/opensips/conf/dispatcher.list` |
| 514 | 72 | 45 | `telephony/opensips/conf/opensips-with-auth.cfg` |
| 515 | 85 | 49 | `telephony/opensips/conf/opensips.cfg` |
| 516 | - | - | `telephony/scripts/__pycache__/day10_concurrency_soak_probe.cpython-312.pyc` |
| 517 | - | - | `telephony/scripts/__pycache__/day4_rtp_probe.cpython-312.pyc` |
| 518 | - | - | `telephony/scripts/__pycache__/day5_ari_external_media_controller.cpython-312.pyc` |
| 519 | - | - | `telephony/scripts/__pycache__/day6_media_resilience_probe.cpython-312.pyc` |
| 520 | - | - | `telephony/scripts/__pycache__/day8_tts_bargein_probe.cpython-312.pyc` |
| 521 | 204 | 0 | `telephony/scripts/assert_canary_ingress.sh` |
| 522 | 16 | 0 | `telephony/scripts/canary_freeze.sh` |
| 523 | 47 | 0 | `telephony/scripts/canary_lock.sh` |
| 524 | 28 | 8 | `telephony/scripts/canary_rollback.sh` |
| 525 | 117 | 22 | `telephony/scripts/canary_set_stage.sh` |
| 526 | 307 | 65 | `telephony/scripts/canary_stage_controller.sh` |
| 527 | 157 | 43 | `telephony/scripts/day10_concurrency_soak_probe.py` |
| 528 | 29 | 5 | `telephony/scripts/day4_rtp_probe.py` |
| 529 | 143 | 33 | `telephony/scripts/day5_ari_external_media_controller.py` |
| 530 | 68 | 16 | `telephony/scripts/day6_media_resilience_probe.py` |
| 531 | 121 | 30 | `telephony/scripts/day8_tts_bargein_probe.py` |
| 532 | 33 | 0 | `telephony/scripts/gateway_test_env.sh` |
| 533 | 4 | 1 | `telephony/scripts/generate_secure_passwords.sh` |
| 534 | 202 | 14 | `telephony/scripts/verify_day10_concurrency_soak.sh` |
| 535 | 15 | 12 | `telephony/scripts/verify_day3_opensips_edge.sh` |
| 536 | 3 | 0 | `telephony/scripts/verify_day4_cpp_gateway.sh` |
| 537 | 3 | 0 | `telephony/scripts/verify_day5_asterisk_cpp_echo.sh` |
| 538 | 3 | 0 | `telephony/scripts/verify_day6_media_resilience.sh` |
| 539 | 3 | 0 | `telephony/scripts/verify_day7_stt_streaming.sh` |
| 540 | 3 | 0 | `telephony/scripts/verify_day8_tts_bargein.sh` |
| 541 | 8 | 1 | `telephony/scripts/verify_day9_transfer_tenant_controls.sh` |
| 542 | 31 | 22 | `telephony/scripts/verify_ws_e.sh` |
| 543 | 7 | 0 | `telephony/scripts/verify_ws_k.sh` |
| 544 | 95 | 64 | `telephony/scripts/verify_ws_l.sh` |
| 545 | 24 | 62 | `telephony/scripts/verify_ws_o.sh` |
| 546 | - | - | `telephony/tests/__pycache__/test_telephony_stack.cpython-312.pyc` |
| 547 | 1122 | 57 | `telephony/tests/test_telephony_stack.py` |
| 548 | 2 | 2 | `tickets/00-PLAN-REVISIONS.md` |
| 549 | 1 | 1 | `tickets/2026-07-24_DAY-02.md` |
| 550 | 1 | 1 | `tickets/README.md` |

## Appendix D — named inbound/backend proof contracts

This is the source-level catalog of named tests in the inbound, Asterisk, media-gateway, hangup, projection, RLS and emergency-audio proof set. It records what was asserted, not only the aggregate pass count.

| # | Source | Test declaration |
|---:|---|---|
| 1 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:8` | `def test_gateway_session_digest_is_deterministic_and_covers_configuration():` |
| 2 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:29` | `def test_gateway_health_contract_requires_protocol_v2_pcmu_and_callback_v2():` |
| 3 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:52` | `async def test_start_gateway_session_requires_matching_protocol_v2_ack_in_production(monkeypatch):` |
| 4 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:88` | `async def test_start_gateway_session_rejects_mismatched_digest_ack(monkeypatch):` |
| 5 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:118` | `async def test_start_gateway_session_rejects_empty_ack_when_required(monkeypatch):` |
| 6 | `backend/tests/unit/test_asterisk_gateway_session_protocol.py:142` | `async def test_start_gateway_session_rejects_wrong_protocol_or_codec(monkeypatch):` |
| 7 | `backend/tests/unit/test_asterisk_inbound_meta.py:22` | `def test_extract_did_and_context_from_dialplan():` |
| 8 | `backend/tests/unit/test_asterisk_inbound_meta.py:40` | `def test_extract_falls_back_to_connected_then_args():` |
| 9 | `backend/tests/unit/test_asterisk_inbound_meta.py:65` | `def test_extracts_canonical_direction_did_and_context_args():` |
| 10 | `backend/tests/unit/test_asterisk_inbound_meta.py:86` | `def test_inbound_shape_log_masks_ani_and_did(caplog):` |
| 11 | `backend/tests/unit/test_asterisk_inbound_meta.py:105` | `def test_extract_tolerates_missing_fields():` |
| 12 | `backend/tests/unit/test_asterisk_inbound_meta.py:113` | `def test_debug_dump_is_one_time():` |
| 13 | `backend/tests/unit/test_asterisk_inbound_meta.py:124` | `async def test_inverse_inventory_is_application_scoped_and_human_channel_only():` |
| 14 | `backend/tests/unit/test_asterisk_inbound_meta.py:159` | `async def test_inverse_inventory_failure_is_not_an_empty_authoritative_result():` |
| 15 | `backend/tests/unit/test_asterisk_inbound_meta.py:171` | `def test_inverse_inventory_excludes_every_locally_managed_physical_leg():` |
| 16 | `backend/tests/unit/test_asterisk_preanswer_admission.py:69` | `async def test_inbound_admission_precedes_answer_and_media_allocation(monkeypatch):` |
| 17 | `backend/tests/unit/test_asterisk_preanswer_admission.py:156` | `async def test_answer_persistence_failure_hangs_up_before_media_and_finalizes():` |
| 18 | `backend/tests/unit/test_asterisk_preanswer_admission.py:190` | `async def test_ambiguous_answer_request_failure_finalizes_never_releases():` |
| 19 | `backend/tests/unit/test_asterisk_preanswer_admission.py:231` | `async def test_answer_http_response_only_releases_when_noncommit_is_proven(` |
| 20 | `backend/tests/unit/test_asterisk_preanswer_admission.py:260` | `async def test_crash_after_answer_2xx_leaves_durable_ambiguous_intent(` |
| 21 | `backend/tests/unit/test_asterisk_preanswer_admission.py:312` | `async def test_disconnect_cancels_post_answer_handoff_and_cleans_owned_channel():` |
| 22 | `backend/tests/unit/test_asterisk_preanswer_admission.py:385` | `def test_pending_handoff_fence_refuses_non_inbound_or_unadmitted_channels():` |
| 23 | `backend/tests/unit/test_asterisk_preanswer_admission.py:408` | `async def test_denied_inbound_hangs_up_without_answer_or_media():` |
| 24 | `backend/tests/unit/test_asterisk_preanswer_admission.py:444` | `async def test_unclaimed_denial_registers_durable_retry_until_absence_proof(` |
| 25 | `backend/tests/unit/test_asterisk_preanswer_admission.py:519` | `async def test_missing_admission_callback_fails_closed():` |
| 26 | `backend/tests/unit/test_asterisk_preanswer_admission.py:539` | `async def test_missing_finalizer_denies_before_reserving():` |
| 27 | `backend/tests/unit/test_asterisk_preanswer_admission.py:561` | `async def test_missing_answer_persistence_hook_denies_before_reserving():` |
| 28 | `backend/tests/unit/test_asterisk_preanswer_admission.py:584` | `async def test_duplicate_stasis_start_admits_only_once():` |
| 29 | `backend/tests/unit/test_asterisk_preanswer_admission.py:621` | `async def test_media_setup_failure_releases_admission_once():` |
| 30 | `backend/tests/unit/test_asterisk_preanswer_admission.py:660` | `async def test_media_setup_failure_retries_unconfirmed_hangup_before_release():` |
| 31 | `backend/tests/unit/test_asterisk_preanswer_admission.py:700` | `async def test_media_setup_cleanup_proves_every_resource_before_release():` |
| 32 | `backend/tests/unit/test_asterisk_preanswer_admission.py:753` | `async def test_cancelled_media_setup_still_cleans_before_release():` |
| 33 | `backend/tests/unit/test_asterisk_preanswer_admission.py:814` | `async def test_active_call_terminal_is_owned_by_lifecycle_not_adapter_release():` |
| 34 | `backend/tests/unit/test_asterisk_preanswer_admission.py:846` | `async def test_bridge_timeout_after_create_cleans_requested_deterministic_id():` |
| 35 | `backend/tests/unit/test_asterisk_preanswer_admission.py:881` | `async def test_external_media_timeout_after_create_cleans_requested_channel_id():` |
| 36 | `backend/tests/unit/test_asterisk_preanswer_admission.py:921` | `async def test_duplicate_stasis_start_is_fenced_while_cleanup_owns_admission():` |
| 37 | `backend/tests/unit/test_asterisk_preanswer_admission.py:939` | `async def test_stasis_end_during_cleanup_never_dispatches_lifecycle_finalizer():` |
| 38 | `backend/tests/unit/test_asterisk_preanswer_admission.py:963` | `async def test_bridge_delete_422_is_unconfirmed_and_retried_before_release():` |
| 39 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1013` | `async def test_disconnect_drains_cancelled_setup_cleanup_before_closing_ari():` |
| 40 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1088` | `async def test_terminal_during_setup_delegates_to_setup_cleanup_once():` |
| 41 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1152` | `async def test_terminal_event_burst_claims_active_teardown_once():` |
| 42 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1204` | `async def test_disconnect_waits_for_tracked_terminal_cleanup_before_session_close():` |
| 43 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1251` | `async def test_forced_disconnect_is_bounded_and_retains_unconfirmed_identity():` |
| 44 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1291` | `async def test_disconnect_fence_blocks_all_new_stasis_start_routes():` |
| 45 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1325` | `async def test_terminal_before_lifecycle_acceptance_uses_adapter_cleanup_only():` |
| 46 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1386` | `async def test_terminal_after_lifecycle_acceptance_uses_lifecycle_once():` |
| 47 | `backend/tests/unit/test_asterisk_preanswer_admission.py:1445` | `async def test_terminal_race_after_real_lifecycle_provisional_registration_unwinds_once(` |
| 48 | `backend/tests/unit/test_asterisk_supervised_transfer.py:33` | `async def test_supervised_transfer_waits_for_answer_and_uses_tenant_trunk():` |
| 49 | `backend/tests/unit/test_asterisk_supervised_transfer.py:123` | `async def test_returned_transfer_id_mismatch_is_rejected_before_bridge_or_dial():` |
| 50 | `backend/tests/unit/test_asterisk_supervised_transfer.py:172` | `async def test_stasis_target_identity_mismatch_never_rebinds_planned_leg(` |
| 51 | `backend/tests/unit/test_asterisk_supervised_transfer.py:216` | `async def test_gateway_stop_failure_retains_retryable_media_ownership_and_no_success():` |
| 52 | `backend/tests/unit/test_asterisk_supervised_transfer.py:268` | `async def test_platform_default_transfer_uses_shared_configured_endpoint(monkeypatch):` |
| 53 | `backend/tests/unit/test_asterisk_supervised_transfer.py:308` | `async def test_busy_target_keeps_ai_and_caller_connected():` |
| 54 | `backend/tests/unit/test_asterisk_supervised_transfer.py:364` | `async def test_busy_then_late_up_cannot_resurrect_target_or_start_handoff():` |
| 55 | `backend/tests/unit/test_asterisk_supervised_transfer.py:435` | `async def test_termination_fence_rejects_transfer_before_target_creation():` |
| 56 | `backend/tests/unit/test_asterisk_supervised_transfer.py:472` | `async def test_provider_leg_collision_across_parents_is_rejected_without_ari():` |
| 57 | `backend/tests/unit/test_asterisk_supervised_transfer.py:530` | `async def test_connected_target_hangup_ends_parent_logical_call():` |
| 58 | `backend/tests/unit/test_asterisk_supervised_transfer.py:575` | `async def test_parent_terminal_defers_logical_end_until_transfer_target_absent():` |
| 59 | `backend/tests/unit/test_asterisk_supervised_transfer.py:653` | `async def test_failed_transfer_relationship_survives_204_until_parent_all_leg_proof():` |
| 60 | `backend/tests/unit/test_asterisk_supervised_transfer.py:734` | `async def test_asterisk_does_not_claim_unsupported_transfer_modes():` |
| 61 | `backend/tests/unit/test_asterisk_transfer_observability.py:103` | `async def test_connected_transfer_records_one_attempt_and_one_terminal_outcome(` |
| 62 | `backend/tests/unit/test_asterisk_transfer_observability.py:177` | `async def test_failed_target_records_cleanup_and_terminal_only_once(monkeypatch):` |
| 63 | `backend/tests/unit/test_asterisk_transfer_observability.py:230` | `async def test_parent_cleanup_reports_linked_scope_and_balances_inflight(monkeypatch):` |
| 64 | `backend/tests/unit/test_asterisk_transfer_observability.py:271` | `async def test_unconfirmed_target_cleanup_stays_inflight_until_later_proof(monkeypatch):` |
| 65 | `backend/tests/unit/test_asterisk_transfer_observability.py:339` | `async def test_preflight_rejection_is_not_a_provider_attempt(monkeypatch):` |
| 66 | `backend/tests/unit/test_asterisk_transfer_observability.py:359` | `def test_transfer_metric_labels_are_bounded(monkeypatch):` |
| 67 | `backend/tests/unit/test_asterisk_transfer_observability.py:398` | `def test_transfer_metrics_are_exported_to_the_prometheus_registry():` |
| 68 | `backend/tests/unit/test_calls_inbound_projection.py:28` | `def test_private_inbound_ani_is_never_recovered_from_phone_number():` |
| 69 | `backend/tests/unit/test_calls_inbound_projection.py:39` | `def test_public_inbound_projection_uses_pinned_route_snapshot():` |
| 70 | `backend/tests/unit/test_calls_inbound_projection.py:72` | `def test_denied_inbound_call_never_claims_media_or_transcript_started():` |
| 71 | `backend/tests/unit/test_calls_inbound_projection.py:86` | `def test_disabled_recording_is_independent_of_live_media():` |
| 72 | `backend/tests/unit/test_calls_inbound_projection.py:102` | `def test_base_campaign_and_inbound_config_id_remain_distinct():` |
| 73 | `backend/tests/unit/test_calls_inbound_projection.py:116` | `def test_inbound_config_id_uses_pinned_route_fallback_for_older_snapshots():` |
| 74 | `backend/tests/unit/test_calls_inbound_projection.py:126` | `def test_outbound_from_number_comes_from_durable_call_leg_projection():` |
| 75 | `backend/tests/unit/test_calls_inbound_projection.py:136` | `def test_inbound_history_filter_targets_config_snapshot_not_base_campaign():` |
| 76 | `backend/tests/unit/test_calls_inbound_projection.py:144` | `def test_call_detail_parent_billing_projection_excludes_child_leg_ledger():` |
| 77 | `backend/tests/unit/test_calls_inbound_projection.py:152` | `async def test_rejected_inbound_feed_unions_pre_row_and_after_hours(monkeypatch):` |
| 78 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:104` | `async def test_tenant_unconfirmed_hangup_performs_no_terminal_write(monkeypatch):` |
| 79 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:141` | `async def test_tenant_confirmed_hangup_orders_proof_before_settlement_and_projection(` |
| 80 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:191` | `async def test_tenant_terminal_replay_is_idempotent_after_provider_absence_proof(` |
| 81 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:232` | `async def test_tenant_terminal_replay_does_not_treat_database_state_as_pbx_proof(` |
| 82 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:269` | `async def test_raw_hangup_reports_only_confirmed_provider_absence(monkeypatch):` |
| 83 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:308` | `async def test_raw_hangup_returns_gateway_error_when_proof_is_missing(monkeypatch):` |
| 84 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:387` | `async def test_campaign_bulk_hangup_separates_attempts_from_confirmations(monkeypatch):` |
| 85 | `backend/tests/unit/test_confirmation_aware_call_endpoints.py:422` | `async def test_campaign_bulk_lookup_failure_is_not_zero_success(monkeypatch):` |
| 86 | `backend/tests/unit/test_confirmation_aware_hangup.py:38` | `def test_inbound_denial_reason_mapping_is_explicit_and_fail_closed(reason, reason_code):` |
| 87 | `backend/tests/unit/test_confirmation_aware_hangup.py:43` | `async def test_reasoned_preanswer_hangup_retries_bare_in_same_iteration(monkeypatch):` |
| 88 | `backend/tests/unit/test_confirmation_aware_hangup.py:67` | `async def test_delete_acceptance_is_not_termination_while_channel_remains(monkeypatch):` |
| 89 | `backend/tests/unit/test_confirmation_aware_hangup.py:85` | `async def test_delete_404_is_authoritative_already_absent_proof(monkeypatch):` |
| 90 | `backend/tests/unit/test_confirmation_aware_hangup.py:101` | `async def test_confirmation_waits_until_every_transfer_human_leg_is_absent(monkeypatch):` |
| 91 | `backend/tests/unit/test_confirmation_aware_hangup.py:124` | `async def test_explicit_recovered_legs_share_one_confirmation_deadline(monkeypatch):` |
| 92 | `backend/tests/unit/test_confirmation_aware_hangup.py:146` | `async def test_ari_failure_without_inventory_proof_is_unconfirmed(monkeypatch):` |
| 93 | `backend/tests/unit/test_confirmation_aware_hangup.py:162` | `async def test_hangup_request_does_not_finalize_while_parent_channel_remains(monkeypatch):` |
| 94 | `backend/tests/unit/test_confirmation_aware_hangup.py:216` | `async def test_legacy_adapter_request_is_never_promoted_to_confirmation():` |
| 95 | `backend/tests/unit/test_confirmation_aware_hangup.py:232` | `async def test_persisted_linked_legs_use_one_multi_leg_confirmation_call():` |
| 96 | `backend/tests/unit/test_confirmation_aware_hangup.py:254` | `async def test_linked_legs_fail_closed_without_multi_leg_capability():` |
| 97 | `backend/tests/unit/test_confirmation_aware_hangup.py:275` | `async def test_load_active_provider_leg_ids_uses_durable_parent_lookup(monkeypatch):` |
| 98 | `backend/tests/unit/test_confirmation_aware_hangup.py:315` | `async def test_termination_context_fences_before_snapshotting_linked_legs(monkeypatch):` |
| 99 | `backend/tests/unit/test_confirmation_aware_hangup.py:370` | `async def test_proven_inbound_finalizer_commits_children_parent_global_then_ack(` |
| 100 | `backend/tests/unit/test_confirmation_aware_hangup.py:432` | `async def test_proven_inbound_finalizer_retains_ledger_when_child_commit_fails(` |
| 101 | `backend/tests/unit/test_emergency_voice_clips.py:10` | `def test_emergency_clips_are_checksum_pinned_complete_pcmu_frames():` |
| 102 | `backend/tests/unit/test_emergency_voice_clips.py:18` | `def test_emergency_clips_are_distinct_messages():` |
| 103 | `backend/tests/unit/test_inbound_admission.py:117` | `def test_private_and_invalid_ani_never_create_identity():` |
| 104 | `backend/tests/unit/test_inbound_admission.py:147` | `async def test_pre_row_rejection_resolves_tenant_and_retains_public_ani(monkeypatch):` |
| 105 | `backend/tests/unit/test_inbound_admission.py:182` | `async def test_unowned_rejection_never_retains_caller_ani(monkeypatch):` |
| 106 | `backend/tests/unit/test_inbound_admission.py:233` | `async def test_invalid_admission_input_fails_before_database(admission_request, reason):` |
| 107 | `backend/tests/unit/test_inbound_admission.py:316` | `async def test_admission_persists_anonymous_call_before_reservation(monkeypatch):` |
| 108 | `backend/tests/unit/test_inbound_admission.py:380` | `async def test_admission_pins_knowledge_content_before_answer(monkeypatch):` |
| 109 | `backend/tests/unit/test_inbound_admission.py:429` | `async def test_zero_monthly_usage_allows_next_admission(monkeypatch):` |
| 110 | `backend/tests/unit/test_inbound_admission.py:444` | `async def test_outbound_usage_exhaustion_denies_inbound_reservation(monkeypatch):` |
| 111 | `backend/tests/unit/test_inbound_admission.py:472` | `async def test_concurrency_denial_remains_nonterminal_until_pbx_proof(monkeypatch):` |
| 112 | `backend/tests/unit/test_inbound_admission.py:508` | `async def test_non_positive_allocation_preserves_unlimited_plan_contract(monkeypatch):` |
| 113 | `backend/tests/unit/test_inbound_admission.py:527` | `async def test_admission_reserves_campaign_max_and_shortens_to_remaining_quota(monkeypatch):` |
| 114 | `backend/tests/unit/test_inbound_admission.py:551` | `async def test_invalid_campaign_max_duration_fails_before_answer(monkeypatch):` |
| 115 | `backend/tests/unit/test_inbound_admission.py:572` | `async def test_settlement_switch_does_not_block_preanswer_admission(monkeypatch):` |
| 116 | `backend/tests/unit/test_inbound_admission.py:587` | `async def test_missing_tenant_ai_config_fails_closed_before_answer(monkeypatch):` |
| 117 | `backend/tests/unit/test_inbound_admission.py:603` | `async def test_hot_admission_rejects_stale_or_unhealthy_trunk_evidence(monkeypatch):` |
| 118 | `backend/tests/unit/test_inbound_admission.py:633` | `async def test_hot_admission_requires_bidirectional_trunk_only_for_transfer(monkeypatch):` |
| 119 | `backend/tests/unit/test_inbound_admission.py:679` | `async def test_hot_admission_requires_bidirectional_trunk_for_selected_transfer(monkeypatch):` |
| 120 | `backend/tests/unit/test_inbound_admission.py:712` | `async def test_after_hours_voicemail_action_is_pinned_before_answer(monkeypatch):` |
| 121 | `backend/tests/unit/test_inbound_admission.py:745` | `async def test_after_hours_voicemail_without_pinned_intake_message_is_denied(monkeypatch):` |
| 122 | `backend/tests/unit/test_inbound_admission.py:776` | `async def test_after_hours_hangup_action_is_pinned_before_answer(monkeypatch):` |
| 123 | `backend/tests/unit/test_inbound_admission.py:794` | `async def test_after_hours_transfer_stays_closed_until_runtime_release_gate(monkeypatch):` |
| 124 | `backend/tests/unit/test_inbound_admission.py:845` | `async def test_after_hours_transfer_rejects_out_of_scope_staging_campaign(monkeypatch):` |
| 125 | `backend/tests/unit/test_inbound_admission.py:884` | `async def test_after_hours_transfer_destination_must_be_explicitly_approved(monkeypatch):` |
| 126 | `backend/tests/unit/test_inbound_admission.py:920` | `async def test_invalid_business_schedule_fails_closed_before_call_insert(monkeypatch):` |
| 127 | `backend/tests/unit/test_inbound_admission.py:942` | `async def test_admission_rejects_unimplemented_dtmf_opt_out_promise(monkeypatch):` |
| 128 | `backend/tests/unit/test_inbound_admission.py:1003` | `async def test_provider_identity_is_idempotently_replayed(monkeypatch):` |
| 129 | `backend/tests/unit/test_inbound_admission.py:1031` | `async def test_terminal_or_unproven_provider_replay_is_rejected(monkeypatch, overrides):` |
| 130 | `backend/tests/unit/test_inbound_admission.py:1114` | `async def test_finalization_appends_delta_releases_lease_and_replays(monkeypatch):` |
| 131 | `backend/tests/unit/test_inbound_admission.py:1159` | `async def test_parent_finalization_rejects_nonterminal_transfer_billing(monkeypatch):` |
| 132 | `backend/tests/unit/test_inbound_admission.py:1186` | `async def test_finalization_accepts_every_canonical_terminal_status(` |
| 133 | `backend/tests/unit/test_inbound_admission.py:1213` | `async def test_deferred_settlement_preserves_endpoint_terminal_status(monkeypatch):` |
| 134 | `backend/tests/unit/test_inbound_admission.py:1247` | `async def test_finalization_replay_repairs_only_a_missing_outcome(monkeypatch):` |
| 135 | `backend/tests/unit/test_inbound_admission.py:1271` | `async def test_finalization_rejects_unstable_outcome_identifiers():` |
| 136 | `backend/tests/unit/test_inbound_admission.py:1286` | `async def test_duration_over_reservation_is_held_not_silently_billed(monkeypatch):` |
| 137 | `backend/tests/unit/test_inbound_admission.py:1309` | `async def test_ambiguous_provider_answer_is_held_for_cdr_not_auto_billed(` |
| 138 | `backend/tests/unit/test_inbound_admission.py:1343` | `async def test_ambiguous_answer_hold_cannot_be_auto_finalized_by_replay(monkeypatch):` |
| 139 | `backend/tests/unit/test_inbound_admission.py:1379` | `async def test_finalization_rejects_non_finite_or_negative_cost():` |
| 140 | `backend/tests/unit/test_inbound_admission.py:1396` | `async def test_settlement_kill_switch_holds_without_mutating_ledger(monkeypatch):` |
| 141 | `backend/tests/unit/test_inbound_admission.py:1420` | `async def test_held_replay_uses_first_terminal_billing_facts(monkeypatch):` |
| 142 | `backend/tests/unit/test_inbound_admission.py:1458` | `async def test_switch_hold_overage_becomes_manual_on_retry(monkeypatch):` |
| 143 | `backend/tests/unit/test_inbound_admission.py:1488` | `async def test_release_after_finalization_is_an_out_of_order_replay(monkeypatch):` |
| 144 | `backend/tests/unit/test_inbound_admission.py:1539` | `async def test_reversal_is_one_idempotent_compensating_entry(monkeypatch):` |
| 145 | `backend/tests/unit/test_inbound_admission.py:1582` | `def test_migration_allows_reverse_after_finalize():` |
| 146 | `backend/tests/unit/test_inbound_admission.py:1621` | `async def test_active_admission_heartbeat_refreshes_exact_lease(monkeypatch):` |
| 147 | `backend/tests/unit/test_inbound_admission.py:1645` | `async def test_active_admission_heartbeat_also_refreshes_connected_transfer_lease(monkeypatch):` |
| 148 | `backend/tests/unit/test_inbound_admission.py:1675` | `async def test_reenabled_switch_hold_reconciler_is_bounded_and_reason_specific(` |
| 149 | `backend/tests/unit/test_inbound_admission.py:1737` | `async def test_failed_switch_hold_batch_rotates_so_row_101_is_not_starved(` |
| 150 | `backend/tests/unit/test_inbound_admission.py:1820` | `async def test_stale_reservation_only_queues_confirmed_pbx_recovery(monkeypatch):` |
| 151 | `backend/tests/unit/test_inbound_admission.py:1890` | `async def test_watchdog_is_bounded_single_flight_and_stoppable():` |
| 152 | `backend/tests/unit/test_inbound_alerting_contract.py:60` | `def test_usage_metrics_preserve_only_real_bounded_alert_dimensions(monkeypatch) -> None:` |
| 153 | `backend/tests/unit/test_inbound_alerting_contract.py:79` | `def test_confirmed_first_audio_refreshes_the_inbound_success_signal(monkeypatch) -> None:` |
| 154 | `backend/tests/unit/test_inbound_alerting_contract.py:92` | `def test_inbound_alerts_reference_exported_application_metrics() -> None:` |
| 155 | `backend/tests/unit/test_inbound_alerting_contract.py:132` | `def test_missing_exporter_signals_remain_documented_release_blockers() -> None:` |
| 156 | `backend/tests/unit/test_inbound_alerting_contract.py:159` | `def test_checked_in_alertmanager_has_no_fake_or_inline_destination() -> None:` |
| 157 | `backend/tests/unit/test_inbound_amd_isolation.py:34` | `async def test_interim_amd_never_flags_or_hangs_up_true_inbound_call():` |
| 158 | `backend/tests/unit/test_inbound_amd_isolation.py:49` | `async def test_interim_amd_keeps_outbound_voicemail_hangup_behavior():` |
| 159 | `backend/tests/unit/test_inbound_amd_isolation.py:65` | `async def test_interim_amd_keeps_outbound_adapter_fallback(monkeypatch):` |
| 160 | `backend/tests/unit/test_inbound_amd_isolation.py:103` | `async def test_final_voicemail_detector_never_hangs_up_true_inbound_call(monkeypatch):` |
| 161 | `backend/tests/unit/test_inbound_amd_isolation.py:119` | `async def test_final_voicemail_detector_keeps_outbound_behavior(monkeypatch):` |
| 162 | `backend/tests/unit/test_inbound_amd_isolation.py:139` | `async def test_realtime_bridge_runs_voicemail_detection_only_outbound(` |
| 163 | `backend/tests/unit/test_inbound_amd_isolation.py:178` | `async def test_realtime_orchestrator_pins_direction_on_session_and_bridge(` |
| 164 | `backend/tests/unit/test_inbound_billing_four_eye_migration.py:9` | `def test_0034_is_single_forward_revision_with_durable_four_eye_state() -> None:` |
| 165 | `backend/tests/unit/test_inbound_billing_four_eye_migration.py:24` | `def test_0034_database_enforces_immutable_distinct_approval() -> None:` |
| 166 | `backend/tests/unit/test_inbound_billing_four_eye_migration.py:39` | `def test_0034_downgrade_refuses_to_destroy_approval_evidence() -> None:` |
| 167 | `backend/tests/unit/test_inbound_billing_hold_migration.py:11` | `def test_0032_persists_only_reasoned_inbound_billing_holds() -> None:` |
| 168 | `backend/tests/unit/test_inbound_billing_hold_migration.py:28` | `def test_0032_downgrade_refuses_to_delete_hold_evidence() -> None:` |
| 169 | `backend/tests/unit/test_inbound_business_hours.py:34` | `def test_windows_are_start_inclusive_and_end_exclusive(instant, after_hours):` |
| 170 | `backend/tests/unit/test_inbound_business_hours.py:49` | `def test_overnight_window_carries_into_following_day(instant, after_hours):` |
| 171 | `backend/tests/unit/test_inbound_business_hours.py:55` | `def test_timezone_is_applied_before_weekday_and_time():` |
| 172 | `backend/tests/unit/test_inbound_business_hours.py:65` | `def test_closed_holiday_overrides_weekly_window():` |
| 173 | `backend/tests/unit/test_inbound_business_hours.py:91` | `def test_invalid_timezone_or_schedule_fails_closed(zone, schedule, reason):` |
| 174 | `backend/tests/unit/test_inbound_campaign_service.py:95` | `def test_readiness_is_stable_and_actionable():` |
| 175 | `backend/tests/unit/test_inbound_campaign_service.py:125` | `def test_readiness_requires_fresh_type_appropriate_asterisk_trunk_proof():` |
| 176 | `backend/tests/unit/test_inbound_campaign_service.py:153` | `def test_request_contract_rejects_unverified_shape_before_service_call():` |
| 177 | `backend/tests/unit/test_inbound_campaign_service.py:176` | `def test_only_tenant_admin_and_higher_receive_inbound_mutation_permissions():` |
| 178 | `backend/tests/unit/test_inbound_campaign_service.py:194` | `def test_create_and_assignment_routes_require_database_effective_assign_grant():` |
| 179 | `backend/tests/unit/test_inbound_campaign_service.py:248` | `async def test_inbound_mutation_denies_revoked_tenant_admin_role_grant():` |
| 180 | `backend/tests/unit/test_inbound_campaign_service.py:269` | `async def test_inbound_mutation_honors_direct_user_grant():` |
| 181 | `backend/tests/unit/test_inbound_campaign_service.py:283` | `def test_fresh_schema_and_0022_seed_all_inbound_role_permissions():` |
| 182 | `backend/tests/unit/test_inbound_campaign_service.py:330` | `async def test_idempotency_replays_exact_request_and_rejects_key_reuse():` |
| 183 | `backend/tests/unit/test_inbound_campaign_service.py:398` | `async def test_update_fails_on_optimistic_version_conflict(monkeypatch):` |
| 184 | `backend/tests/unit/test_inbound_campaign_service.py:429` | `async def test_update_rejects_live_and_archived_configs(monkeypatch, status, expected_code):` |
| 185 | `backend/tests/unit/test_inbound_campaign_service.py:455` | `async def test_paused_campaign_can_be_edited(monkeypatch):` |
| 186 | `backend/tests/unit/test_inbound_campaign_service.py:522` | `async def test_generic_update_rejects_did_or_trunk_assignment_mutation(` |
| 187 | `backend/tests/unit/test_inbound_campaign_service.py:558` | `async def test_active_campaign_must_be_paused_before_archive(monkeypatch):` |
| 188 | `backend/tests/unit/test_inbound_campaign_service.py:581` | `def test_readiness_blocks_disabled_platform_and_unsupported_overrides():` |
| 189 | `backend/tests/unit/test_inbound_campaign_service.py:598` | `def test_neutral_frontend_qualification_defaults_do_not_block_and_supported_values_apply():` |
| 190 | `backend/tests/unit/test_inbound_campaign_service.py:623` | `def test_readiness_rejects_invalid_max_call_duration(value):` |
| 191 | `backend/tests/unit/test_inbound_campaign_service.py:636` | `def test_readiness_accepts_default_and_bounded_max_call_duration(policy):` |
| 192 | `backend/tests/unit/test_inbound_campaign_service.py:642` | `def test_readiness_requires_after_hours_destination_in_explicit_allowlist():` |
| 193 | `backend/tests/unit/test_inbound_campaign_service.py:676` | `def test_readiness_requires_bidirectional_trunk_only_when_transfer_requested():` |
| 194 | `backend/tests/unit/test_inbound_campaign_service.py:711` | `def test_readiness_accepts_safe_actions_and_blocks_unproven_transfer_runtime():` |
| 195 | `backend/tests/unit/test_inbound_campaign_service.py:766` | `def test_readiness_allows_transfer_only_with_runtime_and_platform_gates(monkeypatch):` |
| 196 | `backend/tests/unit/test_inbound_campaign_service.py:790` | `def test_campaign_transfer_write_gate_allows_disable_and_approved_staging(monkeypatch):` |
| 197 | `backend/tests/unit/test_inbound_campaign_service.py:811` | `async def test_campaign_transfer_write_requires_platform_gate(monkeypatch):` |
| 198 | `backend/tests/unit/test_inbound_campaign_service.py:870` | `async def test_transfer_create_replay_survives_gate_closure(monkeypatch):` |
| 199 | `backend/tests/unit/test_inbound_campaign_service.py:902` | `async def test_admin_assignment_list_uses_complete_readiness_bundle(monkeypatch):` |
| 200 | `backend/tests/unit/test_inbound_campaign_service.py:941` | `async def test_missing_tenant_control_is_reported_fail_closed(monkeypatch):` |
| 201 | `backend/tests/unit/test_inbound_campaign_service.py:961` | `async def test_production_off_to_on_platform_transition_requires_live_asterisk(` |
| 202 | `backend/tests/unit/test_inbound_campaign_service.py:1000` | `async def test_production_off_to_on_requires_strict_redis_ownership(monkeypatch):` |
| 203 | `backend/tests/unit/test_inbound_campaign_service.py:1073` | `async def test_assignment_requires_existing_verified_tenant_phone_row():` |
| 204 | `backend/tests/unit/test_inbound_campaign_service.py:1087` | `def test_invalid_timezone_has_stable_422_error():` |
| 205 | `backend/tests/unit/test_inbound_campaign_service.py:1105` | `def test_readiness_matches_admission_ai_and_after_hours_contract(overrides, blocker):` |
| 206 | `backend/tests/unit/test_inbound_campaign_service.py:1111` | `def test_live_did_uniqueness_and_archive_loading_are_database_backed():` |
| 207 | `backend/tests/unit/test_inbound_campaign_service.py:1122` | `async def test_expired_idempotency_claim_is_atomically_reclaimed():` |
| 208 | `backend/tests/unit/test_inbound_campaign_service.py:1141` | `async def test_did_availability_requires_verified_tenant_ownership(monkeypatch):` |
| 209 | `backend/tests/unit/test_inbound_campaign_service.py:1169` | `async def test_platform_cannot_enable_unavailable_transfer_runtime(monkeypatch):` |
| 210 | `backend/tests/unit/test_inbound_campaign_service.py:1208` | `async def test_runtime_capabilities_require_code_and_platform_transfer_gates(monkeypatch):` |
| 211 | `backend/tests/unit/test_inbound_campaign_service.py:1270` | `async def test_platform_can_disable_transfer_when_runtime_is_unavailable(monkeypatch):` |
| 212 | `backend/tests/unit/test_inbound_campaign_service.py:1323` | `async def test_tenant_mutations_reject_platform_quarantine(monkeypatch, operation):` |
| 213 | `backend/tests/unit/test_inbound_campaign_service.py:1369` | `async def test_quarantine_locks_config_before_assignment_and_checks_returning(monkeypatch):` |
| 214 | `backend/tests/unit/test_inbound_campaign_service.py:1436` | `async def test_archived_status_filters_are_retrievable_for_tenant_and_admin(monkeypatch):` |
| 215 | `backend/tests/unit/test_inbound_campaign_service.py:1461` | `def test_archive_response_is_reloaded_from_committed_rows():` |
| 216 | `backend/tests/unit/test_inbound_campaign_service.py:1469` | `async def test_assignment_reason_reaches_versioned_update_without_checksum(monkeypatch):` |
| 217 | `backend/tests/unit/test_inbound_campaign_service.py:1596` | `async def test_reassignment_request_only_references_target_owned_config(monkeypatch):` |
| 218 | `backend/tests/unit/test_inbound_campaign_service.py:1646` | `async def test_reassignment_request_never_converts_or_clones_target(monkeypatch):` |
| 219 | `backend/tests/unit/test_inbound_campaign_service.py:1677` | `async def test_reassignment_request_requires_target_owned_config_without_cloning(` |
| 220 | `backend/tests/unit/test_inbound_campaign_service.py:1853` | `async def test_four_eye_approval_moves_current_owner_but_preserves_source(monkeypatch):` |
| 221 | `backend/tests/unit/test_inbound_campaign_service.py:1917` | `async def test_four_eye_approval_fails_closed_when_target_changes(` |
| 222 | `backend/tests/unit/test_inbound_campaign_service.py:1950` | `async def test_four_eye_approval_rejects_target_trunk_without_live_asterisk_proof(` |
| 223 | `backend/tests/unit/test_inbound_campaign_service.py:1992` | `def test_reassignment_schema_preserves_source_and_links_new_target_assignment():` |
| 224 | `backend/tests/unit/test_inbound_hold_resolution.py:405` | `async def test_finalize_request_is_replay_safe_and_cannot_mutate_money(monkeypatch):` |
| 225 | `backend/tests/unit/test_inbound_hold_resolution.py:435` | `async def test_finalize_requester_cannot_self_approve_and_attempt_rolls_back(monkeypatch):` |
| 226 | `backend/tests/unit/test_inbound_hold_resolution.py:475` | `async def test_finalize_approval_is_bound_to_every_immutable_field(` |
| 227 | `backend/tests/unit/test_inbound_hold_resolution.py:519` | `async def test_consumed_approval_cannot_be_replayed_as_another_approver(` |
| 228 | `backend/tests/unit/test_inbound_hold_resolution.py:554` | `async def test_release_ambiguous_answer_is_atomic_audited_and_quota_zero(monkeypatch):` |
| 229 | `backend/tests/unit/test_inbound_hold_resolution.py:584` | `async def test_finalize_over_reservation_accounts_authoritative_duration_once(monkeypatch):` |
| 230 | `backend/tests/unit/test_inbound_hold_resolution.py:633` | `async def test_authoritative_cost_and_currency_are_canonical_and_persisted(monkeypatch):` |
| 231 | `backend/tests/unit/test_inbound_hold_resolution.py:688` | `async def test_authoritative_currency_must_match_reservation_currency(monkeypatch):` |
| 232 | `backend/tests/unit/test_inbound_hold_resolution.py:713` | `async def test_finalize_fails_closed_while_settlement_switch_is_disabled_or_missing(` |
| 233 | `backend/tests/unit/test_inbound_hold_resolution.py:751` | `async def test_release_remains_available_while_settlement_switch_is_off_or_missing(` |
| 234 | `backend/tests/unit/test_inbound_hold_resolution.py:765` | `async def test_opposite_or_mismatched_resolution_is_conflict_not_duplicate(monkeypatch):` |
| 235 | `backend/tests/unit/test_inbound_hold_resolution.py:786` | `async def test_wrong_role_tenant_reason_and_evidence_fail_closed(monkeypatch):` |
| 236 | `backend/tests/unit/test_inbound_hold_resolution.py:811` | `async def test_super_admin_alias_is_canonical_platform_admin(monkeypatch):` |
| 237 | `backend/tests/unit/test_inbound_hold_resolution.py:820` | `async def test_transaction_rolls_back_ledger_and_idempotency_when_cas_fails(monkeypatch):` |
| 238 | `backend/tests/unit/test_inbound_hold_resolution.py:834` | `def test_admin_route_is_platform_only_and_schema_requires_reason_specific_evidence():` |
| 239 | `backend/tests/unit/test_inbound_hold_resolution.py:921` | `async def test_admin_endpoint_forwards_tenant_actor_evidence_and_idempotency(monkeypatch):` |
| 240 | `backend/tests/unit/test_inbound_hold_resolution.py:984` | `async def test_admin_endpoint_forwards_finalize_approval_identity(monkeypatch):` |
| 241 | `backend/tests/unit/test_inbound_hold_resolution.py:1047` | `async def test_authoritative_values_must_fit_postgres_types(monkeypatch, duration, cost, currency):` |
| 242 | `backend/tests/unit/test_inbound_hold_resolution.py:1072` | `async def test_authoritative_cost_and_currency_are_an_atomic_pair(monkeypatch, cost, currency):` |
| 243 | `backend/tests/unit/test_inbound_hold_resolution.py:1089` | `async def test_shared_call_and_ledger_cost_boundaries_are_exact(monkeypatch, cost):` |
| 244 | `backend/tests/unit/test_inbound_lease_safety_migration.py:12` | `def test_0031_normalizes_and_enforces_the_heartbeat_window() -> None:` |
| 245 | `backend/tests/unit/test_inbound_lease_safety_migration.py:27` | `def test_fresh_inbound_foundation_has_the_same_minimum_window() -> None:` |
| 246 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:45` | `async def test_durable_answer_commits_call_before_promoting_cleanup_ledger(` |
| 247 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:105` | `async def test_durable_answer_db_failure_never_promotes_redis(monkeypatch):` |
| 248 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:144` | `async def test_lease_loss_fence_persists_recovery_and_requires_all_leg_proof(` |
| 249 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:192` | `async def test_lease_loss_retries_until_pbx_absence_is_confirmed(monkeypatch):` |
| 250 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:221` | `async def test_heartbeat_errors_exhaust_authority_before_minimum_lease_window(` |
| 251 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:254` | `async def test_post_answer_callback_rejected_by_ownership_fence_delegates_cleanup(` |
| 252 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:276` | `async def test_after_hours_transfer_uses_persisted_leg_and_accepts_durable_handoff(` |
| 253 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:379` | `async def test_post_answer_inbound_capacity_rejection_delegates_before_release(` |
| 254 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:418` | `async def test_cancelled_preanswer_admission_retains_strict_global_slot_for_proof_owner(` |
| 255 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:482` | `async def test_preanswer_cleanup_ledger_precedes_global_lease_and_durable_admission(` |
| 256 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:548` | `async def test_inbound_guards_start_before_optional_answered_status_io(monkeypatch):` |
| 257 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:600` | `async def test_terminal_global_release_precedes_optional_status_projection(monkeypatch):` |
| 258 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:643` | `async def test_canonical_inbound_finalizer_releases_global_slot_after_durable_success(` |
| 259 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:735` | `async def test_canonical_inbound_finalizer_keeps_global_slot_until_durable_retry_succeeds(` |
| 260 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:814` | `async def test_unclaimed_proof_finalizer_retries_strict_global_release_before_completion(` |
| 261 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:874` | `async def test_unclaimed_proof_recovers_lost_committed_admission_before_capacity_release(` |
| 262 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:941` | `async def test_normal_inbound_terminal_path_has_no_early_global_release(monkeypatch):` |
| 263 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:1039` | `async def test_call_end_retries_durable_inbound_finalization_before_return(` |
| 264 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:1113` | `async def test_outbound_database_outage_retains_ledger_and_logical_marker(monkeypatch):` |
| 265 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:1194` | `async def test_normal_inbound_transfer_finalize_failure_retains_retry_ledger(` |
| 266 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:1283` | `async def test_registered_inbound_session_is_not_double_counted_for_local_capacity(` |
| 267 | `backend/tests/unit/test_inbound_lifecycle_fail_closed.py:1353` | `async def test_release_only_finalizer_cancels_preaccept_runtime_guards(monkeypatch):` |
| 268 | `backend/tests/unit/test_inbound_rejection_log_migration.py:6` | `def test_rejection_log_is_append_only_tenant_isolated_and_privacy_safe(monkeypatch):` |
| 269 | `backend/tests/unit/test_inbound_rejection_log_migration.py:27` | `def test_rejection_log_downgrade_drops_only_its_table(monkeypatch):` |
| 270 | `backend/tests/unit/test_inbound_router.py:58` | `def test_normalize_did_produces_one_canonical_e164_form(raw, expected):` |
| 271 | `backend/tests/unit/test_inbound_router.py:63` | `def test_normalize_did_rejects_invalid_values(raw):` |
| 272 | `backend/tests/unit/test_inbound_router.py:67` | `def test_context_can_only_confirm_never_choose_route():` |
| 273 | `backend/tests/unit/test_inbound_router.py:85` | `def test_exact_complete_binding_routes_with_versions():` |
| 274 | `backend/tests/unit/test_inbound_router.py:100` | `def test_conflict_and_incomplete_binding_fail_closed():` |
| 275 | `backend/tests/unit/test_inbound_router.py:107` | `def test_redacted_did_is_stable_and_never_contains_raw_digits():` |
| 276 | `backend/tests/unit/test_inbound_router.py:190` | `async def test_resolver_queries_actual_assignment_tables_and_exact_canonical_did():` |
| 277 | `backend/tests/unit/test_inbound_router.py:212` | `async def test_two_dids_resolve_to_their_own_tenant_and_campaign_without_cross_routing():` |
| 278 | `backend/tests/unit/test_inbound_router.py:252` | `async def test_resolver_never_trusts_context_without_a_valid_did():` |
| 279 | `backend/tests/unit/test_inbound_router.py:270` | `async def test_resolver_rejects_missing_or_ambiguous_routes(rows, reason):` |
| 280 | `backend/tests/unit/test_inbound_router.py:283` | `async def test_resolver_rejects_dependency_failure_without_fallback():` |
| 281 | `backend/tests/unit/test_inbound_startup_guards.py:80` | `async def test_production_inbound_switch_lookup_fails_closed():` |
| 282 | `backend/tests/unit/test_inbound_startup_guards.py:92` | `async def test_enabled_production_inbound_requires_rls_enforced_role():` |
| 283 | `backend/tests/unit/test_inbound_startup_guards.py:117` | `async def test_disabled_or_nonproduction_inbound_skips_role_constraint():` |
| 284 | `backend/tests/unit/test_inbound_startup_guards.py:130` | `def test_enabled_production_inbound_rejects_auto_or_wrong_adapter():` |
| 285 | `backend/tests/unit/test_inbound_startup_guards.py:147` | `def test_disabled_or_nonproduction_inbound_does_not_constrain_adapter():` |
| 286 | `backend/tests/unit/test_inbound_startup_guards.py:156` | `def test_production_adapter_requires_answer_persistence_capability():` |
| 287 | `backend/tests/unit/test_inbound_startup_guards.py:173` | `def test_live_production_adapter_requires_all_answer_callbacks_wired():` |
| 288 | `backend/tests/unit/test_inbound_startup_guards.py:200` | `def test_live_validator_cannot_be_bypassed_by_an_existing_wrong_adapter():` |
| 289 | `backend/tests/unit/test_inbound_startup_guards.py:214` | `def test_enabled_production_inbound_rejects_local_state(configured):` |
| 290 | `backend/tests/unit/test_inbound_startup_guards.py:226` | `def test_redis_configuration_rejects_local_fallback():` |
| 291 | `backend/tests/unit/test_inbound_startup_guards.py:244` | `def test_production_redis_backend_requires_atomic_inverse_claim():` |
| 292 | `backend/tests/unit/test_inbound_synthetic_monitor.py:10` | `def test_synthetic_probe_is_carrier_hairpin_and_shell_injection_bounded():` |
| 293 | `backend/tests/unit/test_inbound_synthetic_monitor.py:24` | `def test_synthetic_timer_is_installed_and_deploy_requires_configuration():` |
| 294 | `backend/tests/unit/test_inbound_transfer_billing.py:27` | `def test_0030_is_the_next_single_head_and_runtime_remains_closed() -> None:` |
| 295 | `backend/tests/unit/test_inbound_transfer_billing.py:37` | `def test_0030_links_each_usage_subject_to_its_exact_parent_and_child() -> None:` |
| 296 | `backend/tests/unit/test_inbound_transfer_billing.py:79` | `def test_complete_schema_has_the_typed_call_leg_billing_projection() -> None:` |
| 297 | `backend/tests/unit/test_inbound_transfer_billing.py:99` | `def test_parent_ledger_queries_cannot_select_a_transfer_child() -> None:` |
| 298 | `backend/tests/unit/test_inbound_transfer_billing.py:118` | `def test_quota_authority_counts_parent_and_every_transfer_leg_state() -> None:` |
| 299 | `backend/tests/unit/test_inbound_transfer_billing.py:134` | `def test_child_terminal_ledger_keeps_unknown_carrier_cost_null() -> None:` |
| 300 | `backend/tests/unit/test_inbound_transfer_billing.py:279` | `async def test_provider_answer_is_durable_and_idempotent_before_handoff(` |
| 301 | `backend/tests/unit/test_inbound_transfer_billing.py:351` | `async def test_provider_proved_preanswer_failure_releases_child_once(` |
| 302 | `backend/tests/unit/test_inbound_transfer_billing.py:386` | `async def test_answer_racing_parent_terminal_finalizes_not_releases(` |
| 303 | `backend/tests/unit/test_inbound_transfer_billing.py:413` | `async def test_provider_success_without_durable_answer_never_releases_zero_seconds(` |
| 304 | `backend/tests/unit/test_inbound_transfer_billing.py:441` | `async def test_answered_child_keeps_reservation_when_settlement_is_disabled(` |
| 305 | `backend/tests/unit/test_inbound_transfer_billing.py:473` | `async def test_restart_ambiguous_preanswer_child_is_held_not_released(` |
| 306 | `backend/tests/unit/test_inbound_transfer_billing.py:525` | `def test_parent_settlement_allows_only_explicit_restart_reconciliation_holds() -> None:` |
| 307 | `backend/tests/unit/test_inbound_transfer_controls.py:221` | `async def test_outbound_transfer_behavior_is_unchanged():` |
| 308 | `backend/tests/unit/test_inbound_transfer_controls.py:226` | `async def test_inbound_transfer_runtime_fails_closed_before_live_switch():` |
| 309 | `backend/tests/unit/test_inbound_transfer_controls.py:239` | `def test_staging_transfer_proof_switch_is_environment_scoped(monkeypatch, environment):` |
| 310 | `backend/tests/unit/test_inbound_transfer_controls.py:254` | `def test_staging_transfer_proof_requires_explicit_truthy_switch(monkeypatch, value):` |
| 311 | `backend/tests/unit/test_inbound_transfer_controls.py:268` | `def test_staging_transfer_proof_defaults_closed(monkeypatch):` |
| 312 | `backend/tests/unit/test_inbound_transfer_controls.py:291` | `def test_staging_transfer_proof_requires_valid_complete_scope(` |
| 313 | `backend/tests/unit/test_inbound_transfer_controls.py:309` | `def test_staging_transfer_scope_matches_only_exact_tenant_and_config(monkeypatch):` |
| 314 | `backend/tests/unit/test_inbound_transfer_controls.py:335` | `async def test_transfer_authorization_rejects_out_of_scope_staging_campaign(monkeypatch):` |
| 315 | `backend/tests/unit/test_inbound_transfer_controls.py:355` | `async def test_live_platform_switch_remains_a_second_gate():` |
| 316 | `backend/tests/unit/test_inbound_transfer_controls.py:367` | `async def test_pinned_allowlist_accepts_only_approved_destination():` |
| 317 | `backend/tests/unit/test_inbound_transfer_controls.py:383` | `async def test_non_blind_mode_is_rejected_before_attempt_is_persisted():` |
| 318 | `backend/tests/unit/test_inbound_transfer_controls.py:398` | `async def test_after_hours_destination_must_already_be_in_policy_allowlist():` |
| 319 | `backend/tests/unit/test_inbound_transfer_controls.py:427` | `async def test_authorization_persists_exact_provider_target_before_adapter_use():` |
| 320 | `backend/tests/unit/test_inbound_transfer_controls.py:441` | `async def test_durable_same_key_replays_without_creating_a_second_leg():` |
| 321 | `backend/tests/unit/test_inbound_transfer_controls.py:464` | `async def test_same_idempotency_key_with_changed_request_is_rejected():` |
| 322 | `backend/tests/unit/test_inbound_transfer_controls.py:486` | `async def test_completed_idempotency_replay_returns_exact_stored_result():` |
| 323 | `backend/tests/unit/test_inbound_transfer_controls.py:512` | `async def test_transfer_status_projection_is_tenant_scoped_and_truthful(monkeypatch):` |
| 324 | `backend/tests/unit/test_inbound_transfer_controls.py:567` | `async def test_transfer_status_rejects_non_uuid_attempt_id():` |
| 325 | `backend/tests/unit/test_inbound_transfer_controls.py:576` | `async def test_transfer_status_exposes_restart_reconciliation_hold(monkeypatch):` |
| 326 | `backend/tests/unit/test_inbound_transfer_controls.py:628` | `async def test_authorization_reserves_exact_remaining_parent_deadline_per_leg():` |
| 327 | `backend/tests/unit/test_inbound_transfer_controls.py:649` | `async def test_authorization_rejects_child_reservation_over_remaining_quota():` |
| 328 | `backend/tests/unit/test_inbound_transfer_controls.py:669` | `async def test_cleanup_pending_keeps_active_leg_and_transfer_lease(monkeypatch):` |
| 329 | `backend/tests/unit/test_inbound_transfer_controls.py:775` | `async def test_cleanup_pending_converges_when_target_absence_is_proved_later(` |
| 330 | `backend/tests/unit/test_inbound_transfer_controls.py:946` | `async def test_parent_finalization_completes_only_answered_transfer_legs(monkeypatch):` |
| 331 | `backend/tests/unit/test_inbound_transfer_controls.py:1126` | `async def test_missing_call_fails_closed():` |
| 332 | `backend/tests/unit/test_rls_set_local_invariant.py:110` | `def test_no_session_scoped_rls_set(path: Path) -> None:` |
| 333 | `backend/tests/unit/test_rls_set_local_invariant.py:132` | `def test_allowlist_has_no_stale_entries() -> None:` |
| 334 | `backend/tests/unit/test_rls_set_local_invariant.py:148` | `def test_canonical_helper_is_transaction_scoped() -> None:` |
| 335 | `backend/tests/unit/test_rls_set_local_invariant.py:245` | `def test_rls_table_guard_matches_schema_inventory() -> None:` |
| 336 | `backend/tests/unit/test_rls_set_local_invariant.py:355` | `def test_pooled_acquire_establishes_tenant_context(path: Path) -> None:` |
| 337 | `backend/tests/unit/test_rls_set_local_invariant.py:388` | `def test_no_guc_allowlist_has_no_stale_entries() -> None:` |
| 338 | `backend/tests/unit/test_telephony_media_gateway.py:13` | `async def test_hangup_call_uses_adapter_pbx_call_id():` |
| 339 | `backend/tests/unit/test_telephony_media_gateway.py:29` | `async def test_hangup_call_returns_false_for_missing_session():` |
| 340 | `backend/tests/unit/test_telephony_media_gateway.py:38` | `async def test_preencoded_pcmu_clip_bypasses_tts_conversion_and_waits_for_drain():` |
| 341 | `backend/tests/unit/test_telephony_media_gateway.py:64` | `async def test_preencoded_clip_honors_barge_in_before_sending_audio():` |
| 342 | `backend/tests/unit/test_telephony_media_gateway.py:86` | `async def test_recording_gate_blocks_both_sides_but_keeps_live_media_flowing():` |
| 343 | `backend/tests/unit/test_telephony_media_gateway.py:126` | `async def test_closing_recording_gate_purges_already_buffered_audio():` |
| 344 | `backend/tests/unit/test_telephony_media_gateway.py:165` | `async def test_send_audio_caps_tts_recording_buffer_bytes():` |
| 345 | `backend/tests/unit/test_telephony_media_gateway.py:223` | `async def test_clear_recording_buffer_resets_tts_byte_counter():` |
| 346 | `backend/tests/unit/test_telephony_media_gateway.py:264` | `async def test_send_audio_f32le_chunk_not_multiple_of_4_is_not_dropped():` |
| 347 | `backend/tests/unit/test_telephony_media_gateway.py:312` | `async def test_clear_output_buffer_resets_tts_pending_bytes():` |
| 348 | `backend/tests/unit/test_telephony_media_gateway.py:333` | `async def test_barge_in_then_next_turn_does_not_leave_misaligned_carry():` |
| 349 | `backend/tests/unit/test_telephony_media_gateway.py:360` | `async def test_flush_tts_buffer_discards_orphan_pending_bytes():` |
| 350 | `backend/tests/unit/test_telephony_media_gateway.py:382` | `async def test_flush_tts_buffer_clears_buffer_even_when_send_fails():` |
| 351 | `backend/tests/unit/test_telephony_media_gateway.py:411` | `async def test_force_hangup_failure_is_logged_and_deactivates_session():` |
| 352 | `backend/tests/unit/test_telephony_media_gateway.py:446` | `async def test_force_hangup_success_clears_failure_counter():` |
| 353 | `backend/tests/unit/test_telephony_media_gateway.py:473` | `async def test_send_audio_transient_tts_failure_retries_and_clears_counter_on_success():` |
| 354 | `backend/tests/unit/test_telephony_media_gateway.py:498` | `async def test_send_audio_typed_tts_failure_propagates_immediately():` |
| 355 | `backend/tests/unit/test_tts_empty_stream.py:131` | `async def test_an_empty_stream_is_retried_once():` |
| 356 | `backend/tests/unit/test_tts_empty_stream.py:144` | `async def test_the_retry_asks_for_the_same_words():` |
| 357 | `backend/tests/unit/test_tts_empty_stream.py:157` | `async def test_two_empty_streams_still_produce_speech():` |
| 358 | `backend/tests/unit/test_tts_empty_stream.py:170` | `async def test_total_tts_failure_is_still_recorded_as_a_silent_turn():` |
| 359 | `backend/tests/unit/test_tts_empty_stream.py:183` | `async def test_second_failed_turn_plays_terminal_clip_and_hangs_up():` |
| 360 | `backend/tests/unit/test_tts_empty_stream.py:208` | `async def test_a_working_provider_is_synthesised_exactly_once():` |
| 361 | `backend/tests/unit/test_tts_empty_stream.py:221` | `async def test_a_provider_that_yields_one_chunk_then_stops_is_not_retried():` |
| 362 | `backend/tests/unit/test_tts_empty_stream.py:302` | `async def test_a_barge_in_before_the_first_chunk_is_never_retried(via_event):` |
| 363 | `backend/tests/unit/test_tts_empty_stream.py:311` | `async def test_a_barge_in_before_the_first_chunk_gets_no_fallback_either():` |
| 364 | `backend/tests/unit/test_tts_empty_stream.py:322` | `async def test_the_two_silent_causes_are_labelled_differently():` |
| 365 | `backend/tests/unit/test_tts_empty_stream.py:334` | `async def test_empty_chunks_do_not_count_as_audio():` |

## Appendix E — named Talk-Leee frontend test declarations

This is the complete source-level test declaration catalog under Talk-Leee/src at the candidate head. It includes inbound work and the surrounding dashboard contracts that the full 302-test run exercised.

| # | Source | Test declaration |
|---:|---|---|
| 1 | `Talk-Leee/src/app/ai-voices/page.test.tsx:67` | `test("renders voices after fetching", async () => {` |
| 2 | `Talk-Leee/src/app/ai-voices/page.test.tsx:106` | `test("handles fetch error", async () => {` |
| 3 | `Talk-Leee/src/app/ai-voices/page.test.tsx:120` | `test("toggles play state on button click", async () => {` |
| 4 | `Talk-Leee/src/app/meetings/meeting-row.test.tsx:12` | `test("MeetingRow shows participant summary when participants exist", () => {` |
| 5 | `Talk-Leee/src/app/meetings/meeting-row.test.tsx:27` | `test("MeetingRow omits participant summary when empty", () => {` |
| 6 | `Talk-Leee/src/app/security/session-revoke-copy.test.ts:40` | `test("the sessions section no longer claims revoking ends the session immediately", () => {` |
| 7 | `Talk-Leee/src/app/security/session-revoke-copy.test.ts:49` | `test("the sessions section states, in the always-visible text, that the device is not signed out", () => {` |
| 8 | `Talk-Leee/src/app/security/session-revoke-copy.test.ts:58` | `test("the tooltip states the rolling window and the remedy in plain language", () => {` |
| 9 | `Talk-Leee/src/app/security/session-revoke-copy.test.ts:76` | `test("the password form states the window rather than implying an instant sign-out", () => {` |
| 10 | `Talk-Leee/src/components/ai-options/controls.test.tsx:11` | `test("exposes its formatted value and purpose to assistive technology", () => {` |
| 11 | `Talk-Leee/src/components/ai-options/controls.test.tsx:30` | `test("supports stepped keyboard changes", () => {` |
| 12 | `Talk-Leee/src/components/ai-options/controls.test.tsx:48` | `test("can shrink below its desktop size and retains a visible focus treatment", () => {` |
| 13 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:81` | `test("a failed billing request renders the error state, never '0 of 0 minutes used'", async () => {` |
| 14 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:99` | `test("a genuinely empty successful response renders the empty state, not an error", async () => {` |
| 15 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:114` | `test("loading is distinct from both the error and the empty state", async () => {` |
| 16 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:129` | `test("a failed invoices request never renders 'No invoices yet.'", async () => {` |
| 17 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:144` | `test("a failed daily-usage request never renders zeroed call stats", async () => {` |
| 18 | `Talk-Leee/src/components/billing/billing-overview.test.tsx:161` | `test("a failed overage-alerts request is surfaced instead of silently showing no alerts", async () => {` |
| 19 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:57` | `test("a read-only account is told it cannot review instead of being shown the form", async () => {` |
| 20 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:75` | `test("calls:create renders the review form", async () => {` |
| 21 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:83` | `test("a failed permission lookup is reported as unchecked, not as a refusal", async () => {` |
| 22 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:110` | `test("teammates' reviews stay visible to an account that cannot write one", async () => {` |
| 23 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:138` | `test("a 403 on submit does not offer a Try again that would be refused identically", async () => {` |
| 24 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:157` | `test("a server fault still offers Try again, and it re-sends", async () => {` |
| 25 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:208` | `test("the review form promises no points, rewards or credits — even with rewards reported ON", async () => {` |
| 26 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:235` | `test("a saved review confirms the save without announcing an award", async () => {` |
| 27 | `Talk-Leee/src/components/calls/conversation-review-panel.test.tsx:271` | `test("an existing review can be edited without any wording about awards", async () => {` |
| 28 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:89` | `test("hangup responses require the confirmation-aware contract", () => {` |
| 29 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:97` | `test("termination presentation remains pending until the call itself is terminal", () => {` |
| 30 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:109` | `test("inbound live rows show direction, ANI, DID, admission, and consent", async () => {` |
| 31 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:130` | `test("a hangup request shows Ending without optimistically ending the row or allowing a duplicate", async () => {` |
| 32 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:161` | `test("a failed or unconfirmed hangup exposes the provider error and allows retry", async () => {` |
| 33 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:192` | `test("an HTTP unconfirmed response surfaces its structured provider detail", async () => {` |
| 34 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:278` | `test("without download permission the recording object URL is revoked when playback stops", async () => {` |
| 35 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:298` | `test("with download permission playback pausing keeps the loaded recording", async () => {` |
| 36 | `Talk-Leee/src/components/campaigns/live-calls-panel.test.tsx:313` | `test("polling a terminal call clears Ending and uses the server's final status", async () => {` |
| 37 | `Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.test.tsx:16` | `test("shows durable denials and after-hours calls without exposing private ANI", async () => {` |
| 38 | `Talk-Leee/src/components/campaigns/rejected-inbound-calls-panel.test.tsx:57` | `test("shows a healthy empty state", async () => {` |
| 39 | `Talk-Leee/src/components/connectors/connector-card.test.ts:14` | `test("ConnectorCard enables only Connect when disconnected", () => {` |
| 40 | `Talk-Leee/src/components/connectors/connector-card.test.ts:30` | `test("ConnectorCard enables Disconnect when connected", () => {` |
| 41 | `Talk-Leee/src/components/connectors/connector-card.test.ts:46` | `test("ConnectorCard shows Expired status and allows reconnect", () => {` |
| 42 | `Talk-Leee/src/components/connectors/connector-card.test.ts:63` | `test("ConnectorCard calls authorize and shows loading state", async () => {` |
| 43 | `Talk-Leee/src/components/connectors/connector-card.test.ts:105` | `test("ConnectorCard confirms and calls disconnect", async () => {` |
| 44 | `Talk-Leee/src/components/home/contact-section.test.tsx:13` | `it("renders the contact form and info section", () => {` |
| 45 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:14` | `test("inbound form requires a verified DID, AI campaign and inbound trunk", () => {` |
| 46 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:22` | `test("inbound form enforces recording disclosure and E.164 transfer destinations", () => {` |
| 47 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:38` | `test("after-hours UI exposes only runtime-backed actions", () => {` |
| 48 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:49` | `test("AI message intake fails closed without its pinned opening message", () => {` |
| 49 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:58` | `test("saved transfer policies can only be disabled while runtime proof is incomplete", () => {` |
| 50 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:69` | `test("after-hours transfer is accepted only in the server-approved proof window", () => {` |
| 51 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:82` | `test("cached open transfer capability fails closed after a refresh error", () => {` |
| 52 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:95` | `test("E.164 and duration validation match the server boundary contract", () => {` |
| 53 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:108` | `test("only server-visible eligible campaigns and runtime-ready inbound trunks can be selected", () => {` |
| 54 | `Talk-Leee/src/components/inbound/inbound-campaign-form.test.ts:122` | `test("inbound-specific overrides start neutral and inherit the base campaign", () => {` |
| 55 | `Talk-Leee/src/components/notifications/qualified-lead-alerts.test.ts:60` | `test("leads already recorded in localStorage do not toast again", async () => {` |
| 56 | `Talk-Leee/src/components/notifications/qualified-lead-alerts.test.ts:72` | `test("the first load with no history seeds silently instead of toasting the backlog", async () => {` |
| 57 | `Talk-Leee/src/components/notifications/qualified-lead-alerts.test.ts:84` | `test("stale unseen leads are absorbed silently, fresh ones toast", async () => {` |
| 58 | `Talk-Leee/src/components/recordings/recording-media-controls.test.tsx:62` | `test("playback is lazy and download uses a separate request", async () => {` |
| 59 | `Talk-Leee/src/components/recordings/recording-media-controls.test.tsx:88` | `test("recording controls fail closed for each missing permission", () => {` |
| 60 | `Talk-Leee/src/components/recordings/recording-media-controls.test.tsx:96` | `test("delete retries retain one key and lock the audited reason", async () => {` |
| 61 | `Talk-Leee/src/components/recordings/recording-media-controls.test.tsx:133` | `test("legal hold keeps the row and disables further deletion", async () => {` |
| 62 | `Talk-Leee/src/components/states/page-states.test.ts:13` | `test("ErrorState renders message and supports retry", async () => {` |
| 63 | `Talk-Leee/src/components/states/page-states.test.ts:35` | `test("ErrorState renders support action when provided", () => {` |
| 64 | `Talk-Leee/src/components/states/page-states.test.ts:49` | `test("EmptyState renders CTA when provided", async () => {` |
| 65 | `Talk-Leee/src/components/states/page-states.test.ts:68` | `test("EmptyState supports primary and secondary actions", async () => {` |
| 66 | `Talk-Leee/src/components/states/page-states.test.ts:92` | `test("LoadingSkeleton renders requested number of lines", () => {` |
| 67 | `Talk-Leee/src/components/states/page-states.test.ts:98` | `test("LoadingSkeleton list variant renders rows", () => {` |
| 68 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:35` | `test("ConfirmDialog focuses Cancel and traps tab navigation", async () => {` |
| 69 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:53` | `test("ConfirmDialog calls onConfirm and closes on success", async () => {` |
| 70 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:75` | `test("ConfirmDialog closes on Escape", async () => {` |
| 71 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:85` | `test("ConfirmDialog shows error when confirm fails and stays open", async () => {` |
| 72 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:139` | `test("ConfirmDialog clears the inline error when the parent closes it directly", async () => {` |
| 73 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:159` | `test("ConfirmDialog clears the pending spinner when the parent closes it directly", async () => {` |
| 74 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:179` | `test("ConfirmDialog intent=cancel uses default copy", () => {` |
| 75 | `Talk-Leee/src/components/ui/confirm-dialog.test.ts:186` | `test("ConfirmDialog intent=delete uses default copy", () => {` |
| 76 | `Talk-Leee/src/components/ui/info-tip.test.tsx:67` | `test("the trigger is a real button carrying the label as its accessible name", () => {` |
| 77 | `Talk-Leee/src/components/ui/info-tip.test.tsx:81` | `test("each trigger gets its own accessible name rather than a shared 'more info'", () => {` |
| 78 | `Talk-Leee/src/components/ui/info-tip.test.tsx:95` | `test("a tap opens the tip and renders its content", async () => {` |
| 79 | `Talk-Leee/src/components/ui/info-tip.test.tsx:104` | `test("tapping the trigger again closes the tip", async () => {` |
| 80 | `Talk-Leee/src/components/ui/info-tip.test.tsx:115` | `test("a tap outside dismisses a pinned tip", async () => {` |
| 81 | `Talk-Leee/src/components/ui/info-tip.test.tsx:136` | `test("the tip is reachable by keyboard — Tab focuses the trigger, Enter opens it", async () => {` |
| 82 | `Talk-Leee/src/components/ui/info-tip.test.tsx:147` | `test("Space opens the tip too, because the trigger is a button", async () => {` |
| 83 | `Talk-Leee/src/components/ui/info-tip.test.tsx:157` | `test("Escape dismisses a tip opened from the keyboard", async () => {` |
| 84 | `Talk-Leee/src/components/ui/info-tip.test.tsx:174` | `test("an open tip is announced: the trigger describes itself with the tip content", async () => {` |
| 85 | `Talk-Leee/src/components/ui/info-tip.test.tsx:195` | `test("the trigger reflects its open state for styling and for assistive tech", async () => {` |
| 86 | `Talk-Leee/src/components/ui/info-tip.test.tsx:206` | `test("optional Learn more renders as a real link to the given href", async () => {` |
| 87 | `Talk-Leee/src/components/ui/info-tip.test.tsx:226` | `test("no Learn more link when no href is given", async () => {` |
| 88 | `Talk-Leee/src/components/ui/info-tip.test.tsx:236` | `test("rich content is rendered, not stringified", async () => {` |
| 89 | `Talk-Leee/src/components/ui/info-tip.test.tsx:251` | `test("the panel carries the viewport width cap", async () => {` |
| 90 | `Talk-Leee/src/components/ui/info-tip.test.tsx:270` | `test("LabelWithInfo shows the label and derives an accessible name from it", () => {` |
| 91 | `Talk-Leee/src/components/ui/info-tip.test.tsx:278` | `test("LabelWithInfo opens the same tip its label describes", async () => {` |
| 92 | `Talk-Leee/src/components/ui/input.test.tsx:11` | `test("Input updates value", async () => {` |
| 93 | `Talk-Leee/src/components/ui/status-pill.test.ts:21` | `test(`StatusPill renders ${c.state} with tooltip`, async () => {` |
| 94 | `Talk-Leee/src/lib/admin-access.test.ts:5` | `test("isPlatformAdminRole recognizes platform_admin and admin roles", () => {` |
| 95 | `Talk-Leee/src/lib/admin-access.test.ts:14` | `test("isPartnerAdminRole recognizes partner_admin role", () => {` |
| 96 | `Talk-Leee/src/lib/admin-access.test.ts:23` | `test("isTenantScopedRole recognizes tenant-scoped roles", () => {` |
| 97 | `Talk-Leee/src/lib/admin-access.test.ts:33` | `test("getAdminUiCapabilities grants full access to platform_admin", () => {` |
| 98 | `Talk-Leee/src/lib/admin-access.test.ts:48` | `test("getAdminUiCapabilities grants full access to admin role", () => {` |
| 99 | `Talk-Leee/src/lib/admin-access.test.ts:63` | `test("getAdminUiCapabilities grants scoped access to partner_admin", () => {` |
| 100 | `Talk-Leee/src/lib/admin-access.test.ts:78` | `test("getAdminUiCapabilities restricts access for tenant_admin", () => {` |
| 101 | `Talk-Leee/src/lib/admin-access.test.ts:93` | `test("getAdminUiCapabilities restricts access for user role", () => {` |
| 102 | `Talk-Leee/src/lib/admin-access.test.ts:108` | `test("getAdminUiCapabilities handles null user", () => {` |
| 103 | `Talk-Leee/src/lib/admin-access.test.ts:119` | `test("getAdminUiCapabilities ignores whitespace-only IDs", () => {` |
| 104 | `Talk-Leee/src/lib/admin-access.test.ts:130` | `test("roleLabel returns correct labels for all roles", () => {` |
| 105 | `Talk-Leee/src/lib/audio-recording.test.ts:33` | `test("Chrome and Firefox get Opus in WebM", () => {` |
| 106 | `Talk-Leee/src/lib/audio-recording.test.ts:38` | `test("Safari below 18.4 gets MP4, never WebM", () => {` |
| 107 | `Talk-Leee/src/lib/audio-recording.test.ts:44` | `test("Safari 18.4+ can do WebM and is given the better container", () => {` |
| 108 | `Talk-Leee/src/lib/audio-recording.test.ts:49` | `test("a browser that supports nothing we asked about yields no preference", () => {` |
| 109 | `Talk-Leee/src/lib/audio-recording.test.ts:58` | `test("a throwing isTypeSupported does not abort the search", () => {` |
| 110 | `Talk-Leee/src/lib/audio-recording.test.ts:66` | `test("every candidate we would request is one the backend accepts", () => {` |
| 111 | `Talk-Leee/src/lib/audio-recording.test.ts:79` | `test("the recorder's own mimeType wins over what we requested", () => {` |
| 112 | `Talk-Leee/src/lib/audio-recording.test.ts:88` | `test("codec parameters are stripped, matching the backend's normalisation", () => {` |
| 113 | `Talk-Leee/src/lib/audio-recording.test.ts:94` | `test("a blank recorder mimeType falls back to the request, then to WebM", () => {` |
| 114 | `Talk-Leee/src/lib/audio-recording.test.ts:100` | `test("filenames carry an extension that agrees with the container", () => {` |
| 115 | `Talk-Leee/src/lib/audio-recording.test.ts:109` | `test("each microphone failure gets its own remedy", () => {` |
| 116 | `Talk-Leee/src/lib/audio-recording.test.ts:118` | `test("a named error always beats environment sniffing", () => {` |
| 117 | `Talk-Leee/src/lib/audio-recording.test.ts:130` | `test("an unrecognised failure still yields something actionable", () => {` |
| 118 | `Talk-Leee/src/lib/audio-recording.test.ts:142` | `test("every microphone message tells the user what to do next", () => {` |
| 119 | `Talk-Leee/src/lib/audio-recording.test.ts:152` | `test("the duration cap is 30s and matches the API's Form(le=30)", () => {` |
| 120 | `Talk-Leee/src/lib/audio-recording.test.ts:159` | `test("the minimum length rejects a mis-click but not a short sentence", () => {` |
| 121 | `Talk-Leee/src/lib/audio-recording.test.ts:163` | `test("the mirrored accept-list matches the backend's _MIME_EXTENSIONS", () => {` |
| 122 | `Talk-Leee/src/lib/audio-recording.test.ts:178` | `test("durations render as m:ss", () => {` |
| 123 | `Talk-Leee/src/lib/backend-api.admin.test.ts:4` | `test("backendApi.admin.auditLogs.list encodes filters and pagination", async () => {` |
| 124 | `Talk-Leee/src/lib/backend-api.admin.test.ts:58` | `test("backendApi.admin.tenants.suspend posts to suspension endpoint", async () => {` |
| 125 | `Talk-Leee/src/lib/backend-api.assistant.test.ts:4` | `test("backendApi.assistantActions.list calls assistant actions list endpoint", async () => {` |
| 126 | `Talk-Leee/src/lib/backend-api.assistant.test.ts:25` | `test("backendApi.assistantRuns.list supports filtering and sorting query params", async () => {` |
| 127 | `Talk-Leee/src/lib/backend-api.assistant.test.ts:61` | `test("backendApi.assistant.plan posts payload to plan endpoint", async () => {` |
| 128 | `Talk-Leee/src/lib/backend-api.assistant.test.ts:92` | `test("backendApi.assistant.execute posts payload to execute endpoint and parses run", async () => {` |
| 129 | `Talk-Leee/src/lib/backend-api.assistant.test.ts:138` | `test("backendApi.assistantRuns.retry posts to retry endpoint", async () => {` |
| 130 | `Talk-Leee/src/lib/backend-api.calendar-events.test.ts:4` | `test("backendApi.calendarEvents.list calls calendar events list endpoint", async () => {` |
| 131 | `Talk-Leee/src/lib/backend-api.calendar-events.test.ts:25` | `test("backendApi.calendarEvents.list supports pagination query params", async () => {` |
| 132 | `Talk-Leee/src/lib/backend-api.calendar-events.test.ts:51` | `test("backendApi.calendarEvents.create posts event payload to create endpoint", async () => {` |
| 133 | `Talk-Leee/src/lib/backend-api.calendar-events.test.ts:110` | `test("backendApi.calendarEvents.update patches event by id", async () => {` |
| 134 | `Talk-Leee/src/lib/backend-api.calendar-events.test.ts:159` | `test("backendApi.calendarEvents.cancel deletes event by id", async () => {` |
| 135 | `Talk-Leee/src/lib/backend-api.connectors.test.ts:4` | `test("backendApi.connectors.authorize calls authorize endpoint with redirect_uri", async () => {` |
| 136 | `Talk-Leee/src/lib/backend-api.connectors.test.ts:31` | `test("backendApi.connectors.disconnect calls disconnect endpoint via POST", async () => {` |
| 137 | `Talk-Leee/src/lib/backend-api.connectors.test.ts:51` | `test("backendApi.connectors.status parses connector statuses", async () => {` |
| 138 | `Talk-Leee/src/lib/backend-api.email.test.ts:4` | `test("backendApi.email.templates.list parses templates and maps fields", async () => {` |
| 139 | `Talk-Leee/src/lib/backend-api.email.test.ts:45` | `test("backendApi.email.send posts template_id and returns normalized messageId", async () => {` |
| 140 | `Talk-Leee/src/lib/backend-api.voice-calls.test.ts:4` | `test("backendApi.voiceCalls.guard posts guarded call payload and parses allow response", async () => {` |
| 141 | `Talk-Leee/src/lib/backend-api.voice-calls.test.ts:55` | `test("backendApi.voiceCalls.start posts start payload and parses active session response", async () => {` |
| 142 | `Talk-Leee/src/lib/billing-api.test.ts:55` | `test("a 403 on the usage endpoint surfaces as isError, not as empty data", async () => {` |
| 143 | `Talk-Leee/src/lib/billing-api.test.ts:65` | `test("a 500 on the invoices endpoint surfaces as isError, not as an empty list", async () => {` |
| 144 | `Talk-Leee/src/lib/billing-api.test.ts:75` | `test("a network failure on the invoices endpoint surfaces as isError", async () => {` |
| 145 | `Talk-Leee/src/lib/billing-api.test.ts:87` | `test("a successful empty invoice list stays an ordinary empty result", async () => {` |
| 146 | `Talk-Leee/src/lib/billing-api.test.ts:97` | `test("a 404 for one invoice by id is 'not found', not a load failure", async () => {` |
| 147 | `Talk-Leee/src/lib/billing-api.test.ts:107` | `test("a 403 for one invoice by id is still a load failure", async () => {` |
| 148 | `Talk-Leee/src/lib/campaign-performance.test.ts:33` | `test("normalizeCampaignStatus maps known statuses", () => {` |
| 149 | `Talk-Leee/src/lib/campaign-performance.test.ts:42` | `test("campaignProgressPct clamps and handles zero leads", () => {` |
| 150 | `Talk-Leee/src/lib/campaign-performance.test.ts:48` | `test("campaignSuccessRatePct uses completed / (completed+failed)", () => {` |
| 151 | `Talk-Leee/src/lib/campaign-performance.test.ts:54` | `test("applyCampaignFilters supports status, success range, and query", () => {` |
| 152 | `Talk-Leee/src/lib/campaign-performance.test.ts:71` | `test("applyCampaignSort supports multi-column and stable tiebreak", () => {` |
| 153 | `Talk-Leee/src/lib/campaign-performance.test.ts:85` | `test("paginate returns correct range and pageCount", () => {` |
| 154 | `Talk-Leee/src/lib/campaign-performance.test.ts:94` | `test("parseCommandInput reads prefixes and query", () => {` |
| 155 | `Talk-Leee/src/lib/campaign-performance.test.ts:102` | `test("groupEventTime buckets dates into groups", () => {` |
| 156 | `Talk-Leee/src/lib/connectors-utils.test.ts:11` | `test("connectorCardActionFromStatus maps all states", () => {` |
| 157 | `Talk-Leee/src/lib/connectors-utils.test.ts:19` | `test("connectorCardActionLabel renders user-facing labels", () => {` |
| 158 | `Talk-Leee/src/lib/connectors-utils.test.ts:25` | `test("extractAuthorizationUrl accepts multiple response shapes", () => {` |
| 159 | `Talk-Leee/src/lib/connectors-utils.test.ts:32` | `test("parseConnectorsCallback handles success and failure", () => {` |
| 160 | `Talk-Leee/src/lib/connectors-utils.test.ts:44` | `test("parseConnectorsCallback uses default providerType when missing from query", () => {` |
| 161 | `Talk-Leee/src/lib/connectors-utils.test.ts:50` | `test("formatLastSync is stable for empty and invalid values", () => {` |
| 162 | `Talk-Leee/src/lib/dashboard-api.inbound.test.ts:4` | `test("inbound call list and detail preserve direction and caller/DID parties", async () => {` |
| 163 | `Talk-Leee/src/lib/dashboard-layout-removal.test.ts:7` | `test("dashboard layout removes theme toggle and removes global sidebar toggle", () => {` |
| 164 | `Talk-Leee/src/lib/email-audit.test.ts:29` | `test("createAttempt records pending entry and persists to storage", () => {` |
| 165 | `Talk-Leee/src/lib/email-audit.test.ts:56` | `test("markSuccess and markFailed update the audit entry", () => {` |
| 166 | `Talk-Leee/src/lib/email-audit.test.ts:79` | `test("exportHistoryJson includes exportedAt and items", () => {` |
| 167 | `Talk-Leee/src/lib/email-audit.test.ts:97` | `test("clearAll removes items and clears storage", () => {` |
| 168 | `Talk-Leee/src/lib/email-utils.test.ts:5` | `test("splitEmailInput splits by whitespace, commas, semicolons, and newlines", () => {` |
| 169 | `Talk-Leee/src/lib/email-utils.test.ts:10` | `test("isValidEmail accepts simple valid addresses and rejects invalid ones", () => {` |
| 170 | `Talk-Leee/src/lib/email-utils.test.ts:18` | `test("normalizeEmailList trims and de-duplicates case-insensitively", () => {` |
| 171 | `Talk-Leee/src/lib/email-utils.test.ts:23` | `test("buildResponsiveHtmlDocument wraps fragments and injects viewport meta", () => {` |
| 172 | `Talk-Leee/src/lib/email-utils.test.ts:31` | `test("buildResponsiveHtmlDocument injects viewport into existing head", () => {` |
| 173 | `Talk-Leee/src/lib/email-utils.test.ts:36` | `test("buildResponsiveHtmlDocument does not duplicate viewport meta", () => {` |
| 174 | `Talk-Leee/src/lib/env.test.ts:5` | `test("isPublicEnvKey only allows NEXT_PUBLIC client-safe keys", () => {` |
| 175 | `Talk-Leee/src/lib/env.test.ts:12` | `test("publicAppConfig exposes only public configuration metadata", () => {` |
| 176 | `Talk-Leee/src/lib/extended-api.recordings.test.ts:7` | `test("recording playback and download use distinct authorized endpoints", async () => {` |
| 177 | `Talk-Leee/src/lib/extended-api.recordings.test.ts:30` | `test("recording delete sends the reason and caller-owned idempotency key", async () => {` |
| 178 | `Talk-Leee/src/lib/extended-api.recordings.test.ts:59` | `test("recording delete preserves the backend legal-hold error code", async () => {` |
| 179 | `Talk-Leee/src/lib/http-client.session-expired.test.ts:18` | `test("401 fires the registered session-expired handler and clears the token", async () => {` |
| 180 | `Talk-Leee/src/lib/http-client.session-expired.test.ts:53` | `test("multiple parallel 401s fire the handler only ONCE (idempotent)", async () => {` |
| 181 | `Talk-Leee/src/lib/http-client.session-expired.test.ts:95` | `test("resetSessionExpiredLatch re-arms the handler after a successful login", async () => {` |
| 182 | `Talk-Leee/src/lib/http-client.session-expired.test.ts:134` | `test("non-401 errors do NOT fire the session-expired handler", async () => {` |
| 183 | `Talk-Leee/src/lib/http-client.session-expired.test.ts:164` | `test("a thrown handler does not derail the request rejection", async () => {` |
| 184 | `Talk-Leee/src/lib/http-client.test.ts:5` | `test("http client injects Authorization header when token present", async () => {` |
| 185 | `Talk-Leee/src/lib/http-client.test.ts:26` | `test("http client refreshes on 401 then retries the original request", async () => {` |
| 186 | `Talk-Leee/src/lib/http-client.test.ts:59` | `test("http client surfaces 401 when refresh also fails", async () => {` |
| 187 | `Talk-Leee/src/lib/http-client.test.ts:93` | `test("http client maps 429 response to rate_limited with retryAfterMs", async () => {` |
| 188 | `Talk-Leee/src/lib/http-client.test.ts:123` | `test("requestRaw refreshes on 401 then retries and returns the raw binary Response", async () => {` |
| 189 | `Talk-Leee/src/lib/http-client.test.ts:156` | `test("requestRaw surfaces the backend error detail on a non-OK response", async () => {` |
| 190 | `Talk-Leee/src/lib/http-client.test.ts:181` | `test("http client preserves structured FastAPI detail codes", async () => {` |
| 191 | `Talk-Leee/src/lib/http-client.test.ts:211` | `test("requestRaw preserves canonical error codes for binary endpoints", async () => {` |
| 192 | `Talk-Leee/src/lib/inbound-api.test.ts:54` | `test("inbound parser accepts the production envelope and masks the DID", () => {` |
| 193 | `Talk-Leee/src/lib/inbound-api.test.ts:66` | `test("verified phone inventory excludes assigned and unverified numbers", () => {` |
| 194 | `Talk-Leee/src/lib/inbound-api.test.ts:78` | `test("readiness fails closed without an explicit server ready flag", () => {` |
| 195 | `Talk-Leee/src/lib/inbound-api.test.ts:86` | `test("transfer capability parsing requires all explicit server gates", () => {` |
| 196 | `Talk-Leee/src/lib/inbound-api.test.ts:110` | `test("transfer capability request is scoped to the edited inbound config", async () => {` |
| 197 | `Talk-Leee/src/lib/inbound-api.test.ts:134` | `test("archived campaign inventory is requested only for the archived view", async () => {` |
| 198 | `Talk-Leee/src/lib/inbound-api.test.ts:154` | `test("create sends only the confirmed inbound contract and an idempotency key", async () => {` |
| 199 | `Talk-Leee/src/lib/inbound-api.test.ts:186` | `test("an ambiguous retry reuses its idempotency key and a later operation gets a fresh key", async () => {` |
| 200 | `Talk-Leee/src/lib/inbound-api.test.ts:209` | `test("an expired ambiguous retry is blocked before the server claim can roll over", async () => {` |
| 201 | `Talk-Leee/src/lib/inbound-api.test.ts:231` | `test("update uses PUT and includes the stale-edit token", async () => {` |
| 202 | `Talk-Leee/src/lib/inbound-api.test.ts:250` | `test("assignment uses the explicit audited endpoint and optimistic version", async () => {` |
| 203 | `Talk-Leee/src/lib/inbound-api.test.ts:280` | `test("archive uses the confirmed lifecycle endpoint and optimistic version", async () => {` |
| 204 | `Talk-Leee/src/lib/inbound-permissions.test.ts:6` | `test("effective permissions keep inbound capabilities distinct", () => {` |
| 205 | `Talk-Leee/src/lib/inbound-permissions.test.ts:36` | `test("capabilities fail closed when server permission discovery is unavailable", () => {` |
| 206 | `Talk-Leee/src/lib/media-permissions.test.ts:6` | `test("recording permissions stay independent", () => {` |
| 207 | `Talk-Leee/src/lib/media-permissions.test.ts:27` | `test("broader call permissions do not grant recording access", () => {` |
| 208 | `Talk-Leee/src/lib/media-permissions.test.ts:38` | `test("platform admin is an explicit bypass and missing discovery fails closed", () => {` |
| 209 | `Talk-Leee/src/lib/meetings-utils.test.ts:13` | `test("splitAndSortMeetings splits by start time and sorts correctly", () => {` |
| 210 | `Talk-Leee/src/lib/meetings-utils.test.ts:27` | `test("meetingLeadLabel prefers explicit leadName, else participant name/email", () => {` |
| 211 | `Talk-Leee/src/lib/meetings-utils.test.ts:49` | `test("meetingStatusLabel normalizes status values", () => {` |
| 212 | `Talk-Leee/src/lib/meetings-utils.test.ts:61` | `test("meetingParticipantSummary returns first two and extra count", () => {` |
| 213 | `Talk-Leee/src/lib/meetings-utils.test.ts:78` | `test("sortMeetings can sort by title and startTime", () => {` |
| 214 | `Talk-Leee/src/lib/meetings-utils.test.ts:99` | `test("sanitizeMeetingNotesHtml removes scripts and event handlers", () => {` |
| 215 | `Talk-Leee/src/lib/notifications.test.ts:28` | `test("create adds notification and toast by default", () => {` |
| 216 | `Talk-Leee/src/lib/notifications.test.ts:46` | `test("markRead and markAllRead set readAt", () => {` |
| 217 | `Talk-Leee/src/lib/notifications.test.ts:69` | `test("dismissToast removes toast only", () => {` |
| 218 | `Talk-Leee/src/lib/notifications.test.ts:88` | `test("clearAll removes history and toasts", () => {` |
| 219 | `Talk-Leee/src/lib/queries/inbound-queries.test.ts:9` | `test("campaign cache commits never rewrite capability objects", () => {` |
| 220 | `Talk-Leee/src/lib/reminders-utils.test.ts:27` | `test("sanitizeFailureReason strips dangerous characters and truncates", () => {` |
| 221 | `Talk-Leee/src/lib/reminders-utils.test.ts:35` | `test("groupReminders groups by meeting id then contact id", () => {` |
| 222 | `Talk-Leee/src/lib/reminders-utils.test.ts:47` | `test("sortReminders sorts by scheduledAt asc/desc", () => {` |
| 223 | `Talk-Leee/src/lib/reminders-utils.test.ts:58` | `test("retryGuidance returns actionable strings for failed reminders", () => {` |
| 224 | `Talk-Leee/src/lib/review-permissions.test.ts:9` | `test("calls:read alone can read reviews but not write one", () => {` |
| 225 | `Talk-Leee/src/lib/review-permissions.test.ts:17` | `test("the readonly role's real permission set cannot write a review", () => {` |
| 226 | `Talk-Leee/src/lib/review-permissions.test.ts:34` | `test("calls:create grants write", () => {` |
| 227 | `Talk-Leee/src/lib/review-permissions.test.ts:42` | `test("platform:admin grants both", () => {` |
| 228 | `Talk-Leee/src/lib/review-permissions.test.ts:50` | `test("permissions are matched case- and whitespace-insensitively", () => {` |
| 229 | `Talk-Leee/src/lib/review-permissions.test.ts:54` | `test("a missing permission set fails closed and says so", () => {` |
| 230 | `Talk-Leee/src/lib/review-permissions.test.ts:64` | `test("authorization and validation refusals are not retryable", () => {` |
| 231 | `Talk-Leee/src/lib/review-permissions.test.ts:71` | `test("transport, rate-limit and server faults are retryable", () => {` |
| 232 | `Talk-Leee/src/lib/routes.test.ts:14` | `test("new routes use DashboardLayout", () => {` |
| 233 | `Talk-Leee/src/lib/sidebar.test.ts:49` | `test("hydrate reads collapsed state from localStorage", () => {` |
| 234 | `Talk-Leee/src/lib/sidebar.test.ts:66` | `test("setCollapsed persists payload", () => {` |
| 235 | `Talk-Leee/src/lib/sidebar.test.ts:83` | `test("storage event updates snapshot in current tab", () => {` |
| 236 | `Talk-Leee/src/lib/structural-auth-isolation.test.ts:84` | `test("only auth-context (and allowlisted bridges) read the canonical token", () => {` |
| 237 | `Talk-Leee/src/lib/structural-no-bare-fetch.test.ts:87` | `test("no bare fetch() to /api/v1 outside the shared HttpClient", () => {` |
| 238 | `Talk-Leee/src/lib/structural-single-httpclient.test.ts:67` | `test("only api.ts (and the server-side route) call createHttpClient", () => {` |
| 239 | `Talk-Leee/src/lib/structural-single-me-call.test.ts:72` | `test("api.getMe() callers are limited to AuthContext (+ OAuth callback)", () => {` |
| 240 | `Talk-Leee/src/lib/theme-implementation.test.ts:10` | `test("Theme Implementation Verification", async (t) => {` |
| 241 | `Talk-Leee/src/lib/topup-api.test.ts:36` | `test("an unlimited plan is never offered a top-up", () => {` |
| 242 | `Talk-Leee/src/lib/topup-api.test.ts:43` | `test("a metered plan is offered a top-up", () => {` |
| 243 | `Talk-Leee/src/lib/topup-api.test.ts:47` | `test("nothing is offered before the balance is known", () => {` |
| 244 | `Talk-Leee/src/lib/topup-api.test.ts:55` | `test("under 15% remaining reads as low", () => {` |
| 245 | `Talk-Leee/src/lib/topup-api.test.ts:62` | `test("exactly 15% remaining is not low yet", () => {` |
| 246 | `Talk-Leee/src/lib/topup-api.test.ts:69` | `test("an unlimited plan is never low", () => {` |
| 247 | `Talk-Leee/src/lib/topup-api.test.ts:76` | `test("a zero allocation does not divide by zero", () => {` |
| 248 | `Talk-Leee/src/lib/topup-api.test.ts:82` | `test("coming back from the payment page is not proof on its own", () => {` |
| 249 | `Talk-Leee/src/lib/topup-api.test.ts:89` | `test("the ledger total moving up is proof", () => {` |
| 250 | `Talk-Leee/src/lib/topup-api.test.ts:93` | `test("a refund landing in the same window is not a successful top-up", () => {` |
| 251 | `Talk-Leee/src/lib/topup-api.test.ts:100` | `test("a top-up on an account that already bought some still registers", () => {` |
| 252 | `Talk-Leee/src/lib/topup-api.test.ts:104` | `test("no baseline means no claim", () => {` |
| 253 | `Talk-Leee/src/lib/topup-api.test.ts:110` | `test("prices render from minor units, not major", () => {` |
| 254 | `Talk-Leee/src/lib/topup-api.test.ts:116` | `test("a sub-penny per-minute rate keeps its precision", () => {` |
| 255 | `Talk-Leee/src/lib/topup-api.test.ts:120` | `test("the currency comes from the package, not a hardcoded default", () => {` |
| 256 | `Talk-Leee/src/lib/topup-api.test.ts:124` | `test("a missing currency falls back rather than throwing", () => {` |
| 257 | `Talk-Leee/src/lib/topup-api.test.ts:130` | `test("every order status the backend defines has a label and a tone", () => {` |
| 258 | `Talk-Leee/src/lib/topup-api.test.ts:147` | `test("a pending order says it is unpaid, not that it failed", () => {` |
| 259 | `Talk-Leee/src/proxy.trailing-slash.test.ts:84` | `test("every page route redirects to its trailing-slashed form", () => {` |
| 260 | `Talk-Leee/src/proxy.trailing-slash.test.ts:90` | `test("page route count matches the app router page count", () => {` |
| 261 | `Talk-Leee/src/proxy.trailing-slash.test.ts:95` | `test("already-canonical paths are not redirected again", () => {` |
| 262 | `Talk-Leee/src/proxy.trailing-slash.test.ts:102` | `test("api routes are never redirected", () => {` |
| 263 | `Talk-Leee/src/proxy.trailing-slash.test.ts:113` | `test("build assets and static files are never redirected", () => {` |
| 264 | `Talk-Leee/src/proxy.trailing-slash.test.ts:123` | `test("a dot in a non-final segment does not exempt the path", () => {` |
| 265 | `Talk-Leee/src/proxy.trailing-slash.test.ts:127` | `test("non-absolute input is ignored", () => {` |
| 266 | `Talk-Leee/src/server/api-security.test.ts:6` | `test("sanitizeUnknown removes prototype pollution keys", () => {` |
| 267 | `Talk-Leee/src/server/api-security.test.ts:17` | `test("parseStripeSignatureHeader extracts timestamp and v1 signatures", () => {` |
| 268 | `Talk-Leee/src/server/api-security.test.ts:28` | `test("verifyStripeWebhookSignature accepts valid signature and rejects invalid", () => {` |
| 269 | `Talk-Leee/src/server/auth-core.test.ts:35` | `test("password strength validator rejects weak passwords", () => {` |
| 270 | `Talk-Leee/src/server/auth-core.test.ts:41` | `test("argon2 hashes and verifies passwords", async () => {` |
| 271 | `Talk-Leee/src/server/auth-core.test.ts:48` | `test("auth token is extracted from Authorization header", () => {` |
| 272 | `Talk-Leee/src/server/auth-core.test.ts:53` | `test("auth token is extracted from cookie header", () => {` |
| 273 | `Talk-Leee/src/server/auth-core.test.ts:58` | `test("session cookies are httpOnly and sameSite", () => {` |
| 274 | `Talk-Leee/src/server/auth-core.test.ts:71` | `test("sessions enforce absolute expiry, idle timeout, binding, and rotation (db)", async (t) => {` |
| 275 | `Talk-Leee/src/server/auth-core.test.ts:155` | `test("sessions rotate on role/scope change and include usage/billing mapping (db)", async (t) => {` |
| 276 | `Talk-Leee/src/server/mfa.test.ts:5` | `test("RFC 6238 SHA1 test vectors match", () => {` |
| 277 | `Talk-Leee/src/server/passkeys.test.ts:22` | `test("webauthn config falls back to request host and origin in non-production", () => {` |
| 278 | `Talk-Leee/src/server/passkeys.test.ts:41` | `test("webauthn config requires rp id and allowed origins in production", () => {` |
| 279 | `Talk-Leee/src/server/passkeys.test.ts:57` | `test("webauthn config uses explicit allowlist when configured", () => {` |
| 280 | `Talk-Leee/src/server/rbac.test.ts:15` | `test("platform_admin can access any partner and tenant", () => {` |
| 281 | `Talk-Leee/src/server/rbac.test.ts:21` | `test("partner_admin is restricted to their partner", () => {` |
| 282 | `Talk-Leee/src/server/rbac.test.ts:29` | `test("tenant users cannot access other tenants or partners", () => {` |
| 283 | `Talk-Leee/src/server/rbac.test.ts:39` | `test("permission checks always allow platform_admin", () => {` |
| 284 | `Talk-Leee/src/server/voice-security.test.ts:15` | `test("call_guard allows valid calls and lifecycle updates counters", async () => {` |
| 285 | `Talk-Leee/src/server/voice-security.test.ts:59` | `test("startGuardedVoiceCallSessionWithService makes call_guard non-bypassable for start flow", async () => {` |
| 286 | `Talk-Leee/src/server/voice-security.test.ts:89` | `test("call_guard enforces concurrency limits and supports overage reservations", async () => {` |
| 287 | `Talk-Leee/src/server/voice-security.test.ts:129` | `test("call_guard enforces dedicated call rate limits per tenant", async () => {` |
| 288 | `Talk-Leee/src/server/voice-security.test.ts:163` | `test("call_guard rejects disallowed features and then blocks repeated failed attempts", async () => {` |
| 289 | `Talk-Leee/src/server/voice-security.test.ts:201` | `test("call_guard rejects callers outside the tenant scope", async () => {` |

## Appendix F — production truth and release boundary

### Repository-complete items

- The inbound schema through 0036 exists.
- Inbound campaign backend services exist.
- Inbound campaign tenant UI exists.
- Admin inbound controls exist.
- Exact DID routing exists.
- Pre-answer admission exists.
- Quota and billing reservations exist.
- Business-hours evaluation exists.
- Recording-policy gating exists.
- Transfer controls exist and default closed.
- RLS-safe connection acquisition exists.
- Static RLS regression protection exists.
- Ringback exists in managed dialplan configuration.
- SIP denial causes exist.
- Durable rejection history exists.
- Operator live/rejected views exist.
- Emergency provider-independent audio exists.
- Synthetic liveness exists.
- No-success alert rule exists.
- Deployment preflight exists.
- Dialplan candidate comparison exists.
- Gateway build identity exists.
- Two-DID/two-tenant automated routing proof exists.
- Full backend/frontend/Admin validation described above is green.

### External-pending items

- Push the candidate to the approved remote.
- Review the candidate through the project’s normal change-control process.
- Provision the real synthetic DID.
- Confirm every advertised DID and terminating carrier account.
- Populate the production synthetic environment file.
- Configure a real Alertmanager destination.
- Test alert delivery to the staffed on-call owner.
- Run the Linux C++ build and CTest.
- Verify the running gateway build identity.
- Back up the production database.
- Prove restore to an isolated database.
- Run migration 0036 in the controlled deployment.
- Prove application/schema compatibility.
- Prove the managed dialplan against live Asterisk.
- Capture real INVITE destination metadata.
- Place known-DID carrier calls.
- Place unknown-DID carrier calls.
- Place after-hours carrier calls.
- Place capacity-denied carrier calls.
- Place provider-failure carrier calls.
- Prove the configured greeting.
- Prove answer-to-first-audio latency.
- Prove the emergency clips over RTP.
- Prove live operator rows.
- Prove rejected-call records.
- Prove tenant #2 and DID #2.
- Reconcile carrier, Asterisk, backend, database, ledger and dashboard identities.
- Prove billing minutes and holds.
- Approve recording disclosure wording.
- Prove recording starts only after consent.
- Decide whether transfer is in release scope.
- If included, approve and enable only the exact transfer scope.
- Prove transfer success, failure, billing and restart recovery.
- Run controlled call batches.
- Run the 300-call carrier-reconciled evidence batch.
- Run the approved two-hour soak.
- Run backend/Asterisk restart drills.
- Run dependency failure drills.
- Run rollback rehearsal.
- Obtain engineering approval.
- Obtain operations approval.
- Obtain security/privacy approval.
- Obtain billing approval.
- Obtain support approval.
- Obtain carrier approval.
- Obtain legal/compliance approval.
- Obtain product-owner acceptance.
- Obtain change-owner canary authorization.

### Final expanded verdict

The repository work is production-candidate quality and its automated evidence is strong. The caller, operator, routing, RLS, deployment, media failure, monitoring and frontend gaps identified in the hardening plan have repository implementations and passing tests.

The live service is not yet 100% accepted. The exact external-pending list above is the remaining work. This report deliberately refuses to turn those external gates into “done” statements.

A blind push followed by ad-hoc production testing remains prohibited. The next safe action is the frozen deployment/canary procedure already documented in this report and the release gate.

End of report 14.
