# T1.5 — Callee-timezone-aware business hours

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 1, T1.5_
_Tests: 13 new (total 131 across the new-code suite)_
_Routes: unchanged (238)_

---

## What was broken

`CallGuard._check_business_hours` at
`backend/app/domain/services/call_guard.py:803` compared the current
time against `tenant_limits.business_hours_start/end` in the
**tenant's** configured `business_hours_timezone`.

This is the wrong end of the call for TCPA purposes. US regulation
and most international consumer-protection law measures "reasonable
hours" at the **callee's** location, not the caller's:

- Tenant in New York (UTC-5) dials a California number at 3 PM local.
  Tenant tz says 15:00 = "inside 09–17 window" → call allowed.
  Callee in Los Angeles (UTC-8) sees it as 12:00 — fine.
- Tenant in London (UTC+0) dials the US east coast at 3 PM local.
  Tenant tz says 15:00 = "inside 09–17 window" → call allowed.
  Callee in New York sees it as 10:00 AM — fine.
- Tenant in London dials the US east coast at 8 AM local.
  Tenant tz says 08:00 = "inside 08–17 window" → call allowed.
  **Callee in New York sees it as 3 AM — TCPA violation.**

The old check would ship that third call. The new check blocks it.

---

## What shipped

A destination-timezone resolver plus a targeted change to
`_check_business_hours`.

### New module

`backend/app/domain/services/phone_timezone.py`

- `lookup_timezone_sync(e164) -> str | None` — pure-Python call into
  Google's libphonenumber geocoder. Returns an IANA tz name, or
  `None` when the number can't be parsed or the library returns
  `Etc/Unknown`.
- `resolve_timezone(e164, redis_client=None, tenant_fallback_tz="UTC") -> str` —
  async wrapper with Redis caching (1-hour TTL via
  `phone_tz:{e164}` keys). Falls back to the tenant's tz when the
  lookup misses so behaviour never silently degrades.

### New dependency

`phonenumbers==9.0.28` — Google's libphonenumber Python port. Static
data; no network calls. ~2.6 MB wheel.

### Modified file

`backend/app/domain/services/call_guard.py` — `_check_business_hours`:

- Now takes `phone_number` as a kwarg (was ignored before).
- Resolves the callee's tz via `phone_timezone.resolve_timezone(...)`,
  falling back to `tenant_limits.business_hours_timezone` when the
  lookup misses.
- `CheckResult.details` gains `tz_source` (`"callee"` or
  `"tenant_fallback"`) and `phone_number` so logs can explain why a
  call was blocked.

### Design choices

- **Callee's tz wins; tenant tz is a fallback.** Unparseable numbers
  (short codes, malformed input, numbers from country codes
  libphonenumber doesn't know about) fall back to tenant tz —
  **never silently skip the check**.
- **Redis cache keyed on the E.164.** Number → tz mapping is static
  enough that a 1-hour TTL is safe. This also keeps libphonenumber's
  parse cost off the hot path of every call.
- **Library is fail-soft.** Import failure (phonenumbers uninstalled)
  → `None` → tenant fallback. Production-deployed environments that
  haven't re-pip-installed yet still get the old behaviour.
- **Some numbers resolve to multiple tz** (Russia, Kiribati).
  `time_zones_for_number` returns a list — we take the first
  non-`Etc/Unknown` entry. This is a simplification; a future
  revision could surface "ambiguous" as a hard-fail.

---

## Verification

```bash
./venv/bin/python3 -m pytest tests/unit/test_phone_timezone.py -q
# 13 passed
```

Freeze-time integration test (see `test_phone_timezone.py`) places
a call at **16:00 UTC** — well inside a tenant's 09–17 UTC window —
but against a **+1 323** (Los Angeles) number. Local time there is
08:00. The check returns `passed=False, reason="outside_business_hours"`
and the result's `details.tz_source == "callee"`. Proves the callee's
tz wins.

Separate test uses an unparseable number against a tenant tz of
America/New_York with hours 09–17 ET; at 22:00 UTC (17:00 ET) the
check passes on the boundary and logs `tz_source="tenant_fallback"`.
Proves the fallback path preserves pre-T1.5 behaviour.

---

## Test coverage (13 tests)

`backend/tests/unit/test_phone_timezone.py`:

- Sync lookup against four regions (US, UK, Japan, Australia) —
  confirms the geocoder returns the expected continent prefix.
- Empty / `None` / unparseable input → `None`.
- Async resolver: second call hits the Redis cache (1 GET hit, no
  extra SET).
- Unparseable number → tenant fallback (and the cache does NOT
  memoise the fallback — re-try real resolution next time).
- `None` Redis → still works; the libphonenumber lookup runs.
- Empty number + no Redis → returns tenant fallback directly.
- **Callee-tz takes precedence over tenant tz** — the freeze-time
  integration test.
- **Unknown callee → tenant fallback** — preserves pre-T1.5
  behaviour end-to-end.
- Business-hours check short-circuits when `respect_business_hours`
  is disabled.

---

## File manifest

**New**
- `backend/app/domain/services/phone_timezone.py`
- `backend/tests/unit/test_phone_timezone.py`
- `backend/docs/scrutiny/2026-04-25-t1-5-callee-timezone.md` (this file)

**Modified**
- `backend/app/domain/services/call_guard.py` — `_check_business_hours`
  now resolves callee tz; `CheckResult.details` carries the tz source
  for audit.
- New runtime dep: `phonenumbers` (add to requirements once a
  requirements file is the source of truth; installed locally for
  now).

---

## What's next

- **T1.1** — per-tenant AI provider credentials. Shipped in the
  companion entry (`2026-04-25-t1-1-tenant-ai-credentials.md`).
- **T1.3** — STT/TTS reconnect + secondary-provider failover.
- **T1.4** — Twilio/Telnyx adapters.

Tracked in the plan file.
