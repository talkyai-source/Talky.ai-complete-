# Remaining Admin repairs and live backup verification — 27 October 2026

The scheduled backup repair is live. The complete Admin release is still awaiting
the existing coordinated activation record. This follows the
[billing compatibility checkpoint](2026-10-27-admin-billing-release-compatibility.md)
and stays within CP09 and the production-readiness feature freeze.

## Repairs

- **Backups:** commit `eae0465e` validates an explicitly configured active, verified
  platform administrator and exports the same read-only database snapshot used by
  `pg_dump`. This prevents the saved-acknowledgement policy from silently omitting
  rows, including when the administrator is deactivated during a dump. Missing or
  invalid authority fails before publication. Application policies were not widened.
- **Tenant creation:** omitted or null plans now create a tenant with no plan and
  zero included minutes. Deliberate administrator plan/quota choices and existing
  tenant replays remain intact. This removes the accidental free-plan default.
- **Supplier evidence:** the existing Usage screen can retain a bounded Deepgram
  billing-breakdown export, its original bytes/hash, account/period context,
  corrections and separate review/reconciliation records. Request recovery survives
  refresh and selection changes. Zero charges, UTF-8 BOMs and invalid input receive
  explicit handling. Authentication, current authority, RLS and atomic audit apply.
- **Deployment:** preserve the exact pinned API-web unit for pre-migration recovery;
  verify the actual runtime role after migration; atomically publish the release
  version and check both stable API processes. Existing drain requirements remain.
- **Latest billing compatibility:** merge `e937d10e` without replacing its billing,
  top-up or allowance behavior. Metadata-only `0080_supplier_allowance_merge`
  joins `0079_supplier_evidence` and published `0072_allowance_anchor`;
  published `0078_admin_billing_merge` remains unchanged. The runtime preflight
  also checks the allowance-anchor column required by the current quota reader.

Supplier exports are operator-provided evidence, not authenticated provider
statements. Deepgram's reported USD usage charges do not establish tax, credits,
payments or net spend. Supplier-account allocation is absent, so differences stay
explicitly unallocated. Cartesia/Groq formats and a designated finance review are
still missing. This first supported source does not close all supplier-finance
acceptance or authorize new provider requests.

## Live backup change

The production checkout remained `e937d10e842d7be5165a42028fda828ad444ca5a`,
schema `0072_allowance_anchor`, with API-web pinned to `48f61708`. Only the
backup oneshot unit was pointed at immutable backup source `eae0465e`; its
root-owned configuration has mode 0600. No application, gateway, worker or
Asterisk process restarted. The next full service installation replaces the
backup unit pin while retaining its separate configuration.

The candidate backup contained 24,712,097 bytes and 121 TABLE DATA entries. A
separate PostgreSQL 15.17 container, using the same image with no network,
restored ownership, roles, extensions and metadata successfully. Its application
role was demoted to the production non-superuser/non-BYPASSRLS properties before
verification. The exact owned container, volume and temporary password were then
removed; the archive and private evidence remain.

The scheduled service's first activation also succeeded: archive
`talky-20261027T063413Z.dump`, SHA-256
`7417fa936af61d0e1282801c929277cbd75c86f8743070f3469f50f3ed750892`.
All eight protected service PIDs and the production checkout were unchanged;
the backup timer remained active. Activation receipt:
`4542c49f304f3e968a5c5058015200f4fabb5e69a03b7a56f5814e376c6b93ad`.
Restore receipt:
`e0b61232cf931c16e55599b750d2a0642537366201d04167a9c08a875dc3af0c0`.
The source saved-receipt table was empty; populated-row and concurrent-deactivation
proof comes from the separate local PostgreSQL regression, not these live counts.

## Verification and preserved failures

- Backup verification: 19 focused unit/shell checks, six actual PostgreSQL
  snapshot/restore cases and 17 explicit test-target/client guards passed.
- Tenant repair: 65 focused backend and 18 actual ReactDOM checks passed.
- Deployment checks: 27 passed; the Unix ownership/mode case was explicitly
  skipped on Windows and remains required in Linux CI. Native file and symlink
  rollback cases passed. Independent review found no remaining blocker.
- Supplier checks: 28 parser/RLS-inventory, 27 privilege-contract and 24 actual
  ReactDOM checks passed, including the later pending-operation corrections.
- The full Admin suite passed 943 tests with no failures/skips; whole-panel lint
  passed. The production build first correctly rejected an absent API base URL,
  then passed with the same explicit HTTPS API base used by CI. Its existing
  approximately 603 KB bundle warning remains visible.
- The merged backend selection passed 138 checks. Its first attempt exposed a
  positive quota test missing the required reviewed timestamp; the corrected
  fixture now tests the intended bounds instead of passing for an unrelated error.
- Review caught multiple DDL statements inside one asyncpg migration execution;
  the migration now executes each statement separately. Actual isolated migration
  subsequently succeeded.
- The first combined normal-auth database run passed five supplier and two
  planless-tenant cases, but the legacy-replay fixture assumed a retained `free`
  plan that a fresh bootstrap does not contain. This failure is preserved; the
  correction uses an explicitly owned synthetic legacy plan and exact cleanup.
- The corrected combined run passed **8/8** actual PostgreSQL/MFA cases: five
  supplier and three tenant cases. Its clone and eight fixture roles were removed,
  with the protected parent data/roles/catalog and 901 source files unchanged.
  Result SHA-256:
  `a5c9771823414629ae725798a4f607c601474d63e429cc1b067cdc0fdbfd1ef1`.
