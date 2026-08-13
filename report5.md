# Report 5 — the first real canary, and the five defects it found

**2026-08-13.** 40 calls, 13:24–15:05 UTC. Everything below is now live.

---

## 0. Live status

Verified in the running process, not on disk. That distinction has bitten this
project twice, so it is stated first and stated precisely.

| | |
|---|---|
| Production HEAD | **`e17b33d1`** |
| Deployed | 2026-08-13 21:55:43 UTC |
| Preceding wave | `3205c7ef`, deployed 21:18:34 UTC |
| Rollback target | `4ebdbd55` |
| Services | 6/6 active — api, dialer, voice, reminder, C++ gateway, asterisk |
| Health | `{"ready":true,"db":"ok","redis":"ok"}` |
| Gate before each restart | `active_sessions: 0`, no calls in flight |
| Errors since restart | none beyond the perennial "Vonage optional not configured" lines |

Behaviour read out of the deployed code:

```
Sure thing.                      -> 'Sure thing.'                                 (was '.')
Sure thing. The weather today... -> 'The weather today is actually quite nice.'   (was '. The weather...')
Sure, take your time.            -> 'Take your time.'                             (was 'take your time.')
lead first_name 'Call'           -> ''            'Sarah' -> 'Sarah'
event_loop_lag_heartbeat_started period_ms=10
cache_friendly default: True | base first: True | floor last: True
caller_audio_active: nudge->wait  |  60s hangup still absolute
```

Tests across both waves: **5,237 passed.** A baseline run on pristine HEAD
reproduces the same 8 failures and 35 errors — missing secrets in the verify
worktree — so both waves add zero regressions.

Raw logs for the whole run are in `call_logs2.md` (14,441 lines, gitignored:
the repo is public and the journal carries client IPs and tenant UUIDs).

---

## 1. What the run was

The long-standing "0 of 30 canary calls" gap finally closed.

| | |
|---|---|
| Campaign | `INTERNAL-VOICE-VALIDATION` (`c2b6734d`) |
| Calls | 40 · 36 answered · 3 no-answer · 1 voicemail |
| Answered with zero conversation | **2** |
| Code under test | `4ebdbd55` |
| LLM / STT / TTS | `qwen/qwen3.6-27b` · `flux-general-en` (secondary `nova-3`) · `deepgram` |

Measured, from the raw lines:

```
turn latency          n=190   p50 1014ms   p90 1366ms   max 4349ms
  llm_first_token     n=190   p50  788ms   p90  959ms
  tts_first_chunk     n=190   p50  210ms   p90  228ms
groq prompt_time      n=243   p50  614ms   p90  718ms
cache_hit_ratio       279 of 279 = 0.00
telephony_audio_gap   n=421   p50  266ms  (expected 40ms)  36 of 38 calls
silence nudges        35 total — 28 fired while caller RMS > 500
interrupts            200 begun · 139 no-op · 61 real · 1 deduped · max 0.94ms
```

---

## 2. The defects

Five, and three of them share one shape: **a signal everybody trusted turned out
to be incapable of representing the failure.**

### 2.1 STT died silently — 2 of 36 answered calls lost (5.6%)

Flux connected, was handed 400+ audio chunks, and returned **zero transcript
events** — not one StartOfTurn — for the whole call, while the caller talked at
RMS 3504, peak 28988.

```
14:12:17  rms=965   peak=4845    -> SilenceMonitor: "silence (opening)", nudging 'Hello?'
14:12:18  rms=3504  peak=28988   <- caller talking loudly
14:12:20  rms=664                -> nudging 'Hello??'
14:12:24  rms=1520  peak=7758    -> nudging 'Helloooo — are you there?'
14:12:26  caller hangs up
```

The module's own log line says `>500 = speech-likely`. It measured 3504 and
called it silence.

Both safety nets missed it for the same reason: they infer silence from *absent
transcripts*, and a dead stream and a quiet room are the identical observation.

- The failover wrapper's every trigger is an **exception**. A socket that
  answers nothing raises nothing, so it fell through all of them and never
  promoted the armed `nova-3` secondary.
- The silence monitor ran the re-greet ladder to exhaustion over a live human.

**Fix.** They are only separable acoustically, so both now consult energy.
`resilient_stt` counts VOICED audio in against transcripts out and fails over
after 6s, reusing the existing replay buffer so the utterance in flight is
re-transcribed rather than lost. Not wall-clock — a quiet caller accumulates
none of it, so it can never fire on plain silence. Muted frames are excluded: on
a 2-wire line those carry our own TTS at full volume. It re-arms on every
transcript, so a stream that dies at minute nine is caught the same way.

Fixing it in the wrapper rather than in the telephony caller covers telephony,
browser and ask-AI at once.

`silence_action` gained `caller_audio_active`, which suppresses nudging only. It
deliberately does **not** block the 60s hangup: energy on the line is not proof
of a conversation — it is also what a television in the background looks like —
and a bound that noise can extend is not a bound.

