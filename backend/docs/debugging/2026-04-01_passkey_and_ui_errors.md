# Debugging Log — 2026-04-01
## Passkey Login Failure + UI Runtime Errors

---

## Error 1 — Passkey Login: `body` treated as query parameter

### Symptom

Calling `POST /auth/passkeys/login/begin` or `POST /auth/passkeys/login/complete` returned:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "body"],
      "msg": "Field required",
      "input": null,
      "url": "https://errors.pydantic.dev/2.12/v/missing"
    }
  ]
}
```

The frontend was sending a correct JSON body with `Content-Type: application/json`. The backend
was returning a 422 Unprocessable Entity claiming that `body` was a missing **query** parameter,
not a missing JSON body field.

### Root Cause

Two factors combined to break FastAPI's parameter resolution:

**1. `from __future__ import annotations` (PEP 563 — lazy annotations)**

This directive at the top of `passkeys.py` changes every annotation from a live Python object
into a string. For example:

```python
# With `from __future__ import annotations`:
body: LoginBeginRequest   # stored as the string "LoginBeginRequest"

# Without it:
body: LoginBeginRequest   # stored as the actual class LoginBeginRequest
```

FastAPI uses `typing.get_type_hints(func)` to resolve annotations back to types at route
registration time. This normally works, but breaks when the function has been wrapped by a
decorator.

**2. `@limiter.limit("10/minute")` from `slowapi`**

The `slowapi` limiter wraps the endpoint function. Even though it uses `functools.wraps`, the
wrapped function's `__globals__` context is subtly different from the original. When FastAPI
calls `get_type_hints()` on the wrapper, it cannot resolve the string `"LoginBeginRequest"` to
the actual class. The annotation resolution silently fails, and FastAPI falls back to treating
`body` as a simple query parameter (since it has no other type information).

The combination — lazy string annotations + a decorator wrapping the function — is the known
failure mode. Individually, each is usually fine. Together, they prevent FastAPI from discovering
that `body` should be a JSON request body.

### Fix

Added `Body` to the FastAPI imports and explicitly annotated both affected parameters:

```python
# Before (broken)
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

async def login_begin(
    request: Request,
    body: LoginBeginRequest,          # FastAPI could not resolve this
    db_client: Client = Depends(...),
)

# After (fixed)
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

async def login_begin(
    request: Request,
    body: LoginBeginRequest = Body(default_factory=LoginBeginRequest),  # explicit
    db_client: Client = Depends(...),
)

async def login_complete(
    request: Request,
    response: Response,
    body: LoginCompleteRequest = Body(...),  # explicit, required
    db_client: Client = Depends(...),
)
```

`login_begin` uses `default_factory=LoginBeginRequest` because the body is optional — email is
`Optional[str] = None`, so sending `{}` is valid (discoverable/username-less passkey flow).

`login_complete` uses `Body(...)` (Ellipsis = required) because a `ceremony_id` and
`credential_response` are always mandatory.

### Why This Is the Best Approach

The explicit `Body()` annotation bypasses FastAPI's type-hint inference entirely. Instead of
relying on `get_type_hints()` to resolve the annotation string at runtime, FastAPI reads the
`Body()` default directly from the function signature — which is always present regardless of
decorator wrapping or lazy annotations. This is:

- **Robust** — works with any decorator stack, not just `slowapi`
- **Explicit** — the intent (JSON body, with/without a default) is immediately visible in the
  signature
- **Minimal change** — one import and two parameter defaults; no restructuring needed
- **Aligned with FastAPI docs** — the official docs recommend `Body()` explicitly when mixing
  `Body` + `Path`/`Query` params, or when using decorators that modify the function signature

Alternative approaches considered and rejected:

| Alternative | Why Rejected |
|---|---|
| Remove `from __future__ import annotations` | Would require auditing every other annotation in the file for breakage; broad risk for a narrow fix |
| Upgrade `slowapi` | The issue is fundamental to `functools.wraps` + PEP 563, not a `slowapi` bug |
| Use `Annotated[LoginBeginRequest, Body()]` | Equivalent outcome; `= Body(...)` style is more readable and equally correct |

---

## Error 2 — Frontend: `controls.set()` called before component mount

### Symptom

Runtime error from `packages-section.tsx`:

```
controls.set() should only be called after a component has mounted.
Consider calling within a useEffect hook.
    at onViewportLeave (src/components/home/packages-section.tsx:141:22)
```

### Root Cause

Framer Motion's `useAnimationControls()` hook returns an `AnimationControls` object that is
only connected to DOM elements after the component mounts. Calling `controls.set()` before that
point throws because there are no mounted components for the controls to write to.

The `onViewportLeave` callback was firing immediately in certain cases — specifically when the
packages grid is below the initial viewport on page load. Framer Motion's `IntersectionObserver`
detects that the element starts outside the viewport and fires `onViewportLeave` synchronously
during the first render cycle, before `useLayoutEffect` has run and the controls are connected.

```tsx
// Before — fires before mount
onViewportLeave={() => {
    controls.set("hidden");   // throws if controls not yet connected
}}
```

### Fix

Added a `mountedRef` that is set to `true` inside a `useEffect` (which only runs after mount).
The `onViewportLeave` callback is gated on this ref:

```tsx
const mountedRef = useRef(false);
useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
}, []);

// In JSX:
onViewportLeave={() => {
    if (mountedRef.current) controls.set("hidden");
}}
```

### Why This Is the Best Approach

- **Zero re-renders** — a `ref` does not trigger re-renders when written, unlike `useState`.
  Using `useState(false)` + `setMounted(true)` would force an extra render on every mount.
- **Safe unmount** — the cleanup function sets `mountedRef.current = false`, preventing any
  stale callback from running after the component is removed from the DOM.
- **Minimal diff** — the existing animation logic (`onViewportEnter`, variants, `controls`) is
  unchanged; only the guard is added.
- **Matches Framer Motion's recommendation** — Framer Motion's own docs say to "call within a
  useEffect" or guard with a mounted check. A ref is the idiomatic way to track mount state
  in a callback without adding render overhead.

---

## Error 3 — Frontend build: `useSearchParams()` missing Suspense boundary

### Symptom

Next.js 15 production build failed during static page generation:

```
useSearchParams() should be wrapped in a suspense boundary at page "/auth/login".
useSearchParams() should be wrapped in a suspense boundary at page "/auth/register".
```

### Root Cause

This error was hidden in previous builds because TypeScript compilation was failing first (on
the `@ts-ignore` issue in `passkeys.ts`). Once the TypeScript errors were resolved, the build
reached the static pre-rendering phase and hit this pre-existing issue.

Next.js 15 App Router requires that any component using `useSearchParams()` be wrapped in a
`<Suspense>` boundary. This is because `useSearchParams()` causes the page to opt out of
static generation, and without Suspense the prerender throws.

### Fix

Split each page into a content component (which uses `useSearchParams`) and a thin shell
component (the default export, which wraps with `<Suspense>`):

```tsx
// Before
export default function LoginPage() {
    const searchParams = useSearchParams();
    // ...
}

// After
function LoginPageContent() {
    const searchParams = useSearchParams();
    // ... identical body
}

