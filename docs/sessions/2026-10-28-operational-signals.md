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

## External database binding and atomic-receipt follow-up

The unified candidate additionally binds the existing external PostgreSQL
scheduled wrapper to the same exporter. Its systemd StateDirectory is
`/var/lib/talklee-backup-monitoring`, owned by the dedicated backup identity;
the API reads it through the explicit `TALKY_BACKUP_STATUS_DIR` environment
setting. The installer does not enable the optional backup schedule or create
a successful observation. No API write grant was added. The backup wrapper
refuses placing public status inside its private archive root and preserves
an active attempt when another invocation fails to acquire the existing lock.

Review found that two separately atomic files could expose half an update if
the second publication failed. Both producers now use the same standard-library
publisher, `scripts/database/backup_status.py`, which stores the outcome and
`last_success_at` together in one atomic `backup-attempt.json`. A failed
pre-publication write preserves the previous receipt. The successful timestamp
reflects the respective producer's actual checked publication; the external
wrapper continues to report that retention, off-host copy and restore are not
performed by its backup operation.

Follow-up verification:

- Current collector/legacy-backup/auth selection: **50 passed, one POSIX-only
  skip** on Windows. The skipped symlink/FIFO protection case then passed on
  Ubuntu WSL with no skip.
- Shared publisher and external wrapper: **11 passed, zero skips** on Ubuntu
  WSL, including real file modes and advisory-lock exclusion. Windows ran nine
  cases and explicitly skipped those two POSIX cases.
- Existing recovery safety tests: **23 passed**. No real database operation,
  archive copy, service activation or notification was initiated by these
  synthetic wrapper/file tests.
- One additional cross-contract test passed using the actual shared publisher
  and actual metrics reader together. The fresh-host environment example now
  binds the external backup service's status directory without enabling it.
- Independent review found and corrected the legacy shell path-separation
  edge before directory permissions or failure publication. The final backup
  selection passed **14 tests**, and its two new private-directory boundary
  cases also passed on Ubuntu. The installer template tests passed **40 tests**.
  The independent follow-up review found no further actionable issue within
  the publisher/wrapper/permissions scope. These overlapping selections are
  not added into a single total.

Rule names and PromQL are unchanged by this follow-up; the recorded native
rule validation remains applicable. Deployed service permissions, filesystem
mounts, fault delivery and restore acceptance remain separate operating gates.

## Native Linux rule CI coverage

Final review found that the native scenarios were reachable only through the
optional WS-K verifier. The dedicated
[operational monitoring workflow](../../.github/workflows/operational-monitoring-rules.yml)
now selects relevant metric, heartbeat, queue, rule and backup changes on pull
requests, with manual dispatch available. It verifies the official Prometheus
2.54.1 Linux amd64 archive before extracting only `promtool`, then checks rule
syntax and runs `operational_signals_test.yml` against a read-only source
directory. Temporary evaluation data goes to the runner's temporary directory.
It starts no service and configures no receiver or runtime credentials.

The pinned archive SHA-256 is
`31715ef65e8a898d0f97c8c08c03b6b9afe485ac84e1698bcfec90fc6e62924f`,
recorded from the official
[v2.54.1 checksums](https://github.com/prometheus/prometheus/releases/download/v2.54.1/sha256sums.txt)
for `prometheus-2.54.1.linux-amd64.tar.gz` in the
[Prometheus release](https://github.com/prometheus/prometheus/releases/tag/v2.54.1).
Workflow structure and embedded Bash syntax were reviewed locally; the new
workflow's actual Ubuntu execution remains pending until this commit is pushed.
