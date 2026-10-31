# Exact dependency integration verification — 5 October 2026

The combined local candidate passed the full backend unit/security suite and the
35-module migrated database matrix. Source candidate for both runs:
`44dcc2367446cbe85c9c2757838b8695cf15bf1f`. Both manifests confirm unchanged
application, test/fixture, migration, configuration and CI sources during execution.
Nothing was pushed, deployed or tested against customer/provider accounts.

| Verification | Observed result | Boundary |
| --- | --- | --- |
| Backend unit and security suite | **11,775 passed, zero failed, 19 skipped**, 3,902 warnings; 837.27 seconds; combined statement/branch coverage **67.47%** | Local Windows with test credentials, test-only Lua dependency and unavailable unit-test DB. Not Linux, real Redis or live voice acceptance. |
| Migrated PostgreSQL matrix | **435 passed, zero failed, zero skipped**, 77 warnings; 452.45 seconds across **35 modules** | Disposable public migration head 0061, plus explicitly isolated CRM/action schemas. No production database. |

The [unit command and source snapshot](artifacts/backend-complete/exact-command.json),
[unit output](artifacts/backend-complete/exact.txt),
[database command and source snapshot](artifacts/backend-complete/postgres-final-command.json)
and [database output](artifacts/backend-complete/postgres-final.txt) retain exact
commands and results. Counts overlap focused verification and must not be added
as independent customer scenarios. Earlier failed results in the
[prior checkpoint](2026-10-05-repository-regression-checkpoint.md) remain unchanged.

Both runs used the isolated dependency target satisfying all **62 active directly
declared production requirements**, including the already-pinned urllib3 2.8.0
and PyJWT 2.15.1. The shared original virtual environment was not modified.
[Unit parity](artifacts/backend-complete/exact-dependency-parity.json) and
[database parity](artifacts/backend-complete/postgres-final-dependency-parity.json)
are marker-aware installed checks; they do not establish a fresh complete
environment or an installed transitive vulnerability audit.

The 19 unit skips remain visible: two optional librosa checks, eight separately
opted-in cooldown PostgreSQL checks, one non-offered Groq model, four POSIX
recording-mode checks, one other POSIX mode check, one ffmpeg check and two
AF_UNIX checks. The 435 database passes do not replace those eight cooldown checks.
The obsolete authentication allowlist skip was removed and its maintenance
assertion now runs. No skip is counted as a pass.

## Repairs included in the verified source

- [DNC finalization and bulk suppression](2026-10-05-op07-dnc-settlement.md)
  preserve durable opt-out status during normal call settlement and suppress
  retry intent without falsifying the actual call outcome. Non-dialable inbound
  and browser-test recovery retain their bounded compatibility path. Caller
  opt-out duplicates and bulk counts reflect active suppression.
- [Native DNC replay acknowledgement](2026-10-05-ag04-native-receipt-fixture.md)
  supplies an explicit synthetic persistence receipt for positive fixtures and
  verifies withheld speech for false/unknown receipts. It does not relax the
  application speech gate or claim database durability from replay.
- [Authentication audit cleanup](2026-10-05-cp08-auth-audit-cleanup.md) removes
  obsolete protected-route exemptions without granting new roles or permissions.
- [Database regression repairs](2026-10-05-postgres-matrix-regressions.md) scope
  append-only assertions to owned fixtures and verify current rollback guards,
  data preservation and Alembic ancestry without weakening audit protection.

The previously recorded whole dashboard result remains **774 passed, zero failed,
two legacy database skips** at `d058cd23`; the later Security copy-only correction
passed scoped ESLint. The backend quality result at `5d091528` remains a passing
Ruff F gate, zero high-severity Bandit findings and no known vulnerabilities in
the requirements audit. Neither is relabelled as a new run on this candidate.

## Subsequent bounded replay extension and remaining work

After these runs finished, test-only source `7bc0023e` was integrated as
`1c7dce70`. Its [contact and historical DNC report](2026-10-05-ag04-contact-and-historical-dnc-controls.md)
records 97 focused passes and 190 offline replay rows with 1,296 passing runtime
controls. The matrix union reaches **39/50 proposed conditions**. The preserved
Groq semantic failure still fails; 189 other semantic findings remain unreviewed.
No application, prompt, threshold or human approval changed. These later fixtures
were not present in the whole-suite source above.

There are still **27 eligible unfinished packages**, AG03's separate
`candidate_verified` package, and **three deferred packages** (CP05, CP06, CP09).
All 15 final gates and 22 scenario gates remain `not_run`. Current work includes
truthful Admin action receipts, remaining conversation qualification, reviewed
architecture dispositions and designated live/provider/operational acceptance.
The feature freeze remains; local green checks alone do not close the plan.

## Later bounded verification

The [eight opt-in cooldown SQL tests](2026-10-05-cooldown-query-postgres-verification.md)
passed unchanged in a restricted private schema. The original unit run still has
19 skips; this separate run supplies evidence for eight of them without changing
that history. The clone checks do not establish canonical trigger/RLS/FK parity.
Public rows, ACLs, RLS flags and migration head remained unchanged.

The full dashboard `npm run lint` command passed at `33360ddf` with **zero errors
and three warnings**. Earlier commit `fec31d4b` already excluded ignored local
scratch probes; the historical AG01 missing-plugin result is retained. No new
lint configuration, rule suppression or application edit was needed. Warnings
remain for preview-ref cleanup, an unused import and contact-draft hydration.
[Command/environment](artifacts/frontend-lint-scope/verification.json) and
[output](artifacts/frontend-lint-scope/recheck.txt) record this local recheck.

