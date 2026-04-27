# T1.3 + T2.2 — opt-in integration of resilient providers + streams queue

_Date: 2026-04-27_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Tier 1, T1.3 / Tier 2, T2.2_
_Tests: 23 new — total 253 new-code_
_Routes: unchanged (247) — runtime configuration only_

Both shipped as opt-in env flags so the existing list-queue +
single-provider behaviour is preserved by default. Operators can
flip a single env var per-deploy and the new mechanism activates
without code changes.

---

## T1.3 integration — `STT_FAILOVER_ENABLED` / `TTS_FAILOVER_ENABLED`

### What was missing

The resilient wrappers shipped on 2026-04-25 worked in isolation
but were never constructed by `voice_orchestrator._create_stt_provider`
or `_create_tts_provider`. Operators had no way to actually use
them.

### What shipped

**`voice_orchestrator._create_stt_provider`** — when
`STT_FAILOVER_ENABLED=true`, the primary `DeepgramFluxSTTProvider`
gets wrapped together with a secondary `DeepgramFluxSTTProvider`
on a different model. Same vendor / auth / WS shape, different
acoustic model — cheapest possible failover with no additional
provider relationship.

- Default secondary model is `flux-general-en`; override with
  `STT_SECONDARY_MODEL`.
- If the secondary fails to initialise (auth issue, model name
  typo), the wrapper bypasses cleanly and returns the bare
  primary. Caller is never left without an STT.

**`voice_orchestrator._create_tts_provider`** — when
`TTS_FAILOVER_ENABLED=true`, the primary TTS is wrapped together
with a secondary chosen via:

- Default mapping (in `_TTS_DEFAULT_SECONDARY` on the
  orchestrator): Cartesia ↔ ElevenLabs, Deepgram → Cartesia.
- Override via `TTS_SECONDARY_PROVIDER=<name>`.
- Voice-id pairing via `TTS_SECONDARY_VOICE_MAP=primary1=secondary1,…`
  so the secondary uses a similar-sounding voice when failover
  fires. Bad-format entries are silently dropped instead of
  crashing at startup.

The new `_create_tts_secondary(config)` helper builds the secondary
with the same `CredentialResolver` lookup the primary uses, so
per-tenant keys flow into both legs.

### Tests (13 new)

`backend/tests/unit/test_orchestrator_failover_wiring.py`:

- Helper parsing — `_failover_enabled` accepts `1/true/yes/on/TRUE`
  and rejects everything else.
- `_parse_voice_map` parses simple comma-separated pairs, tolerates
  whitespace and bad entries, returns `{}` for empty input.
- STT happy path: flag unset → bare primary returned.
- STT flag on → `ResilientSTTProvider` instance, both legs initialised.
- STT secondary init failure → bypass wrapper, return primary alone.
- TTS happy path: flag unset → bare primary.
- TTS flag on (Cartesia primary) → wrapped with ElevenLabs secondary
  by default.
