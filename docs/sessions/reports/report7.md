# Report 7 — Three Guards, One Recurring Mistake, and the Day They Started Working

**Date:** 2026-08-18
**Production HEAD at start:** `3fa5cadf`
**Production HEAD now:** `f750b1f2`
**Calls examined:** 36
**Journal:** 113,090 lines
**Deploys today:** `f05629f5` (STT echo guard, 20:24) · `f750b1f2` (gateway drain + margin probe, 21:3x)

---

## Table of contents

```
 0.  How to read this report
 1.  Executive summary
 2.  The headline result — 36% -> 9%
 3.  FINDING 1 — the STT watchdog was counting the agent's own voice
 4.  FINDING 2 — post-interrupt silence: a flag that is set in one place and
                 cleared in eight-ways-to-skip
 5.  FINDING 3 — the interrupt that asked nothing (call d068f4b8 forensics)
 6.  FINDING 4 — detection latency is the real barge-in cost
 7.  What shipped today
 8.  The measurement that gates the next fix
 9.  Corrections to my own claims in this report cycle
10.  The pattern: three guards wired to constant signals
11.  Pre-mortems, and how each item landed
12.  Open items
APPENDIX A — Measured distributions
APPENDIX B — Forensic call timelines
APPENDIX C — Commands behind every figure
APPENDIX D — Effective configuration and kill switches
```

---

## 0. How to read this report

Every figure is followed by the command that produced it and the output that came
back. Appendix A holds distributions computed from the journal — not drawn by
hand, bucket edges printed so any bar can be re-derived. Appendix B holds the
full event timeline of the four calls the findings turn on, verbatim.

Three conventions, unchanged from report 6:

* **`?` means the journal cannot answer.** It never means zero.
* **Every verdict is written on clean calls too.** A line that appears only on
  failure cannot be told apart from a check that never ran.
* **Where I was wrong, it says so.** Section 9 is a register of corrections I
  made to my own claims *within this same investigation* — including one where I
  diagnosed a bug from one-second log resolution and had to withdraw it after
  looking at microseconds.

Section 10 is the one that matters beyond today.

---

## 1. Executive summary

**The day started with a regression I had shipped.** Six of fourteen answered
calls were abandoning Deepgram Flux within seconds of answering. The cause was
the silent-stream watchdog from 2026-08-13 counting the agent's own TTS —
echoing back on a 2-wire line — as unanswered caller speech. Every trip was a
false positive.

**It ended with three guards working for the first time.** The false-failover
rate fell from 36% to 9%, and the one failover that remains is genuine: it
fired 211 seconds into a call, mid-conversation, having correctly ignored 11.8
seconds of agent audio first.

**Two other guards proved themselves today**, both dead until yesterday's
pydantic fix: the acoustic nudge guard suppressed its first ever nudge, and the
caller-onset anchor produced its first `detect_ms` and `speech_to_stop_ms`
figures — 236 and 141 samples respectively.

**Those figures immediately identified the next problem.** `detect_ms` p50 is
664ms and p90 is 2,715ms: the time between a caller opening their mouth and the
system beginning to react. Nearly all of it is spent waiting for Flux to raise
`StartOfTurn`, while our own acoustic anchor already knows. That is what "the
agent talks over me" feels like, and it is now measured rather than argued.

**I did not fix it today, deliberately.** The obvious accelerator — trigger
barge-in on acoustic energy — would, if mis-tuned, make the agent interrupt
itself on every turn. That is a worse regression than the delay, and it would
hit every call rather than the tail. Instead I shipped the measurement that
decides whether it is safe.

---

## 2. The headline result — 36% → 9%

The echo guard deployed at 20:24:24. Split the day on that boundary:

```
period                              calls  failovers    rate
------------------------------------------------------------
BEFORE echo guard (<20:24:24)          25          9     36%
AFTER  echo guard (>=20:24:24)         11          1      9%
```

And the one that survived is not the same animal:

```
failovers AFTER the guard:
  f55c8cf0  trip at t+211s into the call

resilient_stt_audit provider=deepgram-flux outcome=failover
  counted_voiced_ms=6000  suppressed_ms=11800  probe=installed  probe_errors=0
```

Read that audit line carefully, because it is the whole fix in one line. The
guard **suppressed 11.8 seconds** of audio it could not attribute to the caller
— the agent's own speech — and still **counted 6.0 seconds** of genuine caller
audio that went unanswered. It then failed over, 211 seconds into the call, in
the middle of a conversation.

That is the watchdog doing exactly the job it was built for on 2026-08-13, with
the false-positive path closed.

Before the guard, the trips clustered at the opening:

```
call        trip
ad57a129    t+15s
5529d6f9    t+ 8s
c8f85a27    t+22s
9ca79a46    t+51s
5e4ac5f0    t+ 7s
35367a41    t+18s
```

After it: one trip, at t+211s.

---

## 3. FINDING 1 — the STT watchdog was counting the agent's own voice

### 3.1 The evidence

Call `5529d6f9`, in full (Appendix B has the complete timeline):

```
t+  0s  audio_stream_started
t+  0s  recording_disclosure_speaking reason=tenant_default_two_party
t+  5s  recording_disclosure_spoken
t+  5s  outbound_greeting_presynth chunks=29
t+  6s  outbound_greeting_presynth_done elapsed_ms=1011 interrupted=False
t+  8s  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
t+  8s  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
```

Eight seconds into the call. The agent had been speaking for six of them — the
two-party recording disclosure, then the pre-synthesised greeting. The watchdog
counted that as "six seconds of caller speech with no transcript" and declared
Flux dead.

I checked all six trip windows. **In every one the agent was mid-utterance** —
the disclosure, the greeting, or a normal reply. The RMS on the line during
those windows was 287–755, and the callers' own speech, once it started,
measured 2,360–4,890. Five to ten times louder.

### 3.2 Why the existing guard did nothing

The watchdog discounted audio only while the provider reported itself `muted`:

```python
if watchdog.observe_audio(chunk, muted=_provider_muted(chosen)):
```

On telephony that is **constant False by design**:

```
backend/app/core/telephony_settings.py:214      mute_during_tts: bool = False
backend/app/api/v1/endpoints/vonage_bridge.py:258
    mute_during_tts=False,   # must be explicit — default True blocks barge-in
```

There is not one `mute()` call anywhere in `voice_pipeline/`, `telephony/` or
the media gateways. Muting STT during TTS would destroy barge-in, which is
exactly what telephony leaves it live for. So the echo protection the
watchdog's docstring described in detail had **never once applied on a phone
call**.

### 3.3 The fix, and what it deliberately is not

The industry offers three options, and I took none of them wholesale:

| approach | why not |
|---|---|
| Duck the mic during TTS | Kills barge-in. Coval: *"The user can't interrupt the agent. Any speech during the agent's turn is lost."* |
| AEC with TTS as reference | We have no server-side AEC, and PSTN callers have no client-side AEC either |
| Post-TTS decay delay | Already have it — 250ms |

The fix is narrower: the watchdog stops **counting** audio it cannot attribute.
While the agent speaks, line energy is either echo or a genuine barge-in, and
without AEC we cannot tell which — so "the caller went unanswered" is
unprovable and the watchdog abstains. Counting resumes the moment the agent is
quiet, plus a 250ms decay tail sharing the constant the pipeline already uses.

The audio path is untouched. Barge-in is untouched.

The probe is a callable reading `CallSession.tts_active` live, not a cached
copy — a mirrored copy of a signal that changes several times a second would
drift, and a drifted copy recreates this bug exactly.

---

## 4. FINDING 2 — post-interrupt silence

You reported that after an interruption the agent sometimes takes 3–4 seconds,
and sometimes goes silent until you speak again. Both have one cause.

### 4.1 The delay: a hold that never releases early

```
holds armed (barge_in_ignored_final_pre_tts)  : 14
TIMED OUT at the 2.5s cap                     :  6
released early because the caller stopped     :  0    <- never, all day
```

Six timeouts, all at the cap: `waited=2.51s, 2.52s, 2.50s, 2.51s, 2.50s, 2.50s`.
Zero early releases. If the flag were accurate, a caller who stops mid-hold
would release it within one 20ms poll.

**Proof that the caller had already stopped.** Call `9bf01238`, 19:50:41:

```
-3s  Flux EndOfTurn: 'Thank you. Good night.'
-3s  Flux StartOfTurn - User started speaking, barge-in detected
-3s  barge_in_ignored_final_pre_tts — playback held until the caller stops
-2s  Flux EndOfTurn: 'and take care. Goodbye.'     <- THE CALLER FINISHED HERE
+0s  pre_tts_hold_timeout waited=2.52s — speaking anyway
+2s  llm_response turn=27 said=''
```

The caller demonstrably stopped at −2s. The gate waited another 2.5 seconds for
an event that had already happened, then gave up. Same shape on `522720bb`:
EndOfTurn *"What's seventeen times six?"* at −4s, hold still ran to the cap.

The arithmetic matches the reported symptom exactly:

```
2.50s   stale hold, always the full cap
0.64s   LLM time-to-first-token (p50 measured today)
0.20s   TTS first chunk
─────
3.34s   -> "sometimes 3"    with a slow turn (TTFT 1.5s) -> 4.2s -> "sometimes 4"
```

### 4.2 The silence: 36 turns that got no answer

```
Flux EndOfTurn (the caller finished speaking)  : 232
llm_response   (the agent actually replied)    : 196
                                                 ───
                                                  36 turns, 15.5%, produced nothing
```

### 4.3 One cause for both

`_caller_speaking` is set in **one** place (`audio_ingest.py:382`, on
StartOfTurn) and cleared in **one** place (`turn_ender.py:456`). Between the
entry of `turn_ender.handle()` at line 110 and line 456 sit **eight early
returns**:

```
132: if not full_transcript:                          -> empty transcript
197: (guard)
211: if ... is_bare_greeting(...) and try_instant_opener(...)
227: if self._p._is_repetitive_transcript(...)         -> repetitive
316: (guard)
366: (guard)
385: (guard)
427: (guard)
```

Any caller utterance filtered by one of those guards returns before reaching
`mark_caller_stopped`. So:

1. **no reply is generated** → the 36 answerless turns → *silence until you
   speak again*;
2. **the flag is never cleared** → the next armed hold waits its full 2.5s →
   *3–4 seconds*.

The guards fired plenty today: 48 duplicate, 47 barge-in-suppressed, 16
backchannel, 3 phantom-goodbye.

The interrupt path never clears it either — `interrupt_playback` sets state to
`LISTENING` and cancels the turn, but does not touch `_caller_speaking`.

**This is a design asymmetry, not a tuning problem.** One signal set in one
place and cleared in one place, with the clearing site behind eight ways to skip
it, leaks by construction. **Not fixed today** — see section 12.

---

## 5. FINDING 3 — the interrupt that asked nothing

You flagged a window 54–62 seconds into the latest call. Here is what was
there, at microsecond resolution.

### 5.1 The identifiers

