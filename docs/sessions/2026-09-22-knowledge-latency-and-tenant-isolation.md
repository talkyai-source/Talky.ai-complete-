# 2026-09-22 — "Knowledge retrieval is not working", and what it actually was

Owner report: knowledge retrieval was not working on a new AllStateEstimation
campaign, then that the agent was too slow and barely used the knowledge. This
records what the evidence said, including three places where my own first answer
was wrong.

Production went from `78420519` to **`edee6fe8`**, alembic `0046` → **`0047`**.

## The headline: it was never a code fault

Knowledge belongs to **one campaign** and is never shared. The inbound campaign
page says so on screen. The uploaded document was on an outbound draft called
"Estimation new"; the calls being tested reached different campaigns entirely.

| Address | Reaches campaign | `knowledge_mode` | Nodes |
|---|---|---|---|
| ext 940004 | "Estimation" (inbound, running) | `none` | 0 |
| +442046132300 | "Inbound test" (inbound, running) | `none` | 0 |
| — | "Estimation new" (outbound, **draft**) | `inline` | 27 |

Nothing was broken. The document sat on a campaign no call reaches, and the
campaign holding it was a draft, so it never dialled either.

**Process failure worth recording:** I had read-only database access from the
first message and did not use it until asked a third time. Two rounds went into
reading code and shipping fixes for problems that were real but were not this
one. One query would have shown an empty knowledge folder in about a minute.

## Corrections to my own reported findings

1. **"Deepgram Flux is failing on one call in four."** Wrong, and the inverse of
   the truth. The *watchdog* was wrong, not the provider. See below.
2. **"The opener asking `is that ?` is a bug in the lead-name block."** Wrong.
   That block is correctly guarded and returns nothing without a lead. The slot
   was in the operator's own campaign script, verbatim:
   `"Hi there — is that [name]?`.
3. **"The model budget is your root cause."** Wrong. The document is 1,999
   tokens and was inlined either way. The budget work is real and shipped, and
   it changed nothing for this report.

## Why responses were late

Measured from one 66-second browser test, call `3736e734`.

### The silent-stream watchdog counted room noise as speech

`resilient_stt` counts caller audio above an RMS gate that arrived without a
transcript and fails the provider over at six seconds. **The counter only ever
added.** A transcript reset it and nothing else, so it never measured "six
seconds of someone talking into a dead stream" — it measured the total
quiet-line energy a call had **ever** contained, gathered across minutes. On any
line with constant hum, tripping is a question of when.

It fired on **4 of 17 sessions** in the week, tearing down a healthy provider
each time and costing the caller the opening seconds of their call.

Compounding it, the replay buffer was **500 ms against a 6 s window**, so a
failover discarded about 5.5 seconds of the very speech that proved the stream
was dead, and the caller had to start again.

**Deliberately not changed: the RMS gate.** Raising it is the obvious response
and the wrong one. On this session the caller really was saying "hello" into a
stream that answered nothing, and a higher gate delays a genuine rescue for any
softly-spoken caller. It is now an env knob at its original value with that
reasoning beside it.

### A ready answer was held for 2.5 seconds

The pre-TTS hold waits for the caller to stop. Its acoustic release needs a
quiet run, and in a noisy room there is never a quiet run, so the hold always
ran to its cap. A reply the model produced in **393 ms** was sat on for
**2.51 s**; the caller heard a three-second gap.

### Also found: the fallback's watchdog was echo-blind

`secondary_watchdog.observe_audio` was never passed `agent_speaking`, so it
counted our own TTS as caller speech. That is the 2026-08-18 false positive,
re-introduced on the fallback path, in the one signal used to judge whether a
provider is genuinely deaf.

| Setting | Before | After |
|---|---|---|
| STT replay buffer | 500 ms | 8,000 ms |
| Watchdog counter | cumulative, never drained | drains 1:1 on quiet |
| Watchdog RMS gate | 500 | 500, unchanged, now a knob |
| Pre-TTS hold cap | 2.5 s | 0.8 s |
| Secondary watchdog | echo-blind | echo-aware |

Every value is env-tunable so a noisy deployment needs no deploy.

## The prompt was 51,583 characters per turn

Measured, not estimated, on the test campaign.

| Layer | Before | After |
|---|---|---|
| Persona base | 28,594 | 14,429 |
| Campaign guidance | 10,420 | 971 |
| Inlined knowledge | 11,904 | 11,906 |
| **Total per turn** | **51,583** (12,672 tok) | **27,306** (6,826 tok) |

Two causes. The `lead_gen` persona body is **13,944** characters against
`customer_support`'s **6,340**, and the campaign was running the cold-call
persona, so it spent its turns qualifying rather than answering. And the
guidance restated the same facts the knowledge base now carries. The original
guidance is preserved in `script_config.additional_instructions_before_2026_09_22`.

