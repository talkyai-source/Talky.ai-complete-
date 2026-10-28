# Deployment

How Talky.ai is actually deployed. For architecture see
[ARCHITECTURE.md](./ARCHITECTURE.md); for incident response see
[RUNBOOK.md](./RUNBOOK.md).

> Every command on this page is taken from a script or unit file in this
> repository — `deploy_to_server.sh`, `backend/systemd/*`, and
> `backend/systemd/install-services.sh`. Nothing here is aspirational.

## What production is

One Hetzner bare-metal box, `admins@144.76.17.150`, running a **hybrid**:

| Layer | How it runs | Where from |
|---|---|---|
| API, 3 Python workers, C++ voice gateway | **systemd units** | git checkout at `/opt/talky` |
| Postgres 15, Redis 7 | **docker compose** | `/opt/talky/docker-compose.yml`, project `talky` |
| SIP / media | **Asterisk only** (bare metal) | — |
| `Talk-Leee` + `Admin` frontends | **Vercel**, auto-deploy from `main` | not on this box |

`/opt/talky` is a git checkout of `origin/main` with a **sparse-checkout that
excludes `Talk-Leee/` and `Admin/`** — the frontends deploy to Vercel from git,
not to the backend box. Every backend service except the C++ gateway runs as
**root**; the gateway runs as `admins`.

There is no Kubernetes, no Prometheus/Grafana, and no FreeSWITCH / OpenSIPS /
Kamailio / rtpengine in production, despite configuration for them existing
under `telephony/`. See [ARCHITECTURE.md](./ARCHITECTURE.md).

## Deploying

### Fresh Ubuntu service identities

The fresh-host profile in [INSTALLATION.md](../INSTALLATION.md) installs the API,
workers, gateway and dashboard as `talky`, with root-owned source and binaries.
Its migration and Asterisk reconciliation oneshots run as `root`, separate from
application processes. Migration credentials belong in the operator-created
`/etc/talky/migration.env`: a root-owned regular file with mode `0600` containing
only `MIGRATION_DATABASE_URL`. The migration unit checks that identity, file
metadata and loaded value before Alembic starts. Migrations have no boot
enablement and must be invoked explicitly after the release gates pass.

The installer enables the long-running `talky-trunk-status.service`, disables
the retired trunk-status timer, and enables `talky-asterisk-reconcile.timer`.
Reconciliation still uses the existing database-derived desired-state check,
writer lock, digest recheck and active-channel refusal before applying changes.
The healthwatch timer is enabled. Legacy Docker database backup and carrier
synthetic-call units retain the root operator identity, but their timers are
not enabled by this installer; retention cleanup is also not enabled. Configure
those operations separately with their required environment and policy inputs.

Use the separate [fresh-host release guide](../scripts/installation/FRESH-RELEASE.md)
for the new `talky` profile. It covers staged backend/gateway/frontend builds,
artifact identity, preserved configuration/data, the existing drain contract,
backup, migration and rollback boundaries. Its full VM qualification is
recorded separately from installation/reboot. The deployment script below
continues to target the older `admins` host layout.

### Existing production host

The **only** supported path. Do not rsync source onto prod — that is what made
the box silently diverge from git in the past.

```bash
# 1. Commit and push. CI runs on push. Record the full reviewed SHA in the
#    release manifest; a branch name is not a deploy artifact.
git push origin main
export TALKY_DEPLOY_SHA="$(git rev-parse HEAD)"

# 2. First freeze inbound/carrier traffic AND outbound origination through the
#    approved topology-specific procedure, prove every active counter is zero,
#    and obtain the candidate-bound immutable JSON manifest described below.
#    The current Asterisk-only host has no repository-owned freeze command, so
#    the operational facts in this artifact remain an external hard gate.
export TALKY_DEPLOY_DRAIN_MANIFEST='/approved/drain-manifest.json'
export TALKY_DEPLOY_DRAIN_MANIFEST_SHA256='<64-lowercase-hex-sha256-of-exact-file>'

# 3. Deploy. You will be prompted for the prod sudo password (interactively —
#    it is not stored anywhere). The script deploys exactly TALKY_DEPLOY_SHA.
./deploy_to_server.sh
```

`deploy_to_server.sh` SSHes to prod and, in order:

1. refuses a dirty local release tree, resolves one full candidate SHA,
   refreshes `origin/main`, and proves the SHA is reachable from that branch
