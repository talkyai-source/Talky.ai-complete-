# Updates and recovery on the fresh Ubuntu profile

`fresh_release.py` is the bounded release path for a host created by this
repository's Ubuntu 24.04 amd64 installer with both frontends on Linux. It is
separate from the legacy `admins` deployment script. This implementation is
awaiting its full disposable-VM update/recovery acceptance; unit and filesystem
tests alone do not qualify it for a customer release.

It preserves the existing OS packages, Node version, Redis image/network/data,
firewall, domains, certificates and carrier topology. Changing those requires a
separately reviewed host rebuild. Run only one coordinated migration operator
per database: the local release lock does not fence another host or DBA.

## Prepare the exact candidate

Retain a reviewed clean root-owned checkout with the full Git history needed
to prove that the candidate descends from the installed source. The runtime
receipt and `git -C /opt/talky rev-parse HEAD` identify the current source;
the original installation receipt is retained as the bootstrap record.

Run inside a root shell with `umask 0022`. Commands below use illustrative
values; substitute the real reviewed SHAs and existing certificate names.

```bash
umask 0022
export PYTHONDONTWRITEBYTECODE=1
python3 /root/reviewed-candidate/scripts/installation/fresh_release.py prepare \
  --source /root/reviewed-candidate \
  --sha FULL_REVIEWED_CANDIDATE_SHA \
  --expected-current-sha FULL_INSTALLED_SHA \
  --domains api.example.com app.example.com admin.example.com
```

