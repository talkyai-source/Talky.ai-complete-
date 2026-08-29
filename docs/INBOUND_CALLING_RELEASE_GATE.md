# Inbound Calling Release Gate

Last updated: 2026-08-29
Scope: Talky.ai inbound calling, tenant dashboard, backend admission/billing, and the proposed ingress architecture

## Current verdict

**The transfer-disabled local candidate has passed the repository-backed controls recorded below, with no known open critical/high code defect. That is not production achievement. Production still runs the pre-inbound commit and database schema (`0021_billing_topup`), while this inbound implementation is uncommitted locally, so production traffic is an unconditional NO-GO. There is no usable staging environment, frozen release manifest, live paging path, verified restore evidence, approved production ingress design, native C++ execution proof, or carrier-delivered call evidence. The source plan's successful-transfer scope is explicitly deferred and requires signed product/telephony acceptance; no live carrier call, deployment, production switch change, or transfer enablement was performed during this work.**

The tenant dashboard has been migrated locally to Next.js 16.3.3. Its production dependency audit is clean, the complete dependency graph has no high or critical finding, and the production build passes. Staging must still prove that the deployed artifact and environment match this verified build.

The safe default remains closed at every layer:

- platform inbound control: OFF
- tenant inbound control: OFF unless explicitly enabled
- OpenSIPS canary: disabled, 0%, frozen
- unknown or ambiguous DID: rejected before answer
- non-Asterisk production adapter: rejected
- non-Redis or unverifiable telephony ownership: rejected
- unavailable or errored Redis global-concurrency state: rejected for inbound admission
- absent or unreadable recording policy: recording remains OFF
- controlled inbound transfer: production declaration remains code-owned OFF;
  a proof-only staging path additionally requires one exact UUID-valid
  tenant/config allowlist plus the independent platform switch
- ambiguous Asterisk Answer outcome: billing is held for evidence-backed adjudication; it is never guessed as answered or unanswered
- unresolved usage overage: billing is held; reserved quota is not silently exceeded
- missing quota/concurrency/AI/trunk/readiness state: rejected

## Non-negotiable invariants

1. A normalized DID resolves to exactly one tenant, one active inbound campaign configuration, and one active inbound trunk.
2. Route, controls, schedule, quota, concurrency, and durable call-row creation complete before Asterisk answers.
3. No default tenant, latest campaign, backup PBX, or unknown-DID AI fallback is permitted.
4. Each admitted provider call has one durable inbound call row, one idempotent reservation, and one idempotent terminal settlement or release.
5. The runtime maximum call duration equals the seconds reserved before answer. Any reported overage is held for reconciliation, never silently charged.
6. Recording buffers stay closed until policy and the pinned disclosure/consent step succeed.
7. Live calls use the immutable admission snapshot. Editing an active configuration is prohibited.
8. Only the process proving the Redis ARI ownership lock may control telephony. Loss of ownership fences the process, drains its locally controlled calls within a bounded deadline, and disconnects the adapter.
9. Canary freeze and rollback disable durable ingress first. `--force` cannot bypass a freeze.
10. Any wrong-tenant route, unconsented recording, billing mismatch, missing call row, one-way audio, transfer-policy breach, or outbound regression is an immediate rollback.
11. A process crash after `StasisStart` but before the first Redis write is recovered only after two successful Talky-application inventory observations, an atomic absent-key cleanup claim, durable DB hydration, and a final local-ownership recheck. Ambiguous inventory or identity fails closed.
12. `Answer()` timeout, cancellation, HTTP 409, or HTTP 5xx is not proof that the carrier did or did not answer. The call enters `provider_answer_ambiguous` hold until reviewed carrier CDR evidence resolves it.
13. A billing hold may be resolved only by a platform administrator, for the explicit tenant and call, with immutable external evidence, one exact idempotency key, the original parent reservation, and the global settlement switch enabled for charging. Release remains available while settlement is disabled.

## Implementation evidence

| Area | Delivered control | Local evidence |
|---|---|---|
| Data | Additive migrations through `0034`, globally unique active DID, direction fields, immutable signed usage ledgers, two-person manual charge approval, audit/reassignment history, RLS/role guard, permanent settlement uniqueness, and repair of historically false-stamped bootstrap databases | Backend and real-PostgreSQL suites |
| Admission | Exact DID route, tenant/platform gates, verified phone and active trunk, schedule, AI, quota and concurrency before answer | Backend combined suite |
| Billing | Full-window pre-answer reservation, signed settlement/release/reversal, explicit Answer/overage holds, evidence-backed manual resolution, stale reservation recovery | Backend, API, and real-PostgreSQL suites |
| Runtime | Durable pre-Answer intent, pinned opening/greeting/prompt/voice/AI config, consent gate, exact quota-backed deadline, idempotent teardown | Backend combined suite |
| Ownership | Production Redis-only coordination, strict acquire/heartbeat/renew, runtime fencing, bounded fair recovery, inverse ARI orphan discovery, and non-owner admission rejection | Backend combined suite |
| Tenant UI | List/create/detail/edit/readiness/activate/pause/archive, exact RBAC, verified DID inventory, fail-closed permission discovery | Typecheck, production build, focused tests |
| Call UI | Direction-aware call history and details, caller/DID party display | Frontend tests and production build |
| SIP edge | Exact DID/source gate, frozen explicit assignment, Asterisk-only dispatcher, no pre-answer `Answer()`, no fallback | Telephony static suite |
| Rollback | Durable disabled + 0% + frozen state before restart/dispatcher change | Telephony static suite |
| CI | Backend migration round-trip and DB integration, tenant frontend, Admin frontend, dependency audit, and telephony static suites included in workflow | Workflow review |

## Source-plan traceability and execution status

| Source-plan obligation | Status | Evidence required or recorded |
|---|---|---|
| Inbound code, tenant isolation, billing, and PostgreSQL contracts through `0034` | **Locally proven** | Repository tests and local real-PostgreSQL migration, transaction, RLS, uniqueness, idempotency, append-only ledger, and four-eye approval proofs; this does not replace the staging-sized-clone gate below. |
| Live PSTN signaling, carrier routing/CDRs, SIP/RTP, and two-way media | **External pending** | Controlled carrier-delivered calls and correlated carrier, PBX, backend, database, and dashboard evidence. |
| Browser behavior and deployed-artifact parity | **External pending** | Desktop/mobile role-based browser E2E against the deployed staging artifact, including failure and recovery states. |
| Rollback/restore, paging alerts, and failure drills | **External pending** | Measured RTO/RPO, clean-database restore, compatibility proof, ingress-disable proof, and alerts delivered through the real on-call path. |
| Canary batches and soak/load | **External pending** | Disjoint 5 + 30 + 10 + 5 controlled-call batches and the 300-call soak with approved thresholds and reconciled evidence. |
| Legal/privacy, security, operations, billing, support, carrier, and change approval | **External pending** | Named approvers and immutable sign-off records in the final table; no local automated result substitutes for these approvals. |
| Inbound transfer | **Deferred; production disabled by design** | Production remains closed in code. A staging-only proof path is scoped to one exact tenant/config and still requires the independent platform switch; only rejection is in production release scope until linked-leg live proof and sign-off complete. |
| Real outbound non-regression baseline | **Not captured; original pre-change point is irrecoverable** | Require a signed deviation/waiver, compare the current candidate with the last known-good release and historical carrier CDR/PBX evidence, then complete one evidenced predeployment call plus daily and frozen-candidate outbound smoke calls. |