| field | value |
|---|---|
| call ID | `d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c` |
| transcript before | *"k. I need the exact price, but I gave you no plans of project size. Can you still give me one?"* |
| agent reply | *"I can't give you a price without those details — I'll have the exact figure confirmed once I have them. What's the best email…"* |
| StartOfTurn | 20:50:51.606 |
| interrupt_id | `0479a76d17ef` |
| detect_ms | **1218.4** |
| speech_to_stop_ms | **absent** |
| outcome | `nothing_playing … tts_active=False cancelled=False elapsed_ms=0.32` |

### 5.2 The microsecond timeline

```
20:50:41.531  Flux EndOfTurn: 'k. I need the exact price...'
20:50:42.512  agent's turn-4 audio starts (first chunk)
20:50:47.985  second sentence starts
20:50:49.454  turn_complete — last chunk handed over, tts_active -> False
20:50:50.388  CALLER'S ACOUSTIC ONSET  (51.606 − detect_ms 1218.4)
20:50:51.606  Flux StartOfTurn — barge-in detected
20:50:51.607  interrupt 0479a76d17ef -> nothing_playing
```

### 5.3 What I found, and what I withdrew

I initially reported that the interrupt was refused because the entry gate
trusts `tts_active` while the gateway was still draining, and that we never
asked the gateway. **The second half is true and is now fixed. The first half
was wrong for this event**, and I withdrew it after looking at microseconds:
Python's last chunk went over 0.93 seconds *before* the caller spoke. `tts_active
= False` was accurate and `nothing_playing` was the correct answer here.

I had been reasoning from one-second log resolution, which cannot distinguish
"0.9s before" from "simultaneously".

### 5.4 What was actually in your 54–62s window

Flux emitted **nothing at all** between 20:50:41.531 and 20:50:51.606 — a ten
second silence spanning the whole window. The line was not silent:

```
during the agent's speech (42.5→49.4)   rms 287, 452, 755, 352, 296, 559, 631, 576
once the caller started (51.8→55.9)     rms 4813, 4890, 2360, 2340, 3273
```

The caller's voice is 5–10× louder than what was on the line while the agent
spoke. So that audio was echo, and Flux was right to ignore it. The caller
began at 50.388 — as the agent was finishing.

### 5.5 The real weakness, fixed anyway

`interrupt_playback` decided there was nothing to stop from one local variable
and returned before `clear_output_buffer`. The C++ gateway was never asked,
never told to stop, and the utterance id was never rotated.

`tts_active` means "Python is still streaming TTS". It does not mean "the
caller stopped hearing audio" — the gateway holds its own queue and paces it in
real time. Every interrupt today that *did* ask found audio in it:

```
dropped audio   n=143  p50=0ms  p90=260ms  max=260ms
       0-1    ms | ████████████████████████████████████████  95   66.4%
     240-260  ms | ███████████                               21   14.7%
     260-300  ms | █████████                                 17   11.9%
```

A third of real interrupts stop 240–300ms of already-queued audio. On this call
the timing happened to work out and the caller was probably not talked over.
That was luck; the code could not have known, because it did not look.

**Fixed in `f750b1f2`:** the `nothing_playing` shortcut stays — two thirds of
interrupt events are ordinary turns and must not pay for a round-trip — but it
no longer applies inside the window where the gateway may still be draining.
There, we ask.

The metric stays honest: a probe that finds an empty queue stopped nothing and
is not recorded as a barge-in; one that finds queued audio counts normally.

---

## 6. FINDING 4 — detection latency is the real barge-in cost

Both figures below exist for the first time today. They were `None` on every
interrupt until yesterday's pydantic fix, because the anchor that produces them
could not write to the session object.

```
detect_ms   (caller onset -> we begin stopping)   n=236  p50=664ms  p90=2715ms  max=19.6s
       0-200   ms | ███████                                    9    3.8%
     200-400   ms | ██████████████                            19    8.1%
     400-600   ms | ████████████████████████████████████████  66   28.0%
     600-800   ms | ██████████████████████████████████        47   19.9%
     800-1000  ms | ███████████████████                       26   11.0%
    1000-1400  ms | ██████████████████                        25   10.6%
    1400-2000  ms | █████████                                 12    5.1%
    2000-3000  ms | ███████                                   10    4.2%
    3000+     ms | ████████████████                          22    9.3%

speech_to_stop_ms (mouth open -> agent silent)    n=141  p50=755ms  p90=4387ms
```

**A median of 664ms passes between a caller speaking and the system reacting**,
and at p90 over 2.7 seconds. Nine percent of the time it exceeds three seconds.
When audio is actually stopped, the caller has been talked over for a median
755ms.

Almost all of that is Flux's `StartOfTurn` decision. Our own acoustic anchor
knew 1.2 seconds earlier on `d068f4b8`.

**This is the number that most directly matches "the agent talks over me".**

---

## 7. What shipped today

### 7.1 `f05629f5` — the STT echo guard

```
resilient_stt_echo_guard probe=installed echo_tail_s=0.25 voiced_needed_s=6.0
resilient_stt_audit provider=deepgram-flux outcome=failover
  counted_voiced_ms=6000 suppressed_ms=11800 probe=installed probe_errors=0
```

Eleven `probe=installed` lines today, zero `ABSENT`, zero `probe_errors`.

### 7.2 `f750b1f2` — the gateway drain window and the margin probe

Verified inside the running deployment:

```
1. drain window          : 0.6 s  (VOICE_GATEWAY_DRAIN_S)
2. drain helper present  : True
3. tts_playback stamps   : True
4. margin probe present  : True
5. inside window (0.3s)  : 300 ms -> ASK gateway
6. ordinary turn (5.0s)  : None   -> cheap path
7. margin probe output   : echo_frames=80 echo_rms_p95=1000 caller_frames=40
                           caller_rms_p50=4000 caller_rms_p95=4000 margin=4.0x
```

### 7.3 Two guards that ran for the first time

**The acoustic nudge guard**, dead since 2026-08-13, suppressed its first nudge:

```
[SilenceMonitor] 5529d6f9-39c — nudge SUPPRESSED, caller audio live rms=1047 n=1
                                (would have talked over them)
```

**The caller-onset anchor** produced 236 `detect_ms` and 141
`speech_to_stop_ms` samples. Both were `None` on every interrupt before
yesterday.

---

## 8. The measurement that gates the next fix

The obvious cure for section 6 is to trigger barge-in on acoustic energy
instead of waiting for Flux. `d068f4b8` showed a 5–10× separation between echo
and caller speech, which would be ample headroom.

**One call is not evidence.** A louder trunk, a speakerphone, or a different
handset could close that gap, and if it closes, the agent starts interrupting
itself on every turn. That is a worse regression than a 664ms delay and it
would hit every call rather than the tail.

So `BargeInMarginProbe` now measures the separation on **every** call:

```
barge_in_margin call_id=... echo_frames=N echo_rms_p95=N caller_frames=N
                caller_rms_p50=N caller_rms_p95=N margin=Nx
```

Nothing reads it to make a decision, by design. It costs one comparison and one
bucket increment per frame, reusing the RMS the level accumulator already
computes. The decision waits for the distribution.

**If `margin` holds above roughly 3× across trunks and handsets**, an adaptive
energy trigger — gated on the agent speaking, thresholded against *that call's
own* measured echo floor, with a kill switch and `early_barge_in=true` in the
log — is safe, and would cut the 664ms median substantially.

**If it collapses on any call**, the answer is server-side AEC, which is a
larger piece of work.

---

## 9. Corrections to my own claims in this cycle

| # | claim | status | what settled it |
|---|---|---|---|
| 1 | "The `nothing_playing` at 20:50:51 happened because the gateway was still draining" | **WITHDRAWN** | microsecond timeline: Python finished 0.93s before the caller spoke |
| 2 | "Two calls never got a Flux transcript at all" | **WRONG** | scorecard's `stt-ok` counted a Flux-only marker; `5529d6f9` had 33 transcripts via Nova |
| 3 | "Stale pre-connected socket causes the failovers" | **DISPROVEN** | idle gap identical: working 7–15s, failing 6–16s |
| 4 | "Opening loudness explains which calls fail" | **DISPROVEN** | 1,532 vs 1,068 mean, ranges overlap heavily |
| 5 | "The acoustic nudge guard is live" (report 5) | **WAS WRONG** | never executed until yesterday; first suppression logged today |

Five corrections. Items 3 and 4 are hypotheses I tested and discarded before
acting, which is the process working. Items 1, 2 and 5 are claims I made and had
to withdraw, which is not.

---

## 10. The pattern: three guards wired to constant signals

This is the part worth carrying beyond today.

| guard | read | why it was always constant |
|---|---|---|
| Acoustic nudge guard (08-13) | `session.last_audio_rms` | pydantic v2 **rejects** undeclared public attributes — the write raised, inside a `try/except` |
| Caller-onset anchor (08-17) | `session.caller_voice_onset_at` | same mechanism, same day |
| STT silent-stream watchdog (08-13) | `provider.is_muted()` | telephony holds `mute_during_tts=False` **by design** so barge-in works |

Different causes, one shape: **I verified each guard's logic and never verified
its input varies.** All three had thorough tests. All three tested the branch and
never the wire.

Two habits are now standing practice:

**Log the wiring, not just the outcome.** `probe=installed|ABSENT` costs one line
per call and collapses "four days of dead code" into one grep. Every guard gets
one.

**Test the seam, not the branch.** A `SimpleNamespace` proves the algorithm; only
the real object proves it will run. `test_session_scratch_attrs` asserts that
`tts_active` is readable on a real `CallSession` *and* that the non-underscore
form still raises — precisely because that assertion would have failed on the
previous two attempts.

---

## 11. Pre-mortems, and how each item landed

Written before the code, in both cases.

### Echo guard (`f05629f5`)

| # | predicted failure | outcome |
|---|---|---|
| F1 | probe never installs, silently | **Mitigated** — 11 `probe=installed`, 0 `ABSENT` |
| F2 | `tts_active` doesn't cover the opening | **Checked before coding** — disclosure via `tts_playback:98`, presynth via `agent_first:430`, and it is a *declared* field |
| F3 | over-correction, watchdog never fires again | **Held** — one genuine trip at t+211s with `suppressed_ms=11800` |
| F4 | echo tail after TTS stops | **Implemented** — 250ms, shared constant |
| F5 | barge-in into a dead Flux undetected | **Accepted**, documented |
| F6 | silent exception swallowing | **Mitigated** — fails toward old behaviour, `probe_errors` counted (0 today) |
| F7 | deployed but never exercised | **Closed** — 11 calls through it |
| F8 | second source of truth drifts | **Avoided** — callable, not a copy |
| F9 | scorecard lies in the next report | **Fixed** — and it immediately corrected me (correction 2) |
| F10 | tests pass against a permissive double | **Fixed** — real `CallSession` |

### Gateway drain window (`f750b1f2`)

| # | predicted failure | mitigation |
|---|---|---|
| G1 | window too long → ordinary turns counted as barge-ins | 0.6s, derived from measured 240–300ms hold; test pins that a 5s-old turn takes the cheap path |
| G2 | metric polluted by probes | probe finding an empty queue is **not** recorded; two tests pin both directions |
| G3 | stamp never written → no change | fails to previous behaviour; `drain_ms` on every begin line |
| G4 | pydantic attribute rejected | underscore-prefixed; a test asserts the public form raises |
| G5 | extra gateway round-trips cost latency | ~1ms local call, only inside a 0.6s window |
| G6 | cannot be reverted quickly | `VOICE_GATEWAY_DRAIN_S=0` restores exact prior behaviour, no redeploy |

**Gate on both:** 4,801 passed, 15 skipped. The 8 failures and 5 errors are
identical to the pristine-HEAD baseline, plus one known intermittent (#54).

---

## 12. Open items

| # | item | status |
|---|---|---|
| — | **`_caller_speaking` leaks past 8 early returns** (§4) | **NOT FIXED.** The 3–4s delay and the 36 answerless turns. Highest-value remaining fix. |
| — | **Energy-triggered barge-in** (§6, §8) | Blocked on the margin distribution, by choice |
| 43 | prompt size / prefill (p50 635ms) | Needs an A/B or a caching model |
| 44 | recordings lost to disclosure barge-in | Product/compliance decision, yours |
| 54 | `CredentialResolver` keyed on `id(db_pool)` | Real, latent, credential path |
| 59 | 5 tenant configs on a 404 model | Needs a DB write |
| 60 | verify the echo guard live | **CLOSED** — §2 |

**Recommended next:** the `_caller_speaking` leak. It is the direct cause of
both symptoms you reported, the fix is well-understood (clear the flag where the
fact becomes true, not where the happy path runs), and unlike §6 it needs no new
data to justify.

---

## APPENDIX A — Measured distributions

Computed from the journal, not drawn by hand. Bucket edges printed so any bar
can be re-derived.

```
============================================================================
CHART 1 — detect_ms: caller onset -> we begin stopping
============================================================================