2. verifies and atomically consumes the candidate-bound production drain
   manifest before opening SSH; any digest, candidate, environment, freshness,
   freeze, zero-count, evidence, approver, or replay mismatch fails closed
3. refuses source-tree drift on the server, preflights Asterisk, the complete
   gateway toolchain, Python, and Alembic, then fetches the branch and proves
   the frozen object is reachable without changing the live checkout
4. creates an isolated detached worktree before mutating the live `/opt/talky`
   checkout; from the frozen SHA it runs the Python import smoke test, builds
   and executes the complete C++ gateway CTest suite, runs
   `reconcile_asterisk_release.sh --check-only` to reject ambiguous
   account-to-DID inventory, and validates the mandatory carrier-hairpin
   synthetic configuration
5. only after those candidate proofs and the zero-session check, saves the exact
   `/etc/systemd/system/talky-api-web.service` file or symlink using `cp -a` in a
   root-private directory under `/var/backups/talky/deploy-unit-recovery`, then checks out the frozen SHA with
   `git checkout --detach <full-sha>` and verifies `HEAD` and the clean tree; it
   never deploys a moving branch tip
6. proves the existing gateway has zero sessions, atomically publishes the
   tested binary outside the git checkout, then reconciles systemd; the
   installer installs `talky-migrate.service`, `talky-inbound-synthetic.service`
   and the other repository units, enables `talky-inbound-synthetic.timer` and
   every persistent service/timer; the deploy then verifies the required units
   are loaded and the release-critical services and timers are enabled
7. rechecks zero gateway sessions immediately before restart, restarts the
   gateway, and proves its authenticated readiness and build SHA; the gateway
   loads the distinct `INTERNAL_SERVICE_TOKEN` and
   `VOICE_GATEWAY_AUTH_TOKEN` secrets plus exact `VOICE_GATEWAY_CALLBACK_HOST`
   and `BACKEND_INTERNAL_URL` origin from `backend/.env`; it refuses startup if
   secrets are absent, short, or reused, and refuses any callback whose scheme,
   host, port, or caller-audio path falls outside that pinned origin
8. starts `talky-migrate.service`, verifies `alembic current == heads`, and runs
   the Admin runtime database privilege gate against the application's configured
   connection. Both checks run with sudo because the production environment file
   is root-owned mode `0600`; inherited database URL overrides are cleared.
   Failures block the backend restart. It then applies the repository-owned Asterisk
   configuration through `reconcile_asterisk_release.sh`, which requires zero
   active Asterisk channels and atomically applies, reloads, verifies, or rolls
   back the managed configuration
9. atomically sets only `APP_VERSION` in `backend/.env` to the full candidate SHA,
   preserving the other bindings, file ownership and permissions. It restarts both
   APIs and three Python workers only after the matching gateway, schema, runtime
   privileges and Asterisk configuration are proven. It then restarts
   `talky-inbound-synthetic.timer` and the long-running trunk-status service;
   trunk-status restart failure remains non-fatal because that unit retries
10. requires every listed unit to be active, then requires liveness, capacity
    readiness, and dependency-aware deep readiness to return successful HTTP
    responses. It reads each API unit's current `MainPID` and verifies that process's
    exact `APP_VERSION` without printing its environment; any failure keeps the
    deploy command non-zero

An abort before a successful migration restores the previous checkout and runs
its unit installer, then restores the saved API-web file or symlink with
`cp -a --remove-destination` and reloads systemd. This preserves an independently
pinned maintenance web unit that the old installer alone would overwrite. The
rollback does not restart services, and retains the private unit backup for
recovery. Once migration succeeds, failures require code-forward recovery or a
separately approved compatible restore; the deploy does not roll code backward
under the migrated schema.

Asterisk configuration is generated and reconciled from the repository:
`setup-asterisk.sh` provisions the managed includes, and deploys use
`backend/scripts/reconcile_asterisk_release.sh`. Do not hand-edit the managed
files in `/etc/asterisk`; the next verified reconciliation will replace drift.

Overridable via environment:

| Variable | Default |
|---|---|
| `TALKY_PROD_HOST` | `admins@144.76.17.150` |
| `TALKY_PROD_KEY` | `$HOME/.ssh/id_rsa_openssh` |
| `TALKY_DEPLOY_BRANCH` | `main` |
| `TALKY_DEPLOY_SHA` | local `HEAD` (resolved once to a full SHA) |
| `TALKY_DEPLOY_DRAIN_MANIFEST` | **required path; no default** |
| `TALKY_DEPLOY_DRAIN_MANIFEST_SHA256` | **required 64-character lowercase digest; no default** |
| `TALKY_DEPLOY_DRAIN_REPLAY_DIR` | local Git common-dir replay ledger |
| `TALKY_LOCAL_PYTHON` | first available `python3` or `python` |

Do not bypass the drain inputs for an application-only change: the supported
script deploys and restarts a commit-matched gateway every time so an old media
binary cannot silently survive a backend security or protocol change.

### Production drain manifest contract

The manifest is a short-lived approval artifact, not a live probe. Its SHA-256
is calculated over the exact file bytes and supplied separately. Version 1
retains the two-operator procedure below (timestamps are RFC3339 UTC). Version 2
explicitly supports an owner-authorized solo operation, as described afterwards;
it is never inferred from an incomplete version-1 manifest.

```json
{
  "schema_version": 1,
  "manifest_id": "drain-20260829-001",
  "candidate_sha": "0123456789abcdef0123456789abcdef01234567",
  "environment": "production",
  "issued_at": "2026-08-29T10:00:00Z",
  "expires_at": "2026-08-29T10:20:00Z",
  "traffic": {
    "ingress_disabled": true,
    "outbound_origination_disabled": true,
    "active_counts": {
      "gateway_sessions": 0,
      "asterisk_legs": 0,
      "redis_leases": 0,
      "db_live_calls": 0
    }
  },
  "evidence": {
    "topology_ref": "topology-proof:prod-asterisk-20260829",
    "change_ref": "change:CAB-12345"
  },
  "approvers": [
    {"principal": "telephony-owner@example.com", "role": "telephony-owner", "approval_ref": "approval:CAB-12345-a"},
    {"principal": "release-manager@example.com", "role": "release-manager", "approval_ref": "approval:CAB-12345-b"}
  ]
}
```

For the current solo-operated platform, the owner explicitly authorized the
single-owner procedure on 28 October 2026. A version-2 manifest keeps every
candidate, environment, time, traffic, counter and evidence field above, and
makes only these approval-field changes:

```json
{
  "schema_version": 2,
  "approval_mode": "single-owner",
  "owner_authorization_ref": "owner-authorization:recorded-solo-policy-decision",
  "approvers": [
    {"principal": "actual-platform-owner-account", "role": "platform-owner", "approval_ref": "owner-approval:recorded-release-decision"}
  ]
}
```

This is a field-change example, not a complete executable manifest. Replace
the illustrative references and principal with the actual owner's identity
and retained authorization/release records. The policy authorization and release
approval may refer to the same owner decision when it covers both. Version 2
requires exactly one `platform-owner`, the literal `single-owner` mode and a
nonempty `owner_authorization_ref`. Missing authority, other modes, extra keys
and a second approver fail validation. Do not invent an independent operator or
use an agent's code review as the owner's approval. Backup, actual ingress and
origination freeze, fresh four-counter drain, candidate verification, migration,
recovery and post-release health checks remain required under either version.

`issued_at` may be no more than 15 minutes old (with at most 60 seconds of
future clock skew), `expires_at` must still be in the future, and the total
validity may not exceed 30 minutes. Version 1 still requires at least two
approvers with distinct principals, roles and immutable approval references.
The verifier rejects duplicate or
unknown JSON keys, booleans masquerading as integer counts, symlink/non-regular
inputs where the platform supports no-follow opens, digest/candidate mismatch,
and a reused `manifest_id`. Calculate the digest after the approvers publish
the immutable artifact, for example:

```bash
sha256sum "$TALKY_DEPLOY_DRAIN_MANIFEST"
```

The local replay ledger is an additional fail-closed guard; the release record
must also enforce global manifest-ID uniqueness. SHA-256 binds the bytes but is
not an approver signature. Neither version authenticates the named person's
authority: the operator must retain the real authorization evidence. The
optional `backend/scripts/observe_release_quiescence.py`
now reads bounded gateway/Asterisk/Redis/database counters using an explicit
`--env-file`. Its output is a non-atomic snapshot, never a drain manifest or
approval; exit zero means the reads were complete and internally consistent,
not that every counter was zero. The carrier topology and ingress/origination
freeze still require owner attestation, and counts must be rechecked after that
freeze. The later gateway `/stats` checks narrow only the local
gateway restart window; neither the manifest nor a phrase eliminates TOCTOU
across the external topology.

