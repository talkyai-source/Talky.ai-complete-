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
