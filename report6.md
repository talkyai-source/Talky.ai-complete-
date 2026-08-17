# Report 6 — six calls, four findings, and two of my own conclusions overturned

**Date:** 2026-08-17
**Production HEAD at start:** `04d8d4b8`
**Calls examined:** 6 answered calls, 20:37:02 → 21:04:37 UTC, plus one ring-out at 21:19:11
**Journal:** `journalctl -u talky-api -u talky-voice-worker --since today` — 31,728 lines

---

## 0. What this report is

Report 5 covered a 40-call canary read by hand. This one covers six calls read by
the tool that run produced — `scripts/call_scorecard.py` — and it is a different
kind of document because of that. Three of the four findings below were surfaced
by the tool in its first minute of use. Two of them contradict things I told you
earlier in the day.

Every claim here is followed by the command that produced it and the output that
came back. Where I have no evidence I say so, and where I was wrong I say that
plainly rather than quietly restating the corrected version.

The theme of report 5 was *a signal that cannot represent the failure will report
success*. The theme of this one is narrower and more uncomfortable: **a signal
that represents the wrong failure will send you to fix the wrong thing.** One
mislabelled log line in this run took me most of the way to shipping a change
that would have made the product worse.

---

## 1. The run at a glance

```
$ journalctl -u talky-api -u talky-voice-worker --since today --no-pager \
    | python3 scripts/call_scorecard.py

call       start      stt             stt-ok nudge  prompt tok   m2e   ttft  reply  barge  gaps
6090952e   20:37:02   ok              1      1/?    ?            ?     ?     -      0/1    0
dec0bb16   20:38:59   FAILOVER>deepg  1      1/?    8385>8507    1029  638   27c    2/7    20
ae686b21   20:40:54   ok              1      1/?    8401>8701    1051  644   5c     3/14   25
defdd260   20:44:47   ok              1      3/?    8428>8746    1573  627   5c     3/10   26
a195284b   20:51:16   ok              1      1/?    8398>9044    ?     656   8c     1/16   47
b3350aee   21:02:20   ok              1      1/?    8401>8777    988   637   15c!   4/12   23
ddf2c92c   21:19:11   no-speech       0      0/?    ?            ?     ?     -      -      0
```

`?` means the journal cannot answer, never that the answer is zero. The
`nudge` and `barge` detail columns read `?` because the instrumentation that
fills them deployed at 21:05 and every answered call here predates it.

---

## 2. Finding 1 — the STT watchdog fired in production and saved a call

This is the good news, and it is the first time the 2026-08-13 fix has been
observed working on real traffic rather than in a test.

```
$ grep -E "resilient_stt_stream_silent|resilient_stt_failed_over_to" today.txt

20:40:28 ERROR [resilient_stt] [call=dec0bb16-7be8-4512-a5d3-a19336df82da]
  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
  — 6.0s of caller speech went in and no transcript event came back

20:40:29 INFO  [resilient_stt] [call=dec0bb16-7be8-4512-a5d3-a19336df82da]
  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
```

Deepgram Flux went deaf mid-call. The watchdog caught it after six seconds of
unanswered speech, promoted Nova-3, and replayed the twelve buffered chunks the
caller had already spoken into the dead stream. The call then ran normally — six
agent turns, first-turn mouth-to-ear 1029ms, two genuine barge-ins.

Two details worth noting:

* **`buffered_chunks=12`** is exactly what the design predicts: a 500ms replay
  buffer at 40ms per chunk. The unit test asserts 12 chunks for the same reason,
  and production produced 12. That is the mechanism confirming itself.
* **On 2026-08-13 this call would have been lost.** Two of thirty-six answered
  calls died this way that day, invisible to both safety nets.

The fault injector shipped this morning (`VOICE_STT_FAULT_SILENT_CAMPAIGN`)
remains available for deliberate testing, but it is no longer the only evidence
the fix works.

---

## 3. Finding 2 — a silent turn, and the label that nearly cost us

### 3.1 What the log said

```
$ grep turn_silent_reason today.txt

21:02:29 WARNING [voice_pipeline_service] [call=b3350aee-...]
  turn_silent_reason call_id=b3350aee-76aa-4248-89fb-acc13d8ceddd
  reason=provider_empty_stream
```

Read on its own, this says the TTS provider returned no audio for a reply the
agent had composed. That is a real failure mode, it has no recovery in the code,
and it produces dead air on a live phone call. I opened it as a defect and wrote
the fix: retry the synthesis once, and if the retry is also empty, speak a short
fallback rather than leave silence.

