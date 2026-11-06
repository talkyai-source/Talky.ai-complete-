# Prepared physical backup and point-in-time recovery

Selected tools/tests from `a0bd0353` are ported as `pitr.py` and `monitor.py`.
Roles, database/server separation, cutover and endpoint changes are excluded.
No PostgreSQL setting, role, service, mount or production data is changed by
adding these tools. The current snapshot-verified logical producer remains in
place; see [encrypted logical/media copies](OFFHOST.md).

The basebackup/monitor unit+timer pairs in `systemd/` are **prepared templates**,
not enabled by the application installer. Each requires
`/etc/talky/pitr.enabled` and a root-owned `/etc/talky/pitr.env`. The owner supplies
`TALKY_PITR_CAPACITY_BYTES` from actual provisioned storage; no capacity is guessed.
Existing PostgreSQL services `talky_pitr_backup` and `talky_pitr_monitor` must be
configured in protected `/etc/talky/pitr-pg-service.conf` using the existing
`recovery.py` verify-full TLS/passfile rules. This patch creates no accounts or
database grants. Have the database owner establish least-privilege credentials
and any required replication/archive capability through the separate approved
operations procedure.

Owner preparation also includes the compatible PostgreSQL client version,
private `/var/backups/talky-pitr/base` and `/var/backups/talky-pitr/wal`, verified
source archive settings, durable capacity, archive retention and an encrypted
off-host WAL/basebackup transfer cadence. `pitr.py archive-settings` prints a
proposed configuration; it does not apply it. Archive mode activation may need
a database restart; no automatic activation/restart is part of this change.

`base-backup` uses streamed WAL, a backup manifest, tar hashes and a private
atomic publication. Tablespaces are refused. Its receipt explicitly states
the verification performed; checksum success is not a recovered-server proof.
`archive-health` rejects empty/stale archives and future clocks. The monitor's
required-archiving mode fails when WAL position/age evidence is missing. Units
use item16's OnFailure receiver; no second alert transport is configured.

Retention is not enabled in the template: first run `pitr.py prune --dry-run`
with an owner-approved keep window and review the oldest-needed WAL boundary.
The tool only deletes older recognized basebackup/segment files when explicitly
run without `--dry-run`; history/backup-label/unknown files are preserved. A
standalone default16MiB segment assumption must match the source before using
prune; do not use it on a non-default WAL-segment cluster without adaptation.

## Proposed recovery targets — owner approves

**RPO15 minutes and RTO2 hours are proposed, owner approves.** They are not SLA
commitments or achieved measurements. RPO requires encrypted off-host base/WAL
coverage at a cadence of15 minutes or faster, including proof that losing the
source host does not lose required WAL. The current daily logical/media copy
does not satisfy that requirement. Remote WAL automation, production archive
activation, key escrow and owner approval remain operational acceptance gates.

For a timed drill on a disposable isolated host:

1. Record source/client versions, verified backup hashes, required WAL range,
   approved recovery target with explicit UTC offset and the start timestamp.
2. Restore escrowed backup and application encryption keys privately, fetch and
   verify basebackup/WAL/media copies. Include transfer/decryption time in RTO.
3. Prepare a **new** data directory. The tool rejects existing nonempty paths,
   links/special archive members and path traversal. It disables archiving and
   binds the prepared server to loopback even before start is requested.

```bash
python3 scripts/database/pitr.py restore \
  --base-backup /srv/disposable-recovery/base/APPROVED_BACKUP \
  --archive-dir /srv/disposable-recovery/wal \
  --target-data-dir /srv/disposable-recovery/new-data \
  --target-time '2026-11-06T12:00:00+00:00' \
  --port 55439 --start --wait-seconds 600 \
  --receipt /srv/disposable-recovery/pitr-result.json
```

4. Use synthetic before/after markers to prove the exact target-minute boundary,
   not just server startup. Verify expected Alembic heads, all-table counts,
   tenant isolation, representative media hashes and application read readiness.
   Never start dialing/voice/provider workers against the restored copy.
5. Record promotion time, total recovery elapsed time and the last recoverable
   commit. Compare with the proposed targets; retain the receipt and failures.
   Restore one tenant only after the separate shared-identity/audit/immutable-
   receipt reconciliation plan is approved. Full-backup recovery is not proof
   of safe merging into today's live tenant state.
6. Stop only the disposable instance that was started for the drill; preserve
   evidence and remove only owner-verified disposable paths.

Local tests use synthetic files and strict clients. A passed unit suite is not
a real PostgreSQL target-minute recovery, host-loss drill, Linux unit execution
or production RPO/RTO measurement. None of those owner operations is claimed.
