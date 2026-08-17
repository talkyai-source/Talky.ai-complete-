# Report 6 — the scorecard you asked for, and what it found

**Date:** 2026-08-17
**Production HEAD:** `3fa5cadf` (from `04d8d4b8` at the start of the day)
**Calls examined:** 9 — six before the 21:05 deploy, three after
**Journal:** `journalctl -u talky-api -u talky-voice-worker --since today` — 44,413 lines

---

## 0. How to read this

You asked for ten things, measured per call, with evidence. Sections 2–11 answer
them one at a time. Every number is followed by the command that produced it and
the output that came back, because the whole reason this report exists is that
the previous ones contained claims I could not back.

Three of your asks could not be answered at all this morning, because the
instrumentation did not exist. Two of them now can. One of them still cannot,
and I will say exactly why rather than give you a plausible figure.

I also have to report that **two features I previously told you were live had
never executed a single time in production**, including one from this morning.
That is section 12, and it is the most important part of this document.

---

## 1. The scorecard

```
$ journalctl -u talky-api -u talky-voice-worker --since today --no-pager \
    | python3 scripts/call_scorecard.py

call       start      stt             stt-ok nudge  prompt tok   m2e   ttft  reply  barge  gaps
6090952e   20:37:02   ok              1      1/?    ?            ?     ?     -      0/1    0
dec0bb16   20:38:59   FAILOVER>nova   1      1/?    8385>8507    1029  638   27c    2/7    20
ae686b21   20:40:54   ok              1      1/?    8401>8701    1051  644   5c     3/14   25
defdd260   20:44:47   ok              1      3/?    8428>8746    1573  627   5c     3/10   26
a195284b   20:51:16   ok              1      1/?    8398>9044    ?     656   8c     1/16   47
b3350aee   21:02:20   ok              1      1/?    8401>8777    988   637   15c!   4/12   23
--------------------------------- 21:05 deploy ---------------------------------
ddf2c92c   21:19:11   FAILOVER>nova   1      1/0    8399>8545    1004  620   15c    9/21   31
319debb3   21:23:33   no-speech       0      0/0    ?            ?     ?     -      -      0
b6d354d9   21:24:21   FAILOVER>nova   1      1/0    8400>8751    999   629   10c    13/24  38
```

`?` means the journal cannot answer. It never means zero. That distinction is
load-bearing and the scorecard's tests enforce it.

---

## 2. STT availability — and the watchdog proved itself three times

**This is the strongest result of the day.** The silent-stream watchdog fired on
three separate live calls, unprompted, and rescued all three:

```
$ grep -E "resilient_stt_stream_silent|resilient_stt_failed_over_to" today.txt

20:40:28 ERROR [call=dec0bb16] resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
20:40:29 INFO  [call=dec0bb16] resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
21:20:48 ERROR [call=ddf2c92c] resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
21:20:48 INFO  [call=ddf2c92c] resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
21:27:29 ERROR [call=b6d354d9] resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
21:27:29 INFO  [call=b6d354d9] resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
```

Every one: six seconds of voiced caller audio into Flux, zero transcript events
back, Nova-3 promoted, **twelve buffered chunks replayed**. All three calls
continued normally afterwards — `ddf2c92c` ran to 21 interrupts, `b6d354d9` to
24, both with normal latency.

Two things worth drawing out.

**`buffered_chunks=12` is the design confirming itself.** A 500ms replay buffer
at 40ms per chunk holds twelve chunks. The unit test asserts twelve for that
reason, and production produced twelve, three times independently. That is the
"preserves the buffered caller utterance" half of your ask, observed live.

**These were mid-call deaths, not turn-0 deaths.** `ddf2c92c` started at 21:19:11
and failed over at 21:20:48 — 97 seconds in, after Flux had been answering
normally. The watchdog only catches that because `observe_transcript()` re-arms
the counter on every transcript, so a stream that dies at minute nine is detected
exactly like one that was never alive. The 2026-08-13 incident was the turn-0
case; today's were the harder one.

**A caveat I owe you.** Three failovers across eight answered calls is a much
higher rate than the two-in-thirty-six that started this work. I cannot tell you
whether Flux is degrading or whether the watchdog is firing on transient stalls
that would have recovered on their own — from here, "dead" and "quiet for six
seconds of speech" are the same observation, and the watchdog deliberately
treats them the same because the cost of a false trip is one extra socket and a
replayed buffer, while the cost of missing one is the whole call. The outcome
was right in all three cases. The rate is worth watching.

---

## 3. Proving the watchdog deliberately — the fault injector

