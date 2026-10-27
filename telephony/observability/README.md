# WS-K Observability Baseline

This directory contains production-oriented observability artifacts for
Phase 3 WS-K.

## Components

1. `prometheus/prometheus.yml`
   - Scrape config for backend `/metrics`.
   - Loads recording/alert rules.
2. `prometheus/rules/telephony_ws_k_rules.yml`
   - Recording rules and alert rules for rollout SLO gates.
3. `alertmanager/alertmanager.yml`
   - Alert grouping, routing, and inhibition defaults for telephony.
   - Its receivers deliberately have no delivery integrations. The repository
     has no approved pager destination, so this baseline must never be counted
     as paging evidence.

## Inbound-critical coverage and blockers

The checked-in rules now use only metrics emitted by the backend:

- backend metrics target loss and stale database-backed metric refresh;
- fail-closed inbound routing/admission dependency failures;
- absence of a successful inbound call for 90 minutes, refreshed by the
  hourly carrier-hairpin synthetic timer;
- creation of Answer-ambiguity, settlement-disabled, and reservation-overage
  billing holds;
- detection of a stale reservation by the proof-aware recovery scan; and
- unconfirmed Asterisk transfer cleanup or a transfer left in-flight.

Those are occurrence/risk signals, not full backlog monitoring. The current
application and checked-in Prometheus topology do **not** export the oldest
unresolved billing-hold age, oldest stale-reservation age, incomplete recovery
backlog, general ARI orphan backlog, PostgreSQL/Redis target or saturation
metrics, application DB-pool pressure, CPU/cgroup memory saturation, or ready-list
enqueue age. There are also no PostgreSQL, Redis, node, or cAdvisor exporters in
the checked-in observability compose file. Rules for those names would be
dead/aspirational, so they are intentionally absent.

The synthetic timer is installed from `backend/systemd/` and intentionally
requires `/etc/talky/inbound-synthetic.env`. The deployment preflight refuses
to select a new production commit until that file contains a validated E.164
DID and PJSIP trunk endpoint. The DID must belong to an always-open,
agent-first synthetic campaign; otherwise a correct after-hours rejection
would look like a media outage. The probe originates out through the carrier
and back to that DID; success is recorded only when the returned inbound leg
reaches confirmed first agent audio. A local dialplan-only call cannot satisfy
this signal.

Production inbound remains blocked until operations installs real exporters or
equivalent measured collectors for every missing signal, approves numeric
thresholds, validates the final Prometheus and Alertmanager configuration with
the exact deployed binaries, and fires every release-critical alert through a
real on-call destination.

## Worker, queue and local-backup observations

The authenticated backend `/metrics` additionally emits bounded observations
from the existing Redis and backup paths. Each request builds fresh metric
families, so an unavailable read removes the old measurement and emits
`*_observation_success 0`. No tenant/job identifiers, payloads, archive paths or
credentials appear in labels. `talky_operational_signal_contract 1` identifies
this exporter contract; an older reachable target without it fires the missing
signals rule. These observations do not depend on the canary evidence scope.

- `talky_worker_heartbeat_healthy{worker}` reuses the existing dialer, voice and
  reminder timestamp reader and 180-second freshness boundary. Missing keys
  mean unhealthy; invalid/future timestamps and Redis failures mean unavailable.
  A role heartbeat cannot inventory individual replicas or prove task progress.
- `talky_queue_jobs{queue,state}` exposes dialer queue depth from actual list
  lengths, the processing ZSET and scheduled ZSET; when Streams is selected,
  its consumer group's lag/pending counts are also read. Acknowledged stream
  history is excluded. List inventory stops at 25 SCAN pages/5,000 keys with a
  three-second deadline and at most 100 outstanding length reads. Commands
  are a bounded observation, not a cross-command atomic snapshot.
- `talky_scheduled_queue_overdue_seconds` measures age past the earliest shared
  scheduled-ZSET due score, zero for empty/future-only work. It is **not**
  ready-list enqueue age: job creation timestamps cannot establish queue wait.
  Deliberate campaign/quota deferral remains possible; inspect actual policy.
- `talky_backup_*` reads the existing backup job's numeric timestamps and fixed
  result from `/var/lib/talky/monitoring`. The job publishes each small file
  atomically. Failure, including rejected/shrunken archives, leaves the prior
  successful timestamp intact. A started attempt without completion stays
  running; interruption can therefore become a stuck-attempt alert. A successful
  receipt requires zero command exit after archive verification and retention.
  This proves neither restoration nor off-host durability, and cannot prove a
  previously written archive has not subsequently been removed.

