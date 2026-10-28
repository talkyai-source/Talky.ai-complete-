# External PostgreSQL runtime grants

Use `runtime_grants.py` after canonical migrations and before starting the API
or workers. It supports PostgreSQL 16, the `canonical-0082` schema profile, one
dedicated application database and its existing migration owner. It separates
the runtime LOGIN from database/object ownership without granting superuser,
BYPASSRLS, CREATEDB, CREATEROLE, REPLICATION or role membership.

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

The profile's table/sequence inventory derives from the retained verified 0082
canonical TLS restore receipt. Functions are additionally checked for reviewed
names, exact argument/return signatures, SECURITY INVOKER and ownership. A profile
match is not a full tamper-proof schema audit; the migration release and trusted
owner are still part of the release boundary. RLS definitions are fingerprinted
before and after grants and must remain identical.

PostgreSQL documents that per-schema defaults add to global defaults and cannot
revoke the built-in global PUBLIC EXECUTE by themselves: see
[ALTER DEFAULT PRIVILEGES](https://www.postgresql.org/docs/16/sql-alterdefaultprivileges.html).
The explicit function review also follows its
[SECURITY DEFINER guidance](https://www.postgresql.org/docs/16/sql-createfunction.html#SQL-CREATEFUNCTION-SECURITY).

## Reproducible acceptance

`tests/runtime_grants_rehearsal.py --run --receipt /protected/new-receipt.json`
uses the existing canonical TLS fixture to create unique local Docker PostgreSQL
servers, load the complete schema and migrate it. It accepts no existing endpoint.
It exercises real logins, normal tenant CRUD and foreign-tenant refusal, system
scope, prohibited DDL/migration/role operations, fail-closed future objects,
unsafe role and ownership refusals, function escalation/overloads, wrong heads,
missing forced RLS and wrong runtime credentials. The initial-admin acceptance
helper also runs on this generated schema with the supported restricted runtime.
Owned containers/network and generated secrets are cleaned up, and only a
nonsecret receipt is retained. Ubuntu CI runs this alongside canonical recovery.
An unexecuted test definition is not live deployment or operating acceptance.
