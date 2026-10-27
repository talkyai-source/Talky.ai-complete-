# Worker, queue and backup signal checkpoint — 28 October 2026

This bounded OP09 repair extends the existing authenticated `/metrics`, Redis
contracts, backup job and Prometheus rules. It starts from full Admin source
`8df88718522406a3522a974cfc8175c4538f9ebe`; no receiver was configured or contacted.

The exporter reuses the existing three worker-role heartbeat classifications,
reads bounded list/consumer-group depths and scheduled due-time lateness, and
reads numeric local-backup status receipts. Every request owns its metric
registry, so a failed read cannot retain a previous healthy gauge. Missing or
invalid observations stay unavailable. The backup script publishes fixed-result
receipts atomically, including early configuration failures; failed, interrupted
and suspicious-shrink runs cannot advance the successful-backup timestamp.
Existing partial archives remain preserved on output-name collision.

The current logical-Docker backup script creates its status directory with
0755 and writes only nonsecret receipt files with 0644, allowing a restricted
API user to read without write authority. Archives retain their private path.
A custom `TALKY_BACKUP_STATUS_DIR` must be shared explicitly by the backup
process and API. Other backup entry points must publish this same contract
before their freshness can be observed; merely installing a timer is not proof.

Validation on Windows:

- Collector/auth/backup selection: **49 passed, one POSIX-only file-type test
  skipped**. A later backup-only run after the collision-preservation fix:
  **12 passed**. These selections overlap and are not added together.
- Existing inbound-alert contracts, worker health and Admin monitoring:
  **37 passed**.
- Official checksum-verified Prometheus **2.54.1** `promtool` accepted all
  **30 rules**, then passed **12 native rule scenarios** covering healthy,
  absent, unavailable, stale, overdue, failure, running, recovery and
  backup-freshness states. Download archive SHA-256:
  `f94b88724a6deaab5a3c3b9f93baba655e93607f5319477bfede05bbf86d192c`.
- The existing WS-K verifier now runs those scenarios with its pinned
  Prometheus image when Docker is available. No Docker/Linux mount execution
  is claimed by the Windows run; the POSIX case remains required in Linux CI.

Limits remain explicit: role timestamps do not inventory all replicas or prove
task progress; scheduled lateness is not ready-list enqueue age; local archive
publication is not restoration, continued file presence or off-host durability.
Initial numeric thresholds require operating acceptance. Existing empty
Alertmanager receivers remain empty. Live scrape continuity, actual fault-to-
receiver arrival, accountable acknowledgement, recovery and outage/restart
proof are still required. This checkpoint does not close OP09 or the feature
freeze, and no production activation is recorded.
