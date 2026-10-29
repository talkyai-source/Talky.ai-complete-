# External PostgreSQL runtime grants

Use `runtime_grants.py` after canonical migrations and before starting the API
or workers. It supports PostgreSQL 16, the default `canonical-0083` schema profile, one
dedicated application database and its existing migration owner. It separates
the runtime LOGIN from database/object ownership without granting superuser,
BYPASSRLS, CREATEDB, CREATEROLE, REPLICATION or role membership.

The existing production catalog is not the default canonical fixture: the
[28 October read-only inventory](../../docs/sessions/2026-10-28-final-live-and-database-observation.md)
contains 123 tables. Migration 0083 repairs four existing customer/support
contracts absent from the old 113-table bootstrap, bringing the fresh profile
to 117. A separate, explicitly selected `production-0083-preserved-20261029`
profile retains the other six tables and the audit-chain sequence with no
runtime/PUBLIC privileges. It checks their reviewed structural fingerprints;
it does not silently adopt other objects. See the [catalog pre-mortem and
sanitized evidence](../../docs/production-readiness/customer-support-database-premortem-20261029.md).
The remaining production role/TLS/ownership cutover and recovery evidence are
still required. Neither profile is an approval to operate the current live
role unchanged or a claim that this work has been deployed.

The backend is a trusted database client. Auth must look up a principal before
knowing its tenant; admin and worker paths need cross-tenant access. Existing
`acquire_with_tenant(pool, None)` uses transaction-local `app.bypass_rls=on`;
ordinary tenant requests use `app.current_tenant_id`. These application settings
remain supported. Runtime credentials must never be issued to customers or an
arbitrary SQL interface: NOBYPASSRLS does not prevent a credential holder from
setting this existing application context. This tool does not rewrite policies
or claim protection from a compromised backend credential.

## Prepare a dedicated production profile

A database administrator creates two separate LOGIN roles and a database owned
by the migration role, assigns generated credentials through the operator's
secret mechanism, and configures certificate-verified PostgreSQL access. Both
roles must be NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE and NOREPLICATION,
with no memberships or login-time role/database overrides. The owner runs the
repository migrations using `MIGRATION_DATABASE_URL`; runtime services receive
only their separate runtime `DATABASE_URL`. Do not put the owner's secret in the
application environment. The grants tool does not create or rotate credentials.

