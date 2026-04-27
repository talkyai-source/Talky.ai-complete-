# T2.2 + T2.3 + T2.4 — ops hardening block

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 2, T2.2 / T2.3 / T2.4_
_Tests: 27 new — total 212 new-code_
_Routes: unchanged (247)_

Three operational-hardening items shipped together because each is
small on its own and none of them change runtime call behaviour.
All three are primarily protection against operator footguns.

---

## T2.3 — Sentry backend SDK + alerting

### What was broken

OTEL was already wired (`backend/app/core/telemetry.py`) but nobody
was woken up when it fired. Errors in request handlers, the dialer
worker, and the voice pipeline went to logs — which no on-call
dashboard was tailing.

### What shipped

`backend/app/core/sentry_init.py` — opt-in Sentry initialiser gated
by `SENTRY_DSN`. No DSN = no init, no network traffic, no overhead.
Dev and CI stay silent; production just sets the env and gets
immediate error capture.

Wired at `app.main.lifespan` step 0.5 — after the prod gate, before
OTEL so both integrations see every request. Hooks:

- `FastApiIntegration` + `StarletteIntegration` — request/response
  context, transaction IDs.
- `AsyncioIntegration` — asyncio task context so errors from
  background tasks carry the right stack.
- `LoggingIntegration(level=WARNING, event_level=ERROR)` — WARNs
  become breadcrumbs; ERRORs become captured events. Matches the
  signal/noise profile of a voice pipeline.

### Design choices

- **`send_default_pii=False`.** We handle customer audio and
  transcripts. Sentry will never see request bodies / headers /
  cookies by default. An operator can layer an allow-list per-event
  later.
- **Traces default to 1%, profiles to 0%.** The voice pipeline
  generates many spans per second; 100% sampling burns a Sentry
  quota fast. `SENTRY_TRACES_SAMPLE_RATE` / `SENTRY_PROFILES_SAMPLE_RATE`
  are knobs.
- **Release tag picked up automatically.** `SENTRY_RELEASE` env
  wins; else best-effort read of `.git/HEAD` (first 12 chars). A
  missing release is not fatal.
- **`capture_exception(exc)` helper** for call sites that want to
  send an exception without importing the SDK directly. No-op when
  Sentry is not initialised.

### Tests (5 new)

`backend/tests/unit/test_sentry_init.py`:
- No DSN → no-op (returns False).
- Empty/whitespace DSN → no-op.
- DSN set → `sentry_sdk.init` called with the expected kwargs
  (environment, traces_sample_rate, `send_default_pii=False`).
- Garbage sample rates → fall back to module defaults instead of
  crashing at startup.
- `capture_exception` without `sentry_sdk` installed does not
  raise.

All assertions run against `patch.dict("sys.modules", ...)` —
no real Sentry network call.

---

## T2.4 — Redis durability probe + docs

### What was broken

The `DialerQueueService` stores every in-flight `DialerJob` in
Redis. A Redis restart without persistence wipes them silently —
campaigns lose in-flight volume and nothing logs why. There was
no check anywhere.

### What shipped

**`backend/app/core/redis_durability.py`** — `probe_redis_durability`
runs at startup (lifespan step 2.5, after container.startup).
Reads `CONFIG GET appendonly` and `CONFIG GET save` on the live
Redis; builds a `DurabilityStatus` with explicit
`is_durable()`.