You asked me to prove the promotion, and to use controlled fault injection
rather than breaking the live provider if it did not happen naturally. It
happened naturally three times, so the injector is now a second line of evidence
rather than the only one. It is shipped and inert:

```
VOICE_STT_FAULT_SILENT_CAMPAIGN=<one campaign uuid>
VOICE_STT_FAULT_SILENT_UNTIL=2026-08-18T23:00:00Z
```

It is scoped to a single named campaign — there is no boolean that turns it on
globally — the expiry is mandatory and **fails closed** if unparseable, and it
refuses outright when `STT_FAILOVER_ENABLED` is off, because deafening a primary
with no secondary destroys a call rather than testing one. Every activation logs
at ERROR.

The offline proof asserts on **content**, not just promotion. Each audio chunk's
amplitude encodes its index, so the test can name the audio the secondary
received:

```
assert heard[:5] == [26, 27, 28, 29, 30]   # spoken into the dead stream
assert heard == list(range(26, 41))        # then the rest of the call, in order
```

A failover that swaps providers and drops the sentence the caller was halfway
through is not a rescue. That test fails if the buffer is dropped, reordered or
duplicated.

---

## 4. Nudge-over-speech — the guard has never once fired

```
$ grep nudge_audit today.txt
[SilenceMonitor] ddf2c92c-eeb — nudge_audit nudges=1 suppressed=0
[SilenceMonitor] 319debb3-5c8 — nudge_audit nudges=0 suppressed=0
[SilenceMonitor] b6d354d9-44c — nudge_audit nudges=1 suppressed=0
```

Two nudges spoken, **zero suppressed**. On 2026-08-13, 28 of 35 nudges landed on
a caller who was audibly mid-sentence, and the fix for that shipped the same
day. I reported it as live. It was not: see section 12. It is live as of
`3fa5cadf` and has not yet been exercised by a call.

The `nudge_audit` line itself is new and deliberately written on **every** call,
including `suppressed=0`. A verdict that only appears on failure cannot be
distinguished from a check that never ran, which is the exact reporting defect
that let two dead STT streams pass for quiet callers.

---

## 5. Prompt cache hit ratio — zero, and it is not fixable by ordering

```
$ grep "llm_usage .*partial=False" today.txt | awk ...
turns=74 prompt_tokens=619588 cached_tokens=0 turns_with_hit=0
```

**Zero hits in 619,588 prompt tokens.** I told you in report 5 that this was
caused by the per-turn LIVE STATE block sitting at character 0, and that
reordering would recover ~600ms. That was wrong, and a fix shipped on the
strength of it. A direct probe — two byte-identical 2,418-token prompts, back to
back:

```
qwen/qwen3.6-27b       first  200 prompt_tokens=2418 cached=None
                       second 200 prompt_tokens=2418 cached=None
llama-3.1-8b-instant   404 — "does not exist or you do not have access to it"
```

`cached` is **None, not zero** — Groq reports no caching for this model at all.
And `llama-3.1-8b-instant`, whose 6,656-token hits I cited as proof that caching
worked on this account, is not on the account. The comparison that anchored the
entire diagnosis was never valid.

Groq's documentation confirms it independently: prompt caching is supported on
the **GPT-OSS family only**, and it wants static content first, variable content
last — which our prompt order already does. The 2026-08-13 reorder is correct by
construction and buys nothing here. I have kept it and corrected the docstring
rather than quietly rewriting it.

Measured alternatives on this account, at production prompt size:

| model | cold TTFT | warm TTFT | cached |
|---|---|---|---|
| `qwen/qwen3.6-27b` (current) | 697ms | 672ms | none |
| `openai/gpt-oss-120b` | 451ms | **102ms** | 7,168 / 7,281 |
| `openai/gpt-oss-20b` | 475ms | 119ms | 7,168 / 7,281 |

That is the 629ms of prefill collapsing to ~100ms on every turn after the first,
without touching a word of tuned prompt. **I have not switched the voice
default** — GPT-OSS was removed in June for stacking questions and spelling
things out on voice calls, and a latency win does not overturn a
conversation-quality finding. Both are back in the menu as preview, with the
measurement and the caveat written into the description.

---

## 6. Mouth-to-ear latency — and where it actually goes

```
first-turn mouth-to-ear p50   1002ms   (post-deploy calls)
LLM TTFT p50                   629ms
prompt_time p50                615ms
```

Prefill is not *part* of the latency, it is nearly all of it: 615ms of a 629ms
time-to-first-token. Every slow turn today has the identical fingerprint —

