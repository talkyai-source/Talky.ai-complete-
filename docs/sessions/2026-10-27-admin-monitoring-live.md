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

Backend commit `80f011d40c187afecdaaf84ee7aba6c188e835fc` is live in API-web,
activated at 2026-10-26 20:21:17 UTC (27 October in Asia/Karachi). The underlying
nine-file repair is `bbfe3df4b829a12a37730f23b7f7a5fb4ebd94df`; its focused gate
passed **111 tests, zero failures/skips**, with independent source review.
Production import/schema preflight passed before activation. All six authenticated
GETs returned valid measurements through both loopback 8001 and the public API
(12 successful reads); the corresponding 12 anonymous reads were rejected.
Three role heartbeats were fresh, both queues measured and database connected.
Login/logout passed and the retired temporary token returned 401.

The seven protected service identities were unchanged: call API, gateway, dialer,
voice worker, reminder worker, trunk-status worker and Asterisk. The dashboard
process was replaced once; a later read confirmed the same accepted process was
still running. No rollback was needed. The live source checkout remains `2739eea`
with schema 0070; API-web alone imports the immutable maintenance worktree.
Root-private server receipt SHA-256:
`ac6c6ac5e80cb8066aa5df767f06ce3a6cb7e53a0862244312267f89b768e5c1`.

Actual protected-browser acceptance passed normal password login, the dashboard's
four statistic cards, System Health host/provider/queue/worker sections, reload,
all six authenticated reads and logout. The database endpoint was read directly;
this page does not contain a dedicated database-detail component. Other business
widgets were blocked before dispatch, so this does not certify all Command Center
widgets or mutations. The first browser pass found clipped worker-table columns.
Frontend commit `6a8d5afca7d6432c48b2bfb8cd163c8912c8a66b` adds the existing
horizontal-scroll container with keyboard focus and bounds the health grid columns.
Its production build and **17 monitoring UI tests** passed.

Dedicated Admin deployment `dpl_D7kJsPbKhGumnRuMtGtZGbRXJQGv` is READY and assigned
to `https://talkleeai-admin.vercel.app`; its 88 source inputs match the committed
frontend. Vercel Authentication remains enabled for all deployments. Customer
deployment `dpl_FVACeir72YWzMgtb9Wma9oNS1iBi` was unchanged. The second real-browser
pass loaded the new JS/CSS, repeated the monitoring checks, and verified access
to Last Heartbeat by programmatic horizontal scrolling at 1280 and 768 pixels without document
overflow. The title is visible at true scroll top; the earlier screenshot overlap
was caused by element capture scrolling, not a header defect.
Final browser manifest SHA-256:
`c49691c1e181b353ecb9187abb46807631ee0dc4c4dd1d6d9af22ccef8d1163b`.
Scrolling was exercised programmatically; touch gestures and keyboard scrolling
were not separately accepted.
The preceding browser evidence remains separately bound as
`5ad8e26e3f65f1110062073b658e3fac43af03821291901ad11cc68bde990ffd`.

Preserved diagnostic limits: the first local frontend build correctly rejected
missing API configuration; it passed with the explicit production API. The
deployment succeeded, but its local helper failed writing Unicode stderr with
Windows' default encoding. READY state, source metadata, protection and aliases
were then independently re-read; no duplicate deployment was made.
Hosting receipt SHA-256:
`6ca7f109a4eb41bbcd447efa1224a2511ba53a8c5972f9a2a73be3f584edaf3d`.

This maintenance repair does not supply observations the running voice processes
never published. STT, LLM, TTS and Realtime rows can remain **Not measured**;
API-web has telephony disabled, so its PBX adapter status is process-scoped and
unknown. No fixed latency or synthetic provider success is substituted. Nginx's
port-8000 fallback still runs the old backend; its contracts and in-memory CORS
configuration remain a full-release limitation. Vercel deployment protection on
the default Admin alias remains enabled. These six monitoring reads do not certify
all Admin mutations, the wider voice product, alerts or customer acceptance.
At acceptance, the measured backlog was 651 dialer items and two assistant
actions, with zero processing. This is a workload snapshot, not proof those jobs
are stalled or delivered; no queued work was released by this read-only QA.

The production-readiness feature freeze and open CP09/full-release gates remain.