## Migrations

Migrations run as their own oneshot unit, never on boot:

```bash
sudo systemctl start talky-migrate.service     # alembic upgrade head
journalctl -u talky-migrate --since '10 min ago'
```

The unit runs `/opt/talky/backend/venv/bin/alembic -c /opt/talky/backend/alembic.ini upgrade head`
with `TimeoutStartSec=180`. It is deliberately **not** install-enabled — a
migration must never run merely because the machine rebooted.

### Admin runtime database privilege check

After migrations and the existing Alembic-head check, run this read-only gate
using the **same environment and OS identity as the backend application**. The
supported API and migration units currently load `/opt/talky/backend/.env`;
environment variables already supplied to the command take precedence over
that file. Do not substitute migration-owner credentials when the application
has a separate runtime connection. Do not print or copy connection strings.

```bash
cd /opt/talky/backend
sudo env -u DATABASE_URL -u READ_DATABASE_URL venv/bin/python scripts/verify_admin_runtime_privileges.py > admin-runtime-privileges.json
```

Require exit code zero before accepting the database capability portion of an
Admin release. `deploy_to_server.sh` invokes this gate automatically after the
successful migration and exact-head check, before publishing `APP_VERSION` or
restarting backend services. The supported units run as root and read their
connections from `backend/.env`; the command clears inherited database URL
overrides so it observes that file's actual runtime role. It opens bounded, read-only transactions and reads
catalogs plus the Alembic version metadata. It never grants privileges, changes
roles, changes application RLS settings, or reads customer records. The role
must be able to read `public.alembic_version` for the head check. Reports omit
connection strings, role names, passwords and raw connection-error messages.

The contract checks the existing Admin routes and their identity, audit,
connector authorization and call-settlement dependencies. `DATABASE_URL`
covers both the primary pool and the legacy adapter's independent connections;
changing a test pool alone does not change that adapter. A running adapter
captures its URL at import, so a fresh preflight cannot certify an old process's
environment. If `READ_DATABASE_URL` differs, its role/head/schema posture is
checked separately; no active Admin route uses that read pool, so the check
does not require Admin writes there or certify its other consumers.

Missing privileges, schema/head drift, required RLS flags, shadowed table
resolution, or SUPERUSER/BYPASSRLS authority on the effective **or login** role
fail the gate. Table ownership is reported separately from effective grants:
ordinary ownership retains DDL/regrant authority, while FORCE RLS still applies.
Column-only grants are an explicit unverified result needing a review of the
exact SQL statements, not a recommendation to grant broad access. Policy
presence is not proof of policy semantics, authorization, or provider effects;
retain the restricted-role behavior tests and normal-auth acceptance journey.

The engineer changing an Admin route, a mandatory shared writer, a table or its
RLS migration must update `GROUPS`, `RLS_TABLES` and `NEW_COLUMNS` in the script
as applicable and have the release reviewer check the cited source paths.
`python scripts/verify_admin_runtime_privileges.py --contract` prints that
inventory offline without loading environment files or connecting. Contract
drift, hosted execution and a successful report remain release requirements;
synthetic unit tests do not establish the host's grants. Optional platform SIP
seeding is included only when its existing configuration enables that hook.

`backend/scripts/set_release_version.py --env-file /opt/talky/backend/.env
--candidate-sha <full-sha>` changes only the release identity and refuses an
invalid file or detected concurrent file change. The deploy runs it with sudo, then checks
both started API processes using `--check-pid <MainPID>`. A public health response
alone is not evidence that both processes loaded the intended version. These
checks read process environments internally and never print credentials.

## Rollback

Rollback begins by stopping new ingress, not by restarting application
processes underneath arriving calls. The current documented production host is
Asterisk-only and this repository does **not** yet contain an approved,
tested Asterisk/carrier ingress-disable command. The OpenSIPS helper below is
valid only if that reviewed stack is actually the active ingress. For the
current topology, use the signed carrier/PBX change procedure and record its
change ID. **If the active topology's durable disable and rejection proof are
unavailable, stop: rollback is blocked and must not continue by guessing.**