Preparation creates `/opt/talky-releases/<SHA>` and leaves the running release
alone. It installs the candidate's exact hashed Python closure and checks it,
builds/tests the native gateway, and runs locked installation plus production
builds for both frontends. Frontend builds use a separate `talky-build` system
account with no login shell, privileged supplementary groups or application
UID. Its write access is removed before preparation finishes. Protected
environment copies are private from creation; they are never shell-sourced.
Privileged Python/native preparation uses the root operator's home and disables
Python user-site and pip configuration discovery, so the running application
cannot supply configuration or executable hooks through its writable home.
The [pip configuration contract](https://pip.pypa.io/en/stable/topics/configuration/#pip-config-file)
defines the null config-file setting used for this boundary.

The private preparation receipt binds source, one Alembic head, both frontend
builds, native binary and Python environment, the installed configuration and
key fingerprints. Only Next's declared runtime cache is excluded from build
artifact hashing. Existing migration files must retain their exact published
bytes, and the old head must occur in the candidate's forward graph. A failed
prepare retains its directory for inspection and cannot overwrite it on retry.

Keep enough disk for the complete candidate, prior release and a fresh backup.
Do not prune old release directories: their explicit recording/upload/state
paths can remain the shared data location for later releases.

## Freeze, back up and activate

Use the real topology's ingress and outbound freeze, four-counter drain and
candidate-bound immutable manifest described in
[DEPLOYMENT.md](../../docs/DEPLOYMENT.md#production-drain-manifest-contract).
The existing version-1 and owner-authorized version-2 verification code is
reused, including freshness, exact digest, exact candidate and single-use
replay rules. A synthetic test manifest is not production approval.

Supply protected libpq services for the same runtime database:

- A restricted runtime login matching the installed `DATABASE_URL`.
- A separate schema-owner login matching `/etc/talky/migration.env` if a
  forward migration is required.
- The approved backup login with the read/snapshot/control-system access
  required by [database recovery](../database/README.md).

The service file and explicit password files must be root-owned mode `0600`.
Use verified TLS with the installed CA. Passwords stay in protected files,
never command arguments. Runtime, owner and backup services must select the
same explicit endpoint/database. URL query options cannot override the
endpoint, database, identity or TLS policy.

```bash
python3 /root/reviewed-candidate/scripts/installation/fresh_release.py activate \
  --sha FULL_REVIEWED_CANDIDATE_SHA \
  --manifest /root/release/drain.json \
  --manifest-sha256 EXACT_SHA256_OF_MANIFEST_BYTES \
  --service-file /etc/talky/pg_service.conf \
  --runtime-service talklee_runtime \
  --owner-service talklee_owner \
  --backup-service talklee_backup
```

Add `--allow-forward-migration` only for an explicitly reviewed forward schema
change. A changed schema without that flag is refused before consuming the
manifest or stopping services.

Activation checks the prepared artifacts and installed configuration again.
It records and disables all known application/backup/retention/synthetic timers
before confirming maintenance oneshots are fully stopped. Unknown application
timers are refused. The existing observer must then read four complete zero
counts; that observation does not replace the real external freeze.

With application processes stopped, the existing recovery implementation takes
a fresh consistent database backup. The updater independently reopens and
checks the published archive, checksum, receipt, actual source endpoint and
schema. This is local backup evidence, not an off-host copy or a restore claim.
Required off-host/key recovery and RPO/RTO evidence remain separate.

Before any migration, the old schema's complete runtime-grant verifier checks
the owner and runtime logins. The actual migration DSN also has to prove its
restricted owner identity, current/session user, endpoint, database, TLS and
schema. The journal records `migration_started` **before** Alembic runs.
Forward migration is followed by the candidate's reviewed grants profile and
exact-head check; no downgrade command exists.

The first activation moves the original `/opt/talky` directory into the
root-owned release store on the same filesystem. It then publishes the stable
`/opt/talky` symlink atomically. Applications stay stopped during the initial
directory-to-link transition. Later switches replace only that link. Protected
configuration and the explicit data directories are shared with the prior
release; seeded candidate data is retained privately rather than merged over
customer files. No recordings, uploads or Redis volume are deleted.

Candidate systemd files retain the installer's `talky` application and root
operator identities and `NoNewPrivileges` protections. The updater preserves
fresh-host drop-ins, publishes only `APP_VERSION`, reconciles Asterisk through
the existing guarded wrapper, then verifies gateway build/protocol, actual API
process version, readiness and all three local HTTPS virtual hosts. It checks
build artifacts, keys, configuration metadata and data-directory identities
again before publishing a successful receipt.

## Failure and recovery

Private journals, protected configuration copies, database archives and command
failure diagnostics remain under `/var/lib/talky-release`, root-only. Raw
diagnostic files can contain secrets and must not be uploaded as public CI
artifacts or pasted into chat. Error messages do not echo subprocess output.

Before migration starts, failure attempts to restore the prior source and unit
files, then reruns exact-version and health checks. The schema must still equal
the old head. Once migration has started—even when Alembic reports failure—the
updater forbids automatic or manual code rollback through this command. It
keeps application services stopped for a reviewed code-forward release or a
separately approved compatible restore. A failed recovery is not success.

An interrupted same-schema release can be recovered using its private journal
and a **new** real manifest bound to the previous source SHA:

```bash
python3 /root/reviewed-candidate/scripts/installation/fresh_release.py recover \
  --journal /var/lib/talky-release/transactions/TRANSACTION_ID/journal.json \
  --manifest /root/release/recovery-drain.json \
  --manifest-sha256 EXACT_RECOVERY_MANIFEST_SHA256
```

Successful activation or recovery leaves producer/maintenance timers disabled,
including across reboot. It does not reopen external traffic. After the owner
has completed the approved health/business checks and chosen the reopening
window, restore exactly the recorded enabled/active timer states:

```bash
python3 /root/reviewed-candidate/scripts/installation/fresh_release.py resume-schedules \
  --journal /var/lib/talky-release/transactions/TRANSACTION_ID/journal.json \
  --acknowledge-traffic-window
```

This command rechecks the current source, schema and health. The operator still
owns the external ingress/origination controls. Restoring a synthetic-call timer
can place its configured real call; do not run this command before its approved
window. The release receipt does not claim customer-call, provider, billing,
off-host recovery or load acceptance.

## Required disposable-VM acceptance

Qualify against a pinned successful fresh-install base, using separate synthetic
TLS PostgreSQL owner/runtime/backup identities. Retain exact candidate/base SHAs
and sanitized receipts. Required cases are:

1. Prepare and activate a real descendant, proving matching API/gateway source,
   both frontends, immutable artifacts, protected keys, retained data and prior
   timer disposition before and after reboot.
2. Induce a candidate startup failure with unchanged schema and prove that the
   prior release is restored, healthy, correctly identified and recorded.
3. Induce failure after the forward-migration boundary and prove that services
   stay stopped and code rollback/downgrade are refused.
4. Verify rejected artifact/config/DSN drift, a currently activating maintenance
   job, repeated manifest and mismatched backup before source activation.

The local tests exercise actual Linux files, atomic links, Git and manifests,
plus explicitly mocked service-fault boundaries. They do not claim these full
VM acceptance cases have already passed.
