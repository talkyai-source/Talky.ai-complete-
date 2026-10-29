# Customer support and database pre-mortem — 29 October 2026

Scope: existing OP10/OP11/OP12 and customer settings contracts. This review does
not approve paid launch, privilege conversion, or a production data copy.

## Failure reproduced in the schema path

The documented fresh installation loads `backend/database/complete_schema.sql`,
stamps `0008_tenant_voice_tuning`, then runs Alembic forward. That snapshot omits
four tables used by registered APIs/services. The stamp skips 0005's telephony
creator; AI credentials and recording policy were supplied by historical SQL;
0067 explicitly skips AI credentials when absent. The same gap also omits
`tenants.active_telephony_provider`. A healthy process and a successful canonical
restore therefore did not prove these customer journeys worked.

0083 adds those existing contracts only. Existing relations must match parsed
reference columns/defaults, checks/keys, indexes, foreign keys, known invoker
triggers, owner identity and canonical tenant RLS. It preserves accepted rows,
ACLs, policies and object identity, and refuses unknown drift. The explicit
data-hygiene exception removes only historical `approvals[].verification_code`
from emergency requests; order and every other field remain intact, no legacy
approval gains a verified marker, and unknown non-array payloads refuse for
private reconciliation. It never changes
ownership or privileges. Missing tenant tables get FORCE RLS; the emergency table
retains its existing global administrative contract. The migration refuses a
destructive downgrade. The original published migrations remain unchanged.

The canonical runtime profile increases from 113 to 117 tables. It remains a
fresh-install profile, not an approval of the observed 123-table production
inventory. The strict refusal of extra objects remains in place.

## Ten additional production objects

The operator ran `scripts/database/customer_support_catalog.sql` in a read-only
transaction. It selected catalog definitions only, not application rows or row
counts. Definitions/defaults and the two attached timestamp trigger bodies were
reviewed; no credential literals were found. The [sanitized catalog receipt](customer-support-catalog-20261029.json)
contains structural counts/types and checksums, without defaults, function bodies
or application contents. The catalog reported no public
routine containing any of the ten names, no dependent views and no incoming FKs
to these ten tables. This supplements repository searches; dynamic SQL outside
the repository still requires operator confirmation before retirement.

| Object | Existing purpose and consumer | Access / retention disposition |
| --- | --- | --- |
| `tenant_ai_credentials` | Credential resolver, tenant BYOK endpoints and API-key management; historical April 25 creator plus 0067 active-only uniqueness. | Supported runtime DML behind FORCE tenant RLS. Preserve disabled rotation history and encrypted values; do not put ciphertext, key suffixes or request bodies in support evidence. Tenant deletion has an existing cascading FK. |
| `tenant_recording_policy` | Recording policy API/service and retention purge worker; historical April 25 creator. | Supported runtime DML behind FORCE tenant RLS. Preserve consent choices and retention settings. Default 90 days is an application default, not a legal retention determination. Validate actual asset purge and held-record behavior separately. |
| `tenant_telephony_credentials` | Saved-provider list/save/delete/test/select APIs; 0005 creator. | Supported runtime DML behind FORCE tenant RLS. Preserve encrypted credentials and selected-provider pointer; provider test results need the same sensitive-data handling as support logs. This repair does not enable unsupported cloud origination. |
| `emergency_access_requests` | Registered `/admin/emergency` routes and emergency-access service; historical security schema. | Existing global administrative record, no tenant column/RLS. Preserve justification, approvals and incident review history. Application authorization and dual control are separate acceptance requirements. Review found unverified plaintext approval codes and concurrent read/modify/write risk, referred to the security workstream. |
| `audit_chain_state` | Historical chain-verification checkpoint; outgoing FK to `audit_logs`. No current application or catalog routine consumer found. | Preserve with runtime/PUBLIC access denied pending explicit audit-history disposition. Do not grant runtime rights or delete to fit the fresh profile. |
| `audit_event_types` | Historical event taxonomy with retention metadata. No current application/catalog consumer found. | Preserve with runtime/PUBLIC access denied. A retention_days column is not proof an enforcement job exists. |
| `rate_limit_events` | Historical rate-limit event store. Current code search found no consumer. Scope keys/endpoints may identify people. | Preserve with runtime/PUBLIC access denied pending operator review and retention decision. Never export historical event contents into a public support bundle. |
| `suspension_propagation_queue` | Historical reliable propagation queue with outgoing FK to `suspension_events`. No current consumer found. | Preserve with runtime/PUBLIC access denied. Do not replay unknown pending work or assume it is safely discardable; reconcile intent/outcome before any future reactivation. |
| `tenant_ai_configs_backup_20260907` | Historical pre-migration configuration copy; 0047 explicitly protects it without dropping it. | Runtime/PUBLIC access denied; FORCE RLS preserved. No runtime grant. Retire only after an explicit preservation/retention decision; the table name alone is not authority to delete. |
| `worker_status` | Historical worker heartbeat table; current health path no longer references it. | Preserve with runtime/PUBLIC access denied until operator confirms no outside producer. Task descriptions may contain customer identifiers; no support export of row contents. |