Run scheduled backups through the existing single-instance systemd oneshot.
The backup process owns the status directory (0755) and writes nonsecret receipt
files (0644); archives remain in their existing private 0700 directory. A
restricted API user needs traversal/read access only, never write access. For
a different topology, set the same `TALKY_BACKUP_STATUS_DIR` in the backup unit
and metrics API environment and arrange a trusted local read-only mount. Do
not point a web process at the dump directory or invent a receipt from a timer
configuration. Missing/malformed/unreadable receipts emit unavailable, not
successful backup. No previous archive is automatically adopted as success.

Checked-in initial rules use one-minute worker/failure persistence, scheduled
due age above five minutes for two minutes, daily-backup freshness above 36
hours, and started-backup age above two hours. These are reviewable operating
thresholds, **not an accepted SLO**. Confirm them against the designated schedule
and workload. With the existing 180-second heartbeat threshold, 15-second rule
evaluation, scrape cadence, one-minute persistence and receiver grouping,
measure actual worker-death receipt against the proposed five-minute target.
No delivery timing is claimed from source configuration. A missing receiver,
stale/absent Prometheus process, muted route, backup mount failure and restart
still require the existing OP09 controlled-fault acceptance.

## Security

Production requires `TELEPHONY_METRICS_TOKEN` in the backend. Prometheus sends
the matching `X-Metrics-Token` value from a mounted file; the token must never
be written into tracked YAML or passed on a command line.

Provision the same random value through the backend's secret-management path,
then create a host file without a trailing newline. A dedicated supplementary
group lets the non-root Prometheus container read it without making the token
world-readable:

```bash
getent group talky-prometheus-secrets >/dev/null || \
  sudo groupadd --system talky-prometheus-secrets
sudo install -d -o root -g talky-prometheus-secrets -m 0750 \
  /opt/talky/secrets
sudo install -o root -g talky-prometheus-secrets -m 0640 /dev/null \
  /opt/talky/secrets/telephony_metrics_token
read -rsp 'Metrics token: ' metrics_token && printf '\n'
printf '%s' "$metrics_token" | \
  sudo tee /opt/talky/secrets/telephony_metrics_token >/dev/null
unset metrics_token
sudo chown root:talky-prometheus-secrets \
  /opt/talky/secrets/telephony_metrics_token
sudo chmod 0640 /opt/talky/secrets/telephony_metrics_token
export TELEPHONY_METRICS_TOKEN_FILE=/opt/talky/secrets/telephony_metrics_token
export PROMETHEUS_SECRET_GID="$(getent group talky-prometheus-secrets | cut -d: -f3)"
```

`docker-compose.observability.yml` refuses to render unless
`TELEPHONY_METRICS_TOKEN_FILE` and the numeric `PROMETHEUS_SECRET_GID` are set.
The container keeps its non-root user and receives only that supplementary
read group. `prometheus.yml` uses the file-backed custom-header form:

```yaml
http_headers:
  X-Metrics-Token:
    files:
      - /etc/prometheus/secrets/talky_metrics_token
```

The checked-in Alertmanager file contains no webhook, Slack, SMTP, or pager
credential placeholders and no fake on-call address. Operations must render
the approved delivery integration outside the repository, source credentials
from mounted secret files supported by the chosen integration, validate the
rendered file with the exact Alertmanager image, and preserve only its
non-secret hash/version in the frozen manifest. The baseline starts with empty
receivers, so it can group alerts locally but cannot notify a person.

This configuration artifact does not install a production Prometheus service
or a real paging route; those release gates remain separate operational work.

## AD08: production incident delivery overlay

The production path is the existing Prometheus rules and Alertmanager. The
backend adapter at `POST /api/v1/webhooks/alertmanager` records delivered alerts
in the existing incident store. It does not evaluate thresholds or send pages.
Keep external notification delivery in Alertmanager so an API/database outage
does not also disable the route to the responsible operator. The legacy Admin
memory settings are not the configuration source for this path.

`alertmanager/render_production_config.py` preserves the checked-in grouping,
severity routes and inhibition rules. It requires an operator-approved native
receiver input containing **all three** names (`telephony-default`,
`telephony-critical`, `telephony-warning`) and adds incident ingestion to each.
Adding only a default receiver is insufficient because the severity child
routes select their own receivers. The safe baseline files stay unchanged.