detect_ms   n=236  min=34ms  p50=664ms  p90=2715ms  max=19586ms
----------------------------------------------------------------------------
       0-200   ms | ███████                                             9    3.8%
     200-400   ms | ██████████████                                     19    8.1%
     400-600   ms | ████████████████████████████████████████████████   66   28.0%
     600-800   ms | ██████████████████████████████████                 47   19.9%
     800-1000  ms | ███████████████████                                26   11.0%
    1000-1400  ms | ██████████████████                                 25   10.6%
    1400-2000  ms | █████████                                          12    5.1%
    2000-3000  ms | ███████                                            10    4.2%
    3000+     ms | ████████████████                                   22    9.3%

============================================================================
CHART 2 — speech_to_stop_ms: mouth open -> agent silent
============================================================================

speech_to_stop   n=141  min=21ms  p50=755ms  p90=4387ms  max=19690ms
----------------------------------------------------------------------------
       0-200   ms | ████████████████████████████████████████████████   35   24.8%
     200-400   ms | ████████████████                                   12    8.5%
     400-600   ms | ██████████████████                                 13    9.2%
     600-800   ms | █████████████████████                              15   10.6%
     800-1000  ms | ████████                                            6    4.3%
    1000-1400  ms | ██████████████████                                 13    9.2%
    1400-2000  ms | ███████████                                         8    5.7%
    2000-3000  ms | ███████████████████                                14    9.9%
    3000+     ms | ██████████████████████████████████                 25   17.7%

============================================================================
CHART 3 — gateway audio binned per real interrupt
============================================================================

dropped audio   n=143  min=0ms  p50=0ms  p90=260ms  max=260ms
----------------------------------------------------------------------------
       0-1     ms | ████████████████████████████████████████████████   95   66.4%
       1-60    ms | █                                                   2    1.4%
      60-120   ms | ██                                                  3    2.1%
     120-180   ms | █                                                   1    0.7%
     180-240   ms | ██                                                  4    2.8%
     240-260   ms | ███████████                                        21   14.7%
     260-300   ms | █████████                                          17   11.9%
     300+     ms |                                                     0    0.0%

============================================================================
CHART 4 — prompt_time (prefill)
============================================================================

prompt_time   n=261  min=449ms  p50=635ms  p90=690ms  max=783ms
----------------------------------------------------------------------------
       0-450   ms | █                                                   1    0.4%
     450-550   ms | ████████████                                       20    7.7%
     550-600   ms | █████████████████████                              33   12.6%
     600-625   ms | ██████████████████████████████████                 54   20.7%
     625-650   ms | ███████████████████████████████████                56   21.5%
     650-700   ms | ████████████████████████████████████████████████   77   29.5%
     700-750   ms | ████████                                           13    5.0%
     750+     ms | ████                                                7    2.7%

============================================================================
CHART 5 — caller audio RMS, 1s windows
============================================================================

RMS   n=3481  min=0  p50=411  p90=2276  max=10160
----------------------------------------------------------------------------
       0-100      | █████████████████████████████████████████████     648   18.6%
     100-250      | ████████████████████████████████████████████████  686   19.7%
     250-500      | ██████████████████████████████████████            545   15.7%
     500-1000     | █████████████████████████████                     417   12.0%
    1000-2000     | ████████████████████████████████████████████████  683   19.6%
    2000-4000     | ████████████████████████████████                  463   13.3%
    4000-8000     | ███                                                36    1.0%
    8000+        |                                                     3    0.1%

============================================================================
CHART 6 — pre-TTS hold outcomes
============================================================================

  armed (barge-in during generation)     ████████████████████████████████████████   14
  TIMED OUT at the 2.5s cap              █████████████████                           6
  released early (caller stopped)                                                    0

============================================================================
CHART 7 — interrupt outcome mix
============================================================================

  real (audio stopped)       ████████████████                                143   35.8%
  no-op (ordinary turn)      █████████████████████████████                   252   63.2%
  deduped                                                                      4    1.0%
  FAILED                                                                       0    0.0%

============================================================================
CHART 8 — STT failover: before vs after the echo guard
============================================================================

  calls that abandoned Flux today: 10
    ad57a129  first trip 17:01:33
    5529d6f9  first trip 18:53:33
    c8f85a27  first trip 18:57:21
    9ca79a46  first trip 19:01:49
    5e4ac5f0  first trip 19:03:06
    35367a41  first trip 19:36:00
    06a6f8c9  first trip 19:55:58
    522720bb  first trip 20:06:10
    3b4acc79  first trip 20:10:19
    f55c8cf0  first trip 20:37:00