## Bounded production-profile qualification

The separate fixed profile `production-0083-preserved-20261029` is implemented in
`runtime_grants_preserved_profile.json` and explicitly selected with `--profile`.
The default remains `canonical-0083`. Its six structural hashes derive from the
reviewed read-only metadata; the script additionally validates the audit-chain
sequence's settings and owning-column relationship. The dedicated acceptance
helper reconstructs the historical schema using synthetic contents and runs at
the end of the existing TLS grants rehearsal. It never selects a profile from
the observed table count or accepts an arbitrary profile file.

The qualification boundaries are:

1. Reconstruct the reviewed schema in an isolated PostgreSQL16 fixture, retaining
   the six nonruntime objects and the `audit_chain_state_id_seq` sequence. Use
   synthetic tenants, credentials, support history and queue outcomes only.
2. The separate production profile explicitly inventories those six preserved
   tables and their sequence with runtime/PUBLIC access denied, validates their reviewed structural
   signatures/dependencies and existing ownership, and denies runtime/PUBLIC
   table, column and sequence privileges. It must continue refusing any other
   unknown object or function. Unknown objects are never silently ignored.
3. Prove 0082→0083 preserves existing objects, ACLs and rows; repeated application
   is harmless; changed defaults/keys/indexes/policies/triggers/owners refuse
   atomically. Prove actual runtime login can complete supported customer writes
   and cannot read another tenant or any preserved historical object.
4. Rehearse backup, restore and verification with distinct restricted roles,
   certificate-verified TLS, off-host backup destination, observed timings and
   retained evidence. A schema-only exercise does not prove customer-data
   recovery, production RPO/RTO or key availability after a restore.
5. Perform coordinated maintenance cutover only after the designated destination
   and privileged operator access exist. Freeze/drain first; bind evidence to the
   exact candidate, schema and backup. A migration that started/failed must use
   the existing guarded recovery path rather than a code-only rollback.

The observed production role owns the application tables and has CREATEDB,
CREATEROLE and REPLICATION; its observed connection lacked TLS. Changing the
runtime grants JSON does not remediate those properties. No live ownership,
role, data, ACL or TLS changes were performed by this workstream.

## Verification and support failure boundaries

`scripts/database/tests/test_customer_support_contracts_pg.py` provisions its
own loopback PG16 cluster, boots the actual 0082 baseline, and checks fresh
repair, historical schema adoption, tenant isolation, history preservation and
atomic refusal under hostile drift. It accepts a binary directory, not an
existing DSN. Runtime-grants TLS rehearsal additionally executes the repaired
customer contracts with its real restricted login through
`support_contracts_acceptance.py`; all acceptance data is synthetic and rolled
back. Execution results must be recorded separately; adding tests is not a pass.

Local PostgreSQL16 validation passed 16 schema/adoption/security regressions,
including exact emergency-code removal and malformed-payload refusal. A separate
native run also matched the reconstructed six-table schema to the independently
observed structural fingerprints and rejected a changed default. Eleven offline
grants-boundary tests passed. Native JUnit files are retained under
`test-output/customer-support-0083-pg.xml` and
`test-output/customer-support-preserved-pg.xml`; disposable clusters were removed.
These results do not claim that the new 123-table TLS rehearsal, Ubuntu forward
upgrade, off-host recovery or actual production cutover has completed. Candidate
CI and release evidence must close those requirements separately.

Support recovery must distinguish an unsubmitted action from an unknown external
outcome before retrying billing, telephony, deletion or delivery. Operators must
not bypass tenant predicates, authorize their own emergency requests, paste
verification secrets into case notes, or use owner credentials for routine
support. Any successful API response, backup file, or green unit suite is only
its own evidence; real delivery, restored application behavior and end-to-end
incident handling remain independently testable release requirements.

The access restriction above concerns the application runtime and PUBLIC. Existing explicit operator or backup-role grants are preserved and still require the operator's access review; this is not a claim that the migration owner is the only role able to read those records.