### 2.2 Nudging over live speech — 28 of 35 nudges, in 20 of 40 calls

The same blindness without the total loss. 15 fired while RMS was above 1500.
It is what the caller kept saying on the line:

> *"While you were talking, I said I need to ask you something before you
> continue, and you didn't stop there."*

> *"I only heard the one about whether this is an internal test. What were the
> other two?"* — the agent, 14:21

Fixed by the same acoustic guard.

### 2.3 The prompt cache had never hit — ~600ms wasted per turn

**279 of 279** voice LLM calls that day at `cache_hit_ratio=0.00`; **426 of 426**
over seven days. Meanwhile the same Groq account was getting hits of up to
**6656 tokens** on `llama-3.1-8b-instant`. Never a model limitation.

`build_turn_prompt` prepended the per-turn LIVE STATE and CAPTURED blocks at
**character 0**, above an otherwise-identical 8.5k-token prompt. Caches key on
the longest common prefix from the first token, so a block that changes every
turn at the very front means no prefix can ever match.

The bill was the dominant term in call latency: `prompt_time` p50 **614ms of a
788ms** time-to-first-token — about 60% of the 1014ms a caller waits between
finishing their sentence and hearing a reply, spent re-reading a prompt the
provider had read seconds earlier.

**Fix.** Stable-for-the-call blocks first, per-turn blocks last:

```
[base] [end-session] [audio-tags] [accent]      <- identical all call: CACHED
[ask-AI] [knowledge] [CAPTURED] [LIVE STATE]    <- per-turn
[trailing]                                      <- compliance floor keeps LAST
```

Two things that look like risks and are not. **LIVE STATE is promoted, not
weakened** — it moves from position 0 to a few hundred tokens from the end, and
this codebase's own hard-won finding is that the trailing slot wins; that is
precisely why `trailing_block` exists. **The compliance floor keeps the final
word**, unchanged.

`VOICE_PROMPT_CACHE_ORDER=false` restores the old order without a redeploy, and
the legacy path stays executable so the revert switch is tested rather than
merely described.

### 2.4 A stray quantifier was deleting whole replies

Two production turns were transcribed as literally `.`, and one as
`. The weather today is actually quite nice.` — the turn before it having said
`Alright, the weather today is actually quite nice.`

```
turn 13  said='Alright, the weather today is actually quite nice.'
turn 14  said='. The weather today is actually quite nice.'
```

`^Sure thing[!,]?\s*` was the only filler pattern using `\s*` rather than `\s+`.
`\s*` matches the empty string, so **"Sure thing."** lost its filler and kept the
full stop — leaving `"."`.

And `turn_streamer` dropped any sentence shorter than six characters. So that
turn produced **no audio at all**. The model answered, and the answer was
deleted between the LLM and the wire. From the caller's side: dead air,
mid-conversation.

The length test was independently wrong and was about to get worse. `"Yes."`,
`"Okay."`, `"Sure."`, `"Got it."` are all under six characters — and the
answer-first rule shipped days earlier explicitly asks the model to reply
plainly in one short sentence, which is exactly the shape it deleted. A length
threshold was always the wrong instrument: the question is whether there is
anything to say, not how many characters it takes to say it.

**Fixing the pattern first moved the bug rather than removing it.** With the
two-word pattern no longer matching `"Sure thing."`, it fell through to the
shorter `^Sure` pattern and became `"Thing."`. A test caught it. The
longest-match ordering only works if the longer pattern can match, so every
filler now terminates on `(?:\s+|$)`, and a filler that *was* the whole message
restores the original.

This also explains the lowercase turns across the run — `take your time.`,
`go ahead.`, `what did you have in mind?`. Stripping the filler stripped the
capital it was carrying. Sentence case is now restored after a strip.

### 2.5 Every call opened "Hi, is this Call?"

The campaign's only lead was `first_name='Call'`, `last_name='30'` — somebody
had typed "Call 30" (as in *call thirty numbers*) into the name field. The
digits were already stripped by the shape allowlist; "Call" was not.

Person names are now checked for plausibility and dropped to `""`, which the
pipeline already handles: the agent falls back to the other name field, or opens
without a name.

The word list is deliberately short and biased toward leaving names alone.
Months (May, June, April), virtue names (Grace, Hope, Faith) and
surnames-as-forenames (Lee) are exactly what a longer list starts eating.
**A security test caught the first version rejecting "张 伟"** — in Han, Kana and
other logographic scripts one glyph is a whole name, so the too-short rule now
applies only to ASCII. Addressing someone by the wrong word is a small
embarrassment; refusing to use a real person's name is a worse one.

---

## 3. The 421 audio gaps — measured, not fixed

p50 **266ms** against an expected 40ms, across **36 of 38 calls**, ~11 per call.
The warning named three possible causes and could distinguish none of them; its
own comment records that naming a suspect had already sent one investigation the
wrong way.