The rollback SHA must also contain the authenticated gateway contract,
`build_voice_gateway_release.sh`, the runtime binary path, and an Asterisk
adapter that sends `VOICE_GATEWAY_AUTH_TOKEN`. The currently deployed
pre-inbound SHA does not satisfy that compatibility contract and is **not** a
valid post-release rollback artifact. Before any forward inbound deployment,
publish and prove a rollback-compatible release (or approve code-forward / a
verified backup restore); do not combine an old backend client with the new
gateway or resurrect the unauthenticated callback path.

```bash
set -Eeuo pipefail
# 1. Freeze and durably disable new inbound attempts using the command approved
#    for the active carrier/PBX topology. On the repository-managed OpenSIPS
#    stack only, this is:
bash telephony/scripts/canary_rollback.sh full \
  telephony/deploy/docker/.env.telephony
sh telephony/scripts/assert_canary_ingress.sh all \
  telephony/deploy/docker/.env.telephony

# 2. Prove an external controlled inbound attempt is rejected before answer,
#    and record the carrier/PBX evidence. Wait for active calls to reach zero
#    (or follow the incident's separately approved forced-hangup policy).
read -rp 'Ingress-disable/rejection evidence ID: ' ingress_evidence_id
test -n "${ingress_evidence_id}"
read -rp 'Type ACTIVE_CALLS_ZERO after checking every active leg: ' active_calls_proof
test "${active_calls_proof}" = 'ACTIVE_CALLS_ZERO'

# 3. Only after steps 1-2, select the previously proven application SHA.
rollback_sha='<full-known-good-sha>'
[[ "${rollback_sha}" =~ ^[0-9a-f]{40,64}$ ]]
ssh -t -i ~/.ssh/id_rsa_openssh admins@144.76.17.150 \
  bash -s -- "${rollback_sha}" <<'REMOTE'
set -Eeuo pipefail
rollback_sha="$1"
[[ "${rollback_sha}" =~ ^[0-9a-f]{40,64}$ ]]
cd /opt/talky
test -z "$(git status --porcelain)"
git cat-file -e "${rollback_sha}^{commit}"
git checkout --detach "${rollback_sha}"
test "$(git rev-parse HEAD)" = "${rollback_sha}"
test -z "$(git status --porcelain)"

# Build and test the gateway from the SAME rollback SHA. Ingress/origination is
# already frozen and every leg is zero, but recheck the local gateway before
# replacing or restarting it. Never keep a newer binary beside older backend
# protocol/auth code.
gateway_candidate="$(mktemp /tmp/talky-voice-gateway-rollback.XXXXXX)"
cleanup_gateway_candidate() { rm -f -- "${gateway_candidate}"; }
trap cleanup_gateway_candidate EXIT
bash backend/scripts/build_voice_gateway_release.sh "${gateway_candidate}"
gateway_stats="$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)"
active_sessions="$(printf '%s' "${gateway_stats}" | backend/venv/bin/python -c \
  'import json,sys; value=json.load(sys.stdin).get("active_sessions"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')"
test "${active_sessions}" -eq 0
sudo install -d -o root -g admins -m 0750 /opt/talky/runtime/bin
sudo install -o root -g admins -m 0750 "${gateway_candidate}" \
  "/opt/talky/runtime/bin/.voice_gateway.${rollback_sha}.new"
sudo mv -f "/opt/talky/runtime/bin/.voice_gateway.${rollback_sha}.new" \
  /opt/talky/runtime/bin/voice_gateway
sudo bash /opt/talky/backend/systemd/install-services.sh
gateway_stats="$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)"
active_sessions="$(printf '%s' "${gateway_stats}" | backend/venv/bin/python -c \
  'import json,sys; value=json.load(sys.stdin).get("active_sessions"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')"
test "${active_sessions}" -eq 0
sudo systemctl restart talky-voice-gateway
curl -fsS --max-time 10 http://127.0.0.1:18080/stats >/dev/null
gateway_ready="$(curl -fsS --max-time 10 http://127.0.0.1:18080/ready)"
printf '%s' "${gateway_ready}" | backend/venv/bin/python -c \
  'import json,sys; p=json.load(sys.stdin); assert p.get("ready") is True; assert p.get("protocol_version") == 2; assert "pcmu" in p.get("codecs", [])'
cleanup_gateway_candidate
gateway_candidate=''
trap - EXIT
sudo systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker

# 4. Fail closed: every probe must return 2xx or the rollback is unsuccessful.
curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/ready >/dev/null
curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/deep >/dev/null

# 5. Keep ingress disabled. Re-enable only through a separately approved
#    forward release after reconciliation, outbound smoke, and external proof.
REMOTE
```