export default function LoginPage() {
    return (
        <Suspense>
            <LoginPageContent />
        </Suspense>
    );
}
```

Applied to both `src/app/auth/login/page.tsx` and `src/app/auth/register/page.tsx`.

### Why This Is the Best Approach

- **No fallback spinner needed** — passing no `fallback` prop to `<Suspense>` renders nothing
  during the SSR boundary, which is correct here since the login/register pages are fully
  client-side anyway (`"use client"` directive present).
- **Minimal restructuring** — the content component is identical to the original. Only the
  export changes.
- **Future-proof** — Next.js 15+ enforces this pattern. The fix satisfies the framework
  requirement without disabling static generation for the whole route.
- **No `dynamic = "force-dynamic"` needed** — an alternative fix is to add
  `export const dynamic = "force-dynamic"` to the page, but that disables static optimization
  for the entire route. The Suspense approach is surgical and preserves static generation for
  everything outside the boundary.

---

## Error 4 — Frontend `passkeys.ts`: cascading type errors from removed `@ts-ignore`

### Symptom

After removing the outdated `@ts-ignore` directive (ESLint required changing it to
`@ts-expect-error`, which then failed because the type error no longer exists), four downstream
type errors became visible:

1. `Conversion of type 'Record<string, unknown>' to 'PublicKeyCredentialRequestOptions'` — needs double cast through `unknown`
2. `Type '{ id: ArrayBuffer }[]' is not assignable to 'PublicKeyCredentialDescriptor[]'` — missing `type: "public-key"` field
3. `'options.user' is of type 'unknown'` — `options` is `Record<string, unknown>`, accessing `.user.id` fails
4. `Property 'authenticatorData' does not exist on 'AuthenticatorAttestationResponse'` — deprecated direct property access, replaced by methods in newer WebAuthn spec

### Fix Summary

| Error | Fix |
|---|---|
| Overlapping cast on `options` | `options as unknown as PublicKeyCredentialRequestOptions` |
| Missing `type` on `allowCredentials` | Added `type: "public-key"` and explicit return type `PublicKeyCredentialDescriptor` in the `.map()` |
| `options.user.id` on `unknown` | Intermediate `const userOptions = options.user as Record<string, unknown>` |
| Deprecated `.authenticatorData` | Replaced with `.getAuthenticatorData()`, `.getPublicKey()`, `.getPublicKeyAlgorithm()` |
| `PasskeyCredential.device_type` union | Relaxed from `"singleDevice" \| "multiDevice"` to `string` (backend returns plain string) |

These were pre-existing type errors that had been suppressed. Fixing them improved type safety and
corrected the use of deprecated WebAuthn Level 2 properties with their Level 3 method equivalents.

---

## Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/passkeys.py` | Added `Body` import; explicit `Body()` on `login_begin` and `login_complete` |
| `Talk-Leee/src/components/home/packages-section.tsx` | Added `mountedRef` mount guard for `controls.set()` |
| `Talk-Leee/src/app/auth/login/page.tsx` | Wrapped content in `<Suspense>` |
| `Talk-Leee/src/app/auth/register/page.tsx` | Wrapped content in `<Suspense>` |
| `Talk-Leee/src/lib/passkeys.ts` | Removed stale `@ts-ignore`; fixed 5 type errors |

---

## Error 5 — Dashboard: duplicate React key `yg-0` in `LiveCallsTimeSeriesChart`

### Symptom

Console error at runtime:

```
Encountered two children with the same key, `yg-0`. Keys should be unique so that
components maintain their identity across updates.
    at LiveCallsTimeSeriesChart (src/components/ui/dashboard-charts.tsx:2019:13)
```

### Root Cause

`scale.ticks` is an array of Y-axis tick values (numbers). When the chart data is flat or
sparse — e.g., all values are `0` during an idle period — `scale.ticks` can contain duplicate
values such as `[0, 0, 0]`. The key was derived purely from the tick value:

```tsx
{scale.ticks.map((t) => {
  const y = scale.yFor(t);
  return <line key={`yg-${t}`} ... />;  // yg-0, yg-0, yg-0 — all duplicates
})}
```

React requires keys to be unique among siblings. Duplicate keys cause React to silently
drop or merge elements, leading to missing grid lines and unpredictable re-render behavior.

### Fix

Include the array index in the key so it is always unique regardless of tick values:

```tsx
// Before
{scale.ticks.map((t) => {
  const y = scale.yFor(t);
  return <line key={`yg-${t}`} ... />;
})}

// After
{scale.ticks.map((t, i) => {
  const y = scale.yFor(t);
  return <line key={`yg-${i}-${t}`} ... />;
})}
```

### Why This Is the Best Approach

- **Always unique** — combining index `i` with value `t` guarantees uniqueness even when all
  tick values are identical (e.g., all zeros on an empty chart).
- **Stable during stable data** — when tick values are distinct (normal operation), the key
  still encodes the value, which helps React detect value changes and reorder lines correctly.
- **Zero logic change** — the fix is a one-character addition to the map callback signature
  (`t` → `t, i`) and a key string interpolation update. No rendering logic is touched.
- **Index-only keys are an anti-pattern** in lists that can reorder, but Y-axis grid lines
  are always rendered in ascending order from the ticks array — they never reorder — so
  `key={`yg-${i}`}` would also be acceptable here. The combined form `yg-${i}-${t}` is
  slightly more descriptive and consistent with the rest of the chart's key conventions.

### Files Changed

| File | Change |
|---|---|
| `Talk-Leee/src/components/ui/dashboard-charts.tsx:2020` | `key={\`yg-${t}\`}` → `key={\`yg-${i}-${t}\`}`; added `i` to `.map()` callback |

---

## Error 6 — Dashboard: duplicate React key `yl-1` in `LiveCallsTimeSeriesChart` Y-axis labels

### Symptom

```
Encountered two children with the same key, `yl-1`. Keys should be unique so that
components maintain their identity across updates.
    at LiveCallsTimeSeriesChart (src/components/ui/dashboard-charts.tsx:2099:11)
```

### Root Cause

Identical to Error 5 — same `scale.ticks` array, same duplicate-value problem — but affecting
the Y-axis label `<text>` elements rendered just below the grid lines:

```tsx
{scale.ticks.map((t) => (
  <text key={`yl-${t}`} ...>   // yl-0, yl-0, yl-0 when ticks are all 0
    {t}
  </text>
))}
```

### Fix

```tsx
// Before
{scale.ticks.map((t) => (
  <text key={`yl-${t}`} ...>

// After
{scale.ticks.map((t, i) => (
  <text key={`yl-${i}-${t}`} ...>
```

### Files Changed

| File | Change |
|---|---|
| `Talk-Leee/src/components/ui/dashboard-charts.tsx:2100` | `key={\`yl-${t}\`}` → `key={\`yl-${i}-${t}\`}`; added `i` to `.map()` callback |

---

## Error 7 — Campaign calls drop immediately after being answered

### Symptom

Starting a campaign and receiving a call resulted in an immediate hangup — the call
connected but dropped within one second with no audio. The identical call made via:

```bash
curl -X POST "http://localhost:8000/api/v1/sip/telephony/call?destination=1002&caller_id=1001&tenant_id=..."
```

worked perfectly with full voice AI every time.

### Root Cause

The dialer worker (`app/workers/dialer_worker.py`) was creating its own temporary Asterisk
ARI adapter to originate calls, then immediately disconnecting it:

```python
# dialer_worker._make_call() — BROKEN
provider = await TelephonyProviderFactory.create()   # opens new ARI WebSocket
call_id  = await provider.originate_call(...)         # Asterisk creates channel
# ...
finally:
    await pbx.disconnect()   # ← disconnects ARI WebSocket immediately
```

**ARI ownership rule**: In Asterisk ARI, every channel belongs to the ARI application
(WebSocket connection) that created it. When that WebSocket disconnects, Asterisk hangs up
all channels it owns — regardless of whether the callee has already answered.

So the sequence was:
1. Dialer opens ARI WebSocket #2, originates call → callee's phone rings
2. Dialer disconnects ARI WebSocket #2 (in `finally` block)
3. Asterisk sees owner disconnected → **hangs up the channel immediately**
4. Callee answers to dead air, then dial tone

