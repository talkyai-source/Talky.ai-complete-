# Reviewed email and calendar authorization repair

On 2026-10-06, the preserved final affected-suite log reports **396 passed, 0 failed, 0 skipped, 28 warnings in 23.03 seconds**. The source was committed locally, unchanged after the cleared final run, as `ac104e2f5497a103429b0a1a6e91855b738ec3a3` over `39407989342b98fdcb0c586a15aab4fbd259f986`. Six application files and five test files changed. No provider, offering, OAuth scope, database migration or new operator-resolution workflow was added.

## Reproduced defects

The initial probe exercised the actual OAuth callback, resolver, reviewed proposal tools and services with synthetic database/provider ports and blocked network. Canonical Gmail, Google Calendar and Outlook Calendar callbacks all saved an active authorization with `external_account_id = null`. The previous reviewed-action contract nevertheless required a nonempty provider account identity. Thus a successfully connected canonical account could neither preview nor execute its supported email/calendar action. Explicit synthetic external identities were positive controls only; they did not represent what the actual callbacks persisted.

[initial.json](initial.json) preserves these three canonical failures and the three positive controls. [refresh-promotion.json](refresh-promotion.json) also shows that refreshing an older, still-active pinned row changed its refresh timestamp and could promote it over a newer reconnect under refresh-time selection.

Two later regression controls identified additional phase-ownership defects. Email binding failure cleanup could overwrite another task's cancelled or running phase; [bind-phase-red.txt](bind-phase-red.txt) records both failures. Calendar finalization could overwrite cancelled/completed phases and report success despite losing that phase; [calendar-phase-red.txt](calendar-phase-red.txt) records 12 failures across create/update/cancel, before and after provider dispatch.

## Repair

- Reviewed email/calendar proposals now carry an explicit `authorization_row_v1` proof: tenant, connector, provider and the actual local authorization row ID. A genuine external provider account ID, when present, remains an additional binding. The local row ID or email address is never fabricated as a provider subject. Display labels are excluded from equality proof and apply arguments.
- Reviewed-effect resolution selects the unique newest active authorization by creation time, with tenant/connector/provider/active-parent checks. A missing or invalid creation time, tie, revoked/deleted row, older row after reconnect, or changed external identity cannot authorize an effect. Same-row token refresh retains a valid review. Ordinary reads, standalone calendar availability and CRM keep their existing selector behavior.
- Resolution checks again after awaited refresh/token installation, and the services check again immediately before dispatch. Calendar update/cancel also retain the saved meeting proof and recheck the saved event after awaited resolution. These are local admission checks; creation time is stable under canonical writers, not database-immutable, and these checks cannot atomically cancel an already dispatched remote operation.
- Email persists the selected original proof and atomically claims its pending action as running before sending. Binding must be acknowledged. Status writes compare the phase this invocation last acknowledged; lost bind acknowledgement cannot claim running-cleanup ownership. One definite 401 may refresh and retry only within the same reviewed authorization. Provider acceptance without a persisted local receipt remains unconfirmed.
- New calendar records retain the complete original proof. Create/update/cancel finalization compares the running phase, preserving a competing cancelled/completed result. Denial before dispatch returns failed; uncertainty after an attempted effect returns unknown and disallows success confirmation.
- Genuine legacy provider-identity proofs remain verifiable. Old records missing their original identity are not retroactively bound to whichever account happens to be connected now. Public action receipts retain the noncredential proof fields.

## Verification and provenance

| Evidence | Result | Relationship |
| --- | --- | --- |
| [affected-final.txt](affected-final.txt) | 396 passed, 28 warnings, 23.03 s | Final affected run; prior owner reported 14 modules. Exact historical argv was not preserved in the recovered artifacts. |
| [canonical-final.txt](canonical-final.txt) | 97 passed, 5.81 s | New canonical controls; included in the final affected run, not additive. |
| [phase-final.txt](phase-final.txt) | 140 passed, 2 warnings, 8.98 s | Focused phase/contract run; overlaps the final run. |
| [legacy-fixtures-final.txt](legacy-fixtures-final.txt) | 69 passed, 21 warnings, 6.58 s | Independently repaired legacy fixture subrun; overlaps the final run. |
| [lint-final.txt](lint-final.txt), [legacy-fixtures-lint.txt](legacy-fixtures-lint.txt) | All checks passed | Preserved owner lint outputs; exact historical argv was not recoverable. |
| Source commit preflight | `git diff --check` passed | Run again immediately before source-only staging; no source edits or test reruns after restart. |
| [Actual PostgreSQL verification](../email-bind-actual-pg/verification.md) | 15 controls passed | Separately scoped actual email intent/bind/status SQL under a restricted private role. |

The final source was reviewed by the original owner, root and the independent reviewer before commitment. After restart, root reread the final calendar running-phase comparison and its 12 actual-method controls and granted source clearance. An additional post-restart independent reread of all six application files and five test files also reported no defects; it did not execute tests. The final artifact hashes and canonical LF source hashes are in [manifest.json](manifest.json). Its source hashes bind the recovered evidence to the frozen candidate reported by the owner; the pytest logs themselves do not embed source hashes or commands.

[commands.json](commands.json) distinguishes recovered command information from recommended reproduction. The server interruption left no saved exact pytest/lint invocation record in this worktree, and the summary-only logs cannot reconstruct the 14-module argument list. No command has been invented as historical evidence, and passing tests were not rerun merely to replace that missing provenance. The recorded 396 result remains an owner-run scoped result, not a newly executed post-restart integration test.

The initial broad affected run in [affected-initial.txt](affected-initial.txt) reported 24 failed and 309 passed. Most failures were fixtures that lacked the new authorization row/proof or did not implement phase filtering; fixtures were updated while retaining rejection, no-send, unknown-outcome and same-account assertions. The standalone availability path also retained ordinary-read behavior. [lint-initial.txt](lint-initial.txt) preserves the initial 69 lint findings rather than replacing that history. The staged evidence whitespace check flags only trailing spaces quoted by that original lint report; the log is intentionally retained byte-for-byte. All other staged evidence passes the whitespace check. The initial/red logs refer to intermediate candidates and must not be presented as executions of the final commit.

The two preserved probe scripts retain their original location-relative output assumption (`Path(__file__).resolve().parents[2]`). They are historical evidence snapshots, not drop-in commands at their archived location; running them there would compute a different output path. Their baseline results also require the original baseline source, not this repaired commit. Reproduce current behavior through the committed canonical test module instead.

## Limits and remaining work

The 97 canonical controls use actual callback/resolver/tool/service code with synthetic ports and blocked external network. They do not demonstrate a live OAuth grant, provider delivery, browser acceptance, a full production schema or distributed atomicity. No customer accounts, messages or calendar resources were used.

The separately preserved PostgreSQL run verifies the actual email service's create/bind/status SQL, JSONB proof retention, pending-phase admission, tenant isolation, rollback, competing-phase preservation and unconfirmed post-send persistence failures. Its email/resolver source hashes match this commit. It does **not** validate the new reviewed selector's SQL/current-account or refresh transaction, the compatibility `.table()` binding branch, production foreign keys/triggers/policies, calendar SQL, an HTTP authorization route, crash recovery or cross-invocation no-resend behavior. Its first evidence-serialization failure and exact first probe remain preserved beside the final result.

Original-action provider inspection, approved operator resolution of ambiguous effects, and designated end-to-end live acceptance remain unfinished AG06 work. This repair does not declare the connector feature production-ready or promote any plan gate.
