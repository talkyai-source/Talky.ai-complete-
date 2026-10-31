# Report 6 — Nine Calls, Ten Dimensions, and Two Features That Were Never Running

**Date:** 2026-08-17
**Production HEAD at start of day:** `04d8d4b8`
**Production HEAD now:** `3fa5cadf`
**Observation window:** 20:37:02 → 21:28:12 UTC
**Calls examined:** 9 (8 answered, 1 no-speech)
**Journal size:** 44,413 lines
**Primary tool:** `scripts/call_scorecard.py`

---

## Table of contents

```
 0.  How to read this report
 1.  Executive summary
 2.  Method, and how to reproduce every number here
 3.  The call-by-call scorecard
 4.  DIMENSION 1 — STT availability
 5.  DIMENSION 2 — Nudge-over-speech
 6.  DIMENSION 3 — Prompt cache hit ratio
 7.  DIMENSION 4 — Mouth-to-ear latency
 8.  DIMENSION 5 — Prompt / state regressions
 9.  DIMENSION 6 — Short replies
10.  DIMENSION 7 — Invalid lead-name handling
11.  DIMENSION 8 — Direct-question behaviour
12.  DIMENSION 9 — Transfer behaviour
13.  DIMENSION 10 — Genuine audible barge-in
14.  SPECIAL ASK A — What the caller heard during an interruption
15.  SPECIAL ASK B — Proving the STT watchdog promotes Nova
16.  SPECIAL ASK C — Recording-disclosure retention stays open
17.  The failure I have to report: two features that never executed
18.  Web research — what the standard is, and what our provider supports
19.  Register of corrections to my own earlier claims
20.  Open items and recommendations
21.  Change inventory
APPENDIX A — Measured distributions (charts)
APPENDIX B — Per-call event timelines
APPENDIX C — Commands behind every figure
APPENDIX D — Effective configuration
```

---

## 0. How to read this report

You asked for ten dimensions measured per call; the caller's experience of an
interruption rather than our internal cancellation time; a proof that the STT
watchdog promotes Nova while preserving the buffered utterance; and the
recording-disclosure problem left open pending a product decision.

Sections 4–16 answer those one at a time. Every figure is followed by the
command that produced it and the output that came back. Appendix A holds
distributions computed from the journal — not drawn by hand, and with bucket
edges printed so any bar can be re-derived. Appendix B holds the full event
timeline of all nine calls, 616 lines, verbatim.

Three conventions matter:

* **`?` means the journal cannot answer.** It never means zero. The scorecard's
  own tests enforce that distinction, because the recurring defect across
  reports 4, 5 and 6 has been signals that could not represent the thing they
  were reporting on.
* **Every verdict is written on clean calls too.** A line that appears only on
  failure cannot be distinguished from a check that never ran.
* **Where I was wrong, it says so.** Section 19 is a register of corrections to
  my own earlier claims, including two shipped fixes that did nothing.

Section 17 is the one to read if you read only one section.

---

## 1. Executive summary

**What went right.**

The silent-stream STT watchdog fired on **three separate live calls** and
rescued all three. Each one replayed exactly twelve buffered chunks into
Nova-3 — the number a 500ms buffer at 40ms/chunk predicts, and the number the
unit test asserts. Two of the three were mid-call deaths at 97 seconds and 188
seconds, which only the re-arming counter catches. That is your "prove the
watchdog promotes Nova and preserves the buffered utterance" ask, answered on
production traffic rather than in a harness.

Cancelled audio did **not** resume: 27 interrupts across two audited calls,
`resumed_chunks=0`, stated positively rather than inferred from an absence of
alarms. Zero failed interrupts across 108 interrupt events.

Short replies survive — five-character replies were spoken on two calls, which
before `e17b33d1` would have produced no audio at all. Answer-first behaviour
holds: every direct question in the transcripts got a plain answer with no
counter-question attached, including the price question, which was declined
rather than invented.

**What went wrong.**

Two features I previously reported as live had **never executed once in
production**. `CallSession` is a pydantic v2 model that rejects undeclared
attribute assignment; both features wrote non-underscore attributes inside a
`try/except`, so the exception was swallowed on every audio frame of every call.
The acoustic nudge guard (shipped 2026-08-13, reported live in report 5) has
never suppressed a single nudge. The caller voice-onset anchor (shipped this
morning) recorded nothing, which is why the one figure you specifically asked
for — caller-speech-to-audible-stop — is still `None` on all 45 occurrences.

**What is unfixable by the route I proposed.**

The prompt cache is at 0% across 619,588 prompt tokens, and my report-5 root
cause was wrong. Groq does not support prompt caching on the model we run.
`prompt_time` p50 629ms is 98% of a 639ms time-to-first-token, and no ordering
change can recover it.

---

## 2. Method, and how to reproduce every number here

Everything in this report derives from one journal capture:

```
$ journalctl -u talky-api -u talky-voice-worker --since today --no-pager > today.txt
$ wc -l today.txt
44413 today.txt
```

The scorecard groups by `[call=<uuid>]`, falling back to `call=`/`call_id=` in
the message body, and resolves truncated 12-character ids against known calls:

```
$ python3 scripts/call_scorecard.py today.txt                 # table + summary
$ python3 scripts/call_scorecard.py today.txt --detail dec0bb16
$ python3 scripts/call_scorecard.py today.txt --since 21:05:00
$ python3 scripts/call_scorecard.py today.txt --format csv
```

The distributions in Appendix A come from a companion script that reads the
same file. The timelines in Appendix B are the raw journal lines, filtered to
lifecycle events and stamped with elapsed seconds from the start of each call.

**A note on redaction.** Caller speech is deliberately redacted in logs
(`transcript='[redacted chars=21 sha=e7cb7e0e]'`). The agent's side is in the
clear via `llm_response`. Flux end-of-turn text does appear, which is how the
direct-question analysis in section 11 is possible at all.

---

## 3. The call-by-call scorecard

```
call       start      stt             stt-ok nudge  prompt tok   m2e   ttft  reply  barge  gaps
6090952e   20:37:02   ok              1      1/?    ?            ?     ?     -      0/1    0
dec0bb16   20:38:59   FAILOVER>nova   1      1/?    8385>8507    1029  638   27c    2/7    20
ae686b21   20:40:54   ok              1      1/?    8401>8701    1051  644   5c     3/14   25
defdd260   20:44:47   ok              1      3/?    8428>8746    1573  627   5c     3/10   26
a195284b   20:51:16   ok              1      1/?    8398>9044    ?     656   8c     1/16   47
b3350aee   21:02:20   ok              1      1/?    8401>8777    988   637   15c!   4/12   23
------------------------------- 21:05 deploy -------------------------------
ddf2c92c   21:19:11   FAILOVER>nova   1      1/0    8399>8545    1004  620   15c    9/21   31
319debb3   21:23:33   no-speech       0      0/0    ?            ?     ?     -      -      0
b6d354d9   21:24:21   FAILOVER>nova   1      1/0    8400>8751    999   629   10c    13/24  38
```

Column meanings:

| column | meaning |
|---|---|
| `stt` | STT verdict — `ok`, `FAILOVER>nova`, `DEAF!`, `no-speech`, `BOTH-DEAF` |
| `stt-ok` | count of `t_stt_first_final` — did STT ever produce a final |
| `nudge` | nudges spoken / nudges suppressed by the acoustic guard |
| `prompt tok` | prompt tokens, first turn → last turn |
| `m2e` | first-turn mouth-to-ear, ms (`speech_to_audio_ms`) |
| `ttft` | LLM time-to-first-token p50, ms |
| `reply` | shortest reply actually spoken; `!` flags a silent turn |
| `barge` | real interrupts / total interrupt events |
| `gaps` | `telephony_audio_gap` warnings |

Aggregate for the whole day:

```
CALLS                     9
  STT resilient wrapper   13 activations  primary=flux-flux-general-en secondary=nova-nova-3
  STT lost/deaf           0  (0%)
  STT failed over         3  -> deepgram-nova:nova-3
```

---

## 4. DIMENSION 1 — STT availability

### 4.1 Result

Zero calls lost. Three calls rescued by failover. No call reached the
`DEAF!`/`BOTH-DEAF` state that defined the 2026-08-13 incident.

```
$ grep -E "resilient_stt_stream_silent|resilient_stt_failed_over_to" today.txt

20:40:28 ERROR [resilient_stt] [call=dec0bb16-7be8-4512-a5d3-a19336df82da]
  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
  — 6.0s of caller speech went in and no transcript event came back;
    treating the stream as dead and failing over
20:40:29 INFO  [resilient_stt] [call=dec0bb16-7be8-4512-a5d3-a19336df82da]
  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12

21:20:48 ERROR [resilient_stt] [call=ddf2c92c-eeb9-41df-8fb2-4a68931fa17c]
  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
21:20:48 INFO  [resilient_stt] [call=ddf2c92c-eeb9-41df-8fb2-4a68931fa17c]
  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12

21:27:29 ERROR [resilient_stt] [call=b6d354d9-44c5-4f62-b35a-68bc027f5f82]
  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
21:27:29 INFO  [resilient_stt] [call=b6d354d9-44c5-4f62-b35a-68bc027f5f82]
  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
```

### 4.2 The acoustic evidence

The watchdog counts VOICED audio, not wall-clock, precisely so a quiet caller
never trips it. Here is what the line was carrying in the seconds before
`ddf2c92c` failed over:

```
$ grep ddf2c92c today.txt | grep -o 'audio_level.*'

audio_level window_s=1.0 chunks=2187 rms=226  peak=1116  samples=16000
audio_level window_s=1.0 chunks=2212 rms=422  peak=2569  samples=16000
audio_level window_s=1.0 chunks=2238 rms=355  peak=2381  samples=16640
audio_level window_s=1.0 chunks=2264 rms=177  peak=1109  samples=16640
audio_level window_s=1.0 chunks=2289 rms=637  peak=4850  samples=16000
audio_level window_s=1.0 chunks=2315 rms=576  peak=3023  samples=16640
audio_level window_s=1.0 chunks=2340 rms=210  peak=1191  samples=16000
audio_level window_s=1.0 chunks=2366 rms=1238 peak=10463 samples=16640
audio_level window_s=1.0 chunks=2392 rms=1023 peak=7654  samples=16640
```

Peaks to 10,463 and windowed RMS repeatedly above the 500 speech threshold.
Flux had the audio and returned nothing. This is exactly the observation that
was indistinguishable from a quiet room before the watchdog existed.

Across all calls, the RMS distribution (Appendix A, Chart 6) shows p50 494 and
p90 2,195 over 1,196 one-second windows — i.e. the callers were genuinely
talking for most of these calls, so the watchdog was operating on real speech,
not on line noise.

### 4.3 `buffered_chunks=12` is the design confirming itself

A 500ms replay buffer at 40ms per chunk holds twelve chunks. The
`ReconnectPolicy` default is `audio_buffer_ms=500`; the production frame is
640 samples at 16 kHz = 40ms. Twelve is therefore the predicted value, it is
what the unit test asserts, and it is what production produced — three times,
independently, on three different calls.

That is the "preserves the buffered caller utterance" half of your ask,
observed live. The offline test in section 15 proves the stronger claim: that
the *contents* of those twelve chunks arrive in order.

### 4.4 Two of three were mid-call deaths

| call | connected | failed over | elapsed | turns before failover |
|---|---|---|---|---|
| `dec0bb16` | 20:38:59 | 20:40:28 | **89s** | 6 |
| `ddf2c92c` | 21:19:11 | 21:20:48 | **97s** | 4 |
| `b6d354d9` | 21:24:21 | 21:27:29 | **188s** | 7 |

None of these was a turn-0 death. In every case Flux had been transcribing
normally for over a minute and then went silent. The watchdog only catches that
because `observe_transcript()` zeroes the voiced-audio counter on every
transcript event, so a stream that dies at minute three is detected exactly like
one that was never alive.

The 2026-08-13 incident was the turn-0 case, which is the easier one. Today's
were the harder one, and the re-arm property held.

### 4.5 A caveat I owe you

Three failovers across eight answered calls (37.5%) is far above the
two-in-thirty-six (5.6%) that started this work. I cannot tell you from here
whether Flux is degrading or whether the watchdog is tripping on transient
stalls that would have recovered on their own.

From inside the wrapper, "the socket is dead" and "the socket has been quiet
through six seconds of speech" are the same observation. It deliberately treats
them the same, because the costs are wildly asymmetric: a false trip costs one
extra Deepgram socket and a replayed half-second (degraded, bounded), while a
miss costs the entire call, which is what 2026-08-13 actually cost.

The outcome was correct in all three cases — every call continued and completed
normally after promotion. The **rate** is worth watching, and the scorecard now
makes it a one-line check rather than an archaeology exercise.

---

## 5. DIMENSION 2 — Nudge-over-speech

### 5.1 Result

```
$ grep nudge_audit today.txt

[SilenceMonitor] ddf2c92c-eeb — nudge_audit nudges=1 suppressed=0
[SilenceMonitor] 319debb3-5c8 — nudge_audit nudges=0 suppressed=0
[SilenceMonitor] b6d354d9-44c — nudge_audit nudges=1 suppressed=0
```

Two nudges spoken across three post-deploy calls. **Zero suppressed.**

### 5.2 Why zero is not the good news it looks like

On 2026-08-13, 28 of 35 nudges (80%, across 20 of 40 calls) landed on a caller
who was audibly mid-sentence. The acoustic guard for that shipped the same day
and report 5 described it as live.

It was not. See section 17. `session.last_audio_rms` was never written, so
`_audio_active` read False on every tick of every call, and the guard has never
suppressed a single nudge in its four days of existence. `suppressed=0` here is
not a statement that the guard had nothing to do — it is a statement that the
guard was not running.

It is running as of `3fa5cadf`. It has not yet been exercised by a call.

### 5.3 The nudges themselves were correct

Both nudges landed in genuine silence, and used the right ladder:

```
+  3s  20:37:05  [SilenceMonitor] 6090952e-457 — silence (opening), nudging: 'Hello?'
+ 88s  20:40:27  [SilenceMonitor] dec0bb16-7be — silence (mid), nudging: 'Still there?'
```

`opening` selects the re-greet ladder ("Hello?"), `mid` selects the check-in
("Still there?"). The 2026-08-12 regression, where a mid-ladder phrase leaked
into an opening state and produced a needy re-offer before the prospect had
spoken, does not recur.

### 5.4 The audit line is new and deliberately unconditional

`nudge_audit` is written on **every** call, including `nudges=0 suppressed=0`
for `319debb3`, a call where nobody spoke. A verdict that appears only when
something goes wrong is indistinguishable from a verdict that was never
computed — which is the precise reporting failure that let two dead STT streams
pass for quiet callers on 2026-08-13.

It is emitted from the `finally` that cancels the silence-monitor task, because
the monitor is normally ended by `task.cancel()` at hangup and anything relying
on falling out of the loop would never run.

---

## 6. DIMENSION 3 — Prompt cache hit ratio

### 6.1 Result: zero, across the whole day

```
$ grep "llm_usage .*partial=False" today.txt | awk '{...}'
turns=74 prompt_tokens=619588 cached_tokens=0 turns_with_hit=0
```

**Zero cache hits across 619,588 prompt tokens over 74 non-partial LLM turns.**
A representative line in full:

```
llm_usage model=qwen/qwen3.6-27b partial=False prompt_tokens=6498 cached_tokens=0
  cache_hit_ratio=0.00 completion_tokens=2 req_id=unknown queue_time=0.090
  prompt_time=0.518 completion_time=0.004 total_time=0.522
```

Per call:

| call | turns | prompt tokens | cached | ratio |
|---|---|---|---|---|
| `dec0bb16` | 6 | 50,570 | 0 | 0.0% |
| `ddf2c92c` | 6 | 50,756 | 0 | 0.0% |
| `b6d354d9` | 11 | 94,375 | 0 | 0.0% |
| `b3350aee` | 10 | 86,450 | 0 | 0.0% |
| `a195284b` | 22 | 193,181 | 0 | 0.0% |

### 6.2 My report-5 root cause was wrong

I told you the 0% was caused by the per-turn LIVE STATE block sitting at
character 0, breaking the cacheable prefix, and that reordering would recover
roughly 600ms per turn. **A fix shipped on that reasoning on 2026-08-13.** Four
days later the hit rate is still exactly zero.

The decisive test — two byte-identical 2,418-token prompts sent back to back,
where the second must hit cache if caching exists at all:

```
qwen/qwen3.6-27b       first  status=200 prompt_tokens=2418 cached=None
                       second status=200 prompt_tokens=2418 cached=None
llama-3.1-8b-instant   status=404 — "The model `llama-3.1-8b-instant` does not
                                      exist or you do not have access to it."
```