The [Admin receipt repair](2026-10-05-ag06-admin-receipts.md) was integrated later
as `0cda5660`; its **75 backend and 34 Admin checks** passed again at `b0cb7689`.
These overlap owner checks and are not part of the earlier whole-backend count.
Unknown outcomes, original account evidence and selection ownership are now
displayed honestly; supported operator resolution and external acceptance remain
open. The [architecture disposition](2026-10-05-ag07-architecture-dispositions.md)
closes only the proved-unused A05 duplication finding, with independent review;
AG07 and every package/gate status remain unchanged.

## Fresh remote-main comparison

A noninteractive read of remote `main` on 5 October 2026 found revision
`0a64828c23a17a8ab84c566d9739487606f26b64`, unchanged from the stored remote ref.
At candidate `a07d9163`, that revision is the merge base: **111 candidate-only
commits and zero main-only commits**, with no new upstream files to reconcile.
The local branch, candidate and tracked working tree were unchanged by the fetch.
[Exact metadata](artifacts/remote-baseline/read-only-main.json) records the check.
This establishes source ancestry at that time, not the deployed revision, remote
CI success or release approval. No push, merge or deployment occurred.

## Rejected retrieval experiments

The [AG02 investigation](artifacts/ag02-normalization/investigation.md) preserves
two local normalization proposals and their counterexamples. Both were rejected:
the original proposal admitted unrelated word stems, and the narrower plural
proposal still admitted passages missing the question's critical noun. Its
unchanged gold experiment improved sufficient evidence from 32/45 to 34/45,
still below the existing 85% target. Those are prototype results only; the
application remains at the recorded 32/45 pinned and 31/45 SQL baseline.

Evidence-only commit `5ee81294` was integrated as `f0e1bc30`. No application,
dependency, prompt, gold fixture or threshold changed. Deliberately failing
experimental controls remain reproducible in the artifact directory and are
not part of the normal unit suite. A content-owner-reviewed campaign corpus
and qualification of existing ingestion/enrichment remain outstanding; neither
enrichment nor local normalization is claimed to close the quality gap.

## Selected-session and reload integration

At `be44bf72`, **1,025 checks passed, zero failed, one POSIX permission check
skipped**, with 1,417 warnings in 31.35 seconds across 38 related modules. The
source snapshot stayed unchanged. [Command/source](artifacts/lifecycle-reload-integration/integrated-command.json)
and [output](artifacts/lifecycle-reload-integration/integrated-tests.txt) preserve
the exact inventory. This covers the owner batches, direct lifecycle callers,
strict profile admission, adjacent contact/replay and Admin receipt regressions.
It is a later scoped run, not a repeat or relabelling of the 11,775-test full run.

- [Selected outbound warmups](2026-10-05-selected-outbound-warmup-lifecycle.md),
  source `ccdbabf1` integrated as `1f094117`, retain the selected session during
  live/unknown long ringing. Lost owned selection and duplicate/terminal/ringing
  races cannot silently replace it with defaults. Unknown provider recovery and
  separately enabled legacy cloud ownership remain open.
- [PJSIP reload receipts](2026-10-05-pjsip-reload-receipts.md), source `28833884`
  integrated as `4781b0ef`, require each command's acknowledgement and retain
  child ownership through timeout/cancellation cleanup. Command acknowledgement
  does not establish runtime trunk health or atomic file/database isolation.
- [Accepted email and phone corrections](artifacts/ag04-accepted-phone/verification.md),
  test-only source `4878fe83` integrated as `20f59135`, use actual runtime guards
  with synthetic receipts. The common replay reports 206 rows and 1,580 passing
  controls, but preserves one captured Groq semantic failure and 205 unreviewed
  findings. The canonical union is **41/50**; nine conditions and per-engine
  gaps remain. No human/audio/provider approval is inferred.

All three changes were independently read-reviewed. No migration, live PBX,
provider, push or deployment occurred. Package/final/scenario statuses remain
unchanged; CP05, CP06 and CP09 remain deferred.

## Guard, cloud and parity integration

At `add699c4`, **1,296 checks passed, zero failed, one POSIX permission check
skipped**, with 1,535 warnings in 33.99 seconds across 47 related modules. The
source snapshot stayed unchanged. [Command/source](artifacts/guard-cloud-integration/integrated-command.json)
and [output](artifacts/guard-cloud-integration/integrated-tests.txt) preserve the
inventory. This later scoped run includes the prior lifecycle/profile/PJSIP
controls plus cloud containment, passive email failure and native parity checks.
It overlaps prior results and is not a new whole-backend or live acceptance run.

- [Passive email failure speech](artifacts/ag04-passive-email-failure/verification.md),
  source `5a6fa48e` integrated as `b45a4c05`, permits the truthful passive failure
  response after a definitive failed action while retaining unsupported-success
  protection. Its 224 owner checks passed. Separate unknown/in-progress receipt
  speech still needs repair: a timeout is not proof of failure or permission to
  resend. That subsequent investigation is not included in this result.
- [Native parity controls](artifacts/ag04-native-parity/verification.md), test-only
  source `9516c2fe` integrated as `a55cc100`, add action failure, contact ownership
  and incomplete-phone cases through actual runtime guards. The exact-source
  common replay has **216 rows, 1,942 passing controls, one preserved Groq semantic
  failure and 215 unreviewed findings**. Its expected exit code remains 1 and
  production approval remains false. The canonical union stays **41/50**.
- [Legacy cloud containment](2026-10-05-legacy-cloud-production-boundary.md), source
  `1bf06a28` integrated as `6702df74`, blocks the unqualified Twilio/Vonage callback
  and media paths in production even when their legacy flags are enabled.
  Explicit nonproduction qualification remains available. Its 241 owner checks
  passed. Existing enabled sessions must drain before rollout; this is not a
  hot-toggle cleanup guarantee or a completed cloud campaign integration.
  The surfaced cloud activation/routing promise and saved-selection admission
  mismatch are a separate follow-up under investigation.
