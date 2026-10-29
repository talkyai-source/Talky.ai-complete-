# Talky.ai / Talk-Lee: Ubuntu installation and operations guide

Status: written 2026-10-30 against `origin/main` at commit `0ef59596` plus the
uncommitted Support-system backend (migration `0082_support_system`).

This guide installs and operates the platform on a fresh **Ubuntu 24.04
amd64** server. Every command, path, unit name, script and variable name in it
was read from a file in this repository. Where something could not be checked,
the text says **Not verified** and gives the reason. Nothing here was run on a
server: no installer, build, migration or test was executed while writing it.

Read this first:

- Nothing in this guide proves that a clean-server install has passed
  acceptance. The repository's own `INSTALLATION.md` says the same.
- The production-readiness feature freeze in `AGENTS.md` is active. This guide
  documents what exists. It does not add features.
- Secrets never appear in this guide. Environment variables are listed by name
  only. Never paste a real value into a ticket, chat or commit.

## Contents

1. Purpose, scope and the two supported layouts
2. Architecture overview
3. Prerequisites
4. Fresh install with the repository installer
5. Manual reference install
6. Support system on a new server
7. Verification checklist
8. Upgrades, deploys, backups and monitoring
9. Security hardening checklist
10. Troubleshooting
11. What exists vs what was claimed
12. Appendix: environment variables and systemd units

---

## 1. Purpose, scope and the two supported layouts

### 1.1 What this guide is for

Use this guide when you must build a **new** Talky.ai server from the git
repository. It covers:

- the repository installer (`install_talklee.sh` and `scripts/installation/install.py`),
- the same steps done by hand, so an operator understands each one,
- the Support system added by migration `0082_support_system`,
- checks after install, upgrades, backups, hardening and troubleshooting.

It does not cover:

- creating the carrier account, SIP credentials or phone numbers,
- creating DNS records or issuing TLS certificates,
- moving an existing production database,
- load or capacity qualification. No capacity rating exists in the repo.

### 1.2 What production actually runs today

These facts come from `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md` and
`docs/RUNBOOK.md`.

| Layer | How it runs today | Source |
|---|---|---|
| API, dialer/voice/reminder workers, C++ gateway | systemd units from a git checkout at `/opt/talky` | `backend/systemd/` |
| PostgreSQL 15 and Redis 7 | docker compose containers, project `talky` | `docker-compose.yml` on the box |
| SIP and media | Asterisk only, bare metal | `setup-asterisk.sh` |
| Dashboard (`Talk-Leee`) and Admin | Vercel, auto-deploy from `main` | not on the backend box |
| Host | one Hetzner bare-metal server | `docs/DEPLOYMENT.md` |

Other facts that matter:

- The production checkout is a **sparse checkout** that leaves out `Talk-Leee/`
  and `Admin/`. The exact sparse patterns are **Not verified** (not in the repo).
- Most backend units run as root. The gateway unit runs as `admins`.
- `docker-compose.yml` is a local-development file per `CLAUDE.md`. Its
  `postgres` service loads `backend/database/schema/baseline_2026-06-02.sql`
  on first start. Do not use that file as the new-server database design.
- The `telephony/` directory (OpenSIPS, Kamailio, rtpengine, FreeSWITCH) is
  modelled in the repo and **not deployed**. Do not install any of it.
- The repository also contains an installer profile for a *new* host. That
  profile differs from production in four ways: the services run as a `talky`
  account, PostgreSQL is **external** (not Docker), Redis is a loopback Docker
  container, and nginx terminates TLS on the host.

### 1.3 The two supported layouts

The installer config has one switch: `frontend_mode`.

| Setting | Where the dashboard and Admin run | What the installer does |
|---|---|---|
| `frontend_mode=external` | On Vercel (the documented topology) | Installs backend, workers, gateway, Asterisk, Redis, nginx for the API only. Does not build or serve frontends. |
| `frontend_mode=linux` | On this Linux host | Also installs Node, builds both frontends with `npm ci --include=dev` and `npm run build`, runs the dashboard as `talky-dashboard.service`, and serves Admin from nginx. |

Rules for choosing:

1. Choosing a mode does not move the existing production frontends or DNS.
2. In `external` mode the dashboard and Admin hostnames still must exist in the
   config and in `CORS_ORIGINS`. The installer checks they are distinct.
3. Pick the mode before you apply. The installer refuses to run twice on one
   host, so changing mode later means a new empty VM.

---

## 2. Architecture overview

### 2.1 Components

```text
 Browser --HTTPS--> Dashboard (Next.js, Talk-Leee)  --+
 Browser --HTTPS--> Admin (Vite/React build)        --+--> nginx (HTTPS) --> API :8000
                                                                              |
                          +-------------------------------+-------------------+
                          |                               |                   |
                     PostgreSQL                        Redis              AI providers
                  (RLS, tenant_id)               (loopback :6379)     (Deepgram, Groq,
                                                                        Cartesia, ...)
 Carrier --SIP/RTP--> Asterisk (ARI 127.0.0.1:8088) <--> C++ voice gateway 127.0.0.1:18080
                                                              |
                                                       HTTP callbacks to API
```

The gateway talks to the API over authenticated HTTP callbacks (PCMU 8 kHz,
20 ms frames). The gateway refuses to start without `INTERNAL_SERVICE_TOKEN`
and `VOICE_GATEWAY_AUTH_TOKEN` (`backend/scripts/build_voice_gateway_release.sh`
proves this with a negative start test).

### 2.2 Ports

| Port | Listener | Exposure | Source |
|---|---|---|---|
| 443, 80 | nginx | public (80 redirects to 443) | `install.py` `nginx_site()` |
| 8000 | `talky-api` (uvicorn) | loopback on the installer profile. **0.0.0.0 in the shipped unit.** | unit file; installer drop-in `20-loopback.conf` |
| 8001 | `talky-api-web` (dashboard API, telephony off) | loopback (`--host 127.0.0.1`) | `talky-api-web.service` |
| 18080 | C++ voice gateway | loopback | `talky-voice-gateway.service` |
| 8088 | Asterisk HTTP/ARI | loopback (`bindaddr = 127.0.0.1`) | `setup-asterisk.sh`, installer check |
| 5060 udp/tcp | Asterisk SIP | restricted to carrier CIDRs by the installer firewall | `setup-asterisk.sh`, `configure_firewall()` |
| 10000-20000 udp | Asterisk RTP | restricted to media CIDRs | `setup-asterisk.sh` `rtp.conf`; `install.example.json` |
| 32000-32999 | gateway-local media ports | loopback host | `ASTERISK_RTP_PORT_START/END` in `backend.env.example` |
| 6379 | Redis | loopback only | installer compose file |
| 5432 | PostgreSQL | external host. Docker postgres binds 127.0.0.1 in `docker-compose.yml`. | docs |
| 3000 | Next.js dashboard (Linux mode only) | loopback | installer `talky-dashboard.service` |

Production example: `setup-asterisk.sh` hardcodes `SERVER_IP="144.76.17.150"`
and the carrier host `sip3.blazedigitel.com`. Those are the current
production values. A new server must not reuse them blindly (see 5.9).

### 2.3 systemd units

All unit files live in `backend/systemd/`. Full schedule table is in the
Appendix (12.2).

| Unit | Kind | What it runs |
|---|---|---|
| `talky-api.service` | daemon | `uvicorn app.main:app` on port 8000. Owns telephony. `--workers 1`. |
| `talky-api-web.service` | daemon | Same app on 127.0.0.1:8001 with `TELEPHONY_ENABLED=false`. Serves dashboard traffic so call load does not block it. |
| `talky-voice-worker.service` | daemon, `Type=notify` | `python -m app.workers.voice_worker` |
| `talky-dialer-worker.service` | daemon, `Type=notify` | `python -m app.workers.dialer_worker` |
| `talky-reminder-worker.service` | daemon, `Type=notify` | `python -m app.workers.reminder_worker` |
| `talky-voice-gateway.service` | daemon | `/opt/talky/runtime/bin/voice_gateway --host 127.0.0.1 --port 18080` |
| `talky-trunk-status.service` | daemon | `scripts/trunk_live_status_updater.py --loop 10` as root. Refreshes SIP trunk evidence. |
| `talky-asterisk-reconcile.service` + `.timer` | oneshot, every minute | `deploy/asterisk-reconcile-on-drift.sh`. Applies website line/number/inbound changes to Asterisk only when they differ. |
| `talky-cleanup.service` + `.timer` | oneshot, 03:00 nightly | `python -m app.workers.cleanup_worker`. Dry run by default. |
| `talky-healthwatch.service` + `.timer` | oneshot, every 2 min | `deploy/healthwatch.sh`. Polls `/api/v1/healthz/workers`. |
| `talky-db-backup.service` + `.timer` | oneshot, 02:30 UTC | `deploy/db-backup.sh`. Works against the Docker postgres container. |
| `talky-inbound-synthetic.service` + `.timer` | oneshot, hourly | `deploy/inbound-synthetic-call.sh`. Places a carrier-hairpin test call. |
| `talky-support-jobs.service` + `.timer` | oneshot, every 15 min | `python -m app.workers.support_jobs`. No-op while `SUPPORT_ENABLED=false`. |
| `talky-migrate.service` | oneshot, manual | `alembic -c /opt/talky/backend/alembic.ini upgrade head`. Never enabled. |
| `talky-trunk-status.timer` | retired | Kept only for upgrade compatibility. `install-services.sh` disables it. |
| `talky.target` | target | Requires `talky-api` and the three Python workers. It does not include the gateway or `talky-api-web`. |

Timer-activated oneshots show `inactive (dead)` between runs. That is normal
(`docs/RUNBOOK.md`).

---

## 3. Prerequisites

### 3.1 Hardware and operating system

| Item | Requirement | Source |
|---|---|---|
| OS | Ubuntu 24.04 LTS, amd64, systemd running | `install.py` `ensure_fresh_host()` |
| Console | Out-of-band console access while the firewall is qualified | `INSTALLATION.md` |
| Python | 3.12 (`python3.12`, `-dev`, `-venv`) | `install.py` `PACKAGES`; CI `PYTHON_VERSION: "3.12"` |
| Node | 22, at least 22.18, only for `frontend_mode=linux` | `Admin/frontend/package.json` `engines`; installer regex |
| Disk | Not defined in the repo. Plan for recordings, logs and backups. | **Not verified** |
| CPU and RAM | Not defined in the repo. The units cap memory (API `MemoryMax=1536M`, workers `MemoryMax=768M`). | **Not verified** as a minimum |

No minimum CPU, RAM or call-capacity figure is published in the repo. Do not
quote one to a customer.

### 3.2 DNS and TLS

You need three distinct hostnames (the installer rejects duplicates):

| Config key | Example | Purpose |
|---|---|---|
| `api_domain` | `api.example.com` | Public API over HTTPS |
| `dashboard_domain` | `app.example.com` | Customer dashboard |
| `admin_domain` | `admin.example.com` | Admin panel |

- Point DNS at the new host before `apply`. The installer checks HTTPS with
  `curl --resolve`, so it does not prove public DNS.
- Supply a PEM certificate chain and private key that cover **all** hosted
  names. The installer copies them to `/etc/talky/tls/`. Renewal is yours.
- For a manual install `talky.conf` shows a Let's Encrypt (`certbot --nginx`)
  layout. Using certbot with the installer profile is **Not verified**.

### 3.3 Accounts and keys (names only)

Required by the production gate (`backend/app/core/prod_gate.py`) and the
installer validation:

| Area | Variable names |
|---|---|
| Database | `DATABASE_URL` (runtime role), `MIGRATION_DATABASE_URL` (operator only) |
| Redis | `REDIS_URL` |
| Auth and signing | `JWT_SECRET`, `SECRET_KEY` |
| Encryption | `SECRETS_MASTER_KEY`, `CONNECTOR_ENCRYPTION_KEY`, `CONNECTOR_ENCRYPTION_KEYS_OLD` |
| Service auth | `INTERNAL_SERVICE_TOKEN`, `VOICE_GATEWAY_AUTH_TOKEN`, `TELEPHONY_METRICS_TOKEN` |
| Voice providers | `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `CARTESIA_API_KEY` |
| Asterisk ARI | `ASTERISK_ARI_USER`, `ASTERISK_ARI_PASSWORD` |
| Billing | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (or `STRIPE_BILLING_DISABLED=1`) |
| Email | `EMAIL_USER`, `EMAIL_PASS`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` |
| Storage (recommended) | `S3_BUCKET_NAME`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL` |
| Google OAuth | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` |

Rules from the repo:

- `JWT_SECRET`, `SECRET_KEY`, `INTERNAL_SERVICE_TOKEN` and
  `VOICE_GATEWAY_AUTH_TOKEN` must each be at least 32 characters (installer).
- `INTERNAL_SERVICE_TOKEN` and `VOICE_GATEWAY_AUTH_TOKEN` must differ.
- `SECRETS_MASTER_KEY`: if you restore existing data, keep the **exact**
  original. A new value makes stored credentials unreadable. In production the
  service refuses to boot if it is unset (`backend/.env.example`).
- `CONNECTOR_ENCRYPTION_KEY` is a Fernet key. Generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  (command from `backend/.env.example`).
- A live Stripe key must start with `sk_live_` on the installer profile. Test
  keys are refused. `STRIPE_BILLING_DISABLED=1` is a non-billing decision and
  does not complete the billing deliverable.

### 3.4 External services you must have ready

| Service | Needed for | Notes |
|---|---|---|
| PostgreSQL server (separate host for the installer profile) | All data | Direct TLS connection, `sslmode=verify-full`, trusted CA file |
| SIP carrier or PBX | Calls | Real credentials and DID numbers. Not supplied by the repo. |
| Deepgram, Groq, Cartesia | Speech and language | Keys validated for presence only |
| SMTP mailbox | Verification and recovery emails | `backend.env.example` uses `smtp.office365.com` as an example |
| S3-compatible bucket | Recordings, feedback audio, Support attachments | Recommended. Local disk is limited in production (see 6.5). |
| Stripe live account | Paid billing | Last step of the readiness plan |
| Off-host backup storage | Backups | Not provided by the repo |

### 3.5 Pre-flight checklist

- [ ] Ubuntu 24.04 amd64 VM, root or sudo, console access.
- [ ] Three DNS names resolve to the host.
- [ ] Certificate chain and key (mode 0600) cover all names.
- [ ] PostgreSQL ready, database prepared (section 5.4), CA file in hand.
- [ ] Reviewed full 40-character commit SHA chosen.
- [ ] Protected input directory prepared outside the checkout.
- [ ] Reviewed Asterisk `.conf` files for the real carrier.
- [ ] SSH source CIDR, carrier SIP CIDR and media CIDR known.
- [ ] Secrets stored in your secret manager, not in the repo.
- [ ] Decision made: `external` or `linux` frontends.

---

## 4. Fresh install with the repository installer

The installer is a Bash launcher (`install_talklee.sh`) that runs
`python3 scripts/installation/install.py "$@"`. It is not an npm package.
Frontend `npm` steps are only a part of `frontend_mode=linux`.

### 4.1 What the installer will and will not do

| It does | It does not |
|---|---|
| Refuse to run on anything but an empty Ubuntu 24.04 amd64 systemd host | Install or create PostgreSQL. The database is always external. |
| Install pinned APT packages | Run migrations, load a schema or restore data |
| Create the `talky` system account | Generate or rotate encryption keys |
| Clone the reviewed commit into `/opt/talky` | Issue or renew TLS certificates |
| Install hash-locked Python packages in `/opt/talky/backend/venv` | Create DNS records |
| Build and test the C++ gateway | Create carrier trunks (it will not decrypt or invent customer trunks) |
| Install reviewed Asterisk config, loopback Redis (Docker), nginx, ufw rules | Enable every timer (see 4.9) |
| Start the core services and check health | Place a controlled call. The receipt keeps `controlled_call_verified=false`. |

It is **never an update tool**. For updates see section 8.

### 4.2 Commands