**47% smaller, and the campaign now answers questions instead of opening a
cold call.**

## Knowledge enrichment had been dead for a month

The enricher was hardcoded to `llama-3.1-8b-instant`. That id began returning
404 around 2026-08-17 and the voice path moved off it the same day; this was
missed. Enrichment is fail-soft, so every upload since logged a warning per
batch and published its nodes bare: no summary, no spoken answer, no keywords,
no example questions. Those feed the fuzzy matching that rescues a caller's
odd phrasing.

Three further problems surfaced while fixing it, each found by running it:

* the replacement model returns structurally invalid JSON at 25 sections per
  request — batch is now 8;
* the output budget was a flat 2,048 tokens regardless of batch size, so a full
  batch was truncated mid-array — it now scales;
* a failed batch discarded every node in it — it now retries one at a time.

That took one document from 1 enriched section to 20. The remaining 7 are the
ones with no body at all, six parent headings and a source line, which are now
skipped rather than sent and reported as failures.

**Any campaign ingested between mid-August and today carries stripped nodes and
needs re-uploading to recover those fields.**

## Tenant isolation

`verify_rls.py --posture` against production:

| Check | Result |
|---|---|
| App role can bypass row security | **No** — `superuser=false bypassrls=false` |
| Tenant-scoped tables with RLS | **82 of 82** (was 81) |
| Tables with FORCE | **82 of 82** |

The one gap was `tenant_ai_configs_backup_20260907`, left over from the
September model migration: three rows covering three different tenants, no
foreign keys, read by nothing, and the only tenant-scoped table with no policy
at all. **Migration 0047** puts it behind the canonical policy, byte for byte,
so it does not register as policy sprawl.

**Four remaining verifier failures are false positives.** The policies it flags
for a missing `WITH CHECK`, and those that ignore `app.bypass_rls`, all read
`USING (false)` — billing ledger, audit log, deletion request keys, inbound
rejections. No row can be selected for update at all, so the missing clause is
unreachable. Those tables are hardened *further* than the heuristic expects.
Checked before concluding; left alone.

## The `[name]` slot

An author writing a script naturally reaches for a placeholder. Nothing
substitutes it, and the model obediently reads the line with the slot empty.
`find_unfilled_slots` now names them in a warning at composition
(`telephony_prompt_unfilled_slot`). It does **not** refuse the call and does
**not** rewrite operator copy. Detection is narrow: it catches a field-shaped
slot and leaves a stage direction like `[repeat email slowly]` alone.

Campaign `a3788fa7` on the `.co` tenant still contains one.

## Numbers and the campaign

* `+442046132300` added to the AllStateEstimation account and verified. The
  Canadian number was already there since June — it is a shared Blaze caller ID
  held by ten accounts, so there was nothing to move.
* Neither could be **attached** to a campaign there. The UK number is held by
  the owner's own `.co` campaign; the Canadian one is held by a different
  customer. That is the exclusivity rule (`uq_inbound_live_canonical_did`)
  working exactly as asked for, platform-wide, and it needed no new code.
* Moving the UK number needs Blaze to re-point it onto a SIP account the gmail
  side owns. Calls arrive on account `150001`, which sits on `.co`.
* New campaign **"Estimation (knowledge test)"**, running, outbound,
  `customer_support`, 27 sections inline, no leads and no contact list, so
  starting it dials nobody. Verified 25 s after going live: 0 dialler jobs.

## Commits

| SHA | What |
|---|---|
| `60512dd1` | answer the caller on a suppressed goodbye instead of talking over them |
| `f30d570a` | budget against the models the product actually runs |
| `6eb12cb2` | register a phone number through the supported service |
| `a21bd0ee` | pass the verification method as its enum |
| `138e73c2` | clone a campaign and re-publish its knowledge |
| `185f6e9c` | stop enriching against a model Groq removed |
| `98f50a1f` | make enrichment survive imperfect JSON |
| `b991c244` | migration 0047 + skip body-less headings |
| `61c446db` | stop tearing down a working speech engine; stop sitting on an answer |
| `edee6fe8` | make an unfilled `[slot]` visible before a caller hears it |

Canonical suite **9,181 passed / 8 skipped**. Ruff clean on `app/`.

## Still open

* **The persona base is 14,429 characters** even after the switch, and it is
  sent on every turn of every call for every tenant. It is the largest single
  item left and nothing here touched it.
* **Contact capture on inbound has never existed.** No inbound call has ever
  produced a lead.
* **`a3788fa7` still has an unfilled `[name]`**, and campaigns ingested since
  mid-August still carry unenriched knowledge.
* **`talky-api` is the service unit**, not `talky-backend`. Every log command
  written against the latter returns nothing; earlier reports repeated that
  mistake.
* **Blaze must re-point +442046132300** before it can answer on the gmail
  account.