Provide two named services in a protected file using the same explicit host,
hostaddr (if any), port and database. Use the configuration format described in
[database recovery](README.md#configuration), `sslmode=verify-full`, an explicit
`sslrootcert`, and a protected password file. Supply only service names and file
paths on the command line. The tool ignores inherited libpq credentials, service
names and options. Passwords and endpoint values are not included in receipts.

Stop migrations and use a maintenance window: the supported dedicated profile
revokes PUBLIC database/schema/object access and normalizes the runtime ACLs.
Existing explicitly granted backup/operator access is retained. Other services
that depended on PUBLIC CONNECT/USAGE need their own explicit reviewed grants.
The owner must own the database, public schema (directly or through
`pg_database_owner`), every application relation and every application function.
Trusted extension member functions may belong to PostgreSQL's bootstrap owner;
they are not reassigned or blindly regranted. Mixed ownership, extra application
schemas, unexpected objects/functions, SECURITY DEFINER routines, role elevation,
missing forced RLS or a different migration head are refusals. Investigate and
reconcile those conditions explicitly; do not use `REASSIGN OWNED` as a generic
workaround. No existing production database has been changed by this work.

```bash
python scripts/database/runtime_grants.py plan \
  --service-file /etc/talklee/secrets/pg_service.conf \
  --owner-service migration --runtime-service runtime \
  --receipt /var/lib/talklee-release/grants-plan.json

python scripts/database/runtime_grants.py apply \
  --service-file /etc/talklee/secrets/pg_service.conf \
  --owner-service migration --runtime-service runtime \
  --receipt /var/lib/talklee-release/grants-apply.json

python scripts/database/runtime_grants.py verify \
  --service-file /etc/talklee/secrets/pg_service.conf \
  --owner-service migration --runtime-service runtime \
  --receipt /var/lib/talklee-release/grants-verify.json
```

These commands select `canonical-0083` by default and refuse the six preserved
objects. For the reviewed preserved inventory, pass the same explicit option to
**each** `plan`, `apply` and `verify` command:

```bash
python scripts/database/runtime_grants.py plan \
  --profile production-0083-preserved-20261029 \
  --service-file /etc/talklee/secrets/pg_service.conf \
  --owner-service migration --runtime-service runtime \
  --receipt /var/lib/talklee-release/preserved-grants-plan.json
```

Then run `apply` and `verify` with that same profile and new receipt filenames.
Only the two repository-pinned profile names are accepted; there is no arbitrary
profile-file option or automatic selection. A future canonical revision requires
another review of the preserved profile's pinned base before it can be used.

`plan` is read-only catalog validation, not a runtime login or applied-grants
receipt. `apply` validates first, changes ACLs in one transaction and then opens
a real runtime TLS connection. `verify` checks catalog/effective privileges and
the real runtime login without writes. A failed runtime password check after
`apply` does **not** roll back already committed grants: its error explicitly
says grants committed; fix credentials and run `verify`. Do not activate the
application unless verification passes. Existing receipt files are never
overwritten. Python uses the locked installer dependencies (`psycopg2-binary`).

## Exact permission contract

- CONNECT and public-schema USAGE; no schema/database CREATE or TEMP.
- SELECT, INSERT, UPDATE and DELETE on the reviewed application tables; existing
  RLS and immutable-record triggers still restrict those operations. Views are
  SELECT-only. `alembic_version`, `ai_config_migrations` and migration adoption
  bookkeeping are SELECT-only. No TRUNCATE, REFERENCES, TRIGGER or grant options.
- Application sequences receive USAGE and SELECT, allowing `nextval` but not
  `setval`. The migration bookkeeping sequence is SELECT-only.
- The explicit preserved profile grants runtime no rights on `audit_chain_state`,
  `audit_event_types`, `rate_limit_events`, `suspension_propagation_queue`,
  `tenant_ai_configs_backup_20260907`, `worker_status`, or
  `audit_chain_state_id_seq`. Rows, definitions, RLS and ownership are preserved.
  Runtime/PUBLIC table and column rights are revoked and verified absent;
  already-reviewed separate backup/operator grants are retained. Other unknown
  tables, sequences or changed preserved definitions still refuse the operation.
- Known invoker trigger functions have no direct runtime/PUBLIC EXECUTE. The
  existing `update_call_status(uuid,text,integer)` compatibility projection is
  the only application function explicitly executable by runtime. Maintenance
  `prune_tenant_policy_audit_log(integer)` remains owner-only. Existing reviewed
  extension functions retain their ACLs; UUID-default execution is checked.
- Owner defaults for future tables, sequences and functions grant nothing to
  runtime or PUBLIC. Function EXECUTE is revoked at both global and public-schema
  default levels. After every reviewed migration, update the pinned profile as
  necessary and reapply/verify before activation. Unknown objects do not silently
  become accessible because their creator has permissive defaults.

The global default changes apply to future objects created by this migration
owner in **every schema of this database**, alongside the explicit public-schema
changes. They do not change that role's defaults in another database. This is why
the tool requires a dedicated application database and refuses extra application
schemas; do not reuse its profile on a shared database without a separate review.

The canonical inventory extends the retained 0082 restore inventory with the
four existing contracts repaired by 0083; its dedicated acceptance exercises
the new schema and customer settings under restricted runtime access. The
preserved inventory compares six table definitions against independently observed
read-only catalog signatures, including columns/defaults, constraints, indexes,
RLS and dependencies. Its sequence type/settings/owning-column relationship are
also checked. Functions are additionally checked for reviewed
names, exact argument/return signatures, SECURITY INVOKER and ownership. A profile
match is not a full tamper-proof schema audit; the migration release and trusted
owner are still part of the release boundary. RLS definitions are fingerprinted
before and after grants and must remain identical.

PostgreSQL documents that per-schema defaults add to global defaults and cannot
revoke the built-in global PUBLIC EXECUTE by themselves: see
[ALTER DEFAULT PRIVILEGES](https://www.postgresql.org/docs/16/sql-alterdefaultprivileges.html).
The explicit function review also follows its
[SECURITY DEFINER guidance](https://www.postgresql.org/docs/16/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).
Table-level revocation also removes the corresponding column grants; the tool
separately verifies no runtime/PUBLIC column ACL survived. See
[REVOKE](https://www.postgresql.org/docs/16/sql-revoke.html).

## Reproducible acceptance

`tests/runtime_grants_rehearsal.py --run --receipt /protected/new-receipt.json`
uses the existing canonical TLS fixture to create unique local Docker PostgreSQL
servers, load the complete schema and migrate it. It accepts no existing endpoint.
It exercises real logins, normal tenant CRUD and foreign-tenant refusal, system
scope, prohibited DDL/migration/role operations, fail-closed future objects,
unsafe role and ownership refusals, function escalation/overloads, wrong heads,
missing forced RLS and wrong runtime credentials. The initial-admin acceptance
helper also runs on this generated schema with the supported restricted runtime.
The customer-settings helper checks AI credential rotation history and active-key
uniqueness, recording defaults, telephony selection, emergency schema, and tenant
isolation. After canonical acceptance, the same disposable fixture reconstructs
the six historical definitions from repository sources and reviewed nullable
backup columns. The explicit 123-table profile must match the independently
observed signatures, preserve synthetic contents across repeat apply, deny
runtime reads even with application bypass enabled, remove column-only PUBLIC
grants, and refuse unknown inventory or altered definitions. Receipt source
hashes cover the profile files, catalog query and these helpers.
Owned containers/network and generated secrets are cleaned up, and only a
nonsecret receipt is retained. Ubuntu CI runs this alongside canonical recovery.
An unexecuted test definition is not live deployment or operating acceptance.

For the native schema/adoption regression suite, set `TEST_POSTGRES_BIN` to
PostgreSQL16's binary directory and run
`python -m pytest scripts/database/tests/test_customer_support_contracts_pg.py`.
It creates only its own loopback cluster and synthetic databases, never accepts
an existing DSN, and proves 0082→0083 repair plus refusal under schema/owner drift.
Its disposable cluster disables fsync to reduce test overhead: these are
transaction/schema tests, not crash-durability or RPO/RTO evidence. TLS/login
and recovery qualification remain separate required drills.
