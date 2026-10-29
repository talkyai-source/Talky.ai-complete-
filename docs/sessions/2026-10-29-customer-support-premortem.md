# Customer support pre-mortem and repairs — 29 October 2026

The candidate integrates the previously qualified Ubuntu release with current
main-branch public-site changes, then repairs reproduced support failures.
Work is isolated on `codex/customer-support-premortem-20261029`; the user's
dirty root checkout is preserved. This record does not certify paid readiness
or say that candidate code is already deployed.

## Scope and approach

The [coverage matrix](../production-readiness/customer-support-premortem-2026-10-29.md)
accounts for all 45 features, 22 scenarios and 15 gates in the frozen plan.
It covers caller-facing AI support, account recovery, enquiries, purchases,
invoices/cancellation, notifications, privacy, operator diagnosis, database
recovery and release. Original end-to-end gate statuses remain unchanged.
User-approved exclusions for reseller operations and staff invitations remain.

Separate customer-journey, AI/security and database reviews reproduced failures
before repairs. Existing authoritative receipts, tenant scopes and identity
fences are reused. No new provider, helpdesk system, scripted dialogue policy
or broad architectural rewrite is introduced.

## Reproduced failures and repairs

| Failure | Change | Verification boundary |
|---|---|---|
| Contact submission still dispatched when its retry reference could not be saved. | Save the reference before dispatch; retain editable unsent draft and existing email fallback on failure. | Two red reproductions, then 13 component checks passed. Real mailbox operation remains unaccepted. |
| Login advertised recovery codes but stripped non-digits and always submitted TOTP. | Expose the existing single-use recovery-code API with an accessible mode switch; retain challenge/account ownership and uncertain-outcome guards. | Three red recovery-mode checks; 19 checks passed, including previous ownership cases. |
| Password-reset rate counter could permanently lose its expiry. | Atomic Redis increment/expiry, bounded repair of legacy counters and refusal of unconfirmed send claims; generic response retained. | Initial 2 failed/2 passed; final four focused checks plus broader recovery selection: 42 passed. No email sent. |
| Checkout and billing errors discarded the captured API reference. | Render a validated UUID support reference without exposing raw error details; preserve purchase recovery state. | 59 billing component checks passed, including the new rendered failure case. |
| Malformed model actions and invalid knowledge arguments reached logs as raw content. | Emit structural diagnostics and validated scoped section references only. | Actual-import reproductions showed private text before the fix; focused privacy/knowledge selection: 34 passed. |
| Follow-up memory silently cut source text at 2,500 characters, potentially dropping exclusions. | Keep complete short sources; retain scoped navigation for oversized sources and require rereading; invalidate memory when catalog or tenant/campaign scope changes. | Regression places qualifying conditions after the old cutoff and tests scope/revision replacement. Model comprehension still needs exact-profile telephone evaluation. |
| A fresh canonical bootstrap omitted four tables and the selected-telephony column used by active APIs. | Add guarded migration 0083 and update the canonical least-privilege profile; preserve existing supported schema/data and refuse drift. | 16 migration tests, one preserved-schema test and 11 grants checks passed locally; actual TLS/release CI remains pending. No production migration performed. |
| Emergency approval accepted and stored unverified codes; approvals and session issuance could race. | Fresh authenticated platform-role/TOTP proof, bounded account attempts, replay protection and request-row serialization; legacy approvals do not become verified proof. Durable request events are separate from buffered audit submission. | 44 security tests, one real PostgreSQL concurrency test and seven independent real-Redis checks passed. No emergency token consumer or delivered alert is inferred. |

The 198 broader AI boundary/replay checks passed separately. They include the
captured Q43/H13 model outputs through the full traditional turn finisher,
action authorization/confirmation and report-issue receipt boundaries. They
do not establish new model quality: **Q43's unsupported handoff promise remains
a failure in the retained actual provider evaluation**. No new provider calls
or real customer calls were made for these local checks.

## Database and Ubuntu qualification

The [database pre-mortem](../production-readiness/customer-support-database-premortem-20261029.md)
and [sanitized catalog receipt](../production-readiness/customer-support-catalog-20261029.json)
separate the four active missing contracts from six additional historical
production tables. The production observation was schema-only and read-only;
application rows, credential values and counts were not exported.

The Ubuntu update rehearsal now obtains the exact pinned older release for
database bootstrap and its grants, then runs the real candidate forward
migration. It compares every pre-existing business column and row across the
upgrade, checks the candidate schema head, reboot and runtime identity, then
retains the existing automatic rollback and migration-started refusal drills.
This prevents an already-upgraded fixture from falsely qualifying migration.
Four local TLS-fixture boundary tests passed and harness files compiled;
actual revised Ubuntu acceptance is still pending in this record. Independent review corrected the negative-probe migration flag, future synthetic backup-role SELECT access and CI test-module dependencies before the complete rehearsal. The preparation guide now explicitly requires actual backup access after a forward migration.