Verification recorded through 2026-08-29:

- final post-hardening backend unit/security regression: **7,362 passed, 7 skipped, 0 failed**; pytest reported **1,304 non-failing warnings**, principally existing deprecations plus test-double coroutine warnings that remain engineering debt
- final focused release-control aggregate covering load/soak evidence, emitted alert contracts, operational safety, candidate/run canary scope-contract enforcement, production fail-closed gates, KMS, gateway callback authentication, concurrency races, deployment contracts, and the Day 10 harness: **197 passed, 0 failed**; the load/soak contract file independently passed **27/27** after widening only its multi-process test-harness timeout from 8 to 20 seconds
- independently selected four-eye billing-hold, Admin API, bootstrap, and reassignment unit suites: **106 passed**; final-head real-PostgreSQL hold/reassignment transaction tests: **3 passed**
- PostgreSQL 16 inbound migration proof: the chain was applied through the then-current head `0034_inbound_billing_four_eye`, and the focused `0032 → 0031 → 0032` hold-migration round trip passed; hold, bootstrap-repair, append-only ledger, and four-eye approval contracts were exercised
- PostgreSQL 16 bootstrap paths: false-stamped legacy repair, preserved baseline, and maintained fresh bootstrap each passed **11/11** final contract tests after upgrading to `0034`; all three disposable proof databases independently reported `0034_inbound_billing_four_eye` at the time of that proof
- the current uncommitted worktree reports the sole Alembic head `0035_user_profiles_role_widen`; its fail-closed constraint-drift contract passed **23/23**, and two isolated real-PostgreSQL 16 bootstrap paths passed **13/13 each (26/26 combined)** through upgrade, retained-new-role downgrade refusal, clean downgrade to `0034`, and re-upgrade to `0035`; both disposable databases, the loopback-only temporary cluster, and its runner were removed afterward
- the maintained `complete_schema.sql` path was loaded exactly; the preserved dump's SQL was unchanged, but local `psql 16.1` required filtering exactly its paired client-only `\restrict`/`\unrestrict` guards emitted by `pg_dump 16.14`; CI now pins a `psql 16.14` container to restore the exact unfiltered file, and staging must do the same with a compatible client
- these disposable databases prove schema behavior, not production migration performance: the sanitized production-sized staging clone, lock/headroom measurement, exact backup restore, and production deployment gates remain open
- migration/bootstrap static regression after final formatting: **76 passed**
- migration `0028_call_terminal_settlement_cas` is an intentional hard rollback boundary: downgrade refuses transactionally and leaves Alembic at head; restore by code-forward/database backup, not by recreating the unsafe pre-`0028` functions
- PostgreSQL migration `0022` proof: pre-inbound baseline → upgrade → guarded downgrade → baseline security assertions → re-upgrade; passed
- PostgreSQL DID/reassignment proof: **2 passed**, including cross-tenant history preservation and concurrent live-DID uniqueness
- migration `0023` dual-bootstrap compatibility and admin-media controls: **14 focused tests passed**; incompatible schema drift is rejected atomically and downgrade preserves immutable audit evidence
- answered-call ownership-loss fix: **60 affected tests passed**, including four deterministic race/split-brain cases
- backend Python compilation: passed
- gateway callback authentication/origin and production-startup contracts: **76 focused checks passed**; the candidate-bound drain-manifest/deploy contracts independently passed **21 focused checks**; these counts overlap the broader suites and are not additive totals
- telephony suite: **25 static/controller checks passed; 14 Docker/live checks skipped by design; 0 failed**
- tenant frontend: Next.js **16.3.3** typecheck passed; full unit runner **234 passed, 2 database-dependent checks skipped, 0 failed (236 collected)**
- tenant frontend lint: passed with **0 findings**
- tenant frontend optimized production build: passed; **66/66** static pages generated, all inbound routes present, and the Next.js proxy recognized
- tenant frontend security: production audit **0 vulnerabilities**; complete graph **0 high/critical** with three moderate development-only Storybook findings
- Admin frontend: **13/13 tests passed**, lint passed, production build passed, dependency audit **0 vulnerabilities**
- backend requirements resolve with `pip check`; dependency audit is clean except for the explicitly documented no-fix advisory; pinned security upgrades include `aiohttp 3.14.3`, `cryptography 50.0.0`, and `pyOpenSSL 26.4.0`
- CI workflow parses successfully and blocks on the real migration/database integration, both frontends, full high-severity dependency audit, and telephony static checks
- scoped diff validation: passed; only repository line-ending notices remain

The skipped Docker/live telephony checks require the deployment stack, carrier path, SIP/RTP traces, and production-equivalent networking. They are external release gates below, not evidence of production readiness. Native C++ CTest/sanitizer execution was also unavailable on this workstation because no C/C++ compiler, usable WSL distribution, or Docker runtime is installed; the Linux gateway release builder enforces CTest before publication. `promtool`, `amtool`, and a usable deployed-browser test target were likewise unavailable, so native rule validation, real pager delivery, and browser/deployed-artifact parity remain open external gates.

### Read-only production and environment inventory (2026-08-28 19:57Z)

- production application HEAD is `41326b9430270abb6b00dec8422e25a2517e85af`;
  its database is at `0021_billing_topup`, and the inbound admission service,
  inbound migrations through `0034`, the current local `0035` head, and this
  runbook are absent there
- production health/readiness/deep/worker/gateway checks returned HTTP 200 and
  the existing API, workers, voice gateway, timers, PostgreSQL, Redis, and
  Asterisk were active; this proves only the existing pre-inbound service
- production is Asterisk-only; OpenSIPS, Kamailio, rtpengine, FreeSWITCH,
  Prometheus, Grafana, Alertmanager, and exporters are absent
- production lacks the explicit `TELEPHONY_ADAPTER=asterisk` declaration and
  an ARI username; active-call idleness could not be proven with the available
  read-only account
- no staging URL/environment, sanitized staging database, carrier fixtures,
  frozen manifest, usable CI control-plane credential, live pager destination,
  restorable backup evidence, or measured RTO/RPO was available
- production repository drift includes a modified built gateway binary and an
  untracked `secrets/` directory; contents were deliberately not inspected

No live calls, deployments, switch changes, or other external mutations were
made while collecting this inventory.

## Required production configuration

Application process:

```text
ENVIRONMENT=production
TELEPHONY_ADAPTER=asterisk
TELEPHONY_STATE_BACKEND=redis
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
VOICE_GATEWAY_CALLBACK_HOST=127.0.0.1
INTERNAL_SERVICE_TOKEN=<secret-manager reference; never inline in evidence>
VOICE_GATEWAY_AUTH_TOKEN=<distinct secret-manager reference>
```

Production startup rejects missing, short, or reused gateway secrets and any
`BACKEND_INTERNAL_URL` that is not an exact plain-HTTP canonical numeric
loopback origin with an explicit port and no credentials, query, fragment,
trailing slash, or base path. The callback host must exactly equal that origin
host. The gateway independently enforces the same startup boundary, accepts
only `/api/v1/sip/telephony/audio/<safe-session-id>` callbacks at that exact
scheme/host/port, and repeats the destination check immediately before adding
the internal token. Session routes require the distinct gateway bearer token;
the backend caller-audio route always requires the internal token.

### Mandatory ingress-topology decision

