# Local infrastructure deliverables

The user requested local preparation of database separation/recovery and Linux
installation after identifying Stripe and Admin as existing workstreams.
This change adds the local implementation and handover package. Production
access, migration, deployment, DNS changes, paid provider actions and calls
were not performed.

## Implementation

- `install_talklee.sh` and `scripts/installation/` provide a plan by default,
  local input validation and explicit fresh-host application. The target is
  Ubuntu 24.04 amd64, with external PostgreSQL, existing systemd application
  services, C++ gateway, Asterisk, Redis, nginx and restricted ingress.
  External or Linux frontends are explicit configuration choices.
- Installation consumes reviewed immutable source and dependency pins, checks
  production configuration, separates migration credentials, runs application
  services under `talky`, validates database TLS/role/schema, and writes version
  and health receipts. Existing-host updates are refused.
- `scripts/database/` provides read-only audit, snapshot-consistent logical
  backup with atomic publication/checksum/size checks, guarded isolated restore,
  row-security checks, age/checksum monitoring and opt-in scheduling examples.
- `backend/Alembic/env.py` now passes the original PostgreSQL DSN directly to
  asyncpg. Verified TLS parameters and URL-encoded passwords are preserved;
  optional `MIGRATION_DATABASE_URL` separates migration and runtime identities.
  Explicit empty migration credentials fail instead of falling back.
- `INSTALLATION.md`, the database recovery guide, protected input examples and
  handover record document setup, recovery, updates and remaining acceptance.

Existing unrelated working-tree changes were preserved. The source baseline
was `a0706b6f037aee1d24160867060a23c7b9eee720`; this implementation remains a local
working-tree change, not a committed, deployed or accepted release.

## Verification

Final verification passed **58 tests**: 28 installer checks, 23 recovery safety
checks and seven migration-connection checks. The real PostgreSQL 16.1 local
rehearsal passed eight scenarios: snapshot consistency during a concurrent
commit, archive age/checksum, separate-cluster restore, two-tenant RLS checks,
existing-target refusal, source-cluster refusal, failed-size publication
protection and corrupt-archive rejection. The successful rehearsal's temporary
clusters were stopped and removed. Bash syntax, default read-only plan, documentation links, JSON and
unchanged open readiness statuses also passed.

The real rehearsal exposed a Windows read-only-file `fsync` error; the repair
uses a writable non-truncating handle and has a byte-preservation regression.
Snapshot startup/shutdown failures also have process/handle cleanup regressions.
The successful final rehearsal used the repaired files.

The interrupted earlier fixture `test-output/database-recovery/synthetic-qcle666d`
is stopped and retained. Automatic execution review rejected its verified,
scoped PowerShell cleanup with `blocked by policy`. No synthetic PostgreSQL
server remained running at the final process check. The retained fixture uses
only synthetic data and is not included in the delivered source or evidence.

Final bounded verification and source hashes are recorded in the adjacent
`artifacts/local-infrastructure-20261027/verification.json`. The checks cover
installer refusal boundaries and production-gate parity, backup/restore safety,
and the real SQLAlchemy-to-asyncpg connection boundary without network access.
The local PostgreSQL rehearsal uses two disposable loopback clusters and
synthetic data; its receipt explicitly excludes production and full-schema
qualification.

Commands:

```powershell
python -m unittest discover -s scripts/installation -p test_install.py -v
python -m unittest discover -s scripts/database/tests -p "test_*.py" -v
$env:PYTHONPATH = (Resolve-Path 'backend/tmp/db-connection-test-deps').Path
python -m unittest discover -s backend/tests/unit -p test_alembic_database_connection.py -v
python scripts/database/tests/local_postgres_rehearsal.py --bin-dir 'C:/Program Files/PostgreSQL/16/bin' --run --receipt test-output/database-recovery/local-rehearsal.json
```

The migration boundary suite used task-local SQLAlchemy 2.0.25, asyncpg 0.29.0
and Alembic 1.13.1. It intercepts connections before sockets and does not claim
a real managed-database TLS handshake. Test dependencies were installed in an
ignored task directory; application requirements were not changed.

## Limits and next operating acceptance

OP10 and OP12 remain in progress. Obtain the designated independent database
and backup storage, target VM, real domain/carrier settings, approved dependency
locks and recovery objectives. Then demonstrate an authorized production-shaped
restore, full runtime grants, recording/key recovery, migration compatibility,
clean Ubuntu installation, update/rollback, reboot, controlled call and actual
alert delivery. Configure retention/PITR where supported, capacity monitoring
and certificate renewal with named owners.

The installer's new service-account profile differs from the existing
production deploy script's `admins` ownership. Its complete update procedure
needs qualification on the rebuilt host; a binary-publication snippet alone
does not establish release acceptance. Frontend hosting selection remains an
operator input. Required walkthrough videos must be captured from successful
rehearsals. No four-deliverable completion or feature-freeze exit is claimed.