- [OP11 permission boundaries](2026-10-05-op11-permission-boundaries.md), integrated
  as `134f0529`, inventory the existing systemd, container, recording and Asterisk
  CLI boundaries. Documentation and a module comment changed; executable code
  did not. No host permissions were exercised or changed. Approved runtime
  identity/topology and positive/negative deployed checks remain outstanding.

All four batches received independent review. No package, final gate or scenario
gate was closed. The 27 eligible unfinished packages, separate AG03 candidate and
three deferred packages remain. No provider call, push or deployment occurred.

## Local audio creation follow-up

Source `4bb26dab` was integrated as `e3f961e7`, with its
[verification report](2026-10-05-feedback-local-storage-privacy.md). New local
feedback directories/files request 0700/0600, preserving exclusive creation and
existing modes/umask. Both feedback and recording writers now fail instead of
looping when their missing storage root cannot be ascended. The ten-module owner
run passed **129 checks, zero failed, nine skipped** (eight POSIX checks and one
FFmpeg check), with 66 warnings in 15.23 seconds. The final source hashes match
the committed source; two reviewers examined the bounded change. This is separate
from the earlier 1,296-check run, not part of that source snapshot or a new full
backend result. No deployed permissions, persistence or live acceptance is
inferred. OP07, OP11 and all final/scenario gate statuses remain unchanged.

## Unresolved-action speech and common runtime controls

The [shared speech repair](artifacts/ag04-unresolved-action-speech/verification.md)
checks existing unknown/in-progress receipts before admitting recognized false
failure or action-specific resend language. It retains honest uncertainty and
the existing repair/fallback/executor policies. Initial source `7e6a96bf` requires
follow-up `39b95480`; both were integrated, ending at source `4c006e6c`. Root found
four positive-claim regressions in the first draft, and a nearest-match prototype
was also rejected. Their evidence remains. Final positive matching preserves the
original predicate spans; this is a bounded language guard, not general NLP proof.

The final four-module guard batch passed **236 checks, zero skipped**. Independent
review passed nineteen actual-method probes. Root also repeated the four positive
regressions across absent/unknown/failed receipts on integrated `b8310ae7`:
[all twelve were blocked correctly](artifacts/ag04-unresolved-action-speech/root-integrated-predicate-check.json).
These overlapping controls are not additive whole-suite coverage.

Test-only source `3b00bc68` was integrated as `b060521d`; its
[final common replay](artifacts/ag04-unknown-common/verification.md) is bound to
the corrected owner source `39b95480`. It passed 180 focused checks with zero
skips. The CLI completed **228 rows and 2,146 runtime controls**, with zero control
failures, one retained Groq semantic failure and 227 unreviewed findings. Exit 1
and `production_approved=false` remain correct. The canonical union is now
**42/50**, adding only the existing unresolved-action condition. Eight conditions
remain unmapped: background speech, echo reentry, grounded nonprice answer, hold
music, sensitive data offer, silence without request, unclear name and unrelated
request. Per-engine gaps and human ratification also remain.

The six adapters use actual runtime guards with synthetic receipts/executor ports.
One executor invocation in the authored sequence does not prove real timeout
reconciliation, durable no-resend behavior, model comprehension or heard speech.
No provider, telephone or database effect was performed. Package/final/scenario
statuses and the three deferred packages remain unchanged.

## Combined selection, action and audio-storage verification

At `bbbb9450`, **1,714 checks passed, zero failed, ten skipped**, with 2,111
warnings in 37.14 seconds across 66 related backend modules. Source hashes stayed
unchanged. [Command/source inventory](artifacts/selection-guard-storage-integration/integrated-command.json)
and [output](artifacts/selection-guard-storage-integration/integrated-tests.txt)
record the combined selected-profile lifecycle, PJSIP acknowledgement, cloud
selection/retry, action speech/common replay and local audio-storage boundaries.
Nine skips require POSIX file modes; one requires unavailable FFmpeg. This is a
new affected-path run, not a new full-backend, PostgreSQL or deployed acceptance
run. Previous counts overlap and are not added to this result.

[Cloud availability](2026-10-05-cloud-telephony-availability.md), source `8f5d7731`
integrated as `bbbb9450`, fixes the actual silent switch from saved cloud selection
to SIP. Production activation is unavailable; existing pointers/credentials stay
visible and block new work until explicitly corrected. Terminal/provider-ID
receipt replay remains authoritative. A temporary selection lookup failure
preserves the same job/attempt; a known unsupported selection has no automatic
retry in either existing retry mode, and absence proof is still required before
settlement. Explicit nonproduction qualification remains separate from cloud
campaign support.

Settings now distinguishes saved credentials, provider-account/local configuration
checks and calling availability. Missing availability cannot enable activation;
legitimate nullable timeout status remains readable. The owner verified **16
rendered frontend checks**, scoped ESLint and TypeScript, alongside 202+66 backend
checks. Integrated frontend files match that owner source exactly; this records
source identity, not another frontend run or a Next build. Backend, frontend/API
and root reviews found no further material issue in the bounded final change.

All changes remain local. No provider request, real call, migration, push or
deployment occurred. There are still **27 eligible unfinished packages**, the
separate AG03 candidate and three deferred packages. All 15 final gates and 22
scenario gates remain not run. The next inspections concern existing held-action
and ambiguous-call recovery; live/operator prerequisites are still outstanding.

## Saved-provider termination proof and inbox health investigation

Source `c9d3f87e`, integrated as `e5e0bc29`, prevents an Asterisk absence response
from finalizing a durable call owned by another provider. Admin, tenant, raw,
campaign-batch and transfer-fallback paths supply the saved concrete provider and
every selected active linked leg's provider before any adapter request. Conflicting
duplicate IDs remain visible to validation. Recovery validates persisted linked
legs before deduplicating them. Existing adapter-owned shutdown and compensation
remain available.