The current production topology is Asterisk-only, but the canary freeze,
percentage rollout, spoofing, and dispatcher proofs in this plan are designed
for OpenSIPS → Asterisk. A named telephony owner must approve and document one
of these before any inbound deployment:

1. deploy and validate the frozen OpenSIPS → Asterisk architecture, including
   its rollback and monitoring stack; or
2. replace every OpenSIPS-dependent gate with an equivalent, independently
   reviewed Asterisk-only ingress, carrier-source, freeze, percentage rollout,
   and rollback control.

Repository telephony scaffolding is not evidence that OpenSIPS exists in
production and must not be deployed as an assumed topology.

If option 1 is approved, OpenSIPS must begin in this durable state:

```text
OPENSIPS_CANARY_ENABLED=0
OPENSIPS_CANARY_PERCENT=0
OPENSIPS_CANARY_FREEZE=1
```

Before activation, replace every placeholder with reviewed production values:

- `OPENSIPS_CANARY_DID`: carrier-delivered digits only, 7–15 digits, no `+`
- `OPENSIPS_CANARY_AGENT_ID`: concrete RFC 4122 agent UUID
- `OPENSIPS_CANARY_SOURCE_REGEX`: anchored exact-IP alternatives containing loopback and every approved carrier SBC source IP
- Asterisk ARI host, port, app, username, and secret from the secret manager
- pinned production image digests
- firewall/NAT and RTP port range
- carrier TLS/mTLS and certificate policy

Every canary evidence run requires a newly frozen identity in both the backend
metrics process and the controller process:

```text
TELEPHONY_CANARY_TENANT_ID=<approved tenant UUID>
TELEPHONY_CANARY_CONFIG_ID=<approved pinned inbound-config UUID>
TELEPHONY_CANARY_DID=<same 7-15 digits as ingress, without +>
TELEPHONY_CANARY_CANDIDATE_DIGEST=sha256:<64 lowercase hex>
TELEPHONY_CANARY_RUN_ID=<new UUIDv4>
TELEPHONY_CANARY_GATE_STARTED_AT=<fresh canonical UTC YYYY-MM-DDTHH:MM:SSZ>
```

The matching inbound runtime route metadata must carry
`canary_scope.inbound_config_id`, `canary_scope.did`,
`canary_scope.candidate_digest`, and `canary_scope.run_id`, and its regex must
match the dedicated DID. The backend accepts at most a six-hour-old run and
queries only distinct call IDs, runtime request IDs, transfer parent-call IDs,
and paired rollback events created at or after the exact start. Partial query
failure publishes zero evidence. The controller independently validates the
same scope hash and baseline, requires a fresh scrape and latest call, and holds
one lock keyed to the resolved environment file through snapshot, decision, and
state action; changing the run ID or evidence directory cannot bypass it.

Current call rows do not persist candidate/run tags. Therefore the exact
artifact and route configuration must be deployed and frozen before the start
timestamp, and any process, artifact, environment, route, prompt, model, or
configuration mutation invalidates the entire run. The metrics identity is not
proof of deployed-artifact parity by itself.

Never commit the populated production environment file or secrets.

## Frozen release manifest

Create one immutable manifest before any controlled call batch. It must record the backend and both frontend commit SHAs and artifact/image digests; Alembic head; OpenSIPS and Asterisk configuration hashes; non-secret environment and feature-flag snapshot; secret-manager version IDs; carrier route version; STT/TTS/LLM provider and model versions; voice, prompt, and knowledge versions; scorecard version and approved thresholds; test tenant, DID, campaign, and configuration IDs; rollback artifact/version; and the evidence repository location. Any behavioral code, schema, configuration, model, prompt, knowledge, secret-version, scorecard, or test-fixture change invalidates the active batch and starts a new manifest.

Before the batch, approve numeric limits and alert routes for call setup, first audio, turn latency, dead air, drop/error rate, stale reservation and billing-hold age, orphan recovery, Redis/PostgreSQL saturation, CPU, memory, and queue depth. Prove carrier, OpenSIPS, Asterisk, backend, Redis, PostgreSQL, and dashboard clocks are NTP-aligned closely enough to correlate one call. Fire every release-critical alert through the real paging path; a dashboard that nobody receives is not evidence.

### Monitoring coverage is intentionally incomplete

The repository-backed rules can honestly alert on backend metrics-target loss,
stale database-backed telemetry refresh, fail-closed inbound dependency errors,
new billing-hold creation, stale-reservation recovery detection, and
unconfirmed/stuck supervised-transfer cleanup. These rules reference bounded
metrics that the application actually emits.

They do not close the release gate. The current exported metrics and checked-in
scrape topology have no current-age/backlog gauge for unresolved billing holds,
stale reservations, incomplete recovery, or general ARI orphans; no
PostgreSQL/Redis target or saturation series; and no DB-pool, CPU/cgroup memory,
or dialer queue-depth series. The checked-in Alertmanager receivers are empty
by design because there is no approved on-call destination or secret mount;
they group alerts but notify nobody. Do not add rules for imagined metric names
or treat an empty receiver as a page.

Before any controlled carrier batch, provision and validate the missing
collectors/exporters, approve numeric thresholds, install a separately rendered
Alertmanager configuration whose credentials come from mounted secret files,
record its non-secret hash in the frozen manifest, and prove critical plus
warning delivery through the real escalation path. Until then, observability
and paging remain explicit external release blockers.

## Manual billing-hold adjudication

This is an exception workflow, not an ordinary call-completion path. Only a platform administrator may resolve a hold, and only after obtaining an immutable carrier/provider record whose tenant, provider call ID, direction, timing, and disposition match the durable Talky call. Preserve the evidence outside Talky, calculate its SHA-256 digest, and record the operator, reviewer, ticket, timestamp, and exact request/response.

The endpoint is:

```text
POST /api/v1/admin/inbound/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve
Idempotency-Key: <unique 8-255 character operation key>
```

An Answer-ambiguity release request looks like:

```json
{
  "hold_reason": "provider_answer_ambiguous",
  "decision": "release_unanswered",
  "evidence_type": "carrier_cdr",
  "evidence_reference": "carrier-cdr://immutable-reference",
  "evidence_sha256": "<64 lowercase hexadecimal characters>",
  "adjudication_reason": "Carrier CDR proves the call was never answered",
  "authoritative_duration_seconds": 0
}
```

A provider-usage finalization is a two-request workflow. Administrator A creates the immutable pending request with a unique requester key:

```json
{
  "hold_reason": "usage_exceeded_reservation",
  "decision": "finalize",
  "evidence_type": "provider_usage_record",
  "evidence_reference": "provider-usage://immutable-reference",
  "evidence_sha256": "<64 lowercase hexadecimal characters>",
  "adjudication_reason": "Provider usage record establishes terminal duration and cost",
  "authoritative_duration_seconds": 91,
  "authoritative_cost": 12.3456,
  "authoritative_currency": "USD",
  "approval_action": "request"
}
```

The response remains `billing_status: "held"`, reports `workflow_status: "pending_approval"`, and returns an `approval_request_id`. No usage, cost, call projection, quota, or monetary state changes at this point. Administrator B—who must be a different authenticated platform administrator—then sends the exact same normalized evidence and adjudication values with a new independent `Idempotency-Key`, plus:

```json
{
  "approval_action": "approve",
  "approval_request_id": "<UUID returned to administrator A>"
}
```

The fields above are additions to the complete original finalization body, not a replacement body. Approval atomically locks and rechecks the settlement switch and pending evidence, records the distinct approver, writes the immutable settlement, updates the call projection, appends the audit event, and commits the idempotency result.

