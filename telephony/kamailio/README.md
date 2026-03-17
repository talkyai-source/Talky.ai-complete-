# Kamailio Backup (Non-Active)

This directory is retained as a backup snapshot only.

## Status

- Runtime SIP edge is **OpenSIPS** under `telephony/opensips/`.
- Docker compose and validation scripts target OpenSIPS.
- Files in this folder are for fallback reference and emergency restore planning.

## Backup Snapshot Contents

- `conf/kamailio.cfg`
- `conf/dispatcher.list`
- `conf/address.list`
- `conf/tls.cfg`
- `certs/` (local backup cert placeholders)

## Guardrails

1. Do not point active runtime to this folder.
2. Do not modify backup files during normal feature work.
3. If fallback is required, create a dedicated rollback PR with explicit runbook updates.