The curl path worked because `POST /sip/telephony/call` uses the telephony bridge's
**persistent** `_adapter` (ARI WebSocket #1, open for the entire server lifetime). That
connection is never closed, so Asterisk keeps the channel alive and `_on_new_call()` fires
normally, initialising the full voice pipeline.

The voice worker's `_run_pipeline()` was a secondary failure: it received the Redis
`call_initiated` event, tried to create a pipeline with a `BrowserMediaGateway` (wrong
type — campaigns need `TelephonyMediaGateway`), never called `media_gateway.on_call_started()`,
and exited immediately when `get_audio_queue()` returned `None`. But this was moot because the
call was already dead from the ARI disconnect.

### Fix

Replaced the temporary-adapter pattern in `dialer_worker._make_call()` with an HTTP call
to the telephony bridge endpoint — the same endpoint the curl command uses:

```python
# dialer_worker._make_call() — FIXED
async with aiohttp.ClientSession() as session:
    async with session.post(
        f"{api_base}/api/v1/sip/telephony/call"
        f"?destination={job.phone_number}&caller_id={caller_id}"
        f"&tenant_id={job.tenant_id}&campaign_id={job.campaign_id}",
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        data = await resp.json()
        return data.get("call_id")
```

Now the call flow is:
1. Dialer POSTs to bridge → bridge's persistent ARI adapter originates the call
2. Asterisk triggers StasisStart → bridge's `_on_new_call()` fires
3. `media_gateway.on_call_started()` is called with the right adapter + channel ID
4. `TelephonyMediaGateway` creates input queue, wires TTS output to C++ gateway
5. Pipeline starts, voice AI runs normally

### Why This Is the Best Approach

| Option | Why rejected / chosen |
|---|---|
| **HTTP to bridge endpoint** ✅ | Bridge already owns the persistent ARI connection. One HTTP call delegates origination cleanly. No extra connections, no lifecycle management. |
| Keep adapter open (don't disconnect) | Leaks ARI connections — one per campaign job, never closed. Would exhaust Asterisk's connection limit. |
| Share `_adapter` object across processes | Processes don't share memory. Would require IPC or a singleton pattern that breaks process isolation. |
| Fix voice_worker to call `on_call_started()` | Fixes the secondary failure but not the root cause. Call still drops because ARI owner disconnected. |

### Files Changed

| File | Change |
|---|---|
| `backend/app/workers/dialer_worker.py` | Rewrote `_make_call()` to POST to `/api/v1/sip/telephony/call` via `aiohttp` instead of creating a temporary adapter |

---

## Error 8 — Campaign starts but makes no calls (dialer worker never runs + bridge never auto-connects)

### Symptom

Starting a campaign via the UI or `POST /campaigns/{id}/start` set the campaign to `running` state and enqueued jobs in Redis, but no outbound calls were ever made. Manually calling `curl -X POST localhost:8000/api/v1/sip/telephony/start` followed by `python -m app.workers.dialer_worker` in a separate terminal made calls work — confirming both components existed but were never started automatically.

### Root Cause

Two independent gaps in the startup sequence:

1. **Dialer worker is a standalone process** — `app.workers.dialer_worker` was designed to run as `python -m app.workers.dialer_worker`. FastAPI's lifespan never started it, so the Redis job queue filled up with nobody consuming it.

2. **Telephony bridge requires manual activation** — `POST /api/v1/sip/telephony/start` had to be called after each server restart to connect the persistent ARI/ESL adapter. Without it, `_adapter` in `telephony_bridge.py` was `None`, causing all `POST /sip/telephony/call` requests from the dialer worker to return 503.

### Fix Applied

Modified `backend/app/main.py` lifespan startup to:

1. Auto-connect the telephony bridge by mirroring the exact logic of `POST /sip/telephony/start` — creates adapter via `CallControlAdapterFactory`, registers all event handlers (`_on_new_call`, `_on_call_ended`, `_on_audio_received`, `_on_ws_session_start`), calls `adapter.connect()`, and sets `_tb._adapter`.

2. Start `DialerWorker.run()` as an `asyncio.create_task()` background task (named `"dialer-worker"`) so it lives in the same event loop as FastAPI.

Shutdown sequence: sets `_dialer_worker.running = False`, cancels the task, disconnects the bridge, then calls `container.shutdown()`.

Both steps are non-fatal — a warning is logged if either fails, so the server still starts even when Asterisk/FreeSWITCH is unreachable (e.g., local dev without a PBX).

**Before** (pseudo-code):
```
lifespan startup → container.startup() → yield
# _adapter = None all runtime
# DialerWorker never starts → Redis queue never consumed
```

**After**:
```
lifespan startup → container.startup()
  → CallControlAdapterFactory.create("auto")
  → adapter.register_call_event_handlers(...)
  → adapter.connect()            # _tb._adapter now live
  → asyncio.create_task(DialerWorker().run())
  → yield
lifespan shutdown → worker.running=False, task.cancel(), adapter.disconnect()
```

### Why This Approach

- **Same event loop**: `asyncio.create_task` keeps the dialer worker co-located with FastAPI's event loop — no separate process, no IPC, no port conflicts. The worker uses `aiohttp` (already async) so it fits naturally.
- **No duplicate code**: The auto-connect logic mirrors `start_telephony()` exactly rather than reimplementing connection logic, keeping a single source of truth.
- **Non-fatal degraded start**: Wrapping both steps in `try/except` with `logger.warning` lets the server boot in dev/CI without a PBX present, while production (with `ENVIRONMENT=production`) would surface the error via logs.

### Files Changed

| File | Change |
|---|---|
| `backend/app/main.py` | Added telephony bridge auto-connect + dialer worker background task to lifespan startup/shutdown |

---

## Error 9 — Campaign started but no calls made: `lead_cooldown_1.0h_of_2h` blocks every retry

### Symptom

Campaign shows as running, jobs appear in Redis, dialer worker processes them — but every attempt logs:
```
Cannot call now: lead_cooldown_1.0h_of_2h
Scheduled retry for job ... (attempt 11) in 300s
```
Jobs were retried every 5 minutes for 55+ minutes, never actually placing a call.

### Root Cause

Two bugs in `backend/app/workers/dialer_worker.py`:

**Bug 1 (primary):** `_update_lead_status("calling")` called `UPDATE leads SET status='calling', last_called_at=NOW()` — setting the timestamp at *origination* before the call was even answered. If a call dropped immediately (the ARI channel ownership bug fixed in Error 7), `last_called_at` was frozen at origination time. Every subsequent retry saw a freshly-set timestamp and hit the 2-hour cooldown, making the lead permanently unreachable within the campaign run window.

**Bug 2 (secondary):** The `lead_cooldown` retry path always used `delay = 300` seconds (5 minutes) regardless of how long the cooldown actually was. This caused 11+ identical "Cannot call now" attempts before enough time elapsed — pointless Redis churn.

### Fix Applied

**Fix 1** — `_update_lead_status`: Exclude `"calling"` from the `last_called_at` update. Only terminal states (`"failed"`, `"completed"`, etc.) now record the timestamp. Origination no longer poisons the per-lead cooldown window.

**Before:**
```python
if status == "pending":
    UPDATE leads SET status = $1 WHERE id = $2
else:
    UPDATE leads SET status = $1, last_called_at = NOW() WHERE id = $2
```

**After:**
```python
if status in ("pending", "calling"):
    UPDATE leads SET status = $1 WHERE id = $2   # no timestamp
else:
    UPDATE leads SET status = $1, last_called_at = NOW() WHERE id = $2
```

**Fix 2** — Retry delay for `lead_cooldown`: Compute exact remaining seconds (`(min_hours - elapsed_hours) * 3600`) instead of hardcoded 300s. Added `timezone` to datetime imports.

### Why This Approach

- `last_called_at` semantically means "last time we spoke to this lead" — that only happens at or after call answer, not at dial attempt. Setting it at origination conflated two different events.
- Computing the exact cooldown delay prevents 24+ pointless retry cycles and makes the worker's log output meaningful.

### Files Changed

| File | Change |
|---|---|
| `backend/app/workers/dialer_worker.py` | `_update_lead_status`: exclude "calling" from `last_called_at`; cooldown retry delay computed from remaining hours; added `timezone` import |

---

## Error 10 — Cooldown fix didn't unblock existing stuck leads (stale `last_called_at` in DB)

### Symptom

After Error 9 fix was applied, new jobs still logged `lead_cooldown_1.2h_of_2h` and retried every 5 minutes. Same lead (`223f9a89-...`) blocked on both old jobs (attempt 14) and brand-new jobs (attempt 1).

### Root Cause

The Error 9 fix prevented FUTURE `last_called_at` sets at origination, but the existing leads in the database already had `last_called_at` set from the old code. The `lead_cooldown` retry path (lines 183–198) calculated a delay and rescheduled but **never cleared** the stale timestamp. So the lead remained blocked indefinitely until the 2-hour window naturally expired.

### Fix Applied

1. **Added `_clear_lead_last_called(lead_id)` helper** — runs `UPDATE leads SET last_called_at = NULL WHERE id = $1`.

2. **In `lead_cooldown` retry path**: instead of computing a remaining-hours delay and waiting, the worker now:
   - Logs that it is clearing a stale origination-time timestamp
   - Calls `_clear_lead_last_called(job.lead_id)`
   - Sets `delay = 30` seconds (retry almost immediately)
   
   On the next run (30s later), `last_called_at` is NULL → cooldown check passes → call goes through.

### Why This Approach

The `last_called_at` was set at origination (a bug, now fixed for new calls). Any existing value in the DB for a lead that never answered was therefore incorrect — clearing it is the right semantic action. The 2-hour cooldown is intended to prevent re-calling a lead who already spoke with the AI; it should never fire for leads that were never answered.

### Files Changed

| File | Change |
|---|---|
| `backend/app/workers/dialer_worker.py` | Added `_clear_lead_last_called()` helper; `lead_cooldown` retry path clears the stale timestamp and retries in 30s |

---

## Error 11 — Dialer worker startup delay + scheduled set round-trip causing 60-90s dead time

### Symptom

After restarting the backend, the dialer worker showed deprecation warnings (proving the loop runs) but no jobs were processed for 60+ seconds. Even after the `last_called_at` fix, cooldown-blocked jobs took another 60s to come back through the queue.

### Root Cause

Three issues combined to create a ~120s dead window:

1. **`_last_scheduled_check = datetime.utcnow()` in `__init__`** — the first scheduled-jobs check was delayed by `SCHEDULED_CHECK_INTERVAL` (60s). All retry-scheduled jobs sat idle in the `dialer:scheduled` sorted set during this time.

2. **Cooldown path used `schedule_retry()` → sorted set** — when `lead_cooldown` cleared the timestamp and rescheduled in 30s, the job went into `dialer:scheduled`. But `process_scheduled_jobs()` only ran every 60s, so the 30s retry actually took 60-90s.

3. **`datetime.utcnow()` deprecation** — caused console noise and used naive datetimes that could mismatch with timezone-aware timestamps elsewhere.

### Fix Applied

1. **Immediate scheduled check on startup**: `_last_scheduled_check = datetime(2000, 1, 1, tzinfo=timezone.utc)` — first loop iteration runs `process_scheduled_jobs()` immediately.

2. **Scheduled check interval reduced from 60s to 10s** — scheduled retries are picked up within 10s instead of 60s.

3. **Cooldown path re-enqueues directly**: instead of `schedule_retry()` (sorted set → wait → process_scheduled → re-enqueue), now calls `queue_service.enqueue_job(job)` which pushes directly into the tenant queue. The next loop iteration (1s later) dequeues it immediately.

4. **Fixed `datetime.utcnow()` deprecation** → `datetime.now(timezone.utc)`.

### Files Changed

| File | Change |
|---|---|
| `backend/app/workers/dialer_worker.py` | Epoch init for `_last_scheduled_check`; 10s scheduled interval; direct re-enqueue for cooldown; `datetime.utcnow()` → `datetime.now(timezone.utc)` |

---

## Error 12 — Campaign restart enqueues zero jobs: all leads stuck in "failed" status

### Symptom

Stopping and restarting a campaign showed `200 OK` for `POST /start`, and the dialer worker logged "listening for jobs" — but no jobs were ever dequeued. The worker loop ran indefinitely with nothing to process.

### Root Cause

`start_campaign()` calls `_get_pending_leads(campaign_id)` which queries `leads WHERE status IN ('pending', 'calling')`. After the cooldown bug cycled all leads through many retries, the exception handler set them to `"failed"` (via `_update_lead_status(lead_id, "failed")`). When the campaign was stopped and restarted, zero leads matched `"pending"` or `"calling"`, so zero jobs were enqueued. The campaign entered "running" state with nothing to do.

### Fix Applied

Added `_reset_leads_for_restart()` to `campaign_service.py`. When `start_campaign()` finds no pending/calling leads, it now:

1. Resets all `failed`/`skipped`/`calling` leads for that campaign back to `"pending"` status
2. Clears their `last_called_at` (removes stale cooldown timestamps)
3. Re-fetches pending leads and enqueues them normally

This makes "restart campaign" actually restart it — all leads get a fresh attempt.

### Files Changed

| File | Change |
|---|---|
| `backend/app/domain/services/campaign_service.py` | Added `_reset_leads_for_restart()`; `start_campaign()` calls it when no pending leads found |

---

## Error 13 — `TypeError: can't subtract offset-naive and offset-aware datetimes` in time window delay

### Symptom

```
File "scheduling_rules.py", line 117, in get_delay_until_next_window
    delay = (next_window - datetime.now()).total_seconds()
TypeError: can't subtract offset-naive and offset-aware datetimes
```

Jobs scheduled with 7200s default delay instead of the correct delay to the next calling window.

### Root Cause

`get_delay_until_next_window()` in `scheduling_rules.py` used `datetime.now()` (naive) but `get_next_window_start()` returns timezone-aware datetimes (localized to the tenant's configured timezone via `tz.localize()`). Python forbids arithmetic between naive and aware datetimes.

Additionally, `datetime.utcnow()` was used throughout `queue_service.py` (for scheduled job timestamps) and `dialer_worker.py` (for job status updates), creating deprecation warnings and potential naive/aware mismatches.

### Fix Applied

1. **`scheduling_rules.py:117`**: `datetime.now()` → `datetime.now(timezone.utc)` — now both sides of the subtraction are tz-aware.

2. **`queue_service.py`**: Added `timezone` import; all `datetime.utcnow()` → `datetime.now(timezone.utc)`.

3. **`dialer_worker.py`**: All remaining `datetime.utcnow()` → `datetime.now(timezone.utc)` (4 occurrences in `_update_job_status` and `_publish_call_event`).

4. **Added time-window logging**: when calls are blocked by time window, logs the configured timezone, window hours, allowed days, and computed delay so the operator can see exactly why calls aren't going through.

### Files Changed

| File | Change |
|---|---|
| `backend/app/domain/services/scheduling_rules.py` | `datetime.now()` → `datetime.now(timezone.utc)` on line 117 |
| `backend/app/domain/services/queue_service.py` | Added `timezone` import; all `datetime.utcnow()` → `datetime.now(timezone.utc)` |
| `backend/app/workers/dialer_worker.py` | All remaining `datetime.utcnow()` → `datetime.now(timezone.utc)`; added time-window delay logging |

---

## Error 14 — Four compounding issues preventing any calls from being made

### Symptom

Campaign starts (200 OK), dialer worker initialized — but zero worker logs appear (no "Processing job", no "Cannot call now"). Calls never originate.

### Root Cause

Four independent issues combined:

**Issue A — Silent task crash (no supervision):**
`asyncio.create_task()` in `main.py` schedules the worker but exceptions inside `run()` are never surfaced. If `initialize()` fails (e.g., DB pool race), the task exits silently. The log says "Dialer worker started as background task" regardless — a false positive.

**Issue B — Time window blocks all calls (default timezone mismatch):**
`CallingRules` defaulted to `timezone="America/New_York"`, `time_window_start="09:00"`, `time_window_end="19:00"`, `allowed_days=[0,1,2,3,4]`. The server runs in a non-ET timezone. `is_within_time_window()` correctly calls `datetime.now(tz)` in ET — if the current ET time is outside 09:00-19:00 (which it is when the server is in UTC+5 and local time is 15:xx), ALL calls are blocked. Zero logs appear because the block happens before any "Processing job" log.

**Issue C — `_get_active_tenant_ids()` returns `None` (wrong type):**
When no rows found or on any DB error, the function returned `None` instead of `[]`. Passing `None` to `dequeue_job(tenant_ids=None)` fell through to the Redis SCAN fallback path which is slow and unreliable. Also masked DB errors silently.

**Issue D — Duplicate DB pool (two asyncpg pools from same DSN):**
`initialize()` called `init_db_pool()` unconditionally, creating a second pool even when running inside FastAPI where the container already owns one. Wastes 20 connections and risks stale pool state if the container pool is later reinitialized.

### Fix Applied

1. **`main.py`**: Added `_on_dialer_done` callback via `task.add_done_callback()` — logs any exception, cancellation, or unexpected exit from the worker task.

2. **`calling_rules.py`**: Changed defaults to `timezone="UTC"`, `time_window_start="00:00"`, `time_window_end="23:59"`, `allowed_days=[0..6]`. Calls work in any timezone out of the box. Tenants configure their own restrictions via the `calling_rules` DB column.

3. **`dialer_worker.py` `_get_active_tenant_ids()`**: Returns `[]` (empty list) instead of `None` on no rows or error. Added debug logging to show which tenants were found.

4. **`dialer_worker.py` `initialize()`**: Reuses `container.db_pool` when running inside FastAPI; falls back to `init_db_pool()` only when running as standalone process.

### Files Changed

| File | Change |
|---|---|
| `backend/app/main.py` | Added `_on_dialer_done` done-callback to surface worker task exceptions |
| `backend/app/domain/models/calling_rules.py` | Default timezone UTC, window 00:00-23:59, all days — permissive out-of-box |
| `backend/app/workers/dialer_worker.py` | `_get_active_tenant_ids()` returns `[]` not `None`; `initialize()` reuses container DB pool |

---

## Error 15 — Time window still blocking after default fix; tenant DB has hardcoded America/New_York rules

### Symptom

All jobs blocked with `Cannot call now: outside_time_window_09:00_19:00 (tz=America/New_York)` even after changing code defaults to UTC.

### Root Cause

The tenant `532b5db0-...` already had `calling_rules` stored as JSONB in the `tenants` DB table with `America/New_York`, `09:00-19:00`, `[0,1,2,3,4]`. `_get_tenant_rules()` reads from the DB first — so the code default change only affects tenants with NULL calling_rules. This tenant had explicit values. Additionally `min_hours_between_calls: 2` was still set, which would re-trigger the cooldown bug after any call.

Additionally diagnosed via Redis inspection: "Dentist" campaign (23003977) had **zero leads** — the worker was looping finding no jobs. Old campaign jobs were stuck in `dialer:scheduled` with far-future timestamps from the time-window delay calculation.

### Fix Applied

1. **Direct DB update** on tenant `532b5db0-...`: set `calling_rules` to UTC, 00:00-23:59, all 7 days, `min_hours_between_calls=0`.

2. **Redis cleanup**: moved all 3 stuck `dialer:scheduled` jobs directly into `dialer:tenant:532b5db0-...:queue`; cleared stuck `dialer:processing` entry.

### Files Changed

| Action | Detail |
|---|---|
| DB `tenants` table | `calling_rules` updated: `timezone=UTC, time_window=00:00-23:59, allowed_days=[0..6], min_hours_between_calls=0` |
| Redis | 3 stuck scheduled jobs moved to tenant queue; processing set cleared |

---

## Error 16 — 415 Unsupported Media Type on telephony bridge POST

### Symptom
```
ERROR - Telephony bridge rejected call: status=415 body={"detail":"Content-Type 'application/octet-stream' not supported"}
```

### Root Cause
`aiohttp` defaults to `Content-Type: application/octet-stream` on a POST with no body. The `APISecurityMiddleware` checks Content-Type on all POST requests and rejects anything not in its allowed set (`application/json`, `application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain`).

### Fix
Added `headers = {"Content-Type": "application/json"}` to the `aiohttp` POST in `_make_call()`.

### Files Changed
| File | Change |
|---|---|
| `backend/app/workers/dialer_worker.py` | Added `Content-Type: application/json` header to bridge POST |

---

## Error 17 — send_audio 0 chunks: C++ gateway startup timeout fires before callee answers

### Symptom
```
AsteriskAdapter: session started channel=1775130027.7 session=asterisk-1775130027.7-32002 rtp_port=32002
Voice pipeline started for 1775130027.7
Starting STT stream_transcribe...
Connected to Deepgram Flux (attempt 1)
send_audio started
[30 seconds of TurnInfo: transcript='']
AsteriskAdapter: session ended channel=1775130027.7 reason=ChannelHangupRequest
send_audio ending. Sent 0 chunks, 0 skipped, 0 invalid
```

The C++ gateway RTP log showed no entries for the new session (`asterisk-1775130027.7-32002`), while the old zombie session (`asterisk-1774961020.6-32014`) had thousands of RTP packets buffering.

### Root Cause (multi-part)

**Primary**: `_on_stasis_start` fires for outbound calls when the channel enters Stasis — which happens **before** the callee answers (during RING state). The C++ gateway session is started immediately with `startup_no_rtp_timeout_ms: 30000ms`. No RTP flows during ringing (Asterisk doesn't send media through the ExternalMedia bridge until the callee picks up). After 30 seconds the gateway kills the session, Asterisk fires `ChannelHangupRequest`, and the call drops.

**Secondary**: The voice pipeline was creating a session and connecting to Deepgram before the call was even answered, wasting STT resources and making it look like the call "connected" when it hadn't.

**Zombie session**: After a backend restart, the old C++ gateway session on port 32014 remained alive because `_on_stasis_end` cleanup was never called. The old Asterisk bridge continued sending RTP to port 32014. This was confusing but not the cause of the 0-chunk issue.

### Fix
Refactored `AsteriskAdapter` to split the outbound call flow into two phases:

**Phase 1 — Ringing** (`_on_outbound_stasis_start`, called on `StasisStart appArgs=outbound`):
- Creates the mixing bridge and adds the outbound channel
- Stores `{bridge_id, listen_port, session_id}` in `_pending_outbound[channel_id]`
- Does NOT start ExternalMedia or C++ gateway

**Phase 2 — Answered** (`_on_outbound_answered`, called on `ChannelStateChange state=Up`):
- Creates ExternalMedia channel pointing to the C++ gateway's RTP port
- Starts C++ gateway session (with `startup_no_rtp_timeout_ms: 10000` since RTP is already flowing)
- Fires `_on_new_call` callback → AI pipeline starts
- RTP arrives at the gateway within milliseconds → no timeout risk

**Unanswered call cleanup** (`_cleanup_pending_outbound`, called on `StasisEnd/ChannelDestroyed/ChannelHangupRequest` for a pending channel):
- Releases the pre-allocated RTP port
- Deletes the ARI bridge
- No gateway session to stop (was never started)

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | Added `_pending_outbound` dict; new methods `_on_outbound_stasis_start`, `_on_outbound_answered`, `_cleanup_pending_outbound`; updated `_handle_ari_event` to handle `ChannelStateChange` and split outbound/inbound paths |

---

## Error 18 — Softphone not ringing; old code still running after fix

### Symptom
After applying the Error 17 fix, backend was NOT restarted. Logs still showed the OLD message `AsteriskAdapter: new call channel=...` instead of the new `outbound call ringing` message, confirming the fix was not loaded. Additionally, the softphone at extension 1002 never rang.

### Root Cause
**Wrong ARI originate endpoint format.** The code was dialing `PJSIP/1002@lan-pbx` — the `@lan-pbx` suffix tells Asterisk to route the call through the `lan-pbx` trunk (a remote PBX). The softphone is registered **locally** to Asterisk, not on the remote PBX. Asterisk sent the SIP INVITE to the lan-pbx trunk, which either dropped it or timed out after 30 seconds, so the local softphone never rang.

**No outbound greeting.** Even once the call connects, the AI was waiting for the callee to speak first (`process_audio_stream` just listens). On an outbound campaign call the AI is the caller and must speak first.

### Fix
1. Changed `originate_call` to dial `PJSIP/{destination}` (no trunk suffix) — calls the locally registered PJSIP endpoint directly.
2. Added `_send_outbound_greeting()` in `telephony_bridge.py`: waits 1 second after pipeline start (for Deepgram to connect), then calls `synthesize_and_send_audio()` to send the AI's opening line through the media gateway. No WebSocket required — audio routes directly via TelephonyMediaGateway → C++ gateway → callee.

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | `originate_call`: reverted back to `PJSIP/{destination}@lan-pbx` (softphone is on OpenSIPS, not local Asterisk) |
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Added `_send_outbound_greeting()`; called via `asyncio.create_task` after pipeline starts |

---

## Error 19 — ARI POST /channels → 500 "Allocation failed"

### Symptom
```
ARI POST /channels → 500: {"error":"Allocation failed"}
```

### Root Cause
Error 18 fix incorrectly changed the ARI originate endpoint from `PJSIP/1002@lan-pbx` to `PJSIP/1002`. Since the softphone at extension 1002 is registered to OpenSIPS (the `lan-pbx` trunk), NOT to local Asterisk, there is no local PJSIP endpoint named "1002". Asterisk returns "Allocation failed" because it cannot find the endpoint.

### Fix
Reverted `originate_call` back to `PJSIP/{destination}@lan-pbx`. The `@lan-pbx` suffix is required to route the SIP INVITE through the OpenSIPS trunk to the registered softphone.

### Remaining issue
With `PJSIP/1002@lan-pbx`, the channel is created successfully but the softphone does not ring. This is an Asterisk/OpenSIPS routing configuration issue — the SIP INVITE reaches OpenSIPS but it is not being forwarded to the registered device. Requires checking Asterisk PJSIP endpoint configuration and OpenSIPS routing rules via:
```
asterisk -rx "pjsip show endpoints"
asterisk -rx "pjsip show contacts"
```

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | Reverted endpoint back to `PJSIP/{destination}@lan-pbx` |

---

## Error 20 — Call answered but heard silence; "unanswered outbound call cleaned up"

### Symptom
User answered the campaign call on their softphone. Backend logged `AsteriskAdapter: unanswered outbound call cleaned up` despite the call being answered. Complete silence on both sides — AI never spoke, user's speech not processed.

### Root Cause
Race condition between `StasisStart` and `ChannelStateChange(Up)` in the ARI WebSocket event queue.

Timeline:
1. `18:11:07` — call originated
2. `18:11:17` — **10-second delay** before `StasisStart` arrived (ARI WS backlog)
3. `_handle_ari_event(StasisStart)` calls `asyncio.create_task(_on_outbound_stasis_start)` — task is **scheduled but not yet run**
4. `ChannelStateChange(Up)` arrives in the same ARI WS burst (user answered during the 10s backlog)
5. `_handle_ari_event(ChannelStateChange)` checks `channel_id in self._pending_outbound` → **False** (task hasn't run yet) → event **silently dropped**
6. `_on_outbound_stasis_start` task finally runs, parks the channel, but `ChannelStateChange` is already gone
7. 37 seconds later, Asterisk hangs up → cleaned up as "unanswered"

### Fix
Added `_preemptive_up_channels: set[str]` to track `Up` events that arrive before the parking task runs.

- In `ChannelStateChange(Up)` handler: if channel not in `_pending_outbound`, add to `_preemptive_up_channels`
- In `_on_outbound_stasis_start`: after parking, check `_preemptive_up_channels`; if present, immediately fire `_on_outbound_answered`
- In hangup handler: discard channel from `_preemptive_up_channels`

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | Added `_preemptive_up_channels` set; updated `ChannelStateChange` handler and `_on_outbound_stasis_start` to handle race condition |

---

## Error 21 — Call recording save fails: asyncpg UUID has no `.replace()`

### Symptoms
After a successful 49-second outbound call with bidirectional audio, the recording save fails:
```
Saving recording for talky-out-77: 48.9s, 782400 bytes
Resolved PBX channel talky-out-77 → calls.id=9c4f8133-..., tenant=532b5db0, campaign=1b82982f
Failed to upload recording for call 9c4f8133-...: 'asyncpg.pgproto.pgproto.UUID' object has no attribute 'replace'
Recording save_and_link returned None for talky-out-77
```

### Root Cause
The DB query in `_save_call_recording()` returns `row.get("id")` as an `asyncpg.pgproto.pgproto.UUID` object (not a Python string). This UUID object was passed directly to `RecordingService.save_and_link()` → `_generate_storage_path()`, which calls `.replace("/", "_")` on it. The asyncpg UUID type does not have a `.replace()` method.

`tenant_id` and `campaign_id` were already being cast via `str()` on lines 330-331, but `internal_call_id` on line 329 was missed.

### Fix
Two-layer fix for defense in depth:

1. **`telephony_bridge.py` line 329**: Cast `row.get("id")` to `str()` at the source
2. **`recording_service.py` lines 130-132**: Wrap all ID parameters in `str()` before calling `.replace()` in `_generate_storage_path()`

### Files Changed
| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Cast `internal_call_id` to `str()` when reading from DB result |
| `backend/app/domain/services/recording_service.py` | Wrap `tenant_id`, `campaign_id`, `call_id` in `str()` in `_generate_storage_path()` |

---

## Error 22 — Recordings list endpoint: Pydantic validation errors for UUID/datetime types

### Symptoms
Frontend shows: `Failed to fetch recordings: 3 validation errors for RecordingListItem` — `id` and `call_id` are `UUID` objects instead of strings, `created_at` is a `datetime` object instead of a string.

### Root Cause
Same class of bug as Error 21. Supabase/asyncpg returns `id` and `call_id` as `asyncpg.pgproto.pgproto.UUID` objects and `created_at` as a `datetime` object. The `RecordingListItem` Pydantic model expects `str` for all three fields, and the values were passed directly without casting.

### Fix
Wrapped `recording["id"]`, `recording["call_id"]`, and `recording.get("created_at")` in `str()` when constructing `RecordingListItem` instances (line 117-122).

### Files Changed
| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/recordings.py` | Cast `id`, `call_id`, `created_at` to `str()` in list_recordings endpoint |

---

## Error 23 — "The element has no supported sources" when playing recordings

### Symptoms
Clicking play on a recording in the frontend throws: `Runtime NotSupportedError: The element has no supported sources.`

### Root Cause
The `<audio src={url}>` element makes a plain browser GET request to the streaming endpoint. The streaming endpoint requires authentication (`Depends(get_current_user)`), but the browser's `<audio>` element does not send the JWT `Authorization` header. The backend returns a 401/redirect instead of audio data, which the browser cannot decode as audio.

### Fix
1. **Frontend (`extended-api.ts`)**: Replaced `getRecordingStreamUrl()` (plain URL) with `fetchRecordingBlob()` — fetches audio via `fetch()` with auth headers, returns a `blob:` URL
2. **Frontend (`recordings/page.tsx`)**: `AudioPlayer` now fetches the blob URL on mount with `useEffect`, shows loading/error states, and cleans up the blob URL on unmount
3. **Backend (`recordings.py`)**: Added `Content-Length` header to streaming response for proper browser audio handling

### Files Changed
| File | Change |
|---|---|
| `Talk-Leee/src/lib/extended-api.ts` | Replaced `getRecordingStreamUrl` with `fetchRecordingBlob` that fetches with auth headers and returns blob URL |
| `Talk-Leee/src/app/recordings/page.tsx` | Updated `AudioPlayer` to use blob URL via `useEffect` fetch with loading/error states |
| `backend/app/api/v1/endpoints/recordings.py` | Added `Content-Length` header to stream response |

---

## Error 24 — Poor recording quality: only caller audio captured (mono, one-sided)

### Symptoms
Recordings play but sound unclear — only the caller's voice is audible, the AI agent's speech is completely missing from the recording.

### Root Cause
`TelephonyMediaGateway.on_audio_received()` appends decoded PCM to `recording_buffer`, but `send_audio()` (the TTS/agent output path) never recorded anything. The resulting WAV contained only the caller's side at 8kHz mono — no agent audio at all.

### Fix — Stereo recording (caller left, agent right)
1. **`telephony_media_gateway.py`**: Added `tts_recording_buffer` to `TelephonySession`; `send_audio()` now captures PCM16 TTS audio into this buffer; added `get_tts_recording_buffer()` method
2. **`recording_service.py`**: Added `mix_stereo_recording()` function that interleaves caller (left channel) and agent (right channel) PCM16 into a stereo WAV; added `_wav_bytes_override` support to `RecordingBuffer.get_wav_bytes()`
3. **`telephony_bridge.py`**: `_save_call_recording()` now fetches both caller and agent buffers, calls `mix_stereo_recording()`, and saves as a 2-channel WAV

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/telephony_media_gateway.py` | Added `tts_recording_buffer`, capture in `send_audio()`, `get_tts_recording_buffer()` |
| `backend/app/domain/services/recording_service.py` | Added `mix_stereo_recording()`, `_wav_bytes_override` in `get_wav_bytes()` |
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Rewrote `_save_call_recording()` to produce stereo WAV from both sides |

---

## Error 25 — Stereo recording has no time alignment (garbled audio, missing caller voice)

### Symptoms
After Error 24 fix, recordings play but are garbled — caller's voice is mostly missing, only a brief echo heard once. The agent's audio is present but at wrong positions in the timeline.

### Root Cause
The Error 24 fix captured both caller and agent audio but used **naive concatenation** for both streams, then interleaved sample-by-sample. This fails because the two streams have fundamentally different timing characteristics:

- **Caller audio**: Arrives continuously every ~20ms in real-time. Concatenating chunks produces a correct, continuous timeline of the full call.
- **Agent/TTS audio**: Arrives in **rapid bursts** during TTS synthesis. Between utterances there are no chunks at all. Concatenating gives just the spoken portions packed together — all silence gaps are lost.

When interleaved sample-by-sample, the agent's ~5 seconds of packed speech was spread across only the first ~5 seconds of a 50-second caller timeline. The rest of the caller audio had silence on the agent channel. The beginning was garbled because both streams were misaligned.

### Fix — Timeline-based sample-counter stamping
The industry-recommended approach for mixing two real-time audio streams: use a **monotonic sample counter** as the shared timeline reference.

1. **`TelephonySession`**: Added `recording_start_time` (monotonic clock), `caller_sample_count` (running count of received PCM16 samples)
2. **`on_audio_received()`**: Increments `caller_sample_count` after each chunk — this is the timeline clock
3. **`send_audio()`**: When TTS audio is captured, it's stamped with the *current* `caller_sample_count`, producing `(sample_offset, pcm_bytes)` tuples instead of raw bytes
4. **`mix_stereo_recording()`**: Rewrote with numpy. Creates two arrays (left=caller, right=agent) of `total_samples` length. Caller chunks fill left channel sequentially. Each agent chunk is placed at its stamped `sample_offset` on the right channel. Result: perfectly time-aligned stereo WAV.

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/telephony_media_gateway.py` | Added `caller_sample_count`, `recording_start_time`; TTS buffer now stores `(sample_offset, pcm_bytes)` tuples |
| `backend/app/domain/services/recording_service.py` | Rewrote `mix_stereo_recording()` with numpy timeline placement |
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Updated duration calculation for timestamped agent chunks |

---

## Error 26 — Hit-and-miss outbound calls: PJSIP trunk channel ID mismatch

### Symptoms
Campaign outbound calls are intermittent — sometimes the softphone rings, sometimes it doesn't. The dialer worker reports `CALL INITIATED via bridge (asterisk)` with HTTP 200, but no ARI events (StasisStart, ChannelStateChange) are processed, and the softphone never rings.

### Root Cause
When originating via `PJSIP/1002@lan-pbx`, ARI creates the channel with our pre-generated `channelId=talky-out-{uuid}`, but Asterisk creates a **separate trunk-leg channel** with a completely different ID (e.g. `1775146982.1`) for the PJSIP endpoint. The StasisStart event arrives with the trunk-leg channel ID, NOT the pre-generated one.

The routing logic in `_handle_ari_event` checked `channel_id in self._originated_channels` — this always failed because the trunk-leg ID was never in the set. The call only worked when Asterisk happened to pass `appArgs=["outbound"]` through the PJSIP trunk, which is **unreliable** (PJSIP trunks may deliver empty or wrong appArgs).

**The hit-and-miss pattern**: appArgs delivery through PJSIP trunks is non-deterministic. Sometimes Asterisk passes them correctly, sometimes it doesn't.

### Fix
Added a third routing condition `is_trunk_leg` that matches when:
1. `channel_id` is NOT in `_originated_channels` (not a direct match)
2. `_originated_channels` is non-empty (we have a pending origination)
3. `channel_name` starts with `"PJSIP/"` (it's a PJSIP trunk channel)

When matched, the stale pre-generated ID is consumed from `_originated_channels`. Also added a 30-second expiry timer to clean up stale entries if no StasisStart arrives at all.

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/asterisk_adapter.py` | Added `is_trunk_leg` detection in `_handle_ari_event`; added 30s expiry for stale `_originated_channels` entries |

---

## Error 27 — Agent voice garbled/missing in stereo recordings (TTS burst overlap)

### Symptoms
Caller audio (left channel) plays correctly. Agent audio (right channel) is distorted, garbled, or mostly silent — only a brief echo heard once.

### Root Cause
TTS delivers audio in **rapid bursts**: a 3-second utterance arrives as ~18 chunks within 0.5 seconds of wall-clock time. Each chunk was stamped with `caller_sample_count`, which barely changes during a burst (or is 0 during the outbound greeting before any caller audio arrives). All chunks got stamped with approximately the same offset (≈0), and the mixer overlaid them on top of each other at the same position using `+=`, producing distorted audio.

**Example from logs**: All 18 TTS chunks for the greeting arrived at `21:23:05` (same second). `caller_sample_count` was ~0 at that point. All chunks were placed at offset 0, overlapping.

### How Asterisk MixMonitor solves this
From Asterisk documentation: *"MixMonitor will insert silence into the specified files to maintain synchronization between them."* MixMonitor uses a **running write cursor** that:
- Advances by each chunk's audio duration (not wall-clock delta)
- Jumps forward to wall-clock position when a new utterance starts after silence

### Fix — Running write cursor (MixMonitor pattern)
Added `agent_rec_cursor` to `TelephonySession`:
1. Compute wall-clock position in samples: `wall_pos = (time.monotonic() - start) * 8000`
2. If `wall_pos > agent_rec_cursor`: silence gap → jump cursor forward
3. Write chunk at cursor position
4. Advance cursor by chunk's sample count → next burst chunk placed **contiguously** after this one

This ensures:
- Burst-delivered TTS chunks are placed back-to-back (no overlap)
- Silence gaps between utterances are preserved (cursor jumps forward)
- Agent audio is temporally synchronized with caller audio

### Files Changed
| File | Change |
|---|---|
| `backend/app/infrastructure/telephony/telephony_media_gateway.py` | Added `agent_rec_cursor`; rewrote TTS recording capture in `send_audio()` with running cursor logic |
| `backend/app/domain/services/recording_service.py` | Updated `mix_stereo_recording()` docstring |

---

## Error 28 — Ask AI used stale Deepgram Flux settings despite endpoint tuning

### Symptoms
Ask AI sessions still logged stale STT settings such as:
```text
Connected to Deepgram Flux (attempt 1, eager=None, eot=0.7, timeout_ms=5000)
```

This made barge-in feel sluggish even after the Ask AI endpoint had already been tuned to use faster eager end-of-turn behavior.

### Root Cause
Ask AI session configuration had drifted into two separate code paths:

1. **`ask_ai_ws.py`** built the live websocket session config
2. **`voice_orchestrator.py`** prewarmed Ask AI providers using a separate hardcoded config

The prewarm path still used:
- `stt_eager_eot_threshold=None`
- `stt_eot_timeout_ms=5000`

As a result, prewarmed Ask AI sessions did not actually reflect the newer endpoint tuning.

### Fix
Created a shared Ask AI config builder and made both the live endpoint and the prewarm path use the same source of truth.

Ask AI now consistently uses:
- Flux STT at `16000Hz`
- Deepgram TTS/browser playback at `24000Hz`
- `stt_eager_eot_threshold=0.4`
- `stt_eot_timeout_ms=3000`

This removes config drift and makes runtime logs match the intended Ask AI behavior.

### Files Changed
| File | Change |
|---|---|
| `backend/app/domain/services/ask_ai_session_config.py` | Added shared `build_ask_ai_session_config()` builder for Ask AI voice sessions |
| `backend/app/api/v1/endpoints/ask_ai_ws.py` | Switched Ask AI websocket startup to the shared config builder |
| `backend/app/domain/services/voice_orchestrator.py` | Switched Ask AI prewarm to the shared config builder instead of hardcoded Flux settings |

---

## Error 29 — Ask AI logs showed both 16kHz and 24kHz audio, making sample-rate behavior look broken

### Symptoms
As soon as Ask AI opened, logs alternated between:
```text
Audio validation passed: 1024 bytes, 21.3ms @ 24000Hz
Audio validation passed: 1024 bytes, 32.0ms @ 16000Hz
```

This looked like duplicated streams or a resampling bug, and it made Ask AI barge-in debugging confusing.

### Root Cause
This was partly expected and partly a real bug.

**Expected:**
- Flux STT is intentionally run at `16000Hz`
- Deepgram TTS and browser playback are intentionally run at `24000Hz`

So Ask AI legitimately has two audio rates in flight:
- **inbound mic/STT path** at `16000Hz`
- **outbound TTS/playback path** at `24000Hz`

**Actual bug:**
The browser mic was still captured at `16000Hz`, but the browser media gateway was using the playback sample-rate setting (`24000Hz`) for inbound buffering and validation too. One config knob was incorrectly driving both directions.

That mismatch did not create the entire dual-rate pattern, but it did:
- blur which side of the pipeline a given log entry referred to
- add avoidable buffering/validation mismatch on Ask AI mic input
- make barge-in timing harder to reason about

### Fix
Split browser gateway audio rates into **input** and **output** settings.

Ask AI now explicitly uses:
- `gateway_input_sample_rate=16000` for browser mic/STT input
- `gateway_sample_rate=24000` for TTS/browser playback output

The browser media gateway now validates and buffers inbound audio against the input rate, while leaving playback at `24000Hz`.

### Files Changed
| File | Change |
|---|---|
| `backend/app/domain/services/ask_ai_session_config.py` | Added explicit Ask AI browser input/output sample-rate split |
| `backend/app/domain/services/voice_orchestrator.py` | Passed `gateway_input_sample_rate` through media gateway creation |
| `backend/app/infrastructure/telephony/browser_media_gateway.py` | Added separate input sample-rate handling for inbound browser audio buffering and validation |

---

## Error 30 — Ask AI barge-in was detected by Flux but processed too late to stop active speech

### Symptoms
The logs clearly showed Deepgram Flux detecting speech while the assistant was still talking:
```text
18:31:03 INFO  [app.infrastructure.stt.deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
```

But the voice pipeline did not react until several seconds later:
```text
18:31:07 INFO  [app.domain.services.voice_pipeline_service] barge_in_detected
18:31:07 INFO  [app.domain.services.voice_pipeline_service] Barge-in event set for call ...
```

In practice this meant:
- the user could interrupt vocally
- Flux heard it immediately
- but the assistant kept speaking until `tts_complete` / `turn_complete`

### Root Cause
This was a multi-part Ask AI interruption bug.

**Part 1 — Split interrupt state**

Greeting playback and reply playback were not always listening to the same interruption primitive:
- the Ask AI endpoint owned a local greeting-only `barge_in_event`
- the voice pipeline set a different per-call barge-in event

So greeting interruption could be missed even when the backend knew the user had started speaking.

**Part 2 — Transcript ingestion was blocked by turn execution**

The STT consumer loop handled transcript events sequentially:
- `process_audio_stream()` awaited transcript handling inline
- `handle_turn_end()` then ran LLM + TTS + browser playback waiting inline

While one turn was still speaking, new `BargeInSignal` events from Flux sat queued behind that work. The result was exactly what the logs showed: `StartOfTurn` was emitted on time, but `barge_in_detected` was delayed until the old turn had already finished.

**Part 3 — Interrupted assistant replies were committed too early**

The full assistant reply was added to canonical conversation history before playback outcome was known. If the user interrupted mid-reply, the conversation state still contained the unspoken tail of that assistant message.

### Fix
The Ask AI interruption path was reworked in three layers.

1. **Unified barge-in state**
   - Ask AI greeting and reply playback now share one per-call interrupt event
   - `handle_barge_in()` sets that shared event for both paths

2. **Made turn execution cancellable**
   - End-of-turn LLM/TTS work now runs in a background task instead of blocking transcript ingestion
   - `handle_barge_in()` cancels the active turn task immediately
   - media output is cleared right away instead of waiting for playback completion

3. **Stopped committing interrupted replies**
   - assistant replies are only committed to canonical history after playback finishes uninterrupted
   - interrupted replies now log as `assistant_reply_not_committed`

This makes barge-in deterministic: Flux can keep streaming transcript events while the old turn is being cancelled, instead of waiting behind synchronous playback work.

### Files Changed
| File | Change |
|---|---|
| `backend/app/domain/models/session.py` | Added shared per-call Ask AI barge-in event state on the session |
| `backend/app/domain/services/voice_orchestrator.py` | Initialized shared interrupt state and made Ask AI greeting playback use it |
| `backend/app/api/v1/endpoints/ask_ai_ws.py` | Removed endpoint-owned Ask AI config/event duplication and switched to shared session config |
| `backend/app/api/v1/endpoints/ai_options_ws.py` | Updated early greeting interruption path to set the shared interrupt state |
| `backend/app/domain/services/voice_pipeline_service.py` | Added active turn task tracking/cancellation, non-blocking turn execution, immediate media clear on barge-in, and deferred assistant-history commit |