- Dev / staging with both off → INFO log only.
- Production with both off → loud WARN log and the `warning` field
  populated with actionable text ("Enable AOF everysec on the
  dialer Redis…"). OTEL + Sentry both pick up the WARN.
- Status stashed on `app.state.redis_durability` for
  `/health` surfacing.

Non-fatal: we don't refuse to boot. An operator might
intentionally be running a cache-only Redis (many small deploys do
— a separate durable Redis for the dialer queue is T2.2
territory).

**`backend/docs/telephony/redis-durability.md`** — operator doc
with `redis.conf` snippets for AOF+RDB, guidance on managed Redis
providers, split cache/queue patterns, verification commands, and
the "what happens if I ignore this" section.

### Tests (8 new)

`backend/tests/unit/test_redis_durability.py`:
- None client → probed=False.
- `CONFIG GET` raises → probed=False, error captured.
- AOF on only → durable, no warning.
- RDB save rules set, AOF off → durable.
- Both off in dev → not durable, no warning (log-only).
- Both off in production → warning populated.
- `save ""` (operator-disabled snapshotting) treated as off.
- `to_dict()` round-trips correctly for the `/health` payload.

---

## T2.2 — Horizontal dialer scaling via Redis Streams

### What was broken

`DialerQueueService` uses Redis lists (`LPUSH` + `BRPOP`). Works
fine with one worker. With N workers the story gets bespoke:
- No native fan-out; each worker `BRPOP`s a shared key with no
  fairness guarantee.
- No built-in "this message is being processed by worker X" —
  manual SETs + TTLs needed for orphan detection.
- Worker death leaves jobs in the processing SET with no
  recovery primitive. Jobs just sit there until someone notices.

### What shipped

**`backend/app/domain/services/streams_queue_service.py`** — a
parallel queue service using Redis Streams + consumer groups.
Shape-compatible with the existing `DialerQueueService` (same
`enqueue_job` / `dequeue_job` / `get_queue_stats`) so swapping is
a one-line change at the service-construction site.

Key mechanisms:

- **`XADD` to `dialer:stream:priority` / `dialer:stream:normal`.**
  Priority ≥ 8 goes to the priority stream; everything else to
  normal. Worker reads priority first, falls through to normal.
- **One consumer group `"dialers"` on each stream.** Every worker
  pod joins the same group and calls
  `XREADGROUP dialers <pod-id> {stream: ">"}`. Redis hands each
  message to exactly one consumer. Adding a worker scales out
  horizontally with no code or config change.
- **`XACK` on success** so `XPENDING` reflects only in-flight work.
- **`reclaim_stale(idle_ms=…)`** runs in the watchdog / supervisor.
  Scans `XPENDING`, `XCLAIM`s entries idle longer than the
  threshold (default 5 min). A pod crash → its in-flight jobs get
  automatically re-dispatched to the next free worker.
- **Consumer name** prefers `$POD_ID`, falls back to `gethostname()`
  — matches the T1.2 global-concurrency pod identity.
- **`DialerStreamstoreResult`** carries the stream + entry_id the
  worker needs for the follow-up XACK; callers MUST ack on success.

**Retry schedule stays in the existing `dialer:scheduled` ZSET.**
Streams don't have native delayed delivery; the ZSET pattern works,
and keeping it minimises migration surface area.

### Not wired into the live worker yet

Integration is a one-line swap at
`backend/app/domain/services/campaign_service.py` (`_get_queue_service`)
plus a matching change in `dialer_worker.py`'s dequeue loop. Deferred
because:

1. A live-pipeline swap of the queue primitive needs a staging
   dry-run — same rule as T1.3 resilient providers.
2. Operators may want to run both queues side-by-side for a
   cutover period (legacy list for campaigns started before N,
   streams for new campaigns after N). The service is designed to
   be instantiated alongside the existing queue rather than
   replacing it wholesale.

Documented in the module header and this scrutiny entry so the
follow-up is mechanical.

### Tests (14 new)

`backend/tests/unit/test_streams_queue.py` — hermetic in-process
fake redis that implements XADD / XREADGROUP / XACK /
XPENDING_RANGE / XCLAIM / XLEN / ZCARD / XGROUP_CREATE. Covers:

- Parse helpers (`_extract_first_job`, `_pending_id`,
  `_pending_idle`) against bytes/string/list/dict variants.
- Enqueue: normal priority → normal stream; high priority →
  priority stream.
- Dequeue returns priority ahead of normal.
- XACK removes entry from XPENDING.
- Empty queue → `dequeue_job()` returns None (not blocking).
- Reclaim-stale transfers ownership to a new consumer when idle
  threshold exceeded.
- Reclaim-stale IGNORES fresh entries so live work isn't yanked
  from its current consumer.
- Stats report priority + normal lengths + scheduled count.
- Consumer naming: `POD_ID` wins over hostname.
- `ensure_groups` is idempotent on BUSYGROUP.

No real Redis required — CI-friendly.

---

## Cross-cutting verification

```bash
./venv/bin/python3 -m pytest \
  tests/unit/test_streams_queue.py \
  tests/unit/test_redis_durability.py \
  tests/unit/test_sentry_init.py \
  -q
# 27 passed
```

Full new-code suite (16 files):

```
212 passed in 1.36s
```

Route count: unchanged — 247 routes.

---

## File manifest

**New**
- `backend/app/core/sentry_init.py`
- `backend/app/core/redis_durability.py`
- `backend/app/domain/services/streams_queue_service.py`
- `backend/tests/unit/test_sentry_init.py`
- `backend/tests/unit/test_redis_durability.py`
- `backend/tests/unit/test_streams_queue.py`
- `backend/docs/telephony/redis-durability.md`
- `backend/docs/scrutiny/2026-04-25-t2-2-t2-3-t2-4-ops-hardening.md` (this file)

**Modified**
- `backend/app/main.py` — lifespan now calls `init_sentry()` (step
  0.5) and `probe_redis_durability()` (step 2.5)

---

## What's next

- **T2.6** — remove the `TELEPHONY_COMPANY_NAME` / `AGENT_NAMES`
  hardcodes once every live campaign has a `persona_type` in its
  `script_config`. Needs a migration check before it can ship
  safely.
- **T1.3 integration** — wire the resilient STT/TTS wrappers into
  the live pipeline after staging dry-run.
- **T2.2 integration** — swap the queue service in
  `campaign_service._get_queue_service` and `dialer_worker`
  after staging dry-run.
- **T1.4** — Twilio / Telnyx adapters (still blocked on sandbox
  credentials).
- **Migrate `os.getenv("*_API_KEY")` call sites** to
  `CredentialResolver` (T1.1 follow-up) once the origination path
  is confirmed stable on per-tenant keys.