The [owner verification](artifacts/provider-proof-fence/verification.md) records
**278 passing tests, zero failures and zero skips** in two disjoint focused runs.
CI Ruff, strict F checks on the new module and whitespace checks passed. Root and
RT reviewed the source, which was cherry-picked without conflicts; this is not
an additional root test run. The stricter exploratory lint findings are preserved
and were not hidden by changing lint policy.

This proves the bounded provider-family fence, not original PBX host/account
identity or full recovery. The separate inbound lease-loss fallback still needs
investigation when durable linked-leg context cannot be loaded. Unknown-provider
and ambiguous-owner cases stay held.

An [inbox health-write design experiment](2026-10-05-inbox-health-lock-design.md)
passed seven isolated PostgreSQL concurrency cases with public rows/catalog state
unchanged and all private objects removed. It supports one atomic parent/account
write guarded by the failed authorization's generation. It is not application
implementation proof. The earlier inbox retry draft was withheld when independent
review found that canonical Gmail connections have no external subject ID; its
compatibility correction is being verified against the actual OAuth/resolver path.

Changes and evidence remain locally committed. No package, final gate, scenario,
deferred item, provider acceptance or deployment status was promoted.

## Inbox authorization-row compatibility

The final [inbox follow-up](artifacts/ag06-inbox-account/verification-row-pin.md),
source `0f69c0f7` integrated as `e8dee2c2`, uses the existing authorization row to
keep a read's one refresh with its original account. The actual Gmail OAuth path
continues to work with its null external subject ID. Missing/replaced/inactive
authorization cannot fall through to another mailbox. Revocation during refresh
is refused by the active-status token-write acknowledgement before a second read.
Existing reviewed-effect external identity rules are unchanged.

The owner ran **220 tests with zero failures or skips** across twelve related
modules, with 13 existing warnings. Ruff F and whitespace checks passed. Root and
RT reviewed the final source. All four integrated source/test hashes match the
owner's corrected UTF-8/LF Git fingerprints; this is source verification, not
another test run. The original manifest used Windows CP1252 decoding before
UTF-8 hashing for two non-ASCII application files. The separately preserved
[correction](artifacts/ag06-inbox-account/row-pin-hash-correction.md) reproduces
that encoding error from the exact committed source; the source itself matches.
The earlier external-ID-required draft and its 102-case result remain historical
and must not be treated as the accepted standalone change.

The separate atomic health-write correction is still being implemented. This
read pin neither resolves held external actions nor binds a later independent
message lookup to an original delivery receipt. No provider, customer mailbox,
PostgreSQL application operation, deployment or push occurred in this patch.
Package and release-gate states remain unchanged.

## Atomic inbox health follow-up (6 October local date)

Source `7babac65`, integrated as `d43deab8`, replaces broad, separate expiry writes
with an atomic check of the rejected authorization row and credential generation.
The [full verification report](2026-10-06-inbox-atomic-health-verification.md)
records **241 owner tests and 13 separate actual-helper PostgreSQL checks**, all
passing. Application hashes match the frozen PG-tested source. Parent/all-account
locks, fresh active-account selection and both status acknowledgements share one
transaction; cancellation and timeout retain honest uncertainty.

The PG checks used private table copies and a restricted role, observed real lock
waits, and verified rollback/pool reuse. Public connector/account data and schema
metadata stayed unchanged; private objects were removed. This is not a new full
backend, production RLS or provider run. Original-action-account receipt inspection
and operator resolution remain open; all package/final/scenario states remain as
previously recorded.


## Inbound inventory and grounded-answer integration (6 October)

Inbound lease-loss source `7e1b31fc`, integrated as `18b9781d`, requires verified
durable leg inventory and all-leg absence before logical finalization. A database
outage still permits the known same-provider parent's hangup request, but neither
parent absence nor its synchronous callback settles the call. The existing
recovery guard is installed before the first await and retained for retry.
Generation tokens prevent an old expiry timer, failed finalizer or late completion
from removing or completing a newer local owner.

The [verification](artifacts/inbound-lease-inventory/verification.md) records
**304 passing tests, zero failures and zero skips** across sixteen modules,
including nineteen new controls and preserved before-fix failures. Root and RT
read the final source; all four integrated source/test paths and sixteen regression
inputs match the recorded canonical LF fingerprints and source commit. This is
source verification, not another test run. Only the separately verified inbox
application files differ from that owner's application tree. Actual PBX host,
account identity, cross-process fencing and transport-ambiguous commands remain
outside this local proof.

AG04 test-only source `daf092b4`, integrated as `b7c1c6a0`, adds a useful non-price
source answer and an unavailable-link negative across the six existing profiles.
The actual knowledge data block, source identities/versions, submitted answer and
assistant history are checked. Root and LLM reviewed the six-file change, which
matches the owner's committed source. The [owner report](2026-10-06-ag04-grounded-answer-controls.md)
records **153 passing tests** and one common CLI run on that exact source:
**240 rows, 2,278 passing runtime controls, zero evidence errors and no network
attempts**. The compressed replay's uncompressed digest was independently checked.
The CLI still exits 1: the captured Groq semantic failure remains failed, and 239
other semantic findings remain unreviewed.

Twelve added profile rows represent one additional canonical condition. Aggregate
offline mapping is now **43/50**, with seven conditions unmapped; this is neither
per-profile completeness nor model/human/live acceptance. Arbitrary unsupported
factual embellishment and the existing AG02 retrieval quality failure remain open.

There remain 27 eligible unfinished packages, AG03 separately candidate-verified,
and three deferred packages. All fifteen final gates and twenty-two scenario
gates retain their previous not-run states. No deferred work, live call, provider
request, deployment, push or production approval occurred in these integrations.


## Native caller activity integration (6 October)

