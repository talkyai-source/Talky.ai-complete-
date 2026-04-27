# T1.2 — cluster-wide concurrency cap

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 1, T1.2_
_Tests: 13 new (total 118 across the new-code suite)_
_Routes: unchanged (238) — runtime behaviour change only_

---

## What was broken

`MAX_TELEPHONY_SESSIONS=50` was a **per-process** cap enforced by the
`_telephony_sessions` dict size at
`backend/app/api/v1/endpoints/telephony_bridge.py:64, 558`.

Two API pods × cap=50 gave a theoretical ceiling of 100 calls, but
because nothing coordinated between them:

- One pod could be saturated while another sat idle.
- A bursty minute on pod A dropped calls with HTTP 429 even though
  pod B had capacity.
- Operator dashboards saw 50/50 on the pod taking heat and had no
  way to see the cluster total.

Also: a crashed pod's calls would "leak" — its in-memory dict
vaporised and nothing recorded those seats anywhere else, so the
cluster never knew they had freed up.

---

## What shipped

A Redis-backed lease scheme that every pod shares. Per-pod cap is
kept as a secondary guard so a single pod can never exceed its own
memory budget — but the primary cap is now cluster-wide.

### New module

`backend/app/domain/services/global_concurrency.py` — a ~200-line
module with four public functions and one tri-state result type:

- `acquire_lease(redis, call_id, pod_id, cap) → LeaseResult` —
  pipelined `SADD + SCARD` against `telephony:active_call_ids`.
  Refuses when `SCARD` exceeds `cap` (and rolls back the SADD so
  the count stays correct). Writes a TTL-decorated lease key
  `telephony:lease:{call_id}` tagged with the pod id.
- `release_lease(redis, call_id)` — `SREM` + `DELETE`. Idempotent.
- `refresh_lease(redis, call_id)` — extends the TTL for a live call.
  Also re-SADDs the call_id so a Redis restart doesn't drop the
  cluster view. Watchdog calls this every 30s for every live call.
- `reconcile_orphans(redis)` — walks the active set, drops any
  call_id whose lease key has expired (crashed-pod cleanup). Returns
  the count removed.
- `current_count(redis)` — `SCARD` for the `/status` dashboard.
- `resolve_global_cap()` — env resolution: prefers
  `MAX_TELEPHONY_SESSIONS_GLOBAL`, falls back to
  `MAX_TELEPHONY_SESSIONS` (so single-pod deploys Just Work with no
  env changes), module default 50.

### Key design choices

- **Fail-safe on Redis unavailability.** `acquire_lease` returns
  `LeaseResult(acquired=True, reason="redis_unavailable_fallback")`
  when the client is `None` or throws. Degraded Redis does NOT
  halt origination — the per-pod cap is the backstop.
- **Lease TTL = 600s** (10 minutes). Longer than any realistic call;
  short enough that a crashed pod's ghosts expire before they
  accumulate. Heartbeats from the watchdog (every 30s) refresh live
  leases.
- **Set-based, not counter-based.** Redis SET operations are
  idempotent on membership — the same call_id re-`SADD`ed is a
  no-op. A counter would need CAS/lua to stay correct under retries.
- **Cap check after SADD, then SREM on overflow.** We pipeline
  `SADD + SCARD` to get the would-be post-insert size atomically,
  then roll back if we're over. This races slightly against
  concurrent acquires — if two pods bump the count from N to N+1 and
  N+2 simultaneously, both see SCARD=N+2 and the second one rolls
  back. Worst-case race is +1 over cap for ~one pipeline round-trip.
  Acceptable — we're not a banking ledger.

### Integration points in `telephony_bridge.py`

1. **`_on_new_call`** — per-pod cap check kept (memory backstop),
   global lease acquire added immediately after. On refusal,
   delegates to a shared `_reject_overcap_call()` helper that tears
   down any ringing pre-warm and hangs the PBX channel.
2. **`_reject_overcap_call`** — new small helper that used to live
   inline in `_on_new_call`. Deduplicates the teardown between the
   per-pod and global refusal paths.
3. **`_on_call_ended`** — releases the lease FIRST, even if the rest
   of the teardown fails. Eager release keeps the cluster count
   accurate.
4. **`_session_watchdog`** — refreshes leases for every live call on
   this pod, then reconciles orphans. Best-effort; failures are
   logged but never touch local state.
5. **`/status` endpoint** — payload gains `capacity.global_current`,
   `capacity.global_max`, `capacity.global_pct_used` alongside the
   existing per-pod fields. Nulls when Redis is unavailable.

### New env vars