The reasoning was sound and had good precedent. This loop already handled the two
failure modes that announce themselves:

| failure | how it shows up | recovery |
|---|---|---|
| provider raises | `Exception` | caught, spoken fallback |
| provider goes quiet | `asyncio.TimeoutError` | synthesis retried once |
| **provider returns nothing** | **`StopAsyncIteration`, instantly** | **none** |

The third row is the same shape as the STT socket that accepted 400 chunks and
answered nothing: the one failure with no symptom was also the one with no
recovery. It does not raise, and it cannot time out, because there is nothing to
wait for.

### 3.2 What the log actually meant

Before shipping I pulled the surrounding sequence rather than trusting the label.

```
$ grep b3350aee today.txt | grep -E "21:02:2[7-9]" | grep -v audio_level

21:02:27 Flux StartOfTurn - User started speaking, barge-in detected
21:02:27 barge_in_detected
21:02:27 interrupt_step=begin            interrupt_id=14f693a9f2fe
21:02:27 interrupt_step=state_listening
21:02:27 interrupt_step=task_cancelled
21:02:27 interrupt_step=nothing_playing  interrupt_id=14f693a9f2fe
21:02:29 Flux EndOfTurn: 'Hi, Alex. Who are you?'
21:02:29 t_stt_first_final
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
the agent's reply had started playing. The turn correctly produced no audio —
that is what a barge-in is *for* — and the code filed it as a provider fault.

Had I shipped the fix as written, a caller who interrupted the agent would have
been answered by the agent re-synthesising the reply they had just talked over,
and then, if that failed, saying *"Sorry — could you say that again?"* to someone
who had not finished their sentence. That is worse than the silence it was
fixing.

### 3.3 The actual root cause

The defect is the classification, not the recovery. Two entirely different events
were being given the same name:

* the caller stopped the turn before audio started — **normal, correct, expected**;
* nobody stopped it and the provider still returned nothing — **a real fault**.

Both produced `provider_empty_stream`. So the metric that exists to count dead
air was counting successful barge-ins, and anybody reading it would be pointed at
the wrong subsystem — as I was.

### 3.4 The fix

`tts_playback.py` now distinguishes them, using the one piece of state that
answers the question precisely — `session.tts_active`, which
`interrupt_playback`'s first step clears:

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

The retry and the fallback both survive, because the genuine failure mode is
still real and still unhandled — but both are now gated on the same condition, so
neither can ever fire at a caller who interrupted.

Two further bugs surfaced while testing this, both found by tests I had just
written:

**(a) The nested fallback polluted the verdict.** The fallback is a recursive
`synthesize_and_send`, and its own `finally` clears `session.tts_active`. A
`finally` block that re-read the flag therefore saw the *nested* call's teardown
and labelled the outer turn `interrupted_before_audio` when no caller had
interrupted anything. Fixed by capturing the verdict at the moment the stream
ends, not re-reading it later:

```python
stopped_by_caller_at_end: Optional[bool] = None   # captured, never re-read
```

**(b) The recovery attempt filed its own silent-turn record.** One unrecoverable
turn was being counted twice in the very metric that measures unrecoverable
turns. Fixed by reading the fallback flag at method entry, the one point where it
unambiguously means "this invocation is the recovery, not the turn":

```python
_is_recovery_attempt = bool(getattr(session, "_tts_fallback_attempted", False))
...
if silent_reason is not None and not _is_recovery_attempt:
    self._p._record_silent_turn(call_id, silent_reason)
```

### 3.5 Tests

`backend/tests/unit/test_tts_empty_stream.py` — 11 tests. The ones that matter
most are the negatives:

```
test_a_barge_in_before_the_first_chunk_is_never_retried[True]
test_a_barge_in_before_the_first_chunk_is_never_retried[False]
test_a_barge_in_before_the_first_chunk_gets_no_fallback_either
test_the_two_silent_causes_are_labelled_differently
test_a_working_provider_is_synthesised_exactly_once
test_a_provider_that_yields_one_chunk_then_stops_is_not_retried
```

The last two are load-bearing: a retry that fired on a healthy turn would make
the agent say every sentence twice, and a retry after audio had already played
would repeat words the caller had heard. The retry is only ever safe before the
first chunk, and only when nobody interrupted.

The production journal excerpt above is pasted into that test file as a comment,
so the next person to read it sees the evidence, not just the assertion.

---

## 4. Finding 3 — prompt size is the whole latency story, and nothing else is

### 4.1 The measurement

```
$ grep -o "prompt_time=[0-9.]*" today.txt | cut -d= -f2 | sort -n | awk ...
n=82 p50=629ms p90=706ms max=755ms