Source `12175fbd`, integrated as `1c3a2ec7`, fixes an accepted Realtime caller
turn leaving the real session activity timestamp unchanged. Seven bridge lines
call the existing activity method after admission of a new, nonempty current
caller final, before awaited processing. Corrections, historical or duplicate
transcripts, raw audio, VAD alone and the agent's own output do not renew activity.
No inactivity or absolute/soft-duration limits changed.

The [verification](artifacts/native-activity/verification.md) records **270
passing tests, zero failures and zero skips** across nine modules, including
32 actual-parser controls with a real CallSession and the actual expiry classifier.
The timestamp is seeded to isolate the existing 300-second inactivity rule; this
is not an elapsed telephone call or actual watchdog hangup. The hard-duration
positive control still expires a call despite a fresh admitted caller turn.
Root and RT reviewed the final source; both owned source files and all nine
regression inputs match the owner's committed source and canonical hashes.
This is source integration verification, not a second root execution.

The [remaining-condition inventory](artifacts/native-activity/unmapped-inventory.md)
distinguishes existing deterministic controls from absent acoustic attribution and
required provider/human acceptance. Aggregate mapping stays 43/50 and all seven
unmapped conditions remain open. All package, scenario, final-gate and deferred
statuses remain unchanged. No provider, real call, database, push or deployment
was involved in this repair.


## Reviewed email/calendar authorization integration (6 October)

Source `ac104e2f`, integrated as `5d8ddba3`, repairs canonical Gmail, Google
Calendar and Outlook Calendar connections being rejected because their actual
OAuth callbacks can save no external provider account ID. New reviewed actions
carry an explicit versioned tenant/connector/provider/authorization-row proof;
a genuine external identity, when present, remains an additional constraint.
Creation-time selection, same-row refresh and final current-account admission
prevent an older refreshed authorization from silently replacing the reviewed
connection. Ordinary reads and standalone availability retain their contracts.

Email acknowledges a pending-to-running claim with the original proof before
sending. Cleanup compares the phase this invocation acknowledged. Calendar
finalization compares running status. A competing cancellation/completion is
preserved; uncertainty after dispatch remains unknown rather than successful.
Legacy records without original account proof are not rebound to a new account.

The [owner verification](artifacts/reviewed-account-identity/verification.md)
preserves **396 passing affected tests, zero failures and zero skips**, including
97 new canonical controls. The original owner reported fourteen modules, but
the exact historical pytest/lint argv was not recoverable after the interruption.
The logs and frozen source identity are retained with that explicit provenance
limit; this is not a post-restart test execution or a new whole-backend result.
Root and independent source reviews cleared the final six application and five
test files. Integrated canonical LF hashes and Git bytes match all eleven files.

The separate [PostgreSQL evidence](artifacts/email-bind-actual-pg/verification.md)
records **15 passing actual email create/bind/status SQL controls** in private
table copies under a restricted role. It covers tenant isolation, committed
proof before one synthetic send, competing-phase preservation, rollback and
honest uncertainty after receipt-write failures. All four imported source hashes
match the integrated candidate. Public data/schema metadata stayed unchanged
and private objects were removed. This does not prove the reviewed selector's
SQL/current-account transaction, calendar SQL, full production schema/policies,
live delivery, HTTP authorization or cross-invocation no-resend behavior.

Original-account receipt inspection, an approved durable operator-resolution
workflow and designated provider/browser acceptance remain unfinished. No
package, scenario, final gate or deferred status was promoted. No live provider,
customer message/calendar, deployment or push occurred in this repair.


## PostgreSQL identity and original-account CRM follow-up (6 October)

Actual database verification subsequently exposed a native UUID mismatch that
the preceding synthetic reviewed-account run missed. Source `45943862` accepts
the actual adapter's UUID type while preserving strict row pinning and rejecting
arbitrary identities. The [database report](artifacts/reviewed-selector-pg/verification.md)
records the original failure, **168 focused tests and 12 actual SQL controls**.
Private schema/role objects were removed and public snapshots were unchanged.
The metadata cache routing and partial-schema limitations remain explicit.

Source `e7add564` also corrects a gap in the earlier statement that unknown CRM
creation was held. Contact creation was held, but uncertain call creation could
still adopt a matching title/subject, update that record and report success.
The [repair](artifacts/crm-reference-hold/verification.md) now holds that path
before provider resolution or lookup, preserving all original evidence.

Source `5b880433` adds a read-only original-account CRM inspection button to the
existing platform-admin call drawer. The [owner report](artifacts/crm-receipt-inspection/verification.md)
records **393 backend and 52 Admin tests**, type checking, lint and build checks.
Inspection derives its destination from the saved receipt; it does not refresh,
write, retry, resolve or infer completion from a matching reference.

The [combined integration](artifacts/crm-account-integration/verification.md)
passed **333 tests, zero failures/skips and one warning** on `35fe11b0` across
eight affected modules. All sixteen recorded source/test inputs remained
unchanged and matched Git; no prohibited network attempt occurred. Earlier
owner totals overlap this run. The shared resolver contains both reviewed changes;
the prior PostgreSQL probe did not execute the later opt-in CRM read mode.

Original email/calendar receipt inspection, approved durable operator resolution,
browser/live-provider and release acceptance remain open. Package counts remain
27 eligible unfinished, AG03 separately candidate verified and three deferred;
all fifteen final gates and twenty-two scenario gates remain not run. No push,
deployment, live provider call or freeze exit is included.


## Exact Gmail inspection and voice receipt handoff (6 October)

Source `a195ac72` adds an explicit platform-admin inspection of an exact saved
Gmail message through its original authorization. A complete co-persisted proof
is required; missing historical fields, bulk/SMTP or no-ID outcomes remain
unavailable. Reads never refresh credentials, alter receipts or infer delivery.
Source `651fc90c` fixes voice email/form results dropping original authorization
and child-action references when constructing the outer durable receipt.
Both accepted and unconfirmed results now retain safe evidence without changing
confirmation, privacy or replay semantics.

