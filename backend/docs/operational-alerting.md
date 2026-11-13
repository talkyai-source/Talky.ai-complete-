# Operational alert delivery and healthwatch

The existing healthwatch timer now runs every minute. Its unit invokes
`deploy/healthwatch.sh --extended`: the worker HTTP probe retains restart grace,
then `scripts/healthwatch.py` observes additional host and dependency signals,
even if the worker probe failed. A nonzero result fails the service and routes
through `OnFailure=talky-notify@%n.service`. Other tracked Talky service, timer
and target definitions use the same failure route.

The notification template is the sole deliberate exception: it has no
`OnFailure`, restart policy or boot enablement, so failed delivery cannot
recursively create more notifications. It runs as `talky`, with a 30-second
systemd deadline and separate 10-second channel deadlines. The monitor has a
55-second systemd deadline, a 20-second asynchronous observation budget and
bounded individual I/O/host commands. Local host command completion and cleanup
can extend beyond the asynchronous budget; systemd supplies the outer bound.

## Destination configuration and truthful health

The owner must configure one or both settings in the existing protected runtime
configuration (do not commit their values):

- `TALKY_ALERT_WEBHOOK_URL`: a Slack-compatible incoming webhook URL.
- `TALKY_ALERT_EMAIL_TO`: one on-call address. This reuses the existing
  `EmailService.send_email` interface and that sender's configured credentials;
  it does not introduce another email provider or alter sender policy.

No destination produces the exact journal message
`alert not delivered: no destination` and a nonzero notifier result.
`GET /api/v1/healthz/deep` includes `alerting: unconfigured`, `configured` or
`unavailable`. `configured` means a destination is present, not that it was
delivered or acknowledged. Alerting configuration does not change the existing
DB/Redis readiness decision. The public field never contains a destination.

The webhook body contains a fixed service label and instruction to inspect the
host journal; it does not attach journal bodies, customer data or credentials.
Each configured channel is attempted independently and reports its actual
delivery outcome. A failed webhook does not prevent an email attempt. The new
wrapper logs exception types, not webhook URLs or response bodies. The existing
email sender has its own recipient/error logging policy; this change does not
claim to sanitize that entire downstream sender.

## Checks and their limits

| Signal | Decision |
|---|---|
| Disk | Used space greater than 85%; `TALKY_MONITOR_DISK_PATH` defaults to `/`. Set it to the filesystem that actually contains monitored data. This is one configured filesystem, not automatic coverage of every mount. |
| Memory | `(MemTotal-MemAvailable)/MemTotal` greater than 90%, from `/proc/meminfo`; malformed/missing data is unavailable. |
| Redis | `INFO memory`: used/maxmemory greater than 90%; missing or unlimited maxmemory is unavailable. Existing single/sentinel/cluster connection configuration is reused; unsupported INFO shapes remain unavailable. |
| Workers | Existing Redis heartbeat registry: dialer, voice and reminder. Missing, invalid, future or at-least-180-second-old heartbeats alert. This is role heartbeat coverage, not every process/task's progress. |
| Payments | Count-only platform receipt query for `processed_webhook_events.state='needs_review'`; any count alerts. No event payload or recipient is read or emitted. These receipts include unassigned tenants and use the platform-only RLS policy. |
| Trunks | Capped tenant-directory discovery followed by explicit tenant-scoped reads. Existing type/direction-aware runtime checker handles registration and IP-auth trunks. Redis atomically tracks first/last unhealthy observations; five continuous observed minutes alerts. Healthy/disabled/deleted trunks clear the marker; a gap over five minutes resets it. Stale/missing/future runtime evidence is separately unavailable, not claimed registration history. State expires after 15 minutes. |
| Providers | Existing last-observation store reports failed observations separately. Empty means no recent evidence. **True provider error rate remains unavailable:** retained records are last results per credential/tenant, with no request denominator. |
| Certificates | Local public PEM files in comma-separated `TALKY_MONITOR_CERTIFICATE_PATHS`, maximum eight. Less than 14 days remaining alerts. No paths means unconfigured; parse/read failures mean unavailable. No remote certificate request or private key read. |
| Clock | `timedatectl` must report synchronized. Offset is read from `chronyc tracking`, falling back to `timedatectl timesync-status`; absolute offset over one second alerts. Missing command/evidence is unavailable. No clock changes are performed. |