```
$ grep -o "voice_slow_turn.*" today.txt
stt_first_ms=0.2    llm_first_token_ms=1502.2  tts_first_chunk_ms=215.8
stt_first_ms=75.0   llm_first_token_ms=1346.8  tts_first_chunk_ms=198.6
stt_first_ms=237.7  llm_first_token_ms=1859.9  tts_first_chunk_ms=206.6
```

Recognition instant, synthesis ~200ms consistently, the LLM 1.3–1.9 seconds.
There is no second bottleneck to find.

The new `prompt tok` column shows first turn → last turn, and it corrected a
figure I produced myself: composing a bare prompt offline gives 6,498 tokens, but
production starts at **~8,400**. Decomposition: ~6,500 base persona/guardrails,
~1,900 per-call blocks (knowledge injection alone is 3,328 characters), and only
~650 of growth across 22 turns. **History is not the lever.** Shrinking
`VOICE_MAX_HISTORY_PAIRS` would buy almost nothing and cost the agent its memory
of the call.

I measured the "developer debris" theory too — dates, code references, A/B prose
left in a tuned prompt. It came to **eighteen tokens**, with zero duplicated
lines. Over half the base is one block of stage-by-stage conversational
instruction, produced by a 96-call A/B matrix. I have not cut it. Trading a
measured 300ms for an unmeasured conversation-quality regression, on my own
judgement, in an afternoon, is not a call I should make.

---

## 7. Prompt / state regressions — none, and the gap that hid it

No zero-token turns, no composition failures, knowledge injected consistently at
3,328 chars. The scorecard originally reported `knowledge=0` for every call,
which was not a product fault: `campaign_knowledge_injected` logged with
`[call=-]` and could not be joined to a call. It now carries a `call_id` and a
`prompt_chars` figure. `telephony_prompt_composed` deliberately keeps no call id
— it runs while building the session config, before a call exists — but gained
`prompt_chars`, with a comment so the absence reads as a decision.

---

## 8. Short replies — the fix holds

```
llm_response turn=7  said='Blueprint.'
llm_response turn=0  said='Alex, from Talk-Lee.'
llm_response turn=9  said="Yeah, I'm here."
llm_response turn=4  said="Yeah, I'm still here."
```

Earlier calls spoke five-character replies (`5c` in the table). Before
`e17b33d1`, `turn_streamer` dropped any sentence under six characters, so "Yes."
and "Okay." produced no audio at all. Reply length across the day: **p50 8 words,
p90 15, max 22** — which also retracts my earlier claim that the agent was
delivering six-second monologues. Six seconds is simply how long fifteen to
twenty words takes to say; I had extrapolated from four tail samples.

---

## 9. Invalid lead-name handling — clean