The [combined verification](artifacts/mail-voice-integration/verification.md)
passed **421 tests, zero failures/skips and one warning** across eleven modules.
Four new handoff controls connect actual voice confirmation/durable persistence
to the Gmail proof parser; they were committed unchanged as `e81f525f` after the
run. All twenty-two source inputs match that commit and stayed unchanged during
execution. Owner totals of 513 backend/68 Admin for Gmail and 138 for voice
overlap this run. Admin type/lint/build results remain bound to unchanged owner
source rather than a new root execution.

Calendar exact-reference observation is the next bounded local implementation;
filtered event lists are insufficient. Durable operator resolution, designated
provider/browser and release acceptance remain open. All package/gate/deferred
states and the feature freeze remain unchanged. No provider request, customer
message, database operation, push or deployment occurred in these repairs.


## Calendar observation and combined receipt verification (6 October)

Source `3de75cd3`, integrated from owner `2010a1bf`, completes bounded exact-event
observation for the existing Google and Outlook calendar connectors and Admin
action panel. It requires the saved original authorization and event reference,
uses one encoded exact-ID read, and changes no provider or local action state.
Found references can be cancelled/deleted; neither presence nor absence proves
the outcome of creation, update or cancellation. No refresh, replacement-account
fallback, window search, automatic resolution or retry is included.

The [owner evidence](artifacts/calendar-receipt-inspection/verification.md) records
296 backend tests across seven modules and 99 Admin tests, with TypeScript,
scoped lint and build checks. Root and independent source reviews cleared the
five application and two test files. The [combined candidate run](artifacts/calendar-mail-voice-integration/verification.md)
passed **508 tests, zero failures/skips and one warning** across twelve modules
on committed `3de75cd3`. Twenty-five recorded inputs stayed unchanged and matched
the committed source; all seven owner files match the integration. Totals overlap
earlier checks. Admin checks bind unchanged owner source; no root browser or
actual PostgreSQL/provider execution is claimed.

Independent evidence review verified all twenty-five root source hashes, seven
owner/integrated Git blobs, twelve owner source/dependency hashes, eighteen owner
artifact hashes and five root artifact hashes, with no mismatch or test rerun.
An initial packaging-only comparison mixed normalized working bytes and an
existing mixed-newline Git blob; comparing raw Git blobs and normalized hashes
corrected that check without changing source or repeating tests.

The remaining-work audit is an inventory review, not fresh correctness proof.
It identified no additional demonstrated bounded repair to start independently
after this assigned inspection work. Substantive gaps remain: CP07's external
alert delivery contract, CP08's invitation disposition, AG06's audited operator
resolution, and OP05's ambiguous owner/original-PBX identity resolution. AG02's
retrieval quality target still fails, AG04 retains its semantic failure and seven
unmapped conditions, and AG05 contact/qualification quality still needs its
approved cohort. These are not merely deployment tasks.

Completion also needs the recorded commercial/reset/refund policy decisions,
designated accounts and owned numbers, approved voice/profile/cohort and carrier
configuration, retention and restore requirements, on-call and operational
owners, supplier-cost evidence, deployment/rollback rehearsal, purchased-journey
acceptance and pilot outcomes. Existing unanswered input requests remain pending;
no default approval is inferred. CP05, CP06 and CP09 remain deferred. Counts stay
27 eligible unfinished plus AG03 candidate verified and three deferred; all
fifteen final and twenty-two scenario gates remain not run. Local commits do not
lift the freeze or establish a paid-production release.


## Knowledge enrichment ownership and passage budget (6 October)

Source `fe1ad226` repairs enrichment results attaching metadata to nodes outside
their requested batch and malformed single retries escaping the fail-soft
boundary. Source `3c6ac038` prevents weak passages exhausting space before useful
source evidence, and lets later candidates survive an oversized heading. Neither
changes source facts, retrieval thresholds, providers or dependencies.

The [combined evidence](artifacts/knowledge-evidence-budget/verification.md)
records **210 passing tests across twelve modules**, zero failures/skips and ten
warnings on committed `d3821a0b`. Twenty-one source/test/fixture hashes stayed
unchanged; the guarded run recorded zero prohibited connection attempts. The
separate budget baseline has 10 failures/6 passes; the enrichment owner baseline
has 36 failures/12 passes. These are parameterized controls, not counts of distinct
product defects. Root and independent source reviews cleared both repairs.

The unchanged raw-source pinned quality evaluator still exits **1**, with 42/45
expected-source recall and 32/45 sufficient passages. Its full final per-case
report is retained; the threshold and gold were not changed. These fixes address
reproduced routing/budget failures, not the remaining paraphrase/source-answer
quality gap. No new PostgreSQL score, model-answer fidelity, customer-corpus
approval, live voice outcome or readiness-gate closure is claimed.

Independent parity review checked all twenty-one executed input hashes, ten root
artifact hashes and seven enrichment-owner artifact hashes with no unexpected
mismatch. The owner's older budget dependency is the sole intentional changed
dependency; both changed source/test pairs match their reviewed commits. Every
gold case result equals the earlier preserved pinned result. This was read-only
evidence verification, not another test execution.


## Explicit secret text and remaining knowledge qualification (6 October)

Source `38b5a8ab`, integrated from `939567b9`, replaces supported complete,
explicitly labelled secret values before new traditional/native caller text
reaches application history, contact extraction and transcript persistence.
Canonical revisions and contact proofs use the same sanitized text. Normal
contacts and business references remain available. Traditional/native privacy
instructions and uncertain-name summary wording were aligned within the existing
prompt budget, with new governed prompt versions and retained old hashes.

