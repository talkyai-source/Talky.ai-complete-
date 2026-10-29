# Final live and database observation — 28 October 2026

The customer dashboard remains live at source `80b52743d8073d0b3ed07fb260c82add50088d6e`, Vercel production deployment `dpl_3CuQmP8NmyUwEGryhmbJ9kCpsdYR`. The exact alias/source and all nine anonymous routes passed at 13:27 UTC. The backend checkout, API/API-web working sources and running gateway remain `10a8b9f0f9d8e7d5c59140f1e4829790050161b9`; required services are active. See the [read-only live receipt](artifacts/paid-live-final-20261028/summary-20261028T132737Z.json).

The first local Python public-API probe returned a generic `URLError`. A retained [transport follow-up](artifacts/paid-live-final-20261028/public-api-transport-20261028T133122Z.json) subsequently passed Python, native PowerShell and Windows curl HTTPS checks with certificate verification enabled and no proxy/TLS bypass. Health/readiness returned 200; the certificate name and validity matched. The initial error was not reproducible; its cause is unknown, and no outage or trust-store defect is inferred from it.

The combined candidate `54665bdbe1ea074d23ecb3ab48d6f7bbf20b5aa6` has [complete bounded Ubuntu installation/update/recovery evidence](2026-10-28-fresh-update-acceptance.md). It has **not** replaced the live backend or migrated the production database. No production configuration, credentials, customer rows or privileges were changed by this observation.

## Actual production database boundary

A query using the existing runtime DSN only in remote process memory ran with `default_transaction_read_only=on`. It queried catalog metadata, not customer records. The [metadata receipt](artifacts/paid-live-final-20261028/database-final-metadata.json) at 13:33:25 UTC confirms:

- Schema head `0081_admin_payment_merge`.
- Database TLS disabled on the observed connection.
- Runtime role owns 123 public tables.
- `CREATEDB`, `CREATEROLE` and `REPLICATION` are enabled; `SUPERUSER` and `BYPASSRLS` are disabled.

The [catalog inventory](artifacts/paid-live-final-20261028/database-final-inventory.json) contains every one of the canonical 0082 profile's 113 tables plus ten additional tables:

`audit_chain_state`, `audit_event_types`, `emergency_access_requests`, `rate_limit_events`, `suspension_propagation_queue`, `tenant_ai_configs_backup_20260907`, `tenant_ai_credentials`, `tenant_recording_policy`, `tenant_telephony_credentials`, `worker_status`.

The canonical fresh-database grants profile is therefore **not a reviewed production inventory**. Its strict unknown-object and ownership checks must remain intact. Before production privilege conversion, reconcile these objects against their actual application/retention purpose, preserve existing data and policies, prepare the appropriate reviewed owner/runtime access profile, and qualify it on an isolated restore of the real catalog. Do not delete the additional objects, grant blanket access, or treat a backup-named table as an ordinary runtime table merely to make the canonical check pass. This is part of the existing database cutover scope, not permission for unrelated schema redesign.

## Remaining deployment and paid-use acceptance

The secure privileged-access reference and designated separate PostgreSQL destination remain missing. The actual database inventory/role/TLS conversion, candidate-bound freeze/drain/backup and coordinated activation are still required. Controlled real calls and the failed Groq Q43 profile, signed-in customer journeys, receiver delivery, off-host recovery/RPO/RTO, finance/load acceptance, independent handover and the three requested videos retain their own evidence requirements. Stripe live-account integration remains intentionally last; it is not the only unfinished requirement.

The owner-approved exclusion of reseller operations and staff invitations is live in the customer dashboard and remains future work. No paid-use gate or feature-freeze exit is closed by the technical qualification or this live observation.