Two things fall out:

* **`cached` is `None`, not `0`.** Groq does not report a
  `prompt_tokens_details.cached_tokens` field for this model at all. There is no
  prefix arrangement that produces a hit, because the feature is not present.
* **The control no longer exists.** `llama-3.1-8b-instant`, whose 6,656-token
  hits I cited as proof that "caching DOES work on this account", is not on the
  account. The comparison that anchored the entire diagnosis was never valid.

### 6.3 The vendor documentation agrees

See section 18 for the full research. In short: Groq supports prompt caching on
the **GPT-OSS family only** (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
`openai/gpt-oss-safeguard-20b`), with a 2-hour TTL, a 128–1024 token minimum
depending on model, and explicit guidance to put static content first and
variable content last.

Our prompt already has that order. The 2026-08-13 reorder is correct by
construction and would pay off immediately on a supported model. It just cannot
pay off here.

### 6.4 What it would be worth

Measured on this account at production prompt size (7,281 tokens),
second identical request:

| model | cold TTFT | warm TTFT | cached tokens |
|---|---|---|---|
| `qwen/qwen3.6-27b` (current) | 697ms | 672ms | none |
| `openai/gpt-oss-120b` (effort=low) | 451ms | **102ms** | 7,168 / 7,281 |
| `openai/gpt-oss-20b` (effort=low) | 475ms | 119ms | 7,168 / 7,281 |

98% of the prompt cached, and time-to-first-token collapsing from 672ms to
102ms on every turn after the first — without touching a word of tuned prompt.

**I have not switched the voice default.** GPT-OSS was removed from this menu on
2026-06-25 for stacking questions and NATO-spelling on voice calls, and a 6×
latency win does not overturn a conversation-quality finding. Both models are
back in the menu as **preview**, with the measurement and the caveat written
into the description so the choice is made with eyes open. Section 20 sets out
what would settle it.

---

## 7. DIMENSION 4 — Mouth-to-ear latency

### 7.1 Headline numbers

```
first-turn mouth-to-ear p50   1002ms   (post-deploy: 1004ms, 999ms)
LLM TTFT p50                   639ms   (n=114, p90 738ms, max 908ms)
prompt_time p50                629ms   (n=93,  p90 706ms, max 755ms)
TTS first chunk               ~200ms   consistently
STT first final                  ~0ms  on most turns
```

**Prefill is not part of the latency. It is essentially all of it.** 629ms of a
639ms time-to-first-token. Chart 1 and Chart 2 in Appendix A show the two
distributions are the same shape offset by about ten milliseconds.

### 7.2 Every slow turn has one fingerprint

```
$ grep -o "voice_slow_turn.*" today.txt

call=ae686b21 turn=9 response_start_ms=1762.0 stt_first_ms=0.2
  llm_first_token_ms=1502.2 tts_first_chunk_ms=215.8 llm_total_ms=8943.6
call=defdd260 turn=1 response_start_ms=1573.0 stt_first_ms=75.0
  llm_first_token_ms=1346.8 tts_first_chunk_ms=198.6 llm_total_ms=7915.0
call=defdd260 turn=2 response_start_ms=2072.2 stt_first_ms=237.7
  llm_first_token_ms=1859.9 tts_first_chunk_ms=206.6 llm_total_ms=9176.5
call=a195284b turn=1 response_start_ms=1608.4 stt_first_ms=0.4
  llm_first_token_ms=1399.3 tts_first_chunk_ms=203.2 llm_total_ms=7711.8
```

Recognition instant. Synthesis around 200ms, consistently, never the outlier.
The LLM 1.3–1.9 seconds every single time. There is no second bottleneck to
find, and no amount of work on STT or TTS will move the number the caller feels.

### 7.3 Prompt size, measured per call

The `prompt tok` column is new, and it corrected a figure I had produced myself.
Composing a bare `lead_gen` prompt offline gives 6,498 tokens; production on
this campaign starts at **~8,400**.

```
call        turns   first    last  growth   trace
a195284b       22    8398    9044    +646   ▁▁▂▂▂▃▃▄▅▅▅▆▆▆▇▇▇▇▇▇█▇
ae686b21       14    8401    8701    +300   ▂▂▂▃▃▄▄▄▅▆▆▇▇█
b3350aee       10    8401    8777    +376   ▂▄▄▅▆▆▇▇██
b6d354d9       11    8400    8751    +351   ▂▂▃▄▄▅▆▆▇▇█
ddf2c92c        6    8399    8545    +146   ▃▃▅▅▆█
dec0bb16        6    8385    8507    +122   ▃▃▄▅▇█
defdd260       13    8428    8746    +318   ▃▂▃▃▄▄▅▆▆▇▇▇█
```

Decomposition:

| component | tokens | evidence |
|---|---|---|
| persona + guardrails base | ~6,500 | offline `compose_prompt` |
| per-call blocks (knowledge, live state, captured, trailing) | ~1,900 | the difference |
| history growth across 22 turns | ~650 | `8398 → 9044` |

**History is not the lever.** Twenty-two turns of real conversation added 646
tokens; the constant preamble is thirteen times that before the caller has said
a word. Shrinking `VOICE_MAX_HISTORY_PAIRS` (currently 20 pairs) would buy
almost nothing measurable and would cost the agent its memory of the call —
which has a known failure mode, re-asking questions the caller already answered.

The knowledge block is measurable and substantial:

```
campaign_knowledge_injected campaign=c2b6734d-899 mode=inline chars=3328
```

### 7.4 The "developer debris" hypothesis, measured and discarded

A tuned prompt accumulates notes aimed at engineers rather than at the model.
It is a real pattern, and there is an instance of it in ours:

```
STAGE 1 — OPEN (2026-08-11: you do NOT speak first anymore. A bare pickup
greeting ... see telephony_session_config.build_telephony_greeting.
```

So I measured it rather than assuming it mattered:

```
ISO date annotation           1 hits     12 chars  ~ 3 tok
source file/symbol ref        0 hits      0 chars  ~ 0 tok
A/B evidence prose            4 hits     60 chars  ~15 tok
LINES containing a date or code reference: 1  73 chars  ~18 tokens
EXACT DUPLICATE LINES (>40 chars): 0 distinct, 0 chars wasted
```

**Eighteen tokens.** The hypothesis was right in kind and irrelevant in
magnitude. Zero duplicated lines, which is a good sign about the prompt's
general condition.

### 7.5 Where the mass actually is

```
section                                              chars   ~tok      %
## COMMUNICATION PRINCIPLES (apply to every reply)  14,191  3,492  53.7%
## HARD RULES — these override everything below      2,474    609   9.4%
## NON-NEGOTIABLES                                   1,434    353   5.4%
## FACTS — SOURCE OF TRUTH                           1,247    307   4.7%
## WRONG PERSON / GATEKEEPER                           912    224   3.5%
## SOUND HUMAN, NOT SCRIPTED                           836    206   3.2%
## ENDING THE CALL                                     809    199   3.1%
## CORE DETAILS                                        727    179   2.8%
## HOW YOU SELL                                        553    136   2.1%
## HESITATION / SOFT OBJECTION                         526    129   2.0%
## REGULATED NICHES                                    467    115   1.8%
## STAYING ON TRACK                                    422    104   1.6%
## PRIVACY                                             374     92   1.4%
## FINAL RESPONSE CONTRACT                             373     92   1.4%
## HANDOFFS                                            302     74   1.1%
(preamble)                                             266     65   1.0%
## HANDLING INTERRUPTIONS                              247     61   0.9%
## GRACEFUL EXIT                                       244     60   0.9%
```

Over half the base prompt is one block of stage-by-stage conversational
instruction. **I have not cut it.** It is tuned content, produced by a 96-call
offline matrix and a 48-call ablation, with findings as specific as *the price
guard must sit adjacent to the injected knowledge or small models invent
figures (11/12 → 0/12)*.

Trading a measured 300ms for an unmeasured regression in conversation quality,
on my own judgement, in an afternoon, with no A/B, is not engineering. It is
left open as a decision with the data attached rather than a change attached.

---

## 8. DIMENSION 5 — Prompt / state regressions

### 8.1 Result: none

```
zero_token_turns                 0
composition failures             0
knowledge injected              10 calls, mode=inline, chars=3328 consistently
telephony_prompt_composed       persona=lead_gen agent=Alex company=Talk-Lee kd=True
```

No prompt failed to compose. No turn ran with an empty prompt. Knowledge
injection is stable at 3,328 characters across every call, which is what you
want to see — a varying figure would indicate the KB query returning different
results per call.

### 8.2 The attribution gap, and why one line keeps none

The scorecard originally reported `knowledge=0` and `composed=0` for **every**
call. That was neither a product fault nor a parser bug: both lines log with
`[call=-]`.

```
[req=-] [call=-] telephony_prompt_composed persona=lead_gen agent=Alex ...
[req=-] [call=-] campaign_knowledge_injected campaign=c2b6734d-899 mode=inline chars=3328
```

`campaign_knowledge_injected` has a call session in scope and now uses it,
plus reports the resulting prompt size:

```python
logger.info(
    "campaign_knowledge_injected campaign=%s mode=%s chars=%d prompt_chars=%d",
    campaign_id[:12], mode, len(tree), len(call_session.system_prompt or ""),
    extra={"call_id": getattr(call_session, "call_id", None)},
)
```

`telephony_prompt_composed` **deliberately keeps none**. It runs while building
the session config, before a call exists. Threading a call id through a public
builder for the sake of a log line is the tail wagging the dog, and the per-call
view is already available from `llm_usage`, which does carry one. It gained
`prompt_chars` instead — the actionable number given section 7 — and a comment
explaining the absence, so it reads as a decision rather than an oversight.

---

## 9. DIMENSION 6 — Short replies

### 9.1 Result: the fix holds, with production evidence

```
$ grep -o "llm_response turn=[0-9]* said=.*" today.txt | sort -t= -k3 | head

llm_response turn=7  said='Blueprint.'
llm_response turn=0  said='Alex, from Talk-Lee.'
llm_response turn=9  said="Yeah, I'm here."
llm_response turn=4  said="Yeah, I'm still here."
llm_response turn=4  said='Yeah, I heard that.'
llm_response turn=8  said='Yeah, I detected that.'
```

Two calls (`ae686b21`, `defdd260`) spoke replies of **five characters**. Before
`e17b33d1`, `turn_streamer` dropped any sentence shorter than six characters, so
"Yes." and "Okay." produced **no audio at all** — the turn ended in silence and
the caller heard nothing. This is the first production evidence that fix holds.

### 9.2 Reply length distribution

```
reply length   n=80  min=1w  p50=8w  p90=16w  max=22w
       0-2      w   | ██████████████████████████                    7    8.8%
       2-4      w   | █████████████████████████████████████████    11   13.8%
       4-6      w   | █████████████████████████████████████████████ 12   15.0%
       6-8      w   | ██████████████████████████████                8   10.0%
       8-10     w   | ██████████████████████████████                8   10.0%
      10-13     w   | ██████████████████████████████████████████████14   17.5%
      13-16     w   | █████████████████████████████████████████████ 12   15.0%
      16-20     w   | ██████████████████████                        6    7.5%
      20-25     w   | ███████                                       2    2.5%
```

**Median eight words.** The longest reply of the day was twenty-four:

> *"Hmm, this is just a system validation call, so I can't help with money — is
> there something else on your mind?"*

That distribution retracts my earlier claim that the agent was delivering six-
to-seven-second monologues driving the interrupt volume. Six seconds is simply
how long fifteen to twenty words takes to say. I had extrapolated a population
from four tail samples — the exact error the scorecard exists to prevent — on
the same day I shipped it. Section 19 records the retraction.

### 9.3 One silent turn, and it was not what the label said

`b3350aee` carries `15c!` — the `!` flags a silent turn:

```
turn_silent_reason call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd
  reason=provider_empty_stream
```

That label was wrong, and chasing it nearly shipped a regression. Full analysis
in section 13.4.

---

## 10. DIMENSION 7 — Invalid lead-name handling

### 10.1 Result: clean, nothing to reject

```
$ grep -c "call_target_field_dropped" today.txt
0
$ grep -c "call_target_field_sanitized" today.txt
0
```

The plausibility guard shipped on 2026-08-13 had nothing to do today. The
defect it exists for — `first_name='Call'`, `last_name='30'`, so all forty
canary calls opened *"Hi, is this Call?"* — does not recur.

### 10.2 The openers, verbatim

```
llm_response turn=0 said='Hey there — this is Alex from Talk-Lee, just checking in on a quick voice test. Got a sec?'
llm_response turn=0 said='Alex, from Talk-Lee.'
llm_response turn=0 said="Good, thanks. I'm Alex from Talk-Lee — do you have a minute?"
```

Every one is permission-based, one breath, name → light ask → reason. None uses
the "caught you at a bad time?" form, which the prompt-craft audit established
as the worst-converting family. The 2026-08-11 opener redesign is holding.

---

## 11. DIMENSION 8 — Direct-question behaviour

### 11.1 Why this scores `?` in the table

Caller speech is redacted in logs by design:

```
transcript='[redacted chars=21 sha=e7cb7e0e]'
```

The scorecard therefore prints `?` rather than a guess. But Flux end-of-turn
text *is* logged, and the agent's replies are in the clear, so the exchange can
be reconstructed for these calls.

### 11.2 Reconstructed exchanges

```
CALLER  20:39:42  Flux EndOfTurn: "Yeah. It's coming very good. How about my wife?"
AGENT   20:39:46  "Hmm, I'm not sure who you mean by that—could you clarify?"

CALLER  20:39:52  Flux EndOfTurn: "Hi, ma'am. Do you hear my voice clearly?"
AGENT   20:39:55  "Yeah, I hear you perfectly."

CALLER  20:40:02  Flux EndOfTurn: "Okay. So tell me about your purpose of the account."
AGENT   20:40:11  "This is an internal Talk-Lee voice-system validation call to test
                   conversation quality and connection reliability."
```

And from the post-deploy calls:

```
AGENT   "There's no cost — this is just an internal system test."
AGENT   "Yeah, I'm still here."
AGENT   "I heard you ask for the main benefit in one short sentence."
AGENT   'You asked me to remember "blueprint," and you interrupted me twice.'
AGENT   "I didn't ignore it — I just didn't have a price to give you."
AGENT   "Fair point — I'll keep that in mind."
```

### 11.3 Assessment

**Answer-first is working.** Every reply leads with the answer and stops. None
attaches a counter-question to a direct question — the behaviour that made
earlier builds feel like an interrogation.

Three cases are worth calling out specifically:

* **The price question.** *"There's no cost — this is just an internal system
  test."* This is the highest-risk case in the whole system: small models invent
  figures, which is why `KNOWLEDGE_PRICE_GUARD` sits adjacent to the injected
  knowledge. The agent answered by declining to invent one.
* **Recall across the call.** *"You asked me to remember 'blueprint,' and you
  interrupted me twice."* Correct on both counts — the journal confirms two
  interrupts before that turn. Working memory plus accurate self-observation.
* **Handling a confusing question.** *"Hmm, I'm not sure who you mean by
  that—could you clarify?"* — the caller's question was genuinely ambiguous, and
  the agent asked rather than guessed.

### 11.4 The limit of this evidence

This is a qualitative read of a handful of exchanges on internal test calls,
not a scored metric. To make it a metric I would need the saved transcripts from
the database, which is a read I have not made. That option remains available on
your say-so.

---

## 12. DIMENSION 9 — Transfer behaviour

### 12.1 Result: untested

```
$ grep -ci "transfer" today.txt
5      # all incidental prose in agent replies, no tool invocation
$ grep -c "tool_call\|tool_invoked\|tool_executed" today.txt
0
```

**No transfer was attempted on any call today.** I am not going to infer that
transfer works from the absence of transfer errors — that is exactly the class
of reasoning this report exists to eliminate.

### 12.2 The known open issue

A previously-recorded finding stands: transfer-to-human promises a check it
cannot perform. That has not been re-examined today and no new evidence bears
on it. It needs a call that actually requests a transfer.

---

## 13. DIMENSION 10 — Genuine audible barge-in

### 13.1 The outcome mix

```
  real (audio stopped)     ████████████████                    35   32.4%
  no-op (ordinary turn)    ████████████████████████████████    70   64.8%
  deduped (dup event)      █                                    3    2.8%
  FAILED                                                        0    0.0%
```

**Two thirds of all interrupt events are ordinary turns with nothing playing.**
`barge_in_detected` cannot be read as "the agent was talked over" — the figure
that matters is `interrupt_complete`, which only fires when audio was actually
stopped or an in-flight turn was actually cancelled.

Per call:

| call | real | no-op | deduped | failed |
|---|---|---|---|---|
| `dec0bb16` | 2 | 5 | 1 | 0 |
| `ae686b21` | 3 | 11 | 0 | 0 |
| `defdd260` | 3 | 7 | 0 | 0 |
| `a195284b` | 1 | 15 | 0 | 0 |
| `b3350aee` | 4 | 8 | 0 | 0 |
| `ddf2c92c` | 9 | 12 | 1 | 0 |
| `b6d354d9` | 13 | 11 | 1 | 0 |

`a195284b` is the extreme: sixteen interrupt events, **one** of them real.

### 13.2 The gateway acknowledgements

```
$ grep -o "interrupt_step=cpp_interrupt.*" today.txt | head -3

interrupt_step=cpp_interrupt ok=True dropped_frames=0  dropped_ms=0   segments=1 attempts=1 rotated=True
interrupt_step=cpp_interrupt ok=True dropped_frames=6  dropped_ms=120 segments=4 attempts=1 rotated=True
interrupt_step=cpp_interrupt ok=True dropped_frames=12 dropped_ms=240 segments=7 attempts=1 rotated=True
```

Every one `ok=True`, every one `rotated=True`, every one on the first attempt.
**Zero failed interrupts across 108 interrupt events all day.** The interrupt
mechanism itself is healthy.

### 13.3 How much audio we binned

```
dropped audio   n=35  min=0ms  p50=240ms  p90=260ms  max=300ms
       0-1      ms  | ████████████████████████████████████████  12   34.3%
     120-180    ms  | ████                                       1    2.9%
     180-240    ms  | █████████                                  2    5.7%
     240-260    ms  | ██████████████████████████                 6   17.1%
     260-280    ms  | ████████████████████████████████████████  12   34.3%
     280-300    ms  | ████                                       1    2.9%
     300+      ms  | ████                                       1    2.9%
```

Median **240ms** of already-queued agent audio discarded per real interrupt —
twelve 20ms PCMU frames the caller did *not* hear because the gateway binned
them. The bimodal shape is expected: 34% discard nothing (the barge-in landed
before the gateway had queued anything), and the rest cluster tightly at
240–280ms, which is roughly one TTS burst.

### 13.4 The mislabelled barge-in, and the regression it nearly caused

`b3350aee` logged a silent turn as `provider_empty_stream`. Read alone, that
says the TTS provider returned no audio for a reply the agent had composed —
a real failure mode with no recovery in that loop:

| failure | how it surfaces | recovery before today |
|---|---|---|
| provider raises | `Exception` | caught, spoken fallback |
| provider goes quiet | `asyncio.TimeoutError` | synthesis retried once |
| **provider returns nothing** | **`StopAsyncIteration`, instantly** | **none** |

The third row does not raise and cannot time out, because there is nothing to
wait for. Same epistemic shape as the STT socket that accepted 400 chunks and
answered nothing.

I wrote the fix — retry once, then speak a short fallback. Then I pulled the
surrounding sequence before shipping:

```
21:02:29 barge_in_detected
21:02:29 interrupt_step=begin            interrupt_id=5fcc4011a71a
21:02:29 interrupt_step=state_listening
21:02:29 interrupt_step=task_cancelled
21:02:29 interrupt_step=buffers_cleared
21:02:29 interrupt_step=cpp_interrupt
21:02:29 turn_silent_reason reason=provider_empty_stream      <-- HERE
21:02:29 interrupt_step=tts_provider_cleared
21:02:29 interrupt_complete {...}
```

The silent turn is **inside an interrupt teardown**. The caller barged in before
the reply started playing. The turn correctly produced no audio, and it was
filed as a provider fault.

Had I shipped the fix as written, a caller who interrupted would have been
answered by the agent re-synthesising the reply they had just talked over, and
then — if that failed — saying *"Sorry, could you say that again?"* to someone
mid-sentence. Worse than the silence it was fixing.

**The defect is the classification.** Two entirely different events shared one
name, so the metric that counts dead air was counting successful barge-ins. Now:

```python
_caller_stopped_it = (
    (barge_in_event is not None and barge_in_event.is_set())
    or not getattr(session, "tts_active", True)
)
silent_reason = (
    "interrupted_before_audio" if _caller_stopped_it
    else "provider_empty_stream"
)
```

The retry and fallback survive, because the genuine failure is still real and
still unhandled, but both are gated on the same condition and can never fire at
a caller who interrupted.

Two further bugs surfaced while testing this, both caught by tests written
minutes earlier:

* **The nested fallback polluted the verdict.** The fallback is a recursive
  `synthesize_and_send` whose own `finally` clears `session.tts_active`. A
  `finally` re-reading the flag saw the *nested* teardown and labelled the outer
  turn `interrupted_before_audio` when nobody had interrupted. Fixed by
  capturing the verdict when the stream ends and never re-reading it.
* **The recovery attempt filed its own silent-turn record**, double-counting
  every unrecoverable turn in the metric that measures unrecoverable turns.
  Fixed by reading the fallback flag at method entry.

---

## 14. SPECIAL ASK A — What the caller heard during an interruption

You asked for caller-speech-to-audible-stop latency, not internal cancellation
duration, and for whether any cancelled audio resumed.

### 14.1 Did cancelled audio resume? No — and it is now stated, not assumed

```
$ grep interrupt_audio_audit today.txt

interrupt_audio_audit call=talky-out-a3 interrupts=11 resumed_chunks=0 stale_rejected=0 verdict=clean
interrupt_audio_audit call=talky-out-97 interrupts=16 resumed_chunks=0 stale_rejected=0 verdict=clean
```

**27 interrupts across two audited calls, zero resumed chunks.**

This matters because the utterance-id rotation protects a narrower window than
its own comment claimed. A chunk already stamped with the retired id is
rejected with a 409 — correct. But a chunk that enters `send_tts_audio` *after*
the rotation reads the **fresh** id from the same dict and is accepted, so the
cancelled generation could resume under a new identity, indistinguishable from
the next legitimate turn.

Several layers should stop one ever arriving: the in-flight task is cancelled
and awaited before the rotation, and the Python send buffer is cleared first.
"Should" is exactly the standard of evidence that lost two calls on 2026-08-13,
so it is now counted. The measurement is deliberately passive — nothing is
blocked or delayed by it, because muting a legitimate turn on an unproven
hypothesis is the worse failure.

The audit line is written on clean calls specifically so that `verdict=clean` is
a measurement rather than an absence.

### 14.2 Caller-speech-to-audible-stop — still unanswered

```
interrupt_complete {'interrupt_id': '0092989ec257', 'ok': True, 'deduped': False,
  'task_cancelled': True, 'local_bytes': 0, 'gw_frames': 12, 'gw_ms': 240,
  'gw_segments': 7, 'gw_attempts': 1, 'elapsed_ms': 1.6,
  'detect_ms': None, 'speech_to_stop_ms': None, 'errors': []}
```

`None` on all 45 occurrences. `elapsed_ms: 1.6` is our teardown — precisely the
misleading number this work set out to replace, and the only one still
available.

The cause is in section 17: the acoustic onset anchor could not write to the
session object, so there was no start time to measure from. Fixed and deployed
in `3fa5cadf`, **unproven**.

### 14.3 What the design measures, once it runs

Three numbers, deliberately separated:

* **`detect_ms`** — caller starts talking → we begin stopping. Dominated by
  Flux's StartOfTurn decision. Expected to be the large term.
* **`elapsed_ms`** — our teardown. Measured at 0.16–1.8ms all day.
* **`speech_to_stop_ms`** — the sum: mouth open → agent silent.

One further term is real and deliberately **excluded** rather than estimated:
after the gateway acknowledges, whatever it has already handed to the RTP socket
still reaches the caller — one 20ms frame plus the receiving jitter buffer, not
observable from this process. The figure is a floor accurate to a few tens of
milliseconds and must never be quoted as if the ear were instrumented.

---

## 15. SPECIAL ASK B — Proving the STT watchdog promotes Nova

You asked for proof, and for controlled fault injection rather than breaking the
live provider if it did not fire naturally. It fired naturally three times
(section 4), so there are now two independent lines of evidence.

### 15.1 The fault injector

Shipped and inert. Two environment variables arm it:

```
VOICE_STT_FAULT_SILENT_CAMPAIGN=<one campaign uuid>
VOICE_STT_FAULT_SILENT_UNTIL=2026-08-18T23:00:00Z
```

Four safety properties, each deliberate:

* **Scoped to a named campaign UUID, not a boolean.** There is no value that
  turns it on globally, so every other campaign on the box is untouched by
  construction rather than by a conditional someone could get wrong.
* **The expiry is mandatory and fails closed.** An unparseable or missing
  `_UNTIL` means *do not inject* — never *inject forever*. A forgotten env var
  stops mattering on its own.
* **It refuses without a safety net.** With `STT_FAILOVER_ENABLED` off there is
  no secondary to promote, so deafening the primary would destroy the call
  rather than test it. It declines and logs why.
* **It is loud.** Every activation logs at ERROR with campaign and call id.

The wrapper forwards `initialize`, `pre_connect`, `mute`, `unmute` and `cleanup`
to the real provider and suppresses only transcripts, so the socket genuinely
opens and genuinely receives audio. The failure simulated is a provider that
answers nothing — not one that never connected, which is a different failure
that already had working failover.

### 15.2 The offline proof asserts on content, not just promotion

Each audio chunk's amplitude encodes its index, so the test can name the audio
the secondary received:

```python
assert primary.chunks_swallowed == 29        # the dead provider really was fed
assert heard, "nothing was transcribed — the caller was lost"
assert heard[:5] == [26, 27, 28, 29, 30]     # spoken INTO the dead stream
assert heard == list(range(26, 41))          # then the rest, in order
assert len(heard) == len(set(heard))         # no duplication
assert heard == sorted(heard)                # no reordering
```

A failover that swaps providers and loses the sentence the caller was halfway
through is not a rescue. This test fails if the buffer is dropped, reordered or
duplicated.

It also pinned a detail worth keeping: the primary receives **29** chunks, not
30. The watchdog raises on the chunk that crosses the threshold *before* that
chunk is yielded onward, so the tripping chunk is buffered and never reaches the
dead provider — meaning the last frame the caller spoke goes to the secondary
rather than into the void.

### 15.3 Coverage of the negative cases

The same suite pins that it does **not** fire when it should not:

```
test_quiet_call_does_not_fail_over            — a silent caller is not a fault
test_muted_primary_does_not_fail_over         — our own TTS echo is not caller speech
test_working_primary_is_never_disturbed       — 400 chunks, healthy provider, no failover
test_a_batch_provider_trips_the_watchdog_by_design  — a recorded, accepted limitation
```

---

## 16. SPECIAL ASK C — Recording-disclosure retention

### 16.1 Today's numbers

```
recording_disclosure_speaking            10
recording_disclosure_spoken               5
recording_disclosure_interrupted          0
recording_suppressed_no_disclosure        0
```

**No recordings were lost today.** But that is luck, not a fix: nobody talked
over the notice. On 2026-08-13, four recordings were destroyed by exactly that
sequence.

### 16.2 Status: open, untouched, as agreed

The behaviour is unchanged. When a caller barges over the disclosure, the notice
is marked interrupted and the recording is suppressed entirely.

A "retry from the top" fix was built and **reverted on 2026-08-11** — callers
hung up roughly two seconds after hearing the notice restart, which is a worse
outcome than the one it fixed.

My recommendation is unchanged: finish the notice at the start of the agent's
*next* turn, never over the caller, and retain the recording once it completes.
But that changes *when a notice counts as delivered*, which is a retention and
compliance decision rather than an engineering one.

**Task #44 remains open pending your decision.** Nothing in today's work touched
it.

---

## 17. The failure I have to report: two features that never executed

### 17.1 How it surfaced

Post-deploy calls came back with `detect_ms: None` on every interrupt, despite
the anchor having deployed hours earlier. Probing the real model explains it:

```
caller_voice_onset_at   FAILED  ValueError: "CallSession" object has no field "caller_voice_onset_at"
caller_voice_last_at    FAILED  ValueError: "CallSession" object has no field "caller_voice_last_at"
last_audio_rms          FAILED  ValueError: "CallSession" object has no field "last_audio_rms"
last_audio_rms_at       FAILED  ValueError: "CallSession" object has no field "last_audio_rms_at"
_nudges_spoken          SET OK  read-back=1.0
_nudges_suppressed      SET OK  read-back=1.0
_tts_fallback_attempted SET OK  read-back=1.0
```

### 17.2 The mechanism

`CallSession` is a pydantic v2 `BaseModel`:

```python
model_config = ConfigDict(
    arbitrary_types_allowed=True,
    use_enum_values=True,
)
```

There is no `extra="allow"`. Assigning an **undeclared** attribute therefore
raises `ValueError`. Names beginning with an underscore are accepted, because
pydantic routes them to its private-attribute store instead of field validation.

So `session._foo = 1` works and `session.foo = 1` does not, and **the difference
is invisible at the call site**.

### 17.3 The consequence

Both writes were wrapped in `try/except Exception: pass`, on the correct
principle that a measurement must never cost a call. The exception was therefore
swallowed on every audio frame of every call, and two features never executed:

* **The acoustic nudge guard, shipped 2026-08-13.** `_audio_active` read False
  forever because `last_audio_rms` was never stored. It has never suppressed a
  single nudge in four days. Report 5 described it as live. Production agrees
  with the code, not with the report: `suppressed=0` on every audited call.
* **The caller voice-onset anchor, shipped this morning.** Hence `detect_ms:
  None`, and hence the one question in your brief that section 14.2 still
  cannot answer.

### 17.4 Why the tests passed throughout

Both features were tested against `types.SimpleNamespace`, which accepts any
attribute. The suite was green against a double possessing the one property the
real object lacks.

This is the deeper lesson and it generalises: **a test double that is more
permissive than production cannot prove production works.** The tests were not
weak in coverage — they were thorough, and they asserted the right behaviours.
They were weak in fidelity.

### 17.5 The fix and its guard rail

Renamed to the private form the model accepts, matching the convention every
*working* scratch field on this object already uses (`_has_introduced`,
`_machine_screening`, `_caller_spoke_since_greeting`, `_tts_fallback_attempted`).

`tests/unit/test_session_scratch_attrs.py` now exercises a **real
`CallSession`** for every scratch field, plus an explicit test that the
non-underscore form still raises:

```python
def test_a_non_underscore_name_still_raises():
    s = _session()
    with pytest.raises(ValueError, match="has no field"):
        s.last_audio_rms = 3504.0
```

That records the convention as a constraint rather than leaving it to be
mistaken for style.

Deployed in `3fa5cadf`. Both features are live for the first time and **neither
has been exercised by a call**. I am not going to repeat this morning's mistake
of reporting a shipped fix as a working one.

---

## 18. Web research — what the standard is, and what our provider supports

You asked me to check what is standard for prompt handling on our provider.

### 18.1 Groq prompt caching — the authoritative answer

From `console.groq.com/docs/prompt-caching`:

* **Supported models:** `openai/gpt-oss-20b`, `openai/gpt-oss-120b`,
  `openai/gpt-oss-safeguard-20b`. That is the complete list.
* **Minimum cacheable prompt:** varies by model, 128–1024 tokens.
* **TTL:** cached data expires after 2 hours without use.
* **Prefix structure:** *"place static content like instructions and examples at
  the beginning of your prompt, and put variable content, such as user-specific
  information, at the end."*
* **Pricing:** 50% discount on cached input tokens, no additional fee.
* **Caveat:** *"Groq tries to maximize cache hits, but this is not guaranteed."*

Our prompt order already complies with the prefix guidance — the 2026-08-13
reorder put stable-for-the-call blocks first and per-turn blocks last. It is
correct by construction and would pay off immediately on a supported model.

### 18.2 What our account can actually serve

```
$ GET https://api.groq.com/openai/v1/models
13 models available on this account:
   allam-2-7b
   canopylabs/orpheus-arabic-saudi
   canopylabs/orpheus-v1-english
   groq/compound
   groq/compound-mini
   meta-llama/llama-prompt-guard-2-22m
   meta-llama/llama-prompt-guard-2-86m
   openai/gpt-oss-120b
   openai/gpt-oss-20b
   openai/gpt-oss-safeguard-20b
   qwen/qwen3.6-27b
   whisper-large-v3
   whisper-large-v3-turbo
```

The only conversational LLMs are `qwen/qwen3.6-27b` and the GPT-OSS family.
**Both Llama models in our curated menu 404.** That was load-bearing in four
places, all fixed in `b7a03450`:

* the curated menu offered both;
* `AIProviderConfig.llm_model` **defaulted** to `llama-3.1-8b-instant`, so any
  tenant without a saved config got a model that could not answer a turn;