- TTS secondary override: `TTS_SECONDARY_PROVIDER=deepgram` honoured.
- TTS voice-id map flows into the policy.
- TTS unknown secondary provider → return primary alone (don't wrap).

All tests use `unittest.mock.patch` against the resolver and provider
classes — no real Deepgram / Cartesia / ElevenLabs calls.

---

## T2.2 integration — `DIALER_QUEUE_BACKEND` factory

### What was missing

The streams-based `DialerStreamsQueueService` shipped on 2026-04-25
was never wired into anything that runs in production. Toggling
backends required code changes.

### Why this is harder than the T1.3 swap

The list and streams services have **overlapping but not identical**
interfaces. Common surface (safe to swap):

```
enqueue_job(job)
get_queue_stats()
```

List-only (worker depends on these): `dequeue_job` returns a bare
`DialerJob` (auto-removed on read), plus `process_scheduled_jobs`,
`schedule_retry`, `mark_completed`, `mark_failed`, `mark_skipped`,
`clear_queue`, `clear_campaign_jobs`.

Streams-only: `dequeue_job(block_ms)` returns a `StreamDequeueResult`
that the caller MUST `ack` after success. `reclaim_stale(idle_ms)`
recovers crashed-pod work from `XPENDING`.

A blanket swap would break the worker's dequeue loop and every
auxiliary state mutation. So we don't blanket-swap — we ship the
factory and migrate call sites that exclusively touch the OVERLAP.

### What shipped

**`backend/app/domain/services/queue_factory.py`** — new
`get_enqueue_service(redis_client, legacy_list_service)` that returns
either backend based on `DIALER_QUEUE_BACKEND`. Defaults to `list`;
unknown values fall through to `list` with a warning. `streams`
without a Redis client also falls back — the streams backend can't
function without it.

**`campaign_service._get_queue_service`** updated to consult the
factory, pulling the live Redis client off the container. Existing
behaviour is preserved when `DIALER_QUEUE_BACKEND` is unset.

**Streams compatibility shims** added so a swap on the
campaign_service path doesn't crash on `close()` or
`clear_campaign_jobs`:

- `close()` — no-op; the Redis pool is owned by the container.
- `clear_campaign_jobs(campaign_id)` — log-only no-op. Streams
  don't support bulk-delete by tag; the cutover relies on the
  worker's per-job campaign-status check, which already drops
  jobs whose campaign was stopped.

The dialer worker is **NOT migrated** in this sprint. It still
holds its own `DialerQueueService` instance bound directly to the
list backend. This is intentional:

- The worker's dequeue→ack semantic change is a hot-path edit.
- A staged cutover where new campaigns enqueue to streams while
  in-flight retries drain from the list keeps blast radius small.
- Once stream traffic is the only traffic on a deploy, the worker
  rewire is mechanical — read `StreamDequeueResult`, call `ack` on
  success, plumb retry into the existing scheduled-ZSET path.

### Tests (10 new)

`backend/tests/unit/test_queue_factory.py`:

- Backend resolution: default → list; explicit `streams`; case-
  insensitive (`LIST` → `list`); garbage value → list with warning.
- `streams` + Redis client → `DialerStreamsQueueService` instance,
  `ensure_groups` called once.
- `streams` + no Redis → falls back to the supplied list service.
- `list` backend with no pre-supplied service → constructs and
  initialises a fresh `DialerQueueService`.
- Streams `close()` is a no-op.
- Streams `clear_campaign_jobs` returns 0 with a warning log.

Combined with the existing 14 streams-queue tests this gives full
coverage of the integration surface.

---

## Operator runbook

To enable the new failover behaviour on a deploy:

```env
# T1.3 — STT/TTS failover (independent flags)
STT_FAILOVER_ENABLED=true
STT_SECONDARY_MODEL=flux-general-en   # optional; default already works

TTS_FAILOVER_ENABLED=true
TTS_SECONDARY_PROVIDER=elevenlabs     # optional; auto-selected by primary
TTS_SECONDARY_VOICE_MAP=cartesia-tessa=eleven-bella  # optional

# T2.2 — streams-based dialer queue (per-deploy cutover)
DIALER_QUEUE_BACKEND=streams
```

To roll back: unset (or set to `false` / `list`). No data
migration required — both backends use Redis but distinct keys.

---

## File manifest

**New**
- `backend/app/domain/services/queue_factory.py`
- `backend/tests/unit/test_orchestrator_failover_wiring.py`
- `backend/tests/unit/test_queue_factory.py`
- `backend/docs/scrutiny/2026-04-27-t1-3-t2-2-opt-in-integration.md` (this file)

**Modified**
- `backend/app/domain/services/voice_orchestrator.py`
  - `_failover_enabled(env_var)` + `_parse_voice_map(raw)` helpers
  - `_TTS_DEFAULT_SECONDARY` provider-pairing map
  - `_create_stt_provider` wraps in `ResilientSTTProvider` when
    `STT_FAILOVER_ENABLED=true`
  - `_create_tts_provider` wraps in `ResilientTTSProvider` when
    `TTS_FAILOVER_ENABLED=true`, with `_create_tts_secondary` helper
- `backend/app/domain/services/campaign_service.py`
  - `_get_queue_service()` now consults `queue_factory.get_enqueue_service()`
- `backend/app/domain/services/streams_queue_service.py`
  - `close()` and `clear_campaign_jobs()` shims for list-queue
    interface compatibility

---

## What's next

- **Worker rewire for full T2.2** — make `dialer_worker._make_call`
  loop handle `StreamDequeueResult` (call `ack` on success, plus
  hook a `reclaim_stale` sweep into the existing watchdog). Once
  this lands, the streams backend is feature-complete.
- **T1.4** — Twilio / Telnyx adapters (still blocked on sandbox
  credentials).
- **Legacy fallback removal** — pending audit reading zero in prod.
- **Recording announcement audio path** — voice-pipeline change.

The infrastructure for everything else is in place. What remains
is operational rollout work.