$ grep -o "client_ttft_ms=[0-9]*" today.txt | cut -d= -f2 | sort -n | awk ...
n=82 p50=641ms p90=738ms
```

**Prefill is essentially the entire time-to-first-token.** 629ms of a 641ms
median. Everything else — network, queueing, the model's first token — is the
remaining twelve milliseconds.

The four slow turns of the day all carry the same fingerprint:

```
$ grep -o "voice_slow_turn.*" today.txt

call=ae686b21 turn=9  response_start_ms=1762.0  stt_first_ms=0.2   llm_first_token_ms=1502.2  tts_first_chunk_ms=215.8
call=defdd260 turn=1  response_start_ms=1573.0  stt_first_ms=75.0  llm_first_token_ms=1346.8  tts_first_chunk_ms=198.6
call=defdd260 turn=2  response_start_ms=2072.2  stt_first_ms=237.7 llm_first_token_ms=1859.9  tts_first_chunk_ms=206.6
call=a195284b turn=1  response_start_ms=1608.4  stt_first_ms=0.4   llm_first_token_ms=1399.3  tts_first_chunk_ms=203.2
```

Speech recognition: instant. Speech synthesis: ~200ms, consistently. The LLM:
1.3–1.9 seconds, every time. There is no second bottleneck to find.

### 4.2 Where the tokens are

The scorecard's new `prompt tok` column shows first turn → last turn:

```
dec0bb16   8385>8507
ae686b21   8401>8701
defdd260   8428>8746
a195284b   8398>9044     (22 turns)
b3350aee   8401>8777
```

This immediately corrected a number I had produced myself. Composing a bare
`lead_gen` prompt offline gave 6,498 tokens, and I reported that as the base.
Production starts at **~8,400**. The decomposition is therefore:

| component | tokens | note |
|---|---|---|
| persona + guardrails base | ~6,500 | measured offline via `compose_prompt` |
| per-call blocks (knowledge, live state, captured, trailing) | ~1,900 | the difference |
| history growth over 22 turns | ~650 | `8398 → 9044` |

**History is not the problem.** Twenty-two turns of conversation added 650
tokens; the constant preamble is thirteen times that before the caller says a
word. Shrinking `VOICE_MAX_HISTORY_PAIRS` — the obvious lever — would buy almost
nothing and would cost the agent its memory of the call.

The knowledge block is measurable and substantial:

```
$ grep -o "campaign_knowledge_injected.*" today.txt | head -1
campaign_knowledge_injected campaign=c2b6734d-899 mode=inline chars=3328
```

### 4.3 Where it is *not*

I had a hypothesis that a tuned prompt accumulates developer-facing debris —
changelog notes, dates, source-file references — that the model pays for on every
turn. It is a real pattern and I found an instance of it:

```
STAGE 1 — OPEN (2026-08-11: you do NOT speak first anymore. A bare pickup
greeting ... see telephony_session_config.build_telephony_greeting.
```

So I measured it rather than assuming it mattered:

```
ISO date annotation           1 hits     12 chars  ~    3 tok
source file/symbol ref        0 hits      0 chars  ~    0 tok
A/B evidence prose            4 hits     60 chars  ~   15 tok
LINES containing a date or a code reference: 1  73 chars  ~18 tokens
EXACT DUPLICATE LINES (>40 chars): 0 distinct, 0 chars wasted
```

**Eighteen tokens.** The hypothesis was right in kind and irrelevant in
magnitude. There are also zero duplicated lines, so there is no dedup win either
— which is itself a good sign about the prompt's condition.

The section breakdown shows where the mass genuinely is:

```
section                                              chars   ~tok     %
## COMMUNICATION PRINCIPLES (apply to every reply)  14,191  3,492  53.7%
## HARD RULES — these override everything below      2,474    609   9.4%
## NON-NEGOTIABLES                                   1,434    353   5.4%
## FACTS — SOURCE OF TRUTH                           1,247    307   4.7%
## WRONG PERSON / GATEKEEPER                           912    224   3.5%
## SOUND HUMAN, NOT SCRIPTED                           836    206   3.2%
   ... 12 further sections, each under 3.2% ...
```

### 4.4 Why I am not cutting it

Over half the base prompt is one block of stage-by-stage conversational
instruction. It is not padding — it is the tuned content, and the record shows it
was tuned with evidence: a 96-call offline matrix and a 48-call ablation, which
produced findings as specific as *the price guard must sit adjacent to the
injected knowledge or small models invent figures (11/12 → 0/12)*.

Cutting a prompt built that way, on my own judgement, in an afternoon, with no
A/B, is not engineering. It would trade a measured 300ms for an unmeasured
regression in conversation quality, and the whole point of the last two reports
is that unmeasured claims are how this system got into trouble.

**So this stays open as a decision for you, with the data attached rather than a
change attached.** Task #43. The honest options:

1. **Trim `COMMUNICATION PRINCIPLES` behind an A/B.** Biggest lever (~3,500
   tokens, so potentially ~250ms). Requires the offline harness the previous
   audit used, and a comparison run — not a judgement call.
2. **Move the knowledge block out of the prompt into a tool call.** ~3,300 chars
   today, and it is only needed on turns that actually reference it.
3. **Change model.** A model with prompt caching turns 629ms of prefill into
   near-zero on turns 2+, without touching a word of tuned prompt. Given §5, this
   is probably the highest value-per-risk option available.

What I *have* shipped is the measurement: `telephony_prompt_composed` now carries
`prompt_chars`, `campaign_knowledge_injected` carries `prompt_chars` and a
`call_id`, and the scorecard tracks first→last prompt size per call. Whatever you
choose, the effect will be visible immediately.

---

## 5. Correction 1 — the prompt-cache fix does nothing, and my root cause was wrong

This corrects report 5 §on caching, and it is the more serious of the two
corrections because a fix was shipped on the strength of it.

**What I told you:** the 0% cache hit rate was caused by the per-turn LIVE STATE
block sitting at character 0, breaking the cacheable prefix. I cited
`llama-3.1-8b-instant` hits of up to 6,656 tokens on the same account as proof
that caching worked and the prefix was at fault — *"it was never a model
limitation; it was this line."*

**What the evidence says.** Four days after the reorder shipped:

```
$ grep "llm_usage .*partial=False" today.txt | awk ...
turns=74 prompt_tokens=619588 cached_tokens=0 turns_with_hit=0
```

Zero hits in 619,588 prompt tokens across 74 turns. So I tested the model
directly — two byte-identical 2,418-token prompts, back to back:

```
qwen/qwen3.6-27b       first  status=200 prompt_tokens=2418 cached=None
                       second status=200 prompt_tokens=2418 cached=None
llama-3.1-8b-instant   status=404 — "The model `llama-3.1-8b-instant` does not
                                    exist or you do not have access to it."
```

Two things fall out of that:

* **`cached` is `None`, not `0`.** Groq does not report prompt caching for this
  model at all. There is no prefix arrangement that produces a hit.
* **The control no longer exists.** The model whose 6,656-token hits I used as
  proof that "caching works on this account" is not on the account. The
  comparison that anchored the entire diagnosis was never valid.

**Consequence.** `VOICE_PROMPT_CACHE_ORDER` is live, harmless, and buys nothing.
I have not removed it — the ordering is correct by construction and costs
nothing, and it would matter immediately on a model that caches. But it must not
be described as a latency fix, and the 629ms of prefill it was supposed to remove
is entirely still there.

The docstring in `build.py` asserted the wrong root cause in forty lines of
confident prose. It now carries the correction above it, including the probe
output, rather than being quietly rewritten — the mistake is more useful to the
next reader than a tidy explanation would be.

---

## 6. Correction 2 — the agent is not too verbose

Earlier today I told you the agent was talking for six to seven seconds a turn
and called it a monologue driving the interrupt volume. I based that on
`tts_total_ms` from the four slow turns.

```
$ grep -o "llm_response turn=[0-9]* said=.*" today.txt | ... | awk '{print NF}'
n=64 min=1 p50=8 p90=15 max=22       (words per reply)
```

**Median reply: eight words.** The longest of the day was twenty-four:

> *"Hmm, this is just a system validation call, so I can't help with money — is
> there something else on your mind?"*

Six to seven seconds is simply how long fifteen to twenty words takes to say. I
extrapolated a population from four tail samples, which is exactly the error the
scorecard exists to prevent, and I made it anyway on the same day I shipped the
scorecard. No change needed; task closed as retracted.

---

## 7. Finding 4 — two log lines that could not be attributed to a call

The scorecard reported `knowledge=0` and `composed=0` for every call in the run.
That was not a product fault and not a parsing bug — both lines log with
`[call=-]`:

```
[req=-] [call=-] telephony_prompt_composed persona=lead_gen agent=Alex ...
[req=-] [call=-] campaign_knowledge_injected campaign=c2b6734d-899 mode=inline chars=3328
```

`campaign_knowledge_injected` **has** a call session in scope and now uses it, and
also reports the resulting prompt size:

```python
logger.info(
    "campaign_knowledge_injected campaign=%s mode=%s chars=%d prompt_chars=%d",
    campaign_id[:12], mode, len(tree), len(call_session.system_prompt or ""),
    extra={"call_id": getattr(call_session, "call_id", None)},
)
```

`telephony_prompt_composed` **deliberately keeps none.** The prompt is composed
while building the session config, before a call exists. Threading a call id
through a public builder for the sake of a log line is the tail wagging the dog,
and the per-call view is already available from `llm_usage`, which does carry
one. It gained `prompt_chars` instead — the number that is actually actionable
given §4 — and a comment explaining why the attribution is absent, so the next
person does not spend an hour rediscovering it.

---

## 8. What else the run showed

**Short replies survive.** Two calls spoke five-character replies (`5c` in the
table). Before `e17b33d1`, `turn_streamer` dropped any sentence under six
characters, so "Yes." and "Okay." produced no audio at all. This is the first
production evidence that fix holds.

**The audio gaps are confirmed harmless.** The `arrived_ratio` instrumentation
shipped this morning reads p50 0.996–0.998 across the three long calls. Packets
are bunching, not going missing. That was my conclusion from measurement on
2026-08-13; it is now confirmed on independent data rather than inferred.

**Barge-in accounting is still badly misleading.** `a195284b` logged sixteen
interrupts of which **one** was real; the other fifteen were ordinary turns.
`barge_in_detected` cannot be read as "the agent was talked over". The
instrumentation to separate these deployed at 21:05 and has no data yet.

**Recording disclosure was clean** — 5 spoken, 0 interrupted, 0 suppressed. The
task #44 problem did not recur, but only because nobody talked over the notice.
It remains unfixed and remains your decision.

---

## 9. Changes in this wave

| file | what |
|---|---|
| `voice_pipeline/tts_playback.py` | empty-stream retry + fallback, both gated on "nobody interrupted"; `interrupted_before_audio` vs `provider_empty_stream`; verdict captured not re-read; recovery attempt no longer files its own silent-turn record |
| `scripts/prompts/build.py` | docstring correction — the cache root cause was wrong, with the probe output |
| `scripts/knowledge/session_inject.py` | `call_id` + `prompt_chars` |
| `telephony_session_config.py` | `prompt_chars`, and why there is no `call_id` |
| `scripts/call_scorecard.py` | `prompt tok` column (first→last), TTS empty-stream counters, cache demoted to a summary line |
| `tests/unit/test_tts_empty_stream.py` | **new** — 11 tests, including the production journal excerpt as evidence |

**Gate:** 4,752 passed, 8 failed, 5 errors — the 13 failures are identical to the
pristine-HEAD baseline (missing-secrets artifacts), and the pass count is +11,
matching the new tests exactly.

---

## 10. Still open, deliberately

| # | item | why it is not fixed |
|---|---|---|
| 43 | prompt size / prefill | Needs an A/B or a model change, not a judgement call. Data in §4. |
| 44 | recordings lost to disclosure barge-in | Product/compliance decision, yours. |
| 54 | `CredentialResolver._CACHE` keyed on `id(db_pool)` | Real defect — CPython reuses freed addresses, so a replaced pool can inherit another pool's cached credential, with no TTL. Credential path; not shipping it unannounced. |

---

## 11. What I would want from the next run

Everything in §3 and the barge-in columns are still unmeasured, because every
answered call today predates the 21:05 deploy. A handful of calls that include a
deliberate mid-sentence interruption would produce, for the first time:

* `detect_ms` — how long the caller had been talking before we began stopping;
* `speech_to_stop_ms` — mouth open to agent silent;
* `interrupt_audio_audit … verdict=clean|AUDIO_RESUMED` — whether cancelled audio
  ever restarts;
* `nudge_audit nudges=N suppressed=M` — whether the acoustic guard is doing
  anything;
* and, now, whether `interrupted_before_audio` correctly absorbs the silent turns
  that used to be filed as provider faults.

That last one is the direct test of this report's main fix, and it needs exactly
the behaviour that produced the bug: interrupt the agent before it starts
speaking.
