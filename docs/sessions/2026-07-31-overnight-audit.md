# Overnight audit — 2026-07-30 → 2026-07-31

Six parallel workers (opening · latency · security · knowledge+prompts · dialer ·
data-retrieval) plus verification. **Every finding was re-checked against code or
the live database before acting** — several worker claims were wrong or
incomplete and are marked below.

Shipped as `2075faeb … ba98c505`. Gate at the time: **4,646 passed / 0 failed**.

---

## Critical — cross-tenant data leak

`security_events.py` and `audit_logs.py` resolved scope as:

```python
scoped_tenant_id = tenant_id or current_user.get("tenant_id")
```

**The client-supplied parameter won.** Any authenticated caller with the relevant
permission could read another tenant's data via `?tenant_id=<victim-uuid>`:

- `GET /admin/security-events/events` — evidence blobs, source IPs, investigation
  notes on that tenant's abuse/fraud cases
- `GET /audit-logs/stats/events-by-type` — their audit-event distribution

Production runs Postgres under a **BYPASSRLS** role, so this predicate is the
*only* tenant isolation in the request path.

`POST /admin/security-events/events` had the same defect on the **write** side —
a caller could attribute events to any tenant. **I missed that one on the first
pass; my own regression test found it.**

Both files already had the correct validate-then-403 pattern elsewhere, so this
was drift, not design.

---

## `/audit-logs/stats/failed-logins` had never worked

Two independent bugs:

1. It filtered on **`attempted_at`** — a column that does not exist. Verified
   against the live DB: `login_attempts` is `(id, email, user_id, ip_address,
   user_agent, success, failure_reason, created_at)`. Every call raised
   `UndefinedColumn` → **500**.
2. **No tenant predicate at all** — once fixed, it would have reported
   platform-wide failed-login counts and attacking IPs to every tenant.

`login_attempts` has no `tenant_id`, so the tenant is derived by joining
`user_profiles` on `user_id`. Attempts against an unknown email belong to no
tenant and are **excluded**, surfaced as `unattributed_excluded` so the gap is
visible rather than looking like "no attacks".

---

## Knowledge silently lost facts

`render_node_answer()` renders a node **source-first** — leading with the node's
own `content` rather than the enricher's `voice_answer`, which summarises only
the *top* of a node while retrieval can match a fact **anywhere** in it.

That fix was wired into `compact_tree` and the realtime bridge — but **not** into
the two paths most campaigns actually use:

```
turn_streamer._knowledge_block_for_turn   <- the DEFAULT per-turn inject path
knowledge_tool.run_knowledge_lookup       <- the tool-call path
```

Concretely, for a node reading *"Our base plan is 200 pounds a month. The tender
add-on is an extra 75 pounds per month."* with `voice_answer` covering only the
base plan, a caller asking about the add-on got a context window that **did not
contain the 75-pound fact at all**.

> ⚠️ The renderer is called **without** `max_chars` — `_trim_kb_body` owns
> truncation because it appends the ellipsis that tells the model a fact is
> incomplete. Passing `max_chars` to both pre-truncated silently and lost that
> marker. An existing test caught that regression.

**`KNOWLEDGE_PRICE_GUARD` was missing from the tool path entirely.** Its
placement is empirically load-bearing: in the 2026-07-02 offline A/B,
llama-3.3-70b invented a price in **11 of 12** probes without it adjacent to the
knowledge block, **0 of 12** with it. Tool mode targets the groq family — the
exact models it was proven against.

---

## Admin dashboards read ~zero while calls were live

Three admin surfaces filtered on:

```
status IN ('in_progress', 'ringing', 'queued', 'initiated')
```

`in_progress` is written by **nothing** in the codebase, and the list omits
`dialing`, `answered`, `in_call` — the three statuses a call spends nearly all
its live time in. Command Center active-calls, the admin Live Calls table and the
Calls health-queue all reported near-zero during real conversations.