Operational rules:

- `provider_answer_ambiguous` requires `carrier_cdr`; `usage_exceeded_reservation` requires `provider_usage_record`.
- `finalize` requires an authoritative nonnegative duration. Cost is optional, but cost and currency are atomic: provide both or neither. Cost must fit exact `DECIMAL(10,4)` and currency must be an assigned ISO-4217 code. Omitting `approval_action` is backward-compatible but fail-closed: it creates only the pending request and never charges.
- `release_unanswered` permits only zero/absent duration and zero/absent cost. It remains available when global settlement is OFF; `finalize` does not.
- Requester and approver must be distinct platform administrators with separate keys. Approval must repeat every immutable normalized value exactly—tenant, call, hold reason, decision, evidence type/reference/SHA-256, adjudication reason, duration, cost, and currency—and name the pending UUID. Same-admin approval, changed evidence/value, opposite decision, third-admin replay, or same approver with a changed key conflicts. Exact requester and approver retries return their stored results. After an uncertain response, read current approval, call, ledger, and audit state before deciding whether any new operation is allowed; never work around a conflict by casually minting another key.
- Before resolving a parent, verify it has no transfer child in `reserved` or `held` billing state. The service rejects unresolved child usage, but the operator must treat its presence as an incident, not a retry prompt.
- After success or replay, fetch `GET /api/v1/admin/calls/{call_id}` for the current call state, then use the approved privileged read-only database/audit path below for the immutable usage and adjudication records. `AdminCallDetail` does not return those ledger tables. The signed ledger is authoritative for money and currency. An idempotent replay reports the original operation result; after any later reversal, fetch current state rather than interpreting that original response as the present billing state.

Before a canary or deployment, these read-only checks must return only understood reasons and zero unresolved transfer children for held parent calls:

```sql
SELECT billing_hold_reason, count(*)
FROM calls
WHERE direction = 'inbound' AND billing_status = 'held'
GROUP BY billing_hold_reason
ORDER BY billing_hold_reason;

SELECT leg.call_id, count(*) AS unresolved_transfer_legs
FROM call_legs AS leg
JOIN calls AS call ON call.id = leg.call_id
WHERE call.direction = 'inbound'
  AND call.billing_status = 'held'
  AND leg.leg_type ILIKE '%transfer%'
  AND leg.billing_status IN ('reserved', 'held')
GROUP BY leg.call_id;

SELECT id, tenant_id, call_id, call_leg_id, transaction_type,
       quantity_seconds, amount, currency, related_transaction_id,
       idempotency_key, metadata, created_at
FROM inbound_usage_transactions
WHERE tenant_id = '<tenant UUID>' AND call_id = '<call UUID>'
ORDER BY created_at, id;

SELECT id, event_type, actor_id, actor_role, reason,
       before_state, after_state, metadata, idempotency_key, created_at
FROM inbound_audit_events
WHERE tenant_id = '<tenant UUID>'
  AND resource_type = 'call'
  AND resource_id = '<call UUID>'
ORDER BY created_at, id;

SELECT id, tenant_id, call_id, hold_reason, evidence_type,
       evidence_reference, evidence_sha256, resolution_hash,
       requested_by, request_id, status, approved_by,
       approval_idempotency_key, requested_at, approved_at
FROM inbound_billing_hold_finalize_approvals
WHERE tenant_id = '<tenant UUID>' AND call_id = '<call UUID>';
```

## Staging deployment order

Record an owner, timestamp, command/output link, and reviewer for every item.

- [ ] Keep OpenSIPS disabled and frozen.
- [ ] Take and verify a restorable database backup.
- [ ] Restore a sanitized, production-sized staging clone and record its size, representative table counts, free disk/headroom, migration duration, blocking sessions, and lock wait. Run the complete chain while normal application traffic is quiesced or within the approved maintenance window; a small empty database is not migration-performance or lock evidence.
- [ ] Run Alembic as the exact deployment role under the deployment-wide migration lock, attempt a concurrent second migrator, and prove it cannot advance the version concurrently. Record acquisition/release behavior on success and injected failure, and stop if an unexpected long-running transaction or lock waiter appears.
- [ ] As the exact migration role, prove `pg_trgm` is already installed or that the role is authorized to install it. If not, require the database owner to preinstall it; do not grant temporary superuser. After migration, verify `pg_extension`, the required trigram indexes/operators, and application-role use.
- [ ] Record that the source plan's original **before-changes** outbound call was not captured and cannot be recreated retroactively. Obtain a signed deviation/waiver, compare against the last known-good release and historical carrier CDR/PBX/dashboard evidence, then—before deploying any inbound artifact—complete one normal outbound call as the candidate baseline. Repeat it each deployment day and against the frozen release candidate; automated tests are not a substitute and any material drift is a rollback.
- [ ] Before migration, query for duplicate `canonical_did` values across every non-archived inbound assignment. Reconcile/archive duplicates under four-eye review; migration `0022` intentionally fails rather than choosing a winner.
- [ ] Apply the complete reviewed Alembic chain through the frozen candidate's actual sole head—currently `0035_user_profiles_role_widen`—to staging. Repeat the now-green disposable PostgreSQL proof on the sanitized production-sized clone, including migration duration, lock/headroom behavior, guarded downgrade/restore, and exact unfiltered backup restore with a client at least as new as the dump producer. Do not stop at `0022`/`0033`/`0034` or use the retired `0021` snapshot-stamp shortcut.
- [ ] Verify migration head, constraints, triggers, indexes, RLS policies, and default OFF controls.
- [ ] Run the database-backed reassignment integration with `TEST_DATABASE_URL`.
- [ ] With ingress and outbound origination frozen and every carrier/PBX/gateway/Redis/database live count proven zero, publish a short-lived candidate-bound production drain manifest with its exact-file SHA-256, two independent approvals, and immutable topology/change references. The deploy verifier must atomically consume it before SSH; a repeated ID, non-zero count, stale timestamp, candidate mismatch, or changed digest blocks deployment. This artifact still externally attests the carrier/Asterisk/Redis/database facts and does not eliminate TOCTOU. Then build and run CTest for the C++ gateway from the exact frozen SHA, atomically publish it, and restart it before the matching backend. Prove the gateway and API load distinct valid control/internal tokens, wrong-origin callbacks are rejected, authenticated exact-origin callbacks succeed, and the gateway refuses invalid security configuration. A manifest or single `/stats` sample is not an independent drain proof.
- [ ] Deploy the backend with production-equivalent Asterisk and Redis settings.
- [ ] Prove exactly one process owns ARI; kill Redis connectivity and confirm the old owner rejects calls and disconnects.
- [ ] Deploy the Talk-Leee build and verify all inbound routes under tenant-admin and restricted roles.
- [ ] Run browser E2E on desktop and mobile for tenant admin, viewer/restricted, and unauthorized roles. Prove loading, retry, configuration-error, low-balance, and incomplete-activation states; unit tests, typecheck, and build do not satisfy this visual/live gate.
- [ ] Verify the deployed tenant artifact matches the locally validated Next.js 16.3.3 build; rerun the production and complete-graph dependency audits in staging.
- [ ] Deploy OpenSIPS/Asterisk while still frozen.
- [ ] Run native parsers: `docker compose config`, `opensips -C`, Asterisk config load, and shell syntax checks.
- [ ] Verify the carrier source IP allowlist, SIP authentication/TLS policy, NAT, and RTP ranges.
- [ ] Attempt carrier-source spoofing, malformed/replayed SIP headers, and bounded flood/replay cases; confirm rejection occurs before answer and creates no AI/media/billing session.
- [ ] Verify carrier, STT, TTS, LLM, storage, database, and Redis account health/capacity; credential/certificate expiry; secret rotation and rollback; and prompt/knowledge exfiltration resistance.
- [ ] Confirm OpenSIPS sends the normalized DID and frozen assignment headers expected by Asterisk/backend.
- [ ] Confirm Asterisk accepts the new Stasis arguments and that only the backend admission callback can cause answer.
- [ ] Inventory plural, disjoint staging fixtures before testing: Tenant A and Tenant B; their administrators and restricted users; dedicated known and unknown DIDs; multiple approved caller numbers plus private/anonymous ANI; inbound and both-direction trunks; active/paused/archived campaigns and assignments; agent-first/caller-first and business/after-hours configurations; and sufficient-, low-, and exhausted-balance states. Record every tenant, user, DID, campaign, config, trunk, and balance fixture ID in the frozen manifest.
- [ ] Confirm readiness is false for each deliberately broken dependency, then true only when all are restored.

