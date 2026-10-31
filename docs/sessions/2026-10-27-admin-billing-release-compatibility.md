# Admin release compatibility after the billing deployment — 27 October 2026

This follows [the remaining-limitations audit](2026-10-27-admin-remaining-limitations.md)
within the existing production-readiness scope. It supersedes that audit's live
source/schema observations; historical results remain attached to their original
commits. The complete Admin release has not been activated.

## Changed production baseline and repair

A fresh read found that another release had advanced the call backend from
`2739eeab` to `a6a6aaae9e7eb89c66ba13e041989be17791389f` and the database to
`0071_paid_only_billing`. API-web now runs
`b090271ecd752b9d487c6bd1096b710b2de9525c`, a merge of that billing release and
the earlier monitoring/PBX maintenance release. The gateway remains unchanged.
These existing changes were preserved; the older Admin candidate was not deployed.

The older candidate did not recognize the deployed billing revision. Simply
combining the files would also leave two Alembic heads, preventing `upgrade head`.
Commit `dd1d044d7bbc18e7d3c9296339b817d7d2e8bd15` merges the deployed billing
code into the Admin release and adds a metadata-only `0078_admin_billing_merge`.
Its parents are the unchanged published `0077_legacy_tenant_rls_repair` and
`0071_paid_only_billing` revisions. Existing billing setup is not replayed when
installing the missing Admin migrations. No provider, pricing decision or
unrelated feature was introduced by this compatibility repair.

Independent review confirmed that the merged backend billing/signup/top-up paths
retain the deployed billing implementation. The frontend retains its existing
Zod compatibility fix alongside the billing changes.

## Verification

- Nine migration-plan tests pass, including an already-current database and a
  database with both predecessor version rows.
- Three real PostgreSQL tests pass in newly created disposable databases:
  billing-only, Admin-only, and both histories. They preserve a complete tenant
  allowance and an existing post-release catalog marker, apply only the missing
  branch, and prove repeat upgrade makes no change. All three databases were
  removed; the shared local baseline was unchanged.
- A fresh production backup contains 121 TABLE DATA entries and 24,711,140 bytes.
  Its SHA-256 is `f2052921dcb3e75a669ab4bb5e492a0e32bb7b2f4a73b93debe5e00cbc5ef61b`.
  It remains private at
  `/var/backups/talky/releases/admin-billing-20261027/talky-current-paid-v2.dump`.
- The archive was restored into a separate PostgreSQL 15.17 container using the
  same image, no network, bounded resources, preserved ownership and application
  role properties. The application role was demoted to its actual non-superuser,
  non-BYPASSRLS permissions before the candidate checks.
- That actual production-copy database upgraded from paid `0071` to `0078`.
  Repeat upgrade, Admin runtime privilege verification and application/schema
  compatibility verification all returned zero.
- Complete row fingerprints match before/after for seven selected tables:
  plans, price options, top-up packages, tenants, billing ledger, checkout
  attempts and saved action acknowledgements. The ledger and acknowledgement
  tables were empty; this is not populated-receipt acceptance or a comparison
  of every database table. The catalog, 17 tenants and one checkout attempt
  supplied populated preservation evidence.
- The temporary recovery container, its exact labeled volume and temporary
  password were removed after identity checks. The backup and evidence remain.
  All eight observed production service PIDs were unchanged by this work.

The isolated migration result hashes to
`eda63309fe2f872b0f6c6fa47cd84ed08ff27e4974465f6fb906e844e9794f3c`.
Local three-path evidence hashes to
`fe5859f0fefeb21bbd37e89dd9e84ecbff77a628356cb14c0d6bb9017a8ec6e7`.
The evidence is under `output/admin-billing-merge-local/`; the server export
manifest binds the sanitized restore helpers and reports. Production data and
credentials were not copied into Git.

Initial failed attempts remain explicit: an initial dump lacked the required
RLS read context and was rejected; a historical capture helper serialized a
PostgreSQL OID as a string and stopped before creating the recovery container.
Both were corrected before the successful restore. A focused Windows
Users/Inbound run exceeded its deadline after source hashing and partial test
execution: 87 collected, 45 logged passes, no terminal reconciliation. It is
not counted as a passed gate.

