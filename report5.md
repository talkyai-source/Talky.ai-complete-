# Report 5 — the first real canary, and the five defects it found

**Run date:** 2026-08-13 · 40 calls · 13:24:27 → 15:05:10 UTC
**Status:** everything in this report is deployed and verified in the running process.

---

## Contents

| § | Section |
|---|---|
| 0 | [Live status](#0-live-status) |
| 1 | [Findings at a glance](#1-findings-at-a-glance) |
| 2 | [Timeline of the day](#2-timeline-of-the-day) |
| 3 | [What the run was](#3-what-the-run-was) |
| 4 | [How every number here was derived](#4-how-every-number-here-was-derived) |
| 5 | [Defect 1 — STT died silently](#5-defect-1--stt-died-silently) |
| 6 | [Defect 2 — nudging over live speech](#6-defect-2--nudging-over-live-speech) |
| 7 | [Defect 3 — the prompt cache had never hit](#7-defect-3--the-prompt-cache-had-never-hit) |
| 8 | [Defect 4 — a stray quantifier deleting replies](#8-defect-4--a-stray-quantifier-deleting-replies) |
| 9 | [Defect 5 — "Hi, is this Call?"](#9-defect-5--hi-is-this-call) |
| 10 | [The 421 audio gaps — measured, not fixed](#10-the-421-audio-gaps--measured-not-fixed) |
| 11 | [Latency anatomy](#11-latency-anatomy) |
| 12 | [Conversation quality](#12-conversation-quality) |
| 13 | [Signals that have stopped informing](#13-signals-that-have-stopped-informing) |
| 14 | [Test inventory](#14-test-inventory) |
| 15 | [Two hypotheses that died to measurements](#15-two-hypotheses-that-died-to-measurements) |
| 16 | [Risk register, kill switches, rollback](#16-risk-register-kill-switches-rollback) |
| 17 | [Still open](#17-still-open) |
| 18 | [What to watch on the next calls](#18-what-to-watch-on-the-next-calls) |
| 19 | [The pattern](#19-the-pattern) |
| A | [Appendix A — reproduce every number](#appendix-a--reproduce-every-number) |
| B | [Appendix B — per-session metrics](#appendix-b--per-session-metrics) |
| C | [Appendix C — change inventory](#appendix-c--change-inventory) |
| D | [Appendix D — effective configuration](#appendix-d--effective-configuration) |

---

## 0. Live status

Verified by importing the deployed modules and calling them, **not** by reading
files on disk. That distinction has cost this project twice — once when code was
pulled onto a running service without a restart and 10 of 47 real calls lost
STT — so it is stated first and stated precisely.

| | |
|---|---|
| Production HEAD | **`e17b33d1`** |
| Deployed | 2026-08-13 21:55:43 UTC |
| Preceding wave | `3205c7ef`, deployed 21:18:34 UTC |
| Report commit | `cfb1f581` |
| Rollback target | `4ebdbd55` (pre-canary-fix baseline) |
| Services | 6/6 active — `talky-api`, `talky-dialer-worker`, `talky-voice-worker`, `talky-reminder-worker`, `talky-voice-gateway`, `asterisk` |
| Health | `{"ready":true,"db":"ok","redis":"ok"}` |
| Workers | 3/3 healthy |
| Restart gate | `active_sessions: 0` and zero calls in flight, checked before **each** of the two restarts |
| Errors since restart | none beyond the perennial `Vonage … not configured (optional)` lines |

### 0.1 Behaviour read out of the deployed code

```
Sure thing.                      -> 'Sure thing.'                                (was '.')
Sure thing. The weather today... -> 'The weather today is actually quite nice.'  (was '. The weather...')
Sure, take your time.            -> 'Take your time.'                            (was 'take your time.')
lead first_name 'Call'           -> ''
lead first_name 'Sarah'          -> 'Sarah'
event_loop_lag_heartbeat_started period_ms=10
cache_friendly default           : True
base is first / floor last       : True True
caller_audio_active param        : True
nudge suppressed on speech       : wait
hangup still absolute            : hangup
```

The last two lines are the pair I most wanted to see together: the acoustic
guard turns a nudge into a wait, and the 60-second hangup still fires regardless.
Noise cannot hold a call open.

### 0.2 Test gate

| Wave | Passed | Failed | Errors |
|---|---|---|---|
| `3205c7ef` | 5,160 | 8 | 35 |
| `e17b33d1` | **5,237** | 8 | 35 |
| Pristine-HEAD baseline | — | 8 | 35 |

The 8 failures and 35 errors are **identical to the baseline** — missing secrets
in the isolated verify worktree (`test_webhooks_call_hmac`,
`test_webhooks_call_idor`, `test_systemd_readiness`, `test_telephony_ws_auth`,
`test_metrics_endpoint_auth`). Both waves therefore add zero regressions. The
baseline was measured on an unmodified worktree at the same HEAD rather than
assumed.

Two regressions *were* introduced during this work. Both were caught by existing
tests and fixed, not suppressed — see §14.3.

### 0.3 Raw evidence

`call_logs2.md` — 14,441 lines, verbatim journal, **gitignored**: this repository
is public and the journal carries client IPs, tenant and call UUIDs.

---

## 1. Findings at a glance

| # | Defect | Impact measured | Status |
|---|---|---|---|
| 1 | STT stream died without erroring | **2 of 36 answered calls lost (5.6%)** | Fixed · `3205c7ef` |
| 2 | Silence ladder ran over live speech | 28 of 35 nudges (80%), 20 of 40 calls | Fixed · `3205c7ef` |
| 3 | Prompt cache never hit | ~614ms of every 788ms TTFT | Fixed · `3205c7ef` |
| 4 | Regex quantifier deleted whole replies | Dead air, ≥3 turns observed | Fixed · `e17b33d1` |
| 5 | Lead name was not a name | All 40 calls opened "Hi, is this Call?" | Fixed · `e17b33d1` |
| 6 | 421 audio-gap warnings | 36 of 38 calls; **no audio lost** | Instrumented · `e17b33d1` |
| 7 | 4 recordings destroyed | Disclosure interrupted → audio discarded | **Open — needs a decision** |
| 8 | STT mis-transcription | "doing great" → "Spiritville" | Open — unattributed |
| 9 | 8 tracebacks | Known client-disconnect `RuntimeError` | Known, unchanged |

Three of the five defects share one shape, and it is the thesis of this report:

> **A signal that cannot represent the failure will report success.**

---

## 2. Timeline of the day

| Time (UTC) | Event |
|---|---|
| 11:53 | Campaign `INTERNAL-VOICE-VALIDATION` created |
| 13:24:27 | First call placed — code under test is `4ebdbd55` |
| 14:12:01 | Call answered; STT returns nothing; ladder runs; caller hangs up at 26s |
| 14:30:21 | Same failure again — 29s |
| 15:05:10 | Last call ends. 40 placed |
| ~20:20 | Log analysis begins |
| 21:18:34 | **Wave 1 deployed** — `3205c7ef` (defects 1–3) |
| 21:55:43 | **Wave 2 deployed** — `e17b33d1` (defects 4–6) |

Both restarts were gated on `active_sessions: 0`. Neither was performed blind
during traffic.

---

## 3. What the run was

This closed the long-standing "0 of 30 canary calls" gap that had been carried
in every previous report. Until this run, every claim about voice behaviour was
supported by tests and log archaeology, never by a body of live calls.

| | |
|---|---|
| Campaign | `INTERNAL-VOICE-VALIDATION` (`c2b6734d-8992-4038-aaf5-b54a885e7abe`) |
| Tenant | `1845a165-08aa-4554-bcec-2d31ac523662` |
| Window | 13:24:27 → 15:05:10 UTC (1h 41m), roughly one call every 2½ minutes |
| Calls placed | 40 |
| Answered | 36 |
| No answer | 3 |
| Voicemail | 1 |
| **Answered, zero conversation** | **2** |
| Pipeline sessions observed in logs | 38 |
| Assistant turns generated | 203 |
| Turns with a latency record | 190 |
| Talk time | 2,952s across the 36 answered calls |
| Median answered-call duration | 76s (range 26–160s) |
| LLM | `qwen/qwen3.6-27b` (Groq) |
| STT | `flux-general-en` primary, `nova-3` secondary (armed, never promoted) |
| TTS | `deepgram` |

### 3.1 Outcome distribution

```
answered   ############################################  36  (90.0%)
no_answer  ####                                           3  ( 7.5%)
voicemail  #                                              1  ( 2.5%)
```

Of the 36 answered, **2 produced no conversation at all** — §5.

### 3.2 Call durations (answered only)

```
   0- 30s  ###                                    3
  30- 60s  ###########                           11
  60- 90s  ############                          12
  90-120s  ######                                 6
 120-160s  ####                                   4
```

---

## 4. How every number here was derived

No figure in this report is estimated. Each is one of:

1. **A SQL aggregate** over the `calls` table on the production database
   (read-only `SELECT`).
2. **A count or percentile over the raw journal**, from the exact window
   13:20:00 → 15:10:00 UTC across four systemd units.
3. **A live import** of the deployed module, calling the function and printing
   the result.

Appendix A gives the exact command for every one. Where a number could not be
derived — the *cause* of the audio gaps — the report says so rather than
estimating (§10).

**One methodological note that changed a conclusion.** The `audio_level` log
line carries `window_s` and `samples`. Because the internal rate is a known
16 kHz mono, `samples / (window_s × 16000)` is the fraction of the audio that was
*owed* for that second and actually *arrived*. That ratio is what converted
"421 warnings, cause unknown" into "nothing was lost, this is bunching" — and it
had been sitting in the logs, uncomputed, the whole time.

---

## 5. Defect 1 — STT died silently

**2 of 36 answered calls (5.6%) lost outright.** Calls at 14:12:01 (26s) and
14:30:21 (29s).

### 5.1 What the logs show

Flux was pre-connected during ringing, was handed 400+ audio chunks, and
returned **zero transcript events for the entire call** — not one `StartOfTurn`.
The caller was talking the whole time.

```
14:11:59  Deepgram Flux pre-connected for call a3be5f6c (eager=0.7 eot=0.85 timeout_ms=500)
14:12:09  stt_resilient_wrapper_active primary=flux-flux-general-en secondary=nova-nova-3
14:12:09  flux_first_audio_sent elapsed_ms=90 — caller audio now flowing to Deepgram STT
14:12:17  audio_level rms=965   peak=4845    -> [SilenceMonitor] silence (opening), nudging: 'Hello?'
14:12:18  audio_level rms=3504  peak=28988   <- caller talking loudly
14:12:19  audio_level rms=2038  peak=13965
14:12:20  audio_level rms=664                -> [SilenceMonitor] silence (opening), nudging: 'Hello??'
14:12:22  audio_level rms=1884  peak=13541
14:12:23  audio_level rms=1644  peak=13426
14:12:24  audio_level rms=1520  peak=7758    -> [SilenceMonitor] nudging: 'Helloooo — are you there?'
14:12:26  AsteriskAdapter: session ended reason=StasisEnd
14:12:26  audio_stream_ended chunks_yielded=418 stt_active=False
```

The module's own log line, emitted on every one of those rows, reads
`(>500=speech-likely, <100=silence-likely)`. It measured **3504** and called it
silence.

The second call is the same signature, independently:

```
14:30:37  audio_level rms=2313 peak=30962
14:30:40  [SilenceMonitor] silence (opening), nudging: 'Hello?'
14:30:43  [SilenceMonitor] silence (opening), nudging: 'Hello??'
14:30:47  [SilenceMonitor] nudging: 'Helloooo — are you there?'
14:30:49  DeepgramFlux cleaned up
```

Peak amplitude 30962 out of a 32767 full-scale range. That is not a quiet room.

### 5.2 Why both safety nets missed it

They failed for the *same* reason, which is why having two of them helped
nothing:

| Mechanism | Trigger it watches | Why a dead stream is invisible to it |
|---|---|---|
| `ResilientSTTProvider` | An **exception** from the provider | A socket that answers nothing raises nothing. Control never leaves the `async for`, so the failover path is never reached. |
| `SilenceMonitor` | **Absent transcripts** | A dead stream and a quiet room produce the identical observation. |

The `nova-3` secondary was armed and initialised on both calls
(`stt_resilient_wrapper_active primary=… secondary=nova-nova-3`) and was never
promoted. There was no bug in the failover logic; it was simply never asked.

Deeper: four of the 38 sessions never produced a single `t_stt_first_final`,
against 34 that did.

### 5.3 The fix

The two states are only separable **acoustically**, so both mechanisms now
consult energy.

**`resilient_stt.py`** — a voiced-audio watchdog inside the wrapper:

- Counts **voiced** audio in against transcript events out. Fails over after
  6 s of voiced audio with nothing returned.
- **Not wall-clock.** A quiet caller accumulates none of it, so this can never
  fire on plain silence. That is the whole reason the naive version of this
  check has never been written before.
- **Re-arms on every transcript**, so a stream that dies at minute nine is
  caught the same way. The observed incident is only the turn-0 case.
- **Excludes muted frames.** While the agent speaks, STT is muted and the
  provider deliberately drops frames — but those frames still travel through
  the wrapper, and on a 2-wire line they carry our own TTS at full volume.
  Counting them would trip the watchdog on every talkative agent.
- **Reuses the existing replay buffer**, so the utterance the caller was
  mid-way through is re-transcribed by the secondary rather than lost.
- **Fails safe.** An unparseable chunk scores 0.0 energy and contributes no
  voiced time, so a malformed frame can never cause a spurious failover.

Two escape hatches for the "provider swallows the exception" case: the raise
propagates out through the provider's own iteration, *and* a post-loop
`watchdog.tripped` re-check catches a provider that ends its stream cleanly
instead. Both paths are tested.

Choosing the wrapper rather than the telephony caller means this covers
telephony, browser and ask-AI at once.

**`audio_ingest.py`** — `silence_action` gained `caller_audio_active`:

```python
if in_grace:
    return "wait"
if caller_silence_s >= hangup_s:
    return "hangup"
if caller_audio_active:          # <- the acoustic escape hatch
    return "wait"
```

It suppresses **nudging only**. It deliberately does *not* block the hangup:
energy on the line is not proof of a conversation — it is also what a television
in the background looks like — and a 60-second bound that noise can extend is
not a bound. A live person whose STT has died is rescued by the failover, not by
refusing to ever hang up.

The signal itself already existed. `audio_ingest` had been publishing
`session.last_audio_rms` once a second for a different purpose, and **nothing
read it** — the comment described a consumer that did not exist. Making it
load-bearing is most of this fix.

One detail worth recording: that stamp used the event-loop clock while the
monitor uses `time.monotonic`. They are the same clock on the default event
loop — and a freshness check that silently depends on that is one custom loop
away from comparing two unrelated timebases and reading as permanently stale,
i.e. this guard quietly not existing. It is now stamped explicitly.

### 5.4 Deliberate limitation, recorded

The watchdog cannot distinguish "dead" from "streaming provider that has not
answered yet". A provider that buffers the whole utterance and answers only at
the end **will** be failed over.

This is accepted, and pinned by a test named for it, because:

- Neither engine we run behaves that way. Flux emits `StartOfTurn` on the first
  word, and this run logged 2,939 transcript events across 40 calls (~73/call).
- The outcomes are wildly asymmetric. A false trip costs one extra socket plus a
  replayed buffer — degraded, bounded. Not tripping costs the entire call, which
  is what this day actually cost.

If a batch-style provider is ever added, raise `silent_stream_voiced_seconds`
for it or set it to 0.

---

## 6. Defect 2 — nudging over live speech

The same blindness as §5, without the total loss.

| Measure | Value |
|---|---|
| Nudges fired | 35 |
| **While caller RMS > 500** (the module's own "speech-likely") | **28 (80%)** |
| While caller RMS > 1500 (unambiguous speech) | 15 (43%) |
| Calls that nudged at all | 24 of 40 |
| Calls that nudged **over speech** | 20 of 40 |

By phrase:

```
'Hello?'                     ######################   22
'Hello??'                    ######                    6
'Helloooo — are you there?'  ####                      4
'Still there?'               ###                       3
```

32 of the 35 were `opening` nudges, 3 were `mid`.

### 6.1 What that sounded like

The callee said so themselves, repeatedly, in the transcripts:

> **14:05** — *"While you were talking, I said I need to ask you something
> before you continue, and you didn't stop there."*

> **14:07** — *"In the start of the call, when I said 'before you continue, can
> you explain what this call is about?' — you heard it and you stopped right
> there and then you didn't answer me."*

> **14:10** — the ladder escalated *after* the caller had already spoken:
> ```
> Assistant: Hello?
> Assistant: Hello??
> User:      Sure. I understand the purpose of the call.
> Assistant: Helloooo — are you there?
> ```

> **14:21** — the agent, on being asked whether it caught three questions:
> *"I only heard the one about whether this is an internal test. What were the
> other two?"*

And twice the ladder fired when the caller had spoken **first**:

```
14:17  User: Tell me what's going on.      Assistant: Hello?
14:19  User: Tell me what this call is for. Assistant: Hello?
```

Fixed by the same acoustic guard as §5.

---

## 7. Defect 3 — the prompt cache had never hit

### 7.1 The measurement

| Scope | Calls | `cache_hit_ratio` |
|---|---|---|
| This run | 279 | **0.00 on every one** |
| Last 7 days, `qwen/qwen3.6-27b` | 426 | **0.00 on every one** |
| Last 7 days, `llama-3.1-8b-instant` | 106 | 0.00 |
| Last 7 days, `llama-3.1-8b-instant` | 27 | **hits, 512 → 6656 tokens** |

That last row is the one that settles it. **Prompt caching works on this
account.** It was never a model limitation or a missing API parameter — one code
path gets hits of up to 6656 tokens and the voice path gets none, ever.

Note the cached values are all multiples of 512: Groq caches in 512-token
blocks, so the prefix must be stable to a block boundary.

### 7.2 The cause

`build_turn_prompt` assembled the final system prompt as:

```
LIVE STATE  +  CAPTURED  +  base  +  optional blocks  +  trailing
^^^^^^^^^^     ^^^^^^^^
changes every turn
```

Both leading blocks change every turn. A prompt cache keys on the **longest
common prefix from the first token**, so with a volatile block at character 0
the cacheable prefix is zero tokens. Not "small" — zero. It could never have hit.

### 7.3 What it cost

| Stage | p50 | p90 | max |
|---|---|---|---|
| Mouth-to-ear | 1014 ms | 1366 ms | 4349 ms |
| ├─ LLM time-to-first-token | 788 ms | 959 ms | 2875 ms |
| │   └─ of which Groq `prompt_time` | **614 ms** | 718 ms | 779 ms |
| │   └─ of which Groq `queue_time` | 89 ms | 184 ms | — |
| └─ TTS first chunk | 210 ms | 228 ms | 3412 ms |

`prompt_time` is 78% of time-to-first-token and about **60% of everything a
caller waits** — spent re-reading an 8.5k-token prompt the provider had already
read seconds earlier on the previous turn.

Prompt sizes: p50 8,529 tokens, max 8,851. The base was ~6.6k of that (first
turn of a call measured 6,636), i.e. roughly **78% of every prompt was
identical from turn to turn** and none of it was reused.

### 7.4 The fix

Stable-for-the-call blocks first, per-turn blocks last:

```
[base] [end-session] [audio-tags] [accent]      <- identical all call: CACHEABLE
[ask-AI] [knowledge] [CAPTURED] [LIVE STATE]    <- per-turn
[trailing]                                      <- compliance floor keeps LAST
```

Two things that look like risks and are not:

- **LIVE STATE is promoted, not weakened.** It moves from position 0 to a few
  hundred tokens from the end. This codebase's own hard-won finding is that the
  trailing slot wins — that is exactly why `trailing_block` exists and why the
  per-turn re-anchor was put there. Moving a block from the front of 8.5k tokens
  to the back is a promotion in recency terms.
- **The compliance floor keeps the final word.** `trailing_block` is still the
  last text in the prompt, unchanged. It sits after the volatile blocks and so
  is not itself cached; at ~1k characters that is a rounding error against the
  ~6.6k tokens that now are.

`VOICE_PROMPT_CACHE_ORDER=false` restores the previous order without a redeploy.
The legacy path is kept **executable**, not merely described, and is covered by
its own tests — a revert switch nobody exercises is not a revert switch.

### 7.5 Expected effect

If ~6.6k of 8.5k tokens become cacheable, `prompt_time` should fall roughly in
proportion — from ~614 ms toward the 150–250 ms range — taking mouth-to-ear from
~1014 ms toward the low 600s. **This is a projection, not a measurement.** No
calls have been placed since the deploy. §18 gives the single log field that
will confirm or refute it.

---

## 8. Defect 4 — a stray quantifier deleting replies

### 8.1 The evidence

Two production turns were transcribed as literally `.`, and one as
`. The weather today is actually quite nice.` — the turn immediately before it
having said `Alright, the weather today is actually quite nice.`

```
13:40:10  llm_response turn=13 said='Alright, the weather today is actually quite nice.'
13:40:19  llm_response turn=14 said='. The weather today is actually quite nice.'
```

And in the persisted transcript of the 13:41 call:

```
User: Stop.        Assistant: .
User: Stop.        Assistant: .
```

### 8.2 The cause

```python
filler_starts = [
    r'^Sure thing[!,]?\s*',     # <- the only pattern in the list using \s*
    r'^No problem[!,]?\s+',
    r'^Happy to help[!,]?\s+',
    r'^Sure[!,]?\s+',
    ...
]
```

`\s*` matches the empty string. So `"Sure thing."` matched `Sure thing`, the
optional `[!,]` did not match the period, `\s*` matched nothing — and the
substitution left `"."`.

`"No problem."` was untouched, because `\s+` requires a separator that a
sentence-final period does not provide. One character of difference between two
adjacent lines.

### 8.3 Why it caused silence, not a stray noise

`turn_streamer` then applied:

```python
if not sentence or len(sentence) < 6:
    continue
```

`"."` is one character. The sentence was dropped. **That turn produced no audio
at all.** The model answered, and the answer was deleted between the LLM and the
wire. From the caller's side: dead air, mid-conversation.

This is the same symptom reported weeks earlier as *"it stops after speaking one
time, no follow up"* — previously attributed to the silence ladder, and only
partly so.

### 8.4 The length test was independently wrong

`"Yes."` `"Okay."` `"Sure."` `"No."` `"Got it."` are all under six characters.
Every one of them would have been silently discarded.

That was latent for as long as the agent was verbose. It stopped being latent
the week the **answer-first** rule shipped, which explicitly instructs the model
to answer a direct question *plainly, in one sentence, with no question
attached* — precisely the shape this deleted. One rule was generating exactly
what another rule threw away.

The replacement drops only what cannot be **spoken**:

```python
if not sentence or not any(c.isalnum() for c in sentence):
    continue
```

A length threshold was always the wrong instrument. The question is whether
there is anything to say, not how many characters it takes to say it.

### 8.5 The first fix moved the bug instead of removing it

Requiring a separator on `Sure thing` meant the two-word pattern no longer
matched `"Sure thing."` — so it fell through to the shorter `^Sure[!,]?\s+`
pattern, which ate `"Sure "` and left `"thing."`, capitalised to `"Thing."`.

A test caught it:

```
E       AssertionError: assert 'Thing.' == 'Sure thing.'
```

The list's own comment says *"Multi-word phrases must come before single-word so
the longer match wins"* — but that ordering only works if the longer pattern can
**match**. Every filler now terminates on `(?:\s+|$)`, so the longer phrase wins
even when it is the whole message, and a filler that *was* the entire message
restores the original text.

### 8.6 The same cause explains the lowercase turns

Across the run, many replies began mid-thought in lower case:

```
'take your time.'          'go ahead.'         'what did you have in mind?'
'just a few minutes.'      'it's mozzarella'   'i don't pay anyone.'
'try interrupting me mid-sentence.'
```

Stripping the filler strips the capital it was carrying: `"Sure, take your
time."` → `"take your time."`. Sentence case is now restored after a strip,
touching only a leading lowercase ASCII letter so `"iPhone"` and capitalised
names are never rewritten.

An orphan-punctuation strip was also generalised. One already existed for
em-dashes — *"left behind after filler removal"* — which is the same defect
class, spotted once and fixed only for the character that had been observed. It
now removes any leading punctuation, but **only when the text actually changed**,
so a reply that legitimately opens with punctuation is untouched.

---

## 9. Defect 5 — "Hi, is this Call?"

Every one of the 40 calls opened by asking the callee whether they were *Call*.

```sql
SELECT first_name, last_name, count(*) FROM leads
 WHERE campaign_id='c2b6734d-8992-4038-aaf5-b54a885e7abe' GROUP BY 1,2;

 first_name | last_name | count
------------+-----------+-------
 Call       | 30        |     1
```

Somebody had typed "Call 30" — as in *call thirty numbers* — into the name
field. The existing sanitizer stripped the digits (`call_target_field_sanitized
field=last_name in_chars=2 out_chars=0`, 80 occurrences), because its shape
allowlist rejects digits in person names. Nothing checked whether the remaining
word was a name.

### 9.1 The guard

Person names are now checked for plausibility and dropped to `""`, which the
pipeline already handles: the agent falls back to the other name field, or opens
without a name. Three shapes are rejected:

1. Nothing but digits or punctuation once the shape allowlist has run.
2. A single **ASCII** character (`"A"`, `"-"`).
3. Every word in a short placeholder vocabulary — campaign-operations words
   (`call`, `test`, `lead`, `dialer`), placeholders (`unknown`, `n/a`, `tbd`,
   `placeholder`), and titles (`mr`, `dr`).

Rejection requires **every** word to be a placeholder, so `"Call Robertson"` —
implausible as a first name but conceivably a surname — is kept.

### 9.2 The list is deliberately short

Months (May, June, April), virtue names (Grace, Hope, Faith), and short
surnames-as-forenames (Lee, Bo, Ng, Al) are exactly what a longer list starts
eating. They are explicitly tested as *must survive*.

Addressing someone by the wrong word is a small embarrassment. Refusing to use a
real person's name is a worse one, and it fails silently.

### 9.3 A security test caught a real false positive

The first version rejected **张 伟**. In Han, Kana and other logographic scripts
a single glyph is a complete given name, and a bare length test rejects those
outright. The too-short rule now applies only to ASCII:

```python
joined = "".join(tokens)
if len(joined) < 2 and joined.isascii():
    return True
```

The test that caught it — `test_legit_names_pass_through_unchanged` in
`tests/security/test_lead_field_injection.py` — was written for injection
hardening, not for this. It is the second time this session that an existing
test written for another purpose caught a new mistake.

---

## 10. The 421 audio gaps — measured, not fixed

### 10.1 The symptom

| Measure | Value |
|---|---|
| Warnings | 421 |
| Calls affected | 36 of 38 |
| Per call | ~11 |
| Expected inter-arrival | 40 ms (2 × 20 ms RTP frames per callback) |
| p50 | **266 ms** (6.6×) |
| p90 | 293 ms |
| max | 440 ms |
| ≥ 200 ms | 350 of 421 (83%) |

```
telephony_audio_gap (ms)  (n=421)
    120-  160 | ##                                                         17 ( 4.0%)
    160-  200 | #######                                                    54 (12.8%)
    200-  240 | #########                                                  65 (15.4%)
    240-  280 | ####################                                      154 (36.6%)
    280-  320 | ################                                          122 (29.0%)
    320-  400 | #                                                           7 ( 1.7%)
    400-  inf |                                                             2 ( 0.5%)
```

A tight cluster: two-thirds fall in a single 80 ms band.

### 10.2 The warning could not diagnose itself

Its own comment is honest about this, and records that it had already misled an
investigation:

> *"The cause is NOT asserted. A gap here means audio did not ARRIVE on time;
> that is equally consistent with the gateway dropping a callback, this process
> being blocked so the callback timed out at the 200 ms fire-and-forget
> deadline, or genuine upstream RTP loss. … Naming one suspect in the message
> sent an earlier investigation down the wrong path."*

The C++ gateway's own counters cannot settle it after the fact either —
`/stats` sums only **live** sessions, so a finished call's loss figures are gone.

### 10.3 The measurement that ruled out a third of the suspects

The `audio_level` line carries `window_s` and `samples`, and the internal rate
is a known 16 kHz mono. So `samples / (window_s × 16000)` is exactly the
fraction of owed audio that arrived:

```
audio delivery ratio (arrived / expected), n=2395 one-second windows
  p01 = 0.800   p10 = 1.000   p50 = 1.000   p90 = 1.040   p99 = 1.240
  mean = 1.0089
  windows below 0.90 :  81 (3.4%)
  windows above 1.10 :  74 (3.1%)

per-call total, n=38 calls
  min = 0.740   p50 = 1.011   max = 1.656   mean = 1.0181
```

**3.4% short against 3.1% long is near-symmetric.** If packets were being
dropped, deficit windows would not be matched by surplus windows. Every byte
arrived; some arrived late and in bursts.

**RTP loss is ruled out.** It could have been struck off on day one if the
warning had carried the number — the data had been in the logs the whole time.

### 10.4 What could not be determined

Whether the bunching is the C++ gateway batching its callbacks or this process
being too busy to service them. Correlating gaps against LLM/TTS activity was
inconclusive:

```
gaps total: 421
  within 1s of TTS activity only : 138
  within 1s of LLM activity only :  73
  within 1s of both              :  43
  quiet (no TTS/LLM within 1s)   : 167   <- 40%
```

40% occur with no nearby LLM or TTS work, and there is no base rate to compare
the other 60% against. That is not enough to attribute a cause, and this
detector has already cost one investigation by inviting a guess.

### 10.5 So: instrument, don't guess

Each warning now carries the two facts that settle it:

| Field | Reads | Means |
|---|---|---|
| `arrived_ratio` | ~1.0 | late, not lost — a timing artefact |
| | sustained < 1.0 | real loss, a different problem entirely |
| `loop_lag_ms` + `stall=ours` | high | we could not be scheduled; ours to fix |
| `loop_lag_ms` + `stall=not-ours` | ~0 | we were idle and waiting; it arrived late from outside |
| `loop_lag=unmeasured` | — | we did not look — deliberately distinct from "0.0" |

That last distinction matters: reporting `loop_lag_ms=0.0` when nothing was ever
sampled asserts the loop was healthy on no evidence.

### 10.6 It reuses the existing heartbeat

I wrote a 10 Hz lag sampler, then found that `main._event_loop_lag_heartbeat`
already existed — 10 ms **absolute** deadlines (so measurement error cannot
accumulate as drift), a resync so one stall yields exactly one observation, and
a Prometheus histogram. Better engineered than mine in every respect.

Mine was deleted. A second ticker measuring the same loop would only disagree
with the first under load, which is precisely when you need to trust it.

What was actually missing was never another *measurement*. A histogram answers
*"how has the loop been lately"*; it cannot answer *"was the loop stalled when
THIS batch arrived late"*, because that needs a value in hand at the moment of
the log call. The heartbeat now publishes each observation to a synchronous
reader alongside its histogram write.

Confirmed in the live process:

```
21:55:43 INFO [app.main] event_loop_lag_heartbeat_started period_ms=10
```

### 10.7 Does the bunching cost anything?

Yes, and it is bounded: a batch arriving up to 266 ms late delays the last frame
of an utterance reaching STT by that much, which directly delays end-of-turn
detection. It does **not** corrupt transcription — Deepgram timestamps by sample
count, and every sample arrives.

That reasoning is why §17 no longer lists the gaps as the suspected cause of the
mis-transcriptions.

---

## 11. Latency anatomy

### 11.1 Mouth-to-ear

```
mouth-to-ear latency (ms)  (n=190)
    900- 1000 | ########################                                   80 (42.1%)
   1000- 1100 | #####################                                      72 (37.9%)
   1100- 1200 | ###                                                        10 ( 5.3%)
   1200- 1400 | ###                                                        10 ( 5.3%)
   1400- 1700 | ###                                                        10 ( 5.3%)
   1700- 2200 | #                                                           5 ( 2.6%)
   2200- 3000 |                                                             0 ( 0.0%)
   3000-  inf | #                                                           3 ( 1.6%)
```

80% of turns land in a 200 ms band around 1 second. This is not a variance
problem — it is a floor problem. Something costs ~1 s on nearly every turn.

### 11.2 Where the second goes

```
LLM time-to-first-token (ms)  (n=190)
    700-  750 | #####                                                      17 ( 8.9%)
    750-  800 | ###########################                                93 (48.9%)
    800-  850 | ############                                               41 (21.6%)
    850-  900 | ####                                                       14 ( 7.4%)
    900- 1000 | ##                                                          8 ( 4.2%)
   1000- 1200 | ##                                                          8 ( 4.2%)
   1200- 1600 | #                                                           4 ( 2.1%)
   1600-  inf | #                                                           5 ( 2.6%)

TTS first chunk (ms)  (n=190)
    150-  180 | ######                                                     21 (11.1%)
    180-  200 | #######                                                    24 (12.6%)
    200-  220 | ################################                          108 (56.8%)
    220-  240 | #######                                                    23 (12.1%)
    240-  300 | #                                                           2 ( 1.1%)
    300-  500 |                                                             1 ( 0.5%)
    500-  inf | ###                                                        11 ( 5.8%)
```

TTS is tight and fast: 80% within 180–240 ms. **Transport and TTS are not the
problem.** 79% of LLM first-token times fall in 750–850 ms.

### 11.3 And inside the LLM time

```
groq prompt_time (ms)  (n=243)
    400-  500 | #####                                                      22 ( 9.1%)
    500-  550 | ##                                                          9 ( 3.7%)
    550-  600 | ############                                               52 (21.4%)
    600-  650 | #######################                                   100 (41.2%)
    650-  700 | #######                                                    31 (12.8%)
    700-  750 | ####                                                       19 ( 7.8%)
    750-  inf | ##                                                         10 ( 4.1%)

groq queue_time (ms)  (n=243)
      0-   50 | ######                                                     28 (11.5%)
     50-  100 | ##########################################                182 (74.9%)
    100-  150 | #                                                           5 ( 2.1%)
    150-  200 | #                                                           3 ( 1.2%)
    200-  300 | #                                                           4 ( 1.6%)
    300-  500 | ##                                                         10 ( 4.1%)
    500-  inf | ###                                                        11 ( 4.5%)
```

Queue time is a well-behaved ~89 ms; we are not being rate-limited. `prompt_time`
is the mass, and `prompt_time` is prompt *reading* — the thing a cache exists to
eliminate. §7.

### 11.4 The accounting bug in these very numbers

273 of the `llm_usage` lines carry a **negative** `client_net_remainder_ms`
(e.g. `-90`, `-83`) — the client-measured total is smaller than the server's own
reported total. That is an instrumentation defect, not a physical one. It does
not affect the figures above (which come from `prompt_time` and the latency
tracker, not from the remainder) but it is recorded here so the field is not
trusted later.

---

## 12. Conversation quality

### 12.1 What held

Both conversation fixes from the previous wave survived contact with 40 real
calls.

**Openers are permission-based, never the availability form.** Across the run:

```
"Hi, this is Alex from Talk-Lee. Do you have a quick minute?"
"This is Alex from Talk-Lee, if you don't mind, do you have a minute?"
"Hi Call, it's Alex from Talk-Lee. Do you have a quick minute?"
```

Not one instance of *"did I catch you at a bad time?"* or any variant. That
family measures 0.9–2.15% in the 300M-call reference data against 11.18% for the
permission-based form, and it had appeared in production before the fix.

**Answer-first works.** A direct question gets a plain answer with nothing
attached, repeatedly:

```
User: Why did you call me?
Assistant: This is an internal Talk-Lee voice-system validation call to test
           conversation quality and call handling.

User: Why are you calling?
Assistant: This is an internal Talk-Lee voice-system validation call to test
           conversation quality and call handling.

User: Can you tell me why you're calling?
Assistant: This is an internal Talk-Lee voice-system validation call to test
           conversation quality and call handling.
```

At 15:03 the callee said it out loud: *"Okay. You're answering my question.
Thank you."*

### 12.2 What did not

**Transcription accuracy on this line is poor.**

| Spoken | Transcribed |
|---|---|
| "doing great" | *"Spiritville"* |
| "voice test" | *"swice test"* |
| "voice validation call" | *"swai validation card"* |
| "Messi" | *"messy"* |
| — | *"Ranch."* (twice, unattributable) |

Now known **not** to be caused by the audio gaps (§10.3). Unattributed.

**Transfer-to-human is answered optimistically then fails.** At 15:05:

```
User:      Can you transfer me to a person?
Assistant: I can certainly try — just a moment while I check if a human agent
           is available.
Assistant: It looks like I can't complete the transfer right now.
```

The agent promised a check it cannot perform. Not fixed in this wave; recorded.

---

## 13. Signals that have stopped informing

Two counters in current use will mislead anyone reading them, including future
me. Both are called out rather than quietly corrected.

### 13.1 `barge_in_detected` is not "the agent was talked over"

```
interrupt_step=begin              200
interrupt_step=state_listening    200
interrupt_step=task_cancelled     200
interrupt_step=nothing_playing    139   <- 70% were no-ops
interrupt_step=tts_provider_cleared 61
interrupt_step=cpp_interrupt        61
interrupt_step=buffers_cleared      61
interrupt_step=deduped               1
```

200 interrupts began; **139 (70%) found nothing playing**. Only ~52–61 did real
work. `tts_active=True` at begin on 52.

The operation itself is healthy — p50 0.18 ms, max **0.94 ms**, one dedupe
(single-flight working), no races. But `barge_in_detected=201` must never be read
as 201 interruptions.

### 13.2 `[SLOW]` fires on ~78% of turns

194 of ~250 turns tripped it. A flag that fires on four turns in five has stopped
discriminating.

Deliberately **not** recalibrated in this wave: the cache fix is about to move
the underlying distribution, and re-tuning a threshold to a distribution that is
about to change would just have to be done twice.

---

## 14. Test inventory

Four new test files (41 test functions, considerably more cases once
parametrisation is expanded) plus three amended files. The suite went from
5,155 to 5,237 passing.

### 14.1 New tests

| File | Pins |
|---|---|
| `test_resilient_stt_silent_stream.py` | Voiced audio trips the watchdog; a quiet caller never does; muted audio is not counted; a transcript re-arms it; unparseable chunks fail safe; a silent primary fails over and the caller is heard; the replayed buffer reaches the secondary; a provider that **swallows** the exception still fails over; a healthy streaming primary is never disturbed over 16 s of continuous speech; **and a batch-style provider trips it by design** |
| `test_audio_gap_is_self_diagnosing.py` | `unmeasured` ≠ `0.0`; healthy loop reads `not-ours`; stalled loop reads `ours`; peak is sticky while current tracks the latest; negative lag clamps; `record()` stays allocation-free at 100k calls; a late batch emits both discriminators; an on-time stream emits nothing; the warning survives an unmeasured loop |
| `test_short_reply_is_not_deleted.py` | `"Sure thing."` keeps its words; the turn-14 production string loses its orphan period; the filler is still stripped when it should be; sentence case is restored; a filler-only reply keeps its words; replies without a filler are untouched; the legacy dash strip still applies; short real replies (`"Yes."`, `"Okay."`, `"Got it."`) reach TTS; unspeakable leftovers are still dropped; the two fixes compose |
| `test_lead_name_plausibility.py` | 13 placeholders rejected; **17 real names survive**, including months, virtue names, single-glyph Han names and short surnames; a partly-placeholder name is kept; company names are exempt; rejection yields `""` not an exception |

### 14.2 Amended tests

| File | Change |
|---|---|
| `test_prompt_build.py` | Rewritten to cover **both** orders explicitly, so neither can be silently reinterpreted by a change of default. Adds: base is the very first text; stable blocks all precede every per-turn block (asserted as a partition, so a future block cannot slip into the prefix); LIVE STATE is the last per-turn block; the two orders contain exactly the same content; the env var selects the order; an explicit argument beats the env |
| `test_silence_action.py` | Audible speech suppresses the nudge; the guard defaults off; **audible speech does not block the hangup**; the guard can only turn `nudge` into `wait`, never the reverse |
| `test_llm_guardrails.py` | One expectation gained its capital — it had been encoding the lowercase-fragment defect as correct behaviour |

### 14.3 Two regressions the tests caught

1. **`'Thing.'`** — fixing the `\s*` pattern made `"Sure thing."` fall through to
   the shorter `^Sure` pattern (§8.5). Caught by a test written minutes earlier
   in the same session.
2. **`张 伟` rejected** — the too-short name rule was ASCII-blind (§9.3). Caught
   by a *security* test written months earlier for injection hardening.

Both were fixed at the root, not by loosening the assertion.

### 14.4 A limitation pinned as a decision

`test_a_batch_provider_trips_the_watchdog_by_design` asserts the behaviour I
would rather not have, with the reasoning in its docstring, so it is a recorded
trade-off rather than a latent surprise for whoever meets it next.

---

## 15. Two hypotheses that died to measurements

Left in deliberately. The wrong turn is the part worth remembering.

**"The audio gaps are causing the STT mangling."** Stated confidently in the
previous summary as the prime suspect and the thing to chase next. Measuring
delivery ratio showed nothing is lost — the gaps are bunching (§10.3). They cost
end-of-turn latency, not accuracy. The mis-transcriptions remain unexplained,
and are now correctly listed as such.

**"We need an event-loop lag sampler."** True, and one already existed, better
built than the one I wrote (§10.6). The actual gap was a synchronous *reader*,
not another *measurement*. Had I not gone looking before wiring it up, this wave
would have shipped two tickers disagreeing with each other under load — exactly
the duplicate-code problem flagged repeatedly on this project.

---

## 16. Risk register, kill switches, rollback

| Change | Risk | Detection | Reversal |
|---|---|---|---|
| Prompt reorder | Model behaviour shifts — re-introductions, name/role drift | Listen on the next call; `has_introduced` re-anchor is the first thing to fail | `VOICE_PROMPT_CACHE_ORDER=false` + restart. **No redeploy** |
| STT watchdog | Spurious failover on an unusual provider | `resilient_stt_stream_silent` firing on healthy calls | `silent_stream_voiced_seconds=0` disables it |
| Acoustic nudge guard | Nudges suppressed on a noisy line | Ladder never fires on a genuinely silent pickup | `VOICE_AUDIO_ACTIVE_RMS` raise, or `VOICE_AUDIO_ACTIVE_MAX_AGE_S=0` |
| Filler/short-reply fix | A filler-only reply now spoken where it was previously dropped | Transcript review | Revert `e17b33d1` |
| Lead-name guard | A real name rejected | `call_target_field_dropped … reason=implausible_person_name` in the journal | Revert `e17b33d1` |
| Gap instrumentation | None — diagnostic only, no behaviour change | — | — |

**Rollback:** `git checkout 4ebdbd55` + restart the four Python services. The
C++ gateway is untouched by both waves and does not need restarting.

**Watch this log line** — it is the one that tells you the name guard fired on
something it should not have:

```
call_target_field_dropped field=first_name reason=implausible_person_name chars=N
```

---

## 17. Still open

### 17.1 Four recordings destroyed — needs a decision, not a patch

```
recording_disclosure_speaking          37
recording_disclosure_interrupted        4   "notice cut short; not retried"
recording_suppressed_no_disclosure      4   "audio discarded"
```

Affected calls: 13:34/13:35, 13:38/13:40, 14:07/14:09, 14:32. Working exactly as
designed — but it destroys evidence on precisely the calls where somebody
interrupted, which are the calls most likely to be worth reviewing.

**Why I did not just fix it.** A "retry from the top" version was already built
and **reverted on 2026-08-11**: two traced production calls showed
`recording_disclosure_interrupted_retrying attempt=1/2` followed by the callee
hanging up ~2 s later. A person who hears "This call may be recorded…" start,
then start again, reads it as a malfunctioning robot. That fix was strictly
worse than the behaviour it replaced — it failed to deliver the notice *and* lost
the caller.

Every remaining option changes **when a notice counts as delivered**, which is a
retention-policy decision rather than an engineering one.

**Recommendation:** finish the notice at the start of the agent's *next* turn —
never over the caller — and retain the recording once it completes. That gets the
notice genuinely heard, which was always the point, without repeating the
reverted mistake.

### 17.2 STT mis-transcription

Unattributed (§12.2). No longer suspected to be the audio gaps.

### 17.3 Eight tracebacks

All the known client-disconnect `RuntimeError: No response returned` through the
`BaseHTTPMiddleware` stack, surfacing as a `BaseExceptionGroup` that skips every
`except Exception`. Unchanged, well understood, low value.

### 17.4 The transfer-to-human promise

The agent says it will check for a human agent, then reports it cannot. Recorded
in §12.2, not fixed.

---

## 18. What to watch on the next calls

Nothing in this report has been heard by a human on a live call. Two log fields
will confirm or refute the two largest changes.

**1. `cache_hit_ratio` should stop being `0.00`.**

```bash
journalctl -u talky-api --since "-1h" | grep -oE "cached_tokens=[0-9]+" | sort | uniq -c
```

Groq caches in 512-token blocks; expect roughly 6.5k of the 8.5k prompt to hit.
If it does, `prompt_time` falls from ~614 ms and mouth-to-ear should drop from
~1014 ms toward the low 600s.

**2. `resilient_stt_stream_silent` is a SUCCESS line.**

```bash
journalctl -u talky-api --since "-1h" | grep -E "resilient_stt_stream_silent|resilient_stt_failed_over_to"
```

It means the watchdog caught a dead stream and failed over — the call survived
where two calls on 2026-08-13 did not. Its absence is also fine; it should be
rare.

**3. The gap warnings now name their own cause.**

```bash
journalctl -u talky-api --since "-1h" | grep telephony_audio_gap | grep -oE "stall=[a-z-]+" | sort | uniq -c
```

**The one behavioural risk is the prompt reorder.** If the agent starts
re-introducing itself mid-call, or drifts its name or job title, that is the
reorder and nothing else — those are exactly the failures the LIVE STATE block
exists to prevent, and it is the block that moved. One env var reverts it.

---

## 19. The pattern

Report 4 closed on *"a fix verified in the wrong place is indistinguishable from
no fix at all."* This run has its own, and it appeared three separate times:

> **A signal that cannot represent the failure will report success.**

- **Absent transcripts** cannot distinguish a dead microphone from a quiet room.
  The silence monitor was not wrong; it was blind. It reported silence, and
  silence was a true statement about its inputs and a false statement about the
  world.
- **A gap timer** cannot distinguish late from lost. It correctly reported that
  audio arrived late, 421 times, and could not say the one thing that would have
  made any of them actionable.
- **A character count** cannot distinguish an empty reply from a short one.
  `len(sentence) < 6` was a perfectly accurate measurement of something that did
  not matter.

In each case the code worked exactly as written and reported exactly what it
could see — which was not enough to be right. No threshold change would have
fixed any of them, because the problem was never the value of the threshold; it
was that the quantity being thresholded did not carry the information.

The fix in each case was a **second, independent kind of evidence**:

| Blind signal | Added evidence |
|---|---|
| transcript presence | acoustic energy |
| elapsed time | delivered-vs-owed ratio |
| character length | presence of speakable content |

And where that second kind of evidence did not exist yet — the audio gaps — the
honest move was to build the instrument rather than to guess, and to say plainly
that the cause is still unknown.

Two of my own hypotheses died to measurements in this report. Both are left in
rather than quietly removed. A report that only contains the things I got right
is a worse guide to the next investigation than one that shows where the
plausible-sounding answer was wrong.

---

## Appendix A — reproduce every number

All read-only. Run from the production host.

**Call counts and outcomes**

```sql
SELECT to_char(created_at,'HH24:MI:SS') t, cp.name campaign, c.status, c.outcome,
       c.duration_seconds, length(coalesce(c.transcript,'')) tlen
  FROM calls c LEFT JOIN campaigns cp ON cp.id=c.campaign_id
 WHERE c.created_at::date = '2026-08-13'
 ORDER BY c.created_at;
```

**The raw window used throughout**

```bash
journalctl -u talky-api -u talky-voice-worker -u talky-dialer-worker \
           -u talky-voice-gateway \
           --since "2026-08-13 13:20:00" --until "2026-08-13 15:10:00" \
           --no-pager -o short-iso > raw.txt
grep -vE '"(GET|POST|PUT|DELETE|PATCH) /api' raw.txt > raw_app.txt   # 14,238 lines
```

**Latency percentiles and histograms**

```bash
grep -oE "Turn [0-9]+ latency: [0-9]+ms \(STT-first: [0-9]+ms, \
LLM-first-token: [0-9]+ms, TTS-first-chunk: [0-9]+ms" raw_app.txt
```

**Prompt-cache hit rate**

```bash
grep -oE "cache_hit_ratio=[0-9.]+" raw_app.txt | sort | uniq -c
journalctl -u talky-api --since "-7 days" \
  | grep -oE "model=[^ ]+ .*cached_tokens=[0-9]+"
```

**Audio delivery ratio (the measurement that ruled out packet loss)**

```python
# samples / (window_s * 16000) per audio_level line
m = re.search(r"audio_level.*window_s=([\d.]+).*samples=(\d+)", line)
ratio = int(m.group(2)) / (float(m.group(1)) * 16000.0)
```

**Nudges fired over live speech**

```python
# track the most recent audio_level rms per call, then join to each
# "[SilenceMonitor] ... nudging:" line for the same call
```

**Interrupt breakdown**

```bash
grep -oE "interrupt_step=[a-z_]+" raw_app.txt | sort | uniq -c | sort -rn
```

**Lead names**

```sql
SELECT first_name, last_name, count(*) FROM leads
 WHERE campaign_id = 'c2b6734d-8992-4038-aaf5-b54a885e7abe' GROUP BY 1,2;
```

**Live behaviour of the deployed code**

```bash
cd /opt/talky/backend && venv/bin/python -c "
from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.telephony_session_config import _sanitize_lead_field
from app.services.scripts.prompts.build import build_turn_prompt
g = LLMGuardrails()
print(repr(g.clean_response('Sure thing.')))
print(repr(_sanitize_lead_field('Call', field='first_name')))
print(build_turn_prompt('BASE', live_state_block='LIVE', trailing_block='FLOOR')[:4])
"
```

**Test gate (isolated worktree, never touches production)**

```bash
git -C /opt/talky worktree add --detach /tmp/tw HEAD
# scp changed files into /tmp/tw, then:
cd /tmp/tw/backend && /opt/talky/backend/venv/bin/python -m pytest \
  tests/unit tests/security --ignore=tests/unit/test_dialer_redis_reliability.py -q
git -C /opt/talky worktree remove /tmp/tw --force
```

`test_dialer_redis_reliability.py` is excluded because `fakeredis` is not
installed in the production venv — a pre-existing environment gap, unrelated to
these changes.

---

## Appendix B — per-session metrics

38 pipeline sessions observed in the logs (40 calls placed; the difference is
calls that never reached media setup). `lat_p50` is the median mouth-to-ear for
that session in ms; `0` means no turn produced a latency record.

| session | turns | gaps | nudges | lat_p50 | note |
|---|---:|---:|---:|---:|---|
| `98ecb12e` | 0 | 0 | 1 | 0 | |
| `83ed010e` | 5 | 8 | 0 | 1019 | first call of the run |
| `25f4e86e` | 5 | 9 | 0 | 1016 | |
| `5e5c137c` | 4 | 9 | 1 | 1544 | |
| `bf96f14d` | 9 | 22 | 0 | 1056 | |
| `917b1418` | 5 | 9 | 1 | 994 | disclosure interrupted → recording lost |
| `b7749189` | 14 | 26 | 0 | 1051 | the `. The weather…` call; disclosure lost |
| `6f689bf7` | 11 | 16 | 1 | 1125 | |
| `c4d64b40` | 9 | 17 | 0 | 1017 | |
| `286bad78` | 6 | 11 | 0 | 1076 | |
| `0e9c479d` | 5 | 8 | 1 | 948 | fastest median of the run |
| `8ea306cd` | 6 | 12 | 0 | 994 | |
| `9c6ee952` | 8 | 11 | 0 | 996 | |
| `2c4e2924` | 9 | 14 | 0 | 997 | |
| `08768ede` | 5 | 10 | 0 | 1012 | |
| `35f254f8` | 9 | 21 | 1 | 1014 | |
| `7b87b58f` | 5 | 11 | 3 | 1034 | full opening ladder |
| `2fdb8ae4` | 4 | 13 | 0 | 1234 | disclosure interrupted → recording lost |
| `9b4a1297` | 5 | 14 | 3 | 1144 | full opening ladder |
| **`a3be5f6c`** | **0** | 3 | **3** | 0 | **STT dead — call lost (14:12)** |
| `8adc445b` | 4 | 6 | 2 | 989 | |
| `0dee5a30` | 6 | 10 | 1 | 1012 | |
| `750bec49` | 1 | 4 | 1 | 0 | |
| `7246a237` | 4 | 8 | 1 | 1024 | |
| `460d3b50` | 5 | 18 | 2 | 1054 | |
| `e52272d5` | 2 | 6 | 1 | 1458 | |
| `d5034e89` | 7 | 17 | 0 | 977 | |
| **`abf708ba`** | **0** | 4 | **3** | 0 | **STT dead — call lost (14:30)** |
| `b5480e99` | 0 | 0 | 0 | 0 | voicemail; disclosure interrupted → recording lost |
| `444fa94e` | 9 | 21 | 1 | 1043 | |
| `1a938799` | 4 | 6 | 0 | 979 | |
| `750e287b` | 4 | 7 | 1 | 1003 | |
| `4024bc9f` | 3 | 6 | 1 | 970 | |
| `4747bd71` | 6 | 12 | 1 | 1027 | |
| `049e3f7a` | 7 | 13 | 1 | 979 | |
| `c196ff8e` | 5 | 17 | 2 | 980 | |
| `629ae8a0` | 3 | 6 | 1 | 1009 | |
| `df3707d7` | 9 | 16 | 1 | 997 | |
| **totals** | **203** | **421** | **35** | | |

Both lost calls share the identical fingerprint: **0 turns, 3 nudges** — the full
opening ladder spent on a caller the system could not hear. No other session in
the run matches it.

---

## Appendix C — change inventory

### Wave 1 — `3205c7ef` (deployed 21:18:34 UTC)

| File | Δ | What |
|---|---:|---|
| `app/domain/services/resilient_stt.py` | +207 | Voiced-audio watchdog, `STTStreamSilentError`, `_chunk_rms`, policy knob, secondary logging watchdog |
| `app/domain/services/voice_pipeline/audio_ingest.py` | +74 | `caller_audio_active` in `silence_action`; freshness-checked RMS read; explicit monotonic stamp; two env knobs |
| `app/services/scripts/prompts/build.py` | +118 | Cache-friendly order, executable legacy path, `VOICE_PROMPT_CACHE_ORDER` |
| `tests/unit/test_resilient_stt_silent_stream.py` | +381 | New — 14 tests |
| `tests/unit/test_prompt_build.py` | +232/−77 | Rewritten for both orders — 14 tests |
| `tests/unit/test_silence_action.py` | +57 | Acoustic guard — 5 added (31 in file) |

6 files, 992 insertions, 77 deletions.

### Wave 2 — `e17b33d1` (deployed 21:55:43 UTC)

| File | Δ | What |
|---|---:|---|
| `app/domain/services/llm_guardrails.py` | +73/−18 | `(?:\s+|$)` filler termination; orphan-punctuation strip; filler-was-everything restore; sentence-case restore |
| `app/domain/services/voice_pipeline/turn_streamer.py` | +20/−2 | Speakable-content test replaces the length threshold |
| `app/domain/services/telephony_session_config.py` | +76 | `_NON_NAME_WORDS`, `_is_implausible_person_name`, plausibility step |
| `app/infrastructure/telephony/telephony_media_gateway.py` | +63/−10 | `arrived_ratio`, `loop_lag_ms`, `first_audio_received_at` |
| `app/utils/event_loop_lag.py` | +92 | New — synchronous reader over the existing heartbeat |
| `app/main.py` | +7 | Heartbeat publishes to the reader |
| `tests/unit/test_audio_gap_is_self_diagnosing.py` | +177 | New — 10 tests |
| `tests/unit/test_short_reply_is_not_deleted.py` | +123 | New — 10 tests |
| `tests/unit/test_lead_name_plausibility.py` | +94 | New — 7 tests |
| `tests/unit/test_llm_guardrails.py` | +12/−4 | Expectation corrected |
| `.gitignore` | +7 | `call_logs2.md` — public repo |

11 files, 718 insertions, 26 deletions.

Totals across the two fix commits: **1,710 insertions across 17 files**
(`git diff --shortstat 4ebdbd55 e17b33d1` reports 2,263 across 18, the
difference being `report4.md` carried in the same fast-forward). **1,076 of
those lines are tests** — 63% of the change.

---

## Appendix D — effective configuration

Read from the live process (`os.getenv` plus the code default that applies when
unset), not transcribed from source.

```
VOICE_OPENING_HELLO_S            =      2.5   opening silence before the first re-greet
VOICE_MID_NUDGE_S                =       16   mid-call silence before a check-in
VOICE_SILENCE_HANGUP_S           =       60   continuous caller silence before auto-close
VOICE_NUDGE_MIN_GAP_S            =     15.0   min gap between MID nudges
VOICE_OPENING_NUDGE_GAP_S        =      2.5   min gap between OPENING re-greets
VOICE_AUDIO_ACTIVE_RMS           =      500   NEW — RMS above which a nudge is suppressed
VOICE_AUDIO_ACTIVE_MAX_AGE_S     =      2.0   NEW — max age of the RMS reading
VOICE_PROMPT_CACHE_ORDER         =     true   NEW — stable-prefix prompt order
DEEPGRAM_MIN_INTERRUPT_WORDS     =  (unset)
FLUX_NUMERALS                    =  (unset)
STT_FAILOVER_ENABLED             =     true   CORRECTED 2026-08-18 — see below
VOICE_GATEWAY_STT_REORDER        =  (unset)

ReconnectPolicy (STT failover)
  reconnect_timeout_seconds      = 0.5
  max_reconnect_attempts         = 1
  audio_buffer_ms                = 500
  failure_threshold              = 3
  recovery_timeout_seconds       = 30.0
  silent_stream_voiced_seconds   = 6.0        NEW

Telephony audio
  RTP_FRAME_MS                   = 20
  AUDIO_CALLBACK_BATCH_FRAMES    = 2
  AUDIO_CALLBACK_INTERVAL_MS     = 40
  _AUDIO_GAP_WARN_MS             = 120        max(120, interval * 3)

Deepgram Flux (per call)
  eager_eot_threshold            = 0.7
  eot_threshold                  = 0.85
  eot_timeout_ms                 = 500
```

The four `NEW` rows did not exist during the run. They are the defaults shipped
afterwards, listed so this file records the configuration the **next** run will
use.

### Correction — `STT_FAILOVER_ENABLED` (2026-08-18)

This table originally reported `STT_FAILOVER_ENABLED = (unset)`. **That was
wrong.** The value is `true` in the server environment, and the wrapper was
active on every call of the run:

```
$ grep -c "^STT_FAILOVER_ENABLED" /opt/talky/backend/.env
1                                       # STT_FAILOVER_ENABLED=true

$ journalctl -u talky-api -u talky-voice-worker \
      --since "2026-08-13 13:00" --until "2026-08-13 16:00" \
    | grep -c stt_resilient_wrapper_active
41

$ ... | grep -m1 stt_resilient_wrapper_active
stt_resilient_wrapper_active primary=flux-flux-general-en secondary=nova-nova-3
```

The error came from reading `os.getenv` in a shell that had not loaded the
service's environment file, and it matters more than a typo would: had it been
true, the silent-stream watchdog shipped in `3205c7ef` would have been
unreachable code in production — a fix that could never fire, described in this
report as live. It also means the two lost calls had a working Nova-3 secondary
sitting behind them the whole time, unused, because nothing raised. That is the
finding, restated: the failover was present, correctly configured, and blind.

The general lesson is the same one as the rest of this document. I asked the
environment a question in a context that could not answer it, and wrote the
answer down. `41 stt_resilient_wrapper_active` lines is evidence about the
running process; `os.getenv` in a detached shell is evidence about the shell.

---

*Report 5 · 2026-08-13 · production HEAD `e17b33d1` · raw logs in `call_logs2.md`*
