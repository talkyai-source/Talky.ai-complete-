# External availability monitoring — F9-11 / QA-MON-004

**Prepared configuration; owner activation and alert delivery are unverified.**
This document does not create a monitor or establish current production uptime.
No production URL was requested while preparing it. There is no executable
before/after regression for this documentation-only item.

Run these checks from infrastructure outside the Talklee application host and
its failure domain. A timer or curl command on the application server cannot
report that the server itself has disappeared. The owner must select and
configure the external monitoring account and the on-call destination. Item 16's
alert delivery remains unverified; checks without a confirmed recipient do not
close this finding.

## Exact checks

Use unauthenticated HTTPS GET, certificate verification enabled, a 5-second
connection timeout and a 10-second total timeout. Run each check every 60 seconds.
Require HTTP 200, not a broad 2xx/3xx match. DNS/connect/TLS errors, timeouts,
unexpected redirects, missing/malformed expected JSON and non-200 responses fail.
Do not add authentication tokens, bypass TLS checks, or accept a login page as
healthy. If an intended canonical redirect exists, the owner must verify and
explicitly configure its HTTPS destination instead of enabling unrestricted
redirect following.

| Name | Exact URL | Successful response | Alert threshold |
|---|---|---|---|
| Public website | `https://talkleeai.com/` | HTTP 200 and a nonempty page body | 2 consecutive failures |
| API liveness | `https://api.talkleeai.com/health` | HTTP 200; JSON `status == "healthy"` | 2 consecutive failures |
| API dependency readiness | `https://api.talkleeai.com/api/v1/healthz/deep` | HTTP 200; JSON `ready == true`; retain the public `alerting` state separately | 2 consecutive failures |
| Worker heartbeats | `https://api.talkleeai.com/api/v1/healthz/workers` | HTTP 200; JSON `healthy == true` | 1 failure; the endpoint already allows a 180-second heartbeat age |

Send a recovery notification after 2 consecutive successful checks. Deduplicate
ongoing alerts by check and retain acknowledgement, first failure, delivery and
recovery timestamps. With these intervals, site/API detection normally takes at
most about 130 seconds after a hard failure. A worker that stops refreshing just
after its last heartbeat can take about 250 seconds to alert, including its
180-second stale threshold, polling phase and request timeout. These are
configuration bounds, not a demonstrated delivery SLA; the external scheduler
and notification channel must be tested.

This provider-neutral configuration is a translation guide for the owner's
monitor console, not an installed or executable repository integration:

```yaml
method: GET
interval_seconds: 60
connect_timeout_seconds: 5
total_timeout_seconds: 10
verify_tls: true
follow_redirects: false
expected_http_status: 200
recovery_successes: 2
checks:
  - name: talklee-website
    url: https://talkleeai.com/
    failure_threshold: 2
    body: nonempty
  - name: talklee-api-liveness
    url: https://api.talkleeai.com/health
    failure_threshold: 2
    json_equals: {status: healthy}
  - name: talklee-api-dependencies
    url: https://api.talkleeai.com/api/v1/healthz/deep
    failure_threshold: 2
    json_equals: {ready: true}
  - name: talklee-workers
    url: https://api.talkleeai.com/api/v1/healthz/workers
    failure_threshold: 1
    json_equals: {healthy: true}
alert_destination: OWNER_TO_CONFIGURE_AND_TEST
```

## What the current routes prove

The source contract is in [operational.py](../app/api/operational.py) and
[health.py](../app/api/v1/endpoints/health.py); the latter is mounted under
`/api/v1` by [main.py](../app/main.py).

- Root `/health` is liveness. It can return HTTP 200 with
  `container: "not_initialized"`; it does not actively check PostgreSQL or Redis.
  `/api/v1/health` is also liveness and must not replace the deep check.
- `/healthz/deep` bounds each PostgreSQL `SELECT 1` and Redis `PING` at 0.5 seconds.
  An uninitialized container or failed database probe returns `ready: false`
  and HTTP 503. Redis failure/disabled Redis also causes HTTP 503 when
  `ENVIRONMENT=production`. Outside production the code permits that Redis
  fallback; the owner must verify the deployed environment is production.
  Anonymous callers receive only `ready` and `alerting`. Dependency fields
  require a valid `X-Internal-Service-Token` header and are not needed by the
  external monitor.
  The deep route does not currently incorporate the separate drain/capacity
  snapshot used by `/healthz/ready`.
- `/healthz/workers` performs bounded concurrent reads of the three registered
  Redis heartbeat keys. Missing, invalid, future-dated or stale timestamps, and
  Redis read failures, produce an unhealthy result and HTTP 503. Exactly 180
  seconds old is stale. This is role-heartbeat evidence, not proof that every
  worker instance or individual job is progressing. Reconcile operational
  coverage when the registry changes. Public callers receive only
  `healthy`; names, timestamps, ages and reasons require the internal token.
- Deep readiness exposes `alerting: "unconfigured"` when no item 16 destination
  is configured (`configured` or `unavailable` otherwise). This does not change
  readiness. Record a separate configuration warning for an unconfigured or
  unavailable state. `configured` means a destination was supplied, not that
  delivery succeeded.