Checks publish safe JSON to the journal. Required unavailable/unconfigured
checks fail the monitor visibly. The explicitly known provider-rate gap is
reported but does not alone manufacture a provider outage. A normal command
exit therefore does **not** certify complete provider-rate coverage. The bounded
inventory defaults to 1,000 tenants and at most 1,000 trunk rows; reaching a cap
is unavailable, never silently complete. Trunk continuity can establish only
the sampled observations; transient recovery between samples is not observable.

The host timer/service use two worker attempts, five-second request timeouts
and a five-second retry delay. The standalone shell's prior defaults remain.
These tighten the nominal detection window toward the QA five-minute objective;
actual delivery latency, host scheduling and an end-to-end drill are unverified.

## Owner activation and synthetic drill

The installer retains the existing nightly model catalog, recording inventory,
memory-priority and journal-retention wiring. It exposes `talky-alert-test`.
Install/restart only through the approved release procedure. Owner commands:

```sh
sudo bash /opt/talky/backend/systemd/install-services.sh
sudo systemctl daemon-reload
sudo systemctl enable --now talky-healthwatch.timer
sudo systemctl start talky-healthwatch.service
sudo systemctl status talky-healthwatch.timer talky-healthwatch.service
journalctl -u talky-healthwatch.service -n 30 --no-pager
talky-alert-test                 # dry-run; no message sent
talky-alert-test --send          # explicit synthetic test to configured destinations
journalctl -u 'talky-notify@*' -n 30 --no-pager
```

The installed alert-test executable must retain executable mode. Choose a
public certificate path readable by `talky`; do not grant it private-key or
backup-content access. Confirm the configured recipient actually receives the
synthetic message. In a separately approved maintenance window, stop one worker,
record detection/delivery timestamps, restart it, and record recovery and the
human acknowledgement in the owner's incident system. No such send, stop or
drill was performed during this code change. OnFailure is not itself an incident
acknowledgement/recovery workflow or an off-host uptime monitor (see
`external-monitoring.md`). Repeated failed timer passes can repeat alerts.

Rollback removes these notification routes and restores the prior healthwatch
unit/timer/script through an approved code release and daemon reload. No database
migration is involved; leftover Redis continuity keys expire automatically.
Do not remove backup files or reset unrelated service state to roll this back.

## Unfinished coverage and handoffs

**Backup-age installation is pending format alignment (item20).** The requested
`scripts/database/systemd/talklee-db-backup-health.*` reader expects timestamped
receipt bundles in `/var/backups/talklee` under a separately provisioned
`talklee-backup` account. The currently tracked production backup service writes
root-private flat `talky-*.dump` plus `.sha256` files in `/var/backups/talky`.
Changing only the path would not make that reader compatible. These staged units
have failure routes, but item16 deliberately does not enable a mismatched reader,
create the backup account or widen `talky` permissions. A compatible adapter or
approved bundle cutover and real backup-age rehearsal are still required.

**Hand back to Claude: actual provider rate instrumentation.** Add bounded
shared success/failure attempt counters at the real LLM, STT and TTS producer
boundaries in the protected `app/domain/services/voice_pipeline/**` and
`app/realtime/**` paths, including cancellation/partial-output semantics. Counters
need a consistent time window, credential/tenant scope without secrets and a
request denominator; last-observation hashes must remain observations. Only then
can the monitor evaluate an agreed rate threshold and prove a spike alert. No
protected producer file was edited here.

The owner still chooses and activates destinations, resolves the carrier
registration, confirms local certificate/NTP availability, installs compatible
backup health, runs the delivery/acknowledgement/recovery drill and activates
off-host monitoring. This is partial code coverage of F9-01/F9-02/F15-04/F7-06,
not a claim that a live on-call system or carrier line now works.

Primary command references: [systemd timedatectl](https://www.freedesktop.org/software/systemd/man/262/timedatectl.html)
and [chronyc tracking](https://chrony-project.org/doc/4.4/chronyc.html).
