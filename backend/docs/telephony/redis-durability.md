# Redis durability (dialer queue)

_T2.4 — operator-facing. Code-level check lives in
`backend/app/core/redis_durability.py` and runs at startup._

---

## Why this matters

The `DialerQueueService` stores every in-flight `DialerJob` in
Redis. A Redis restart without persistence wipes those jobs
**silently** — a campaign that had 500 pending calls now has 0,
and there is no error, no retry, no visible failure. Operators
notice when the daily call-volume dashboard dips.

The production gate (`enforce_production_gate`) does NOT refuse to
boot when Redis has no persistence — an operator might
intentionally be running a cache-only Redis. Instead we log loudly
and surface the state in `/health`, so you can SEE that this is
the configuration you chose.

---

## What "durable" means here

`probe_redis_durability` at startup reads two CONFIG values:

| Value | What it is | Durable when |
|-------|------------|--------------|
| `appendonly` | Append-Only File — every command fsynced | `"yes"` |
| `save` | RDB snapshot rules — periodic disk dumps | non-empty, not `""` or `'""'` |

Either one satisfies the durability check. Both on is fine; many
production Redis deployments run both (AOF for recent writes, RDB
for fast restart restore).

---

## Recommended settings for the dialer Redis

### Managed Redis (AWS ElastiCache, Redis Cloud, Upstash, Fly)

Most managed providers default to AOF+RDB. Verify in your provider's
console and ensure the plan you chose includes durability. If you
have separate "cache" and "queue" roles, the dialer queue **must**
go on the durable tier.

### Self-hosted Redis (docker-compose, bare-metal)

Stock `redis:7-alpine` defaults to RDB-only (`save 3600 1 300 100
60 10000`). Dialer queues want something more conservative:

`redis.conf`:

```conf
# T2.4 — durable config for the dialer queue Redis.
#
# AOF everysec: every write is flushed to disk at least once per
# second. Max theoretical loss window is ~1 second of writes. Good
# tradeoff for our workload where a DialerJob maps to one
# outbound call — losing 1 second is "one or two calls retry".
appendonly yes
appendfsync everysec

# Keep RDB snapshots as a belt-and-braces recovery path. Hourly is
# fine for the dialer's write volume.
save 3600 1

# Prevent Redis from refusing writes when AOF grows large.
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
```

Then `docker compose up -d redis` with a volume mount on
`/data` so the AOF and RDB files survive container restarts.

### Separating cache from queue

If you're running one Redis and want cache-style ephemerality for
non-dialer keys, use Redis ACLs to give the dialer a dedicated user
that writes to a durable DB index (e.g. `SELECT 1`) while the
cache path uses `SELECT 0` — or, cleaner, run two Redis instances
and give each the settings it needs.

---

## Verification

On the running service:

```bash
curl -s http://localhost:8000/health | jq '.redis_durability'
```

Expected shape:

```json
{
  "probed": true,
  "aof_enabled": true,
  "rdb_snapshots_enabled": true,
  "rdb_save_rules": "3600 1",
  "warning": null
}
```

In production without durability, `warning` will be a string
explaining what to fix. OTEL + Sentry both pick up the startup WARN
for alerting.

You can also probe manually:

```bash
redis-cli CONFIG GET appendonly
redis-cli CONFIG GET save
```

---

## What happens if I ignore this

In dev / staging — nothing, beyond the log warning.

In prod — on any `redis-server` restart (container crash, OOM kill,
planned deploy, host reboot), **every DialerJob that was in-flight
is lost**. There is no recovery — the campaigns just need to be
re-kicked. The dialer worker tries to pick up the next job and
sees an empty queue.

This is the kind of bug that's invisible until a big customer
notices their 5,000-call campaign dropped to 4,200. The check
lands the warning before that ever happens.
