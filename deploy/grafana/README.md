# Grafana dashboards

`dashboards/talky-backend.json` is a starter dashboard covering the four
golden signals (rate, errors, latency, saturation) plus telephony KPIs.

## Importing

1. Grafana → Dashboards → New → Import
2. Upload `talky-backend.json` (or paste its contents)
3. Pick your Prometheus datasource

## Metrics it expects

| Metric | Source |
|---|---|
| `http_requests_total{status,...}` | FastAPI Prometheus instrumentor |
| `http_request_duration_seconds_bucket` | same |
| `pg_stat_database_numbackends` | postgres_exporter |
| `redis_commands_processed_total` | redis_exporter |
| `telephony_active_calls`, `telephony_calls_total{outcome}` | `app/core/telephony_observability.py` |

If a panel reads "No data", that exporter isn't scraped yet. Add it to your
Prometheus config — it's not a dashboard bug.

## Recommended alerts

Set these up in Grafana / Alertmanager once the dashboard is healthy:

- 5xx rate > 1% for 5 minutes  → page
- p95 latency > 1s for 10 minutes → page
- Postgres connections > 80% of pool max  → warn
- Active calls drops to 0 during business hours  → warn
- Redis evictions > 0  → warn (capacity)
