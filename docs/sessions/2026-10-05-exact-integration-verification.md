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