The [owner evidence](artifacts/ag04-explicit-secret-boundary/evidence.json) records
**472 passing tests across seventeen modules**, including 89 new boundary controls,
and strict scoped Ruff/diff checks. Both source reviews cleared the final patch.
They caught punctuation, generic account references, bracketed passwords and an
unbounded length-description exemption; the corresponding failing controls and
final result are preserved. One original SQL control had an invalid UUID fixture;
its corrected reproduction against unchanged application code is recorded
separately and is not hidden in the failure count.

Root verified eleven normalized source/test hashes and eleven identical
owner/integrated Git blobs. No other application changes intervened between the
owner base and integration. This was parity review, not another test execution.
These tests exercise actual application methods with synthetic ports, not actual
PostgreSQL, model behavior or human/audio acceptance. The helper handles bounded
English explicit labels; ambiguous state words, unlabelled/cross-turn/oversized
values, audio/provider retention and historical transcripts are outside its
contract. AG04 mapping remains 43/50; no privacy scenario or readiness gate closes.

A separate read-only audit found no additional wiring/source-selection defect
established by the remaining saved knowledge failures. The pinned thirteen
insufficient cases comprise three missing expected sources and ten found sources
with all required fragments but weak literal query coverage. The raw fixture has
no enrichment aliases. Historical SQL results remain separate; they were not
rerun. Related/wrong-product sources can also pass lexical coverage, so a better
retrieval score alone cannot establish faithful answers. The rejected
normalization experiments, failed raw-source gate, content-owner approval and
actual configured-profile answer review remain explicit. No thresholds, gold
answers, providers or dependencies changed during that audit.


## Saved acknowledgement recovery and Admin session wiring (6 October)

Backend `c0cac1f7` and Admin `31fd9b36` add explicit recovery of the narrow
canonical Gmail failure envelope that already contains a successful original
provider acknowledgement. The [package report](2026-10-06-ag06-saved-acknowledgement-recovery.md)
records proof/intent validation, current platform-admin session checks after the
action lock, atomic append-only audit/status writes and unchanged request replay.
It restores saved acceptance without sending another email. Missing evidence,
unknown remote outcomes and unsupported action types remain held.

The Admin panel requires a reason and confirmation, retains the same uncertain
request, and refreshes the whole drawer before displaying the current saved
result. Review found and repaired the actual AuthProvider updating browser
storage without updating the API client's token. Login/logout/initial verification
now use the existing token setter. Stale reload callbacks cannot adopt a later
session, and a coherent recovery by another administrator retires an obsolete
local pending request while displaying the actual recorded actor/reason.

Owner checks passed **441 backend tests across twelve modules**, **eight actual
PostgreSQL controls**, and **159 Admin tests**, including 118 focused receipt
controls. Admin TypeScript/build, scoped lint and backend scoped Ruff passed.
These are separately scoped, overlapping checks; they are not an additive total.
The database fixture applied namespace-rewritten migration 0062 to private
relevant-shape tables under a restricted role. All 108 public table data hashes,
schema metadata and public head 0061 remained unchanged; temporary objects were
removed. It is not full production schema/grant or live-provider acceptance.
The first probe's socket-only Proactor guard limitation is preserved, alongside
the final exact-database async-transport guard and measured admissions.

The [combined root regression](artifacts/knowledge-privacy-recovery-integration/verification.md)
passed **539 tests, zero failures/skips and 208 warnings** across sixteen modules
on committed `c0cac1f7`. All thirty-four recorded inputs stayed unchanged and
matched Git; both socket and async-transport prohibited-attempt counters were
zero. Independent read-only review verified the thirty-four source hashes, five
artifact hashes and eleven privacy source/Git pairs. This is local synthetic-port
integration proof; the owner Admin checks are source-bound separately, not a
root browser run or a new whole-backend checkpoint.

Final integration parity checked all thirty-one owner input hashes, twenty-one
artifact/report hashes and ten owner/integrated source Git blobs with no mismatch.
The entire Admin tree equals the tested owner tree. No tests were repeated for
this parity check; the source-bound owner build and UI limits remain explicit.

Migration 0062 must precede release of the recovery control. Its retained audit
restricts hard deletion of a recovered action or a cascading tenant purge, and
destructive downgrade is refused. Retention/deletion approval remains open.
The pending UI cache is bounded and memory-only; no cross-tab/browser-restart
guarantee or actual React/browser usability acceptance is claimed.

The remaining path from knowledge to customer acceptance still requires approved
versioned campaign content and factual answers, a ratified existing voice/model
profile and owned-call cohort, human comprehension/interruption/contact review,
real Leads/Sales Hub browser evidence, designated connector outcomes, and the
recorded operational/release prerequisites. Raw retrieval sufficiency remains
32/45, AG04 offline mapping remains 43/50, and live/customer acceptance remains
unrun. All package/gate/deferred states and the feature freeze are unchanged.

## Canonical recovery and actual React lifecycle follow-up (6 October)

The earlier hand-built database and synthetic hook-scheduler limitations now
have separate, stronger local evidence. [Canonical recovery acceptance](2026-10-06-ag06-canonical-recovery-acceptance.md)
is integrated as test `684fb47f` and evidence `6aae5fae`. Ten controls passed on
the unmodified consolidated schema followed by the real 0009–0062 migration
chain. JWT/session checks, current role, adapter metadata, Admin detail/digest,
concurrent recovery, refetch, executor replay and transactional rollback were
exercised under a restricted non-owner role. The protected database's 108 table
snapshots, schema and head 0061 stayed unchanged; both generated databases and
roles were removed. The first run's record-copy fixture failure is retained.
No backend application repair was needed. Root verified all 83 recorded source
and 15 artifact/report hashes after integration without another test run.

Admin source `1bf6eaee` repairs three failures reproduced with real ReactDOM:
late verification could restore a logged-out user, late rejected verification
could clear a newer login, and late logout could clear a newer login. A local
operation counter and the existing API authentication generation now fence
asynchronous completions; effect cleanup invalidates outstanding work. This
changes no backend authority or recovery outcome contract.