### Controlled transfer proof window (staging only)

This is an isolated evidence window for the original plan's linked-leg transfer
scenarios, not authorization to include successful transfer in the production
release. The default is
`INBOUND_TRANSFER_STAGING_PROOF_ENABLED=false`. Production must keep it false
or unset; production startup rejects every truthy or unknown enabled value.
Use a dedicated test tenant, DID, destination, and signed change ID. Freeze SIP
ingress, confirm zero active test calls/legs, and name the rollback operator
before opening either gate.

```bash
bash telephony/scripts/canary_freeze.sh freeze \
  telephony/deploy/docker/.env.telephony
sh telephony/scripts/assert_canary_ingress.sh all \
  telephony/deploy/docker/.env.telephony
```

Set these shell values on the staging operator host; never use production
tokens or URLs:

```bash
export STAGING_API="https://staging-api.example.com/api/v1"
export PLATFORM_ADMIN_TOKEN="<staging-platform-admin-token>"
export TEST_TENANT_TOKEN="<staging-test-tenant-admin-token>"
export OTHER_TENANT_TOKEN="<staging-tenant-b-admin-token>"
export TRANSFER_PROOF_CHANGE_ID="<signed-change-id>"
export TRANSFER_PROOF_TENANT_ID="<staging-test-tenant-uuid>"
export TRANSFER_PROOF_CONFIG_ID="<existing-safe-staging-inbound-config-uuid>"
export OTHER_TEST_CONFIG_ID="<different-config-owned-by-test-tenant-uuid>"
export OTHER_TENANT_CONFIG_ID="<config-owned-by-staging-tenant-b-uuid>"
```

Open and verify in this order:

1. Keep the durable platform transfer switch false. Before opening the
   process gate, scan **all tenants**, not only the test tenant. There must be
   no latent transfer-enabled configuration; otherwise stop and remediate it:

   ```sql
   SELECT tenant_id, id AS config_id, status, after_hours_action, transfer_policy
   FROM inbound_campaign_configs
   WHERE after_hours_action = 'transfer'
      OR transfer_policy @> '{"enabled": true}'::jsonb;
   -- must return zero rows before the proof window opens
   ```

2. In the **staging backend
   service configuration only**, set and redeploy/restart the same frozen
   artifact with all three proof values. The identifiers must match the
   signed manifest exactly:

   ```dotenv
   ENVIRONMENT=staging
   INBOUND_TRANSFER_STAGING_PROOF_ENABLED=true
   INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID=<staging-test-tenant-uuid>
   INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID=<existing-safe-staging-inbound-config-uuid>
   ```

3. Before opening the durable switch, prove that only the exact scoped
   campaign's process/runtime gate is open. A request without `config_id`, or
   with any other tenant/config pair, must report runtime/configuration false:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer ${TEST_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities?config_id=${TRANSFER_PROOF_CONFIG_ID}" \
   | jq -e '.transfer_runtime_available == true
       and .transfer_platform_enabled == false
       and .transfer_configuration_available == false'

   curl -fsS \
     -H "Authorization: Bearer ${TEST_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities" \
   | jq -e '.transfer_runtime_available == false
       and .transfer_platform_enabled == false
       and .transfer_configuration_available == false'

   curl -fsS \
     -H "Authorization: Bearer ${TEST_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities?config_id=${OTHER_TEST_CONFIG_ID}" \
   | jq -e '.transfer_runtime_available == false
       and .transfer_platform_enabled == false
       and .transfer_configuration_available == false'

   curl -fsS \
     -H "Authorization: Bearer ${OTHER_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities?config_id=${OTHER_TENANT_CONFIG_ID}" \
   | jq -e '.transfer_runtime_available == false
       and .transfer_platform_enabled == false
       and .transfer_configuration_available == false'
   ```

4. Read the current controls, preserve every unrelated switch, and enable only
   transfer with optimistic versioning and a unique change-scoped idempotency
   key:

   ```bash
   controls="$(curl -fsS \
     -H "Authorization: Bearer ${PLATFORM_ADMIN_TOKEN}" \
     "${STAGING_API}/admin/inbound/controls")"
   open_payload="$(jq -c \
     --arg reason "controlled staging transfer proof ${TRANSFER_PROOF_CHANGE_ID}" \
     '{inbound_enabled, recording_enabled, settlement_enabled,
       transfer_enabled: true, reason: $reason, expected_version: .version}' \
     <<<"${controls}")"
   opened="$(curl -fsS -X PATCH \
     -H "Authorization: Bearer ${PLATFORM_ADMIN_TOKEN}" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: transfer-proof-open-${TRANSFER_PROOF_CHANGE_ID}" \
     --data "${open_payload}" \
     "${STAGING_API}/admin/inbound/controls")"
   jq -e '.transfer_enabled == true' <<<"${opened}"
   ```

5. Prove both independent gates are now open for the exact allowlisted config,
   then configure only the isolated
   test campaign and run the signed linked-leg matrix:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer ${TEST_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities?config_id=${TRANSFER_PROOF_CONFIG_ID}" \
   | jq -e '.transfer_runtime_available == true
       and .transfer_platform_enabled == true
       and .transfer_configuration_available == true'
   ```

Close on completion **or on the first failed/uncertain step**:

1. Freeze and disable durable SIP ingress first. Stop new attempts and confirm
   every test parent/target leg has a terminal state or has been forcibly
   reconciled within the approved deadline.

   ```bash
   bash telephony/scripts/canary_freeze.sh freeze \
     telephony/deploy/docker/.env.telephony
   sh telephony/scripts/assert_canary_ingress.sh all \
     telephony/deploy/docker/.env.telephony
   ```
2. Change the isolated test campaign back to `hangup`/`voicemail` with
   `transfer_policy.enabled=false`; do not leave a latent transfer policy.