* `DEFAULT_ASSISTANT_MODEL` was `llama-3.3-70b-versatile` — both the in-app
  assistant's default *and* the value `resolve_model()` falls back to when a
  stored choice fails validation, so the failure path led to a dead model;
* `_LLM_DEFAULT_SECONDARY_MODEL["groq"]` was `llama-3.1-8b-instant`, i.e. the
  same-vendor failover safety net was a model that 404s.

Five tenant configs still point at the dead id. **Not an outage**: those tenants
have never placed a call, and `LLM_SECONDARY_PROVIDER=gemini` overrides the dead
secondary, so they would fail over rather than fail. Task #59.

### 18.3 Voice-agent prompt guidance from the field

From Vapi's prompting guide: *"Every token costs latency. The system prompt
loads into the model's context on every turn. A bloated prompt increases time to
first token, which the caller experiences as dead air."* It recommends replies
of one to two sentences (we measure a median of eight words, so this is
satisfied) and warns explicitly against long enumerated "never say X, Y, Z"
lists because *"every banned phrase is a token in the model's active context."*

Broader field guidance suggests keeping the system prompt under roughly 400
tokens and total context under 2,000 for voice, on the basis that token count
has a near-linear relationship with first-token latency.

**We are at ~8,400 tokens of total context, with a ~6,500-token system prompt.**
That is materially above the guidance in both cases. I am reporting the gap
rather than acting on it, for the reason in section 7.5: our prompt is not
bloated by accident, it is tuned, and the tuning has measured behavioural value
that the generic guidance does not know about.

Sources:
`console.groq.com/docs/prompt-caching`, `console.groq.com/docs/models`,
`docs.vapi.ai/prompting-guide`, `deepgram.com/learn/considerations-for-building-ai-agents`.

---

## 19. Register of corrections to my own earlier claims

| # | claim | status | evidence |
|---|---|---|---|
| 1 | "The 0% prompt cache is caused by the LIVE STATE block at character 0" | **WRONG** | `cached=None` on two identical prompts; Groq docs list GPT-OSS only |
| 2 | "Caching DOES work on this account — llama-3.1-8b gets hits up to 6656 tokens" | **INVALID** | that model 404s on the account |
| 3 | "The acoustic nudge guard is live" (report 5) | **WRONG** | never executed; `ValueError` swallowed on every frame |
| 4 | "The agent talks for 6–7 seconds a turn, a monologue" | **RETRACTED** | p50 8 words, max 22, n=80 |
| 5 | "STT_FAILOVER_ENABLED = (unset)" (report 5 Appendix D) | **WRONG** | it is `true`; 13 `stt_resilient_wrapper_active` lines today |
| 6 | "The audio gaps are the prime suspect for STT mangling" (report 5) | **WRONG** | `arrived_ratio` p50 0.997 — bunching, not loss |
| 7 | "provider_empty_stream is a broken TTS provider" | **WRONG** | it was a barge-in inside an interrupt teardown |
| 8 | "Production base prompt is 6,498 tokens" | **CORRECTED** | ~8,400 in production; 6,498 is the bare offline compose |

Eight corrections in three days is not a good ratio, and the pattern in them is
consistent: I reported the output of a check without first establishing that the
check could observe the thing it was checking. Items 1, 3 and 7 are all the same
mistake in different clothing.

---

## 20. Open items and recommendations

### 20.1 Open, with reasons

| # | item | why it is not fixed |
|---|---|---|
| 43 | prompt size / prefill cost | Needs an A/B or a model change, not a judgement call. Data in §7. |
| 44 | recordings lost to disclosure barge-in | Product/compliance decision. Yours. |
| 54 | `CredentialResolver._CACHE` keyed on `id(db_pool)` | CPython reuses freed addresses, so a replaced pool can inherit another pool's cached credential. No TTL. Credential path — not shipping unannounced. |
| 59 | 5 tenant configs on a 404 model | Requires a DB write. |

### 20.2 Recommended next steps, in order

1. **One call with a deliberate mid-sentence interruption.** This is the single
   highest-value action available. It produces `detect_ms` and
   `speech_to_stop_ms` for the first time, confirms the nudge guard now
   suppresses, and validates `interrupted_before_audio` — three fixes that are
   deployed and unproven.
2. **An internal voice call on `openai/gpt-oss-120b`.** The only experiment that
   can unlock a 6× latency improvement. The June objection was about
   conversational behaviour; the current prompt is substantially different from
   the one that objection was measured against.
3. **Decide task #44.** It is the only item where the blocker is a policy
   question rather than engineering effort.
4. **Authorise the #59 DB update and the #54 fix.** Both small, both currently
   latent rather than active.

---

## 21. Change inventory

| commit | subject |
|---|---|
| `e63a907c` | STT fault injection — campaign-scoped, expiring, fails closed |
| `b0c00358` | caller-heard interrupt measurement, resume audit, nudge audit |
| `04d8d4b8` | call-by-call scorecard + report 5 correction |
| `26f47bcb` | barge-in was being filed as a broken TTS provider |
| `9e56613d` | prompt-size measurement + cache root-cause correction |
| `b7a03450` | model menu offered two models the account cannot serve |
| `3fa5cadf` | two features silently dead — pydantic attribute names |

