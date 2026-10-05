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