3. Preserve unrelated controls and close the durable platform switch:

   ```bash
   controls="$(curl -fsS \
     -H "Authorization: Bearer ${PLATFORM_ADMIN_TOKEN}" \
     "${STAGING_API}/admin/inbound/controls")"
   close_payload="$(jq -c \
     --arg reason "close staging transfer proof ${TRANSFER_PROOF_CHANGE_ID}" \
     '{inbound_enabled, recording_enabled, settlement_enabled,
       transfer_enabled: false, reason: $reason, expected_version: .version}' \
     <<<"${controls}")"
   closed="$(curl -fsS -X PATCH \
     -H "Authorization: Bearer ${PLATFORM_ADMIN_TOKEN}" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: transfer-proof-close-${TRANSFER_PROOF_CHANGE_ID}" \
     --data "${close_payload}" \
     "${STAGING_API}/admin/inbound/controls")"
   jq -e '.transfer_enabled == false' <<<"${closed}"
   ```

4. Reset `INBOUND_TRANSFER_STAGING_PROOF_ENABLED=false` and unset both
   `INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID` and
   `INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID` in the staging service
   configuration, then redeploy/restart. This reset-to-false step is mandatory
   rollback, not cleanup that may be deferred.
5. Verify all three capabilities are closed and no latent transfer policy
   remains for the test tenant:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer ${TEST_TENANT_TOKEN}" \
     "${STAGING_API}/inbound-campaigns/capabilities?config_id=${TRANSFER_PROOF_CONFIG_ID}" \
   | jq -e '.transfer_runtime_available == false
       and .transfer_platform_enabled == false
       and .transfer_configuration_available == false'
   ```

   ```sql
   SELECT inbound_transfer_enabled
   FROM platform_runtime_controls
   WHERE id = 1; -- must be false

   SELECT tenant_id, id, after_hours_action, transfer_policy
   FROM inbound_campaign_configs
   WHERE after_hours_action = 'transfer'
      OR transfer_policy @> '{"enabled": true}'::jsonb;
   -- must return zero rows across every tenant
   ```

6. Retain the open/close API responses, audit events, environment-version
   evidence, parent/child leg reconciliation, and the final false/zero-row
   proofs. Keep ingress disabled after the proof; the production release scope
   remains transfer-disabled.
- [ ] Enable the tenant gate, settlement gate, and platform gate through audited/versioned controls.

## Mandatory staging call matrix

Each test must capture provider call ID, durable call ID, tenant, DID, route/config version, answer time, hangup reason, recording state, reservation, settlement, logs, and expected/actual outcome. Structured routing/failure logs must expose the bounded event name, durable/provider correlation IDs, tenant/config/assignment IDs, stage, outcome/reason code, and timestamp while redacting ANI, secrets, prompts, and knowledge. Prove one call can be followed without ambiguity from carrier through OpenSIPS, Asterisk, backend, database, and dashboard.

### Routing and isolation

- [ ] Known DID reaches the exact configured tenant/campaign.
- [ ] Unknown DID is rejected before answer and has no AI/media session.
- [ ] Duplicate active DID assignment is prevented.
- [ ] Cross-tenant context/header/body tampering is rejected.
- [ ] Replayed provider event returns the same durable call identity without a second reservation.
- [ ] Paused/archived campaign, paused assignment, unverified number, wrong-direction trunk, inactive tenant, or disabled tenant gate is rejected.
- [ ] Inbound caller/contact creation follows the approved policy, repeated ANI is deduplicated deterministically, and private/anonymous ANI never creates a fabricated identity.
- [ ] Tenant A cannot retrieve or inject Tenant B knowledge. The pinned campaign knowledge version and retrieval scope match the admitted tenant on every call.

### Time and configuration

- [ ] Agent-first and caller-first openings use the pinned greeting and never overlap.
- [ ] Business-hours boundaries, overnight windows, holidays, invalid timezone, and DST transitions behave deterministically.
- [ ] A mid-call configuration edit cannot change the active call snapshot.
- [ ] The durable admission snapshot pins the exact prompt, knowledge, voice/TTS, and AI provider/model versions used by the call; dashboard and backend evidence agree.
- [ ] Invalid or unsupported overrides block activation/admission.
- [ ] Maximum duration hangs up at the reserved deadline and settles at or below the reservation.

### Billing and concurrency

- [ ] Rejection before media releases the reservation and lease exactly once.
- [ ] Completed call settles actual seconds and returns unused reserved seconds.
- [ ] Duplicate terminal events do not double bill or double release.
- [ ] A timed-out/cancelled `Answer()` and ARI 409/5xx creates exactly one `provider_answer_ambiguous` hold; ARI 400/401/403/404/405 follows the definitive pre-answer release path.
- [ ] A platform administrator resolves an Answer-ambiguity hold only from a carrier CDR reference and SHA-256; exact replay returns the original result, while changed evidence or the opposite decision conflicts.
- [ ] A platform administrator resolves `usage_exceeded_reservation` only from a provider usage record; the ledger delta and quota equal authoritative duration minus the original reservation.
- [ ] With global settlement OFF, manual finalize is rejected and remains atomic, while an evidence-backed unanswered release is permitted.
- [ ] Every manual monetary finalization has independent four-eye approval linking the immutable evidence, normalized request, currency, operator, reviewer, and ticket.
- [ ] Reversal restores quota and creates one compensating immutable entry.
- [ ] Run separate sufficient-, low-, and exhausted-balance calls. Prove sufficient admits and settles, low balance surfaces the approved warning and enforces the exact shortened/denied policy, and exhausted rejects before answer without AI/media or a charge.
- [ ] While Tenant A calls, prove Tenant B's reservations, quota/minutes, concurrency leases, usage ledger, billing balance, and Admin/tenant UI totals remain byte-for-byte or value-for-value unchanged except for unrelated timestamps explicitly excluded from the comparison.
- [ ] Exhausted quota rejects before answer.
- [ ] Remaining quota shortens the admitted maximum and timer to the same value.
- [ ] Tenant, global, and per-pod concurrency limits reject bursts without oversubscription.
- [ ] Run exact two-simultaneous-call and five-simultaneous-call batches, then a limit-plus-one rejection batch; reconcile leases, rows, audio, and settlements for every call.
- [ ] Duplicate, reordered, and simultaneous terminal callbacks still create exactly one terminal call transition and one terminal ledger entry.
- [ ] Before each batch, the canary tenant has zero stale reservations/leases, zero unowned holds, zero holds older than the approved threshold, and zero unexplained parent/transfer settlement mismatch. Reconcile carrier CDR seconds, rounding, amount, and currency to the ledger per call.
- [ ] Redis/DB interruption never creates an answered but untracked call.
- [ ] Kill the owner after `StasisStart` but before its first Redis write. Recovery requires two successful bounded inventory observations, never claims an internal/media/local/preemptive channel, creates one cleanup obligation only if no key exists, hydrates durable tenant/provider identity, and never answers or starts AI.
- [ ] Deliver a late normal `StasisStart` while inverse recovery is hydrating the same channel. The normal ownership record wins; the cleanup claimant rechecks ownership and performs no PBX action.

### Recording, privacy, and safety

- [ ] Recording disabled: no recording is created.
- [ ] Recording enabled: the exact pinned disclosure is the first required audio and buffers open only afterward.
- [ ] Consent/disclosure failure leaves recording closed and terminates safely.
- [ ] Caller hangup during the greeting/disclosure path ends promptly with recording buffers closed and exactly one reservation release/terminal transition.
- [ ] Private/anonymous ANI is not invented or exposed.
- [ ] Tenant A cannot fetch Tenant B call, transcript, media, recording, or signed URL.
- [ ] Call history/detail show the correct caller, DID, campaign, recording, transcript, outcome, duration, billing state, and hold reason when applicable; compare UI values to database, PBX, and carrier evidence by durable call ID.
- [ ] Charged minutes, remaining balance, low-balance warnings, hold state, reversal, and currency/unknown-currency presentation reconcile to the authoritative ledger; no UI combines unlike currencies or invents a symbol.
- [ ] Retention/deletion/legal-hold behavior is approved by legal and verified.

### Media, transfer, and failure

- [ ] Two-way audio, DTMF, early media, caller-first speech, long turns, silence, and provider reconnect work.
- [ ] After-hours hangup rejects before answer.
- [ ] Inbound transfer remains disabled in campaign readiness, pre-answer admission, and the transfer API until a controlled linked-leg runtime exists. Enabling it requires: Talky-owned caller and destination channel IDs; an application-owned bridge for the full conversation; one pinned deadline that forcibly tears down both legs; durable parent/child leg linkage; reservation and final settlement covering every billable leg; idempotent terminal events under duplicate or reordered PBX events; no-answer/busy/timeout/cancel recovery; restart reconciliation without orphaned PSTN legs; and live carrier tests proving these behaviors.
- [ ] The current after-hours AI option is described and tested as **AI message intake**, not voicemail: the AI converses with the caller and stores the ordinary call transcript/outcome. Do not promise a beep, one-way message capture, message playback, or a voicemail inbox until those capabilities exist and have their own audit and failure proof.
- [ ] A true voicemail feature is enabled only after its live path, durable message artifact, authorized playback/inbox, retention, audit, and failure behavior are explicitly approved.
- [ ] After the controlled runtime exists, transfer allowlists, hop/attempt limits, audit, and failure actions are explicitly approved before enabling the transfer gates.
- [ ] Transfer to an unapproved destination is rejected; no arbitrary destination can be injected.
- [ ] STT, TTS, LLM, ARI, media gateway, database, Redis, and carrier failures end without dead air or leaked leases.
- [ ] Backend restart, Asterisk restart, and ownership handoff leave no zombie call or split-brain controller.
- [ ] Existing outbound call regression suite remains green.
- [ ] The real outbound baseline, each daily outbound smoke call, and the final frozen-candidate outbound call remain materially equivalent; record any expected difference and reviewer approval.

## Canary procedure

The dedicated DID is binary at the SIP edge: 0% or 100%. Cohort percentages refer to approved callers/DIDs, not probabilistic SIP sampling. Before using those percentages, freeze an ordered friendly-caller roster in the release manifest with a denominator of at least 20 that is divisible by 20, its SHA-256 digest, and the exact ingress/application allowlist mechanism. The 5%, 25%, and 50% stages must authorize exactly the first `N/20`, `N/4`, and `N/2` roster entries; prove selected callers are accepted and unselected callers are rejected before answer, and reconcile every attempt. If no independently reviewed allowlist can enforce that roster, percentage cohorts are unavailable: use explicitly sized controlled batches and do not describe them as traffic percentages or controller stages.

Before the first call, engineering, telephony, billing, and QA must approve the inbound test matrix and acceptance scorecard, including its rubric, numeric thresholds, blocking categories, latency targets, and allowable defect count. The source plan requires a scorecard but does not supply those numbers; blank or retroactively chosen thresholds are a NO-GO. The milestone batches below are disjoint and cannot be cross-counted. Reconcile **every** call in every 5 + 30 + 10 + 5 batch—no sampling—across carrier, OpenSIPS, Asterisk, backend, database, ledger, recordings/transcripts, and dashboard; every frontend or backend defect must cite its durable call ID (or the provider attempt ID when rejection correctly prevents a call row).

1. Start frozen and run all assertions against the populated environment file.
2. Complete five end-to-end staging calls. For each, prove recording, transcript, summary, outcome, and agreement among dashboard, backend, Asterisk, and carrier evidence; file every defect by durable call ID.
3. Freeze one canary candidate with synchronized routing, voice, billing, persistence, and dashboard evidence. Make no behavioral change during its controlled batch.
4. Complete at least 30 controlled inbound calls with at least five speakers spanning at least two accents. Include normal and noisy audio, after-hours and unknown-DID calls, interruptions, deliberately rejected transfer attempts, provider failures, concurrent calls, and the mandatory staging matrix above. Score every recording and transcript; no sampling is allowed.
5. Fix release blockers only, rerun the affected and complete regression suites, retest every failed canary scenario, and then complete ten additional confirmation calls with no regression in previously passing cases.
6. Freeze the backend commit, environment, telephony configuration, frontend commit/artifact, immutable image, and rollback version. Run final automation and then complete five separate release-candidate calls, proving every call used those exact frozen artifacts.
7. Obtain engineering, operations, security/privacy, billing, support, carrier, and legal/compliance sign-off.
8. Explicitly unfreeze; unfreeze still leaves ingress disabled.
9. Activate the dedicated DID at 100% only after the activation assertion succeeds.
10. Run the manifest-bound 5%, 25%, and 50% friendly-caller roster cohorts exactly as defined above, pausing for complete evidence review at each stage.
11. Complete the 300-call soak/load proof before general availability.
12. General availability requires a named incident commander, staffed observation window, and explicit change approval.

### Load-driver evidence contract

`backend/scripts/loadtest_calls.py` and `backend/scripts/soak_runner.sh` are
outbound loopback pressure tools, not inbound carrier-call generators. Their
HTTP worker pool is named `--request-workers` because it caps simultaneous
origination requests only; it is not evidence of live-call concurrency. The
retired `--concurrent` option must not be restored or reported as a call peak.

For the release soak, operations must approve and explicitly export
`REQUIRED_PEAK_LIVE_CALLS`. Run only in an isolated staging slice after proving
the Redis-backed cluster live count is zero:

```bash
export INTERNAL_SERVICE_TOKEN='<from the staging secret manager>'
export LOADTEST_TENANT_ID='<dedicated staging tenant UUID>'
export REQUIRED_PEAK_LIVE_CALLS='<approved cluster live-call peak>'
export SOAK_REQUEST_WORKERS=10  # HTTP pressure only; not call concurrency
bash backend/scripts/soak_runner.sh
```

The driver fails unless all of the following are true: at least 300 HTTP 200
responses contain `status=calling` and distinct non-empty call IDs; any queued
HTTP 202 response fails evidence because it has no call ID to reconcile and may
originate later; the status sampler observes the
approved peak in `capacity.global_current` (the Redis-backed cluster count);
every returned status says the adapter is healthy, running, and connected; the
initial live count is zero; no status sample returns an error; and the cluster
returns to zero for two consecutive samples before the drain timeout.
A single accepted request, 300 queued responses, HTTP-request concurrency, a
per-pod `active_sessions` value, an empty Prometheus result, or load-driver exit
zero without a matching `loadtest-evidence.json` all fail closed.

Preserve the generated evidence JSON, load log, periodic Prometheus snapshots,
and their hashes with the frozen manifest. The JSON includes every unique
originated provider call ID so SRE can reconcile all generated pressure calls;
it refuses to overwrite an existing artifact. The soak runner also refuses to
reuse a same-stamp result directory and atomically publishes each snapshot
without replacing an existing destination. Even a passing artifact proves
only the outbound loopback pressure/peak/drain contract. It does **not** prove
that 300 calls completed successfully, that billing/media/SLOs passed, or that
the required carrier-delivered inbound batch occurred. Reconcile completion,
terminal status, leaks, carrier/PBX records, database rows, and approved SLOs
separately, and complete the inbound 300-call evidence described below.

For the transfer-disabled candidate, the Day 10 SIP-signaling harness must be
invoked with both transfer and barge-in scenarios explicitly set to zero. The
defaults are also fail-safe at `100/0/0`, but production evidence must retain
the explicit values below. The live-media manifest is a separate mandatory gate
and must already have been produced from reviewed staging carrier calls against
the same frozen candidate:

```bash
export DAY10_PROFILE_TRANSFER_PERCENT=0
export DAY10_PROFILE_BASELINE_PERCENT=100
export DAY10_PROFILE_BARGEIN_PERCENT=0
export DAY10_REQUIRE_TRANSFER=0
export DAY10_REQUIRE_BARGEIN_REACTION=0
export DAY10_REQUIRE_EXTERNAL_LIVE_MEDIA_EVIDENCE=1
export DAY10_CANDIDATE_SHA="<full frozen candidate commit or image digest>"
export DAY10_EXTERNAL_LIVE_MEDIA_EVIDENCE_JSON="/secure/evidence/day10-live-media.json"
bash telephony/scripts/verify_day10_concurrency_soak.sh \
  telephony/deploy/docker/.env.telephony
