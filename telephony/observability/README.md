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

## Security

If `TELEPHONY_METRICS_TOKEN` is configured in backend:
1. Scraper must send `X-Metrics-Token`.
2. Configure matching header in Prometheus scrape job.

## Validation

Use WS-K verifier:

```bash
bash telephony/scripts/verify_ws_k.sh telephony/deploy/docker/.env.telephony
```