The explicit preserved-inventory profile keeps all 123 reviewed production tables while denying runtime/PUBLIC access to the six historical objects and sequence. Other reviewed operator/backup grants are retained. This does not change actual production ownership, privileges or TLS.

See the [AI/security owner record](2026-10-29-support-ai-emergency-premortem.md), [customer journey evidence](artifacts/2026-10-29-customer-journeys.json), and [real Redis evidence](artifacts/2026-10-29-emergency-redis-budget.json). The local database unittest discovery ran 49 checks with two platform-dependent skips; these are not the real PostgreSQL acceptance tests.

## Security audit follow-up (30 October)

The first candidate, `523db18b2a7558e03e7470866b9e329f108bd9ef`, passed
canonical database TLS recovery, operational monitoring, frontend tests/build
and Admin tests/build, but failed dependency auditing. The affected pinned
LangGraph SDK was reported as CVE-2026-118667
([maintainer advisory](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-3fx2-cqw9-xr3c)).
The failure was retained; the audit was not waived.

The replacement pins are LangGraph 1.2.14, SDK 0.4.6 and langchain-core 1.4.7.
The Linux/Python 3.12 hash lock and its provenance were regenerated with the
recorded resolver command. Exactly three of 157 resolved packages changed;
154 remained unchanged. Independent metadata review checked active Linux
dependency constraints and published package hashes. The local hash-locked
audit with pip-audit 2.10.1 found no known vulnerabilities, and all 15 installer
preparation/provenance checks passed. These results do not replace clean
runtime compatibility tests or exact-candidate Ubuntu acceptance.

Retained receipts: [package metadata and lock review](artifacts/customer-support-20261029/langgraph-patch-metadata.json),
[local audited dependency inventory](artifacts/customer-support-20261029/patched-runtime-lock-audit.json),
and [first-candidate TLS manifest](artifacts/customer-support-20261029/first-candidate-tls-manifest.json).
The manifest hashes identify original downloaded CI artifacts, before any
Git text newline conversion; the linked run retains those full artifacts.

The earlier TLS workflow, run 40937477959, retained 18 native PostgreSQL
acceptance checks with no failures or skips, canonical 117-table recovery,
and canonical/preserved-inventory restricted-role verification. It used
synthetic databases on separate TLS containers on one runner. It does not
qualify off-host production disaster recovery or the patched candidate.

The same candidate's full backend suite reported 15,737 passed, 288 skipped
and three failed tests. One confidentiality assertion accidentally searched
generated timestamp digits as if they were user content. Two recovery tests
used the former non-atomic Redis fake, which correctly caused the new limiter
to refuse the send. The fixtures were corrected without weakening runtime
privacy or fail-closed behavior; the complete corrected suite must pass on
the replacement candidate. The fast voice suite passed separately.
The corrected secret-boundary, reset-truth and atomic send-limit modules
passed all 96 focused checks locally before resubmission.

## Operating acceptance still required

The source repairs do not supply secure host administration, a designated
separate production database, an off-host restore destination, owned calling
routes/destinations, connector accounts, a functioning support mailbox, or a
named on-call recipient. Exact-release deployment, delivered alerts,
human-rated telephone behavior, sustained capacity and the complete paid
lifecycle remain required. The existing request for secure infrastructure
references remains unanswered; passwords and keys must not be pasted into
support reports.

Emergency access remains an incomplete operating journey until its actual
consumer, durable audit/alert path and approved operator procedure are
qualified. A customer with neither authenticator nor recovery code still
needs an approved identity-verification process; the UI repair is not an
authentication bypass.

Final Stripe account integration is intended to be the last handover step.
It is not the only remaining acceptance item. The freeze stays active.

Public source also still advertised the excluded reseller program. The FAQ, feature card and service-availability wording now match the approved first-release scope; branding preview remains. This is availability correction, not acceptance of future reseller operations.

## Why this approach helps later scaling

Durable idempotency references and database row locks make retries and
simultaneous support actions predictable across processes. Explicit schema
contracts and least-privilege profiles make new installations and upgrades
repeatable. Scoped knowledge snapshots prevent stale or foreign context from
being reused. Correlation references connect customer reports to API evidence
without copying private diagnostic payloads into the UI.

These improvements do not prove horizontal call ownership, HA or capacity.
Process-local voice state, per-process connection pools and coordinated
migration ownership remain scaling constraints that must be measured and
addressed within the recorded plan.

## Current public-site observation

At 18:32 UTC on 29 October, the Vercel production alias was READY on main source `0ef59596e9f6e46aea4050fc99e6ff202c6f9da3`. This is the pre-repair deployment, not this candidate. Native workstation HTTP checks failed DNS resolution. Independent Google/Cloudflare DoH and TCP queries to both authoritative nameservers resolved the domain, while the local/network UDP path returned SERVFAIL. Using that verified address with the normal hostname and certificate validation, all nine known anonymous homepage/login/branding/protected-route checks passed at 18:39 UTC. No certificate check was bypassed and no DNS record was changed. This diagnoses a local/network resolution-path failure, not a proven global deployment outage.