```bash
# Print the plan. Read-only. Uses install.example.json by default.
bash install_talklee.sh
bash install_talklee.sh plan --config /secure/talky/install.json

# Print the package names that need exact versions.
python3 scripts/installation/install.py packages

# Check your inputs. Installs nothing, contacts nothing.
bash install_talklee.sh validate --config /secure/talky/install.json

# Apply to the empty host. Must be root.
sudo bash install_talklee.sh apply \
  --config /secure/talky/install.json \
  --source /path/to/clean/reviewed-checkout \
  --confirm-new-host
```

Arguments (from `install.py` `main()`):

| Argument | Meaning |
|---|---|
| `plan` (default), `validate`, `packages`, `apply` | The command |
| `--config PATH` | JSON config. Default is `scripts/installation/install.example.json`. |
| `--source PATH` | The clean checkout to install from. Default is the repo root. |
| `--confirm-new-host` | Required for `apply`. |

On Windows `python scripts/installation/install.py plan` works. `apply`
refuses Windows, containers without systemd, non-amd64 and other Ubuntu
versions.

Local safety tests, from the README (do not run them on a low-memory host):

```bash
python -m unittest discover -s scripts/installation -p test_install.py -v
bash -n install_talklee.sh
```

### 4.3 Step 1: get the reviewed source

Install the small checkout tools, clone **outside** `/opt/talky`, and pin the
commit. A downloaded ZIP without git history is not accepted: the installer
runs `git rev-parse HEAD` and `git remote get-url origin`.

```bash
sudo apt-get update
sudo apt-get install --yes git python3 ca-certificates
git clone <your-repository-url> talklee-install-source
cd talklee-install-source
git checkout --detach <FULL_40_CHAR_SHA>
test "$(git rev-parse HEAD)" = "<FULL_40_CHAR_SHA>"
test -z "$(git status --porcelain)"
bash install_talklee.sh plan
```

The origin URL must be credential-free HTTPS or SSH (`checked_origin()`).
Never embed a token in the URL.

Important for this tree: the Support system is **uncommitted** in the worktree
this guide was written in. The installer needs a clean checkout at a pushed
commit. Commit and push the Support change first, or the server will not have
it.

### 4.4 Step 2: prepare the protected inputs

On `main` there is no helper that generates these files. You assemble them by
hand. Copy the examples to a directory outside the checkout and outside
`/opt/talky`:

| Example in repo | Copy to | Notes |
|---|---|---|
| `scripts/installation/install.example.json` | `/secure/talky/install.json` | Installer config |
| `scripts/installation/backend.env.example` | `/secure/talky/backend.env` | Runtime env. `chmod 0600`. |
| `scripts/installation/dashboard.env.example` | `/secure/talky/dashboard.env` | Linux mode only |
| `scripts/installation/admin.env.example` | `/secure/talky/admin.env` | Linux mode only |

```bash
sudo install -d -m 0700 /secure/talky
sudo cp scripts/installation/install.example.json /secure/talky/install.json
sudo install -m 0600 scripts/installation/backend.env.example /secure/talky/backend.env
sudo install -m 0600 scripts/installation/dashboard.env.example /secure/talky/dashboard.env
sudo install -m 0600 scripts/installation/admin.env.example /secure/talky/admin.env
sudoedit /secure/talky/install.json /secure/talky/backend.env
```

#### install.json fields

The key set must equal the example exactly (`validate_config()` checks
`set(config) == KEYS`). Extra or missing keys are an error.

| Key | Contract |
|---|---|
| `schema_version` | Must be `1` |
| `source_sha` | Full 40-hex commit. Not all zeros. The checkout must be exactly at it. |
| `frontend_mode` | `external` or `linux` |
| `api_domain`, `dashboard_domain`, `admin_domain` | Lowercase hostnames with a dot. All three distinct. |
| `ssh_port`, `sip_port`, `rtp_port_start`, `rtp_port_end` | Integers 1-65535. Start not above end. |
| `ssh_source_cidrs`, `sip_source_cidrs`, `media_source_cidrs` | Non-empty lists. `/0` networks are refused. |
| `backend_env_file`, `dashboard_env_file`, `admin_env_file` | Absolute paths. Env files must be mode 0600 or stricter. |
| `asterisk_config_directory` | Directory of reviewed `.conf` files (see 4.5) |
| `tls_certificate_file`, `tls_private_key_file` | PEM chain and key. Key mode 0600. |
| `database_ca_file` | Trusted CA bundle. Copied to `/etc/ssl/certs/talky-db-ca.pem`. |
| `python_requirements_lock` | Hash-locked requirements (see 4.4.1) |
| `apt_versions_file` | JSON map package name to exact version |
| `redis_image` | Must match `redis:<tag>@sha256:<64 hex>` |
| `node_version`, `node_archive_sha256` | Linux mode only. Version `22.18.x` or higher in line 22. SHA256 of the official Linux x64 archive. |
| `database_prepared` | Must be `true` for validate/apply. Set it only after section 5.4. |

The example holds documentation networks and `REPLACE_WITH_...` placeholders so
it cannot be applied by accident.

#### 4.4.1 The Python hash lock

`apply` runs `pip install --require-hashes -r <lock>`. A normal
`backend/requirements.txt` is not a lock. Every row must be `name==version`
followed by one or more `--hash=sha256:<64 hex>`. No URLs, no `-r` includes, no
index lines. The README gives this recipe (produce it on Linux with Python 3.12):

```bash
pip-compile --generate-hashes --output-file requirements-linux-py312.lock backend/requirements.txt
```

Add `--no-emit-index-url --no-emit-trusted-host` if index settings would leak
into the file. `backend/uv.lock` is a stub and is not this lock.

If your backend env sets `STRIPE_SECRET_KEY`, the lock must contain `stripe`
(`backend/requirements.txt` has `stripe>=12.0.0,<15.0.0`). Review the whole
resolved closure and run the relevant tests before you trust it.

#### 4.4.2 The APT version map

```bash
python3 scripts/installation/install.py packages
```

prints the names the map must contain. On this tree they are:
`asterisk`, `build-essential`, `ca-certificates`, `cmake`, `curl`, `docker.io`,
`docker-compose-v2`, `git`, `libffi-dev`, `libpq-dev`, `libssl-dev`, `nginx`,
`postgresql-client-16`, `python3.12`, `python3.12-dev`, `python3.12-venv`,
`ufw`, `xz-utils`.

Collect exact versions on a disposable Ubuntu 24.04 VM with
`apt-cache policy <package>` or `apt-cache madison <package>`, and store:

```json
{"asterisk": "<exact-version>", "nginx": "<exact-version>"}
```

(all 18 names are required). Every value must match `[A-Za-z0-9.+:~_-]+`. The
installer installs exactly `name=version` for every key in the file.
Use Ubuntu's `docker.io` family. Do not mix it with Docker's own `docker-ce`
packages.

`ffmpeg` is not in the installer's package list. `backend/.env.example` says
mp3 or opus recordings need `ffmpeg` on the host and fall back to wav without
it. Adding it is an operator decision. **Not verified**: whether extra names in
the version map are accepted. The code installs every key in the file, so they
likely are, but this was not run.

#### 4.4.3 Redis image digest

```text
redis:7.4-alpine@sha256:<64 hex>
```

Look up the manifest digest yourself. Tag-only values and `latest` are refused.

### 4.5 Asterisk configuration directory

The installer never copies the repo's lab configs. You supply a directory of
regular `.conf` files (no symlinks) with at least:
`asterisk.conf`, `pjsip.conf`, `extensions.conf`, `http.conf`, `ari.conf`,
`rtp.conf`, `modules.conf`.

Checks the installer makes (all in `validate_config()`):

| File | Required content |
|---|---|
| any | No `local_only_change_me`, no `password=1002`, no `#exec`, no absolute includes. Includes must be a local `NAME.conf` or `pjsip.d/*.conf`. |
| `asterisk.conf` | `runuser=asterisk`, `rungroup=asterisk`, `astctlpermissions=0660`, `astctlowner=asterisk`, `astctlgroup=asterisk` |
| `http.conf` | `enabled` yes, `bindaddr = 127.0.0.1`, `bindport` equal to `ASTERISK_ARI_PORT` (default 8088) |
| `ari.conf` | `[general] enabled=yes`; a section named `ASTERISK_ARI_USER` with `password` equal to `ASTERISK_ARI_PASSWORD` and `read_only=no` |
| `pjsip.conf` | Must contain the line `#include pjsip.d/*.conf` |
| `ari.conf`, `pjsip.conf` | File mode 0600 or stricter |

`setup-asterisk.sh` shows the production shape of these files (read-only
reference; do not run it with the installer, see 5.9).

### 4.6 The backend env file

Start from `scripts/installation/backend.env.example`. It is a complete
template. Rules the installer enforces before any host change:

- `ENVIRONMENT=production`.
- No `MIGRATION_DATABASE_URL` in this file. `READ_DATABASE_URL` must be unset.
- These must be non-empty and contain no `REPLACE` or `change_me`:
  `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `SECRET_KEY`,
  `CONNECTOR_ENCRYPTION_KEY`, `SECRETS_MASTER_KEY`, `TELEPHONY_METRICS_TOKEN`,
  `INTERNAL_SERVICE_TOKEN`, `VOICE_GATEWAY_AUTH_TOKEN`, `BACKEND_INTERNAL_URL`,
  `VOICE_GATEWAY_CALLBACK_HOST`, `ASTERISK_ARI_USER`, `ASTERISK_ARI_PASSWORD`,
  `FRONTEND_URL`, `API_BASE_URL`.
- `FRONTEND_URL` equals `https://<dashboard_domain>`; `API_BASE_URL` equals
  `https://<api_domain>`.
- `CORS_ORIGINS` explicitly lists the dashboard and Admin origins. No `*`.
- `BACKEND_INTERNAL_URL=http://127.0.0.1:8000`, `VOICE_GATEWAY_CALLBACK_HOST=127.0.0.1`,
  `ASTERISK_ARI_HOST=127.0.0.1`.
- `TELEPHONY_ADAPTER=asterisk`. `TELEPHONY_PJSIP_AUTO_RELOAD` on.
- `CLEANUP_DRY_RUN=true` (kept until retention is signed off).
- `CALLER_ID_ENFORCEMENT_MODE=enforce`; the strict inbound routing flags not disabled;
  `TELEPHONY_DEV_BYPASS_GUARD_ERRORS`, `TELEPHONY_LOCAL_DEV` and
  `INBOUND_TRANSFER_STAGING_PROOF_*` off or unset.
- `DATABASE_URL`: PostgreSQL scheme, a non-local host (not `localhost`,
  `postgres`, or a loopback IP), user not `postgres` or `root`,
  `sslmode=verify-full`, `sslrootcert=/etc/ssl/certs/talky-db-ca.pem`, and no
  query keys that override host, port, user, password, dbname, service or ssl.
- `REDIS_URL`: `redis://:<password>@127.0.0.1:6379/<db>`, password 32+
  characters from letters, digits, `_`, `-`.
- ARI password: 32+ URL-safe characters.
- Optional path variables, if set, must equal the documented writable paths:
  `LOCAL_RECORDINGS_DIR`, `RECORDINGS_STORAGE_DIR`, `LOCAL_FEEDBACK_DIR`,
  `TELEPHONY_PJSIP_CONF_DIR`.

The env file format is strict: `KEY=value` lines, no duplicate keys, no shell
substitution, no backslashes, no multi-line values. Quote only with a matching
pair.

The template has **no `SUPPORT_*` lines**. Support defaults to off when the
names are absent (`app/domain/services/support/config.py`). See section 6 to
add them.

### 4.7 What `apply` does, in order

Derived from reading `apply()` in `install.py`. Nothing was executed.

1. Validate config and inputs again; check the host is fresh.
2. Create `/var/lib/talky-install` (mode 0700) with `state.json`.
3. Install `/usr/sbin/policy-rc.d` returning 101 so packages cannot auto-start
   services. Run `apt-get update`, then `apt-get install --yes --no-install-recommends`
   with every `name=version`. Remove the policy file.
4. `useradd --system ... talky`; add `talky` to group `asterisk`.
5. `git clone --no-hardlinks --no-checkout` the source into `/opt/talky`, then
   `git checkout --detach <source_sha>` and verify the SHA.
6. Copy the backend env to `/opt/talky/backend/.env`, mode 0640, owner `root:talky`.
7. Create `logs`, `recordings`, `tmp`, `uploads`, `audio_files/storage`,
   `feedback_audio` under `/opt/talky/backend` and `/opt/talky/runtime/state`,
   owned by `talky`.
8. `python3.12 -m venv /opt/talky/backend/venv`; `pip install --require-hashes`;
   `pip check`.
9. Run the production gate:
   `from app.core.prod_gate import enforce_production_gate; enforce_production_gate()`.
10. Build the gateway with `backend/scripts/build_voice_gateway_release.sh`
    into `/opt/talky/runtime/bin/voice_gateway` (`root:talky`, mode 0750).
11. Copy the DB CA to `/etc/ssl/certs/talky-db-ca.pem`. Connect with the
    runtime identity. Fail unless the role is not superuser, BYPASSRLS,
    CREATEDB or CREATEROLE, the session uses TLS, and the `alembic_version`
    rows equal the checkout's Alembic heads.
12. Copy your Asterisk `.conf` files to `/etc/asterisk` (mode 0640, `root:asterisk`).
    Create `/etc/asterisk/pjsip.d` (`asterisk:asterisk`, mode 2770).
13. Write `/etc/talky/redis.conf`, `/etc/talky/compose.yml` and
    `talky-redis.service` (Docker Redis bound to `127.0.0.1:6379`, AOF on).
14. Copy every `*.service`, `*.timer`, `*.target` from `backend/systemd/` to
    `/etc/systemd/system/`. Add a drop-in `10-fresh-host.conf` for each service.
15. Write drop-ins: `talky-migrate.service.d/20-migration-identity.conf`
    (requires `/etc/talky/migration.env` with `MIGRATION_DATABASE_URL`) and
    `talky-api.service.d/20-loopback.conf` (API on `127.0.0.1:8000`).
16. Linux mode only: install Node from `nodejs.org`, check the SHA256, link into
    `/usr/local/bin`, build both frontends, write `talky-dashboard.service`.
17. Copy TLS files to `/etc/talky/tls/`; write `/etc/nginx/sites-available/talky`,
    enable it, drop the default site, `nginx -t`.
18. Configure ufw: default deny incoming; SSH only from your CIDRs; 80 and 443
    open; SIP (udp and tcp) from SIP CIDRs; RTP range (udp) from media CIDRs.
    Then `ufw --force enable`.
19. `systemctl enable --now` Docker, `talky-redis`, `asterisk`, the gateway,
    `talky-api`, the voice, dialer and reminder workers,
    `talky-healthwatch.timer`, `talky-trunk-status.timer`, `nginx`
    (and `talky-dashboard` in Linux mode).
20. Check each service is active. Poll (12 tries, 5 s apart):
    `http://127.0.0.1:18080/ready` (must show `ready`, `protocol_version` 2 and
    `pcmu`), `http://127.0.0.1:8000/health`,
    `http://127.0.0.1:8000/api/v1/healthz/ready`, `.../healthz/deep`.
    Then an HTTPS check of the API (and frontends in Linux mode) using
    `curl --resolve`.
21. Write `/var/lib/talky-install/receipt.json`, `apt-installed.tsv`,
    `python-installed.txt`.

#### The drop-in applied to every service

```ini
[Unit]
After=network-online.target talky-redis.service asterisk.service
Wants=network-online.target talky-redis.service
[Service]
User=talky
Group=talky
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
```

`talky-trunk-status.service` also gets `SupplementaryGroups=asterisk`.

### 4.8 If apply fails

- The installer prints `ERROR: ...` with field or file names only. It
  suppresses command output on purpose.
- Nothing is cleaned up. Do **not** rerun `apply` on the same host: it refuses
  because `/opt/talky` and `/var/lib/talky-install` now exist.