The [React evidence](2026-10-06-ag06-react-dom-acceptance.md) retains the initial
nine passing/three failing controls and the final twelve passing controls,
included in 171 passing Admin tests. TypeScript/Vite build passed. A subsequent
documented lint exception for the intentional mount-only check changed comments
only; final scoped lint passed without warnings. The exact pre-comment test/build
hash and final source hash are recorded separately. JSDOM 25.0.1 is an exact dev
dependency; no existing locked dependency version changed. Tests ran in a fresh
clean-install fixture with source parity, real React effects/StrictMode/native
DOM events and synthetic HTTP responses. This is neither a browser run nor
designated-account usability acceptance.

React evidence is integrated as `863c3882`. Root verified all 67 final recorded
Admin source/config/dependency/test hashes and exact equality of the entire
integrated Admin Git tree with the owner source commit. This is integration
parity, not another test/build run. Independent review of these documentation
changes confirmed every package, scenario, gate and freeze status is unchanged.

The remaining blockers are substantive: approved campaign facts and retrieval
quality, configured model/voice comprehension and acoustic checks, a designated
call/contact cohort, actual Leads/Sales Hub and connector outcomes, and recorded
operational/release acceptance. Historical campaigns and synthetic fixtures are
not current designations. The existing request for the campaign, approved
document/version, owned test number and connected test accounts is unanswered.
No package or readiness gate is promoted, no deferred package is resumed, and no
deployment or push is claimed.

## Knowledge source evidence and diagnostic parity (6 October)

Three independently reviewed repairs are now integrated:

- `15c6a7c0`: [adjacent required terms](2026-10-06-ag02-required-conditions.md)
  remain with a selected price or both are withheld. Four generic English
  obligation markers extend the existing grouping; this is not general semantic
  qualifier recognition. Baseline thirteen failing/one passing controls became
  fourteen passing controls, included in 143 passing affected tests.
- `059334c4`: [authored-source coverage](2026-10-06-ag02-authored-coverage.md)
  prevents valid same-node generated aliases and other nodes' alias frequency
  from granting factual confidence. Search ranking and candidates are unchanged.
  Numeric-only queries cannot inherit an older coverage score. Final owner
  verification passed 174 unit tests and thirteen separate actual PostgreSQL
  controls. The SQL run preceded the final pinned-only correction; exact SQL
  function bytes remained identical and that source distinction is recorded.
- `bb678ac0`: [existing knowledge test preview](2026-10-06-ag02-knowledge-diagnostic-evidence.md)
  now shows the same admitted source passages, weak/no-match state and provenance
  as the shared voice boundary. Generated derivatives remain in the legacy raw
  candidate response for compatibility, but the UI never uses them as evidence.
  Query/campaign/content changes invalidate stale results. The frontend rejects
  missing/inconsistent evidence rather than silently using an older response.
  Backend and frontend must be released compatibly. Owner checks passed 156
  backend and 25 frontend tests, TypeScript, scoped lint and the Next production
  build. These separately scoped totals overlap other checks and are not added.

The [combined root artifact](artifacts/ag02-source-evidence-integration/manifest.json)
records **220 passing tests, ten warnings and zero skips** across thirteen
modules in 9.38 seconds on committed `059334c4`. All 25 recorded source inputs
matched Git before execution and remained unchanged afterward. Socket and async
transport prohibited-attempt counters were zero; 231 internal socketpairs were
recorded. This is offline actual-method integration with synthetic ports, not
another database, browser, provider or full-backend qualification.

The same run executed the unchanged sixty-question raw-source evaluator:
**42/45 expected-source recall and 32/45 sufficient passages**, quality-gate exit
one. Every case result equals the preserved pinned baseline. The owner SQL run
retains **40/45 recall and 31/45 sufficiency**. Neither gate is redefined by the
passing regression tests. Gold files, thresholds, providers and dependencies are
unchanged; the owner's LF/CRLF matrix-hash difference is explicitly reconciled.

These repairs reduce misleading previews and unsupported evidence admission.
They do not establish semantic answer accuracy or improve the measured raw
paraphrase score. Authored-source SQL weighting adds query work; deployed timing
and configured model/audio behavior still require qualification. The requested
current campaign designation is pending, along with approved versioned facts,
owned destinations/accounts and the existing customer/operational acceptance.
No package, scenario or final gate is promoted; CP05/CP06/CP09 stay deferred.

Final read-only parity verified eleven owned owner/integrated Git blob pairs,
46 owner artifact/report hashes and exact equality of the entire integrated
Talk-Leee tree with the frontend owner's source commit. Independent review
verified the combined 25 source and five artifact hashes, all sixty unchanged
gold case objects and unchanged tracker states. These were parity checks, not
additional test executions. The stronger combined source evidence is separate
from earlier owner runs whose dependency files preceded the sibling repairs.

## Knowledge query and tool evidence checkpoint — 7 October 2026

The [query-quality record](2026-10-07-ag02-query-quality.md) binds 534 passing
checks across 22 modules at `c0f0429895f87dcf4ec7d6b1e75c2dda42740af4`, with 37
unchanged committed input hashes. It covers the current-turn/successive-lookup
evidence repairs, knowledge-only tool buffering, separate native query intent
instructions and the offline-tested bounded semantic qualification runner.
The first integrated structural-test failure is preserved; the repaired test
checks actual source delivery instead of requiring an unused import.

This checkpoint used the existing virtual environment, not the exact dependency
target used for the original whole-backend run. No provider, database, browser,
audio or customer acceptance was performed. The integrated synthetic model dry
plan is ready; its designated credential source remains pending. All sixty raw
gold case results are unchanged, including the failed 32/45 sufficiency score.
Default injection has no automatic reformulation added. No acceptance status,
deferred package or feature-freeze decision changes.