Use the backend Python environment; `backend/requirements.txt` already pins
PyYAML. No extra runtime, notification service, or template engine is needed.
The approved input, generated configuration and credentials must be absolute
paths outside any Git checkout. The renderer performs no network requests,
notification sends, deployments or credential generation. It writes atomically
and preserves an existing output when rendering or requested native validation
fails. It prints only the configuration hash and validation state.

Have operations supply the approved receiver, ownership/escalation contact and
change reference. An input file contains only a native `receivers` list. For
an approved service that accepts Alertmanager's native webhook payload, the
following is the **structure**, not an approved destination or delivery proof:

```yaml
receivers:
  - name: telephony-default
    webhook_configs:
      - url_file: /etc/alertmanager/external/receiver_url
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/external/receiver_credential
  - name: telephony-critical
    webhook_configs:
      - url_file: /etc/alertmanager/external/receiver_url
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/external/receiver_credential
  - name: telephony-warning
    webhook_configs:
      - url_file: /etc/alertmanager/external/receiver_url
        http_config:
          authorization:
            type: Bearer
            credentials_file: /etc/alertmanager/external/receiver_credential
```

Distinct approved destinations per severity are allowed. The renderer also
accepts native `slack_configs` with `api_url_file`, `pagerduty_configs` with
`routing_key_file` or `service_key_file`, `opsgenie_configs` with `api_key_file`,
and `email_configs` with explicit `to`, `from`, `smarthost` and TLS. Use native
file fields for authentication (for example `auth_password_file`); no inline
passwords, secret URLs or tokens. Referenced external files must exist beneath
the mounted `/etc/alertmanager/external` directory. These are supported native
configuration formats, not newly configured or approved providers. Unsupported
native fields still require the exact-image validation below.

Provision a separate `ALERTMANAGER_INGEST_TOKEN` for the backend: 32–256 printable
ASCII nonwhitespace characters. Put the same value in the host file named by
`ALERTMANAGER_INGEST_TOKEN_FILE`. Never reuse the broad internal telephony token
or the read-only metrics token. Set a stable backend `ALERTMANAGER_SOURCE_ID`
(default `platform-alertmanager`) so changing a release does not change alert
identity. Do not place token bytes in command arguments or repository files.
Limit file ownership/read access to the operator and the container's dedicated
supplementary group; use `0640` files and traversable `0750` directories. The
renderer initially writes its output with private permissions; grant that
group read access to the completed artifact before container validation.

Set these nonsecret deployment inputs through the approved host environment:

- `ALERTMANAGER_CONFIG_FILE`: absolute generated production configuration path.
- `ALERTMANAGER_INGEST_TOKEN_FILE`: absolute dedicated ingestion credential file.
- `ALERTMANAGER_EXTERNAL_SECRETS_DIR`: absolute approved receiver secret directory.
- `ALERTMANAGER_SECRET_GID`: numeric group with read access to all three mounts.
- `ALERTMANAGER_APPROVED_RECEIVERS_FILE`: absolute approved native input file.
- `ALERTMANAGER_INGEST_URL`: exact `/api/v1/webhooks/alertmanager` URL, HTTPS
  unless it uses same-host loopback. No URL credentials, query or fragment.

Render without deploying or contacting a receiver:

```bash
python telephony/observability/alertmanager/render_production_config.py \
  --approved-receivers "$ALERTMANAGER_APPROVED_RECEIVERS_FILE" \
  --output "$ALERTMANAGER_CONFIG_FILE" \
  --ingestion-url "$ALERTMANAGER_INGEST_URL" \
  --external-secrets-dir "$ALERTMANAGER_EXTERNAL_SECRETS_DIR" \
  --ingest-token-file "$ALERTMANAGER_INGEST_TOKEN_FILE"
```