- Preserve `/var/lib/talky-install`, `journalctl` output and the config. Fix the
  input and repeat on a **new empty VM**.

### 4.9 Gaps after `apply` (you must close these)

Derived from the code and unit files. Not run.

| Gap | Why | What to do |
|---|---|---|
| Database schema | The installer only checks it | Section 5.4, done **before** apply |
| `/etc/talky/migration.env` | Installer intentionally does not create it | Create it only when a later migration is needed (section 8.3) |
| `talky-api-web` | Not in the installer's service list; nginx proxies only 8000 | Enable it only if you want the split API. Not verified on the installer profile. |
| `talky-support-jobs.timer` | Copied, not enabled | `sudo systemctl enable --now talky-support-jobs.timer` (section 6) |
| `talky-cleanup.timer` | Copied, not enabled | Enable when retention is signed off. Stays dry-run. |
| `talky-asterisk-reconcile.timer` | Copied, not enabled. The drop-in changes its user to `talky`; the shipped unit runs as root. | **Not verified** that the reconcile script works as `talky`. Test before enabling. |
| `talky-db-backup.timer` | The shipped script dumps a Docker container `talky-postgres-1`. An external DB has none. | Use `scripts/database/` tools (section 8.5) |
| `talky-inbound-synthetic.timer` | Needs `/etc/talky/inbound-synthetic.env` and a real DID. It places a real carrier call. | Leave off until a DID and endpoint are chosen |
| `talky-trunk-status.timer` | The unit text calls it retired; `install-services.sh` disables it. The installer enables it. | **Not verified** whether the timer-started loop service behaves like the enabled service. Check `systemctl status talky-trunk-status`. |
| First platform admin | No script on `main` creates one | `users-roles.md` says it is done in the DB or by an existing platform admin. See section 10. |
| Carrier trunks | Generated from DB records, not by the installer | Reconcile through the app's supported flow before calls |
| TLS renewal, backups, alerts | Out of scope | Name an owner for each |
| Reboot test | Not done by the installer | Reboot once and rerun section 7 |

---

## 5. Manual reference install

Use this section to understand each step, to do a non-installer layout, or to
repair a host. It follows the production layout in `docs/DEPLOYMENT.md`:
checkout at `/opt/talky`, venv at `/opt/talky/backend/venv`, units symlinked
from `/opt/talky/backend/systemd/`.

Do not mix this section with a half-finished installer run.

### 5.1 OS packages

```bash
sudo apt-get update
sudo apt-get install --yes --no-install-recommends \
  asterisk build-essential ca-certificates cmake curl docker.io docker-compose-v2 \
  git libffi-dev libpq-dev libssl-dev nginx postgresql-client-16 \
  python3.12 python3.12-dev python3.12-venv ufw xz-utils
```

Add `ffmpeg` if you want mp3 or opus recordings (see 4.4.2).
`deploy_to_server.sh` also expects these tools on the box: `asterisk`, `bash`,
`cmake`, `ctest`, `c++`, `make`, `curl`, `git`, `install`, `mktemp`, `rmdir`,
`timeout`. `ctest` comes with `cmake`.

Installing `asterisk` through APT auto-starts it with default config. Stop it
before you write config (`setup-asterisk.sh` does `systemctl stop asterisk`).

### 5.2 Accounts and checkout

The shipped gateway unit has `User=admins` and `Group=admins`. The other
backend units have no `User=` and run as root. `setup-asterisk.sh` also uses
the `admins` account. To use the shipped units unchanged, create that account:

```bash
sudo adduser --disabled-password --gecos "" admins
sudo usermod -aG asterisk admins
```

The repo only requires that `admins` exists and is in group `asterisk`. If you
prefer a different account, use `systemctl edit` drop-ins as the installer
does. That layout is exactly what section 4 automates.

Clone the repo:

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone <your-repository-url> talky
cd /opt/talky
sudo git checkout --detach <FULL_SHA>
```

Keep `/opt/talky` clean: `deploy_to_server.sh` refuses a dirty tree.

Use a **full** checkout if the frontends build on this host. Support help
articles do not need one: on a sparse checkout without `Talk-Leee/` the importer
reads the docs from the repository's git objects at `HEAD` (section 6.3).

### 5.3 Python environment

The shipped units call `/opt/talky/backend/venv/bin/...`. The directory name
is `venv` (not `.venv`).

```bash
cd /opt/talky/backend
python3.12 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
venv/bin/python -c "import app.main"
```

`import app.main` is the smoke test `deploy_to_server.sh` uses. For a repeatable
server prefer the hash lock from 4.4.1 with `--require-hashes`.
Do not install `requirements-dev.txt` on a server.

### 5.4 PostgreSQL bootstrap

Alembic's first revision does not build the historical schema. A new database
must load the maintained snapshot, be stamped at the conservative floor, then
upgraded. This is the path CI runs (`.github/workflows/ci.yml`, step
"Bootstrap and migrate primary test database") and `backend/database/MIGRATIONS.md`
documents it.

Prerequisites on the database server (from the schema and migrations):

- Extensions `uuid-ossp` and `pgcrypto` (created by `complete_schema.sql`),
  and `pg_trgm` (created by migrations `0010` and `0033`). The migration login
  must be allowed to create them, or a DBA must create them first.
- A migration/owner login that can create objects.
- A **separate** runtime login: NOSUPERUSER, NOBYPASSRLS, NOCREATEDB,
  NOCREATEROLE. The installer rejects anything else.

Illustrative role creation (adapt names; not shipped by the repo):

```sql
-- Run as a DBA. Names and password handling are examples only.
CREATE ROLE talky_owner LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '<from your secret manager>';
CREATE ROLE talky_runtime LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '<from your secret manager>';
CREATE DATABASE talkyai OWNER talky_owner;
```

**Runtime grants: Not verified.** `main` has no script that grants the runtime
role its table, sequence and function rights. The read-only gate
`backend/scripts/verify_admin_runtime_privileges.py` reports what is missing.
Run it after migrations (see 7.4). Codex's unmerged branch adds a
`runtime_grants.py` tool (section 11); it is not on `main`.

Bootstrap on the **new, empty** database, from `/opt/talky/backend`. Supply the
owner DSN through your secret mechanism, not on a command line that is saved
in shell history:

```bash
cd /opt/talky/backend
# 1. Load the maintained schema floor. Stop on any error.
psql "$MIGRATION_DATABASE_URL" -X -v ON_ERROR_STOP=1 -f database/complete_schema.sql

# 2. Record the proven floor, then run every newer migration.
export MIGRATION_DATABASE_URL   # the owner DSN, verify-full TLS
venv/bin/alembic -c alembic.ini stamp 0008_tenant_voice_tuning
venv/bin/alembic -c alembic.ini upgrade head
venv/bin/alembic -c alembic.ini current
```

Rules:

- `backend/Alembic/env.py` uses `MIGRATION_DATABASE_URL` if set, else
  `DATABASE_URL`. Prefer the owner DSN for migrations.
- **Never** `alembic stamp head` on an old snapshot. It skips migrations.
- Do not run the archived raw SQL in `database/migrations/_archive/`.
- Do not use `docker-compose.yml`'s postgres init as the production path; that
  mounts the 2026-06-02 snapshot for local development.
- `backend/database/MIGRATIONS.md` still says the sole head is
  `0035_user_profiles_role_widen`. That paragraph is stale. The current sole
  head in this tree is **`0082_support_system`** (checked by reading all 86
  revision files: exactly one revision is not a parent of another).
- `complete_schema.sql` is the bootstrap CI uses; it is 3518 lines and has 71
  `CREATE TABLE` statements. Per `MIGRATIONS.md`, it does not contain every
  0009-0021 object, so the full chain must run.
- CI creates a NOLOGIN role `authenticated` first. Migration `0028` only grants
  to it `IF EXISTS`, so it is optional.

Confirm the result:

```bash
venv/bin/alembic -c alembic.ini heads      # expect 0082_support_system (this tree)
venv/bin/alembic -c alembic.ini current    # must equal the heads
```

If the Support system is not yet committed in your checkout, the head is
`0081_admin_payment_merge`.

If you intend to restore existing customer data instead of starting empty,
stop. Follow `docs/production-readiness/database-recovery.md` and keep the
original encryption keys. Never bootstrap over restored data.

### 5.5 Redis

Production Redis is a Docker container. For a new host use the same pattern the
installer writes: bound to loopback, password protected, AOF on.

Settings the installer uses (`/etc/talky/redis.conf`):

```text
bind 0.0.0.0        # inside the container; the published port is 127.0.0.1 only
protected-mode yes
port 6379
appendonly yes
appendfsync everysec
maxmemory-policy noeviction
requirepass <from your secret manager>
```

and a compose service with `ports: ["127.0.0.1:6379:6379"]`. The local dev
`docker-compose.yml` uses `allkeys-lru`; the installer uses `noeviction`.
Which is right for production is **Not verified**; follow the installer.

`REDIS_URL` must then be `redis://:<password>@127.0.0.1:6379/0`. Redis is
required: rate limits, worker heartbeats and sessions depend on it.

### 5.6 Backend `.env`

Create `/opt/talky/backend/.env`. Every unit loads it with
`EnvironmentFile=/opt/talky/backend/.env`. Start from `backend/.env.example`
(full catalog) or `scripts/installation/backend.env.example` (production
minimum). Permissions:

```bash
sudo chown root:root /opt/talky/backend/.env
sudo chmod 0600 /opt/talky/backend/.env
```

Use `root:<service-group>` and mode 0640 if services run as a non-root
account, as the installer does. Never commit this file. Never print it.

`setup-backend-env.sh` exists for the current production box. It writes a `.env`
and **hard-codes provider keys inside the script**; do not use it for a new
server and do not copy it.

Categories and what to fill (names only; the full list is in 12.1):

| Category | Names | Notes |
|---|---|---|
| Mode | `ENVIRONMENT`, `DEBUG`, `LOG_LEVEL` | `ENVIRONMENT=production` turns on the production gate |
| URLs | `FRONTEND_URL`, `API_BASE_URL`, `CORS_ORIGINS` | Must match real hostnames |
| Database | `DATABASE_URL`, `PG_POOL_ACQUIRE_TIMEOUT` | Units set `PG_POOL_MIN_SIZE` and `PG_POOL_MAX_SIZE` themselves |
| Redis | `REDIS_URL` | |
| Auth | `JWT_SECRET`, `SECRET_KEY`, `MASTER_KEY`, `SESSION_LIFETIME_HOURS` | The example calls `SECRET_KEY` legacy |
| Encryption | `SECRETS_MASTER_KEY`, `CONNECTOR_ENCRYPTION_KEY`, `CONNECTOR_ENCRYPTION_KEYS_OLD` | Back these up off the machine |
| Service auth | `INTERNAL_SERVICE_TOKEN`, `VOICE_GATEWAY_AUTH_TOKEN`, `VOICE_GATEWAY_CALLBACK_HOST`, `BACKEND_INTERNAL_URL`, `VOICE_GATEWAY_REQUIRE_CONFIG_ACK` | Gateway reads the same file |
| Telephony | `TELEPHONY_ADAPTER`, `ASTERISK_ARI_*`, `ASTERISK_GATEWAY_BASE_URL`, `TELEPHONY_PJSIP_*`, `TELEPHONY_STATE_BACKEND`, `TELEPHONY_METRICS_TOKEN` | `TELEPHONY_STATE_BACKEND=memory` is the default; `redis` mirrors call ownership |
| Providers | `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, `CARTESIA_API_KEY`, optional `ASSEMBLYAI_API_KEY(S)`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY(S)`, `CEREBRAS_API_KEY`, `ELEVENLABS_API_KEY` | Providers are chosen in `backend/config/providers.yaml` and in the app |
| Storage | `S3_*`, `LOCAL_FEEDBACK_DIR`, `CALL_FEEDBACK_*`, `RECORDING_AUDIO_*` | |
| Billing | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_BILLING_DISABLED` | Read by `prod_gate.py` |
| Email | `EMAIL_USER`, `EMAIL_PASS` (and `SMTP_*` in the installer template) | |
| Support | `SUPPORT_*` | Section 6 |

Generate secrets with the commands in the example file, for instance
`python -c "import secrets; print(secrets.token_hex(32))"`.

The production gate (`app/core/prod_gate.py`) refuses to boot on: dev bypass
flags, weak or placeholder JWT secret, missing or short service secrets,
reused gateway secrets, an unpinned callback host, a missing metrics token,
default Asterisk or FreeSWITCH passwords, staging proof flags, and Stripe
problems (mock mode, non-live key, missing SDK, missing webhook secret).
The gate lists all violations together.

### 5.7 systemd installation

```bash
cd /opt/talky
sudo bash backend/systemd/install-services.sh
```

What the script does (read from the file):

1. Symlinks every `*.service`, `*.target` and `*.timer` in `backend/systemd/` into
   `/etc/systemd/system/` (`ln -sf`). The units therefore track the checkout.
2. Runs `systemctl disable --now talky-trunk-status.timer`.
3. `systemctl daemon-reload`.
4. Enables: `talky-api`, `talky-api-web`, `talky-voice-worker`,
   `talky-dialer-worker`, `talky-reminder-worker`, `talky-voice-gateway`,
   `talky-cleanup.timer`, `talky-healthwatch.timer`, `talky-trunk-status.service`,
   `talky-inbound-synthetic.timer`, `talky-db-backup.timer`,
   `talky-support-jobs.timer`, `talky.target`; and enables **and starts**
   `talky-asterisk-reconcile.timer`.

It does **not** start the other services. It does not enable
`talky-migrate.service`, on purpose.

Cautions before you run it on a new host:

- `talky-inbound-synthetic.timer` is enabled by this script. Its service needs
  `/etc/talky/inbound-synthetic.env` (the `EnvironmentFile` has no `-`, so a
  missing file fails the unit) and it places a real call. Create the file from
  `backend/systemd/inbound-synthetic.env.example` with a real DID and endpoint,
  or run `sudo systemctl disable --now talky-inbound-synthetic.timer`.
- `talky-db-backup.timer` is enabled. Its script targets the Docker container
  `talky-postgres-1` and needs `/etc/talky/db-backup.env` with
  `TALKY_BACKUP_PRINCIPAL_ID`. On an external-database host it will fail.
  Disable it and use section 8.5.
- `talky-asterisk-reconcile.timer` starts at once. Install Asterisk first.
- `talky-api.service` binds `0.0.0.0:8000`. Either firewall port 8000 or add an
  override with `--host 127.0.0.1`.

Start in order:

```bash
sudo systemctl start talky-voice-gateway
sudo systemctl start talky.target          # api + 3 workers
sudo systemctl start talky-api-web         # optional, dashboard API on :8001
```

If any start fails, read `journalctl -u <unit> -n 200 --no-pager`.

### 5.8 Gateway build

The unit expects `/opt/talky/runtime/bin/voice_gateway`. Build it with the
repo script, which runs cmake, builds, runs the full CTest suite, proves the
binary refuses to start without `INTERNAL_SERVICE_TOKEN`, then installs it:

```bash
cd /opt/talky
sudo install -d -o root -g admins -m 0750 /opt/talky/runtime/bin
gateway_candidate="$(mktemp /tmp/talky-voice-gateway-candidate.XXXXXX)"
VOICE_GATEWAY_BUILD_SHA="$(git rev-parse HEAD)" \
  bash backend/scripts/build_voice_gateway_release.sh "$gateway_candidate"
