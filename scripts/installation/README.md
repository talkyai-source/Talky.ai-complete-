# Fresh Linux installation

This package prepares a new Ubuntu 24.04 amd64 VM/server using the existing
systemd backend, workers and C++ gateway, bare-metal Asterisk, Docker Redis,
and an external PostgreSQL database. The example defaults to `linux`, which
builds and serves both the dashboard and Admin on this host. Set
`frontend_mode` to `external` to retain the documented Vercel topology.
Choosing a mode does not move the existing production frontends.

If an installation command fails, its bounded output is retained in a root-only
`/var/lib/talky-install/command-failure-*.json` file. The terminal reports the
private path and exit status, without printing command arguments, environment
or tool output. Inspect it as the authorized host operator; tool output may
contain credentials, so do not upload the raw file. The marked synthetic CI VM
redacts its generated secrets before retaining any diagnostic excerpt.

The default command is a read-only plan. It does not install packages, read
secret inputs, access a database, contact a server, or make calls:

```bash
bash install_talklee.sh
bash install_talklee.sh plan --config /secure/talky/install.json
```

On Windows the same plan is available through
`python scripts/installation/install.py plan`. `apply` refuses Windows,
containers without systemd, non-amd64 machines and other Ubuntu releases.

## Qualified inputs

Copy `install.example.json` outside the repository and complete every field.
The example deliberately contains documentation networks and placeholders;
it cannot be applied accidentally. `validate` checks the inputs without
installing anything or contacting services:

```bash
python3 scripts/installation/install.py packages
bash install_talklee.sh validate --config /secure/talky/install.json
```

Required input | Contract
--- | ---
`source_sha` | Full reviewed 40-character commit; invoking checkout must be clean and exactly at that commit, including this installer.
`backend_env_file` | Mode 0600 regular dotenv file; no shell commands, duplicate keys or escapes. Runtime credentials only; `MIGRATION_DATABASE_URL` is rejected.
`database_prepared` | Set true only after separate bootstrap/restore/migration and schema acceptance; installer performs no database migration or restore.
`database_ca_file` | Trusted CA bundle copied to `/etc/ssl/certs/talky-db-ca.pem`; runtime DSN must specify that path and `sslmode=verify-full`.
`python_requirements_lock` | Full Linux/Python 3.12 dependency closure, exact `==` versions and SHA256 hashes. Standard string environment-marker comparisons joined by `and`/`or` are accepted, including the repository's Linux/x86_64 denoiser marker. No URLs, nested includes, index directives or unresolved version ranges.
`apt_versions_file` | JSON mapping each name from `packages` to an exact Ubuntu package version. Review against the target Ubuntu repositories and preserve the package artifacts/mirror for later reproduction.
`redis_image` | Approved Redis image tag with `@sha256:` manifest digest. No unpinned `latest` or tag-only input.
`asterisk_config_directory` | Reviewed `.conf` files for the real carrier/PBX topology, including `asterisk.conf`, `pjsip.conf`, `extensions.conf`, `http.conf`, `ari.conf`, `rtp.conf`, `modules.conf`. Lab defaults are rejected.
`tls_certificate_file`, `tls_private_key_file` | PEM certificate chain and private key covering all hosted domain names; key mode 0600. Renewal remains an operator responsibility.
Network fields | Actual management/carrier/media CIDRs and matching SIP/RTP/SSH ports. `0.0.0.0/0` and `::/0` are refused for restricted services.
Linux frontend inputs | Public URLs and secret-free browser build configuration in separate protected environment files; approved Node 22 >=22.18 version and official Linux x64 archive SHA256.

APT versions can be collected on a disposable Ubuntu 24.04 preparation VM
using `apt-cache policy <package>` / `apt-cache madison <package>`. Store the
selected versions as JSON, for example `{"asterisk": "<exact-version>", ...}`.
The installer does not select a moving package candidate automatically.

Produce the Python hash lock on the same Linux/Python version using a pinned
version of `pip-tools`, with `pip-compile --generate-hashes --output-file
requirements-linux-py312.lock backend/requirements.txt`. Review the complete
resolved closure, run the relevant application tests and retain the exact lock.
The repository's small `backend/uv.lock` does not currently contain that closure.
Use `pip-compile --no-emit-index-url --no-emit-trusted-host` if the environment
would otherwise emit index configuration. Keep index credentials in the
approved pip configuration, outside the lock and repository.

The protected runtime environment must contain the existing application's
provider/configuration keys in addition to the installer prerequisites. The
installer verifies these additional deployment constraints:

```dotenv
ENVIRONMENT=production
TELEPHONY_ADAPTER=asterisk
DATABASE_URL=postgresql://talky_runtime:URL_ENCODED_PASSWORD@db.example.net/talky?sslmode=verify-full&sslrootcert=/etc/ssl/certs/talky-db-ca.pem
REDIS_URL=redis://:AT_LEAST_32_URL_SAFE_CHARACTERS@127.0.0.1:6379/0
FRONTEND_URL=https://app.example.com
API_BASE_URL=https://api.example.com
CORS_ORIGINS=https://app.example.com,https://admin.example.com
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
VOICE_GATEWAY_CALLBACK_HOST=127.0.0.1
ASTERISK_ARI_HOST=127.0.0.1
TELEPHONY_PJSIP_AUTO_RELOAD=on
CLEANUP_DRY_RUN=true
```

Leave `READ_DATABASE_URL` unset for this bounded installation profile; a
separate read database needs its own role/TLS/schema/location qualification.
Query parameters cannot override the database hostname, identity or SSL policy.
For Linux frontends, start from the adjacent `dashboard.env.example` and
`admin.env.example`. The installer checks production mode, the public API URL
and disabled Admin development authentication before any changes. Builds use
these reviewed environment files; exported variables from the invoking shell
cannot override the API URL or enable development authentication in a bundle.

Use distinct strong `JWT_SECRET`, `SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`,
`VOICE_GATEWAY_AUTH_TOKEN`, `TELEPHONY_METRICS_TOKEN`, a valid
`CONNECTOR_ENCRYPTION_KEY`, the retained `SECRETS_MASTER_KEY`, and the real
`ASTERISK_ARI_USER` / `ASTERISK_ARI_PASSWORD`. Environment values are not
printed. Asterisk `http.conf` must bind ARI to `127.0.0.1`; its port and user
must match the runtime environment. `rtp.conf` and SIP transport ports must
match the configured firewall ranges. Carrier NAT/external-address settings
belong in the reviewed supplied configuration.

Preserve the original `SECRETS_MASTER_KEY` and connector-encryption key when
restoring customer data; generating replacement keys makes stored credentials
unreadable. The installer neither generates nor rotates these keys.

Active billing requires the Stripe SDK in the hash lock and
`STRIPE_WEBHOOK_SECRET`, with either a live `STRIPE_SECRET_KEY` or a test key
plus the existing explicit `STRIPE_TEST_MODE_IN_PRODUCTION=1` acknowledgement.
The latter takes no real payment and retains the application's test-checkout
restrictions. `STRIPE_BILLING_DISABLED=1` acknowledges a non-billing environment
and does not need a key or Stripe SDK. Mock billing is rejected when billing
is active. These checks match the current application's production gate;
test/disabled billing does not complete the paid billing deliverable. Development guard
bypasses, staging transfer proof flags/scopes, weakened caller-ID enforcement
and disabled strict tenant routing are refused before any host mutation. The
actual application production gate runs again after package installation.

In `asterisk.conf`, configure `runuser=asterisk`, `rungroup=asterisk`,
`astctlpermissions=0660`, `astctlowner=asterisk` and `astctlgroup=asterisk` in
their appropriate Asterisk sections.
If `astmoddir` is explicitly supplied, it must be
`/usr/lib/x86_64-linux-gnu/asterisk/modules`, matching the supported Ubuntu
24.04 amd64 package. Omit it to use Asterisk's compiled package default.
The installer creates the dedicated
`pjsip.d` directory with group inheritance and adds `talky` to `asterisk` so
existing trunk generation and CLI reload can work without running the entire
backend as root. `pjsip.conf` must include `#include pjsip.d/*.conf` and define
the transports needed by the real trunks. `extensions.conf` must contain
`#tryinclude extensions.d/talky-inbound.conf`; this exact managed file is absent
on first startup and loaded after the guarded reconciler creates it. Other
generated dialplan directories or wildcards are not accepted by this profile.
Existing generated configs must be
reconciled from the restored authoritative database through the application's
supported lifecycle before controlled calls; the installer does not decrypt
or fabricate customer trunks.

Writable application paths are `/opt/talky/backend/recordings`,
`audio_files/storage`, `feedback_audio`, `logs`, `tmp`, `uploads`, and
`/opt/talky/runtime/state`. If the corresponding optional storage path variables
are set, they must match these paths. Source and the gateway executable remain
root-owned. Voice preview caches use each service's private temporary directory.

## Apply to the empty host

