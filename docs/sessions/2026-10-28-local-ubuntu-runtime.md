# Local Ubuntu runtime — 28 October 2026

The customer dashboard, Admin frontend, authenticated API, Redis, Asterisk and
C++ voice gateway are installed in the owned Ubuntu WSL distribution
`Talklee-Installer-Test`. The application uses a separate PostgreSQL process on
the same Windows machine. This is a local test installation with synthetic
accounts and data, not a public production deployment or completed installation
acceptance.

This record extends the earlier [Ubuntu installer test release](2026-10-28-ubuntu-installer-release.md).
Its preparation/build results remain historical evidence. The later manual
runtime setup does not establish that `install_talklee.sh apply` has succeeded,
and the now-configured distribution is no longer a fresh-host installer target.

## Source and artifact boundaries

- Published test branch: `codex/ubuntu-installer-rehearsal-20261028` in
  <https://github.com/talkyai-source/Talky.ai-complete->.
- Published repaired candidate: `8f45d12eadb3de02de6696b0bf6e7a90b644c3f1`,
  installed in the actual runtime with database revision
  `0074_incidents_bootstrap`. The earlier runtime was verified at
  `3d7be29326c19834ff84b6e461903a622aa79be0` and revision 0073.
- The initial installed dashboard/Admin builds passed at
  `192db45d9df7eaee385cabae83916be18fec2cea` in
  [the first local-frontend workflow](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40582352004).
  Following the Admin health repair, replacement artifacts passed on Ubuntu
  24.04 using Node 22.22.0 and the committed npm locks at
  `d10235e1c99dc9701aae8e4da2a84dfd12027cab` in
  [the follow-up workflow](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40585195011).
  Their manifest matches the repaired candidate's frontend Git trees:
  dashboard `74cac50edc0412f7f63292600406e7ea9cd9684c`; Admin
  `6b1f8d768debfa91089a2b10f457d73140e28e33`. These replacement artifacts are
  installed and both frontend HTTP checks passed. Final authenticated API
  checks and the repaired Admin frontend's browser login/health-display check
  passed after restart.
- The installed C++ binary reports build SHA
  `a152195b8b15257f33837932ee96917d7e3d3ba6`. Its source tree is unchanged in
  the current checkout: `6a917f19f05d58b0b95caa30fc27af8cce25428e`.
  The binary's actual build identity is retained rather than relabelled.
- Work remained isolated from the original dirty checkout. No production
  machine, database, payment, phone number or DNS configuration was changed.

## Runtime configuration

Ubuntu 24.04.5 LTS runs the application services under dedicated service users.
The Python 3.12 environment contains the existing 157-package hash-locked
closure; installation and `pip check` passed. The C++ release build passed all
four CTest suites. Asterisk has no trunks, registered endpoints or active calls.
The gateway readiness response reported ready, protocol version 2 and PCMU.

The Windows PostgreSQL 16 cluster listens only on `127.0.0.1:55540`, using the
synthetic database `talklee_local_rehearsal_20261028`. Ubuntu connects with
verified TLS and a distinct restricted runtime login. The migration owner is
also non-superuser and has no PostgreSQL `BYPASSRLS` privilege. The runtime has
the required table/sequence access and no database/schema creation, ownership,
TRUNCATE permission or membership in the migration role. Twelve read-only
identity, TLS, grant and RLS metadata checks passed at the earlier 0072 revision;
the later application event check below exercises the repaired 0073 schema.
This same-machine arrangement does not establish off-host database resilience.

The application environment is `development`; frontend assets use production
builds with `NEXT_PUBLIC_APP_ENV=development`. Dashboard authentication is
required, and both Admin dummy authentication and Admin development mode are
disabled. Synthetic customer and platform-admin accounts use the actual
password, session and role mechanisms. API login and `/api/v1/auth/me` returned
200 for each account, including the final post-restart check with the actual
`tenant_admin` and `platform_admin` roles. An unauthenticated
`/api/v1/auth/me` returned 401.