sudo install -o root -g admins -m 0750 "$gateway_candidate" /opt/talky/runtime/bin/voice_gateway
rm -f "$gateway_candidate"
```

The output path must be absolute. `VOICE_GATEWAY_BUILD_SHA` must be a full
lowercase SHA (the script defaults it to `git rev-parse HEAD`). The gateway
source is `services/voice-gateway-cpp/` (CMake, needs Threads). The build is
CPU and memory heavy: do it when the host is idle.

Check it:

```bash
curl -fsS http://127.0.0.1:18080/ready
curl -fsS http://127.0.0.1:18080/stats
```

`/ready` must report `ready: true`, `protocol_version: 2` and `pcmu` in
`codecs` (this is what `deploy_to_server.sh` asserts).

### 5.9 Asterisk

`setup-asterisk.sh` is the **production** Asterisk configuration source. It is
hardcoded to the current production server and carrier: `SERVER_IP="144.76.17.150"`,
`TRUNK_HOST="sip3.blazedigitel.com"`, ARI user `talky`, Stasis app `talky_ai`,
RTP 10000-20000. It also edits `/opt/talky/backend/.env` and restarts
`talky-api`. It is not generic. `CLAUDE.md` forbids editing it without explicit
inbound-work scope.

What it does, in order (read from the file):

1. Requires root and the env vars `TRUNK_USER` and `TRUNK_PASS` (it aborts if unset).
2. Refuses to continue unless `/etc/asterisk/extensions.conf` already contains
   `#include extensions.d/*.conf`. It never rewrites `extensions.conf`.
3. Runs `reconcile_asterisk_release.sh --check-only`.
4. Installs Asterisk if missing; stops it.
5. Generates an ARI password with `openssl rand -hex 24`, saves it to
   `/opt/talky/secrets/ari-password` (mode 600).
6. Backs up `/etc/asterisk` to `/etc/asterisk.bak.<timestamp>`.
7. Writes `http.conf` (127.0.0.1:8088), `ari.conf`, `rtp.conf`, and `pjsip.conf`
   with one carrier trunk and `#include pjsip.d/*.conf`.
8. Creates `/etc/asterisk/pjsip.d` (`asterisk:asterisk`, 2770) and
   `/etc/asterisk/extensions.d` with a `00-keep.conf` placeholder.
9. Writes the new ARI password into `/opt/talky/backend/.env`, restarts Asterisk,
   runs `reconcile_asterisk_release.sh`, restarts `talky-api`, prints status.

For a new server with a different carrier, do not run it as is. Either:

- use the installer profile with your own reviewed `.conf` directory (section 4.5), or
- copy the structure by hand and keep these invariants: ARI on loopback, one
  ARI user matching `ASTERISK_ARI_USER` and `ASTERISK_ARI_PASSWORD`, `pjsip.d`
  included and writable by the backend account, `extensions.d` included.

After Asterisk is configured, the database drives trunk and inbound config:

```bash
# Read-only drift check (what deploy runs before checkout):
sudo env TALKY_BACKEND_ENV_FILE=/opt/talky/backend/.env \
  TALKY_BACKEND_PYTHON=/opt/talky/backend/venv/bin/python \
  bash /opt/talky/backend/scripts/reconcile_asterisk_release.sh --check-only
# Apply (requires zero active channels):
sudo bash /opt/talky/backend/scripts/reconcile_asterisk_release.sh
```