- All **six** isolated branch migration cases passed after a bounded wait for
  server-side disconnect visibility, retaining exact-zero and unforced deletion
  requirements. Tenant/catalog values, allowance anchors and repeat-upgrade
  behavior were preserved; all clones were removed and the parent was unchanged.
  Result: `6c079aa6945232a992ba4fbd5b783321233a14d28c7bca1491d670c0a375ffe8`.
- CI on runtime `25d2e49e` passed all 943 Admin frontend tests, lint, audit and
  build, plus 484 inbound cases. Its serial Admin run exposed the already-corrected
  legacy-plan fixture and a separate immediate disconnect-count assertion during
  backup teardown. All five supplier cases and 23 backup test bodies passed;
  teardown still failed. The backup fixture now waits boundedly for zero sessions
  and retains unforced deletion. The corrected 23-case local run passed, but did
  not reproduce the Linux observation delay. The failed CI gate prevented later
  backend coverage/OpenAPI and dependent Docker checks; it is not a green release.
- That candidate's separate voice/security run passed 15,488 cases with 220
  skips, but failed two test guards: nested supplier routes were not traversed by
  the security inventory, and a deployment assertion expected obsolete prose.
  The inventory now checks effective inherited dependencies and explicitly covers
  all six supplier method/path pairs. Deployment/backup assertions cover the
  current unit recovery, privilege/version order and isolated restore procedure.
  Both modules pass 34 focused tests; existing failed runs remain recorded.

These results have distinct source boundaries and overlapping suites. They must
not be added into one score or described as complete live acceptance.

## Production-shaped upgrade and subsequent baseline change

A fresh 24,712,514-byte backup was restored into another same-image, network-none
cluster. Runtime `25d2e49e` upgraded its `0072_allowance_anchor` schema to `0080`,
repeated the upgrade, and passed actual restricted-role privilege and application
compatibility checks. All seven selected billing/tenant/checkout/receipt table
fingerprints were unchanged. Receipt and ledger tables were empty, so this does
not establish populated financial or receipt acceptance. Upgrade report:
`798ddc9d9d76ff2e5f95ab5739fd678c069082c63caf8179ed6d6207ff66f39d`.

The exact owned test container, volume and initialization secret were removed.
The final service-invariance assertion then detected an independent production
deployment: checkout `f892ddd3e32884bec98aa1613f7433dfd359e04c`, API-web
`ec96a75ba2d850bfdf0cccb182763ebbc68324ee`, six changed Python-service PIDs,
unchanged gateway/Asterisk identities and unchanged `0072_allowance_anchor`
schema. This failed invariance assertion is preserved; the test cannot be
described as an uninterrupted live baseline. The backup maintenance pin remained
active. Our operation did not activate the candidate or restart those services.

Merge `84fe9fa1` preserves the newer authentication/onboarding and migration
connection handling. Fifty-nine focused backend checks pass. Six selected Next
modules could not start locally because `jsdom` was missing; no assertions ran,
and the clean-install CI check remains required. Its new direct-asyncpg migration
path supports `MIGRATION_DATABASE_URL`; isolated probes must explicitly bind that
variable to the owned database and use the encoded Unix-socket authority DSN.
The earlier SQLAlchemy-style socket query is not portable to that path. Earlier
restore and browser evidence remains attached to the recorded source, and does
not silently qualify a later merged artifact. The fresh combined CI result is
tracked on [PR 22](https://github.com/talkyai-source/Talky.ai-complete-/pull/22).

## Fresh hosted Admin check

Normal password login, logout and monitoring/PBX visibility passed against the
current API-web `48f61708`. The six-view run made 37 API attempts, with 34
browser-readable responses, no business writes or paid-provider actions, and
successful bearer/password clearing and browser/private-file cleanup. It did not
exercise an MFA challenge. The following failures were reproduced afresh:

| Area | Current live result | Release repair awaiting activation |
|---|---|---|
| Users operations | Three discovery requests return 500 without CORS headers | Static operation routes and reviewed-operation migrations |
| Inbound history | All filter returns 400 `invalid_status` | Matching filter and cursor contract |
| Connector totals | 200 list lacks the required summary | Platform aggregate response |
| Media-deletion history | 404 | History, receipt and recovery routes |

Inbound controls now return 200 with the Admin CORS header. Enquiries return an
empty list, which cannot prove populated revision compatibility. The fresh
browser checkpoint hashes to
`d36ed6d078153e20446e2317abdaa31346762cab74df6f681db11998b300ac52`.

After the independent deployment, the same bounded six-view browser check was
repeated against **API-web `ec96a75b`**. It again confirmed normal login/logout,
Operational PBX, corrected controls CORS and the same four remaining failures.
It retained the 37-request/34-readable-response distinction, empty enquiry
limitation and successful credential/browser cleanup. This current receipt
hashes to `313c78ee8c84802553cbffec5e7ad01cb8eb2e6c6ffadf224e12063077532dd9`;
the preceding `48f61708` result remains historical.

## Remaining activation and acceptance

Deployment is authorized by the user. The missing operational record is specified
by the [deployment contract](../DEPLOYMENT.md#production-drain-manifest-contract):
actual inbound/outbound traffic freeze, fresh gateway/ARI/Redis/database zero
counts, topology/change references and two distinct telephony-owner/release-manager
attestations bound to the final candidate. No such record or supplier finance/test
destination designation has been supplied. Idle counters cannot establish a freeze.

The coordinated release, matching Admin frontend promotion and post-release
business/MFA/role/provider journeys therefore remain open. Real call, connector
and media effects need designated owned resources. Off-host recovery, agreed
RTO/RPO, load, finance and policy/customer acceptance remain explicit requirements.
The backup maintenance release does not close these gates, mark CP09 complete or
lift the feature freeze.