No `call_target_field_dropped` events today; the plausibility guard had nothing
to reject. The 2026-08-13 defect (`first_name='Call'`, so every call opened *"Hi,
is this Call?"*) does not recur — today's openers are correct:

```
llm_response turn=0 said='Hey there — this is Alex from Talk-Lee, just checking in on a quick voice test. Got a sec?'
```

Permission-based, one breath, name then ask then reason. That is the intended
opener shape, holding.

---

## 10. Direct-question behaviour — answer-first is working

This is the one dimension the journal genuinely could not score, because caller
speech is redacted by design. But the agent's side is in the clear, and the
answers are self-evidently answers rather than deflections:

```
turn=3  "There's no cost — this is just an internal system test."
turn=4  "Yeah, I'm still here."
turn=11 "I didn't ignore it — I just didn't have a price to give you."
turn=9  'You asked me to remember "blueprint," and you interrupted me twice.'
turn=12 "Fair point — I'll keep that in mind."
```

Every one leads with the answer and stops. None attaches a counter-question. The
"blueprint" turn shows working recall across the conversation *and* accurate
awareness of having been interrupted twice. The price question — historically
the highest-risk case, where small models invent figures — is answered by
declining to invent one.

---

## 11. Transfer behaviour and genuine audible barge-in

No transfer was attempted on any call today, so that dimension is untested.

Barge-in is where the numbers get interesting:

```
real interrupts         22
no-ops (ordinary turn)  23
failed interrupts        0
gw_ms  total=35 nonzero=23 p50=240ms max=300ms
```

Roughly half of all "interrupts" are ordinary turns with nothing playing —
`barge_in_detected` cannot be read as "the agent was talked over". On the 22 that
were real, we discarded a **median 240ms** of already-queued agent audio, which
is the gateway audio the caller did *not* hear because we binned it.

```
$ grep interrupt_audio_audit today.txt
interrupt_audio_audit call=talky-out-a3 interrupts=11 resumed_chunks=0 stale_rejected=0 verdict=clean
interrupt_audio_audit call=talky-out-97 interrupts=16 resumed_chunks=0 stale_rejected=0 verdict=clean
```

**Did cancelled audio resume? No — 27 interrupts, zero resumed chunks.** That is
a positive statement written on clean calls, not an absence of alarms. It matters
because the utterance-id rotation protects a narrower window than its comment
claimed: a chunk stamped before the rotation is rejected, but one arriving after
it picks up the fresh id and is accepted. Several layers should stop one ever
arriving, and now we count instead of assuming.

**What I still cannot give you is the caller-speech-to-audible-stop latency.**

```
interrupt_complete {... 'elapsed_ms': 1.6, 'detect_ms': None, 'speech_to_stop_ms': None ...}
```

`None` on all 45 occurrences. The measurement shipped this morning and recorded
nothing. That is section 12.

---

## 12. The failure I have to report: two features that never ran

Post-deploy calls came back with `detect_ms: None` on every interrupt. Probing
the real model explains it:

```
caller_voice_onset_at   FAILED  ValueError: "CallSession" object has no field "caller_voice_onset_at"
last_audio_rms          FAILED  ValueError: "CallSession" object has no field "last_audio_rms"
_nudges_spoken          SET OK  read-back=1.0
_tts_fallback_attempted SET OK  read-back=1.0
```

`CallSession` is a pydantic v2 model whose config does not set `extra="allow"`,
so assigning an **undeclared** attribute raises. Private names — leading
underscore — are accepted, because pydantic routes them to its private-attribute
store. `session._foo = 1` works; `session.foo = 1` does not; the difference is
invisible at the call site.

Both writes were wrapped in `try/except Exception: pass`, on the correct
principle that a measurement must never cost a call. So the exception was
swallowed on every frame of every call, and two features never executed:

* **The acoustic nudge guard, shipped 2026-08-13.** `_audio_active` read False
  forever because `last_audio_rms` was never stored. It has never suppressed a
  single nudge. Report 5 described it as live. Production agrees with the code,
  not with the report: `suppressed=0` on every audited call.
* **The caller voice-onset anchor, shipped this morning.** Hence `detect_ms:
  None`, and hence the one question in your brief I still cannot answer.

**Why the tests passed throughout.** Both features were tested against
`types.SimpleNamespace`, which accepts any attribute. The suite was green against
a double with the one property the real object lacks. `test_session_scratch_attrs`
now exercises a real `CallSession`, including an explicit test that the
non-underscore form still raises — so the convention is recorded as a constraint
rather than mistaken for style.

Fixed and deployed in `3fa5cadf`. Both features are live for the first time. The
onset anchor is deployed but **unproven** — it needs one call with a deliberate
mid-sentence interruption before I will claim it works, and I am not going to
repeat this morning's mistake of reporting a shipped fix as a working one.

---

## 13. Recording-disclosure retention — still open, as agreed

```
recording_disclosure_spoken              5
recording_disclosure_interrupted         0
recording_suppressed_no_disclosure       0
```

No recordings were lost today, but only because nobody talked over the notice.
The underlying problem is unchanged and untouched: when a caller barges over the
disclosure, the recording is suppressed entirely. A "retry from the top" fix was
built and reverted on 2026-08-11 (callers hung up ~2s after hearing the notice
restart). My recommendation stands — finish the notice at the start of the
agent's *next* turn, never over the caller, and retain the recording once it
completes — but that changes *when a notice counts as delivered*, which is a
retention-policy decision, not an engineering one. **Task #44 remains open
pending your call.**

---

## 14. Summary of the day's changes

| commit | what |
|---|---|
| `e63a907c` | STT fault injection — campaign-scoped, expiring, fails closed |
| `b0c00358` | caller-heard interrupt measurement, resume audit, nudge audit |
| `04d8d4b8` | call-by-call scorecard + report 5 correction |
| `26f47bcb` | barge-in was being filed as a broken TTS provider |
| `9e56613d` | prompt-size measurement + cache root-cause correction |
| `b7a03450` | model menu offered two models the account cannot serve |
| `3fa5cadf` | two features were silently dead — pydantic attribute names |

Gate throughout: 4,773 passed, with the same 8 pre-existing missing-secrets
failures as pristine HEAD plus one known intermittent (task #54).

**Open, deliberately:** #43 prompt size (needs an A/B or a model change), #44
disclosure retention (yours), #54 the `id(db_pool)` credential-cache key, #59
five tenant configs still pointing at a model that 404s.

**What would close the last gap in your brief:** one call where you interrupt the
agent mid-sentence. That produces `detect_ms` and `speech_to_stop_ms` for the
first time, and confirms the nudge guard now suppresses rather than shouting.