```

The immutable external manifest must use this contract; the SIP harness copies
and hashes it into the Day 10 evidence directory and fails before starting
containers when any field is missing or the candidate digest does not match:

```json
{
  "schema_version": 1,
  "status": "passed",
  "environment": "staging",
  "candidate_sha": "<same full digest as DAY10_CANDIDATE_SHA>",
  "two_way_audio_pass": true,
  "barge_in_pass": true,
  "live_carrier_call_ids": ["<carrier call id>"],
  "evidence_references": ["<immutable trace/recording/review reference>"],
  "approved_by": ["<telephony or QA reviewer>" ]
}
```

The load generator opens only a SIP UDP socket. It advertises an RTP port but
does not bind that port, send RTP, receive RTP, inject speech, or measure an
interruption reaction. Consequently its barge-in report is always
`status: not_measured` with `pass: null`; any non-zero barge-in profile or
required barge-in reaction aborts before network work. Live two-way audio and
barge-in pass only through the external carrier-call evidence gate above.

This time-based harness can support SIP signaling concurrency, recovery, and
leak evidence. It does **not** by itself prove the required 300 correlated
carrier-delivered inbound calls, even if it produces 300 or more attempts. The
release evidence must separately identify exactly 300 successful or
expected-rejection inbound attempts and reconcile every attempt across the
carrier, SIP edge, Asterisk, backend, database/ledger, recording/transcript,
and dashboard. Successful-transfer evidence remains deferred. In the
transfer-disabled run, the verifier starts both ARI controllers with blind
transfer disabled and treats any `transfer_*` trace event, non-zero transfer
counter, or reported transfer attempt/outcome as a scope breach and immediate
stop.

During every canary pause and the post-release observation window, measure campaign creation failures, readiness/activation failures, tenant/Admin dashboard API and render errors, frontend unhandled exceptions, and stale/missing call-detail updates against pre-approved numeric thresholds. Correlate each breach to structured logs and the affected campaign/config/call IDs; any unowned or uncorrelatable breach pauses progression.

Example operator sequence from the repository root on the Linux deployment host:

```bash
sh telephony/scripts/assert_canary_ingress.sh all telephony/deploy/docker/.env.telephony
bash telephony/scripts/canary_freeze.sh unfreeze telephony/deploy/docker/.env.telephony
bash telephony/scripts/canary_stage_controller.sh set 100 \
  telephony/deploy/docker/.env.telephony \
  --reason "approved dedicated-DID canary activation" \
  --operator "$USER"