All application listeners are intended for local testing. HTTP cookies use the
local HTTP configuration. Use the exact `127.0.0.1` URLs consistently:

| Service | Local address |
|---|---|
| Customer dashboard | <http://127.0.0.1:3030/auth/login> |
| Admin | <http://127.0.0.1:3031/login> |
| API documentation | <http://127.0.0.1:8000/docs> |
| API health | <http://127.0.0.1:8000/health> |
| Gateway readiness | <http://127.0.0.1:18080/ready> |

The installed frontend artifacts target this loopback API. They are not suitable
for a public hostname without rebuilding with the reviewed deployment settings.
Both frontend services were active and their login pages rendered in a real
browser. The real customer browser login succeeded, reaching `/dashboard/`
with the seeded customer's tenant-admin identity and zero-minute, planless
tenant; the Contacts screen was also checked. Its initial attempt timed out
while the Admin page was polling; a retry after closing Admin succeeded. The
customer frontend tree is unchanged in the installed replacement artifacts.

The final real Admin browser login passed after restart with the repaired
artifact and backend. Its root page showed all three STT/LLM/TTS providers as
`unknown` / `Not measured`, and `No open incidents` after the synthetic API
check's incident was removed. No browser console errors were observed. These
logins and displayed results do not qualify performance or complete customer
journeys.

## Canonical database bootstrap repair

Starting the real API exposed a missing `stream_events` relation. The supported
`complete_schema.sql` → stamp `0008_tenant_voice_tuning` → upgrade path omitted
the table created in historical revision 0003. Reaching 0072 therefore did not
ensure that event emission and retention could run.

Revision 0073 creates the relation only when absent, with all current event
categories, indexes, tenant policy and forced row-level security. The canonical
schema contains the same relation for new databases. Existing tables, retained
events, grants and policies are preserved; downgrade does not drop event data.

The following checks passed using actual PostgreSQL from native Ubuntu:

- Seven database cases in `test_stream_events_bootstrap_migration.py`, covering
  the application emitter and payload/categories, populated tenant isolation,
  retention, invalid values/references, and preservation of an existing table.
- Four migration-graph cases in `test_release_migration_merge.py`.
- A second empty synthetic database loaded the full maintained schema, stamped
  0008 and upgraded through the complete migration chain to the sole head 0073.
- The actual local runtime database upgraded to 0073. Its restricted runtime
  login successfully executed the real `emit_event` insert and readback in a
  transaction that was rolled back after verification.

These checks establish the bounded bootstrap repair. They are not a backup
restoration, a complete feature regression suite or production acceptance.

## Admin health and incident repairs

Browser use exposed two additional existing Admin defects: fixed provider
health/latency values were presented without a real observation, and the
canonical installation lacked the incidents table. These are bounded repairs
to the existing installation and truthful operating-status contracts.

Commit `d10235e1c99dc9701aae8e4da2a84dfd12027cab` reports unmeasured STT, LLM
and TTS status as `unknown`, with null latency and `Not measured`, whether or
not credentials exist. It does not invent a successful probe timestamp. The
database check remains measured; a failed check has no invented zero latency.
The Admin response types accept the unknown/null values. Five focused HTTP
health cases and 33 existing Admin platform-access cases passed: 38 checks in
that run. Provider availability and latency are still unqualified.

Commit `8f45d12eadb3de02de6696b0bf6e7a90b644c3f1` adds the existing incident
storage contract to both the canonical schema and revision 0074. It creates
the table only when absent, with forced platform-only row-level security, and
preserves existing rows, policies and grants. Downgrade does not erase incident
evidence. Incident listing now returns an explicit HTTP 503 when its data or
count queries fail, rather than claiming a successful empty result.

