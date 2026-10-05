# Repository regression checkpoint — 5 October 2026

The local integration now contains the DNC schema/persistence/speech repairs,
private recording creation, five remaining campaign pacing holds, authentication
availability repair and retention-copy correction. The full verification exposed
additional stale fixtures and one replay contract still being corrected. No push,
deployment, provider effect or customer call occurred. No package or release gate
is closed by this checkpoint.

## Whole-suite results and their boundaries

| Check | Exact candidate and observed result | Meaning |
|---|---|---|
| First complete backend unit/security run | `1b33f6eb`: 11,627 passed, 17 failed, 16 skipped; 67.23% coverage | Preserved failed baseline. Ten failures exposed credential/storage ordering, five were stale API/branding fixtures, one was an RLS inventory statement-boundary defect and one was a stale typed guard result fixture. |
| Follow-up complete backend unit/security run | `5d091528`: 11,749 passed, one failed, 20 skipped; 67.45% coverage; 879.70 seconds | The remaining failure is the native replay's DNC close case: it supplies no persistence acknowledgement but expects speech/close. The new speech gate withholds that output. The replay fixture is under repair; no whole-backend green claim. |
| Whole dashboard suite | `d058cd23`: 774 passed, zero failed, two legacy database skips; 776 total | The previous obsolete campaign row-menu test is corrected. This is a Node unit/structural suite, not browser acceptance. The later Security wording change passed scoped ESLint. |
| Migrated PostgreSQL matrix | `e7b8a84c`: 406 passed, four failed, zero skipped across 34 modules | Actual disposable public head 0061 plus declared isolated CRM/action fixtures. Four assumptions were stale: global append-only row count, older rollback messages and allowed migration-head list. |
| Corrected database modules | Owner source `938783f7`, integrated as `3435fe35`: all 27 passed, zero skipped | Exact fixture identities, retained-row checks, original rollback guards and current-head data/marker preservation replace the stale assumptions. Root full matrix after integration remains pending. |
| Existing backend CI quality commands | `5d091528`: Ruff F gate, high-severity Bandit gate and requirements pip-audit all exited zero | Audit reported no known requirement vulnerabilities; Bandit found zero high-severity findings but counted 255 low and 126 medium findings. This is not an all-severity clean/security certification claim. |

Commands, source hashes and logs are retained under
[backend-complete](artifacts/backend-complete). All recorded source snapshots were
unchanged during their runs. Counts overlap earlier focused suites and must not
be added as distinct scenarios. The dashboard runner saved exit code zero and an
unchanged snapshot before its final console-print step hit a Windows encoding
error; that wrapper error did not change the completed test result. The wrapper
now uses UTF-8. The initial failures remain preserved.

The 20 backend skips are explicit in `final.txt`: two optional librosa controls,
eight non-opted-in cooldown PostgreSQL controls, one stale auth-exception-list
maintenance check, one non-offered model, four POSIX recording-mode controls,
one other POSIX mode control, one optional ffmpeg control and two AF_UNIX checks.
The actual PostgreSQL matrix is separate evidence; it does not silently replace
those skipped tests. The stale auth exception is now being investigated.

## Dependency parity discovered during verification

The shared test venv still imports urllib3 2.7.0 and PyJWT 2.13.0; the repository
already requires 2.8.0 and 2.15.1. Earlier runs are therefore not exact-pin release
verification. No history establishes why that pre-existing environment was not
refreshed. An isolated test-only package target now supplies the required versions
ahead of the existing Lua test dependency. All 62 active declared production
requirements satisfy their markers/specifiers, and both changed imports resolve
inside that target. Default shared imports remain unchanged. This is neither a
fresh complete environment nor a transitive installed-package audit. Exact-pin
combined verification follows the current DNC settlement/replay fixes.

The [environment manifest](artifacts/backend-complete/dependency-environment/manifest.json)
retains installation arguments, before/after imports, parity checks and limits.
No production requirement, shared venv or original application checkout changed.

## Current repairs and remaining work

- [DNC schema alignment](2026-10-05-dnc-schema-verification.md) preserves existing
  records and source-specific identities. Native persistence passed actual
  PostgreSQL checks; [speech admission](2026-10-05-native-dnc-speech-verification.md)
  requires acknowledgement and preserves interrupted/unknown partial delivery.
  Finalization can still overwrite the lead's DNC projection or book an unnecessary
  retry in the current integration; that separately reviewed repair is in progress.
- [Pacing holds](2026-10-05-op05-pacing-holds.md) preserve the original attempt at
  all five remaining pre-intent boundaries. The CI migrated matrix now includes
  pacing, DNC migration and native durability modules. Remote CI has not run.
- [Authentication](2026-10-05-api-auth-availability-repair.md) rejects malformed
  credentials before database access and fails account verification outages closed.
  [Branding/inventory evidence](2026-10-05-branding-inventory-regressions.md) retains
  the static SQL statement-boundary repair and canonical STT fixtures.
- [Recording privacy](2026-10-05-op11-recording-privacy.md) bounds local writes and
  new file creation. [Data lifecycle inventory](2026-10-05-op07-data-lifecycle-inventory.md)
  distinguishes audio, metadata, transcripts, telemetry and backups; unsupported
  Security-page automatic-expiry/countdown promises are removed. Approved policy,
  deployed supplier/storage facts and full request execution remain open.

There remain 27 eligible unfinished packages, AG03's bounded candidate verification,
and three deferred packages (CP05, CP06, CP09). All 15 final gates and 22 scenario
gates remain unrun. Actual model/voice/caller acceptance, connector outcomes,
owned-number/carrier proof, billing-policy decisions, on-call delivery, restore,
compatible release and business acceptance remain required. Existing targets,
failed retrieval/semantic samples and the feature freeze are preserved.