**My first hypothesis was also wrong.** I suspected these drove the STT mangling
("doing great" → *"Spiritville"*, "voice test" → *"swice test"*). Measured
instead, by reconstructing delivery from the `audio_level` sample counts:

```
audio delivery ratio (arrived / expected), n=2395 one-second windows
  p50 = 1.000    mean = 1.0089
  windows below 0.90 :  81 (3.4%)
  windows above 1.10 :  74 (3.1%)
per-call total, n=38 calls: p50 1.011, mean 1.0181
```

Near-symmetric. **Nothing was ever lost** — audio arrives in bursts. "RTP loss"
could have been struck off on day one if the line had carried the number.

So this change does not assert a cause either. It makes the **next** occurrence
answer the question. Each warning now carries:

- `arrived_ratio` — ~1.0 means late, not lost; a sustained dip is real loss and
  a different problem entirely.
- `loop_lag_ms` and `stall=ours|not-ours` — whether this process could be
  scheduled at that instant. High: the callback was late because we were busy,
  and it is ours to fix. ~0: we were idle and waiting, so it arrived late from
  outside.

**It reuses the existing heartbeat.** I wrote a sampler before discovering that
`main._event_loop_lag_heartbeat` already existed — 10ms absolute deadlines,
stall resync, feeding a Prometheus histogram — and deleted mine. A second ticker
measuring the same loop would only disagree with the first under load. What was
missing was never another measurement, but a way to read the current one
**synchronously**, at the moment of the log call. `unmeasured` is kept distinct
from `0.0`: "we did not look" and "the loop was fine" are different claims.

Confirmed live: `event_loop_lag_heartbeat_started period_ms=10`.

---

## 4. What held, and what to distrust

**Held.** Openers are permission-based, never the "bad time?" form. Answer-first
works — a direct question gets a plain answer with nothing attached, repeatedly,
across many calls. The interrupt operation is healthy: max 0.94ms, one dedupe,
no races.

**Distrust this number.** 200 interrupts ran; **139 (70%) were no-ops**. So
`barge_in_detected=201` must never be read as "the agent was talked over 201
times" — only ~52–61 were real.

**And this one.** 194 of ~250 turns tripped `[SLOW]`. A flag that fires on ~78%
of turns has stopped discriminating. Worth recalibrating once the cache fix has
moved the underlying numbers — not before, or we would just be re-tuning to a
distribution that is about to change.

---

## 5. Still open

**4 recordings destroyed.** `recording_disclosure_speaking` (37) → caller barges
in → `recording_disclosure_interrupted` (4, "not retried") → audio discarded.
Working as designed, but it destroys evidence on exactly the calls where someone
interrupted.

This one needs a decision rather than a patch. A "retry from the top" fix was
already built and **reverted on 2026-08-11** — two traced calls showed the
callee hanging up ~2s after hearing the notice restart. Every remaining option
changes *when a notice counts as delivered*, which is a retention-policy call.

Recommendation: finish the notice at the start of the agent's **next** turn —
never over the caller — and keep the recording once it completes. That gets the
notice genuinely heard, which was always the point, without repeating the
reverted mistake.

**STT accuracy on this line.** "doing great" → *"Spiritville"*, "voice test" →
*"swice test"*, "Messi" → *"messy"*. Now known **not** to be the audio gaps.
Unattributed.

**Eight tracebacks**, all the known client-disconnect `RuntimeError`. Unchanged.

---

## 6. What to watch on the next calls

Two numbers will confirm or refute the two biggest changes, and both are in the
logs:

1. **`cache_hit_ratio` should stop being `0.00`.** Groq caches in 512-token
   blocks; expect roughly 6.5k of the 8.5k prompt to hit. If it does,
   `prompt_time` falls from ~614ms and mouth-to-ear should drop from ~1014ms
   toward the low 600s.
2. **`resilient_stt_stream_silent`** appearing is a **success** line, not an
   error — the watchdog caught a dead stream and failed over, and the call
   survived.

The prompt reorder is the one behavioural risk. If the agent starts
re-introducing itself mid-call or drifting its name or role, that is the reorder:
set `VOICE_PROMPT_CACHE_ORDER=false` and restart. No redeploy needed.

---

## 7. The pattern

Report 4 closed on *"a fix verified in the wrong place is indistinguishable from
no fix at all."* This run has its own version, and it showed up three times:

**A signal that cannot represent the failure will report success.**

Absent transcripts cannot distinguish a dead microphone from a quiet room. A
gap timer cannot distinguish late from lost. A character count cannot
distinguish an empty reply from a short one. In each case the code was working
exactly as written and reporting exactly what it could see — which was not
enough to be right.

The fix in each case was not a better threshold. It was a second, independent
kind of evidence: acoustic energy beside transcripts, delivered-ratio beside
elapsed time, speakable-content beside length. Where that evidence did not exist
yet — the audio gaps — the honest move was to instrument rather than to guess,
and to say so.

Two of my own hypotheses died to measurements in this report. Both are left in
rather than quietly removed, because the wrong turn is the part worth
remembering.