Useful checks (from the script's own follow-up list):
`sudo asterisk -rx 'pjsip show registrations'`, `sudo asterisk -rx 'pjsip show endpoints'`,
`sudo asterisk -rx 'core show channels'`.

### 5.10 nginx and TLS

The installer writes a simple site. `talky.conf` at the repo root is an older
manual template (Let's Encrypt paths, HTTP/2, rate limits, WebSocket
locations). Use one or the other.

Manual template steps (from the file header):

```bash
sudo cp /opt/talky/talky.conf /etc/nginx/sites-available/talky.conf
sudo ln -s /etc/nginx/sites-available/talky.conf /etc/nginx/sites-enabled/
# add the 3 limit zone lines from nginx_http_block_addition.conf
# inside the http { } block of /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.your-domain.com -d api.your-domain.com   # as the template says
```

`talky.conf` uses placeholder names `app.your-domain.com` and
`api.your-domain.com`. Replace them. It proxies the API to `127.0.0.1:8000`,
rate-limits `/api/v1/auth/(login|register|token)` (5/minute zone) and `/api/`
(30 r/s zone), and routes WebSockets for `/api/v1/(voice|assistant|ai-options)/ws`.
**Not verified**: that this template covers a Linux-hosted Admin build; the
installer's own nginx site is the one that serves Admin.

Behind nginx, bind the API to loopback only (installer drop-in) so port 8000 is
not public.

### 5.11 Frontends

If the frontends stay on Vercel, set their environment there. The dashboard
needs `NEXT_PUBLIC_API_BASE_URL` pointing at `https://<api_domain>/api/v1`.

For Linux hosting (what the installer does):

```bash
# Node 22 (>= 22.18) must already be installed.
cd /opt/talky/Talk-Leee
npm ci --include=dev
npm run build          # runs: next build --webpack
node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000

cd /opt/talky/Admin/frontend
npm ci --include=dev
npm run build          # runs: tsc -b && vite build, output in dist/
```

Environment files:

| App | File | Names |
|---|---|---|
| Dashboard | `Talk-Leee/.env.production` | `NODE_ENV`, `NEXT_PUBLIC_APP_ENV`, `NEXT_PUBLIC_API_BASE_URL`, `TALKLEE_REQUIRE_AUTH` |
| Admin | `Admin/frontend/.env.production` | `VITE_API_BASE_URL`, `VITE_USE_DUMMY_AUTH=false`, `VITE_ADMIN_DEV_MODE=false` |

`NEXT_PUBLIC_*` and `VITE_*` values are public in the browser. Never put a
secret in them. The installer requires the Admin dev flags to be `false`.

Build memory: a Next.js production build is memory heavy. **Not verified**
what size is needed; do not run it on a tiny host while the call stack is up.

---

## 6. Support system on a new server

The Support system (help bot, tickets, help articles, staff console) is in the
working tree as uncommitted files. It ships **off**. Nothing about it is
active until you set `SUPPORT_ENABLED=true`.

### 6.1 What it adds

| Piece | Where |
|---|---|
| Migration | `backend/Alembic/versions/0082_support_system.py` (`down_revision = 0081_admin_payment_merge`) |
| Customer routes | `backend/app/api/v1/endpoints/support/` (`conversations.py`, `tickets.py`, `attachments.py`), mounted in `backend/app/api/v1/routes.py` |
| Staff routes | `backend/app/api/v1/endpoints/support/admin.py` (`/admin/support/...`) |
| Settings | `backend/app/domain/services/support/config.py` |
| Background jobs | `backend/app/workers/support_jobs.py`, `backend/app/domain/services/support/jobs.py` |
| Systemd | `backend/systemd/talky-support-jobs.service` and `.timer` |
| Article import | `backend/scripts/import_support_articles.py` |
| Permissions | `support:report`, `support:read_team`, `support:manage` (also in `backend/app/core/security/rbac.py`) |

Migration `0082` creates these tables in schema `public`: `support_tickets`,
`support_conversations`, `support_conversation_messages`,
`support_ticket_messages`, `support_ticket_attachments`, `support_ticket_events`,
`support_ticket_reads`, `support_request_receipts`, `support_kb_articles`, plus
the sequence `support_ticket_number_seq`. It turns on and forces row level
security on the tenant tables (`app.bypass_rls` / `app.current_tenant_id`
contract). It inserts the three permission rows and role grants.

Role grants made by the migration:

| Permission | Roles |
|---|---|
| `support:report` | `tenant_admin`, `partner_admin`, `platform_admin`, `campaign_manager`, `user`, `agent`, `billing_user` |
| `support:read_team` | `tenant_admin`, `partner_admin`, `platform_admin` |
| `support:manage` | `platform_admin` |

The grants insert from `roles r CROSS JOIN permissions p WHERE r.name IN (...)`.
Roles that do not exist in the `roles` table simply get no row. **Not
verified**: whether a freshly migrated database already has those role rows;
`backend/scripts/seed_rbac.py` exists to seed roles and permissions, but whether a freshly migrated database needs it was not checked.

Downgrade is intentionally refused while tickets, conversations or
staff-written articles exist. Do not plan to roll this migration back in
production.

### 6.2 Order of operations

1. Commit and push the Support change so the server can check it out.
2. Run the migration (`0081` to `0082`) before restarting any app process
   (section 8.3). On a brand-new database the bootstrap in 5.4 already
   includes it.
3. Deploy code and restart API units.
4. Enable the timer: `sudo systemctl enable --now talky-support-jobs.timer`
   (`install-services.sh` already enables it on a host where you run that script).
5. Import help articles (6.3).
6. Check that support is **off** (6.7).
7. Turn on for a pilot tenant (6.8).

### 6.3 Importing help articles

The import reads the public docs markdown from
`Talk-Leee/src/app/docs/_content` (a `registry.ts` plus the page files). It is
idempotent. Unchanged sections are not touched. Staff-written articles are never
overwritten. Docs sections that disappeared are unpublished, never deleted.

Dry run (parses only, does not touch the database):

```bash
cd /opt/talky/backend
venv/bin/python scripts/import_support_articles.py --dry-run
```

Real run, with the backend environment (from `docs/RUNBOOK.md`):

```bash
sudo systemd-run --wait --pipe -p WorkingDirectory=/opt/talky/backend \
  -p EnvironmentFile=/opt/talky/backend/.env \
  /opt/talky/backend/venv/bin/python scripts/import_support_articles.py
```

Options (from the script's `argparse`):

| Option | Meaning |
|---|---|
| `--dry-run` | Parse and print the section count. No database. |
| `--docs-dir PATH` | Read the docs from this folder. |
| `--git-ref REF` | Read the docs from git objects at `REF` (for example `HEAD`). |

Without either option the importer reads `<repo>/Talk-Leee/src/app/docs/_content`
when it exists, otherwise git objects at `HEAD` (the deployed commit). This is
how it runs on production, whose checkout is sparse.

Expected output line: `support articles: source=... parsed=N inserted=N updated=N unchanged=N unpublished=N skipped_staff=N`.
Exit code 2 with `DATABASE_URL is required` means the environment file was not
loaded. `registry.ts not found in git HEAD:...` means the commit has no docs
(not expected on `main`).

The import uses `DATABASE_URL` (the runtime login), through `asyncpg`.
Staff edit their own articles in the staff console.

### 6.4 Settings

All names are in `backend/.env.example` (the "Support system" block at the end).
Values are read from the environment (`config.py`). A restart of the API units is
needed after editing `.env`, because systemd loads the file at process start.

| Variable | Default | Meaning and limits |
|---|---|---|
| `SUPPORT_ENABLED` | `false` | Master switch |
| `SUPPORT_TENANT_ALLOWLIST` | empty | Comma-separated tenant UUIDs. Empty means every tenant. **One malformed entry turns support off for everyone.** |
| `SUPPORT_BOT_ENABLED` | `true` | Help bot on or off. The ticket form keeps working when off. |
| `SUPPORT_BOT_MODEL` | `openai/gpt-oss-20b` | Must be an allowed assistant model id (`app/infrastructure/assistant/model_config.py`); otherwise the default is used |
| `SUPPORT_RATE_LIMIT_PER_MINUTE` | `10` | Range 1-600 |
| `SUPPORT_DAILY_MESSAGE_CAP_PER_TENANT` | `300` | Range 1-100000 |
| `SUPPORT_MAX_TURNS` | `20` | Range 1-200 |
| `SUPPORT_ATTACHMENT_MAX_MB` | `10` | Range 1-50 |
| `SUPPORT_SLA_<PRIORITY>_FIRST_RESPONSE_MINUTES` | urgent 60, high 240, normal 1440, low 4320 | Priorities `URGENT`, `HIGH`, `NORMAL`, `LOW`. Range 5 to 43200. |
| `SUPPORT_SLA_<PRIORITY>_RESOLUTION_MINUTES` | urgent 480, high 1440, normal 4320, low 10080 | Range 5 to 129600. Never below the first-response value. |
| `SUPPORT_STAFF_ALERT_EMAIL` | empty | Staff alert address for urgent and high tickets and missed deadlines |
| `SUPPORT_TRANSCRIPT_RETENTION_DAYS` | `180` | Range 1-3650. Bot transcripts that never became a ticket are deleted after this. |
| `SUPPORT_ALLOW_LOCAL_STORAGE` | `false` | Allows local-disk attachments in production |
| `SUPPORT_ATTACHMENT_LOCAL_DIR` | `./support_attachments` | Commented out in the example |

An invalid number keeps the safe default and logs `support_setting_invalid`. A
malformed allowlist logs `support_allowlist_invalid ... support_disabled=true`.
Slack alerts use `SLACK_WEBHOOK_URL` from the notification service
(`app/domain/services/notification_service.py`); it is referenced in the
example's comment but has no line of its own there. The ticket link in emails is
built from `FRONTEND_URL` (default `http://localhost:3000` if unset), so set it.

### 6.5 Attachments and storage

Attachments are stored privately under a `support/<tenant>/` prefix in the same
S3-compatible bucket as recordings (`S3_BUCKET_NAME`, `S3_REGION`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, optional `S3_ENDPOINT_URL`).
Downloads use short-lived signed URLs.

Without S3, attachments go to local disk. In `ENVIRONMENT=production` local disk
is used **only if** `SUPPORT_ALLOW_LOCAL_STORAGE=true`
(`attachments.py` `_local_allowed()`). Do not enable it unless the disk
survives rollouts.

On the installer profile the default local folder `./support_attachments` would
sit under the root-owned `/opt/talky/backend`, and the installer only makes a
fixed set of folders writable by `talky`. That folder is not in the list. So on
that profile use S3, or set `SUPPORT_ATTACHMENT_LOCAL_DIR` to a folder you
create and own for `talky`. This is derived from reading the code; **Not
verified** by running it.

### 6.6 Scheduled jobs

`talky-support-jobs.timer` runs `talky-support-jobs.service` every 15 minutes
(`OnCalendar=*:0/15`, `Persistent=true`). The service runs
`python -m app.workers.support_jobs` with `TimeoutStartSec=10min`.

- Every run: marks newly missed deadlines (once per ticket per deadline) and
  alerts staff.
- First quarter of each hour (or `--hourly`): closes tickets resolved more than
  7 days ago; deletes uploads never attached within 24 hours; marks bot
  conversations idle for 24 hours as abandoned; deletes old unticketed bot
  transcripts.
- While `SUPPORT_ENABLED=false` it logs `support_jobs_skipped reason=support_disabled`
  and exits 0.
- Exit 1 means a database pool or pass failure.

```bash
systemctl list-timers talky-support-jobs.timer --no-pager
journalctl -u talky-support-jobs --since '1 hour ago'
sudo systemctl start talky-support-jobs.service
sudo systemd-run --wait --pipe -p WorkingDirectory=/opt/talky/backend \
  -p EnvironmentFile=/opt/talky/backend/.env \
  /opt/talky/backend/venv/bin/python -m app.workers.support_jobs --hourly
```

### 6.7 Verify the shipped state (support off)

`GET /api/v1/support/config` needs a signed-in user. With a valid bearer token
for any tenant user:

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" https://api.example.com/api/v1/support/config
```

While support is off for that tenant the body is:

```json
{"enabled": false, "bot_enabled": false,
 "permissions": {"report": false, "read_team": false},
 "categories": [], "suggestions": [],
 "attachments": {"max_mb": 0, "max_per_message": 0, "types": []}}
```

Every other `/api/v1/support/...` route answers 404 while off (comment in
`.env.example`). How you obtain `$TOKEN` is the normal login flow; this guide
does not document it. The exact status for a missing token is **Not verified**.

Also check the migration and timer:

```bash
sudo systemd-run --wait --pipe -p WorkingDirectory=/opt/talky/backend \n  -p EnvironmentFile=/opt/talky/backend/.env \n  /opt/talky/backend/venv/bin/alembic -c alembic.ini current   # 0082_support_system (head)
systemctl list-timers talky-support-jobs.timer --no-pager
```

### 6.8 Pilot and roll back

Pilot on one tenant:

```text
SUPPORT_ENABLED=true
SUPPORT_TENANT_ALLOWLIST=<one-tenant-uuid>
```

Validate every UUID before saving. Restart `talky-api` and `talky-api-web`
(both serve `/api/v1`). Then repeat the config call as a user of that tenant:
`enabled` must be `true`, and as a user of any other tenant it must stay `false`.

To switch off quickly: set `SUPPORT_ENABLED=false`, restart the API units. Data
stays in the tables.

---

## 7. Verification checklist after install

Run this after install, after a reboot, and after every deploy. All commands
are read-only. Unit names come from `backend/systemd/`.

### 7.1 Services

```bash
systemctl list-units 'talky-*' --all --no-pager --plain
systemctl is-active talky-api talky-voice-worker talky-dialer-worker \
  talky-reminder-worker talky-voice-gateway talky-redis asterisk nginx
systemctl is-enabled talky-api talky-voice-gateway talky.target
systemctl list-timers 'talky-*' --no-pager
```

Expected: the daemons are `active`. Oneshot units behind timers show
`inactive (dead)`. That is normal. `talky-redis` exists only on the installer
profile. `talky-dashboard` exists only in Linux frontend mode.

### 7.2 Health endpoints

Routes were read from `backend/app/api/v1/endpoints/health.py` and `main.py`
(the API router is mounted at `/api/v1`).

| URL | Meaning | Healthy |
|---|---|---|
| `http://127.0.0.1:8000/health` | Process liveness (defined in `main.py`) | 200 |
| `http://127.0.0.1:8000/api/v1/health` | API-scoped basic health | 200, `"status": "healthy"` |
| `http://127.0.0.1:8000/api/v1/health/detailed` | Container, Redis and session status | 200 |
| `http://127.0.0.1:8000/api/v1/healthz/live` | Event loop alive | 200 `{"alive": true}` |
| `http://127.0.0.1:8000/api/v1/healthz/ready` | Drain and capacity gate | 200; 503 with `Retry-After` when draining or at capacity |
| `http://127.0.0.1:8000/api/v1/healthz/deep` | Pings Postgres and Redis (0.5 s budget each) | 200; 503 if a dependency is down |
| `http://127.0.0.1:8000/api/v1/healthz/workers` | Worker heartbeats from Redis | 200 when every worker is healthy, else 503 |
| `http://127.0.0.1:8001/health` | Dashboard API (`talky-api-web`) | 200 |
| `http://127.0.0.1:18080/ready` | Gateway readiness | `ready: true`, `protocol_version: 2`, `pcmu` in `codecs` |
| `http://127.0.0.1:18080/stats` | Gateway stats | JSON with integer `active_sessions` |

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/v1/healthz/ready
curl -fsS http://127.0.0.1:8000/api/v1/healthz/deep
curl -fsS http://127.0.0.1:8000/api/v1/healthz/workers
curl -fsS http://127.0.0.1:18080/ready
```

Over HTTPS: `curl -fsS https://<api_domain>/health`. A passing local check does
not prove public DNS or the carrier path.

### 7.3 Database revision

```bash
cd /opt/talky/backend
venv/bin/alembic -c alembic.ini heads
sudo env -u DATABASE_URL -u READ_DATABASE_URL venv/bin/python scripts/verify_alembic_current_heads.py
```

The second script fails closed unless the database heads equal the repository
heads exactly. `deploy_to_server.sh` runs the same command. Expect
`0082_support_system` in this tree.

### 7.4 Runtime database privileges and RLS

```bash
cd /opt/talky/backend
sudo env -u DATABASE_URL -u READ_DATABASE_URL venv/bin/python scripts/verify_admin_runtime_privileges.py > admin-runtime-privileges.json
venv/bin/python scripts/verify_admin_runtime_privileges.py --contract   # offline inventory
venv/bin/python scripts/verify_rls.py --posture                          # posture only
```

The first command is read-only and exit code 0 is required
(`docs/DEPLOYMENT.md`). It fails on SUPERUSER or BYPASSRLS on the effective or
login role, missing grants or head drift. `verify_rls.py` explains whether RLS
protects anything; the full run (without `--posture`) creates scratch objects, so
read its header before using it on a live database.

### 7.5 Logs

```bash
journalctl -u talky-api -n 200 --no-pager
journalctl -u talky-voice-worker --since '15 min ago'
journalctl -p err --since '1 hour ago'
journalctl -u talky-healthwatch --since '10 min ago'
```

Every API log line carries `[req=<uuid>]`. Ask a user for the `X-Request-ID`
response header and grep for it.

### 7.6 Edge and telephony

```bash
sudo nginx -t
sudo ufw status verbose
sudo ss -ltnp | grep -E ':(80|443|8000|8001|18080|8088|6379|3000)\b'
sudo asterisk -rx 'pjsip show registrations'
sudo asterisk -rx 'core show channels'
docker ps
```

Check: 8000, 8001, 18080, 8088, 6379 and 3000 listen on 127.0.0.1 only (the
shipped `talky-api.service` listens on 0.0.0.0:8000 unless overridden).
Redis (use the pattern from `docs/RUNBOOK.md` so the password is not on the
command line):

```bash
(
  set -Eeuo pipefail
  read -rsp 'Redis password: ' REDISCLI_AUTH && printf '\n'
  export REDISCLI_AUTH
  docker compose -f /etc/talky/compose.yml exec -T -e REDISCLI_AUTH redis redis-cli ping
)
```

(`/etc/talky/compose.yml` is the installer's file; the production box uses
`/opt/talky/docker-compose.yml`.)

### 7.7 Smoke list (non-destructive)

- [ ] Dashboard loads at `https://<dashboard_domain>` and shows the sign-in page.
- [ ] Admin loads at `https://<admin_domain>`.
- [ ] `https://<api_domain>/health` returns 200 with a valid certificate.
- [ ] `/api/v1/healthz/deep` returns 200 (database and Redis reachable).
- [ ] `/api/v1/healthz/workers` returns 200 (all three workers beating).
- [ ] Gateway `/ready` shows protocol 2 and `pcmu`.
- [ ] `alembic current` equals `alembic heads`.
- [ ] `verify_admin_runtime_privileges.py` exits 0.
- [ ] `systemctl --failed` lists no `talky-*` unit.
- [ ] After a reboot, repeat all of the above.
- [ ] Support: `/api/v1/support/config` returns `"enabled": false` (6.7).
- [ ] A **controlled** call to an owned, authorised number (see `docs/production-readiness/first-call-setup.md`). The installer never does this for you.

---

## 8. Upgrades, deploys, backups and monitoring

### 8.1 Two profiles, two update paths

| Profile | Update tool | Status |
|---|---|---|
| Existing production (`admins@` host, root-owned units, Docker Postgres) | `deploy_to_server.sh` | Documented in `docs/DEPLOYMENT.md` |
| Fresh host built by `install_talklee.sh` (`talky` account, external DB) | **No tool on `main`** | `INSTALLATION.md`: the unchanged deploy script "must not be used on the new profile" because it hardcodes `root:admins` |

For the fresh profile the repo only documents the gateway publication commands
(`scripts/installation/README.md`, "Updates and recovery"):

```bash
# Only after the release's source, migration, backup and zero-call gates.
sudo install -d -o root -g talky -m 0750 /opt/talky/runtime/bin
sudo install -o root -g talky -m 0750 "$gateway_candidate" /opt/talky/runtime/bin/.voice_gateway.next
sudo mv -f /opt/talky/runtime/bin/.voice_gateway.next /opt/talky/runtime/bin/voice_gateway
```

`$gateway_candidate` must come from `build_voice_gateway_release.sh` run on the
exact reviewed checkout. A full update/rollback procedure for the fresh profile
is **Not verified** on `main`; section 11 notes Codex's unmerged `fresh_release.py`.

### 8.2 `deploy_to_server.sh` (existing production profile)

Environment variables it reads:

| Variable | Default / rule |
|---|---|
| `TALKY_PROD_HOST` | `admins@144.76.17.150` |
| `TALKY_PROD_KEY` | `$HOME/.ssh/id_rsa_openssh` |
| `TALKY_DEPLOY_BRANCH` | `main` |
| `TALKY_DEPLOY_SHA` | local `HEAD`, resolved once to a full SHA |
| `TALKY_DEPLOY_DRAIN_MANIFEST` | **required**, no default |
| `TALKY_DEPLOY_DRAIN_MANIFEST_SHA256` | **required** |
| `TALKY_DEPLOY_DRAIN_REPLAY_DIR` | `<git common dir>/talky-deploy-drain-used` |
| `TALKY_LOCAL_PYTHON` | first `python3` or `python` |

What it does, in order (read from the script):

1. Refuses a dirty local tree. Freezes one full SHA. Fetches `origin/<branch>`
   and proves the SHA is on it.
2. Verifies the drain manifest with `backend/scripts/verify_deploy_drain_manifest.py`
   (candidate, freshness, digest, single use).
3. SSH to the server (`ssh -t`; you type the sudo password). Refuses a dirty
   `/opt/talky`. Checks the toolchain, `backend/venv/bin/python` and
   `backend/venv/bin/alembic`.
4. Builds an isolated detached worktree of the SHA: import smoke test, gateway
   build and CTest, `reconcile_asterisk_release.sh --check-only`, and
   `inbound-synthetic-call.sh --check-config-file /etc/talky/inbound-synthetic.env`.
5. Proves the running gateway has zero active sessions (`/stats`).
6. Saves the `talky-api-web.service` unit, then `git checkout --detach <SHA>`.
7. Publishes the tested gateway binary atomically, runs
   `sudo bash backend/systemd/install-services.sh`, and checks required units
   are loaded and enabled.
8. Rechecks zero sessions, restarts the gateway, and proves `/ready` and the
   build SHA.
9. `sudo systemctl start talky-migrate.service`, then
   `verify_alembic_current_heads.py` and `verify_admin_runtime_privileges.py`.
10. Reconciles Asterisk, sets `APP_VERSION` with `set_release_version.py`,
    restarts `talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker talky-api-web`,
    restarts `talky-inbound-synthetic.timer` and `talky-trunk-status`.
11. Requires listed units active, then liveness, readiness, deep readiness and
    dashboard-API (`:8001/health`) probes, then a per-process version check.

If it aborts before the migration succeeds, it restores the previous checkout
and reruns that checkout's `install-services.sh`, without restarting services.
After a successful migration it never rolls code backward.

Notes for a new server:

- The script requires `/etc/talky/inbound-synthetic.env` to pass its config check.
  Create it from `backend/systemd/inbound-synthetic.env.example` (see
  `docs/DEPLOYMENT.md` for the DID rules).
- The script is built for `admins@144.76.17.150`. Override `TALKY_PROD_HOST` and
  `TALKY_PROD_KEY` only for a host that has the same layout.
- A clean checkout of `origin/main` is required locally; the Support change
  must be committed and pushed before a deploy can carry it.

### 8.3 Migrations at deploy time

Migrations are a separate oneshot, never run at boot:

```bash
sudo systemctl start talky-migrate.service      # alembic upgrade head
journalctl -u talky-migrate --since '10 min ago'
sudo systemctl show talky-migrate.service -p Result -p ExecMainStatus
```

The unit has `TimeoutStartSec=180`. On the installer profile the drop-in also
requires `/etc/talky/migration.env`. Create it only when you are about to
migrate, as a mode-0600 file whose content is only the privileged
`MIGRATION_DATABASE_URL`. The API and workers never load that file.

Before any migration: take and verify a backup, drain calls, and rehearse on an
isolated copy (`docs/production-readiness/database-recovery.md`).

### 8.4 Rollback

Rolling code back does not roll the database back (`docs/DEPLOYMENT.md`).
Rollback starts with **stopping ingress**, not with restarting processes under
live calls. The repo has no approved ingress-disable command for the current
Asterisk-only topology; if you cannot durably disable and prove rejection of
inbound calls, rollback is blocked. The SHA you roll back to must contain the
authenticated gateway contract and `build_voice_gateway_release.sh`. Follow
the script in `docs/DEPLOYMENT.md` ("Rollback") instead of improvising.

Do not run `alembic downgrade` unless you have verified the old code works with
the newer schema, or the migration is designed to downgrade. `0082` refuses to
downgrade once support records exist.

### 8.5 Backups

Two mechanisms exist on `main`.

**A. Existing production: `talky-db-backup`.** Timer at 02:30 UTC (random delay
up to 10 min, `Persistent=true`). Runs `backend/deploy/db-backup.sh` with
`backend/deploy/backup_snapshot.py`. Defaults: container `talky-postgres-1`,
database `talkyai`, role `talkyai`, directory `/var/backups/talky`, keep 30
days (`TALKY_BACKUP_DIR`, `TALKY_BACKUP_KEEP_DAYS`). It needs a root-owned file
`/etc/talky/db-backup.env`, mode 0600, containing only:

```ini
TALKY_BACKUP_PRINCIPAL_ID=<existing-platform-admin-UUID>
```

(`docs/RUNBOOK.md`, "Backups"). A plain `pg_dump` is **not** acceptable: under
row level security it silently dumps schema only. The script verifies that
table data entries exist and publishes `talky-<UTC>.dump` plus `.sha256`. A
dump under half the previous size is published but the unit exits nonzero.

```bash
sudo systemctl show talky-db-backup.service -p FragmentPath -p ExecStart -p ActiveState
sudo systemctl start talky-db-backup.service
sudo journalctl -u talky-db-backup.service -n 30 --no-pager
systemctl list-timers talky-db-backup.timer --no-pager
```

This only works where PostgreSQL is the Docker container. On an external
database use B.

**B. External database: `scripts/database/`.** A Python tool with four
subcommands (`audit`, `backup`, `restore`, `health`) using libpq service files
and verified TLS (`scripts/database/README.md`). Copy
`scripts/database/pg_service.conf.example` outside the repo; keep passwords in a
mode-0600 `PGPASSFILE`.

```bash
export PGSERVICEFILE=/etc/talklee/secrets/pg_service.conf
export PGPASSFILE=/etc/talklee/secrets/pgpass

python3 scripts/database/recovery.py audit --service talklee_runtime \
  --app-address <app-server-ip> --receipt /secure/evidence/database-audit.json

python3 scripts/database/recovery.py backup --service talklee_backup \
  --destination /var/backups/talklee/<UTC-stamp> \
  --previous /var/backups/talklee/<previous-stamp>   # omit for the first baseline

python3 scripts/database/recovery.py health \
  --backup-root /var/backups/talklee --maximum-age-hours 26
```

A bundle holds `database.dump`, `database.dump.sha256` and `receipt.json`. It is
a logical dump of the database only. It does not contain role definitions, keys,
recordings or `.env`. Optional timer templates are in `scripts/database/systemd/`
(`talklee-db-backup.service/.timer` at 02:00 UTC and
`talklee-db-backup-health.service/.timer` hourly). They run as a dedicated
`talklee-backup` user, use `/etc/talklee/secrets` and `/var/backups/talklee`, and
are **not** enabled by anything. Create the user, directories and secrets first.
The units point at `/opt/talky/scripts/database/...`.

**Back up what the database dump does not hold:**

- `backend/.env`, and above all `SECRETS_MASTER_KEY`, `CONNECTOR_ENCRYPTION_KEY`
  and `JWT_SECRET`, in a secret manager that is not on this machine.
- Recordings if they are on local disk (`LOCAL_RECORDINGS_DIR`).
- The Asterisk configuration.
- Redis AOF (about 1 second RPO per `docs/RUNBOOK.md`).

Copy backups off the host, encrypted. A daily logical dump gives about a 24 hour
data-loss window at best. The repo does not provide an off-host copy, retention
policy, PITR or alert receiver; each needs an owner.

### 8.6 Restore test

Never restore into the live database. The tested method is a new isolated
cluster from the same image, restoring the full archive with ownership and ACLs
(see `docs/RUNBOOK.md` "Restore test" for the six required boundaries). For the
external-database tool:

```bash
python3 scripts/database/recovery.py restore --service talklee_restore \
  --bundle /var/backups/talklee/<UTC-stamp> \
  --database talky_restore_<date> \
  --runtime-role <restricted-probe-role> \
  --expected-head 0082_support_system \
  --receipt /secure/evidence/restore-<date>.json
```

It creates a new `talky_restore_*` database and refuses to touch an existing
one. `--expected-head` must come from the source release's real Alembic head.
It checks counts, heads and tenant isolation. It does not prove recording, key
or full application recovery. `backend/scripts/verify_restore_compatibility.py`
is a separate pool/import check. Test restores monthly and before a release that
changes recovery assumptions (`docs/RUNBOOK.md`).

### 8.7 Monitoring and logs

| What | How |
|---|---|
| Unit logs | `journalctl -u <unit>`. Identifiers: `talky-api`, `talky-dialer`, `talky-voice`, `talky-reminder`, `talky-voice-gateway`, `talky-healthwatch`, `talky-cleanup`, `talky-db-backup`, `talky-support-jobs`, `talky-asterisk-reconcile`, `talky-inbound-synthetic`, `talky-api-web` |
| Errors | `journalctl -p err --since '1 hour ago'` |
| Worker health | `talky-healthwatch.timer` (every 2 min) runs `deploy/healthwatch.sh`, retries 6 times 10 s apart, and writes `HEALTHWATCH CRITICAL` at error severity if a worker stays unhealthy |
| Watchdog | The three Python workers are `Type=notify` with `WatchdogSec=180`. Crash loops are capped at 5 starts per 300 s, then the unit is `failed`. |
| Telephony metrics | The metrics endpoint needs the header `X-Metrics-Token` equal to `TELEPHONY_METRICS_TOKEN` (`.env.example`) |
| Retention | `talky-cleanup.timer` at 03:00. Dry run unless `CLEANUP_DRY_RUN=false`. Windows `CLEANUP_CALL_EVENTS_DAYS`, `CLEANUP_CALL_LEGS_DAYS`, `CLEANUP_STREAM_EVENTS_DAYS` (default 90). |
| Support | `journalctl -u talky-support-jobs`; Prometheus-style counters live in `app/infrastructure/metrics/support_metrics.py` |

Prometheus and alert configuration files exist (`backend/deploy/prometheus/`,
`alertmanager.yml`), but `docs/DEPLOYMENT.md` says there is no running
Prometheus or Grafana on the production box. No alert receiver is configured by
the repo. Route failed units and stale backups to a real receiver and prove
delivery.

---

## 9. Security hardening checklist

Each item is grounded in a repo file. Tick them before go-live.

### 9.1 Secrets

- [ ] `backend/.env` is mode 0600 (root) or 0640 `root:<service group>`; never in git.
- [ ] `JWT_SECRET`, `SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`, `VOICE_GATEWAY_AUTH_TOKEN`
      are 32+ characters, random, and the last two differ.
- [ ] `SECRETS_MASTER_KEY` is backed up off the machine. Changing it makes stored
      secrets unreadable; unsetting it stops the service from booting in production.
- [ ] `CONNECTOR_ENCRYPTION_KEY` is stable on every process. Old keys go in
      `CONNECTOR_ENCRYPTION_KEYS_OLD` during rotation only.
- [ ] `TELEPHONY_METRICS_TOKEN` is set (an empty value exposes the metrics route).
- [ ] No provider key lives in a `NEXT_PUBLIC_*` or `VITE_*` variable.
- [ ] Carrier credentials (`TRUNK_USER`, `TRUNK_PASS`) come from the environment
      only. `setup-asterisk.sh` documents that earlier hardcoded values sat in a
      public repository history; rotate any that were ever committed.
- [ ] `setup-backend-env.sh` is not used on the new server.
- [ ] Rotation is planned (`docs/RUNBOOK.md` table): JWT 90 days (invalidates all
      sessions), KMS/master key yearly only with a tested re-encryption job,
      database and Redis 180 days, TLS monitored with an alert 14 days before expiry.

### 9.2 Network and firewall

- [ ] Only 80, 443 and the SSH port are open to the world or to management CIDRs;
      SIP and RTP only to carrier CIDRs. The installer's ufw rules do this.
- [ ] API (8000), API-web (8001), gateway (18080), ARI (8088), Redis (6379) and the
      dashboard (3000) listen on loopback only. Override `talky-api.service` if
      needed; it ships with `--host 0.0.0.0`.
- [ ] Asterisk `http.conf` has `bindaddr = 127.0.0.1`.
- [ ] Asterisk and ARI use non-default passwords. The production gate rejects the
      defaults `asterisk`, `ari_password`, `secret` and empty.
- [ ] `TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS=off` for hosted tenants.
- [ ] Outbound calls to a destination require consent. Test calls go only to
      owned or authorised numbers.
- [ ] nginx terminates TLS 1.2+; HTTP redirects to HTTPS; edge rate limits from
      `talky.conf` (or equivalent) are in place for auth routes.

### 9.3 Database

- [ ] Runtime role is NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE. Prove it
      with `verify_admin_runtime_privileges.py` and `verify_rls.py --posture`.
- [ ] Migration owner credentials exist only as `MIGRATION_DATABASE_URL` in an
      operator-only file (`/etc/talky/migration.env`), never in the runtime `.env`.
- [ ] Backups use a separate login (`talklee_backup`) that can read all tables
      despite RLS; the backup credential is never the app credential.
- [ ] Database traffic uses `sslmode=verify-full` with a CA file.
- [ ] RLS caveat: the production database role has previously been a superuser
      with BYPASSRLS, so policies did nothing. **Every SQL statement must also
      carry an explicit `tenant_id` predicate.** Never rely on RLS alone
      (`CLAUDE.md`). The Support migration says the same.
- [ ] `docker-compose.yml` publishes Postgres on `127.0.0.1:5432` only. Keep it so.

### 9.4 Accounts and access

- [ ] The installer profile runs services as `talky` with `NoNewPrivileges=true`
      and `PrivateTmp=true`. The shipped production units run most services as
      root; treat that as a known weakness.
- [ ] The gateway binary is root-owned (`root:talky` or `root:admins`, mode 0750).
- [ ] Only `platform_admin` users reach the staff console (`support:manage`).
      Creating a `platform_admin` is deliberate and rare (`users-roles.md`).
- [ ] Admin build has `VITE_ADMIN_DEV_MODE=false` and `VITE_USE_DUMMY_AUTH=false`.
- [ ] Recording notice, consent and opt-out behaviour is tested before real calls.
- [ ] `CLEANUP_DRY_RUN` stays `true` until the retention windows are approved.

### 9.5 Operational

- [ ] A named owner for TLS renewal, backups, alert receipt and key custody.
- [ ] A tested restore (8.6) and a recorded RPO/RTO.
- [ ] The inbound synthetic call is configured with an always-open DID, or the
      timer is disabled.
- [ ] Changes to `.env` are followed by a restart; systemd does not reload them.

---

## 10. Troubleshooting

Format: symptom, cause, fix. The "Source" line names the file that grounds the
entry. Commands are read-only unless stated.

### Installer and validation

**T01. `ERROR: Refusing existing path: /opt/talky` (or `/etc/nginx`, `/etc/asterisk`, `/var/lib/docker`)**
- Cause: `apply` only runs on an empty host. It checks `/opt/talky`,
  `/var/lib/talky-install`, `/etc/talky`, `/etc/systemd/system/talky-api.service`,
  `/etc/nginx`, `/etc/asterisk`, `/var/lib/docker`, `/usr/sbin/policy-rc.d`.
- Fix: use a new empty VM. There is no supported reset. Do not delete these paths
  to force a rerun.
- Source: `install.py` `ensure_fresh_host()`.

**T02. `Refusing active pre-existing service: nginx` (or `asterisk`, `docker`, `talky-*`)**
- Cause: a service from an earlier attempt or the base image is running.
- Fix: start from a clean image. Do not install nginx, Asterisk or Docker before `apply`.
- Source: `install.py` `ensure_fresh_host()`.

**T03. `Installer source checkout must be completely clean` / `Source checkout must equal the reviewed source_sha`**
- Cause: `git status --porcelain` is not empty, or `HEAD` differs from `source_sha`.
  This includes uncommitted work such as the Support system in a worktree.
- Fix: commit and push; `git checkout --detach <sha>`; copy the full 40-character
  SHA into `install.json`.
- Source: `install.py`.

**T04. `Configuration keys differ from install.example.json`**
- Cause: the JSON has a missing or extra key.
- Fix: start from the example and only change values.

**T05. `Python lock must contain exact versions with SHA256 hashes only`**
- Cause: the file is a plain `requirements.txt`, or contains URLs, `-r`, index lines
  or ranges.
- Fix: generate with `pip-compile --generate-hashes` (4.4.1).

**T06. `DATABASE_URL must verify TLS using sslmode=verify-full` / `Database must be separate from the application host`**
- Cause: URL uses `localhost`, `postgres`, a loopback IP, or lacks the exact
  `sslrootcert=/etc/ssl/certs/talky-db-ca.pem`.
- Fix: use the external hostname covered by the certificate and the exact CA path.
- Source: `install.py`.

**T07. `Reviewed ARI user/password/permissions must match the runtime environment`**
- Cause: `ari.conf` section name or password differs from `ASTERISK_ARI_USER` /
  `ASTERISK_ARI_PASSWORD`, `read_only` is not `no`, or `[general] enabled` is off.
- Fix: make them identical. Use a 32+ URL-safe password.

**T08. `Lab Asterisk credentials are not accepted` / `Asterisk includes must resolve inside the supplied config`**
- Cause: a file contains `local_only_change_me`, `password=1002`, an absolute
  `#include`, or `#exec`.
- Fix: supply reviewed production config only. See 4.5.

**T09. `apply` stops at the database check ("prepare and verify schema before install")**
- Cause: runtime role has superuser, BYPASSRLS, CREATEDB or CREATEROLE; the
  connection is not TLS; or `alembic_version` does not equal the checkout's heads.
- Fix: fix the role; run section 5.4 against this exact commit's migrations; compare
  `alembic heads` with `alembic current`. The installer runs no migrations.
- Source: `DATABASE_CHECK_SCRIPT` in `install.py`.

**T10. `apply` failed halfway and the host is in an unknown state**
- Cause: the installer prints only field/file names and suppresses output.
- Fix: keep the host for diagnosis (`/var/lib/talky-install/state.json`,
  `journalctl`, `apt` logs), correct the input, rebuild on a new empty VM.

**T11. SSH session dropped after `ufw` was enabled**
- Cause: the allowed SSH CIDR did not contain your address. The installer only
  checks `SSH_CONNECTION` when it is set.
- Fix: use the console. Keep console access during `apply`. Set `ssh_source_cidrs`
  correctly before running.

### Application boot and configuration

**T12. `ProductionGateError` at startup (service restarts in a loop)**
- Cause: `ENVIRONMENT=production` and a rule failed. Rules include
  `dev_bypass_in_prod`, `caller_id_enforcement_weakened`,
  `inbound_strict_routing_disabled`, `staging_proof_flag_in_prod`,
  `asterisk_default_password`, `missing_secret`, `weak_secret`, `secret_reuse`,
  `gateway_callback_host_unpinned`, `gateway_callback_url_mismatch`,
  `STRIPE_MOCK_MODE`, `STRIPE_LIVE_KEY`, `STRIPE_SDK_MISSING`, `STRIPE_WEBHOOK_SECRET`.
- Fix: `journalctl -u talky-api -n 100 --no-pager`; the gate lists every violation
  in one message. Fix them all, then restart.
- Source: `backend/app/core/prod_gate.py`.

**T13. Service refuses to boot because of `SECRETS_MASTER_KEY`**
- Cause: unset in production (by design). In development a random key is minted.
- Fix: set the retained key. For a new environment generate 64 hex characters.
  Never change it on a database that already holds secrets.
- Source: `backend/.env.example`.

**T14. Edited `.env` but behaviour did not change**
- Cause: systemd loads `EnvironmentFile` at start only.
- Fix: `sudo systemctl restart talky-api talky-api-web talky-voice-worker talky-dialer-worker talky-reminder-worker`
  (and the gateway for gateway secrets). Source: `docs/RUNBOOK.md`.

**T15. All users logged out after a deploy**
- Cause: `JWT_SECRET` changed. Rotating it invalidates every session.
- Fix: keep it stable; rotate only in a planned window.

**T16. Browser shows CORS errors**
- Cause: `CORS_ORIGINS` lacks the exact dashboard or Admin origin, or the
  frontend points at the wrong `NEXT_PUBLIC_API_BASE_URL` / `VITE_API_BASE_URL`.
- Fix: list both origins exactly (`https://app...`, `https://admin...`), no `*`,
  restart the API.

**T17. `DatabasePoolTimeoutError` in the journal**
- Cause: pool exhausted or a wedged database. `PG_POOL_ACQUIRE_TIMEOUT` (default 10 s)
  makes it fail fast. Units cap pools (`PG_POOL_MAX_SIZE` 15 API, 12 API-web, 10
  voice/dialer, 3 reminder).
- Fix: check `pg_stat_activity` on the database; check for leaked connections;
  raise the timeout only for known slow queries. Source: `.env.example`, units.

**T18. Redis errors (`NOAUTH`, connection refused) and `/healthz/deep` 503**
- Cause: `REDIS_URL` password differs from the container's `requirepass`, or the
  container is down.
- Fix: `docker ps`; compare the password; after a change restart the API units.
  Source: `docs/RUNBOOK.md` ("Redis unhealthy").

**T19. Recordings saved as wav, not mp3**
- Cause: `RECORDING_AUDIO_CODEC=mp3` needs `ffmpeg`; it falls back to wav without it.
  The installer package list has no `ffmpeg`.
- Fix: `sudo apt-get install ffmpeg` (version pinning is your choice).

**T20. There is no platform admin and nobody can open the staff console**
- Cause: self-signup creates a `tenant_admin` of a new tenant; it cannot create a
  `platform_admin`. `main` has no bootstrap script for the first one.
- Fix: per `users-roles.md`, a platform admin is made deliberately (in the
  database, or by an existing platform admin through the role API). Do this with
  the owner's approval and an audit note. Codex's branch adds a
  `bootstrap_first_admin.py` (unmerged, section 11). Exact SQL is **Not verified**
  here; do not guess it.

### Migrations

**T21. `alembic current` shows nothing, or an old revision, after "bootstrap"**
- Cause: schema loaded but `stamp` or `upgrade head` was skipped or failed.
- Fix: run `alembic stamp 0008_tenant_voice_tuning` once on a fresh load, then
  `upgrade head`. Never `stamp head` on an old snapshot.
  Source: `backend/database/MIGRATIONS.md`.

**T22. `RuntimeError: MIGRATION_DATABASE_URL or DATABASE_URL is required for Alembic migrations.`**
- Cause: neither variable is in the process environment. `alembic` run by hand
  does not read `.env` for you.
- Fix: export the owner DSN first (5.4), or run through `talky-migrate.service`.
  Source: `backend/Alembic/env.py`.

**T23. `talky-migrate` fails immediately on the installer profile**
- Cause: the drop-in requires `/etc/talky/migration.env` with a non-empty
  `MIGRATION_DATABASE_URL`; the installer does not create it.
- Fix: create it (mode 0600, root) with only that variable.
  Source: `install.py` (`20-migration-identity.conf`).

**T24. Two Alembic heads, or "Multiple head revisions are present"**
- Cause: two branches each added a revision on top of `0081`. Example: this tree's
  `0082_support_system` and Codex's `0082_ubuntu_admin_merge` (section 11).
- Fix: do not merge both unchanged. The CI rule is one head. A reviewed merge
  revision or renumbering is needed; that is a code change, not an operator step.
  Check with `venv/bin/alembic -c alembic.ini heads`.

**T25. `Refusing to drop support records` on `alembic downgrade`**
- Cause: by design, `0082` downgrade refuses while tickets, conversations or
  staff-written articles exist.
- Fix: export the records and use a reviewed forward migration. Do not delete rows
  to make the downgrade pass.

### Support system

**T26. Support routes return 404**
- Cause: `SUPPORT_ENABLED=false` (the shipped default). Only `/support/config`
  answers, with `enabled: false`.
- Fix: follow 6.8 for a pilot. Restart the API units.

**T27. Support is off for every tenant although `SUPPORT_ENABLED=true`**
- Cause: a malformed entry in `SUPPORT_TENANT_ALLOWLIST` (not a UUID) invalidates
  the whole list. Log line: `support_allowlist_invalid ... support_disabled=true`.
- Fix: correct or empty the list. Check with
  `journalctl -u talky-api | grep support_allowlist_invalid`.

**T28. Support is on for one tenant but not another**
- Cause: an allowlist is set. Empty means all tenants; non-empty means only those.
- Fix: add the tenant UUID or empty the list.

**T29. `talky-support-jobs` runs but nothing happens**
- Cause: `SUPPORT_ENABLED=false` makes it log `support_jobs_skipped reason=support_disabled`
  and exit 0. Or only the 15-minute deadline pass ran; housekeeping is the first
  quarter of each hour.
- Fix: enable support, or force with `--hourly` (6.6).

**T30. `import_support_articles.py`: `DATABASE_URL is required` or `registry.ts not found`**
- Cause: environment file not loaded; or an importer older than the git
  fallback on a sparse checkout without `Talk-Leee/`.
- Fix: run through `systemd-run ... -p EnvironmentFile=...` (6.3); update to
  `main`, where the importer reads git objects when the folder is absent, or
  pass `--git-ref HEAD`.

**T31. Support attachment upload fails in production**
- Cause: no S3 settings, and `SUPPORT_ALLOW_LOCAL_STORAGE` is not `true`; or local
  directory is not writable by the service account.
- Fix: configure `S3_*` (6.5), or enable local storage deliberately with a durable,
  writable `SUPPORT_ATTACHMENT_LOCAL_DIR`.

**T32. Support emails link to `http://localhost:3000`**
- Cause: `FRONTEND_URL` is unset; the Support config falls back to it.
- Fix: set `FRONTEND_URL` to the dashboard URL.

### Services, workers and health

**T33. A timer-activated unit shows `inactive (dead)`**
- Cause: normal for oneshots between runs (`talky-cleanup`, `-healthwatch`,
  `-support-jobs`, `-db-backup`, `-inbound-synthetic`, `-asterisk-reconcile`).
- Fix: check `systemctl list-timers 'talky-*'` and `journalctl -u <unit>`.
  Only `failed` is a fault. Source: `docs/RUNBOOK.md`.

**T34. A worker is `failed` and does not come back**
- Cause: it hit `StartLimitBurst=5` in 300 s, or the watchdog (180 s) killed it
  repeatedly.
- Fix: read `journalctl -u talky-dialer-worker -n 300 --no-pager`, fix the cause,
  then `sudo systemctl reset-failed talky-dialer-worker` and restart.
  Source: `docs/RUNBOOK.md`.

**T35. `/api/v1/healthz/workers` returns 503**
- Cause: a worker heartbeat in Redis is stale or missing, or Redis is unreachable.
  `talky-healthwatch` logs `HEALTHWATCH CRITICAL` after 6 failed tries.
- Fix: check the three worker units and Redis (T18, T34).

**T36. `/api/v1/healthz/ready` returns 503 with `Retry-After`**
- Cause: the process is draining (shutdown started) or at its call capacity.
- Fix: wait; check `MAX_TELEPHONY_SESSIONS` use and active calls. Not an outage by itself.

**T37. Gateway exits with code 2 at start**
- Cause: `INTERNAL_SERVICE_TOKEN` or `VOICE_GATEWAY_AUTH_TOKEN` missing, short, or the
  same value. `build_voice_gateway_release.sh` expects exit 2 when the token is absent.
- Fix: set two distinct 32-512 character secrets in `backend/.env` and restart the gateway.

**T38. Gateway `/ready` is not `ready: true`, protocol 2 and `pcmu`, or `build_sha` differs**
- Cause: an old binary, or a binary built from a different commit than the checkout.
- Fix: rebuild with `VOICE_GATEWAY_BUILD_SHA=<current sha>` (5.8) and redeploy it.
  `deploy_to_server.sh` fails the deploy on this.

**T39. `talky-api` starts but port 8000 is reachable from the internet**
- Cause: the shipped unit uses `--host 0.0.0.0`.
- Fix: add a drop-in `ExecStart=` with `--host 127.0.0.1`, or firewall 8000.

### Telephony

**T40. Calls do not connect**
- Cause: several are possible. Telephony is Asterisk only.
- Fix: `systemctl status asterisk`; `curl localhost:18080/stats`; refresh trunk
  evidence with `sudo systemctl start talky-trunk-status.service`; read
  `journalctl -u talky-voice-worker -n 300 --no-pager`; check the global concurrency
  cap in `app/domain/services/telephony_concurrency_limiter.py`. Source: `docs/RUNBOOK.md`.

**T41. ARI check returns 401 or 404**
- Cause: `setup-asterisk.sh` documents it: 200 is good, 401 means wrong password,
  404 means ARI is off.
- Fix: align `ARI_PASSWORD` in `ari.conf` and `ASTERISK_ARI_PASSWORD`; confirm
  `http.conf` is enabled on 127.0.0.1:8088.

**T42. A tenant trunk never loads**
- Cause: `pjsip.conf` lacks `#include pjsip.d/*.conf`; or `/etc/asterisk/pjsip.d` is
  not `asterisk:asterisk` mode 2770; or the backend user is not in group `asterisk`.
  A `pjsip reload` still reports success. Source: `setup-asterisk.sh` step 8b.
- Fix: restore those three prerequisites, then reconcile.

**T43. `reconcile_asterisk_release.sh` refuses to apply**
- Cause: it needs zero active channels and an unheld lock. The one-minute timer
  retries next tick.
- Fix: wait for calls to end; do not force it during calls.

**T44. `talky-inbound-synthetic.service` fails**
- Cause: `/etc/talky/inbound-synthetic.env` missing or unreadable, DID not 7-15
  digits, endpoint name invalid, or wait outside 8-60 s.
- Fix: create it from `backend/systemd/inbound-synthetic.env.example`; validate with
  `sudo bash /opt/talky/backend/deploy/inbound-synthetic-call.sh --check-config-file /etc/talky/inbound-synthetic.env`.
  Or disable the timer until a real DID exists.

### Deploy and backup

**T45. `deploy_to_server.sh`: "Set TALKY_DEPLOY_DRAIN_MANIFEST and ..._SHA256"**
- Cause: the drain manifest inputs are mandatory.
- Fix: follow the manifest contract in `docs/DEPLOYMENT.md`. Do not bypass for an
  application-only change.

**T46. Deploy says the server checkout is dirty, or checkout fails**
- Cause: `/opt/talky` has tracked or untracked drift. The deploy refuses. Operators
  have also reported that root-owned tracked directories can break `git checkout`
  (team notes; not stated in a repo doc), and a failed checkout is not rolled back.
- Fix: `git -C /opt/talky status --short`; reconcile the drift; fix ownership
  before the next run. Never rsync source onto the server.

**T47. Deploy blocked: "Gateway still owns N active session(s)"**
- Cause: live calls are on the gateway. The deploy proves zero sessions twice.
- Fix: freeze ingress and outbound, wait for calls to end, retry.

**T48. Backup unit fails: `TALKY_BACKUP_PRINCIPAL_ID is required`**
- Cause: `/etc/talky/db-backup.env` missing, wrong mode or empty.
- Fix: create it as in 8.5 with an existing platform-admin UUID. The unit's
  `EnvironmentFile=-` lets the unit install without it, but the run fails.

**T49. Backup exits nonzero but a file exists**
- Cause: the dump is under half the previous size; it is published on purpose and
  the unit exits nonzero so the alert fires.
- Fix: investigate data loss before deleting the previous good archive.

**T50. Backup contains no table data**
- Cause: a bare `pg_dump` under forced RLS dumps schema only.
- Fix: use the service/script, which sets the bypass context and verifies table
  data entries. Source: `backend/deploy/db-backup.sh` header.

### Frontend and edge

**T51. `npm ci` / build fails with an engine or type-stripping error**
- Cause: Node older than 22.18. Admin requires `>=22.18`.
- Fix: install Node 22.18 or newer (the installer takes a pinned official archive).

**T52. nginx returns 502 or WebSocket calls drop**
- Cause: API not listening on `127.0.0.1:8000`, or WebSocket upgrade headers/timeouts
  missing. The installer's site sets `Upgrade`, `Connection` and `proxy_read_timeout 3600s`.
- Fix: `curl http://127.0.0.1:8000/health`; `sudo nginx -t`; check the voice WebSocket
  locations if you use `talky.conf`.

**T53. Cleanup worker deletes nothing**
- Cause: `CLEANUP_DRY_RUN=true` is the default and the installer enforces it.
- Fix: leave it until retention is approved; then set `false` and restart nothing
  (the oneshot reads `.env` at each run).

**T54. Trunk status looks wrong after install**
- Cause: the installer enables `talky-trunk-status.timer`, which the shipped unit
  calls retired, while `install-services.sh` enables the service instead.
- Fix: `systemctl status talky-trunk-status talky-trunk-status.timer`. Whether the
  timer-started service is equivalent is **Not verified**.

---

## 11. What exists vs what was claimed (Codex comparison)

The owner asked for the Ubuntu installation README that Codex "claimed it has
done". This section compares `main` (this tree, `0ef59596`) with Codex's branch
`codex/customer-support-premortem-20261029`. It reports what the files show. It
does not judge their quality.

### 11.1 Facts about the branch

| Item | Value (from `git`) |
|---|---|
| Branch tip | `9613abde` "Verify preserved support schemas through the actual migration driver" |
| Relation to main | Branch contains `0ef59596` (merge base equals `origin/main`) and is 47 commits ahead. **Not merged into main.** |
| Published | A remote branch `origin/codex/customer-support-premortem-20261029` exists. Codex's handover calls it PR 28. |
| Size | 168 files changed, 29,074 insertions, 485 deletions versus `origin/main` |

### 11.2 Installation files: main vs branch

| File | On `main` | On the branch |
|---|---|---|
| `INSTALLATION.md` | Yes (182 lines) | Yes, rewritten (+109/-20): clone-first steps, pinned candidate SHA, `frontend_mode=linux` recommended |
| `install_talklee.sh` | Yes | Yes |
| `scripts/installation/README.md` | Yes (219 lines) | Yes, changed (135 changed lines) |
| `scripts/installation/install.py` | Yes (711 lines) | Yes, changed (391 changed lines) |
| `scripts/installation/install.example.json`, `*.env.example`, `test_install.py` | Yes | Yes, changed |
| `scripts/installation/PREPARE-UBUNTU.md` (267 lines) | **No** | Yes. Step-by-step preparation: inputs, database path, install and record. |
| `scripts/installation/FRESH-RELEASE.md` (221 lines) | **No** | Yes. Update and recovery on the fresh profile. |
| `scripts/installation/prepare.py` (243 lines) | **No** | Yes. Writes `apt-versions.json`, the verified lock and config templates into a new protected directory. |
| `scripts/installation/fresh_release.py` (979 lines) | **No** | Yes. Commands `prepare`, `activate`, `recover`, `resume-schedules`. |
| `scripts/installation/requirements-linux-py312.lock` (3426 lines) and `.provenance.json` | **No** | Yes. Provenance says `uv 0.9.17`, 157 resolved packages, Python 3.12, `x86_64-unknown-linux-gnu`, cross-platform resolution. |
| `blank_host_rehearsal.py`, `guest_rehearsal.py`, `guest_release_rehearsal.py` | **No** | Yes (VM rehearsal drivers) |
| `test_prepare.py`, `test_fresh_release.py` | **No** | Yes |

Other branch-only files that the Ubuntu guides refer to:
`scripts/database/runtime_grants.py`, `scripts/database/RUNTIME_GRANTS.md`,
`scripts/database/backup_status.py`, `docs/operations/FIRST-ADMIN.md`,
`backend/scripts/bootstrap_first_admin.py`, and migrations
`0082_ubuntu_admin_merge` and `0083_customer_support_contracts`.

Branch-only handover (outside git): `output/production-handover-20261030/`
(`README.md`, `handover-manifest.json`, `evidence/`). That folder is on the
shared checkout's disk and is gitignored, so it is not in any branch.

### 11.3 Claims compared

| Claim (Codex branch or handover) | Check | Result |
|---|---|---|
| "An Ubuntu installation README exists" | `INSTALLATION.md` and `scripts/installation/README.md` are already on `main` (commit `f892ddd3`) | True for `main`. The branch has a longer, more complete set (PREPARE-UBUNTU, FRESH-RELEASE). |
| "A preparation helper and a pinned Linux lock file exist" | Not in `main` | Exist only on the branch |
| "A fresh-profile update and recovery path exists (`fresh_release.py`)" | Not in `main`. `main` says no tool exists for the fresh profile (8.1). | Exist only on the branch |
| Candidate `9613abde` passed all six CI workflows and an Ubuntu VM install/update/recovery matrix | Cited as GitHub Actions run IDs in the handover; no network access here | **Not verified** by this guide |
| "Not live, not certified for paid use" | Handover says so itself | Consistent with `AGENTS.md` freeze |
| Installation, update and backup/restore walkthrough videos | Handover lists them as not delivered | Not on disk in the repo |
| Head after the branch is `0083_customer_support_contracts` | Branch migration files read | True for the branch |

### 11.4 Conflicts to resolve before both lines merge

1. **Migration numbering clash.** This tree's Support migration is
   `0082_support_system` with `down_revision = 0081_admin_payment_merge`. The
   branch has `0082_ubuntu_admin_merge` (down revisions `0081_admin_payment_merge`
   and `0075_release_history_merge`) and `0083_customer_support_contracts` on top.
   Merging both unchanged gives two heads at revision 0082. The repo rule is one
   head. See T24.
2. **Different "support" features.** The branch's `0083` adds support *contracts*
   (settings and a selected-telephony column). It contains no ticket system,
   no `talky-support-jobs` unit and no `/support` routes (checked by listing the
   branch tree). The Support system in this guide is separate work.
3. **Installer service list.** On `main` the installer enables a fixed list of
   services (4.7 step 19). Whether the branch installer enables
   `talky-support-jobs.timer` is not applicable: that unit is not in the branch.
4. **Production state.** The handover records live production at an older
   revision (`0081_admin_payment_merge`) and with a database topology the
   installer profile rejects. The installer profile is for a new host; it does
   not migrate that database.

### 11.5 Recommendation (process only)

Treat `main`'s `INSTALLATION.md` plus this guide as the installation reference
for what is merged. Treat Codex's `PREPARE-UBUNTU.md` and `FRESH-RELEASE.md` as
reviewable proposals. Do not follow them as instructions until they are merged
and their migration numbering is reconciled.

---

## 12. Appendix

### 12.1 Environment variable names in `backend/.env.example`

Names and one-line meaning only. Values are never listed. A name in brackets is
commented out in the file.

**Email**

| Name | Meaning |
|---|---|
| `EMAIL_USER` | Sender mailbox for outgoing mail |
| `EMAIL_PASS` | Mailbox password or app password |

**Speech and voice providers**

| Name | Meaning |
|---|---|
| `DEEPGRAM_API_KEY` | Speech-to-text key |
| `ASSEMBLYAI_API_KEY` | Optional second STT provider key |
| `ASSEMBLYAI_API_KEYS` | Optional comma-separated key pool |
| `ASSEMBLYAI_MAX_CONCURRENT` | Streams per process |
| `ELEVENLABS_API_KEY` | Optional TTS key |
| `TTS_PROVIDER` | Default TTS provider name |
| `TTS_SOURCE_SAMPLE_RATE` | Sample rate for TTS output |

**Language models**

| Name | Meaning |
|---|---|
| `GROQ_API_KEY` | Groq LLM key |
| `CEREBRAS_API_KEY` | Optional Cerebras key |
| `GEMINI_API_KEY` | Optional Gemini key |
| `DEEPSEEK_API_KEY` | Optional DeepSeek key |
| `DEEPSEEK_API_KEYS` | Optional DeepSeek key pool |

**Storage and call feedback**

| Name | Meaning |
|---|---|
| `S3_BUCKET_NAME`, `S3_REGION` | Bucket for recordings, feedback audio and Support attachments |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Storage credentials |
| `S3_ENDPOINT_URL` | Set for R2 or MinIO; omit for AWS |
| `S3_CONNECT_TIMEOUT_SECONDS`, `S3_READ_TIMEOUT_SECONDS` | Client timeouts |
| `S3_PERMANENT_DELETE_TIMEOUT_SECONDS`, `S3_PERMANENT_DELETE_MAX_PAGES`, `S3_PERMANENT_DELETE_MAX_VERSIONS` | Bounds for fail-closed permanent erasure |
| `CALL_FEEDBACK_MAX_AUDIO_BYTES` | Maximum reviewer voice-note size |
| `CALL_FEEDBACK_TRANSCRIPTION_TIMEOUT_SECONDS`, `CALL_FEEDBACK_PENDING_RETRY_AFTER_SECONDS` | Feedback transcription timing |
| `CALL_FEEDBACK_DEEPGRAM_MODEL`, `CALL_FEEDBACK_DEEPGRAM_LANGUAGE` | Feedback transcription model and language |
| `LOCAL_FEEDBACK_DIR` | Local folder when S3 is not used |
| `CALL_FEEDBACK_ALLOW_LOCAL_STORAGE` | Allow local feedback storage in production |
| `RECORDING_AUDIO_CODEC`, `RECORDING_AUDIO_BITRATE` | Recording format (mp3, opus, wav) and bitrate |

**URLs, database, Redis**

| Name | Meaning |
|---|---|
| `FRONTEND_URL` | Dashboard URL for email links |
| `API_BASE_URL` | Public API URL |
| `DATABASE_URL` | Runtime PostgreSQL connection |
| `PG_POOL_ACQUIRE_TIMEOUT` | Seconds to wait for a pooled connection |
| `REDIS_URL` | Redis connection |

**Authentication and encryption**

| Name | Meaning |
|---|---|
| `JWT_SECRET` | Token signing secret |
| `SECRET_KEY` | Legacy alternative secret |
| `MASTER_KEY` | Master encryption key for sensitive data |
| `SECRETS_MASTER_KEY` | Key-encryption key for the secrets store |
| `CONNECTOR_ENCRYPTION_KEY` | Fernet key for connector tokens |
| `CONNECTOR_ENCRYPTION_KEYS_OLD` | Prior Fernet keys during rotation |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Shared Google OAuth app |
| `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_LOGIN_URL` | Salesforce connector |
| `SESSION_LIFETIME_HOURS` | Session length |

**Environment and monitoring**

| Name | Meaning |
|---|---|
| `ENVIRONMENT` | `development`, `staging` or `production` |
| `DEBUG`, `LOG_LEVEL`, `WORKER_LOG_LEVEL` | Logging and debug |
| `INBOUND_TRANSFER_STAGING_PROOF_ENABLED`, `..._TENANT_ID`, `..._CONFIG_ID` | Staging-only proof gate; production refuses them |
| `TELEPHONY_METRICS_TOKEN` | Token for the telephony metrics route |
| `TELEPHONY_CANARY_TENANT_ID`, `..._CONFIG_ID`, `..._DID`, `..._CANDIDATE_DIGEST`, `..._RUN_ID`, `..._GATE_STARTED_AT` | Canary evidence identity |

**Telephony and gateway**

| Name | Meaning |
|---|---|
| `RTP_REMOTE_IP`, `RTP_REMOTE_PORT`, `RTP_LOCAL_PORT`, `RTP_CODEC` | Legacy raw-RTP path |
| `FREESWITCH_ESL_HOST`, `FREESWITCH_ESL_PORT`, `FREESWITCH_ESL_PASSWORD` | FreeSWITCH (not deployed) |
| `ASTERISK_HANGUP_CONFIRM_TIMEOUT_S`, `ASTERISK_HANGUP_CONFIRM_POLL_S` | Hangup proof timing |
| `TELEPHONY_PJSIP_AUTO_RELOAD`, `TELEPHONY_PJSIP_CONF_DIR` | Tenant trunk file writing and reload |
| `TELEPHONY_TRUNK_STATUS_FRESHNESS_SECONDS` | Max age of trunk evidence |
| `TELEPHONY_ALLOW_PRIVATE_SIP_TARGETS` | Keep off for hosted tenants |
| `INTERNAL_SERVICE_TOKEN` | Dialer and gateway callback secret |
| `VOICE_GATEWAY_AUTH_TOKEN` | Gateway control-plane secret |
| `VOICE_GATEWAY_CALLBACK_HOST`, `BACKEND_INTERNAL_URL` | Pinned loopback callback origin |
| `VOICE_GATEWAY_REQUIRE_CONFIG_ACK` | Require protocol v2 config acknowledgement |
| `TELEPHONY_MEDIA_WATCHDOG_INTERVAL_S` | Dead-media check cadence |
| `TELEPHONY_STATE_BACKEND` | `memory` or `redis` |
| [`POD_ID`] | Heartbeat key prefix |
| `MEDIA_GATEWAY_TYPE`, `MAX_CONCURRENT_PIPELINES` | Worker media and concurrency |
| [`VOICE_GATEWAY_STT_REORDER`], [`VOICE_GATEWAY_STT_REORDER_WINDOW`], [`VOICE_GATEWAY_STT_REORDER_HOLD_MS`] | STT reorder tap |
| [`CALL_WRAP_UP_LEAD_SECONDS`] | Wrap-up nudge before max duration |

**Support system**

| Name | Default | Meaning |
|---|---|---|
| `SUPPORT_ENABLED` | `false` | Master switch |
| `SUPPORT_TENANT_ALLOWLIST` | empty | Pilot tenant UUIDs |
| `SUPPORT_BOT_ENABLED` | `true` | Help bot |
| `SUPPORT_BOT_MODEL` | `openai/gpt-oss-20b` | Bot model |
| `SUPPORT_RATE_LIMIT_PER_MINUTE` | `10` | Per-user bot rate |
| `SUPPORT_DAILY_MESSAGE_CAP_PER_TENANT` | `300` | Daily cap |
| `SUPPORT_MAX_TURNS` | `20` | Turns per conversation |
| `SUPPORT_ATTACHMENT_MAX_MB` | `10` | Upload size |
| `SUPPORT_SLA_URGENT_FIRST_RESPONSE_MINUTES` / `_RESOLUTION_MINUTES` | 60 / 480 | Urgent deadlines |
| `SUPPORT_SLA_HIGH_FIRST_RESPONSE_MINUTES` / `_RESOLUTION_MINUTES` | 240 / 1440 | High deadlines |
| `SUPPORT_SLA_NORMAL_FIRST_RESPONSE_MINUTES` / `_RESOLUTION_MINUTES` | 1440 / 4320 | Normal deadlines |
| `SUPPORT_SLA_LOW_FIRST_RESPONSE_MINUTES` / `_RESOLUTION_MINUTES` | 4320 / 10080 | Low deadlines |
| `SUPPORT_STAFF_ALERT_EMAIL` | empty | Staff alert address |
| `SUPPORT_TRANSCRIPT_RETENTION_DAYS` | `180` | Unticketed transcript retention |
| `SUPPORT_ALLOW_LOCAL_STORAGE` | `false` | Local attachments in production |
| [`SUPPORT_ATTACHMENT_LOCAL_DIR`] | `./support_attachments` | Local attachment folder |

**Names used by other files but not listed in `backend/.env.example`**

| Name | Where it appears |
|---|---|
| `ASTERISK_ARI_HOST`, `ASTERISK_ARI_PORT`, `ASTERISK_ARI_USER`, `ASTERISK_ARI_PASSWORD`, `ASTERISK_ARI_APP`, `ASTERISK_GATEWAY_BASE_URL`, `ASTERISK_GATEWAY_RTP_IP`, `ASTERISK_RTP_PORT_START`, `ASTERISK_RTP_PORT_END` | `scripts/installation/backend.env.example` |
| `TELEPHONY_PROVIDER`, `TELEPHONY_ADAPTER` | installer template; `prod_gate.py` |
| `TELEPHONY_DEV_BYPASS_GUARD_ERRORS`, `TELEPHONY_LOCAL_DEV`, `CALLER_ID_ENFORCEMENT_MODE`, `TELEPHONY_INBOUND_REQUIRE_TENANT`, `INBOUND_STRICT_ROUTING`, `TELEPHONY_STRICT_INBOUND_ROUTING` | installer template; production gate |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_BILLING_DISABLED` | installer template; `prod_gate.py` |
| `SESSION_COOKIE_SECURE`, `JWT_EXPIRY_HOURS` | installer template |
| `CLEANUP_DRY_RUN`, `CLEANUP_CALL_EVENTS_DAYS`, `CLEANUP_CALL_LEGS_DAYS`, `CLEANUP_STREAM_EVENTS_DAYS`, `CLEANUP_BATCH_SIZE`, `CLEANUP_MAX_BATCHES_PER_TABLE` | `cleanup_worker.py` header |
| `LOCAL_RECORDINGS_DIR`, `RECORDINGS_STORAGE_DIR` | installer template |
| `EMAIL_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME` | installer template |
| `SLACK_WEBHOOK_URL` | `notification_service.py` |
| `PG_POOL_MIN_SIZE`, `PG_POOL_MAX_SIZE`, `TELEPHONY_ENABLED` | set inside the unit files |
| `MIGRATION_DATABASE_URL`, `READ_DATABASE_URL` | `Alembic/env.py`; installer rules |
| `APP_VERSION` | written by `set_release_version.py` at deploy |
| `TALKY_BACKUP_DIR`, `TALKY_BACKUP_KEEP_DAYS`, `TALKY_BACKUP_PRINCIPAL_ID`, `TALKY_PG_CONTAINER`, `TALKY_PG_USER`, `TALKY_PG_DB`, `TALKY_BACKUP_MIN_TABLE_DATA` | `db-backup.sh` and its unit |
| `INBOUND_SYNTHETIC_DID`, `INBOUND_SYNTHETIC_TRUNK_ENDPOINT`, `INBOUND_SYNTHETIC_WAIT_SECONDS` | `/etc/talky/inbound-synthetic.env` |
| `TALKY_HEALTHWATCH_URL`, `TALKY_HEALTHWATCH_RETRIES`, `TALKY_HEALTHWATCH_RETRY_DELAY` | `healthwatch.sh` |
| `TALKY_BACKEND_DIR`, `TALKY_BACKEND_ENV_FILE`, `TALKY_BACKEND_PYTHON`, `TALKY_RECONCILER`, `TALKY_RECONCILE_APPLY` | `asterisk-reconcile-on-drift.sh` |
| `TALKY_PROD_HOST`, `TALKY_PROD_KEY`, `TALKY_DEPLOY_*`, `TALKY_LOCAL_PYTHON` | `deploy_to_server.sh` |
| `VOICE_GATEWAY_BUILD_SHA` | `build_voice_gateway_release.sh` |
| `TRUNK_USER`, `TRUNK_PASS` | `setup-asterisk.sh` (required at run time) |

### 12.2 systemd units and schedules

| Unit | Type | Schedule or trigger | Enabled by `install-services.sh` | Enabled by `install.py apply` |
|---|---|---|---|---|
| `talky-api.service` | daemon, `Restart=on-failure` | boot | yes | yes |
| `talky-api-web.service` | daemon, `Restart=always` | boot | yes | no |
| `talky-voice-worker.service` | notify daemon, watchdog 180 s | boot | yes | yes |
| `talky-dialer-worker.service` | notify daemon, watchdog 180 s | boot | yes | yes |
| `talky-reminder-worker.service` | notify daemon, watchdog 180 s | boot | yes | yes |
| `talky-voice-gateway.service` | daemon (C++) | boot | yes | yes |
| `talky-trunk-status.service` | daemon, loop every 10 s, `MemoryMax=256M` | boot | yes | no (timer is enabled instead) |
| `talky-trunk-status.timer` | retired | disabled by the script | disabled | enabled |
| `talky-asterisk-reconcile.timer` | `OnBootSec=2min`, `OnUnitActiveSec=60s` | every minute | yes, and started | no |
| `talky-cleanup.timer` | `OnCalendar=*-*-* 03:00:00`, `Persistent` | nightly 03:00 | yes | no |
| `talky-healthwatch.timer` | `OnBootSec=2min`, `OnUnitActiveSec=2min`, `Persistent` | every 2 min | yes | yes |
| `talky-db-backup.timer` | `OnCalendar=*-*-* 02:30:00`, up to 10 min random delay, `Persistent` | nightly 02:30 UTC | yes | no |
| `talky-inbound-synthetic.timer` | `OnBootSec=10min`, `OnUnitActiveSec=60min`, up to 5 min delay, `Persistent` | hourly | yes | no |
| `talky-support-jobs.timer` | `OnCalendar=*:0/15`, `Persistent` | every 15 min | yes | no |
| `talky-migrate.service` | oneshot, `TimeoutStartSec=180` | manual only | no (by design) | no |
| `talky.target` | target (api + 3 workers) | boot | yes | no |
| `talky-redis.service` | oneshot, Docker compose | boot | not in repo | written and enabled by installer |
| `talky-dashboard.service` | daemon (Next.js on 3000) | boot | not in repo | Linux mode only |
| `scripts/database/systemd/talklee-db-backup.timer` | template | 02:00 UTC daily | no | no |
| `scripts/database/systemd/talklee-db-backup-health.timer` | template | hourly | no | no |

Run `systemctl list-timers 'talky-*' --no-pager` to see what is actually
scheduled on your host.

### 12.3 Key paths

| Path | Purpose |
|---|---|
| `/opt/talky` | Git checkout |
| `/opt/talky/backend/.env` | Runtime environment (every unit loads it) |
| `/opt/talky/backend/venv` | Python virtual environment |
| `/opt/talky/runtime/bin/voice_gateway` | Gateway binary |
| `/opt/talky/runtime/state` | Writable state (installer profile) |
| `/opt/talky/backend/recordings`, `audio_files/storage`, `feedback_audio`, `logs`, `tmp`, `uploads` | Writable data folders |
| `/etc/talky/` | `tls/`, `redis.conf`, `compose.yml`, `migration.env`, `db-backup.env`, `inbound-synthetic.env` |
| `/etc/asterisk/` | Asterisk config; `pjsip.d/`, `extensions.d/` are generated and managed |
| `/etc/ssl/certs/talky-db-ca.pem` | Database CA (installer profile) |
| `/var/lib/talky-install/` | Installer state and receipt |
| `/var/backups/talky/` | Backups of the existing production profile |
| `/etc/nginx/sites-available/talky` | nginx site (installer) |

### 12.4 Source files this guide was checked against

`INSTALLATION.md`, `scripts/installation/{README.md,install.py,install.example.json,backend.env.example,dashboard.env.example,admin.env.example}`,
`install_talklee.sh`, `backend/systemd/*`, `deploy_to_server.sh`, `docs/{DEPLOYMENT,RUNBOOK,ARCHITECTURE}.md`,
`backend/database/MIGRATIONS.md`, `backend/Alembic/versions/0082_support_system.py`,
`backend/Alembic/env.py`, `.github/workflows/ci.yml`, `setup-asterisk.sh`,
`backend/scripts/{build_voice_gateway_release.sh,import_support_articles.py}`,
`backend/app/workers/support_jobs.py`, `backend/app/domain/services/support/config.py`,
`backend/app/api/v1/endpoints/{health.py,support/*}`, `backend/app/core/prod_gate.py`,
`backend/.env.example`, `backend/requirements.txt`, `scripts/database/*`,
`docs/production-readiness/database-recovery.md`, `talky.conf`,
`nginx_http_block_addition.conf`, `docker-compose.yml`, `CLAUDE.md`, `AGENTS.md`.

End of guide.