- `MAX_TELEPHONY_SESSIONS_GLOBAL` (optional) — cluster-wide cap.
  Defaults to `MAX_TELEPHONY_SESSIONS` if unset, so single-pod
  deploys don't need the new variable.

---

## How the flow behaves now

```
┌─── pod A ────────────┐      ┌─── Redis ─────────────────┐
│ _on_new_call(c1)     │      │ SET: active_call_ids      │
│   per-pod cap ok?    │      │     {c1, c2, c3, …}       │
│   acquire c1 ───────►├─────►│ lease:c1 TTL=600, pod=A   │
│   (SCARD <= cap)     │      │ lease:c2 TTL=600, pod=A   │
│   start pipeline     │      │ lease:c3 TTL=600, pod=B   │
│                      │      │                           │
│ _session_watchdog    │      │ — periodic —              │
│   refresh c1, c2 ───►├─────►│ EXPIRE each 600s          │
│   reconcile ────────►├─────►│ drop expired leases       │
│                      │      │                           │
│ _on_call_ended(c1) ──│─────►│ SREM c1, DEL lease:c1     │
└──────────────────────┘      └───────────────────────────┘
```

A pod crash:

```
pod B crashes with c3 in-flight
  └► Redis SET still contains c3
  └► 600s later lease:c3 TTL expires
  └► next reconcile_orphans() SREMs c3 from active_call_ids
  └► slot freed for next caller
```

---

## Verification

```bash
./venv/bin/python3 -m pytest tests/unit/test_global_concurrency.py -q
# 13 passed
```

Run the full new-code suite to confirm no regressions:

```bash
./venv/bin/python3 -m pytest \
  tests/unit/test_global_concurrency.py \
  tests/unit/test_recording_policy.py \
  tests/unit/test_caller_id_verification.py \
  tests/unit/test_prod_fail_closed.py \
  tests/unit/test_prompt_composer.py \
  tests/unit/test_interruption_filter.py \
  tests/unit/test_agent_name_rotator.py \
  tests/unit/test_telephony_bridge_first_speaker.py \
  -q
# 118 passed in ~1s
```

Manual smoke against a real Redis:

```bash
redis-cli SCARD telephony:active_call_ids
# 0   (no calls)
# bridge originate a call …
redis-cli SCARD telephony:active_call_ids
# 1
redis-cli TTL telephony:lease:<call_id>
# ~600 (refreshed every 30s by the watchdog)
```

---

## Test coverage

Covered by `backend/tests/unit/test_global_concurrency.py`:

- First acquire succeeds and counter ticks to 1.
- Re-acquiring the same `call_id` is idempotent (no double-count).
- Cap refusal: N+1 call is rejected with `reason=cap_reached` and
  SCARD stays at N.
- Release returns the slot; a new call can take it.
- Release is idempotent (second release is a no-op).
- `None` Redis → `acquire` falls through to `acquired=True` so
  the per-pod cap is the backstop.
- Orphan reconcile: lease key expires → next reconcile drops the
  set member.
- `refresh` keeps a long-running call's lease alive past the
  initial TTL.
- `current_count(None)` returns None (not 0) so dashboards can
  render "unknown" rather than "0 calls".
- Env-var precedence: `_GLOBAL` wins, otherwise per-pod, then
  module default; ignores garbage + non-positive values.

Built on an in-process fake that mimics the `redis.asyncio`
pipeline/SET/SCARD/SADD/SREM/EXISTS surface. Hermetic — no real
Redis needed for CI.

---

## File manifest

**New**
- `backend/app/domain/services/global_concurrency.py`
- `backend/tests/unit/test_global_concurrency.py`
- `backend/docs/scrutiny/2026-04-25-t1-2-global-concurrency.md` (this file)

**Modified**
- `backend/app/api/v1/endpoints/telephony_bridge.py`
  - extracted `_reject_overcap_call()` helper
  - `_on_new_call` now acquires the global lease after the per-pod check
  - `_on_call_ended` releases the lease eagerly (before other teardown)
  - `_session_watchdog` refreshes live leases + reconciles orphans
  - `/status` payload exposes `global_current` / `global_max` /
    `global_pct_used` alongside the per-pod capacity block

---

## What's next

- **T1.3** — STT/TTS reconnect + secondary-provider failover.
  Complements T1.2: cluster-wide capacity protection is less useful
  if a Deepgram WebSocket drop takes a call offline anyway.
- **T1.1** — per-tenant provider credentials.
- **T1.4** — Twilio/Telnyx adapters (each is 3-4 days; needs real
  provider creds to integration-test).

Plan file `~/.claude/plans/zazzy-wibbling-stardust.md` is the source
of truth for what's open.
