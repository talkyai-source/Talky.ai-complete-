# Admin monitoring maintenance — 27 October 2026

## Defect and bounded repair

The deployed Admin frontend rejected all six HTTP-200 monitoring responses from
production backend `2739eeabf7d091ffa63b7dc0695019da77f972f9`. Required observation
timestamps, scope, nullable measurements and current aggregate field names were
absent. Several old collectors also returned fixed provider latency, database
pool or queue values. CORS and normal administrator authentication were already
verified separately; these response defects caused the dashboard's unexpected
response banner and unavailable System Health views.

This branch backports nine runtime files from the reviewed monitoring repairs
in `d5d6cf1dc66b3308807cb6978634b75a0cece256` onto the actual production base.
It corrects dashboard aggregates, dependency observations, host metrics, worker
heartbeat freshness, Redis/action backlog, and database/pool measurements.
Unavailable measurements remain explicitly unknown. Legacy incident/alert models,
call-control handlers and public health routes retain their production behavior.
Authentication, migrations, dependencies, startup, provider clients, voice hooks,
nginx and the full-release drain procedure are unchanged.

## Activation and rollback

`backend/deploy/admin-monitoring-maintenance.py` is a separate, single-base
maintenance command. It rejects source outside the nine-file runtime allowlist
and the exact associated tests/documentation/command. It requires a clean immutable
Git worktree at `/opt/talky/releases/<full commit>`, the expected live base, the
canonical dashboard unit and its previously inspected dependency-wait override.
An import-only/schema preflight uses the existing virtual environment and checks
the existing database columns without starting a duplicate application lifespan.

The command retains `/opt/talky/backend` as working directory and its environment
file, preserving relative recordings/data paths. It selects the immutable code
using uvicorn `--app-dir`, atomically replaces only the complete API-web unit link,
and restarts only `talky-api-web`. It verifies direct port 8001 and public API
responses using normal administrator login, rejects anonymous access, and checks
that the seven other service identities and live source/environment are unchanged.
Any activation or verification failure restores the original complete unit link
and restarts only API-web. Receipts contain measurements and service identities,
never credentials or session tokens, and are retained root-private on the server.

No persistent release drop-in is created. A later normal full deployment's existing
unit installer replaces this complete unit link, so the maintenance code pin cannot
silently survive that deployment. Manual rollback uses the receipt's original unit
link, `systemctl daemon-reload`, then `systemctl restart talky-api-web` and repeats
authentication checks; the old frontend/backend contract mismatch will return.

## Evidence and remaining limits

Activation and actual browser acceptance are pending at this preparation commit.
The final evidence entry must identify the activated immutable commit, affected
test result, direct/public responses, browser behavior, rollback disposition and
unchanged call-process identities before claiming this repair live.

This maintenance repair does not supply observations the running voice processes
never published. STT, LLM, TTS and Realtime rows can remain **Not measured**;
API-web has telephony disabled, so its PBX adapter status is process-scoped and
unknown. No fixed latency or synthetic provider success is substituted. Nginx's
port-8000 fallback still runs the old backend; its contracts and in-memory CORS
configuration remain a full-release limitation. Vercel deployment protection on
the default Admin alias remains enabled. These six monitoring reads do not certify
all Admin mutations, the wider voice product, alerts or customer acceptance.

The production-readiness feature freeze and open CP09/full-release gates remain.