Now sourced from `job_states.LIVE_CALL_STATUSES`.
`CONVERSATION_LIVE_CALL_STATUSES` is kept **separate** — the stuck-call reaper
deliberately excludes `initiated`, the hung-origination case it must still reap.

Also added `blocked`/`non_retryable` to `TERMINAL_STATUSES`; both are written in
production and neither appeared in the "single source of truth".

---

## Double-dial risk

The call-guard **throttle** and **queue** branches wrote `JobStatus.SKIPPED`
immediately after `schedule_retry` had placed a live copy in Redis. `SKIPPED` is
terminal and therefore **outside** `uq_dialer_jobs_one_active_per_lead` — so for
that 30–60s window the lead looked jobless, and a campaign restart could create a
**second** job. Both would dial the same person minutes apart.

Every sibling gate already wrote `RETRY_SCHEDULED`.

---

## The recording notice was destroying recordings

Measured on the first seven calls after it shipped:

```
recording_disclosure_spoken         4
recording_disclosure_interrupted    3      <- 43%
recording_suppressed_no_disclosure  3
```

A ~4-second sentence in the one moment people always speak. One interruption was
treated as final and the whole call's audio was discarded.

Now re-delivers **once** after a 1.2s settle, and **still fails closed** — if
every attempt is talked over, the audio is discarded exactly as before.

---

## The agent was hearing itself

```
User: This call may be recorded.                 <- the agent's OWN notice
Assistant: happy to continue. What's the best way I can help you today?
User: Happy to continue. What's the best topic for you today?
```

**The caller never spoke.** The self-echo guard compared against only the most
recent assistant message — which silently assumed exactly one agent utterance
precedes each caller turn. Adding the disclosure broke that: the notice ends up
two messages back.

Now compares against every agent utterance **since the caller last spoke** —
exactly the audio still capable of echoing, and self-limiting. Bounded by
conversation position rather than a fixed last-N, so a caller who legitimately
repeats the agent's wording later is not stripped.

---

## A lead was permanently un-callable

`retry_scheduled` is inside `uq_dialer_jobs_one_active_per_lead` but **excluded**
from `IN_FLIGHT_STATUSES`, so `reap_stuck_jobs` never touched it. If the Redis
schedule entry is lost, the job can never fire *and* never be cleared — the lead
is never dialled again, silently.

One production job had been wedged **21 days**.

`reap_orphaned_scheduled_jobs` clears them. Threshold is 48h and **must** stay
above the longest legitimate retry (no-answer and voicemail both schedule +24h);
two tests pin that, one deriving the bound from `_RETRY_SCHEDULES` itself. It
keys **only** off age — checking Redis would make a transient error look like
"no entry" and reap a healthy retry.

---

## Corrections to my own earlier claims

- **The C++ gateway was never bypassed.** `/stats` mixes cumulative
  `sessions_*_total` with sums over *live* sessions only, so `packets_in` reads 0
  between calls. I misread a telemetry artefact as a missing component — it cost
  an hour.
- **Recordings work.** 248 rows in `recordings_s3`, 985MB on disk. I had queried
  the legacy empty `recordings` table.
- **Caller ID was always correct.** The carrier rewrites the presented number to
  the trunk's DID, so our CDR `clid` (…300) is not what the callee sees (…301).
  The handset was the ground truth, not our logs.

---

## Data repairs applied

- `+17789249977` registered + verified for **7 tenants** that had none
- **4,283** undialable leads tagged `invalid_number_format` (all `+1` with 13–15
  digits when NANP is exactly 11). **Not truncated** — `+14053889089060` →
  `+14053889089` is a real number belonging to someone who never opted in
- **153** leads reset from wedged `calling`
- Campaign voice → Alice (female, british), matching "Sarah jones"
- `ignore_schedule` on the test campaign (the supported field, which survives UI
  saves — unlike the testing override, which a UI save silently drops)