The incident repair passed 54 checks: six real PostgreSQL migration cases,
11 HTTP availability cases, four migration-graph cases and the same 33 platform
access cases. These totals overlap with the health run and are not additive
unique coverage. A third empty synthetic database loaded the full updated
`complete_schema.sql`, stamped 0008 and upgraded to head 0074. It contained
the 12-column event table and 11-column incidents table, both with forced RLS.
The actual runtime then upgraded to 0074; its restricted runtime login passed
the insert/readback and platform RLS proof in a transaction rolled back after
verification.

Final authenticated API checks passed after the complete service/database
restart. A customer tenant admin received HTTP 403 when requesting Admin
incidents. The platform admin's health response contained three unmeasured
provider statuses with null latency. One synthetic incident was inserted using
the restricted runtime role in platform context; the actual Admin API listed
it, acknowledged it and resolved it, with HTTP 200 and the resulting database
state verified. The synthetic incident was removed afterward. The nonsecret
local receipt is `tmp/ubuntu-local-runtime-20261028/evidence/final-api-smoke.json`.
The final Admin browser check described above also passed. This completes the
bounded local runtime checkpoint; broader operating acceptance below remains
unfinished.

This does not complete OP09's accountable alert-delivery acceptance or resume
the user-deferred CP09 customer-UX work. Those package statuses and scope
decisions remain unchanged.

## Local operation and remaining checks

Task-local PowerShell helpers are kept outside the published source under
`tmp/ubuntu-local-runtime-20261028/` in the original checkout:

- `Start-TalkleeLocal.ps1` checks the exact owned PostgreSQL data directory,
  process identity and loopback port, starts it if stopped, starts a hidden WSL
  keepalive if needed, and starts the six application services.
- `Stop-TalkleeLocal.ps1` stops those services, the verified owned keepalive and
  that PostgreSQL cluster. It does not shut down other WSL distributions.
- `Get-TalkleeLocalStatus.ps1` reports database, systemd and HTTP status.

Syntax and read-only fixture checks passed, followed by an idempotent start and
a complete stop/start cycle. Stop returned successfully with all six Ubuntu
services and the owned database stopped; the unrelated PostgreSQL process on
port 55434 remained unchanged. The corrected start helper returned successfully
after a real PostgreSQL restart with a new process, all six Ubuntu units
active/running, and HTTP 200 from the dashboard, Admin, API health and gateway
readiness endpoints.

The first start exercise exposed a PowerShell wait that also waited for the
long-running PostgreSQL descendant. The corrected helper bounds its wait for
`pg_ctl` itself to 65 seconds and waits up to 60 seconds for API HTTP 200 before
reporting startup. The successful cycle used those corrections. This proves
the bounded local service/database restart; Windows or WSL reboot recovery
remains untested.

The status helper allows 30 seconds per HTTP request and reports exception and
HTTP status without credential output. Earlier five-second checks timed out
for the API/dashboard under local memory pressure while browser access worked;
no responsiveness or capacity acceptance is claimed. Do not rerun the
database initializer, configuration generator or account seeder as a restart
procedure. The private credentials file is
`tmp/ubuntu-local-runtime-20261028/private/LOCAL-ACCESS.txt`; its contents are
excluded from public evidence and source control.

The local test CA expires on **3 November 2026 at 20:13:55 UTC** and the database
server certificate ten seconds later. Renew the certificates and trusted CA
before expiry while preserving existing data and certificate verification.

Voice-provider credentials, carrier configuration and SMTP delivery are absent;
provider-dependent workers are not started. Telephony is disabled and Stripe
billing is explicitly disabled. This installation does not demonstrate real
calls, payments, email, complete Admin business journeys or customer onboarding.

Public deployment still requires designated hosting and reviewed external
database, domain/TLS, network, provider and carrier inputs. Full installer apply,
off-host backup/restore, measured recovery objectives, update/rollback,
independent operator reproduction and the approved controlled call remain
unfinished. OP10 and OP12 remain `in_progress`; no acceptance gate, release
decision, feature completion or production-readiness freeze status changes.