**Rolling the code back does not roll the database back.** Only run
`alembic downgrade` if you have verified the older application version is
compatible with the current schema, and test the downgrade in a scratch
database first — a failed migration rollback is far worse than a failed
forward migration.

Migration `0028_call_terminal_settle_cas` is an intentional hard rollback
boundary: its downgrade acquires the settlement lock and refuses rather than
restore the replay-unsafe pre-0028 status function. Roll code forward against
the additive schema, or restore a verified pre-migration backup during a fully
quiesced incident procedure; do not plan an in-place downgrade through 0028.

## Fresh box / adding a unit

Unit files live in **`backend/systemd/`** — one directory, the single source of
truth. `install-services.sh` symlinks every `*.service`, `*.target` and
`*.timer` there into `/etc/systemd/system`, runs `daemon-reload`, and enables
them:

```bash
sudo bash backend/systemd/install-services.sh
sudo systemctl start talky.target      # API + the three workers
```

When you add a unit, add it to the explicit `systemctl enable` list in that
script too. A unit that is symlinked but never enabled is present on disk and
dead after a reboot, which is indistinguishable from "still missing".

Postgres and Redis on the box are compose-managed:

```bash
docker compose -f /opt/talky/docker-compose.yml ps
```

## Website enquiry intake configuration

Set `CONTACT_ENQUIRY_PUBLIC_ORIGIN` in the Next server environment to the exact public website origin; the backend's allowed origins must include it. When browser API requests use a same-origin reverse proxy, also set server-only `CONTACT_ENQUIRY_API_BASE_URL` to the explicit FastAPI base ending in `/api/v1`, such as `http://127.0.0.1:8000/api/v1` on a host where that is the actual backend listener. Do not point it at the public website or Next listener. If the override is absent, the existing separate-origin `NEXT_PUBLIC_API_BASE_URL` remains the fallback; an explicitly empty or invalid override fails closed. No caller-supplied destination, cookies or authorization headers are forwarded.

Verify public contact submission produces a committed receipt, then use normal platform-admin access to find that exact reference, mark it handled and reopen it. This saves an enquiry; it does not send email. Apply migration `0074_contact_enquiry_revision` before the matching backend and Admin client: status writes require the reviewed revision, and an unknown response requires a saved-state read before another deliberate decision. Older clients without that revision are rejected rather than overwriting a newer decision.

## Pre-production checklist

- [ ] Secrets strong, generated, and **not** committed — `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] `.env` and `backend/.env` ignored: `git check-ignore .env backend/.env`
- [ ] `ENVIRONMENT=production` so `prod_gate` enforces strict config validation and HSTS is emitted
- [ ] CORS `allowed_origins` restricted to the real domains
- [ ] DB backup taken and a restore rehearsed — see [RUNBOOK.md → Backups](./RUNBOOK.md#backups)
- [ ] `talky-migrate.service` succeeds on a copy of prod data before it runs on prod
- [ ] [RUNBOOK.md](./RUNBOOK.md) read by whoever is on call

---

## Local development (docker compose)

**This section is for a laptop or a scratch VM. It is not how production runs.**
Production uses systemd for the application and compose only for Postgres and
Redis; the `backend` compose service below does not exist on the prod box.

```bash
cp .env.example .env                 # POSTGRES_PASSWORD, REDIS_PASSWORD
cp backend/.env.example backend/.env # JWT_SECRET, AI provider keys

docker compose config -q             # validate before starting
docker compose up -d --build
docker compose logs -f backend

docker compose exec backend alembic upgrade head

curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/api/v1/healthz/ready
```

| Task | Command |
|---|---|
| Tail logs | `docker compose logs -f backend` |
| Restart backend | `docker compose restart backend` |
| One-off shell | `docker compose exec backend bash` |
| Stop (volumes kept) | `docker compose down` |

Compose binds Postgres and Redis to `127.0.0.1` only — keep it that way.