An optional `--amtool /absolute/path/to/amtool` validates before replacement
and refuses versions other than **0.27.0**. Without that argument the renderer
explicitly reports **NOT natively validated**. Structural tests are not a
substitute for native validation. The source-pinned supported file syntax is
in [Alertmanager 0.27.0](https://github.com/prometheus/alertmanager/blob/v0.27.0/config/notifiers.go)
and its [Prometheus common 0.46.0 dependency](https://github.com/prometheus/common/blob/v0.46.0/config/http_config.go).

Validate the actual mounted artifact with the exact deployed image, without
network access. This checks native configuration, not recipient delivery:

```bash
docker run --rm --network none \
  --group-add "$ALERTMANAGER_SECRET_GID" \
  --mount "type=bind,src=$ALERTMANAGER_CONFIG_FILE,dst=/etc/alertmanager/alertmanager.yml,readonly" \
  --mount "type=bind,src=$ALERTMANAGER_INGEST_TOKEN_FILE,dst=/etc/alertmanager/secrets/ingest_token,readonly" \
  --mount "type=bind,src=$ALERTMANAGER_EXTERNAL_SECRETS_DIR,dst=/etc/alertmanager/external,readonly" \
  --entrypoint /bin/amtool prom/alertmanager:v0.27.0 \
  check-config /etc/alertmanager/alertmanager.yml

docker compose \
  -f telephony/deploy/docker/docker-compose.observability.yml \
  -f telephony/deploy/docker/docker-compose.observability.production.yml \
  config --quiet
```

The production override replaces the mount at the actual container config
path and adds read-only secret mounts. Missing source paths fail instead of
being created as directories. It preserves the persistent Alertmanager volume
and non-root container user. The base Prometheus metrics-token environment is
also required when rendering the combined compose. Use both files for an
approved deployment; the override alone is not a stack. Review mount paths,
read permissions, image and the generated hash in the deployment manifest.
The configuration hash does not attest to later changes in mounted destination
or credential files; retain their approved secret-manager version references
and repeat delivery acceptance after changes, without publishing secret bytes.
Keep a prior validated config for rollback; retain incident records. Rotating
the ingestion secret requires coordinated backend/Alertmanager configuration,
then a delivery probe; an authentication failure is not evidence of recovery.
Because rendering replaces a file atomically, recreate the Alertmanager
container with the same two compose files during the approved rollout. A hot
reload alone may still read the old inode from an existing file bind mount.

### AD08 acceptance and limits

- The adapter accepts webhook version 4, 1–100 alerts, at most 256 KiB, and
  requires `truncatedAlerts: 0`. Ingestion sets `max_alerts: 0` because truncation drops
  data rather than paging it. A larger group is rejected as a whole; correct
  trusted grouping/rule cardinality and redeliver. Do not count rejected or
  truncated groups as successfully recorded incidents.
- Alertmanager 0.27 retries HTTP 5xx responses; HTTP 4xx responses (including
  408) are terminal for that delivery. The adapter uses retryable 503 for a
  transient body-read timeout or database unavailability. Invalid credentials,
  malformed/truncated payloads and exceeded bounds need operator correction
  and redelivery; they are not automatically retried into successful records.
  This follows the pinned [native webhook retrier](https://github.com/prometheus/alertmanager/blob/v0.27.0/notify/util.go).
- Deduplication uses the configured source plus alert fingerprint and start
  time. Repeats and concurrent deliveries must preserve one episode across
  API/database restarts; a genuinely new episode must create another incident.
  Test wrong/missing credentials, malformed batches, database failure and
  retry. Only committed ingestion is success.
- Alertmanager `firing`/`resolved` maps to stored source `firing`/`recovered`
  and describes monitoring's observation. Human
  acknowledgement/resolution is separate and must preserve the responsible
  actor/time. Source recovery must not fabricate human acknowledgement.
- Silence and inhibition suppress deliveries to the incident webhook too.
  Recorded incidents represent delivered notifications, not every evaluated
  alert. Preserve the approved silencing/inhibition scope in acceptance notes.
- A persistent Alertmanager volume is not proof of a durable historical event
  queue. Exercise an alert that fires **and recovers while ingestion is down**,
  including an Alertmanager restart. Record any gap; do not claim lossless
  incident history from a successful post-recovery request.
- Independently prove the approved recipient receives and acknowledges the
  page when the API/database is unavailable, plus recovery notification where
  configured. A webhook 200, failed systemd unit or healthwatch log is not
  evidence that a responsible person received an alert.
- Record named recipient, rule/config hash, fault time, notification receipt,
  operator acknowledgement and recovery. Measure the worker-death target
  against heartbeat expiry, polling/retries, grouping and actual delivery.
  The existing healthwatch timer only logs/exits; it is not wired to this
  adapter. Prometheus now reads the same role heartbeat keys directly through
  authenticated `/metrics`; healthwatch remains an independent log signal.
  Missing per-replica progress, ready-list enqueue age, provider coverage and
  operating acceptance remain explicit; do not add a second evaluator or
  imagined metrics.

## Validation

Use WS-K verifier:

```bash
bash telephony/scripts/verify_ws_k.sh telephony/deploy/docker/.env.telephony
```
