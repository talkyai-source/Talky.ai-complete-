# Report 12 — Two P0 Goals, the Transcript Bug I Wrote, and the Cache Hit We Were Throwing Away

**Date:** 2026-08-24
**Previous report:** `report11.md`
**Production HEAD:** `3729e6de` — **everything in this report is live**
**Scope:** goals.md §4 (Security sidebar), §8 (Tooltips), the test-call
transcript fix, the review/feedback UX rework, the MVP model selection, and the
prompt-cache defect that was voiding every hit.

**The one-line version:** two P0 goals closed, a bug I wrote yesterday found and
fixed, and a single line of prompt assembly that was costing us **99.6% of the
prompt cache on every call** — measured, not inferred.

**And the caveat that matters:** 9 of 10 tenants are still on the old models, so
the latency work is deployed and idle. See §6b.5.

---

## Table of Contents

- [§1. What was asked, and what was delivered](#1-what-was-asked-and-what-was-delivered)
- [§2. The transcript bug — two independent causes, one of them mine](#2-the-transcript-bug--two-independent-causes-one-of-them-mine)
- [§3. Review and feedback, moved to where the audio is](#3-review-and-feedback-moved-to-where-the-audio-is)
- [§4. goals.md §4 — Security as a sidebar destination](#4-goalsmd-4--security-as-a-sidebar-destination)
- [§5. goals.md §8 — one explain-this control](#5-goalsmd-8--one-explain-this-control)
- [§6a. Picking the two MVP models — how, and why those two](#6a-picking-the-two-mvp-models--how-and-why-those-two)
- [§6b. The lead's name was voiding every cache hit](#6b-the-leads-name-was-voiding-every-cache-hit)
- [§7. Test and gate status](#7-test-and-gate-status)
- [§8. What I got wrong, and what I deliberately did not do](#8-what-i-got-wrong-and-what-i-deliberately-did-not-do)
- [§9. September 10 position](#9-september-10-position)

---

## §1. What was asked, and what was delivered

The instruction was: *"pick the next three items that is more important at this
time and achieve them."*

I picked:

| # | Item | Why this one | Status |
|---|---|---|---|
| 1 | **§4 Security sidebar** (#87) | Smallest open P0; frontend-only; reuses endpoints that already exist | **Done** |
| 2 | **§8 Tooltips** (#88) | No backend dependency; the component is the only hard part | **Done** |
| 3 | **Ship everything pending** | Finished-but-undeployed work is not delivered | **Done** — live on `3729e6de` |

Then, on a second instruction, two more pieces of work that turned out to be
connected:

| # | Item | Outcome |
|---|---|---|
| 4 | **Pick the MVP model pair** (§6a) | Cerebras `gpt-oss-120b` + Groq `gpt-oss-20b`, chosen on measured latency, stability and voice-correctness |
| 5 | **Tune the prompt for them** (§6b) | Found and fixed a line that was voiding **100%** of the prompt cache on every call |

Item 3 also carried two things already written but unshipped: the test-call
transcript fix, and the review/feedback rework from the previous session.

**Result: P0 goals go from 3 of 11 to 5 of 11.**

```
[x] Generic lead-generation prompt connected to live campaign runtime
[x] Prompt version and hash visible in call logs
[ ] Expanded contact fields available to the agent
[ ] Structured interested-lead information capture
[x] Per-conversation review and feedback storage
[x] Security moved from Settings to the main sidebar        ← today
[ ] Inbound campaign MVP
[x] AI Options and AI Summary information tooltips          ← today
[ ] Billing minute top-up MVP
[ ] Client-management and multi-tenant validation
[ ] End-to-end test and controlled release
```

---

## §2. The transcript bug — two independent causes, one of them mine

### 2.1 Confirmed before investigating

Not reasoned from code. Asked the database first:

```
id      |status   |secs|has_txt|txt_len|txt_rows|created_at
3ca179cd|completed|  24|f      |      0|       0|23:07:46
ec839ac4|completed|  51|f      |      0|       0|23:06:52
fcf60e70|completed|  47|f      |      0|       0|21:57:18
```

Three test calls. 24, 51 and 47 seconds of real conversation. **Every one with
a NULL transcript and zero `transcripts` rows.** No partial success anywhere,
which is itself informative — a flaky write would have produced at least one.

### 2.2 Cause 1 — the row id did not match what the flush targets

Transcripts persist incrementally, once per turn, from `turn_ender.py`:

```python
await self._p.transcript_service.flush_to_database(
    call_id=call_id,
    target_call_id=_resolve_transcript_target_call_id(session),
)
```

`_resolve_transcript_target_call_id` returns `None` for a browser session — its
own docstring says so — and the flush falls back to `session.call_id`.

But `_record_test_call` had:

```python
call_uuid = str(uuid.uuid4())          # a brand-new, unrelated UUID
```

Two independently generated UUIDs. Every `UPDATE calls ... WHERE id = <session
id>` matched **zero rows**, once per turn, for the entire call.

**A zero-row UPDATE is a success in PostgreSQL.** Nothing raised. Nothing
logged. The transcript evaporated silently, every turn.

### 2.3 This is a defect the codebase already documents

From `call_transcript_persister.py`'s module docstring, describing outbound
calls:

> `TranscriptService.flush_to_database()` does `UPDATE calls WHERE id =
> voice_session.call_id` which matches zero rows, so the campaign's calls row
> never receives the transcript.

That module exists specifically to prevent this. **I reintroduced it from the
opposite direction** — not by using a dialer id where a session id was needed,
but by inventing a third id matching neither — in commit `0c286854`, earlier
the same day, in a file that imports nothing from that module.

The fix:

```python
call_uuid = str(getattr(voice_session, "call_id", "") or uuid.uuid4())
```

Ordering checked and correct: `_record_test_call` runs after
`create_voice_session` (so `call_id` exists) and before `start_pipeline` (so the
row exists before the first turn can flush).

### 2.4 Cause 2 — the hangup persist was never called

Fixing cause 1 alone populates `calls.transcript`. It does **not** populate the
`transcripts` table — and the read API prefers that table:

```python
# First try the transcripts table (Day 10)
transcript_response = db_client.table("transcripts").select(...)
...
# Fallback to calls table transcript fields
```

`save_call_transcript_on_hangup` writes that row, and on the phone path is
invoked from `lifecycle.py`'s teardown. The browser path calls
`orchestrator.end_session()` directly and never goes near it.

There is a second gate inside the persister:

```python
if not dialer_call_id:
    logger.info("... no dialer binding ...")
    _safe_clear(transcript_service, session_call_id)
    return
```

`_dialer_call_id` normally comes from a PBX `external_call_uuid` lookup, which
has no meaning for a browser session. So even if the function *had* been
called, it would have returned early **and cleared the buffer**.

New `_persist_test_transcript` sets the binding explicitly and calls it.

### 2.5 Ordering is load-bearing and fails silently

```python
# BEFORE teardown: end_session cancels the pipeline, and the transcript
# buffer lives on that pipeline's transcript_service.
if voice_session and test_call_id:
    await _persist_test_transcript(...)
if voice_session:
    await container.voice_orchestrator.end_session(voice_session)
```

Transposed, the persister reads a buffer whose owning pipeline was just
cancelled, hits `if not turns_json: return`, and writes nothing.

**An empty transcript is indistinguishable from "the caller said nothing."** No
error, no warning, no partial write. That is exactly the silent degradation that
survives for months, so a test asserts the call *order* rather than merely that
both happen.

### 2.6 A consequence worth knowing

`save_call_transcript_on_hangup` schedules AI summary generation once the
transcript lands. Test calls will now produce summaries, which they never have.
Not requested — a side effect of routing through the same code path phone calls
use, which is the point of routing through it.

---

## §3. Review and feedback, moved to where the audio is

Two user-reported problems, both about placement rather than function.

### 3.1 Feedback was nowhere near the audio

The judgement about a call forms *while listening to it*. Asking someone to
navigate to a detail page to record that judgement means, in practice, a list of
recordings gets reviewed by nobody.

Every recording now carries three ways to answer, directly under its player:

| Control | Cost | What it captures |
|---|---|---|
| **Thumbs up / down** | one click | better/worse only — but the one people actually give |
| **Voice note, ≤30s** | a few seconds | nuance: *"she'd already said no twice and it kept pitching"* |
| **Typed text** | slowest | the only option when you cannot talk |

All three exist because forcing one shape means people use none.

Thumbs-down writes **rating 2**, so the call lands in the "needs listening"
queue (1s and 2s), leaving 1 to mean something worse, chosen deliberately in the
full panel.

### 3.2 None of them overwrites another

`submitReview` is a PUT of the *whole* review. A naive quick-thumb would send
`tags: []`, `comment: null` and silently wipe a colleague's tagged assessment —
and the person who lost their written note would never know.

So a thumb resends the existing comment and tags untouched; saving a comment
resends the existing rating. Without that, whichever control you used last would
erase the other.

### 3.3 The 30-second cap is real on both sides

The old cap was **300 seconds**.

- `FEEDBACK_MAX_SECONDS = 30` — and `useVoiceRecorder` already auto-stopped at
  this value, so it is a genuine cutoff, not a label
- Backend `Form(..., le=30)` — was `le=300`
- The test pinning `=== 300` updated and passing

**Stated honestly in both places:** `duration_seconds` is *client-reported*. A
hostile client can under-report it. The enforceable backstop is
`FEEDBACK_MAX_BYTES`, measured on the actual upload.

### 3.4 Reviews now surface in the admin panel

The management view sat at a **top-level `/reviews`** in the main sidebar,
offered to every user — while its backend was always `require_admin_tenant`. A
non-admin clicking "Agent Reviews" got a bare 403 dressed as a broken page.

Moved to `/admin/reviews` under an admin-only group. `/reviews` redirects so
existing links survive. Rating a call invalidates the admin list and summary, so
it updates without a refresh.

The split is now: **everyone rates, next to the audio; admins read the
aggregate.**

### 3.5 One placement decision I backed out of

I first put the full feedback bar in the calls list too, then removed it. That
row is a fixed CSS grid, and an expanding panel containing an audio player and a
textarea inside an `auto` column squashes every other column. The recordings
page has the vertical room; the calls list does not, so it gets thumbs only and
the full panel stays one click away.

---

## §4. goals.md §4 — Security as a sidebar destination

### 4.1 Why it moved

Everything on this page is something a person looks for in a hurry: *is 2FA
on?*, *what is still signed in?*, *who changed that?*. Buried three levels down
— Settings → Account & Security → Security tab — none of it is findable at the
moment it matters.

### 4.2 What is on it

| Section | Source |
|---|---|
| Password change | `POST /auth/change-password` — signs out every other session |
| Passkeys | existing `PasskeyRegistration` / `PasskeyList` |
| 2FA status, turn-off, recovery-code rotation | `mfa-utils` |
| Active sessions | existing `DeviceList` → `/sessions/active` |
| API keys, audit activity, voice security | admin-only link cards |
| Data retention | read-only; set by plan, not per user |

### 4.3 Moved, not copied

The Settings tab is now a pointer. **MFA disable and recovery-code regeneration
came with the rest** — otherwise the new page would have said "regenerate them
in Settings" while Settings no longer had them. I caught that only because I
read my own copy back.

Removing the tab orphaned roughly 90 lines of state, four imports and three
handlers. Those are deleted rather than left dead; lint on the file is clean.

### 4.4 "Allowed IPs" is left unticked, deliberately

§4 lists *"Allowed IPs, if supported."* It is not supported — there is no IP
allow-list anywhere in the backend. Rather than ship an empty panel implying a
control exists, the page says so in as many words.

**A security page that overstates what it enforces is worse than one that admits
a gap.**

`Sensitive mutations appear in the audit log` is also unticked: it is a backend
claim I did not verify today, and ticking it on assumption is how a checklist
stops meaning anything.

---

## §5. goals.md §8 — one explain-this control

### 5.1 Why not just the existing Radix tooltip

A plain Radix tooltip opens on hover and focus, closes on blur. **A phone has
neither.** A hover-only tooltip on a touch screen is either permanently shut or
opens and will not go away — and §8 lists mobile tap as a first-class
requirement.

So `InfoTip` drives Radix in **controlled** mode and adds a *pinned* state:
hover/focus opens transiently, a tap pins it, tap again / Escape / outside-tap
dismisses. One component, three input methods, no duplicated content.

### 5.2 Details that matter

- **`label` is required, not optional.** Nine buttons all announced as "more
  info" is worse for a screen-reader user than no tooltips at all.
- **The trigger is a real `<button>`**, so keyboard focus, Enter and Space work
  with no extra handling.
- **`collisionPadding` + a viewport-capped width.** On a narrow screen an
  unclamped popup renders off-screen — and that is exactly where tap is the only
  path.

### 5.3 Content

**AI Options** — Tokens (what they are, that higher limits cost more and add
latency, that voice replies should stay short) and Creativity/Temperature
(lower is consistent, higher is varied but more mistakes, the recommended
0.4–0.6 lead-gen range **shown without silently changing it**).

**AI Summary** — outcome (qualified / interested / callback / unsuccessful),
sentiment, and qualification, each explaining what the term means *and* that it
is an inference.

### 5.4 The rule §8 sets that a component cannot enforce

> "Avoid hiding essential warnings only inside tooltips."

*"This was written by a model and can be wrong"* is essential. So provenance is
a **visible line** on the summary — quotes are what was said, everything else is
interpretation — and the tooltips only explain individual terms.

That rule is unenforceable by types, so it is written in the component's header
where the next person adding a tip will read it, and a test asserts the
documentation is still there.

---

## §6a. Picking the two MVP models — how, and why those two

Brief: one model from Groq, one from Cerebras, restricted for the MVP, and they
must not be the same model. Full write-up: `docs/MODEL-SELECTION.md`.

### 6a.1 Measured, not read off a datasheet

Every number below comes from **our own accounts**, against a **37–38k character
prompt** (production's real size), with the **same `reasoning_effort` production
sends**. That last clause turns out to be load-bearing — see §6a.5.

The reason for measuring at all: this project has been wrong twice on published
numbers. Gemini's documented latency ordering **inverted** under test, and two
Llama ids sat on the menu after the account had lost access to them.

A live `/v1/models` call first:

- **Groq:** 13 models, 4 of them conversational
- **Cerebras:** exactly 2 — `gemma-4-31b`, `gpt-oss-120b`

**That immediately exposed a third dead id.** Our menu offered
`cerebras/zai-glm-4.7` and **the account does not serve it**. Same defect as the
Llama entries: pick it, the first turn 404s, and failover silently runs a model
the tenant never chose.

### 6a.2 Round 1 — correctness, which did not separate them

Seven checks, each one a failure this project has actually had: brevity, one
question per reply, honest AI disclosure, no invented prices, no NATO-spelling,
exact email read-back, wrong-person pivot.

| Model | p50 TTFT | Checks |
|---|---|---|
| `groq/openai/gpt-oss-120b` | 172 ms | 10/10 |
| `cerebras/gemma-4-31b` | 274 ms | 10/10 |
| `cerebras/gpt-oss-120b` | 292 ms | 10/10 |
| `groq/openai/gpt-oss-20b` | 412 ms | 10/10 |
| `groq/qwen/qwen3.6-27b` | 617 ms | 10/10 |

**All five passed everything.** So correctness could not decide it — latency and
stability had to.

Worth recording: the June 2026 finding that GPT-OSS "stacks questions and
NATO-spells on voice" **did not reproduce**. Both GPT-OSS entries scored 10/10
including those two checks specifically. The prompt has been substantially
reworked since that judgement; on this evidence it no longer holds.

### 6a.3 Round 2 — stability, which changed the answer

p50 hides the turn that hurts. A caller does not experience the median. Twenty
sequential turns per model:

| Model | p50 | p95 | worst | stdev |
|---|---|---|---|---|
| `cerebras/gpt-oss-120b` | **269** | 566 | 701 | 118 |
| `groq/openai/gpt-oss-20b` | 416 | **449** | **465** | 142 |
| `groq/openai/gpt-oss-120b` | 360 | 462 | 464 | 137 |
| `cerebras/gemma-4-31b` | 317 | **1133** | **1671** | **368** |
| `groq/qwen/qwen3.6-27b` | 640 | 717 | 792 | 43 |

**`gemma-4-31b` is disqualified here and only here.** It beat `gpt-oss-120b` on
p50 in round 1 (317 vs 292 ms) and looked like the obvious Cerebras pick. Its
p95 is 1133 ms and its worst turn 1671 ms — roughly one turn in ten would feel
broken to a caller. **Judged on p50 alone we would have shipped it.**

### 6a.4 The decision, and the test that settled the fallback

| Role | Provider | Model |
|---|---|---|
| **Primary** | Cerebras | `gpt-oss-120b` |
| **Fallback** | Groq | `openai/gpt-oss-20b` |

**Primary:** fastest first word measured anywhere (269 ms), second-tightest
spread, cheapest and highest-limit model Cerebras offers.

**Fallback:** the *most predictable* model tested — worst turn 465 ms, only
49 ms above its own p95. A fallback fires when something has already gone wrong;
predictable beats fast.

The deciding test between the two Groq candidates was the **email read-back**,
because capturing a lead's email is the product's actual job:

| Model | Reply to a messy email address |
|---|---|
| `gpt-oss-20b` | *"Sure, I'll email the estimate to **r.oconnell42@buildwright-uk.co.uk**"* |
| `qwen3.6-27b` | *"I will have someone follow up with **that email**"* |

**qwen never repeats it back.** A mis-heard address would reach the CRM with
nobody ever knowing. That is worse than being slower.

**On "not the same model":** I first recommended `gpt-oss-120b` on *both*
providers — same weights, independent infrastructure — arguing that the real
risk is a provider incident and that a mid-call failover should not change how
the agent sounds. That was overruled, correctly. If the model itself has a
behavioural flaw, both sides carry it, and this project has a documented example
in the June GPT-OSS finding. `gpt-oss-20b` and `gpt-oss-120b` are different
models (different sizes, separately trained) but the same family — provider risk
is fully diversified, family risk is not. Recorded so the trade stays visible.

### 6a.5 A correction I had to make mid-test

My first run scored `qwen3.6-27b` at **5/10**, every reply opening with
`<think> Here's a thinking process:`. **That was my benchmark's fault, not the
model's** — I passed `reasoning_effort` only for Cerebras, while `groq.py` forces
`"none"` for the whole qwen3 family. Corrected, qwen scores 10/10.

Left in the record because a benchmark that quietly misconfigures one candidate
produces a confident, wrong recommendation — and I nearly published one.

### 6a.6 Hidden, not forbidden

Validation reads the **offered** menu, so narrowing it would have 400'd any
tenant whose stored model was removed — locking them out of their own settings
page over a menu change.

So offered and accepted are now different sets. The menu shows one model per
provider; validation accepts everything tenants already store, **including
`llama-3.1-8b-instant`** despite it 404ing, because blocking those five tenants'
saves does not repair them, it only traps them.

---

## §6b. The lead's name was voiding every cache hit

The largest performance defect found today, and it was one line.

### 6b.1 What was wrong

```python
system_prompt = call_target_block + "\n" + system_prompt
```

The callee's **name and company** were prepended to the front of a ~38,000
character prompt. Both providers cache by **exact prefix**, and Cerebras
documents the consequence plainly:

> *"Even a single character difference in the first token will result in a cache
> miss for that block **and all subsequent blocks**."*

Every lead has a different name. **The entire prompt missed cache on every
single call.**

It cost nothing while we ran qwen, which has no prompt caching at all. On the
gpt-oss pair it became the most expensive line in the system.

### 6b.2 Proven, not assumed

`backend/scripts/verify_prompt_cache.py` — same prompt, only the block's
position changing:

| Layout | call 1 | call 2 | call 3 |
|---|---|---|---|
| **Name at the END (fixed)** | **99.6%** | **99.6%** | 0.0% |
| **Name at the FRONT (before)** | 0.0% | 0.0% | 0.0% |

**4,224 of 4,239 prompt tokens served from cache, versus none.**

The 0.0% on call 3 is not noise to wave away: Cerebras documents that
data-centre routing changes and its 5-minute TTL both cause misses. Expect the
occasional cold call even inside a warm campaign.

**The prompt's wording is unchanged word-for-word.** Only the separator moved
from tail to head, because it is trailing content now. Reordering and rewording
at the same time would make any regression impossible to attribute.

### 6b.3 A measurement that corrected my own design doc

I had assumed Groq caching was straightforward. It is not:

| Prompt size | Cached |
|---|---|
| 3,555 tokens | **0%** |
| 10,899 tokens | 30.5% (a fixed 3,328 tokens) |

Groq caches **nothing** at small prompts and only a bounded block at large ones —
far weaker than Cerebras' 99.6%. The documented "128–1024 token minimum" is not
the operative threshold in practice.

**Our prompt is ~9,500 tokens, sitting right on that line.** A future
prompt-size reduction could therefore switch Groq caching *off* — recorded as a
trap, because it is exactly the kind of change that looks like a pure win.

### 6b.4 Three near-misses

**Groq rejects `prompt_cache_key` outright** — `HTTP 400 property
'prompt_cache_key' is unsupported`. Adding it generically would have made
**every fallback request fail**: the thing that exists to catch a primary outage
would itself have been broken. It is set in `cerebras.py` only, and a test pins
that.

**The in-app assistant was collateral damage.** Its default model was one of the
ids the voice menu stopped offering, so `normalize_model` would have "corrected"
every stored choice to a value it also rejected. Caught by the gate, not by me.
Its allow-list now spans offered + hidden, and its default is an id it offers.

**`cached_tokens` had to be asked for.** A streamed response carries no usage at
all unless `stream_options.include_usage` is set — so the only honest proof the
cache works would simply have been absent. TTFT cannot distinguish a warm cache
from a quiet afternoon.

### 6b.5 Deployed, but not yet reaching anyone

Stated plainly, because "it's live" would otherwise mislead:

| provider | model | tenants |
|---|---|---|
| groq | `llama-3.1-8b-instant` | **5** — dead, 404s |
| groq | `qwen/qwen3.6-27b` | **4** |
| cerebras | `gpt-oss-120b` | **1** |

**Nine of ten tenants are still on the old models.** The new default applies only
to tenants with no saved config. So the 99.6% cache hit and the 269 ms first word
are deployed and idle — qwen has no caching to benefit from the reorder, and the
five on the dead Llama id are not slow but *broken*, silently running the Gemini
fallback instead of what their config names.

Migrating those rows changes what live agents run, so it is a decision to take
deliberately rather than a side effect of a deploy.

---

---

## §7. Test and gate status

### 7.1 Backend

Final run, after all of today's work:

```
11 failed, 5523 passed, 16 skipped, 36 errors in 187.69s
```

**5523 passed against an 11-failure baseline that did not move.** Failures are
exactly the standing set:

- `test_call_feedback_api.py` (3), `test_webhooks_call_hmac.py` (5),
  `test_webhooks_call_idor.py` (2) — httpx/starlette TestClient mismatch (#79)
- `test_systemd_readiness.py` (1) — executable-bit artifact from a Windows
  checkout

None in code touched today.

It did not get there in one pass: the model-menu and cache changes first
produced 21 failures. Nine were tests correctly catching an intentional change
and needing re-pointing at the new contract. **One was a genuine break in
shipping code** — the in-app assistant's default was an id the narrowed voice
menu no longer allowed — and the gate is the only reason it did not ship.

### 7.2 New tests

| Test | Pins |
|---|---|
| `test_the_test_call_row_id_is_the_voice_session_id` | the row id equals `voice_session.call_id` |
| `test_transcript_is_persisted_and_bound_to_the_test_row` | `_dialer_call_id` set, persister called |
| `test_transcript_persists_before_the_session_is_torn_down` | persist **before** `end_session` |
| `info-tip.test.ts` (7) | tap support, real button, Escape + outside dismiss, viewport clamping, required label, Learn more, the documented usage rule |
| `audio-recording.test.ts` | the 30s cap matches the server's `le=30` |

### 7.3 Frontend

```
tsc --noEmit     exit 0
next lint        ✔ No ESLint warnings or errors
npm run build    ✓ Compiled successfully
                 /security  10.5 kB
                 /recordings 5.27 kB (was 3.56)
                 /admin/reviews 3.4 kB
                 /reviews   161 B (redirect)
```

### 7.4 The standing caveat, repeated

`fakeredis` is still not installed in the production venv, so
`test_dialer_redis_reliability.py` cannot be **collected** there and the gate
needs `--continue-on-collection-errors` to run at all. Every "the gate passed on
the server" statement in this project's history has quietly excluded that file.

---

## §8. What I got wrong, and what I deliberately did not do

### 8.1 The transcript bug was mine

Stated in §2 and repeated here because it is the most important entry. I wrote
`call_uuid = str(uuid.uuid4())` without checking what the transcript flush
targets, and it silently destroyed every test transcript. The information needed
to avoid it was in a module docstring in the same repository describing the
identical failure.

### 8.2 I nearly shipped a page pointing at controls I had just deleted

The `/security` page said "regenerate them in Settings" while I was removing the
Security tab from Settings in the same change. Caught on re-reading my own copy,
not by a test or a type. Fixed by moving the disable and regeneration controls
with everything else — which is what "moved, not copied" should have meant from
the start.

### 8.3 I put the feedback bar somewhere it did not fit

Added the full bar to the calls list, then removed it when the grid squashed.
Cheap to undo, but it was avoidable by looking at the row's column definition
before writing the JSX rather than after.

### 8.5 My first benchmark misconfigured a candidate and nearly buried it

`qwen3.6-27b` scored 5/10 with raw `<think>` text in every reply, because I sent
`reasoning_effort` only to Cerebras while production forces `"none"` for qwen on
Groq. Corrected, it scores 10/10. It still lost on other grounds — but it lost
on the right ones, and a report published from the first run would have
condemned it for my own error.

### 8.6 I recommended the same model on both providers

Argued that provider-level diversity was what mattered and that failover should
not change the agent's voice mid-call. Overruled, and correctly: a family-level
behavioural flaw would then sit on both sides, and this project has a documented
example of exactly that in the June GPT-OSS finding. The revised pair is two
different models.

### 8.7 Deliberately not done

- **`Sensitive mutations appear in the audit log`** — unticked. Backend claim,
  unverified today.
- **`Allowed IPs`** — unticked. Genuinely unsupported; the page says so.
- **`Popovers do not cover save buttons`** — ticked on the strength of Radix's
  collision handling and tips being anchored to labels rather than actions. This
  is the weakest tick on the page and deserves a look on a real narrow screen.
- **Reward admin UI and suspicious-activity monitoring** — still env-var
  configuration and a single log line. P1, and rewards are off by default.

---

## §9. September 10 position

**5 of 11 P0 goals complete. 17 days remain.**

| Remaining | Size | Blocker |
|---|---|---|
| §11 Expanded contact fields (#82) | M | shares schema with §7 — do them together |
| §7 Interested-lead capture (#85) | M | depends on §11 |
| §9 Billing top-up (#86) | L | needs a real payment path |
| §5 Inbound campaign (#84) | **XL** | **no backend exists** — DID routing, business hours |
| §12 200-tenant validation (#89) | L | needs the four above |
| End-to-end + controlled release (#96) | L | needs everything |

**§5 remains the schedule risk**, and it has not moved: campaign type, DID
routing and business-hours logic are all still net-new. It is the one item that
could credibly slip past the 10th, and the argument for starting it early gets
stronger each day it waits.

**One dependency worth naming again:** #89 validates 200 tenants for isolation
while the app's database role is still superuser with `BYPASSRLS` (#80). Until
that is fixed, a passing isolation test proves considerably less than it
appears to.

### The immediate decision, which is not a P0

Everything in this report is live on `3729e6de`. But **9 of 10 tenants are still
on the old models** (§6b.5), so the two biggest wins of the day — a 99.6% cache
hit and a 269 ms first word — are deployed and reaching nobody.

That is a deliberate decision, not an oversight: moving those rows changes what
live agents run, and the brief was explicitly *do not lock anyone out*. Not
locked out and not migrated are different states, and only one of them is mine
to choose.

Two ways forward:

1. **Move all nine to `cerebras/gpt-oss-120b`** — one reversible SQL update. The
   five on the dead Llama id are a straight repair; the four on qwen go from
   roughly 1255 ms to 450 ms first-word.
2. **Move the Dojo campaign only**, watch `cerebras_prompt_cache` in the journal
   on real traffic, then decide about the other eight.

**Option 2 is the better one**: it proves the cache and latency claims against
live calls before touching eight other tenants, and the logging to judge it by
shipped in the same commit. The whole thread started with Dojo's 49-of-51
`[SLOW]` turns, so it is also the campaign most likely to show the difference.