- `/api/v1/health/detailed` requires the internal token, even for a loopback or
  platform-admin request. The health namespace's rate-limit exemption never
  substitutes for this authentication. Diagnostic responses use `no-store`.
- Global SIP status requires an internal service or platform administrator;
  voice-prefetch status requires an authenticated user. The unused public
  `/api/v1/webhooks/secure/verify-test` demo is removed in all environments;
  signature generation remains available to local tests through its helper.

Treat readiness 503 as a failed check even when `/health` remains 200. All four
checks being green does not establish carrier registration, provider quality,
payment processing, backup freshness or browser workflow correctness. Item 16's
trunk, resource, provider, payment, clock, certificate and backup alerts remain
separate requirements.

## Owner verification from an external machine

Run this only from an owner-controlled machine outside the application host.
It requires Bash, curl and jq. It makes only the four public GET requests,
verifies exact status/body conditions, and exits nonzero if any check fails.
The temporary body file is local to that machine. These commands have **not**
been executed as part of this remediation.

```bash
probe_dir=$(mktemp -d) || exit 1
trap 'rm -f -- "$probe_dir/body"; rmdir -- "$probe_dir"' EXIT

probe() {
  url=$1
  predicate=$2
  status=$(curl --silent --show-error --proto '=https' \
    --connect-timeout 5 --max-time 10 \
    --output "$probe_dir/body" --write-out '%{http_code}' "$url") || return 1
  if [ "$status" != 200 ]; then
    printf 'FAIL %s HTTP %s\n' "$url" "$status"
    return 1
  fi
  if [ "$predicate" = nonempty ]; then
    test -s "$probe_dir/body" || return 1
  else
    jq -e "$predicate" "$probe_dir/body" >/dev/null || return 1
  fi
  printf 'PASS %s\n' "$url"
}

failed=0
probe 'https://talkleeai.com/' nonempty || failed=1
probe 'https://api.talkleeai.com/health' \
  '.status == "healthy"' || failed=1
probe 'https://api.talkleeai.com/api/v1/healthz/deep' \
  '.ready == true' || failed=1
probe 'https://api.talkleeai.com/api/v1/healthz/workers' \
  '.healthy == true' || failed=1
exit "$failed"
```

For a failed check, retain its timestamp, status and public JSON. Authorized
operators can retrieve diagnostic details using the internal token through
their protected operations tooling; do not put that secret into external
monitor URLs, exported reports or shell history. Do not turn a 503
into success merely because the process still answers HTTP. If only the public
site fails, investigate its DNS/TLS/frontend path separately from the API. If
liveness succeeds and deep readiness fails, inspect startup/dependency state;
if deep readiness succeeds and workers fail, inspect heartbeat producers and
Redis reads. These are investigation directions, not diagnoses from status
alone.

## Activation and drill evidence still required

1. Configure the four checks outside the application host and record their
   external monitor IDs, probe location, intervals and expected predicates.
2. The owner chooses the primary on-call recipient and escalation recipient.
   QA requests phone/SMS delivery; item 16's exact destination remains unresolved.
   Confirm a test notification reaches the intended person and record receipt
   time. Monitor creation alone does not prove delivery.
3. Arrange a maintenance window and approved rollback with the operations owner.
   The QA acceptance drill is a two-minute nginx interruption: record which
   checks actually fail, notification receipt/acknowledgement, nginx restoration
   and recovery notifications. Do not assume the frontend shares nginx's
   failure domain. No stop/restart command is authorized or executed here.
4. Separately exercise each monitor's failure/recovery path using its test
   facility or an approved staging fault; a healthy curl result does not prove
   alerting. Verify worker detection plus notification stays within five
   minutes under the configured heartbeat/monitor thresholds.
5. Attach timestamped red/failure and green/recovery receipts to the QA record.
   Until the checks, recipient and drill are confirmed, mark external activation
   and QA-MON-004 **not verified**, not complete.

No vendor, subscription, scheduled GitHub workflow or additional monitoring
agent is introduced by this document.

## GitHub Actions outside watch (no new account, limited)

`.github/workflows/uptime-watch.yml` runs `scripts/uptime_watch.py` every 10 minutes from a GitHub runner, outside the application host. It checks `/health`, the website, `/healthz/deep`, `/healthz/workers` (public fields only) and the TLS certificate of both hosts (at least 14 days left); each check is retried 3 times, 20 s apart. It opens one issue labelled `uptime`, repeats a comment at most hourly (at once if the failing set changes), and closes the issue on recovery. The run fails (exit 1) only when it opens or repeats an alert.

Limits: scheduled workflows can start late or be skipped under GitHub load, so detection is minutes, not the 60-second figure above; GitHub disables schedules after 60 days without repository activity; there is no phone/SMS delivery, only GitHub issue and failed-run notifications to whoever has them enabled. It does not replace the external monitor, the alert recipient or the nginx drill above, and has not yet run on GitHub.
