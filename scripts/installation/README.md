# Fresh Linux installation

This package prepares a new Ubuntu 24.04 amd64 VM/server using the existing
systemd backend, workers and C++ gateway, bare-metal Asterisk, Docker Redis,
and an external PostgreSQL database. `external` frontend mode preserves the
documented Vercel topology. `linux` builds and serves both frontends on this
host. Choosing a mode does not move the existing production frontends.

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
`python_requirements_lock` | Full Linux/Python 3.12 dependency closure, exact `==` versions and SHA256 hashes. No URLs, nested requirement includes, index directives or unresolved version ranges.
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
and disabled Admin development authentication before any changes.

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

Production startup also requires a live `STRIPE_SECRET_KEY` and the Stripe SDK
in the hash lock, unless `STRIPE_BILLING_DISABLED=1` explicitly acknowledges a
non-billing environment. A set test key is refused even when that flag is set.
Disabling billing does not complete the Stripe deliverable. Development guard
bypasses, staging transfer proof flags/scopes, weakened caller-ID enforcement
and disabled strict tenant routing are refused before any host mutation. The
actual application production gate runs again after package installation.

In `asterisk.conf`, configure `runuser=asterisk`, `rungroup=asterisk`,
`astctlpermissions=0660`, `astctlowner=asterisk` and `astctlgroup=asterisk` in
their appropriate Asterisk sections. The installer creates the dedicated
`pjsip.d` directory with group inheritance and adds `talky` to `asterisk` so
existing trunk generation and CLI reload can work without running the entire
backend as root. `pjsip.conf` must include `#include pjsip.d/*.conf` and define
the transports needed by the real trunks. Existing generated configs must be
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
and the supplied certificate domain names before running:

```bash
sudo bash install_talklee.sh apply \
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
data in its own Docker volume. Its credential is in a read-only container
mount protected by the host's root-only `/etc/talky` directory, not in process
arguments. PostgreSQL is never installed on this application host.

Before starting application services, the installer connects using the runtime
DB identity and rejects superuser/BYPASSRLS/CREATEDB/CREATEROLE roles, non-TLS connections and
schema revisions that do not equal the checkout's Alembic heads. This is a
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
The new service-user profile requires gateway publication as `root:talky`
mode 0750 and retention of `/etc/systemd/system/*.service.d` overrides.
The existing `deploy_to_server.sh` hardcodes `root:admins`; qualify that
ownership boundary before using it with this new profile.

The bounded publication commands for an already-built candidate in the
existing accepted freeze/drain procedure are:

```bash
# Only after the release's source, migration, backup and zero-call gates.
# gateway_candidate must be the output of build_voice_gateway_release.sh
# from the exact reviewed checkout, not an arbitrary old binary.
sudo install -d -o root -g talky -m 0750 /opt/talky/runtime/bin
sudo install -o root -g talky -m 0750 "$gateway_candidate" /opt/talky/runtime/bin/.voice_gateway.next
sudo mv -f /opt/talky/runtime/bin/.voice_gateway.next /opt/talky/runtime/bin/voice_gateway
```

These commands only publish the compatible binary; they do not authorize or
replace the remaining release procedure. Do not run the unchanged old deploy
script on this profile and try to repair ownership after its restart.

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
literal environment parsing and both proxy modes. Actual package installation,
systemd startup, nginx/TLS, Docker volume permissions, Linux frontend builds,
database access and calls still require the clean Ubuntu acceptance rehearsal.

Reference installation behavior was checked against the official
[Docker Ubuntu installation documentation](https://docs.docker.com/engine/install/ubuntu/)
and [Node release archive](https://nodejs.org/en/download/archive/v22.22.0).
Ubuntu's `docker.io` / `docker-compose-v2` distribution packages are used here;
do not mix them with Docker's separate `docker-ce` repository package family.