Prepare the external database first using the separate recovery procedure.
Preserve source, TLS material, keys and storage required by that environment.
On the new VM, clone the reviewed repository and check out the full SHA.
Place protected inputs outside `/opt/talky` and the source checkout. Verify DNS
and the supplied certificate domain names before running. Enter the root shell
with umask `0022` so the service account can read source directories; the
installer refuses other masks before mutation and stages secrets as mode 0600:

```bash
sudo -i
umask 0022
cd /path/to/clean/reviewed-checkout
bash install_talklee.sh apply \
  --config /secure/talky/install.json \
  --source /path/to/clean/reviewed-checkout \
  --confirm-new-host
```

`apply` refuses existing `/opt/talky`, installer state, Talky configuration or
active Asterisk/nginx/Docker/application services. It also checks that the
current SSH address/port stays allowed if `SSH_CONNECTION` is available.
Keep console access available for initial firewall qualification. Package
auto-start is inhibited until explicit configuration is installed.

The script creates a `talky` system account; application processes run under
it through systemd drop-ins. Redis binds only to host loopback and preserves
data in its own Docker volume. This fresh-host profile disables Docker's
automatic bridge and firewall manipulation and runs only Redis with host
networking, with Redis itself bound to `127.0.0.1`. It therefore does not add
private bridge routes that could shadow an external database subnet. Existing
Docker state or `/etc/docker/daemon.json` is refused before installation; this
is not a reconfiguration path for an existing Docker host. Adding other
containers needs a separate network/security review. Its credential is in a read-only container
mount protected by the host's root-only `/etc/talky` directory, not in process
arguments. PostgreSQL is never installed on this application host.

Before starting application services, the installer connects using the runtime
DB identity and rejects superuser/BYPASSRLS/CREATEDB/CREATEROLE roles, non-TLS connections and
role-switched sessions. Connection `options` overrides are refused, and
`public.alembic_version` must equal the checkout's nonempty Alembic heads. This is a
preflight, not a substitute for cross-tenant isolation acceptance.

Success records `/var/lib/talky-install/receipt.json`, `apt-installed.tsv` and
`python-installed.txt`. The receipt records source/lock identity and health
success, while explicitly retaining `controlled_call_verified=false` and
`production_recovery_verified=false`. If installation fails, preserve the
partially installed VM/state for diagnosis; there is no destructive automatic
cleanup or unsafe rerun. Correct the input and repeat on another empty VM.

## Updates and recovery

This bootstrap command is never an update command. Preserve the established
freeze/drain, backup, compatible-schema and exact-commit release procedure.
The [fresh-host release guide](FRESH-RELEASE.md) provides the separate
`fresh_release.py` prepare, activate, recover and resume-schedules commands.
It binds actual builds to the selected source, preserves the `talky` identity
and installed overrides, and keeps schema-safe rollback separate from
post-migration recovery. Read its actual VM qualification status before using
it for a customer release. The older `deploy_to_server.sh` continues to assume
the `admins` host profile and must not be used unchanged on this host.

Migrations use a required separate `/etc/talky/migration.env` file that the
installer intentionally does not create. Before a later reviewed migration,
an operator provisions mode 0600 content containing only the privileged
`MIGRATION_DATABASE_URL` with the approved verified-TLS settings. The migration
unit refuses a missing/empty value. The API/workers never load this file.

Backup scheduling is supplied by `scripts/database/` and must be configured
for the approved storage destination, retention and alert receiver. This
installer does not pretend to establish an off-host backup, an alert receiver,
PITR, a real restore, certificate renewal or carrier approval. Complete those
operating acceptance items before recording the deliverables as done.

## Local verification

```bash
python -m unittest discover -s scripts/installation -p test_install.py -v
bash -n install_talklee.sh
```

These checks cover plan isolation, destructive-entry guards, unsafe database
URLs/roles, leaked migration credentials, dependency pins, network allow-list
validation, Asterisk lab defaults/path traversal, secret-redacted errors,
literal environment parsing and both proxy modes. The retained
[blank Ubuntu acceptance](../../docs/sessions/2026-10-28-blank-ubuntu-acceptance.md)
records actual package installation, Linux frontend builds, systemd startup,
TLS, service identities, database routing and reboot for its exact source.
Controlled calls, production recovery and independent operating handover remain
separate acceptance requirements.

Reference installation behavior was checked against the official
[Docker Ubuntu installation documentation](https://docs.docker.com/engine/install/ubuntu/)
and [Node release archive](https://nodejs.org/en/download/archive/v22.22.0).
Ubuntu's `docker.io` / `docker-compose-v2` distribution packages are used here;
do not mix them with Docker's separate `docker-ce` repository package family.