```

The controller must fetch and pass the authenticated metric gates; do not add
`--skip-gates`. Do not use `--force` as an approval mechanism; it cannot bypass
freeze or validation.

## Immediate rollback triggers

Rollback without waiting for a trend if any of these occurs:

- wrong tenant, campaign, agent, or DID route
- unknown DID reaches AI/media
- answered call lacks its durable row/reservation
- duplicate/missing/incorrect usage settlement
- recording begins without required disclosure/consent
- cross-tenant data or media exposure
- one-way/no audio, uncontrolled dead air, or repeated dropped calls
- unauthorized transfer or transfer loop/fraud signal
- Redis ownership cannot be proved or two ARI controllers appear
- sustained error/latency/resource threshold breach
- material outbound regression

Before canary, approve measurable RTO/RPO, the maximum drain deadline, and a trigger-by-trigger drain-versus-forced-hangup policy. Prove the previous application against the new forward-only schema, frontend rollback against both API versions, and restore of the isolated backup into a clean database. If any compatibility combination is unproven, rollback means code-forward or verified backup restore—not improvising an Alembic downgrade.

The current pre-inbound production SHA is also incompatible with the hardened
gateway control/authentication contract and is not a valid rollback artifact.
Freeze and prove a distinct rollback release that retains the mandatory
gateway/internal tokens, callback-host pin, commit-matched binary build, and
authenticated Asterisk client before forward deployment; otherwise rollback is
code-forward or verified restore while ingress remains disabled.

Rollback order:

1. Freeze and disable durable SIP ingress.
2. Turn the platform inbound control OFF.
3. Turn affected tenant controls OFF and pause assignments/campaigns.
4. Confirm OpenSIPS is disabled, 0%, and frozen; confirm no new inbound call is answered.
5. Drain or explicitly end admitted calls according to the incident decision.
6. Reconcile every in-flight reservation, lease, call row, recording, and provider call ID.
7. Preserve logs/evidence and open the incident record before any re-enable.
8. From outside the cluster, place a controlled attempt and prove ingress is rejected before answer. Verify the database/config version, no new reservation or AI/media session, reconciliation totals, alert delivery, and one normal outbound smoke call within the approved RTO/RPO.

Database rollback note: migrations before `0028` may use their reviewed downgrade paths. `0028` and later must be restored by deploying compatible code forward or restoring the verified pre-change backup; the `0028` downgrade intentionally raises while holding the migration lock so the unsafe non-monotonic settlement RPC cannot be recreated.

Repository rollback command:

```bash
bash telephony/scripts/canary_rollback.sh full telephony/deploy/docker/.env.telephony
```

## Final sign-off record

Production remains NO-GO until all fields are complete.

| Gate | Required approver | Evidence | Status |
|---|---|---|---|
| Migration and restore drill | Database owner | staging run + rollback/restore proof | Pending |
| Carrier signaling/media | Telephony owner + carrier | SIP/RTP traces and native parser output | Pending |
| Security and tenant isolation | Security owner | adversarial test report | Pending |
| Recording/privacy/retention | Legal/privacy owner | jurisdiction and policy approval | Pending |
| Billing and quota | Billing owner | ledger/reconciliation report | Pending |
| Transfer scope deferral | Product owner + telephony owner | signed acceptance that successful transfer scenarios from the source plan are excluded and all transfer gates remain disabled | Pending |
| Missing original outbound baseline | Change approver + QA + telephony owner | signed deviation/waiver, last-known-good/historical evidence comparison, and candidate/daily/frozen outbound smoke evidence | Pending |
| Load and resilience | SRE/operations | disjoint 5 + 30 + 10 + 5 call batches, 300-call soak, thresholds, alerts, failure drills | Pending |
| Support readiness | Support owner | runbook, alert routing, escalation roster | Pending |
| Canary authorization | Change approver | signed change record | Pending |

No blank, verbal-only, or assumed approval counts as a passed gate.