```

---

## APPENDIX B — Forensic call timelines

The four calls the findings turn on, verbatim from the journal, stamped with
elapsed seconds from each call's first event.

```
==============================================================================================
5529d6f9 — FALSE FAILOVER — watchdog tripped on the agent's own disclosure+greeting
==============================================================================================
  t+   0s  audio_stream_started call_id=5529d6f9-39cf-4830-8e63-705b2bd0ad24 queue_size=0 stt_active=True
  t+   0s  recording_disclosure_speaking call_id=5529d6f9-39c reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  t+   5s  recording_disclosure_spoken call_id=5529d6f9-39c
  t+   5s  outbound_greeting_presynth call_id=5529d6f9-39c chunks=29 text='[redacted chars=7 sha=47316768]'
  t+   6s  outbound_greeting_presynth_done call_id=5529d6f9-39c elapsed_ms=1011 interrupted=False
  t+   8s  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0 — 6.0s of caller speech went in and no transcript even
  t+   8s  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
  t+   8s  barge_in_detected
  t+   8s  interrupt_step=begin interrupt_id=27c879cf9595 call=5529d6f9-39c reason=barge_in tts_active=False
  t+   8s  interrupt_step=nothing_playing interrupt_id=27c879cf9595 call=5529d6f9-39c reason=barge_in elapsed_ms=0.17 detect_ms=2
  t+   9s  [SilenceMonitor] 5529d6f9-39c — nudge SUPPRESSED, caller audio live rms=1047 n=1 (would have talked over them)
  t+  12s  barge_in_detected
  t+  12s  interrupt_step=begin interrupt_id=14001fd4eb34 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  12s  interrupt_step=nothing_playing interrupt_id=14001fd4eb34 call=5529d6f9-39c reason=barge_in elapsed_ms=0.17 detect_ms=1
  t+  13s  barge_in_detected
  t+  13s  interrupt_step=begin interrupt_id=36a252c7dda2 call=5529d6f9-39c reason=barge_in tts_active=True
  t+  13s  interrupt_complete {'interrupt_id': '36a252c7dda2', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  14s  barge_in_detected
  t+  14s  interrupt_step=begin interrupt_id=6095e4275d9e call=5529d6f9-39c reason=barge_in tts_active=False
  t+  14s  interrupt_complete {'interrupt_id': '6095e4275d9e', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  16s  barge_in_detected
  t+  16s  interrupt_step=begin interrupt_id=3cda3362da00 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  17s  interrupt_complete {'interrupt_id': '3cda3362da00', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  21s  barge_in_detected
  t+  21s  interrupt_step=begin interrupt_id=a06b621b4f47 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  21s  interrupt_complete {'interrupt_id': 'a06b621b4f47', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  25s  barge_in_detected
  t+  25s  interrupt_step=begin interrupt_id=70cbb54171f7 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  25s  interrupt_step=nothing_playing interrupt_id=70cbb54171f7 call=5529d6f9-39c reason=barge_in elapsed_ms=0.22 detect_ms=1
  t+  30s  barge_in_detected
  t+  30s  interrupt_step=begin interrupt_id=aefe3d95e219 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  30s  interrupt_complete {'interrupt_id': 'aefe3d95e219', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  31s  barge_in_detected
  t+  31s  interrupt_step=begin interrupt_id=50e79a8c7076 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  31s  interrupt_step=nothing_playing interrupt_id=50e79a8c7076 call=5529d6f9-39c reason=barge_in elapsed_ms=0.17 detect_ms=1
  t+  34s  barge_in_detected
  t+  34s  interrupt_step=begin interrupt_id=96c397177a8d call=5529d6f9-39c reason=barge_in tts_active=False
  t+  34s  interrupt_complete {'interrupt_id': '96c397177a8d', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  37s  barge_in_detected
  t+  37s  interrupt_step=begin interrupt_id=718df735cb4c call=5529d6f9-39c reason=barge_in tts_active=False
  t+  37s  interrupt_complete {'interrupt_id': '718df735cb4c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  38s  barge_in_detected
  t+  38s  interrupt_step=begin interrupt_id=3c8ef742300f call=5529d6f9-39c reason=barge_in tts_active=False
  t+  38s  interrupt_complete {'interrupt_id': '3c8ef742300f', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  43s  barge_in_detected
  t+  43s  interrupt_step=begin interrupt_id=89e199ca7f41 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  43s  interrupt_complete {'interrupt_id': '89e199ca7f41', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  45s  barge_in_detected
  t+  45s  interrupt_step=begin interrupt_id=5ef6d5050bd9 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  45s  interrupt_step=nothing_playing interrupt_id=5ef6d5050bd9 call=5529d6f9-39c reason=barge_in elapsed_ms=0.46 detect_ms=4
  t+  46s  barge_in_detected
  t+  46s  interrupt_step=begin interrupt_id=794bcb6814f3 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  46s  interrupt_complete {'interrupt_id': '794bcb6814f3', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  50s  barge_in_ignored_final_pre_tts call=5529d6f9-39c — protecting in-flight answer, playback held until the caller stops
  t+  51s  barge_in_detected
  t+  51s  interrupt_step=begin interrupt_id=0c2b6272c1bb call=5529d6f9-39c reason=barge_in tts_active=True
  t+  51s  interrupt_complete {'interrupt_id': '0c2b6272c1bb', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  55s  barge_in_detected
  t+  55s  interrupt_step=begin interrupt_id=0f7801ef8522 call=5529d6f9-39c reason=barge_in tts_active=False
  t+  55s  interrupt_complete {'interrupt_id': '0f7801ef8522', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  58s  [SilenceMonitor] 5529d6f9-39c — nudge_audit nudges=0 suppressed=4

==============================================================================================
f55c8cf0 — GENUINE FAILOVER after the echo guard — mid-call, t+211s
==============================================================================================
  t+   0s  resilient_stt_echo_guard probe=installed echo_tail_s=0.25 voiced_needed_s=6.0
  t+   0s  audio_stream_started call_id=f55c8cf0-9943-4cf0-822b-e42b0e83c583 queue_size=0 stt_active=True
  t+   0s  recording_disclosure_speaking call_id=f55c8cf0-994 reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  t+   3s  recording_disclosure_spoken call_id=f55c8cf0-994
  t+   3s  outbound_greeting_presynth call_id=f55c8cf0-994 chunks=21 text='[redacted chars=7 sha=47316768]'
  t+   4s  outbound_greeting_presynth_done call_id=f55c8cf0-994 elapsed_ms=839 interrupted=False
  t+   7s  Flux StartOfTurn - User started speaking, barge-in detected
  t+   7s  barge_in_detected
  t+   7s  interrupt_step=begin interrupt_id=0e08a1db3e6c call=f55c8cf0-994 reason=barge_in tts_active=False
  t+   7s  interrupt_step=nothing_playing interrupt_id=0e08a1db3e6c call=f55c8cf0-994 reason=barge_in elapsed_ms=0.33 detect_ms=2
  t+   7s  Flux EndOfTurn: 'Hi.'
  t+   7s  t_stt_first_final call_id=f55c8cf0-9943-4cf0-822b-e42b0e83c583
  t+  13s  llm_response turn=0 said='Hi there, this is Sara Lehman from Talk-Lee — do you have a quick minute?'
  t+  13s  [SLOW] Turn 0 latency: 944ms (STT-first: 57ms, LLM-first-token: 728ms, TTS-first-chunk: 181ms, LLM-total: 5366ms, TTS-
  t+  15s  Flux StartOfTurn backchannel 'Yeah.' — barge-in suppressed
  t+  16s  Flux EndOfTurn: 'Yeah.'
  t+  19s  llm_response turn=1 said='Great — what are you working on right now?'
  t+  19s  [SLOW] Turn 1 latency: 1019ms (STT-first: 0ms, LLM-first-token: 811ms, TTS-first-chunk: 206ms, LLM-total: 3682ms, TTS-
  t+  22s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  22s  barge_in_detected
  t+  22s  interrupt_step=begin interrupt_id=08f9c8cd6dee call=f55c8cf0-994 reason=barge_in tts_active=False
  t+  22s  interrupt_step=nothing_playing interrupt_id=08f9c8cd6dee call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=4
  t+  23s  Flux EndOfTurn: 'I'm working on many projects.'
  t+  26s  Flux StartOfTurn backchannel 'Yeah.' — barge-in suppressed
  t+  27s  llm_response turn=2 said="Sounds busy — which one's got your attention most right now?"
  t+  27s  [SLOW] Turn 2 latency: 1003ms (STT-first: 238ms, LLM-first-token: 811ms, TTS-first-chunk: 172ms, LLM-total: 4346ms, TT
  t+  28s  Flux EndOfTurn: 'Yeah.'
  t+  30s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  30s  barge_in_detected
  t+  30s  interrupt_step=begin interrupt_id=dc317e690977 call=f55c8cf0-994 reason=barge_in tts_active=True
  t+  30s  interrupt_complete {'interrupt_id': 'dc317e690977', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+  31s  Flux EndOfTurn: 'Lot of things.'
  t+  36s  llm_response turn=4 said='Yeah, I get that — is there one thing that’s been weighing on you the most lately?'
  t+  36s  [SLOW] Turn 4 latency: 990ms (STT-first: 0ms, LLM-first-token: 744ms, TTS-first-chunk: 211ms, LLM-total: 5452ms, TTS-t
  t+  41s  Flux StartOfTurn backchannel 'Yeah.' — barge-in suppressed
  t+  42s  Flux EndOfTurn: 'Yeah.'
  t+  44s  llm_response turn=5 said='What is it?'
  t+  44s  [SLOW] Turn 5 latency: 953ms (STT-first: 0ms, LLM-first-token: 736ms, TTS-first-chunk: 214ms, LLM-total: 1455ms, TTS-t
  t+  48s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  48s  barge_in_detected
  t+  48s  interrupt_step=begin interrupt_id=0d9c9431d40b call=f55c8cf0-994 reason=barge_in tts_active=False
  t+  48s  interrupt_step=nothing_playing interrupt_id=0d9c9431d40b call=f55c8cf0-994 reason=barge_in elapsed_ms=0.21 detect_ms=4
  t+  50s  Flux EndOfTurn: 'It's about the construction project.'
  t+  54s  llm_response turn=6 said='Construction — what’s the biggest headache with it right now?'
  t+  54s  [SLOW] Turn 6 latency: 1063ms (STT-first: 236ms, LLM-first-token: 841ms, TTS-first-chunk: 200ms, LLM-total: 4246ms, TT
  t+  59s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  59s  barge_in_detected
  t+  59s  interrupt_step=begin interrupt_id=4ef4a01fa6d8 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+  59s  interrupt_step=nothing_playing interrupt_id=4ef4a01fa6d8 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.28 detect_ms=1
  t+  62s  Flux EndOfTurn: 'Plenty of things about the time, about the meeting.'
  t+  67s  llm_response turn=7 said='Time and meetings — which one is the bigger drag?'
  t+  67s  [SLOW] Turn 7 latency: 1958ms (STT-first: 241ms, LLM-first-token: 1730ms, TTS-first-chunk: 209ms, LLM-total: 4901ms, T
  t+  71s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  71s  barge_in_detected
  t+  71s  interrupt_step=begin interrupt_id=00f59d5d1c4b call=f55c8cf0-994 reason=barge_in tts_active=False
  t+  71s  interrupt_step=nothing_playing interrupt_id=00f59d5d1c4b call=f55c8cf0-994 reason=barge_in elapsed_ms=0.36 detect_ms=2
  t+  73s  Flux EndOfTurn: 'It's the time. We don't have that much time.'
  t+  78s  llm_response turn=8 said='Tight deadlines are tough — what’s the biggest time sink right now?'
  t+  78s  [SLOW] Turn 8 latency: 1004ms (STT-first: 204ms, LLM-first-token: 770ms, TTS-first-chunk: 209ms, LLM-total: 5027ms, TT
  t+  84s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  84s  barge_in_detected
  t+  84s  interrupt_step=begin interrupt_id=8718e152e008 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+  84s  interrupt_step=nothing_playing interrupt_id=8718e152e008 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.2 detect_ms=52
  t+  86s  Flux EndOfTurn: 'Is it safer by sharing you?'
  t+  92s  llm_response turn=9 said='Hmm, not sure I follow — do you mean sharing details about the project?'
  t+  92s  [SLOW] Turn 9 latency: 1082ms (STT-first: 236ms, LLM-first-token: 856ms, TTS-first-chunk: 200ms, LLM-total: 6184ms, TT
  t+  95s  Flux StartOfTurn backchannel 'Yeah.' — barge-in suppressed
  t+  95s  Flux EndOfTurn: 'Yeah.'
  t+ 111s  llm_response turn=10 said="Oh, I see. This is actually an internal Talk-Lee voice-system validation call to test conve
  t+ 111s  [SLOW] Turn 10 latency: 3208ms (STT-first: 0ms, LLM-first-token: 2960ms, TTS-first-chunk: 213ms, LLM-total: 15471ms, T
  t+ 113s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 113s  barge_in_detected
  t+ 113s  interrupt_step=begin interrupt_id=3b6cb86c032d call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 113s  interrupt_step=nothing_playing interrupt_id=3b6cb86c032d call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=9
  t+ 116s  Flux EndOfTurn: 'k. So do you think anyone is listening to us beside me and you?'
  t+ 120s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 120s  barge_in_detected
  t+ 120s  interrupt_step=begin interrupt_id=85fac6915b04 call=f55c8cf0-994 reason=barge_in tts_active=True
  t+ 120s  interrupt_complete {'interrupt_id': '85fac6915b04', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 121s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 122s  Flux EndOfTurn: 'Hi. Are you sure?'
  t+ 125s  llm_response turn=12 said='Yeah, just me and you.'
  t+ 125s  [SLOW] Turn 12 latency: 1009ms (STT-first: 166ms, LLM-first-token: 798ms, TTS-first-chunk: 203ms, LLM-total: 3272ms, T
  t+ 127s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 127s  barge_in_detected
  t+ 127s  interrupt_step=begin interrupt_id=be1899c36a4e call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 127s  interrupt_step=nothing_playing interrupt_id=be1899c36a4e call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=1
  t+ 130s  Flux EndOfTurn: 'How much from the scale from one to a hundred percent?'
  t+ 134s  llm_response turn=13 said="One hundred percent — it's just the two of us."
  t+ 134s  [SLOW] Turn 13 latency: 1010ms (STT-first: 236ms, LLM-first-token: 777ms, TTS-first-chunk: 211ms, LLM-total: 3633ms, T
  t+ 137s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 137s  barge_in_detected
  t+ 137s  interrupt_step=begin interrupt_id=c190ca105130 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 137s  interrupt_step=nothing_playing interrupt_id=c190ca105130 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=1
  t+ 137s  Flux EndOfTurn: 'And what's my name?'
  t+ 142s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 142s  barge_in_detected
  t+ 142s  interrupt_step=begin interrupt_id=90fd047f118b call=f55c8cf0-994 reason=barge_in tts_active=True
  t+ 142s  interrupt_complete {'interrupt_id': '90fd047f118b', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 143s  Flux EndOfTurn: 'Oh, I hear you. And you asked my name?'
  t+ 150s  llm_response turn=15 said="I didn't ask for your name — this is just a quick system test, so no need to share it."
  t+ 150s  [SLOW] Turn 15 latency: 1133ms (STT-first: 0ms, LLM-first-token: 870ms, TTS-first-chunk: 218ms, LLM-total: 6395ms, TTS
  t+ 151s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 151s  barge_in_detected
  t+ 151s  interrupt_step=begin interrupt_id=8e3c5d1f53cd call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 151s  interrupt_step=nothing_playing interrupt_id=8e3c5d1f53cd call=f55c8cf0-994 reason=barge_in elapsed_ms=0.2 detect_ms=41
  t+ 159s  Flux EndOfTurn: 'Hi. Good afternoon. My name is TTS or whatever it's a real call. So why wouldn't you ask someone when
  t+ 170s  llm_response turn=16 said="Good evening — fair point, usually I would. Since this is just a quick system test, I'm ski
  t+ 170s  [SLOW] Turn 16 latency: 1151ms (STT-first: 238ms, LLM-first-token: 922ms, TTS-first-chunk: 211ms, LLM-total: 11008ms, 
  t+ 171s  Flux StartOfTurn backchannel 'Yeah.' — barge-in suppressed
  t+ 175s  Flux EndOfTurn: 'Yeah. Good afternoon. Why would you skip for amenities in this call?'
  t+ 180s  llm_response turn=17 said="Just keeping it brief for the test. How's the rest of your day going?"
  t+ 180s  [SLOW] Turn 17 latency: 1078ms (STT-first: 0ms, LLM-first-token: 843ms, TTS-first-chunk: 218ms, LLM-total: 4940ms, TTS
  t+ 181s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 181s  barge_in_detected
  t+ 181s  interrupt_step=begin interrupt_id=554f85461baf call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 181s  interrupt_step=nothing_playing interrupt_id=554f85461baf call=f55c8cf0-994 reason=barge_in elapsed_ms=0.31 detect_ms=9
  t+ 186s  Flux EndOfTurn: 'Can you hold for a second?'
  t+ 188s  llm_response turn=18 said='Take your time.'
  t+ 188s  [SLOW] Turn 18 latency: 1013ms (STT-first: 247ms, LLM-first-token: 793ms, TTS-first-chunk: 211ms, LLM-total: 1715ms, T
  t+ 191s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 191s  barge_in_detected
  t+ 191s  interrupt_step=begin interrupt_id=ebcd38af9a44 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 191s  interrupt_step=nothing_playing interrupt_id=ebcd38af9a44 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+ 196s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 202s  Flux EndOfTurn: 'Wildix communication system. Discover new ways to communicate.'
  t+ 205s  llm_response turn=19 said="Sounds like an ad — I'll wait right here."
  t+ 205s  [SLOW] Turn 19 latency: 1033ms (STT-first: 0ms, LLM-first-token: 796ms, TTS-first-chunk: 218ms, LLM-total: 3975ms, TTS
  t+ 211s  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0 — 6.0s of caller speech went in and no transcript even
  t+ 211s  resilient_stt_audit provider=deepgram-flux outcome=failover counted_voiced_ms=6000 suppressed_ms=11800 probe=installed
  t+ 211s  resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12
  t+ 212s  barge_in_detected
  t+ 212s  interrupt_step=begin interrupt_id=621c41d17375 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 212s  interrupt_step=nothing_playing interrupt_id=621c41d17375 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=2
  t+ 215s  barge_in_detected
  t+ 215s  interrupt_step=begin interrupt_id=7073bf5e4533 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 215s  interrupt_step=nothing_playing interrupt_id=7073bf5e4533 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=4
  t+ 219s  barge_in_detected
  t+ 219s  interrupt_step=begin interrupt_id=94c4988d59ee call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 219s  interrupt_step=nothing_playing interrupt_id=94c4988d59ee call=f55c8cf0-994 reason=barge_in elapsed_ms=0.54 detect_ms=7
  t+ 222s  barge_in_detected
  t+ 222s  interrupt_step=begin interrupt_id=9c682a3cdcb2 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 222s  interrupt_step=nothing_playing interrupt_id=9c682a3cdcb2 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=1
  t+ 223s  [SilenceMonitor] f55c8cf0-994 — nudge SUPPRESSED, caller audio live rms=1498 n=1 (would have talked over them)
  t+ 227s  barge_in_detected
  t+ 227s  interrupt_step=begin interrupt_id=17299371cd7a call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 227s  interrupt_step=nothing_playing interrupt_id=17299371cd7a call=f55c8cf0-994 reason=barge_in elapsed_ms=0.24 detect_ms=1
  t+ 231s  barge_in_detected
  t+ 231s  interrupt_step=begin interrupt_id=0d23cedb0a6a call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 231s  interrupt_step=nothing_playing interrupt_id=0d23cedb0a6a call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=1
  t+ 236s  barge_in_detected
  t+ 236s  interrupt_step=begin interrupt_id=3935cc89846f call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 236s  interrupt_step=nothing_playing interrupt_id=3935cc89846f call=f55c8cf0-994 reason=barge_in elapsed_ms=0.18 detect_ms=2
  t+ 239s  llm_response turn=20 said="Got it — I'm still here."
  t+ 239s  [SLOW] Turn 20 latency: 1051ms (STT-first: 21094ms, LLM-first-token: 814ms, TTS-first-chunk: 226ms, LLM-total: 2794ms,
  t+ 239s  barge_in_detected
  t+ 239s  interrupt_step=begin interrupt_id=87e3eb414276 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 239s  interrupt_step=nothing_playing interrupt_id=87e3eb414276 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=2
  t+ 242s  barge_in_detected
  t+ 242s  interrupt_step=begin interrupt_id=8ffc791dd026 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 242s  interrupt_step=nothing_playing interrupt_id=8ffc791dd026 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.19 detect_ms=3
  t+ 246s  barge_in_detected
  t+ 246s  interrupt_step=begin interrupt_id=253573c9fb9c call=f55c8cf0-994 reason=barge_in tts_active=True
  t+ 246s  interrupt_complete {'interrupt_id': '253573c9fb9c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 250s  barge_in_detected
  t+ 250s  interrupt_step=begin interrupt_id=bfbb4d81b934 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 250s  interrupt_step=nothing_playing interrupt_id=bfbb4d81b934 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.4 detect_ms=38
  t+ 253s  barge_in_detected
  t+ 253s  interrupt_step=begin interrupt_id=9fb2567662c6 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 253s  interrupt_step=nothing_playing interrupt_id=9fb2567662c6 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.32 detect_ms=4
  t+ 258s  barge_in_detected
  t+ 258s  interrupt_step=begin interrupt_id=0f0443a7fa38 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 258s  interrupt_step=nothing_playing interrupt_id=0f0443a7fa38 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=4
  t+ 262s  barge_in_detected
  t+ 262s  interrupt_step=begin interrupt_id=4389d9fc98a9 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 262s  interrupt_step=nothing_playing interrupt_id=4389d9fc98a9 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.16 detect_ms=5
  t+ 266s  barge_in_detected
  t+ 266s  interrupt_step=begin interrupt_id=7355e4a6be3f call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 266s  interrupt_step=nothing_playing interrupt_id=7355e4a6be3f call=f55c8cf0-994 reason=barge_in elapsed_ms=0.34 detect_ms=5
  t+ 270s  barge_in_detected
  t+ 270s  interrupt_step=begin interrupt_id=10cf3ffca7f9 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 270s  interrupt_step=nothing_playing interrupt_id=10cf3ffca7f9 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+ 273s  barge_in_detected
  t+ 273s  interrupt_step=begin interrupt_id=5d8db6497ae8 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 273s  interrupt_step=nothing_playing interrupt_id=5d8db6497ae8 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.52 detect_ms=N
  t+ 277s  barge_in_detected
  t+ 277s  interrupt_step=begin interrupt_id=4001b98a0ef5 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 277s  interrupt_step=nothing_playing interrupt_id=4001b98a0ef5 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.27 detect_ms=N
  t+ 279s  llm_response turn=22 said='Still here.'
  t+ 279s  [SLOW] Turn 22 latency: 1012ms (STT-first: 24803ms, LLM-first-token: 809ms, TTS-first-chunk: 202ms, LLM-total: 1715ms,
  t+ 281s  barge_in_detected
  t+ 281s  interrupt_step=begin interrupt_id=6190aa45b0a6 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 281s  interrupt_step=nothing_playing interrupt_id=6190aa45b0a6 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=N
  t+ 286s  barge_in_detected
  t+ 286s  interrupt_step=begin interrupt_id=11d4ccbe69f4 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 286s  interrupt_step=nothing_playing interrupt_id=11d4ccbe69f4 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.38 detect_ms=N
  t+ 289s  barge_in_detected
  t+ 289s  interrupt_step=begin interrupt_id=8a7fa903ac05 call=f55c8cf0-994 reason=barge_in tts_active=True
  t+ 289s  interrupt_complete {'interrupt_id': '8a7fa903ac05', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 292s  barge_in_detected
  t+ 292s  interrupt_step=begin interrupt_id=02905c5bea05 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 292s  interrupt_step=nothing_playing interrupt_id=02905c5bea05 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.19 detect_ms=N
  t+ 295s  barge_in_detected
  t+ 295s  interrupt_step=begin interrupt_id=9f415d701c53 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 295s  interrupt_step=nothing_playing interrupt_id=9f415d701c53 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.16 detect_ms=N
  t+ 299s  barge_in_detected
  t+ 299s  interrupt_step=begin interrupt_id=4a53f5fc23f3 call=f55c8cf0-994 reason=barge_in tts_active=False
  t+ 299s  interrupt_step=nothing_playing interrupt_id=4a53f5fc23f3 call=f55c8cf0-994 reason=barge_in elapsed_ms=0.17 detect_ms=N
  t+ 302s  [SilenceMonitor] f55c8cf0-994 — nudge_audit nudges=0 suppressed=13

==============================================================================================
d068f4b8 — THE INTERRUPT FORENSICS — nothing_playing at 20:50:51
==============================================================================================
  t+   0s  resilient_stt_echo_guard probe=installed echo_tail_s=0.25 voiced_needed_s=6.0
  t+   0s  audio_stream_started call_id=d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c queue_size=0 stt_active=True
  t+   0s  recording_disclosure_speaking call_id=d068f4b8-cd1 reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  t+   4s  recording_disclosure_spoken call_id=d068f4b8-cd1
  t+   4s  outbound_greeting_presynth call_id=d068f4b8-cd1 chunks=33 text='[redacted chars=10 sha=00c5fd40]'
  t+   5s  outbound_greeting_presynth_done call_id=d068f4b8-cd1 elapsed_ms=1142 interrupted=False
  t+   7s  Flux StartOfTurn - User started speaking, barge-in detected
  t+   7s  barge_in_detected
  t+   7s  interrupt_step=begin interrupt_id=8c8336bbb970 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+   7s  interrupt_step=nothing_playing interrupt_id=8c8336bbb970 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.19 detect_ms=4
  t+   8s  Flux EndOfTurn: 'Hi. How are you doing? What's your name?'
  t+   8s  t_stt_first_final call_id=d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c
  t+  13s  llm_response turn=0 said="I'm good, thanks. I'm Sara Lehman from Talk-Lee."
  t+  13s  [SLOW] Turn 0 latency: 1156ms (STT-first: 245ms, LLM-first-token: 967ms, TTS-first-chunk: 178ms, LLM-total: 4459ms, TT
  t+  16s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  16s  barge_in_detected
  t+  16s  interrupt_step=begin interrupt_id=dd310b56c0aa call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  16s  interrupt_step=nothing_playing interrupt_id=dd310b56c0aa call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.33 detect_ms=8
  t+  19s  Flux EndOfTurn: 'k. So my name is Sam. So use Max. What's my name now?'
  t+  22s  llm_response turn=1 said="Got it — you're Max."
  t+  22s  [SLOW] Turn 1 latency: 984ms (STT-first: 0ms, LLM-first-token: 776ms, TTS-first-chunk: 198ms, LLM-total: 3207ms, TTS-t
  t+  24s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  24s  barge_in_detected
  t+  24s  interrupt_step=begin interrupt_id=5d457e10cde2 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  24s  interrupt_step=nothing_playing interrupt_id=5d457e10cde2 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.19 detect_ms=1
  t+  29s  Flux EndOfTurn: 'Can I remember these three things, apple, forty four, red? I will ask later.'
  t+  34s  llm_response turn=2 said='Got it — apple, forty-four, red.'
  t+  34s  [SLOW] Turn 2 latency: 1802ms (STT-first: 0ms, LLM-first-token: 1593ms, TTS-first-chunk: 193ms, LLM-total: 4984ms, TTS
  t+  37s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  37s  barge_in_detected
  t+  37s  interrupt_step=begin interrupt_id=e800d7b873dd call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  37s  interrupt_step=nothing_playing interrupt_id=e800d7b873dd call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.2 detect_ms=57
  t+  42s  Flux EndOfTurn: 'My project is a house in Texas. Actually, an office in Florida. What's the final project?'
  t+  45s  llm_response turn=3 said='The final project is an office in Florida.'
  t+  45s  [SLOW] Turn 3 latency: 1063ms (STT-first: 0ms, LLM-first-token: 748ms, TTS-first-chunk: 301ms, LLM-total: 3365ms, TTS-
  t+  47s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  47s  barge_in_detected
  t+  47s  interrupt_step=begin interrupt_id=0ec839353771 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  47s  interrupt_step=nothing_playing interrupt_id=0ec839353771 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.43 detect_ms=3
  t+  54s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+  54s  Flux EndOfTurn: 'k. I need the exact price, but I gave you no plans of project size. Can you still give me one?'
  t+  62s  capture_mode ENTER call_id=d068f4b8-cd1 (email/spell ask detected)
  t+  62s  llm_response turn=4 said="I can't give you a price without those details — I'll have the exact figure confirmed once I
  t+  62s  [SLOW] Turn 4 latency: 980ms (STT-first: 247ms, LLM-first-token: 767ms, TTS-first-chunk: 172ms, LLM-total: 7922ms, TTS
  t+  64s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  64s  barge_in_detected
  t+  64s  interrupt_step=begin interrupt_id=0479a76d17ef call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  64s  interrupt_step=nothing_playing interrupt_id=0479a76d17ef call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.32 detect_ms=1
  t+  71s  Flux EndOfTurn: 'k. So my deadline is Friday at three PM. What's fifteen multiply eight? Now what's my deadline?'
  t+  71s  capture_mode EXIT call_id=d068f4b8-cd1 (back to normal)
  t+  76s  llm_response turn=5 said='One hundred twenty. Your deadline is Friday at three PM.'
  t+  76s  [SLOW] Turn 5 latency: 1331ms (STT-first: 231ms, LLM-first-token: 1115ms, TTS-first-chunk: 208ms, LLM-total: 5145ms, T
  t+  78s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  78s  barge_in_detected
  t+  78s  interrupt_step=begin interrupt_id=c34a4c630207 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+  78s  interrupt_step=nothing_playing interrupt_id=c34a4c630207 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.37 detect_ms=1
  t+  80s  Flux EndOfTurn: 'I told you the project was in New York. Right?'
  t+  84s  llm_response turn=6 said='No, you said it was an office in Florida.'
  t+  84s  [SLOW] Turn 6 latency: 1066ms (STT-first: 0ms, LLM-first-token: 863ms, TTS-first-chunk: 201ms, LLM-total: 3407ms, TTS-
  t+  86s  Flux StartOfTurn backchannel 'Okay.' — barge-in suppressed
  t+  90s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+  93s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+  93s  Flux EndOfTurn: 'Okay. So the scope is drywall, plumbing, and electrical. Remove plumbing and add roofing. What slip?'
  t+  97s  llm_response turn=7 said='The scope is drywall, electrical, and roofing.'
  t+  97s  [SLOW] Turn 7 latency: 1054ms (STT-first: 0ms, LLM-first-token: 844ms, TTS-first-chunk: 193ms, LLM-total: 4236ms, TTS-
  t+ 100s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 100s  barge_in_detected
  t+ 100s  interrupt_step=begin interrupt_id=65e5e445bf13 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+ 100s  interrupt_step=nothing_playing interrupt_id=65e5e445bf13 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.16 detect_ms=6
  t+ 107s  Flux EndOfTurn: 'k. My company is ABC Builders. Change it to Ace Construction. Which name should you remember?'
  t+ 109s  llm_response turn=8 said='Ace Construction.'
  t+ 109s  [SLOW] Turn 8 latency: 1006ms (STT-first: 0ms, LLM-first-token: 816ms, TTS-first-chunk: 186ms, LLM-total: 2073ms, TTS-
  t+ 111s  Flux StartOfTurn backchannel 'Okay.' — barge-in suppressed
  t+ 114s  Flux EndOfTurn: 'Okay. What were the three things I asked you to remember earlier?'
  t+ 117s  llm_response turn=9 said='Apple, forty-four, and red.'
  t+ 117s  [SLOW] Turn 9 latency: 959ms (STT-first: 0ms, LLM-first-token: 760ms, TTS-first-chunk: 188ms, LLM-total: 3463ms, TTS-t
  t+ 119s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 119s  barge_in_detected
  t+ 119s  interrupt_step=begin interrupt_id=a54b7821ce26 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+ 119s  interrupt_step=nothing_playing interrupt_id=a54b7821ce26 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.41 detect_ms=4
  t+ 120s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 127s  Flux EndOfTurn: 'Good. Tell me only the final correct details from this call, not the old ones, and give it to me in t
  t+ 138s  llm_response turn=10 said='Got it — Ace Construction, office in Florida, scope is drywall, electrical, and roofing, de
  t+ 138s  [SLOW] Turn 10 latency: 991ms (STT-first: 159ms, LLM-first-token: 764ms, TTS-first-chunk: 192ms, LLM-total: 10493ms, T
  t+ 142s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 142s  barge_in_detected
  t+ 142s  interrupt_step=begin interrupt_id=aebb9e00e09f call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+ 142s  interrupt_step=nothing_playing interrupt_id=aebb9e00e09f call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.18 detect_ms=5
  t+ 143s  Flux EndOfTurn: 'And what's my name?'
  t+ 144s  llm_response turn=11 said='Max.'
  t+ 144s  [SLOW] Turn 11 latency: 989ms (STT-first: 232ms, LLM-first-token: 811ms, TTS-first-chunk: 178ms, LLM-total: 1386ms, TT
  t+ 146s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 146s  barge_in_detected
  t+ 146s  interrupt_step=begin interrupt_id=8f08b3af08f2 call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+ 146s  interrupt_step=nothing_playing interrupt_id=8f08b3af08f2 call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+ 148s  Flux EndOfTurn: 'the three things.'
  t+ 151s  llm_response turn=12 said='Apple, forty-four, and red.'
  t+ 151s  [SLOW] Turn 12 latency: 1043ms (STT-first: 232ms, LLM-first-token: 824ms, TTS-first-chunk: 206ms, LLM-total: 3506ms, T
  t+ 156s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 156s  barge_in_detected
  t+ 156s  interrupt_step=begin interrupt_id=4d5eaa5e07da call=d068f4b8-cd1 reason=barge_in tts_active=False
  t+ 156s  interrupt_step=nothing_playing interrupt_id=4d5eaa5e07da call=d068f4b8-cd1 reason=barge_in elapsed_ms=0.23 detect_ms=1
  t+ 158s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 160s  Flux EndOfTurn: 'Well, that will be all. Sara Lehman. Thank you. Take care, and goodbye.'
  t+ 163s  llm_response turn=13 said=''
  t+ 163s  [SLOW] Turn 13 latency: N/Ams (STT-first: 228ms, LLM-first-token: 772ms, TTS-first-chunk: 0ms, LLM-total: 812ms, TTS-t
  t+ 164s  [SilenceMonitor] d068f4b8-cd1 — nudge_audit nudges=0 suppressed=0
  t+ 165s  audio_stream_ended call_id=d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c chunks_yielded=4063 stt_active=False

==============================================================================================
9bf01238 — PRE-TTS HOLD TIMEOUT — caller had already stopped
==============================================================================================
  t+   0s  audio_stream_started call_id=9bf01238-a5ff-4006-ab2d-6a23379bdc4c queue_size=0 stt_active=True
  t+   0s  recording_disclosure_speaking call_id=9bf01238-a5f reason=tenant_default_two_party text='[redacted chars=60 sha=0e2a86
  t+   4s  recording_disclosure_spoken call_id=9bf01238-a5f
  t+   4s  outbound_greeting_presynth call_id=9bf01238-a5f chunks=15 text='[redacted chars=6 sha=0da72197]'
  t+   4s  Flux StartOfTurn - User started speaking, barge-in detected
  t+   4s  barge_in_detected
  t+   4s  interrupt_step=begin interrupt_id=f18b76476351 call=9bf01238-a5f reason=barge_in tts_active=True
  t+   4s  outbound_greeting_presynth_done call_id=9bf01238-a5f elapsed_ms=208 interrupted=True
  t+   5s  interrupt_complete {'interrupt_id': 'f18b76476351', 'ok': True, 'deduped': False, 'task_cancelled': False, 'local_byte
  t+  10s  Flux EndOfTurn: 'This will continue to remove your name, your company name, and the exact purpose of this call. I'm go
  t+  10s  t_stt_first_final call_id=9bf01238-a5ff-4006-ab2d-6a23379bdc4c
  t+  16s  llm_response turn=0 said="Got it — I'll hold off on that for now. What would you like to test first?"
  t+  16s  [SLOW] Turn 0 latency: 1532ms (STT-first: 229ms, LLM-first-token: 819ms, TTS-first-chunk: 692ms, LLM-total: 5874ms, TT
  t+  18s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  18s  barge_in_detected
  t+  18s  interrupt_step=begin interrupt_id=7b2e178665f8 call=9bf01238-a5f reason=barge_in tts_active=False
  t+  18s  interrupt_step=nothing_playing interrupt_id=7b2e178665f8 call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+  22s  Flux EndOfTurn: 'My name is James. Sorry. Actually, my name is Jason. What's my name?'
  t+  23s  llm_response turn=1 said='Jason.'
  t+  23s  [SLOW] Turn 1 latency: 1015ms (STT-first: 0ms, LLM-first-token: 804ms, TTS-first-chunk: 211ms, LLM-total: 1478ms, TTS-
  t+  27s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  27s  barge_in_detected
  t+  27s  interrupt_step=begin interrupt_id=b26a0aff71d2 call=9bf01238-a5f reason=barge_in tts_active=False
  t+  27s  interrupt_step=nothing_playing interrupt_id=b26a0aff71d2 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=4
  t+  34s  Flux EndOfTurn: 'Remember these exact three words, blueprint falcon twenty seven. Don't repeat them yet. I will ask yo
  t+  35s  llm_response turn=2 said='Got it.'
  t+  35s  [SLOW] Turn 2 latency: 1018ms (STT-first: 231ms, LLM-first-token: 814ms, TTS-first-chunk: 204ms, LLM-total: 1399ms, TT
  t+  38s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  38s  barge_in_detected
  t+  38s  interrupt_step=begin interrupt_id=bb980517d8a6 call=9bf01238-a5f reason=barge_in tts_active=False
  t+  38s  interrupt_step=nothing_playing interrupt_id=bb980517d8a6 call=9bf01238-a5f reason=barge_in elapsed_ms=0.39 detect_ms=6
  t+  41s  Flux EndOfTurn: 'I need a commercial estimate for Portland.'
  t+  46s  capture_mode ENTER call_id=9bf01238-a5f (email/spell ask detected)
  t+  46s  llm_response turn=3 said="I'll get you that exact figure. What's the best email for it?"
  t+  46s  [SLOW] Turn 3 latency: 994ms (STT-first: 247ms, LLM-first-token: 780ms, TTS-first-chunk: 200ms, LLM-total: 4377ms, TTS
  t+  47s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  47s  barge_in_detected
  t+  47s  interrupt_step=begin interrupt_id=e204174e2273 call=9bf01238-a5f reason=barge_in tts_active=False
  t+  47s  interrupt_step=nothing_playing interrupt_id=e204174e2273 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=9
  t+  49s  Flux EndOfTurn: 'Which Portland do you think... I mean,'
  t+  49s  capture_mode EXIT call_id=9bf01238-a5f (back to normal)
  t+  53s  llm_response turn=4 said='Portland, Oregon, or Maine?'
  t+  53s  [SLOW] Turn 4 latency: 993ms (STT-first: 0ms, LLM-first-token: 782ms, TTS-first-chunk: 201ms, LLM-total: 3137ms, TTS-t
  t+  56s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  56s  barge_in_detected
  t+  56s  interrupt_step=begin interrupt_id=a849312d004a call=9bf01238-a5f reason=barge_in tts_active=False
  t+  56s  interrupt_step=nothing_playing interrupt_id=a849312d004a call=9bf01238-a5f reason=barge_in elapsed_ms=0.15 detect_ms=4
  t+  67s  Flux EndOfTurn: 'k. I'm working on a residential project in Texas, around fifteen thousand square feet. Actually, magn
  t+  70s  llm_response turn=5 said='Commercial project in Florida.'
  t+  70s  [SLOW] Turn 5 latency: 1112ms (STT-first: 246ms, LLM-first-token: 899ms, TTS-first-chunk: 203ms, LLM-total: 2854ms, TT
  t+  73s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  73s  barge_in_detected
  t+  73s  interrupt_step=begin interrupt_id=3e6539463101 call=9bf01238-a5f reason=barge_in tts_active=False
  t+  73s  interrupt_step=nothing_playing interrupt_id=3e6539463101 call=9bf01238-a5f reason=barge_in elapsed_ms=0.43 detect_ms=6
  t+  82s  Flux EndOfTurn: 'k. I need the exact price for my estimate, but I haven't told you the project size. Drying, scope, tr
  t+  86s  capture_mode ENTER call_id=9bf01238-a5f (email/spell ask detected)
  t+  86s  llm_response turn=6 said="I'll get you that exact figure. What's the best email for it?"
  t+  86s  [SLOW] Turn 6 latency: 1179ms (STT-first: 231ms, LLM-first-token: 955ms, TTS-first-chunk: 210ms, LLM-total: 4603ms, TT
  t+  90s  Flux StartOfTurn - User started speaking, barge-in detected
  t+  90s  barge_in_detected
  t+  90s  interrupt_step=begin interrupt_id=7a1ce99fdeeb call=9bf01238-a5f reason=barge_in tts_active=False
  t+  90s  interrupt_step=nothing_playing interrupt_id=7a1ce99fdeeb call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=9
  t+  98s  Flux EndOfTurn: 'My project is a hotel renovation in Orlando. It has two... twenty drawing pages, and I need drywall l
  t+  98s  capture_mode EXIT call_id=9bf01238-a5f (back to normal)
  t+ 101s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 101s  barge_in_detected
  t+ 101s  interrupt_step=begin interrupt_id=5f3c458a5a08 call=9bf01238-a5f reason=barge_in tts_active=True
  t+ 101s  interrupt_complete {'interrupt_id': '5f3c458a5a08', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 102s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 103s  Flux EndOfTurn: 'Wait. What was your name again?'
  t+ 105s  llm_response turn=8 said='Sara Lehman.'
  t+ 105s  [SLOW] Turn 8 latency: 989ms (STT-first: 76ms, LLM-first-token: 779ms, TTS-first-chunk: 204ms, LLM-total: 1774ms, TTS-
  t+ 108s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 108s  barge_in_detected
  t+ 108s  interrupt_step=begin interrupt_id=ee25107339e5 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 108s  interrupt_step=nothing_playing interrupt_id=ee25107339e5 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=1
  t+ 111s  Flux EndOfTurn: 'k. Continue with what I was telling you before I interrupted.'
  t+ 115s  llm_response turn=9 said='I was asking for the best email to send that estimate to.'
  t+ 115s  [SLOW] Turn 9 latency: 1038ms (STT-first: 0ms, LLM-first-token: 804ms, TTS-first-chunk: 212ms, LLM-total: 4020ms, TTS-
  t+ 117s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 117s  barge_in_detected
  t+ 117s  interrupt_step=begin interrupt_id=4404e7bfee81 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 117s  interrupt_step=nothing_playing interrupt_id=4404e7bfee81 call=9bf01238-a5f reason=barge_in elapsed_ms=0.19 detect_ms=6
  t+ 120s  Flux EndOfTurn: 'The project is commercial in Chicago.'
  t+ 120s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 120s  barge_in_ignored_final_pre_tts call=9bf01238-a5f — protecting in-flight answer, playback held until the caller stops
  t+ 123s  pre_tts_hold_timeout call=9bf01238-a5f waited=2.51s reason=barge_in_during_generation — speaking anyway; a caller who 
  t+ 126s  Flux EndOfTurn: 'the bid is due Friday. Remember that. Now it's seventeen times six.'
  t+ 126s  barge_in_detected
  t+ 126s  interrupt_step=begin interrupt_id=d7e488addf30 call=9bf01238-a5f reason=barge_in tts_active=True
  t+ 126s  interrupt_complete {'interrupt_id': 'd7e488addf30', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 133s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 133s  barge_in_detected
  t+ 133s  interrupt_step=begin interrupt_id=497a01d5b287 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 133s  interrupt_complete {'interrupt_id': '497a01d5b287', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 135s  Flux EndOfTurn: 'What? Seventeen times six.'
  t+ 137s  llm_response turn=11 said='One hundred and two.'
  t+ 137s  [SLOW] Turn 11 latency: 1364ms (STT-first: 240ms, LLM-first-token: 789ms, TTS-first-chunk: 567ms, LLM-total: 2428ms, T
  t+ 141s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 141s  barge_in_detected
  t+ 141s  interrupt_step=begin interrupt_id=266351326f0d call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 141s  interrupt_step=nothing_playing interrupt_id=266351326f0d call=9bf01238-a5f reason=barge_in elapsed_ms=0.19 detect_ms=1
  t+ 143s  Flux EndOfTurn: 'After previously saying the project is in Chicago.'
  t+ 145s  llm_response turn=12 said='Commercial in Chicago.'
  t+ 145s  [SLOW] Turn 12 latency: 1057ms (STT-first: 0ms, LLM-first-token: 887ms, TTS-first-chunk: 166ms, LLM-total: 2398ms, TTS
  t+ 150s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 150s  barge_in_detected
  t+ 150s  interrupt_step=begin interrupt_id=99a8d57988cc call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 150s  interrupt_step=nothing_playing interrupt_id=99a8d57988cc call=9bf01238-a5f reason=barge_in elapsed_ms=0.43 detect_ms=7
  t+ 152s  Flux EndOfTurn: 'Earlier, I told you the project was in New York. Right?'
  t+ 156s  llm_response turn=13 said='No, you said Florida, then Chicago.'
  t+ 156s  [SLOW] Turn 13 latency: 990ms (STT-first: 0ms, LLM-first-token: 800ms, TTS-first-chunk: 178ms, LLM-total: 3893ms, TTS-
  t+ 160s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 160s  barge_in_detected
  t+ 160s  interrupt_step=begin interrupt_id=ec01e2ddaa0e call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 160s  interrupt_step=nothing_playing interrupt_id=ec01e2ddaa0e call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=4
  t+ 172s  [SilenceMonitor] 9bf01238-a5f — nudge SUPPRESSED, caller audio live rms=2040 n=1 (would have talked over them)
  t+ 175s  [SilenceMonitor] 9bf01238-a5f — silence (mid), nudging: 'Still there?'
  t+ 176s  Flux TurnResumed - User continued speaking, cancelling speculative LLM
  t+ 182s  Flux EndOfTurn: 'k. Listen carefully. My name is Michael Turner. My company is Apex Builders. We are bidding on a sixt
  t+ 183s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 183s  barge_in_detected
  t+ 183s  interrupt_step=begin interrupt_id=d69d6997d9e7 call=9bf01238-a5f reason=barge_in tts_active=True
  t+ 183s  interrupt_complete {'interrupt_id': 'd69d6997d9e7', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 185s  Flux EndOfTurn: 'Forty nine plus two.'
  t+ 186s  llm_response turn=15 said='Fifty-one.'
  t+ 186s  [SLOW] Turn 15 latency: 1025ms (STT-first: 0ms, LLM-first-token: 808ms, TTS-first-chunk: 213ms, LLM-total: 1646ms, TTS
  t+ 188s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 188s  barge_in_detected
  t+ 188s  interrupt_step=begin interrupt_id=09ef84ba8c23 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 188s  interrupt_step=nothing_playing interrupt_id=09ef84ba8c23 call=9bf01238-a5f reason=barge_in elapsed_ms=0.3 detect_ms=65
  t+ 189s  Flux EndOfTurn: 'What company do you work for?'
  t+ 191s  llm_response turn=16 said='Talk-Lee.'
  t+ 191s  [SLOW] Turn 16 latency: 1008ms (STT-first: 0ms, LLM-first-token: 793ms, TTS-first-chunk: 214ms, LLM-total: 1551ms, TTS
  t+ 193s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 193s  barge_in_detected
  t+ 193s  interrupt_step=begin interrupt_id=2ace30e13f89 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 193s  interrupt_step=nothing_playing interrupt_id=2ace30e13f89 call=9bf01238-a5f reason=barge_in elapsed_ms=0.37 detect_ms=5
  t+ 195s  Flux EndOfTurn: 'Forticipate present this call.'
  t+ 200s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 200s  barge_in_detected
  t+ 200s  interrupt_step=begin interrupt_id=8d51cde8783c call=9bf01238-a5f reason=barge_in tts_active=True
  t+ 200s  interrupt_complete {'interrupt_id': '8d51cde8783c', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes
  t+ 202s  Flux EndOfTurn: 'you tell me something about airplanes?'
  t+ 209s  llm_response turn=18 said="That's outside what I help with — I'm here for voice-system validation. What would you like
  t+ 209s  [SLOW] Turn 18 latency: 1246ms (STT-first: 0ms, LLM-first-token: 1047ms, TTS-first-chunk: 170ms, LLM-total: 7468ms, TT
  t+ 211s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 211s  barge_in_detected
  t+ 211s  interrupt_step=begin interrupt_id=fabec30dbaf8 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 211s  interrupt_step=nothing_playing interrupt_id=fabec30dbaf8 call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+ 218s  Flux EndOfTurn: 'Actually, change Miami to Orlando. Removing, floating, and my company is Apex contracting, not Apex B
  t+ 225s  capture_mode ENTER call_id=9bf01238-a5f (email/spell ask detected)
  t+ 225s  llm_response turn=19 said="Got it — Orlando, Apex Contracting. What's the best email for the details?"
  t+ 225s  [SLOW] Turn 19 latency: 1015ms (STT-first: 0ms, LLM-first-token: 832ms, TTS-first-chunk: 166ms, LLM-total: 7118ms, TTS
  t+ 227s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 227s  barge_in_detected
  t+ 227s  interrupt_step=begin interrupt_id=510590749ad9 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 227s  interrupt_step=nothing_playing interrupt_id=510590749ad9 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=5
  t+ 231s  Flux EndOfTurn: 'Without asking me any more questions. Summarize everything you can do know about my project.'
  t+ 231s  capture_mode EXIT call_id=9bf01238-a5f (back to normal)
  t+ 235s  llm_response turn=20 said='Commercial project in Orlando for Apex Contracting.'
  t+ 235s  [SLOW] Turn 20 latency: 995ms (STT-first: 243ms, LLM-first-token: 804ms, TTS-first-chunk: 175ms, LLM-total: 4138ms, TT
  t+ 240s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 240s  barge_in_detected
  t+ 240s  interrupt_step=begin interrupt_id=8e88d5aefb18 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 240s  interrupt_step=nothing_playing interrupt_id=8e88d5aefb18 call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=5
  t+ 242s  Flux EndOfTurn: 'And what were these three words?'
  t+ 245s  llm_response turn=21 said='Blueprint falcon twenty seven.'
  t+ 245s  [SLOW] Turn 21 latency: 1218ms (STT-first: 243ms, LLM-first-token: 833ms, TTS-first-chunk: 376ms, LLM-total: 2840ms, T
  t+ 247s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 247s  barge_in_detected
  t+ 247s  interrupt_step=begin interrupt_id=270b6ea1d704 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 247s  interrupt_step=nothing_playing interrupt_id=270b6ea1d704 call=9bf01238-a5f reason=barge_in elapsed_ms=0.16 detect_ms=6
  t+ 248s  Flux EndOfTurn: 'What's nineteen plus seventeen?'
  t+ 250s  llm_response turn=22 said='Thirty-six.'
  t+ 250s  [SLOW] Turn 22 latency: 1008ms (STT-first: 0ms, LLM-first-token: 834ms, TTS-first-chunk: 174ms, LLM-total: 1768ms, TTS
  t+ 252s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 252s  barge_in_detected
  t+ 252s  interrupt_step=begin interrupt_id=a66816d361f9 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 252s  interrupt_step=nothing_playing interrupt_id=a66816d361f9 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=1
  t+ 253s  Flux EndOfTurn: 'And where was the project?'
  t+ 255s  llm_response turn=23 said='Orlando.'
  t+ 255s  [SLOW] Turn 23 latency: 1124ms (STT-first: 0ms, LLM-first-token: 929ms, TTS-first-chunk: 195ms, LLM-total: 1786ms, TTS
  t+ 258s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 258s  barge_in_detected
  t+ 258s  interrupt_step=begin interrupt_id=eaea51bd6ce0 call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 258s  interrupt_step=nothing_playing interrupt_id=eaea51bd6ce0 call=9bf01238-a5f reason=barge_in elapsed_ms=0.18 detect_ms=6
  t+ 259s  Flux EndOfTurn: 'What was the total square feet?'
  t+ 263s  llm_response turn=24 said='You mentioned fifteen thousand square feet earlier.'
  t+ 263s  [SLOW] Turn 24 latency: 1028ms (STT-first: 0ms, LLM-first-token: 831ms, TTS-first-chunk: 185ms, LLM-total: 3570ms, TTS
  t+ 266s  Flux StartOfTurn backchannel 'Okay.' — barge-in suppressed
  t+ 268s  Flux EndOfTurn: 'Okay. That will be all. Thank you.'
  t+ 272s  llm_response turn=25 said="Sorry, I'm still here — what else can I help you with?"
  t+ 272s  [SLOW] Turn 25 latency: N/Ams (STT-first: 0ms, LLM-first-token: 808ms, TTS-first-chunk: 0ms, LLM-total: 849ms, TTS-tot
  t+ 274s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 274s  barge_in_detected
  t+ 274s  interrupt_step=begin interrupt_id=3de091d7803d call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 274s  interrupt_step=nothing_playing interrupt_id=3de091d7803d call=9bf01238-a5f reason=barge_in elapsed_ms=0.17 detect_ms=1
  t+ 275s  Flux EndOfTurn: 'Nothing. Thank you.'
  t+ 279s  llm_response turn=26 said="Sorry, I'm still here — what else can I help you with?"
  t+ 279s  [SLOW] Turn 26 latency: N/Ams (STT-first: 0ms, LLM-first-token: 842ms, TTS-first-chunk: 0ms, LLM-total: 881ms, TTS-tot
  t+ 282s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 282s  barge_in_detected
  t+ 282s  interrupt_step=begin interrupt_id=0f81b6da0e2c call=9bf01238-a5f reason=barge_in tts_active=False
  t+ 282s  interrupt_step=nothing_playing interrupt_id=0f81b6da0e2c call=9bf01238-a5f reason=barge_in elapsed_ms=0.2 detect_ms=13
  t+ 284s  Flux EndOfTurn: 'Thank you. Good night.'
  t+ 284s  Flux StartOfTurn - User started speaking, barge-in detected
  t+ 284s  barge_in_ignored_final_pre_tts call=9bf01238-a5f — protecting in-flight answer, playback held until the caller stops
  t+ 285s  Flux EndOfTurn: 'and take care. Goodbye.'
  t+ 287s  pre_tts_hold_timeout call=9bf01238-a5f waited=2.52s reason=barge_in_during_generation — speaking anyway; a caller who 
  t+ 289s  llm_response turn=27 said=''
  t+ 289s  [SLOW] Turn 27 latency: N/Ams (STT-first: 241ms, LLM-first-token: 900ms, TTS-first-chunk: 0ms, LLM-total: 906ms, TTS-t
  t+ 292s  [SilenceMonitor] 9bf01238-a5f — nudge_audit nudges=1 suppressed=3
  t+ 292s  audio_stream_ended call_id=9bf01238-a5ff-4006-ab2d-6a23379bdc4c chunks_yielded=7204 stt_active=False

```

---

## APPENDIX C — Commands behind every figure

```
journalctl -u talky-api -u talky-voice-worker --since today --no-pager > today.txt

# §2 the headline result
grep -c audio_stream_started today.txt
grep resilient_stt_stream_silent today.txt
grep resilient_stt_audit today.txt

# §3 the echo guard
grep resilient_stt_echo_guard today.txt        # must be probe=installed
grep -c "probe=ABSENT" today.txt               # must be 0
grep 5529d6f9 today.txt | grep -E "disclosure|presynth|resilient_stt"

# §4 the pre-TTS hold
grep -c barge_in_ignored_final_pre_tts today.txt
grep -c pre_tts_hold_timeout today.txt
grep -c pre_tts_hold_released today.txt        # 0 all day
grep -c "Flux EndOfTurn" today.txt
grep -c "llm_response turn=" today.txt

# §5 the interrupt forensics (microsecond resolution matters)
journalctl -u talky-api --since "20:50:38" --until "20:50:56" -o short-precise \
  --no-pager | grep d068f4b8

# §6 detection latency
grep -o "detect_ms=[0-9.]*" today.txt | cut -d= -f2 | sort -n
grep -o "'speech_to_stop_ms': [0-9.]*" today.txt | grep -o "[0-9.]*$" | sort -n
grep -o "'gw_ms': [0-9]*" today.txt

# §7 the two guards that ran for the first time
grep "nudge SUPPRESSED" today.txt
grep nudge_audit today.txt

# §8 the measurement gating the next fix
grep barge_in_margin today.txt

# the drain window
grep -o "drain_ms=[0-9-]*" today.txt
grep "'drain_probe': True" today.txt

# deployment verification, inside the running process
cd /opt/talky/backend && venv/bin/python -c "
from app.domain.services.voice_pipeline import interrupt as I
print(I._GATEWAY_DRAIN_S, hasattr(I, '_playback_may_still_be_audible'))"
```

---

## APPENDIX D — Effective configuration and kill switches

```
STT failover
  STT_FAILOVER_ENABLED           =     true
  primary / secondary            = deepgram flux-general-en / deepgram nova-3
  silent_stream_voiced_seconds   =      6.0
  audio_buffer_ms                =      500     -> 12 replayed chunks, as observed
  VOICE_STT_ECHO_TAIL_S          =     0.25     NEW — echo decay window

STT fault injection (OFF)
  VOICE_STT_FAULT_SILENT_CAMPAIGN=  (unset)     inert
  VOICE_STT_FAULT_SILENT_UNTIL   =  (unset)     unparseable would fail CLOSED

Interrupt / barge-in
  _INTERRUPT_DEDUPE_S            =     0.35
  _RESUME_WINDOW_S               =     0.75     resume attribution, never a gate
  VOICE_GATEWAY_DRAIN_S          =      0.6     NEW — ask the gateway inside this window
  _MAX_HOLD_S (pre-TTS hold)     =      2.5     always hit, never released early

Voice-onset anchor
  VOICE_ONSET_RMS                =      500
  VOICE_ONSET_GAP_S              =      0.4

Silence monitor
  VOICE_AUDIO_ACTIVE_RMS         =      500     first suppression fired today
  VOICE_AUDIO_ACTIVE_MAX_AGE_S   =      2.0
  VOICE_SILENCE_HANGUP_S         =       60

LLM
  model                          = qwen/qwen3.6-27b   (no prompt caching on Groq)
  LLM_SECONDARY_PROVIDER         = gemini / gemini-2.5-flash
  prompt_time p50                =    635ms
```

### Kill switches — no redeploy required

| switch | effect |
|---|---|
| `VOICE_GATEWAY_DRAIN_S=0` | restores the exact pre-`f750b1f2` interrupt behaviour |
| `VOICE_STT_ECHO_TAIL_S=0` | removes the echo decay tail only |
| `STT_FAILOVER_ENABLED=false` | disables the watchdog and Nova promotion entirely |
| `VOICE_ONSET_RMS=999999` | disables the caller-onset anchor |
| `VOICE_AUDIO_ACTIVE_RMS=999999` | disables the acoustic nudge guard |
| `VOICE_STT_FAULT_SILENT_CAMPAIGN=` | disarms fault injection |

---

*End of report 7.*
