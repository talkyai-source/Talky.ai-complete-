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
- creation of Answer-ambiguity, settlement-disabled, and reservation-overage
  billing holds;
- detection of a stale reservation by the proof-aware recovery scan; and
- unconfirmed Asterisk transfer cleanup or a transfer left in-flight.

Those are occurrence/risk signals, not full backlog monitoring. The current
application and checked-in Prometheus topology do **not** export the oldest
unresolved billing-hold age, oldest stale-reservation age, incomplete recovery
backlog, general ARI orphan backlog, PostgreSQL/Redis target or saturation
metrics, application DB-pool pressure, CPU/cgroup memory saturation, or dialer
queue depth. There are also no PostgreSQL, Redis, node, or cAdvisor exporters in
the checked-in observability compose file. Rules for those names would be
dead/aspirational, so they are intentionally absent.

Production inbound remains blocked until operations installs real exporters or
equivalent measured collectors for every missing signal, approves numeric
thresholds, validates the final Prometheus and Alertmanager configuration with
the exact deployed binaries, and fires every release-critical alert through a
real on-call destination.

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

## Validation

Use WS-K verifier:

```bash
bash telephony/scripts/verify_ws_k.sh telephony/deploy/docker/.env.telephony
```
