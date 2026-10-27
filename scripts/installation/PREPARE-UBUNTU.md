# Prepare a complete Ubuntu installation

Use this with the [clone-first installation guide](../../INSTALLATION.md).
Prepare inputs before running `apply`. The target application server must be
Ubuntu 24.04 amd64 with systemd and no existing Talky, nginx, Asterisk or Docker
installation. Keep console access available while qualifying its firewall.
Do dependency qualification and database preparation on a separate preparation
machine so these tasks do not turn the target into a partly installed server.

Set `frontend_mode` to `linux` to install the dashboard and Admin on this host.
The `external` mode skips their builds and hosting. Neither choice changes an
existing production deployment or its DNS automatically.

## Files the release needs

The full checkout must include the following matching source at one reviewed
commit; the installer kit alone does not include the application:

- `backend/app/`, `backend/config/`, `backend/systemd/`,
  `backend/deploy/healthwatch.sh` and the backend scripts, including
  `build_voice_gateway_release.sh` and `trunk_live_status_updater.py`.
- `backend/database/complete_schema.sql`, `backend/alembic.ini` and the complete
  `backend/Alembic/` migration tree.
- `services/voice-gateway-cpp/` with its CMake files, source, headers and tests.
- `Talk-Leee/` and `Admin/frontend/`, including their source, assets,
  `package.json` and `package-lock.json`, for Linux frontend mode.
- `install_talklee.sh` and `scripts/installation/` from the same commit.

Do not copy `node_modules`, a Windows virtual environment or an old gateway
binary to Ubuntu. The installer builds these from the release inputs.

## Separate reusable release files from environment inputs

| Input | Preparation and owner |
| --- | --- |
| Full source SHA | Release maintainer publishes the reviewed commit. A branch name alone can move. |
| Python 3.12 hash lock | Release includes `requirements-linux-py312.lock` and its `.provenance.json`; the helper verifies their requirements/lock hashes before copying. `backend/uv.lock` is not that installer lock. Check provenance for actual Ubuntu installation evidence. |
| Exact Ubuntu package map | The helper collects candidate versions for every name printed by `install.py packages` against the preparation host's Ubuntu 24.04 metadata. Review the repositories/versions and preserve artifacts or an approved mirror. |
| Redis image digest; Node version/archive SHA256 | The helper retrieves the Redis manifest digest and official Node archive checksum, or accepts supplied immutable values. Downloads still require qualification. Node is required only in Linux frontend mode. |
| External database endpoint, roles and trusted CA | Database owner provisions the database and approves the bootstrap or restore, grants and access tests below. |
| Runtime secrets | Environment owner supplies approved provider, SMTP, billing, authentication and encryption credentials. Retain original encryption keys for restored data. |
| API, dashboard and Admin domains; TLS chain/key | Environment owner supplies working DNS and certificates covering the hosted names, with a renewal owner. |
| Asterisk files and network allowlists | Telephony/network owner supplies real carrier configuration, caller ID, management/carrier/media CIDRs, SIP/RTP ports and NAT addresses. |