**Gate:** 4,773 passed, 15 skipped. The 8 failures and 5 errors are identical to
the pristine-HEAD baseline (missing-secrets artifacts in webhook HMAC, IDOR and
metrics-auth tests), plus one known intermittent (task #54).

**New tests this wave:** 20 (fault injection), 17 (barge-in audibility), 22
(scorecard), 11 (TTS empty stream), 8 (model menu), 13 (session scratch attrs).

---

## APPENDIX A — Measured distributions

Every chart below is computed from the journal by `scripts/report_charts.py`, not
drawn by hand. Bucket edges are printed so any bar can be re-derived.

```
==============================================================================
CHART 1 — prompt_time (Groq prefill) per LLM turn
==============================================================================

prompt_time   n=93  min=451ms  p50=629ms  p90=706ms  max=755ms
------------------------------------------------------------------------------
       0-450    ms  |                                                         0    0.0%
     450-500    ms  | ███████                                                 4    4.3%
     500-550    ms  | █████                                                   3    3.2%
     550-600    ms  | █████████                                               5    5.4%
     600-625    ms  | ████████████████████████████████████████████████████   29   31.2%
     625-650    ms  | █████████████████████████████████████████              23   24.7%
     650-675    ms  | ███████████████████████                                13   14.0%
     675-700    ms  | █████████                                               5    5.4%
     700-750    ms  | ████████████████                                        9    9.7%
     750+      ms  | ████                                                    2    2.2%

==============================================================================
CHART 2 — client_ttft_ms (what the turn loop waited for)
==============================================================================

TTFT   n=114  min=423ms  p50=639ms  p90=738ms  max=908ms
------------------------------------------------------------------------------
       0-550    ms  | █████████████████████                                  10    8.8%
     550-580    ms  | ██████                                                  3    2.6%
     580-600    ms  | ████                                                    2    1.8%
     600-620    ms  | ████████████████████████████████████████               19   16.7%
     620-640    ms  | ████████████████████████████████████████████████████   25   21.9%
     640-660    ms  | ██████████████████████████████████████████             20   17.5%
     660-700    ms  | ███████████████████████████                            13   11.4%
     700-800    ms  | ██████████████████████████████████████████             20   17.5%
     800+      ms  | ████                                                    2    1.8%

==============================================================================
CHART 3 — agent reply length (words)
==============================================================================

reply length   n=80  min=1w  p50=8w  p90=16w  max=22w
------------------------------------------------------------------------------
       0-2      w   | ██████████████████████████                              7    8.8%
       2-4      w   | █████████████████████████████████████████              11   13.8%
       4-6      w   | █████████████████████████████████████████████          12   15.0%
       6-8      w   | ██████████████████████████████                          8   10.0%
       8-10     w   | ██████████████████████████████                          8   10.0%
      10-13     w   | ████████████████████████████████████████████████████   14   17.5%
      13-16     w   | █████████████████████████████████████████████          12   15.0%
      16-20     w   | ██████████████████████                                  6    7.5%
      20-25     w   | ███████                                                 2    2.5%
      25+      w   |                                                         0    0.0%

==============================================================================
CHART 4 — audio binned per interrupt (gateway dropped_ms)
==============================================================================

dropped audio   n=35  min=0ms  p50=240ms  p90=260ms  max=300ms
------------------------------------------------------------------------------
       0-1      ms  | ████████████████████████████████████████████████████   12   34.3%
       1-60     ms  |                                                         0    0.0%
      60-120    ms  |                                                         0    0.0%
     120-180    ms  | ████                                                    1    2.9%
     180-240    ms  | █████████                                               2    5.7%
     240-260    ms  | ██████████████████████████                              6   17.1%
     260-280    ms  | ████████████████████████████████████████████████████   12   34.3%
     280-300    ms  | ████                                                    1    2.9%
     300+      ms  | ████                                                    1    2.9%

==============================================================================
CHART 5 — telephony audio gap size
==============================================================================

gap   n=210  min=121ms  p50=241ms  p90=292ms  max=1251ms
------------------------------------------------------------------------------
       0-150    ms  | █████                                                   8    3.8%
     150-200    ms  | ██████████████████                                     27   12.9%
     200-250    ms  | ████████████████████████████████████████████████████   80   38.1%
     250-300    ms  | ███████████████████████████████████████████████████    79   37.6%
     300-400    ms  | ██████████                                             15    7.1%
     400-500    ms  |                                                         0    0.0%
     500-750    ms  |                                                         0    0.0%
     750-1000   ms  |                                                         0    0.0%
    1000+      ms  | █                                                       1    0.5%

==============================================================================
CHART 6 — caller audio RMS, 1s windows, all calls
==============================================================================

RMS   n=1196  min=7  p50=494  p90=2195  max=7426
------------------------------------------------------------------------------
       0-50         | ████                                                   29    2.4%
      50-100        | ███████                                                51    4.3%
     100-200        | ███████████████████████                               161   13.5%
     200-500        | ████████████████████████████████████████████████████  359   30.0%
     500-1000       | █████████████████████████████                         202   16.9%
    1000-2000       | ███████████████████████████████████                   242   20.2%
    2000-4000       | ████████████████████                                  139   11.6%
    4000-8000       | ██                                                     13    1.1%
    8000+          |                                                         0    0.0%

==============================================================================
CHART 7 — prompt size growth per call (tokens)
==============================================================================

call        turns   first    last  growth   trace
------------------------------------------------------------------------------
a195284b       22    8398    9044    +646   ▁▁▂▂▂▃▃▄▅▅▅▆▆▆▇▇▇▇▇▇█▇
ae686b21       14    8401    8701    +300   ▂▂▂▃▃▄▄▄▅▆▆▇▇█
b3350aee       10    8401    8777    +376   ▂▄▄▅▆▆▇▇██
b6d354d9       11    8400    8751    +351   ▂▂▃▄▄▅▆▆▇▇█
ddf2c92c        6    8399    8545    +146   ▃▃▅▅▆█
dec0bb16        6    8385    8507    +122   ▃▃▄▅▇█
defdd260       13    8428    8746    +318   ▃▂▃▃▄▄▅▆▆▇▇▇█

==============================================================================
CHART 8 — interrupt outcome mix
==============================================================================

  real (audio stopped)     ████████████████                                     35   32.4%
  no-op (ordinary turn)    ████████████████████████████████                     70   64.8%
  deduped (dup event)      █                                                     3    2.8%
  FAILED                                                                         0    0.0%
```

---

## APPENDIX B — Per-call event timelines

All nine calls, verbatim from the journal, filtered to lifecycle events and
stamped with elapsed seconds from the first event of each call. This is the raw
material behind every claim in sections 4-16.

```

==============================================================================
TIMELINE 6090952e   20:37:02 -> 20:37:10   8 events
==============================================================================
  +   0s  20:37:02  audio_stream_started call_id=6090952e-457b-4ef8-a0dd-f938fe579da4 queue_size=0 stt_active=True
  +   3s  20:37:05  [SilenceMonitor] 6090952e-457 — silence (opening), nudging: 'Hello?'
  +   5s  20:37:07  barge_in_detected
  +   5s  20:37:07  interrupt_step=begin interrupt_id=e51b29b7a953 call=6090952e-457 reason=barge_in tts_active=False
  +   5s  20:37:07  interrupt_step=nothing_playing interrupt_id=e51b29b7a953 call=6090952e-457 reason=barge_in elapsed_ms=0.35
  +   6s  20:37:08  Flux EndOfTurn: 'Hi. This is Liam. How are you?'
  +   6s  20:37:08  t_stt_first_final call_id=6090952e-457b-4ef8-a0dd-f938fe579da4
  +   8s  20:37:10  audio_stream_ended call_id=6090952e-457b-4ef8-a0dd-f938fe579da4 chunks_yielded=307 stt_active=False

==============================================================================
TIMELINE dec0bb16   20:38:59 -> 20:40:47   43 events
==============================================================================
  +   0s  20:38:59  audio_stream_started call_id=dec0bb16-7be8-4512-a5d3-a19336df82da queue_size=0 stt_active=True
  +   0s  20:38:59  recording_disclosure_speaking call_id=dec0bb16-7be reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   5s  20:39:04  recording_disclosure_spoken call_id=dec0bb16-7be
  +   6s  20:39:05  outbound_greeting_presynth_done call_id=dec0bb16-7be elapsed_ms=1019 interrupted=False
  +  10s  20:39:09  Flux EndOfTurn: 'Yeah. How are you doing?'
  +  10s  20:39:09  t_stt_first_final call_id=dec0bb16-7be8-4512-a5d3-a19336df82da
  +  15s  20:39:14  llm_response turn=0 said="Good, thanks. I'm Alex from Talk-Lee — do you have a minute?"
  +  15s  20:39:14  first_turn_latency call=dec0bb16-7be turn=0 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=1029 llm_f
  +  19s  20:39:18  Flux EndOfTurn: 'Yeah.'
  +  27s  20:39:26  llm_response turn=1 said='Great. This is just an internal validation call to test our voice system. How’s your evening
  +  31s  20:39:30  Flux EndOfTurn: 'Yeah. It's pretty good.'
  +  38s  20:39:37  llm_response turn=2 said='Nice. So, just to check the audio—is everything coming through clearly on your end?'
  +  43s  20:39:42  Flux EndOfTurn: 'Yeah. It's coming very good. How about my wife?'
  +  47s  20:39:46  llm_response turn=3 said='Hmm, I’m not sure who you mean by that—could you clarify?'
  +  50s  20:39:49  barge_in_detected
  +  50s  20:39:49  interrupt_step=begin interrupt_id=62bc84217b1e call=dec0bb16-7be reason=barge_in tts_active=False
  +  50s  20:39:49  interrupt_step=nothing_playing interrupt_id=62bc84217b1e call=dec0bb16-7be reason=barge_in elapsed_ms=0.17
  +  53s  20:39:52  Flux EndOfTurn: 'Hi, ma'am. Do you hear my voice clearly?'
  +  56s  20:39:55  llm_response turn=4 said='Yeah, I hear you perfectly.'
  +  63s  20:40:02  Flux EndOfTurn: 'Okay. So tell me about your purpose of the account.'
  +  72s  20:40:11  llm_response turn=5 said='This is an internal Talk-Lee voice-system validation call to test conversation quality and c
  +  88s  20:40:27  [SilenceMonitor] dec0bb16-7be — silence (mid), nudging: 'Still there?'
  +  89s  20:40:28  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0 — 6.0s of caller speech went in and no transcript even
  +  90s  20:40:29  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
  +  90s  20:40:29  barge_in_detected
  +  90s  20:40:29  interrupt_step=begin interrupt_id=711825dc75b7 call=dec0bb16-7be reason=barge_in tts_active=False
  +  90s  20:40:29  interrupt_step=nothing_playing interrupt_id=711825dc75b7 call=dec0bb16-7be reason=barge_in elapsed_ms=0.17
  +  94s  20:40:33  barge_in_detected
  +  94s  20:40:33  interrupt_step=begin interrupt_id=24630a5df464 call=dec0bb16-7be reason=barge_in tts_active=False
  +  94s  20:40:33  interrupt_step=nothing_playing interrupt_id=24630a5df464 call=dec0bb16-7be reason=barge_in elapsed_ms=0.17
  +  98s  20:40:37  barge_in_detected
  +  98s  20:40:37  interrupt_step=begin interrupt_id=291b337698a2 call=dec0bb16-7be reason=barge_in tts_active=True
  +  98s  20:40:37  interrupt_complete {'interrupt_id': '291b337698a2', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 101s  20:40:40  barge_in_detected
  + 101s  20:40:40  interrupt_step=begin interrupt_id=7a10a7638389 call=dec0bb16-7be reason=barge_in tts_active=False
  + 101s  20:40:40  interrupt_step=nothing_playing interrupt_id=7a10a7638389 call=dec0bb16-7be reason=barge_in elapsed_ms=0.16
  + 105s  20:40:44  barge_in_detected
  + 105s  20:40:44  interrupt_step=begin interrupt_id=f60a5d3595e5 call=dec0bb16-7be reason=barge_in tts_active=False
  + 105s  20:40:44  interrupt_step=nothing_playing interrupt_id=f60a5d3595e5 call=dec0bb16-7be reason=barge_in elapsed_ms=0.18
  + 108s  20:40:47  barge_in_detected
  + 108s  20:40:47  interrupt_step=begin interrupt_id=8123dd52e988 call=dec0bb16-7be reason=barge_in tts_active=True
  + 108s  20:40:47  interrupt_complete {'interrupt_id': '8123dd52e988', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 108s  20:40:47  barge_in_detected

==============================================================================
TIMELINE ae686b21   20:40:54 -> 20:43:24   81 events
==============================================================================
  +   0s  20:40:54  audio_stream_started call_id=ae686b21-cae7-466d-a44c-442f928db44d queue_size=0 stt_active=True
  +   0s  20:40:54  recording_disclosure_speaking call_id=ae686b21-cae reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   4s  20:40:58  recording_disclosure_spoken call_id=ae686b21-cae
  +   4s  20:40:58  outbound_greeting_presynth_done call_id=ae686b21-cae elapsed_ms=840 interrupted=False
  +   8s  20:41:02  [SilenceMonitor] ae686b21-cae — silence (opening), nudging: 'Hello?'
  +   8s  20:41:02  barge_in_detected
  +   8s  20:41:02  interrupt_step=begin interrupt_id=f7d64cba5ea9 call=ae686b21-cae reason=barge_in tts_active=True
  +   8s  20:41:02  interrupt_complete {'interrupt_id': 'f7d64cba5ea9', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  +   9s  20:41:03  Flux EndOfTurn: 'What's your name?'
  +   9s  20:41:03  t_stt_first_final call_id=ae686b21-cae7-466d-a44c-442f928db44d
  +  12s  20:41:06  llm_response turn=0 said="I'm Alex from Talk-Lee."
  +  17s  20:41:11  barge_in_detected
  +  17s  20:41:11  interrupt_step=begin interrupt_id=f0ad727b8797 call=ae686b21-cae reason=barge_in tts_active=False
  +  17s  20:41:11  interrupt_step=nothing_playing interrupt_id=f0ad727b8797 call=ae686b21-cae reason=barge_in elapsed_ms=0.25
  +  19s  20:41:13  Flux EndOfTurn: 'What's your purpose of the account?'
  +  26s  20:41:20  llm_response turn=1 said='This is an internal Talk-Lee voice-system validation call to test conversation quality and c
  +  26s  20:41:20  first_turn_latency call=ae686b21-cae turn=1 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=1051 llm_f
  +  33s  20:41:27  Flux EndOfTurn: 'Okay. And'
  +  34s  20:41:28  llm_response turn=2 said='Yeah?'
  +  41s  20:41:35  barge_in_detected
  +  41s  20:41:35  interrupt_step=begin interrupt_id=458f8ccf8487 call=ae686b21-cae reason=barge_in tts_active=False
  +  41s  20:41:35  interrupt_step=nothing_playing interrupt_id=458f8ccf8487 call=ae686b21-cae reason=barge_in elapsed_ms=0.17
  +  42s  20:41:36  Flux EndOfTurn: 'Hello?'
  +  46s  20:41:40  llm_response turn=3 said="Yeah, I'm here — what's on your mind?"
  +  48s  20:41:42  barge_in_detected
  +  48s  20:41:42  interrupt_step=begin interrupt_id=472c3f1f3ff4 call=ae686b21-cae reason=barge_in tts_active=False
  +  48s  20:41:42  interrupt_step=nothing_playing interrupt_id=472c3f1f3ff4 call=ae686b21-cae reason=barge_in elapsed_ms=0.21
  +  51s  20:41:45  Flux EndOfTurn: 'I have plenty of things on my mind.'
  +  54s  20:41:48  llm_response turn=4 said="Right, yeah — what's top of the list?"
  +  61s  20:41:55  barge_in_detected
  +  61s  20:41:55  interrupt_step=begin interrupt_id=e55dba0417ea call=ae686b21-cae reason=barge_in tts_active=False
  +  61s  20:41:55  interrupt_step=nothing_playing interrupt_id=e55dba0417ea call=ae686b21-cae reason=barge_in elapsed_ms=0.18
  +  62s  20:41:56  Flux EndOfTurn: 'Are you gonna help me?'
  +  66s  20:42:00  llm_response turn=5 said="Yeah, I'm here to help — what do you need?"
  +  69s  20:42:03  barge_in_detected
  +  69s  20:42:03  interrupt_step=begin interrupt_id=51214b732926 call=ae686b21-cae reason=barge_in tts_active=False
  +  69s  20:42:03  interrupt_step=nothing_playing interrupt_id=51214b732926 call=ae686b21-cae reason=barge_in elapsed_ms=0.18
  +  70s  20:42:04  Flux EndOfTurn: 'I need money.'
  +  76s  20:42:10  barge_in_detected
  +  76s  20:42:10  interrupt_step=begin interrupt_id=4079ffef87ab call=ae686b21-cae reason=barge_in tts_active=True
  +  76s  20:42:10  interrupt_complete {'interrupt_id': '4079ffef87ab', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  76s  20:42:10  Flux EndOfTurn: 'One.'
  +  78s  20:42:12  llm_response turn=7 said='One what?'
  +  80s  20:42:14  barge_in_detected
  +  80s  20:42:14  interrupt_step=begin interrupt_id=23e091d93dfe call=ae686b21-cae reason=barge_in tts_active=False
  +  80s  20:42:14  interrupt_step=nothing_playing interrupt_id=23e091d93dfe call=ae686b21-cae reason=barge_in elapsed_ms=0.17
  +  82s  20:42:16  Flux EndOfTurn: 'Well, you said you can help me.'
  +  87s  20:42:21  llm_response turn=8 said="Right, I did — what's the one thing you need help with?"
  +  90s  20:42:24  barge_in_detected
  +  90s  20:42:24  interrupt_step=begin interrupt_id=5339c34118f1 call=ae686b21-cae reason=barge_in tts_active=False
  +  90s  20:42:24  interrupt_step=nothing_playing interrupt_id=5339c34118f1 call=ae686b21-cae reason=barge_in elapsed_ms=0.17
  +  91s  20:42:25  Flux EndOfTurn: 'Money.'
  + 100s  20:42:34  llm_response turn=9 said="Hmm, this is just a system validation call, so I can't help with money — is there something 
  + 100s  20:42:34  voice_slow_turn call_id=ae686b21-cae turn_id=9 response_start_ms=1762.0 stt_first_ms=0.2 llm_first_token_ms=1502.2 tts
  + 102s  20:42:36  barge_in_detected
  + 102s  20:42:36  interrupt_step=begin interrupt_id=88973e3d21b4 call=ae686b21-cae reason=barge_in tts_active=False
  + 102s  20:42:36  interrupt_step=nothing_playing interrupt_id=88973e3d21b4 call=ae686b21-cae reason=barge_in elapsed_ms=0.17
  + 104s  20:42:38  Flux EndOfTurn: 'So what you can help me with?'
  + 110s  20:42:44  llm_response turn=10 said='Just testing how the call handles conversation and interruptions.'
  + 112s  20:42:46  barge_in_detected
  + 112s  20:42:46  interrupt_step=begin interrupt_id=14f480d5e291 call=ae686b21-cae reason=barge_in tts_active=False
  + 112s  20:42:46  interrupt_step=nothing_playing interrupt_id=14f480d5e291 call=ae686b21-cae reason=barge_in elapsed_ms=0.18
  + 114s  20:42:48  Flux EndOfTurn: 'Well, that's not the help.'
  + 118s  20:42:52  llm_response turn=11 said="Fair enough — sounds like I'm not the right person for that."
  + 125s  20:42:59  Flux EndOfTurn: 'Okay. So what do you think about the account?'
  + 128s  20:43:02  llm_response turn=12 said="It's working well so far."
  + 130s  20:43:04  barge_in_detected
  + 130s  20:43:04  interrupt_step=begin interrupt_id=82ee284475f5 call=ae686b21-cae reason=barge_in tts_active=False
  + 130s  20:43:04  interrupt_step=nothing_playing interrupt_id=82ee284475f5 call=ae686b21-cae reason=barge_in elapsed_ms=0.17
  + 131s  20:43:05  Flux EndOfTurn: 'Do you hear me clearly?'
  + 134s  20:43:08  llm_response turn=13 said='Yeah, I hear you perfectly.'
  + 136s  20:43:10  barge_in_detected
  + 136s  20:43:10  interrupt_step=begin interrupt_id=7c9cf4276063 call=ae686b21-cae reason=barge_in tts_active=False
  + 136s  20:43:10  interrupt_step=nothing_playing interrupt_id=7c9cf4276063 call=ae686b21-cae reason=barge_in elapsed_ms=0.18
  + 137s  20:43:11  Flux EndOfTurn: 'Any difficulty?'
  + 138s  20:43:12  barge_in_detected
  + 138s  20:43:12  interrupt_step=begin interrupt_id=5ab1930ec822 call=ae686b21-cae reason=barge_in tts_active=True
  + 138s  20:43:12  interrupt_complete {'interrupt_id': '5ab1930ec822', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 139s  20:43:13  Flux EndOfTurn: 'my wife.'
  + 143s  20:43:17  llm_response turn=15 said='Oh, right — is she around?'
  + 150s  20:43:24  audio_stream_ended call_id=ae686b21-cae7-466d-a44c-442f928db44d chunks_yielded=3704 stt_active=False

==============================================================================
TIMELINE defdd260   20:44:47 -> 20:47:23   72 events
==============================================================================
  +   0s  20:44:47  audio_stream_started call_id=defdd260-0e75-4049-9518-fc6f34d73636 queue_size=0 stt_active=True
  +   0s  20:44:47  recording_disclosure_speaking call_id=defdd260-0e7 reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   5s  20:44:52  recording_disclosure_spoken call_id=defdd260-0e7
  +   5s  20:44:52  outbound_greeting_presynth_done call_id=defdd260-0e7 elapsed_ms=800 interrupted=False
  +   9s  20:44:56  barge_in_detected
  +   9s  20:44:56  interrupt_step=begin interrupt_id=d4e0010e462e call=defdd260-0e7 reason=barge_in tts_active=False
  +   9s  20:44:56  interrupt_step=nothing_playing interrupt_id=d4e0010e462e call=defdd260-0e7 reason=barge_in elapsed_ms=0.22
  +   9s  20:44:56  [SilenceMonitor] defdd260-0e7 — silence (opening), nudging: 'Hello?'
  +  10s  20:44:57  Flux EndOfTurn: 'How are you doing?'
  +  10s  20:44:57  t_stt_first_final call_id=defdd260-0e75-4049-9518-fc6f34d73636
  +  10s  20:44:57  barge_in_detected
  +  10s  20:44:57  interrupt_step=begin interrupt_id=c5ae208c0678 call=defdd260-0e7 reason=barge_in tts_active=True
  +  10s  20:44:57  interrupt_complete {'interrupt_id': 'c5ae208c0678', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  +  13s  20:45:00  [SilenceMonitor] defdd260-0e7 — silence (opening), nudging: 'Hello??'
  +  17s  20:45:04  [SilenceMonitor] defdd260-0e7 — silence (opening), nudging: 'Helloooo — are you there?'
  +  19s  20:45:06  Flux EndOfTurn: 'Yeah.'
  +  20s  20:45:07  Flux EndOfTurn: 'Yeah.'
  +  23s  20:45:10  llm_response turn=0 said="Hey, I'm Alex from Talk-Lee — got a minute?"
  +  25s  20:45:12  Flux EndOfTurn: 'Yeah.'
  +  33s  20:45:20  llm_response turn=1 said='Great — this is just an internal validation call to test our voice system. How’s your evenin
  +  33s  20:45:20  first_turn_latency call=defdd260-0e7 turn=1 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=1573 llm_f
  +  33s  20:45:20  voice_slow_turn call_id=defdd260-0e7 turn_id=1 response_start_ms=1573.0 stt_first_ms=75.0 llm_first_token_ms=1346.8 tt
  +  38s  20:45:25  barge_in_detected
  +  38s  20:45:25  interrupt_step=begin interrupt_id=4fe26dd893fc call=defdd260-0e7 reason=barge_in tts_active=False
  +  38s  20:45:25  interrupt_step=nothing_playing interrupt_id=4fe26dd893fc call=defdd260-0e7 reason=barge_in elapsed_ms=0.3
  +  39s  20:45:26  Flux EndOfTurn: 'It's going pretty well. What about you?'
  +  48s  20:45:35  llm_response turn=2 said='Doing well, thanks. Just running some quick tests on our end — how are you handling calls ri
  +  48s  20:45:35  voice_slow_turn call_id=defdd260-0e7 turn_id=2 response_start_ms=2072.2 stt_first_ms=237.7 llm_first_token_ms=1859.9 t
  +  52s  20:45:39  barge_in_detected
  +  52s  20:45:39  interrupt_step=begin interrupt_id=9b1397f14637 call=defdd260-0e7 reason=barge_in tts_active=False
  +  52s  20:45:39  interrupt_step=nothing_playing interrupt_id=9b1397f14637 call=defdd260-0e7 reason=barge_in elapsed_ms=0.28
  +  55s  20:45:42  Flux EndOfTurn: 'Well, so far, so good.'
  +  59s  20:45:46  llm_response turn=3 said='Nice — anything tricky with the call handling lately?'
  +  64s  20:45:51  Flux EndOfTurn: 'Yeah.'
  +  67s  20:45:54  llm_response turn=4 said='Hmm — tricky how?'
  +  69s  20:45:56  barge_in_detected
  +  69s  20:45:56  interrupt_step=begin interrupt_id=471bfac6a6f7 call=defdd260-0e7 reason=barge_in tts_active=False
  +  69s  20:45:56  interrupt_step=nothing_playing interrupt_id=471bfac6a6f7 call=defdd260-0e7 reason=barge_in elapsed_ms=0.17
  +  72s  20:45:59  Flux EndOfTurn: 'Like the way you're speaking to slowly?'
  +  74s  20:46:01  barge_in_detected
  +  74s  20:46:01  interrupt_step=begin interrupt_id=707cc00451a6 call=defdd260-0e7 reason=barge_in tts_active=True
  +  74s  20:46:01  interrupt_complete {'interrupt_id': '707cc00451a6', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  83s  20:46:10  Flux EndOfTurn: 'And, uh, also, when I also when I talked in the mid of few conversations, so you don't answer that. Y
  +  89s  20:46:16  llm_response turn=6 said='Got it — so when you interrupt, I’m not picking up on it?'
  +  93s  20:46:20  Flux EndOfTurn: 'Yeah. Sometime you're doing, sometime you're not.'
  + 100s  20:46:27  llm_response turn=7 said="Right, that's exactly what we're testing. I'll make sure to listen closely from now on."
  + 108s  20:46:35  Flux EndOfTurn: 'Okay. And what do you think about my voice? Are you getting it clearly?'
  + 111s  20:46:38  llm_response turn=8 said='Yeah, coming through perfectly.'
  + 115s  20:46:42  barge_in_detected
  + 115s  20:46:42  interrupt_step=begin interrupt_id=4bd327819cbf call=defdd260-0e7 reason=barge_in tts_active=False
  + 115s  20:46:42  interrupt_step=nothing_playing interrupt_id=4bd327819cbf call=defdd260-0e7 reason=barge_in elapsed_ms=0.35
  + 116s  20:46:43  Flux EndOfTurn: 'So tell me about yourself.'
  + 120s  20:46:47  llm_response turn=9 said="I'm Alex, an automated voice agent for Talk-Lee."
  + 123s  20:46:50  Flux EndOfTurn: 'Okay. Next.'
  + 125s  20:46:52  llm_response turn=10 said='What would you like to test next?'
  + 131s  20:46:58  Flux EndOfTurn: 'I would like you to sing me a song.'
  + 136s  20:47:03  barge_in_detected
  + 136s  20:47:03  interrupt_step=begin interrupt_id=86baa6298265 call=defdd260-0e7 reason=barge_in tts_active=True
  + 136s  20:47:03  interrupt_complete {'interrupt_id': '86baa6298265', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 136s  20:47:03  Flux EndOfTurn: 'Y.'
  + 137s  20:47:04  llm_response turn=12 said='Yeah?'
  + 144s  20:47:11  barge_in_detected
  + 144s  20:47:11  interrupt_step=begin interrupt_id=55bd95a6344c call=defdd260-0e7 reason=barge_in tts_active=False
  + 144s  20:47:11  interrupt_step=nothing_playing interrupt_id=55bd95a6344c call=defdd260-0e7 reason=barge_in elapsed_ms=0.28
  + 146s  20:47:13  Flux EndOfTurn: 'Okay. Well, thanks. Alex.'
  + 149s  20:47:16  llm_response turn=13 said='Thanks for your time — take care.'
  + 151s  20:47:18  barge_in_detected
  + 151s  20:47:18  interrupt_step=begin interrupt_id=f8084cc5d725 call=defdd260-0e7 reason=barge_in tts_active=False
  + 151s  20:47:18  interrupt_step=nothing_playing interrupt_id=f8084cc5d725 call=defdd260-0e7 reason=barge_in elapsed_ms=0.59
  + 151s  20:47:18  Flux EndOfTurn: 'Goodbye.'
  + 154s  20:47:21  llm_response turn=14 said=''
  + 156s  20:47:23  audio_stream_ended call_id=defdd260-0e75-4049-9518-fc6f34d73636 chunks_yielded=3843 stt_active=False

==============================================================================
TIMELINE a195284b   20:51:16 -> 20:55:47   102 events
==============================================================================
  +   0s  20:51:16  audio_stream_started call_id=a195284b-dd92-4bb0-a1b0-649caa17a9f6 queue_size=0 stt_active=True
  +   0s  20:51:16  recording_disclosure_speaking call_id=a195284b-dd9 reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   5s  20:51:21  recording_disclosure_spoken call_id=a195284b-dd9
  +   6s  20:51:22  outbound_greeting_presynth_done call_id=a195284b-dd9 elapsed_ms=1160 interrupted=False
  +   8s  20:51:24  barge_in_detected
  +   8s  20:51:24  interrupt_step=begin interrupt_id=3724f5740e98 call=a195284b-dd9 reason=barge_in tts_active=False
  +   8s  20:51:24  interrupt_step=nothing_playing interrupt_id=3724f5740e98 call=a195284b-dd9 reason=barge_in elapsed_ms=0.16
  +   9s  20:51:25  [SilenceMonitor] a195284b-dd9 — silence (opening), nudging: 'Hello?'
  +  10s  20:51:26  Flux EndOfTurn: 'Which company are you calling from?'
  +  10s  20:51:26  t_stt_first_final call_id=a195284b-dd92-4bb0-a1b0-649caa17a9f6
  +  13s  20:51:29  llm_response turn=0 said='Yeah, this is Alex from Talk-Lee.'
  +  13s  20:51:29  first_turn_latency call=a195284b-dd9 turn=0 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=-371 llm_f
  +  15s  20:51:31  barge_in_detected
  +  15s  20:51:31  interrupt_step=begin interrupt_id=4d48bc87921f call=a195284b-dd9 reason=barge_in tts_active=False
  +  16s  20:51:32  interrupt_step=nothing_playing interrupt_id=4d48bc87921f call=a195284b-dd9 reason=barge_in elapsed_ms=0.37
  +  16s  20:51:32  Flux EndOfTurn: 'And why are you calling me?'
  +  24s  20:51:40  llm_response turn=1 said='This is an internal Talk-Lee voice-system validation call to test conversation quality and c
  +  24s  20:51:40  voice_slow_turn call_id=a195284b-dd9 turn_id=1 response_start_ms=1608.4 stt_first_ms=0.4 llm_first_token_ms=1399.3 tts
  +  32s  20:51:48  Flux EndOfTurn: 'Okay. Can you stop there and repeat only the last sentence, but speak a little faster?'
  +  40s  20:51:56  llm_response turn=2 said='This is an internal Talk-Lee voice-system validation call to test conversation quality and c
  +  47s  20:52:03  Flux EndOfTurn: 'Yeah. What was the first problem I mentioned earlier about your speaking style?'
  +  52s  20:52:08  llm_response turn=3 said="Hmm, you haven't mentioned a problem yet — what did you have in mind?"
  +  54s  20:52:10  barge_in_detected
  +  54s  20:52:10  interrupt_step=begin interrupt_id=18a2ff928cb8 call=a195284b-dd9 reason=barge_in tts_active=False
  +  54s  20:52:10  interrupt_step=nothing_playing interrupt_id=18a2ff928cb8 call=a195284b-dd9 reason=barge_in elapsed_ms=0.35
  +  58s  20:52:14  Flux EndOfTurn: 'Great. Before continuing, can you confirm that you heard my interruption?'
  +  64s  20:52:20  llm_response turn=4 said="Yeah, I heard you — I stopped right away. What's next?"
  +  66s  20:52:22  barge_in_detected
  +  66s  20:52:22  interrupt_step=begin interrupt_id=6dd75f378b6d call=a195284b-dd9 reason=barge_in tts_active=False
  +  66s  20:52:22  interrupt_step=nothing_playing interrupt_id=6dd75f378b6d call=a195284b-dd9 reason=barge_in elapsed_ms=0.25
  +  73s  20:52:29  Flux EndOfTurn: 'k. My voice is clear now, but what would you do if it suddenly became unclear and started breaking up
  +  79s  20:52:35  llm_response turn=5 said="I'd just ask you to repeat it — sorry, you cut out there, say that again?"
  +  81s  20:52:37  barge_in_detected
  +  81s  20:52:37  interrupt_step=begin interrupt_id=f7de71c5a3dc call=a195284b-dd9 reason=barge_in tts_active=False
  +  81s  20:52:37  interrupt_step=nothing_playing interrupt_id=f7de71c5a3dc call=a195284b-dd9 reason=barge_in elapsed_ms=0.45
  +  87s  20:52:43  Flux EndOfTurn: 'How are you? Is this clear now? But what would you do if suddenly became unclear starting breaking up
  +  93s  20:52:49  llm_response turn=6 said="Yeah, it's clear now. If it broke up, I'd just ask you to repeat it."
  +  96s  20:52:52  barge_in_detected
  +  96s  20:52:52  interrupt_step=begin interrupt_id=babb091f2364 call=a195284b-dd9 reason=barge_in tts_active=False
  +  96s  20:52:52  interrupt_step=nothing_playing interrupt_id=babb091f2364 call=a195284b-dd9 reason=barge_in elapsed_ms=0.17
  + 102s  20:52:58  Flux EndOfTurn: 'Please remember these three words, blueprint, estimator, and Tuesday. I will ask you about them later
  + 104s  20:53:00  llm_response turn=7 said='Got it — blueprint, estimator, Tuesday.'
  + 115s  20:53:11  Flux EndOfTurn: 'Okay. So what were the three words I asked you to remember, and and what order?'
  + 118s  20:53:14  llm_response turn=8 said='Blueprint, estimator, Tuesday.'
  + 133s  20:53:29  Flux EndOfTurn: 'Okay. So now please feel totally inside naturally and then explain it in one sentence with the compan
  + 138s  20:53:34  llm_response turn=9 said='Talk-Lee helps businesses stop missing calls.'
  + 143s  20:53:39  barge_in_detected
  + 143s  20:53:39  interrupt_step=begin interrupt_id=46125f39c5d3 call=a195284b-dd9 reason=barge_in tts_active=False
  + 143s  20:53:39  interrupt_step=nothing_playing interrupt_id=46125f39c5d3 call=a195284b-dd9 reason=barge_in elapsed_ms=0.35
  + 148s  20:53:44  Flux EndOfTurn: 'Can you say it naturally and explain it in one sentence what the company does?'
  + 152s  20:53:48  llm_response turn=10 said='Talk-Lee helps businesses stop missing calls.'
  + 161s  20:53:57  Flux EndOfTurn: 'Okay. And what were the three words?'
  + 165s  20:54:01  llm_response turn=11 said='Blueprint, estimator, Tuesday.'
  + 172s  20:54:08  Flux EndOfTurn: 'Okay. Now say that three words again, but start from the end.'
  + 176s  20:54:12  llm_response turn=12 said='Tuesday, estimator, blueprint.'
  + 187s  20:54:23  Flux EndOfTurn: 'Okay. My voice is clear now. But what would you do if it suddenly became unclear or started breaking 
  + 191s  20:54:27  llm_response turn=13 said="I'd just ask you to repeat it."
  + 196s  20:54:32  barge_in_detected
  + 196s  20:54:32  interrupt_step=begin interrupt_id=d5ef0d75c4a2 call=a195284b-dd9 reason=barge_in tts_active=False
  + 196s  20:54:32  interrupt_step=nothing_playing interrupt_id=d5ef0d75c4a2 call=a195284b-dd9 reason=barge_in elapsed_ms=0.19
  + 200s  20:54:36  Flux EndOfTurn: 'k. Wait before you continuing. Can you confirm that you heard my introduction?'
  + 204s  20:54:40  barge_in_detected
  + 204s  20:54:40  interrupt_step=begin interrupt_id=5e43c90d2b1b call=a195284b-dd9 reason=barge_in tts_active=True
  + 204s  20:54:40  interrupt_complete {'interrupt_id': '5e43c90d2b1b', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 204s  20:54:40  Flux EndOfTurn: 'when was it?'
  + 206s  20:54:42  llm_response turn=15 said='Tuesday.'
  + 209s  20:54:45  barge_in_detected
  + 209s  20:54:45  interrupt_step=begin interrupt_id=f1388e0ed263 call=a195284b-dd9 reason=barge_in tts_active=False
  + 209s  20:54:45  interrupt_step=nothing_playing interrupt_id=f1388e0ed263 call=a195284b-dd9 reason=barge_in elapsed_ms=0.17
  + 210s  20:54:46  Flux EndOfTurn: 'No. I'm not talking about today.'
  + 214s  20:54:50  llm_response turn=16 said="Sorry, I'm not sure what you mean — could you clarify?"
  + 218s  20:54:54  barge_in_detected
  + 218s  20:54:54  interrupt_step=begin interrupt_id=d7aea99cc8cf call=a195284b-dd9 reason=barge_in tts_active=False
  + 218s  20:54:54  interrupt_step=nothing_playing interrupt_id=d7aea99cc8cf call=a195284b-dd9 reason=barge_in elapsed_ms=0.33
  + 219s  20:54:55  Flux EndOfTurn: 'What were the three words?'
  + 223s  20:54:59  llm_response turn=17 said='Blueprint, estimator, Tuesday.'
  + 226s  20:55:02  barge_in_detected
  + 226s  20:55:02  interrupt_step=begin interrupt_id=299bd61c1bff call=a195284b-dd9 reason=barge_in tts_active=False
  + 226s  20:55:02  interrupt_step=nothing_playing interrupt_id=299bd61c1bff call=a195284b-dd9 reason=barge_in elapsed_ms=0.51
  + 227s  20:55:03  Flux EndOfTurn: 'Can you spell TalkLee?'
  + 232s  20:55:08  llm_response turn=18 said='T-a-l-k hyphen L-e-e.'
  + 237s  20:55:13  barge_in_detected
  + 237s  20:55:13  interrupt_step=begin interrupt_id=75feb5274479 call=a195284b-dd9 reason=barge_in tts_active=False
  + 237s  20:55:13  interrupt_step=nothing_playing interrupt_id=75feb5274479 call=a195284b-dd9 reason=barge_in elapsed_ms=0.18
  + 238s  20:55:14  Flux EndOfTurn: 'And can you use it in a sentence?'
  + 242s  20:55:18  llm_response turn=19 said="Yeah, I'm with Talk-Lee."
  + 247s  20:55:23  barge_in_detected
  + 247s  20:55:23  interrupt_step=begin interrupt_id=bded1b8714e7 call=a195284b-dd9 reason=barge_in tts_active=False
  + 247s  20:55:23  interrupt_step=nothing_playing interrupt_id=bded1b8714e7 call=a195284b-dd9 reason=barge_in elapsed_ms=0.18
  + 248s  20:55:24  Flux EndOfTurn: 'Okay. That will be all. Thank you.'
  + 253s  20:55:29  llm_response turn=20 said="Sorry, I'm still here — what else can I help you with?"
  + 258s  20:55:34  barge_in_detected
  + 258s  20:55:34  interrupt_step=begin interrupt_id=29f9c17e58c6 call=a195284b-dd9 reason=barge_in tts_active=False
  + 258s  20:55:34  interrupt_step=nothing_playing interrupt_id=29f9c17e58c6 call=a195284b-dd9 reason=barge_in elapsed_ms=0.18
  + 260s  20:55:36  Flux EndOfTurn: 'Thanks.'
  + 263s  20:55:39  llm_response turn=21 said="You're welcome — take care."
  + 264s  20:55:40  barge_in_detected
  + 264s  20:55:40  interrupt_step=begin interrupt_id=edd2e4983bad call=a195284b-dd9 reason=barge_in tts_active=False
  + 264s  20:55:40  interrupt_step=nothing_playing interrupt_id=edd2e4983bad call=a195284b-dd9 reason=barge_in elapsed_ms=0.16
  + 265s  20:55:41  Flux EndOfTurn: 'Goodbye.'
  + 268s  20:55:44  llm_response turn=22 said=''
  + 271s  20:55:47  audio_stream_ended call_id=a195284b-dd92-4bb0-a1b0-649caa17a9f6 chunks_yielded=6703 stt_active=False

==============================================================================
TIMELINE b3350aee   21:02:20 -> 21:04:37   66 events
==============================================================================
  +   0s  21:02:20  audio_stream_started call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd queue_size=0 stt_active=True
  +   0s  21:02:20  recording_disclosure_speaking call_id=b3350aee-76a reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   4s  21:02:24  recording_disclosure_spoken call_id=b3350aee-76a
  +   5s  21:02:25  outbound_greeting_presynth_done call_id=b3350aee-76a elapsed_ms=1000 interrupted=False
  +   7s  21:02:27  barge_in_detected
  +   7s  21:02:27  interrupt_step=begin interrupt_id=14f693a9f2fe call=b3350aee-76a reason=barge_in tts_active=False
  +   7s  21:02:27  interrupt_step=nothing_playing interrupt_id=14f693a9f2fe call=b3350aee-76a reason=barge_in elapsed_ms=0.21
  +   9s  21:02:29  [SilenceMonitor] b3350aee-76a — silence (opening), nudging: 'Hello?'
  +   9s  21:02:29  Flux EndOfTurn: 'Hi, Alex. Who are you?'
  +   9s  21:02:29  t_stt_first_final call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd
  +   9s  21:02:29  barge_in_detected
  +   9s  21:02:29  interrupt_step=begin interrupt_id=5fcc4011a71a call=b3350aee-76a reason=barge_in tts_active=True
  +   9s  21:02:29  turn_silent_reason call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd reason=provider_empty_stream
  +   9s  21:02:29  interrupt_complete {'interrupt_id': '5fcc4011a71a', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  +  10s  21:02:30  barge_in_detected
  +  10s  21:02:30  interrupt_step=begin interrupt_id=204cccd6a5a3 call=b3350aee-76a reason=barge_in tts_active=False
  +  10s  21:02:30  interrupt_complete {'interrupt_id': '204cccd6a5a3', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  13s  21:02:33  Flux EndOfTurn: 'Which company are you calling from, and what is the purpose of this call?'
  +  23s  21:02:43  barge_in_detected
  +  23s  21:02:43  interrupt_step=begin interrupt_id=98907704a59b call=b3350aee-76a reason=barge_in tts_active=True
  +  23s  21:02:43  interrupt_complete {'interrupt_id': '98907704a59b', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  27s  21:02:47  Flux EndOfTurn: 'Sorry to sorry to interrupt. Can you repeat that more quickly in one sentence?'
  +  37s  21:02:57  llm_response turn=1 said='This is an internal Talk-Lee voice-system validation call to test conversation quality and c
  +  37s  21:02:57  first_turn_latency call=b3350aee-76a turn=1 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=988 llm_fi
  +  39s  21:02:59  barge_in_detected
  +  39s  21:02:59  interrupt_step=begin interrupt_id=d8c9ca535c25 call=b3350aee-76a reason=barge_in tts_active=False
  +  39s  21:02:59  interrupt_step=nothing_playing interrupt_id=d8c9ca535c25 call=b3350aee-76a reason=barge_in elapsed_ms=0.17
  +  49s  21:03:09  Flux EndOfTurn: 'k. Please remember these three details. My name is Hasham. The test is on Tuesday, and the issue is s
  +  56s  21:03:16  llm_response turn=2 said='Got it — Hasham, Tuesday, slow responses. What time on Tuesday works best for you?'
  +  60s  21:03:20  barge_in_detected
  +  60s  21:03:20  interrupt_step=begin interrupt_id=ea21c5e7ab3a call=b3350aee-76a reason=barge_in tts_active=False
  +  60s  21:03:20  interrupt_step=nothing_playing interrupt_id=ea21c5e7ab3a call=b3350aee-76a reason=barge_in elapsed_ms=0.19
  +  63s  21:03:23  Flux EndOfTurn: 'Could you hear that clearly? If not, ask me to repeat it.'
  +  69s  21:03:29  llm_response turn=3 said='Yeah, loud and clear — Hasham, Tuesday, slow responses.'
  +  73s  21:03:33  barge_in_detected
  +  73s  21:03:33  interrupt_step=begin interrupt_id=bf1fbff146c2 call=b3350aee-76a reason=barge_in tts_active=False
  +  73s  21:03:33  interrupt_step=nothing_playing interrupt_id=bf1fbff146c2 call=b3350aee-76a reason=barge_in elapsed_ms=0.18
  +  79s  21:03:39  Flux EndOfTurn: 'k. The test is on Wednesday. Sorry. I mean, Thursday. Which day should you remember?'
  +  81s  21:03:41  llm_response turn=4 said='Thursday it is.'
  +  85s  21:03:45  barge_in_detected
  +  85s  21:03:45  interrupt_step=begin interrupt_id=1ade5c4f91ac call=b3350aee-76a reason=barge_in tts_active=False
  +  85s  21:03:45  interrupt_step=nothing_playing interrupt_id=1ade5c4f91ac call=b3350aee-76a reason=barge_in elapsed_ms=0.17
  +  92s  21:03:52  Flux EndOfTurn: 'If I ask something unrelated, like, what is twenty seven plus eighteen, can you answer it and then re
  +  97s  21:03:57  llm_response turn=5 said="Forty-five. What's the best time on Thursday to catch you?"
  + 101s  21:04:01  barge_in_detected
  + 101s  21:04:01  interrupt_step=begin interrupt_id=36e281b6bdc7 call=b3350aee-76a reason=barge_in tts_active=False
  + 101s  21:04:01  interrupt_step=nothing_playing interrupt_id=36e281b6bdc7 call=b3350aee-76a reason=barge_in elapsed_ms=0.17
  + 106s  21:04:06  Flux EndOfTurn: 'Before we finish, summarize the issues I reported, and I repeat the three final details you remember.
  + 111s  21:04:11  llm_response turn=6 said='Slow responses. Hasham, Thursday.'
  + 114s  21:04:14  barge_in_detected
  + 114s  21:04:14  interrupt_step=begin interrupt_id=ec0e7069349f call=b3350aee-76a reason=barge_in tts_active=False
  + 114s  21:04:14  interrupt_step=nothing_playing interrupt_id=ec0e7069349f call=b3350aee-76a reason=barge_in elapsed_ms=0.44
  + 115s  21:04:15  Flux EndOfTurn: 'That will be all. Thank you.'
  + 120s  21:04:20  llm_response turn=7 said="Sorry, I'm still here — what else can I help you with?"
  + 122s  21:04:22  barge_in_detected
  + 122s  21:04:22  interrupt_step=begin interrupt_id=d87f68adc692 call=b3350aee-76a reason=barge_in tts_active=False
  + 122s  21:04:22  interrupt_step=nothing_playing interrupt_id=d87f68adc692 call=b3350aee-76a reason=barge_in elapsed_ms=0.22
  + 123s  21:04:23  Flux EndOfTurn: 'Nothing. Thank you.'
  + 123s  21:04:23  Flux EndOfTurn: 'Goodbye.'
  + 130s  21:04:30  llm_response turn=8 said="Sorry, I'm still here — what else can I help you with?"
  + 132s  21:04:32  barge_in_detected
  + 132s  21:04:32  interrupt_step=begin interrupt_id=379abec242dc call=b3350aee-76a reason=barge_in tts_active=True
  + 132s  21:04:32  interrupt_complete {'interrupt_id': '379abec242dc', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 133s  21:04:33  Flux EndOfTurn: 'Goodbye.'
  + 136s  21:04:36  llm_response turn=10 said=''
  + 137s  21:04:37  audio_stream_ended call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd chunks_yielded=3399 stt_active=False

==============================================================================
TIMELINE ddf2c92c   21:19:11 -> 21:22:01   89 events
==============================================================================
  +   0s  21:19:11  audio_stream_started call_id=ddf2c92c-eeb9-41df-8fb2-4a68931fa17c queue_size=0 stt_active=True
  +   0s  21:19:11  recording_disclosure_speaking call_id=ddf2c92c-eeb reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   3s  21:19:14  recording_disclosure_spoken call_id=ddf2c92c-eeb
  +   4s  21:19:15  outbound_greeting_presynth_done call_id=ddf2c92c-eeb elapsed_ms=640 interrupted=False
  +   8s  21:19:19  [SilenceMonitor] ddf2c92c-eeb — silence (opening), nudging: 'Hello?'
  +   9s  21:19:20  barge_in_detected
  +   9s  21:19:20  interrupt_step=begin interrupt_id=666bc53d66bc call=ddf2c92c-eeb reason=barge_in tts_active=True
  +   9s  21:19:20  interrupt_complete {'interrupt_id': '666bc53d66bc', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  +  10s  21:19:21  Flux EndOfTurn: 'Hi, Alex.'
  +  10s  21:19:21  t_stt_first_final call_id=ddf2c92c-eeb9-41df-8fb2-4a68931fa17c
  +  17s  21:19:28  llm_response turn=0 said='Hey there — this is Alex from Talk-Lee, just checking in on a quick voice test. Got a sec?'
  +  23s  21:19:34  Flux EndOfTurn: 'Yeah. Tell me what services are you offering and how can it help me?'
  +  29s  21:19:40  barge_in_detected
  +  29s  21:19:40  interrupt_step=begin interrupt_id=ab9718500816 call=ddf2c92c-eeb reason=barge_in tts_active=True
  +  29s  21:19:40  interrupt_complete {'interrupt_id': 'ab9718500816', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  33s  21:19:44  Flux EndOfTurn: 'Sorry. Stop there. Can you explain the main benefit in one, sir? Short sentence?'
  +  43s  21:19:54  llm_response turn=2 said='This is just an internal test to check how well the system handles conversation and interrup
  +  43s  21:19:54  first_turn_latency call=ddf2c92c-eeb turn=2 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=1004 llm_f
  +  45s  21:19:56  barge_in_detected
  +  45s  21:19:56  interrupt_step=begin interrupt_id=8a486a8cdcf7 call=ddf2c92c-eeb reason=barge_in tts_active=False
  +  45s  21:19:56  interrupt_step=nothing_playing interrupt_id=8a486a8cdcf7 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.17 detect_ms=N
  +  49s  21:20:00  Flux EndOfTurn: 'How much does it cost? Please give me a dark answer before asking any follow-up question.'
  +  55s  21:20:06  llm_response turn=3 said="There's no cost — this is just an internal system test."
  +  58s  21:20:09  Flux EndOfTurn: 'Yes.'
  +  65s  21:20:16  barge_in_detected
  +  65s  21:20:16  interrupt_step=begin interrupt_id=01cfc4ca7f38 call=ddf2c92c-eeb reason=barge_in tts_active=False
  +  65s  21:20:16  interrupt_complete {'interrupt_id': '01cfc4ca7f38', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  67s  21:20:18  Flux EndOfTurn: 'Hello?'
  +  70s  21:20:21  llm_response turn=4 said="Yeah, I'm still here."
  +  73s  21:20:24  barge_in_detected
  +  73s  21:20:24  interrupt_step=begin interrupt_id=d4aa7f02b6ce call=ddf2c92c-eeb reason=barge_in tts_active=False
  +  73s  21:20:24  interrupt_step=nothing_playing interrupt_id=d4aa7f02b6ce call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.21 detect_ms=N
  +  75s  21:20:26  Flux EndOfTurn: 'Did you hear my interruption, and can you repeat exactly what I said?'
  +  80s  21:20:31  llm_response turn=5 said='I heard you ask for the main benefit in one short sentence.'
  +  97s  21:20:48  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0 — 6.0s of caller speech went in and no transcript even
  +  97s  21:20:48  [SilenceMonitor] ddf2c92c-eeb — silence (mid), nudging: 'Still there?'
  +  97s  21:20:48  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
  +  97s  21:20:48  barge_in_detected
  +  97s  21:20:48  interrupt_step=begin interrupt_id=afe353d245b4 call=ddf2c92c-eeb reason=barge_in tts_active=True
  +  98s  21:20:49  interrupt_complete {'interrupt_id': 'afe353d245b4', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  + 101s  21:20:52  barge_in_detected
  + 101s  21:20:52  interrupt_step=begin interrupt_id=1bc2c963e0f9 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 101s  21:20:52  interrupt_step=nothing_playing interrupt_id=1bc2c963e0f9 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 106s  21:20:57  barge_in_detected
  + 106s  21:20:57  interrupt_step=begin interrupt_id=0c0c96217cfc call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 106s  21:20:57  interrupt_step=nothing_playing interrupt_id=0c0c96217cfc call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.2 detect_ms=No
  + 110s  21:21:01  barge_in_detected
  + 110s  21:21:01  interrupt_step=begin interrupt_id=16cfaa56df54 call=ddf2c92c-eeb reason=barge_in tts_active=True
  + 110s  21:21:01  interrupt_complete {'interrupt_id': '16cfaa56df54', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 115s  21:21:06  barge_in_detected
  + 115s  21:21:06  interrupt_step=begin interrupt_id=1fe625f8be5a call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 115s  21:21:06  interrupt_step=nothing_playing interrupt_id=1fe625f8be5a call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 118s  21:21:09  barge_in_detected
  + 118s  21:21:09  interrupt_step=begin interrupt_id=6b21b8793f2e call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 118s  21:21:09  interrupt_step=nothing_playing interrupt_id=6b21b8793f2e call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 121s  21:21:12  barge_in_detected
  + 121s  21:21:12  interrupt_step=begin interrupt_id=1b0b016d3ccb call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 121s  21:21:12  interrupt_step=nothing_playing interrupt_id=1b0b016d3ccb call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.31 detect_ms=N
  + 126s  21:21:17  barge_in_detected
  + 126s  21:21:17  interrupt_step=begin interrupt_id=cc8bb665272c call=ddf2c92c-eeb reason=barge_in tts_active=True
  + 126s  21:21:17  interrupt_complete {'interrupt_id': 'cc8bb665272c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 131s  21:21:22  barge_in_detected
  + 131s  21:21:22  interrupt_step=begin interrupt_id=8ef4a6893f83 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 131s  21:21:22  interrupt_step=nothing_playing interrupt_id=8ef4a6893f83 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 133s  21:21:24  barge_in_detected
  + 133s  21:21:24  interrupt_step=begin interrupt_id=398fff023257 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 133s  21:21:24  interrupt_complete {'interrupt_id': '398fff023257', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 138s  21:21:29  barge_in_detected
  + 138s  21:21:29  interrupt_step=begin interrupt_id=ac678d01ce25 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 138s  21:21:29  interrupt_step=nothing_playing interrupt_id=ac678d01ce25 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 140s  21:21:31  barge_in_detected
  + 140s  21:21:31  interrupt_step=begin interrupt_id=d8944e230899 call=ddf2c92c-eeb reason=barge_in tts_active=True
  + 140s  21:21:31  interrupt_complete {'interrupt_id': 'd8944e230899', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 140s  21:21:31  barge_in_detected
  + 150s  21:21:41  llm_response turn=9 said="Yeah, I'm here."
  + 150s  21:21:41  voice_slow_turn call_id=ddf2c92c-eeb turn_id=9 response_start_ms=3709.6 stt_first_ms=3456.2 llm_first_token_ms=839.5 t
  + 150s  21:21:41  barge_in_detected
  + 150s  21:21:41  interrupt_step=begin interrupt_id=c83b0bbc7971 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 150s  21:21:41  interrupt_step=nothing_playing interrupt_id=c83b0bbc7971 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 155s  21:21:46  barge_in_detected
  + 155s  21:21:46  interrupt_step=begin interrupt_id=8f0d4f240b13 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 155s  21:21:46  interrupt_step=nothing_playing interrupt_id=8f0d4f240b13 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 158s  21:21:49  barge_in_detected
  + 158s  21:21:49  interrupt_step=begin interrupt_id=6b4f41589c4d call=ddf2c92c-eeb reason=barge_in tts_active=True
  + 158s  21:21:49  interrupt_complete {'interrupt_id': '6b4f41589c4d', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 163s  21:21:54  barge_in_detected
  + 163s  21:21:54  interrupt_step=begin interrupt_id=76544dcde5f9 call=ddf2c92c-eeb reason=barge_in tts_active=False
  + 163s  21:21:54  interrupt_step=nothing_playing interrupt_id=76544dcde5f9 call=ddf2c92c-eeb reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 170s  21:22:01  [SilenceMonitor] ddf2c92c-eeb — nudge_audit nudges=1 suppressed=0

==============================================================================
TIMELINE 319debb3   21:23:34 -> 21:23:37   4 events
==============================================================================
  +   0s  21:23:34  audio_stream_started call_id=319debb3-5c85-4ac8-82c0-b1d358512d76 queue_size=0 stt_active=True
  +   2s  21:23:36  [SilenceMonitor] 319debb3-5c8 — silence (opening), nudging: 'Hello?'
  +   3s  21:23:37  [SilenceMonitor] 319debb3-5c8 — nudge_audit nudges=0 suppressed=0
  +   3s  21:23:37  audio_stream_ended call_id=319debb3-5c85-4ac8-82c0-b1d358512d76 chunks_yielded=88 stt_active=False

==============================================================================
TIMELINE b6d354d9   21:24:21 -> 21:28:12   115 events
==============================================================================
  +   0s  21:24:21  audio_stream_started call_id=b6d354d9-44c5-4f62-b35a-68bc027f5f82 queue_size=0 stt_active=True
  +   0s  21:24:21  recording_disclosure_speaking call_id=b6d354d9-44c reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  +   5s  21:24:26  recording_disclosure_spoken call_id=b6d354d9-44c
  +   5s  21:24:26  outbound_greeting_presynth_done call_id=b6d354d9-44c elapsed_ms=542 interrupted=False
  +   9s  21:24:30  [SilenceMonitor] b6d354d9-44c — silence (opening), nudging: 'Hello?'
  +   9s  21:24:30  barge_in_detected
  +   9s  21:24:30  interrupt_step=begin interrupt_id=9fbdc79d6d4f call=b6d354d9-44c reason=barge_in tts_active=True
  +   9s  21:24:30  interrupt_complete {'interrupt_id': '9fbdc79d6d4f', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  +  10s  21:24:31  Flux EndOfTurn: 'What's your name?'
  +  10s  21:24:31  t_stt_first_final call_id=b6d354d9-44c5-4f62-b35a-68bc027f5f82
  +  13s  21:24:34  llm_response turn=0 said='Alex, from Talk-Lee.'
  +  16s  21:24:37  barge_in_detected
  +  16s  21:24:37  interrupt_step=begin interrupt_id=2deae5a39395 call=b6d354d9-44c reason=barge_in tts_active=False
  +  16s  21:24:37  interrupt_step=nothing_playing interrupt_id=2deae5a39395 call=b6d354d9-44c reason=barge_in elapsed_ms=0.31 detect_ms=N
  +  20s  21:24:41  Flux EndOfTurn: 'Alex, what exactly are you testing on this card, and how will the results be used?'
  +  26s  21:24:47  barge_in_detected
  +  26s  21:24:47  interrupt_step=begin interrupt_id=15f0063b9c02 call=b6d354d9-44c reason=barge_in tts_active=True
  +  26s  21:24:47  interrupt_complete {'interrupt_id': '15f0063b9c02', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  27s  21:24:48  Flux EndOfTurn: 'Stop. One moment.'
  +  29s  21:24:50  barge_in_detected
  +  29s  21:24:50  interrupt_step=begin interrupt_id=143ad96a77da call=b6d354d9-44c reason=barge_in tts_active=True
  +  29s  21:24:50  interrupt_complete {'interrupt_id': '143ad96a77da', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  32s  21:24:53  Flux EndOfTurn: 'Please confirm that you heard'
  +  33s  21:24:54  barge_in_detected
  +  33s  21:24:54  interrupt_step=begin interrupt_id=d87fb0b17d2c call=b6d354d9-44c reason=barge_in tts_active=True
  +  33s  21:24:54  interrupt_complete {'interrupt_id': 'd87fb0b17d2c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  36s  21:24:57  Flux EndOfTurn: 'Please confirm that you heard this interruption before continuing.'
  +  38s  21:24:59  llm_response turn=4 said='Yeah, I heard that.'
  +  38s  21:24:59  first_turn_latency call=b6d354d9-44c turn=4 mode=agent prompt_kind=outbound persona=none speech_to_audio_ms=999 llm_fi
  +  41s  21:25:02  barge_in_detected
  +  41s  21:25:02  interrupt_step=begin interrupt_id=66cae2f47f08 call=b6d354d9-44c reason=barge_in tts_active=False
  +  41s  21:25:02  interrupt_step=nothing_playing interrupt_id=66cae2f47f08 call=b6d354d9-44c reason=barge_in elapsed_ms=0.19 detect_ms=N
  +  46s  21:25:07  Flux EndOfTurn: 'Hi. Good afternoon. This is Alex. Is there any cost for this caller service answered on yes or no?'
  +  47s  21:25:08  barge_in_detected
  +  47s  21:25:08  interrupt_step=begin interrupt_id=beb6972a4fc0 call=b6d354d9-44c reason=barge_in tts_active=True
  +  47s  21:25:08  interrupt_complete {'interrupt_id': 'beb6972a4fc0', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  48s  21:25:09  Flux EndOfTurn: 'first, then explain.'
  +  59s  21:25:20  llm_response turn=6 said='Got it — I heard the interruption. This is an internal Talk-Lee voice-system validation call
  +  65s  21:25:26  Flux EndOfTurn: 'Okay.'
  +  73s  21:25:34  barge_in_detected
  +  73s  21:25:34  interrupt_step=begin interrupt_id=0c65f7a4b77f call=b6d354d9-44c reason=barge_in tts_active=False
  +  73s  21:25:34  interrupt_complete {'interrupt_id': '0c65f7a4b77f', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  +  79s  21:25:40  Flux EndOfTurn: 'I said the word blueprint. Repeat the exact word I just said, not something from earlier.'
  +  81s  21:25:42  llm_response turn=7 said='Blueprint.'
  +  98s  21:25:59  [SilenceMonitor] b6d354d9-44c — silence (mid), nudging: 'Still there?'
  + 106s  21:26:07  Flux EndOfTurn: 'Yeah. I'm here. Stop your chicken. And tell me whether you detected that I started speaking.'
  + 109s  21:26:10  llm_response turn=8 said='Yeah, I detected that.'
  + 113s  21:26:14  barge_in_detected
  + 113s  21:26:14  interrupt_step=begin interrupt_id=a02e317bc109 call=b6d354d9-44c reason=barge_in tts_active=False
  + 113s  21:26:14  interrupt_step=nothing_playing interrupt_id=a02e317bc109 call=b6d354d9-44c reason=barge_in elapsed_ms=0.34 detect_ms=N
  + 124s  21:26:25  Flux EndOfTurn: 'k. Before ending, repeat this exact question, then answer it. What was the price? What exact word did
  + 129s  21:26:30  llm_response turn=9 said='You asked me to remember "blueprint," and you interrupted me twice.'
  + 136s  21:26:37  barge_in_detected
  + 136s  21:26:37  interrupt_step=begin interrupt_id=195ff4620422 call=b6d354d9-44c reason=barge_in tts_active=False
  + 136s  21:26:37  interrupt_step=nothing_playing interrupt_id=195ff4620422 call=b6d354d9-44c reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 139s  21:26:40  Flux EndOfTurn: 'And what was the price you ignored this question? What?'
  + 144s  21:26:45  barge_in_detected
  + 144s  21:26:45  interrupt_step=begin interrupt_id=e51c6888019c call=b6d354d9-44c reason=barge_in tts_active=True
  + 144s  21:26:45  interrupt_complete {'interrupt_id': 'e51c6888019c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 145s  21:26:46  Flux EndOfTurn: 'But why did you ignore it?'
  + 150s  21:26:51  llm_response turn=11 said="I didn't ignore it — I just didn't have a price to give you."
  + 153s  21:26:54  barge_in_detected
  + 153s  21:26:54  interrupt_step=begin interrupt_id=5eba430d3e8f call=b6d354d9-44c reason=barge_in tts_active=False
  + 153s  21:26:54  interrupt_step=nothing_playing interrupt_id=5eba430d3e8f call=b6d354d9-44c reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 158s  21:26:59  Flux EndOfTurn: 'But when I ask you, you should have stated that I don't have a price instead of ignoring it.'
  + 162s  21:27:03  llm_response turn=12 said="Fair point — I'll keep that in mind."
  + 168s  21:27:09  barge_in_detected
  + 168s  21:27:09  interrupt_step=begin interrupt_id=678890964bf0 call=b6d354d9-44c reason=barge_in tts_active=False
  + 168s  21:27:09  interrupt_step=nothing_playing interrupt_id=678890964bf0 call=b6d354d9-44c reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 170s  21:27:11  Flux EndOfTurn: 'Well, that will be all, Alex. Thank you.'
  + 171s  21:27:12  Flux EndOfTurn: 'take care, and goodbye.'
  + 171s  21:27:12  barge_in_detected
  + 171s  21:27:12  interrupt_step=begin interrupt_id=e17048904e93 call=b6d354d9-44c reason=barge_in tts_active=True
  + 171s  21:27:12  interrupt_complete {'interrupt_id': 'e17048904e93', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 188s  21:27:29  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0 — 6.0s of caller speech went in and no transcript even
  + 188s  21:27:29  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
  + 189s  21:27:30  barge_in_detected
  + 189s  21:27:30  interrupt_step=begin interrupt_id=54795e6d16d7 call=b6d354d9-44c reason=barge_in tts_active=False
  + 189s  21:27:30  interrupt_complete {'interrupt_id': '54795e6d16d7', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 196s  21:27:37  barge_in_detected
  + 196s  21:27:37  interrupt_step=begin interrupt_id=1cb4ed521983 call=b6d354d9-44c reason=barge_in tts_active=True
  + 196s  21:27:37  interrupt_complete {'interrupt_id': '1cb4ed521983', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 202s  21:27:43  llm_response turn=15 said="Yeah, I'm here."
  + 202s  21:27:43  voice_slow_turn call_id=b6d354d9-44c turn_id=15 response_start_ms=3493.1 stt_first_ms=0.3 llm_first_token_ms=789.4 tts
  + 203s  21:27:44  barge_in_detected
  + 203s  21:27:44  interrupt_step=begin interrupt_id=14b36587cc2d call=b6d354d9-44c reason=barge_in tts_active=False
  + 203s  21:27:44  interrupt_step=nothing_playing interrupt_id=14b36587cc2d call=b6d354d9-44c reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 207s  21:27:48  barge_in_detected
  + 207s  21:27:48  interrupt_step=begin interrupt_id=0092989ec257 call=b6d354d9-44c reason=barge_in tts_active=True
  + 207s  21:27:48  interrupt_complete {'interrupt_id': '0092989ec257', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 211s  21:27:52  barge_in_detected
  + 211s  21:27:52  interrupt_step=begin interrupt_id=63b8c04e1576 call=b6d354d9-44c reason=barge_in tts_active=False
  + 211s  21:27:52  interrupt_step=nothing_playing interrupt_id=63b8c04e1576 call=b6d354d9-44c reason=barge_in elapsed_ms=0.19 detect_ms=N
  + 214s  21:27:55  barge_in_detected
  + 214s  21:27:55  interrupt_step=begin interrupt_id=38d1c6100c0a call=b6d354d9-44c reason=barge_in tts_active=False
  + 214s  21:27:55  interrupt_step=nothing_playing interrupt_id=38d1c6100c0a call=b6d354d9-44c reason=barge_in elapsed_ms=0.2 detect_ms=No
  + 218s  21:27:59  barge_in_detected
  + 218s  21:27:59  interrupt_step=begin interrupt_id=eb70701064ba call=b6d354d9-44c reason=barge_in tts_active=True
  + 218s  21:27:59  interrupt_complete {'interrupt_id': 'eb70701064ba', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 218s  21:27:59  barge_in_detected
  + 223s  21:28:04  barge_in_detected
  + 223s  21:28:04  interrupt_step=begin interrupt_id=1990796c66a3 call=b6d354d9-44c reason=barge_in tts_active=False
  + 223s  21:28:04  interrupt_complete {'interrupt_id': '1990796c66a3', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  + 226s  21:28:07  barge_in_detected
  + 226s  21:28:07  interrupt_step=begin interrupt_id=7d3168eeef8e call=b6d354d9-44c reason=barge_in tts_active=False
  + 226s  21:28:07  interrupt_step=nothing_playing interrupt_id=7d3168eeef8e call=b6d354d9-44c reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 229s  21:28:10  llm_response turn=18 said='Goodbye, take care.'
  + 229s  21:28:10  voice_slow_turn call_id=b6d354d9-44c turn_id=18 response_start_ms=1517.7 stt_first_ms=4999.9 llm_first_token_ms=803.2 
  + 229s  21:28:10  agent_end_call call_id=b6d354d9-44c — model requested hangup
  + 229s  21:28:10  llm_end_session_action call_id=b6d354d9-44c reason=agent_end_call session_type=telephony
  + 229s  21:28:10  TelephonyMediaGateway: hangup requested call_id=b6d354d9-44c pbx=talky-out-97 reason=agent_end_call
  + 229s  21:28:10  barge_in_detected
  + 229s  21:28:10  interrupt_step=begin interrupt_id=a8216cb0fa7a call=b6d354d9-44c reason=barge_in tts_active=False
  + 229s  21:28:10  interrupt_step=nothing_playing interrupt_id=a8216cb0fa7a call=b6d354d9-44c reason=barge_in elapsed_ms=0.18 detect_ms=N
  + 231s  21:28:12  [SilenceMonitor] b6d354d9-44c — nudge_audit nudges=1 suppressed=0
```

---

## APPENDIX C — Commands behind every figure

Every number in this report can be re-derived from a single journal capture.

```
# The capture
journalctl -u talky-api -u talky-voice-worker --since today --no-pager > today.txt

# The scorecard
python3 scripts/call_scorecard.py today.txt
python3 scripts/call_scorecard.py today.txt --since 21:05:00
python3 scripts/call_scorecard.py today.txt --detail dec0bb16
python3 scripts/call_scorecard.py today.txt --format csv > scorecard.csv

# Section 4 — STT availability
grep -E "resilient_stt_stream_silent|resilient_stt_failed_over_to" today.txt
grep -c "stt_resilient_wrapper_active" today.txt
grep dec0bb16 today.txt | grep -o 'audio_level.*'

# Section 5 — nudging
grep nudge_audit today.txt
grep "nudge SUPPRESSED" today.txt
grep -o "\[SilenceMonitor\].*nudging.*" today.txt

# Section 6 — prompt cache
grep "llm_usage .*partial=False" today.txt \
  | sed -E 's/.*prompt_tokens=([0-9]+) cached_tokens=([0-9]+).*/\1 \2/' \
  | awk '{p+=$1; c+=$2; n++; if($2>0) h++} END {print n, p, c, h+0}'

# Section 7 — latency
grep -o "prompt_time=[0-9.]*" today.txt | cut -d= -f2 | sort -n
grep -o "client_ttft_ms=[0-9]*" today.txt | cut -d= -f2 | sort -n
grep -o "voice_slow_turn.*" today.txt
grep -o "first_turn_latency.*" today.txt

# Section 9 — reply length
grep -o "llm_response turn=[0-9]* said=.*" today.txt \
  | sed 's/^llm_response turn=[0-9]* said=//' | awk '{print NF}' | sort -n

# Section 10 — lead names
grep -c "call_target_field_dropped" today.txt
grep -c "call_target_field_sanitized" today.txt

# Section 13 / 14 — barge-in and resumed audio
grep -c "interrupt_complete" today.txt
grep -c "interrupt_step=nothing_playing" today.txt
grep -c "interrupt_FAILED" today.txt
grep -o "interrupt_step=cpp_interrupt.*" today.txt
grep -o "'gw_ms': [0-9]*" today.txt
grep interrupt_audio_audit today.txt

# Section 16 — disclosure
for p in recording_disclosure_spoken recording_disclosure_interrupted \
         recording_suppressed_no_disclosure; do
  printf "%-40s %s\n" "$p" "$(grep -c $p today.txt)"
done

# Section 17 — the pydantic attribute probe
python3 - <<'PY'
from app.domain.models.session import CallSession
s = CallSession(call_id="c", campaign_id="x", lead_id="l",
                provider_call_id="p", system_prompt="sp", voice_id="v")
for name in ("caller_voice_onset_at", "last_audio_rms", "_nudges_spoken"):
    try:
        setattr(s, name, 1.0)
        print(f"{name:26} SET OK   read-back={getattr(s, name)}")
    except Exception as e:
        print(f"{name:26} FAILED   {type(e).__name__}: {e}")
PY

# Section 18 — what the account can serve
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY" | jq -r '.data[].id' | sort
```

---

## APPENDIX D — Effective configuration

Read from the running process and the service environment, not transcribed from
source.

```
STT
  STT_FAILOVER_ENABLED           =     true    wrapper active on all 13 activations
  primary                        = deepgram flux-general-en
  secondary                      = deepgram nova-3
  STT_SECONDARY_MODEL            =  (unset)    -> nova-3 default
  FLUX_NUMERALS                  =  (unset)
  VOICE_GATEWAY_STT_REORDER      =  (unset)

ReconnectPolicy (STT failover)
  reconnect_timeout_seconds      =      0.5
  max_reconnect_attempts         =        1
  audio_buffer_ms                =      500    -> 12 chunks at 40ms, as observed
  failure_threshold              =        3
  recovery_timeout_seconds       =     30.0
  silent_stream_voiced_seconds   =      6.0    voiced audio before declaring death

STT fault injection (OFF)
  VOICE_STT_FAULT_SILENT_CAMPAIGN=  (unset)    inert
  VOICE_STT_FAULT_SILENT_UNTIL   =  (unset)    inert; unparseable would fail CLOSED

LLM
  provider                       = groq
  model                          = qwen/qwen3.6-27b
  LLM_FAILOVER_ENABLED           =     true
  LLM_SECONDARY_PROVIDER         =   gemini
  LLM_SECONDARY_MODEL            = gemini-2.5-flash
  llm_max_tokens                 =       90
  llm_temperature                =      0.6
  VOICE_MAX_HISTORY_PAIRS        =       20    40 messages; NOT the latency lever
  VOICE_PROMPT_CACHE_ORDER       =     true    correct, but inert on this model

Silence monitor / nudging
  VOICE_OPENING_HELLO_S          =      2.5
  VOICE_MID_NUDGE_S              =       16
  VOICE_SILENCE_HANGUP_S         =       60
  VOICE_NUDGE_MIN_GAP_S          =     15.0
  VOICE_OPENING_NUDGE_GAP_S      =      2.5
  VOICE_OPENING_MAX_NUDGES       =        3
  VOICE_MID_MAX_NUDGES           =        2
  VOICE_AUDIO_ACTIVE_RMS         =      500    LIVE for the first time on 3fa5cadf
  VOICE_AUDIO_ACTIVE_MAX_AGE_S   =      2.0

Voice-onset anchor (NEW, live on 3fa5cadf, unproven)
  VOICE_ONSET_RMS                =      500
  VOICE_ONSET_GAP_S              =      0.4
  _VOICE_ONSET_MAX_AGE_S         =     60.0

Interrupt / barge-in
  _INTERRUPT_DEDUPE_S            =     0.35
  _RESUME_WINDOW_S               =     0.75    attribution window, never a gate
  DEEPGRAM_MIN_INTERRUPT_WORDS   =  (unset)

Telephony audio
  RTP_FRAME_MS                   =       20
  AUDIO_CALLBACK_BATCH_FRAMES    =        2
  AUDIO_CALLBACK_INTERVAL_MS     =       40
  _AUDIO_GAP_WARN_MS             =      120    max(120, interval * 3)

Deepgram Flux (per call)
  eager_eot_threshold            =      0.7
  eot_threshold                  =     0.85
  eot_timeout_ms                 =      500

TTS
  _TTS_INTER_CHUNK_TIMEOUT_S     =      5.0
  empty-stream retry             =        1    NEW, gated on "nobody interrupted"
```

### Kill switches — no redeploy required

| switch | effect |
|---|---|
| `STT_FAILOVER_ENABLED=false` | disables the watchdog and Nova promotion entirely |
| `VOICE_PROMPT_CACHE_ORDER=false` | restores the pre-2026-08-13 prompt order |
| `VOICE_STT_FAULT_SILENT_CAMPAIGN=` | disarms fault injection |
| `VOICE_AUDIO_ACTIVE_RMS=999999` | effectively disables the acoustic nudge guard |
| `VOICE_ONSET_RMS=999999` | disables the voice-onset anchor |

---

*End of report 6.*