The first merged full unit/security CI run reported 15,386 passed, one failed
and 220 skipped. Its sole failure was a stale documentation assertion that said
the repository could not read release counters, despite the new read-only
observer. Test-only commit `828e0650` corrects that expectation while retaining
the explicit non-atomic observation, external freeze/approval and TOCTOU
requirements. All 24 focused release-safety tests and independent review pass.
The main CI job had the same sole failure (15,432 passed, 174 skipped); these
overlapping suite counts are not added together. Runtime and migration files are
unchanged from the restored `dd1d044d` candidate.

Final source candidate `828e0650de141531aac0dcc3580847be59ce3ccb` passed all
**14 checks**. [Main CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40414743124)
includes 666 migrated Admin PostgreSQL passes, 469 inbound integration passes,
both frontend checks and the container build/security scan. Its broader backend
suite reports 15,433 passes and 174 skips.
[Voice/security CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40414743132)
reports 15,387 passes, 220 skips and 18 passing subtests.
[Native gateway CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40414743331)
passes the production build and sanitizer gates. Overlapping counts are not
added together; existing skips and warnings are not hidden or treated as
production acceptance. The final 14-check receipt hashes to
`310094e44a40c17e5850a0ccc479480367f6840fc1dae3033da6bb1b3432bd54`.
Later documentation-only commits do not change this tested runtime source.

## Current live browser result

The protected hosted Admin panel was rechecked against API-web `b090271e`.
Telephony is Operational and the exact Inbound-controls GET now returns the
correct Admin CORS header. Its previous CORS failure is resolved in this live
baseline. No business mutations, call origination or paid-provider actions were
made; monitoring performed bounded PBX control reads.

Four contract failures remain observed:

| Area | Current symptom | Matching candidate repair |
|---|---|---|
| Users & Roles | Pending/terminal operation discovery returns 500 | Static operation routes, reviewed operations and matching user migrations |
| Inbound history | All filter returns 400 | All-status filtering and bounded cursor response |
| Connectors | Global totals are unavailable | All-connectors aggregate response |
| Media history | Deletion-history route returns 404 | Matching operation/history/recovery API |

The enquiry list still contains no rows. Its current successful empty response
does not establish populated revision compatibility. Read-only page success does
not establish mutation, MFA, ownership, provider or recovery acceptance.

The current browser receipt hashes to
`4f465211ea77cd37ab6f20723752b2cc66a9a6889fa141b9e22a2db3a7761179`.
It binds six views and 37 API attempts, with 34 browser-readable responses and
successful logout. The three unreadable Users-operation 500 responses without
CORS headers are recorded, not discarded.

## Remaining release work

The deploy checkout's only untracked content was the five registered immutable
release worktrees. Each directory, full commit and clean status was checked;
only its exact `/releases/<SHA>/` path was added to local Git exclusions.
Unknown paths remain visible. No release directory was deleted or broadly
ignored, and the parent checkout is now clean.

The candidate Asterisk `--check-only` inventory and configured synthetic-call
syntax check pass. These checks do not originate a call or prove returned audio.

Activation still requires the existing
[production drain contract](../DEPLOYMENT.md#production-drain-manifest-contract):
the actual inbound/outbound freeze, fresh gateway/ARI/Redis/database zero counts,
topology/change references and two distinct owner attestations. Deployment was
already authorized by the user; the concrete operational record is missing.
The updated owner/procedure question is pending. No freeze or approval has been
invented. The candidate remains on `codex/admin-release-20261026` / draft PR 22.

At activation, preserve the actual `b090271e` API-web unit as a recovery input,
publish the final commit through `APP_VERSION`, verify nginx exact-route CORS,
run the actual-role privilege gate after migration, and repeat the four failing
hosted contracts before designated Users/Inbound mutation acceptance. Account
MFA/session, real provider effects, recovery/load, supplier finance and policy
requirements remain open; the feature freeze remains active.

Two additional existing limitations were identified without expanding this
repair:

1. Scheduled backups set `app.bypass_rls` but saved acknowledgement SELECT also
   requires an active, verified platform-admin `app.current_user_id`. Archive
   table counts cannot detect rows hidden by that policy. The current table is
   empty, so no loss is demonstrated. The bounded repair needs an explicitly
   configured backup principal and validation/dump in one read-only snapshot,
   with a populated restore and concurrent-deactivation regression. Do not
   widen the application policy or privileges. The manual backup used the
   existing administrator read context; this does not fix the scheduled job.
2. Existing platform-admin tenant creation still defaults to the retained free
   plan. It needs reconciliation with the separately approved paid-only signup
   policy, while preserving deliberate administrator quota authority. This is
   an existing operator-path mismatch, not a merge regression.