Generating version lists, hashes and a configuration draft is automated by the
preparation helper. It does not qualify those artifacts or create the external
accounts, database permissions, carrier authorization, DNS or secrets.
See the [exact input formats and pin preparation](README.md#qualified-inputs).

## Assemble protected configuration

From the clean release checkout on Ubuntu 24.04 amd64, refresh the approved
APT metadata and run:

```bash
sudo apt-get update
sudo python3 scripts/installation/prepare.py --source "$PWD" --output /secure/talky
```

The output must be a new directory outside both the checkout and `/opt/talky`.
The helper creates it mode `0700`, writes files mode `0600`, and refuses
existing output. It needs the bundled Python lock and provenance to match the
selected `backend/requirements.txt`; missing/mismatched artifacts stop
preparation before creating output. No Python package or application service
is installed by this command.

The output includes `apt-versions.json`, the verified Python lock/provenance,
`preparation.json` and these templates:

| Example | Populated file in the example deployment |
| --- | --- |
| `install.example.json` | `/secure/talky/install.json` |
| `backend.env.example` | `/secure/talky/backend.env` |
| `dashboard.env.example` | `/secure/talky/dashboard.env` |
| `admin.env.example` | `/secure/talky/admin.env` |

The helper sets absolute input paths, the clean source SHA and
`frontend_mode=linux`. Review `preparation.json` and complete the generated
files using a protected editor, for example:

```bash
sudoedit /secure/talky/install.json /secure/talky/backend.env \
  /secure/talky/dashboard.env /secure/talky/admin.env
```

Replace the example domains, documentation CIDRs and credentials. Supply the
certificate/key/CA files and the reviewed Asterisk directory at the configured
paths. Keep `database_prepared=false` until the database checks below are
complete. Do not overwrite a populated input directory to refresh metadata;
prepare a new directory, review differences and transfer secrets through the
approved protected process.

Populate the runtime environment for the actual selected providers and match
its URLs, CORS origins, Asterisk ARI identity and ports to the configuration.
The runtime file must not contain `MIGRATION_DATABASE_URL`. The frontend files
contain public build settings only; never put service credentials in
`NEXT_PUBLIC_*` or `VITE_*` variables. Paid-use acceptance requires the approved
live Stripe account configuration. The existing production gate also permits
a Stripe test environment when `STRIPE_TEST_MODE_IN_PRODUCTION=1` explicitly
acknowledges the test key, or a non-billing environment when
`STRIPE_BILLING_DISABLED=1` is explicitly selected. Test billing takes no real
payment; neither test billing nor disabled billing passes paid-use acceptance.

## Prepare the external database

The installer never creates a PostgreSQL server or database, loads a schema,
restores customer data or runs migrations. Its final database preflight checks
the runtime role, TLS and revision marker. Those checks cannot replace database
preparation or establish that the runtime role has all required permissions.

Choose exactly one preparation path:

1. **New environment with no existing data:** use the maintained schema and
   conservative Alembic floor below on a newly created, empty database.
2. **Recovery of existing data:** follow the [database recovery procedure](../../docs/production-readiness/database-recovery.md)
   using the approved archive, preserved keys and matching source revision.
   Preserve the archive's real revision; do not bootstrap over it or restamp it
   as a fresh database. Rehearse any upgrade separately on an isolated copy.

The database owner must provision the database, a migration/owner identity and
a separate runtime login with `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`.
The server needs `uuid-ossp` and `pgcrypto` support. The migration identity must
be permitted to create the schema and required extensions, or the DBA must
install the extensions first. Use direct verified-TLS connections whose
hostname matches the server certificate; keep the database off the application
host and restrict network access to the intended clients.

For the canonical fresh path, the preparation machine needs the qualified
Linux/Python 3.12 environment with the release's hash-locked dependencies,
PostgreSQL clients and the full source checkout. Use a protected libpq service
named `talklee_migration` and a mode-0600 `PGPASSFILE` for `psql`; the
[service-file example](../database/pg_service.conf.example) explains the format.
The service contains host, port, database, user, `sslmode=verify-full` and
`sslrootcert`, but no password. Export `PGSERVICEFILE` and `PGPASSFILE` to their
absolute protected paths. The owner must first verify that this service names
the new empty database intended for this release.

From `backend/`, with that qualified environment activated, the schema
sequence is:

```bash
# Only the designated NEW, EMPTY database. Stop if schema loading fails.
psql 'service=talklee_migration' -X -v ON_ERROR_STOP=1 \
  -f database/complete_schema.sql
```

After successful loading, provide the SAME database's migration DSN to
Alembic through the environment. This private-shell example reads it without
echo or a password in command arguments/history; use an approved secret-manager
injection instead where available. The DSN needs verified TLS and an existing
CA path on the preparation machine, which can differ from the installed
application's CA path.

```bash
(
  set -e
  read -r -s -p 'Migration DSN for the same new database: ' MIGRATION_DATABASE_URL
  printf '\n'
  export MIGRATION_DATABASE_URL
  python -m alembic heads --verbose
  python -m alembic stamp 0008_tenant_voice_tuning
  python -m alembic upgrade head
  python -m alembic current --verbose
)
```

Record the actual head from the selected source and verify the database
revision matches it. The historical `MIGRATIONS.md` paragraph naming `0035`
is not a current release pin: later migrations exist. Never replace the
`0008_tenant_voice_tuning` floor with `head` to skip migrations. Do not run
archived SQL migrations, `fresh_schema.sql` or development seed scripts as
additional installation steps. A failed bootstrap needs diagnosis and a new
empty database; these instructions do not authorize dropping an existing one.

The repository does not yet supply a complete, independently qualified grant
script for distinct migration-owner and runtime roles. The DBA must record and
apply the reviewed database/schema, table, sequence and function permissions,
including how future migration-created objects receive the required grants.
Do not replace this work by granting the runtime role superuser, BYPASSRLS or
membership in the migration role. Run representative application reads and
writes with the real runtime role and populated own/foreign-tenant fixtures to
verify permissions and tenant isolation. Review bootstrap plan/catalog data
against the approved billing configuration; schema seeds are not proof of a
working Stripe subscription or a configured administrator account.

Only after these checks set `database_prepared=true`. If restored data is used,
also preserve media and encryption keys and resolve pending jobs before starting
workers. Do not connect the full installer to a recovery scratch database that
still contains actionable customer jobs. Migration credentials belong only in
the operator's preparation environment or the separately provisioned
`/etc/talky/migration.env` for later reviewed migrations, never in backend runtime
or frontend files.

## Install and record the result

On the new application host, enter a root login shell and set the required
`0022` umask before applying. Replace the checkout path with its actual
absolute location; root's home directory differs from the operator's home:

```bash
sudo -i
umask 0022
cd /absolute/path/to/talklee-install-source
bash install_talklee.sh validate --config /secure/talky/install.json
bash install_talklee.sh apply \
  --config /secure/talky/install.json \
  --source "$PWD" \
  --confirm-new-host
```

The umask keeps installed source directories readable by the `talky` service
account. Secret files still receive explicit protected modes. Git trust for
the operator-owned checkout is scoped to the exact selected source by the
helper/installer; no global `safe.directory=*` exception is required.

`validate` reads local inputs only. `apply` installs and starts the services,
checks the database and local readiness, and probes the configured HTTPS
names against the new host. Confirm real browser access and DNS separately;
an internal HTTPS probe alone does not prove public DNS or external routing.

Preserve `/var/lib/talky-install/receipt.json`, `apt-installed.tsv` and
`python-installed.txt`, the release/pin identities and the operator's results.
Reboot, verify services and both frontends, reconcile the approved telephony
configuration, then make only the explicitly authorized controlled call.
Record the received caller ID, transcript/outcome and recovery/backup checks.
The receipt intentionally leaves controlled-call and production-recovery
acceptance false; an independent engineer must reproduce the documented
installation without relying on unrecorded setup knowledge.
