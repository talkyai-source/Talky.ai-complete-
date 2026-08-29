# Report 9 — The agent was never channel 0

**Prod HEAD `e00dfa2e`** · deployed 2026-08-20 · 5 services active ·
`{"ready":true,"db":"ok","redis":"ok"}` · zero post-restart warnings ·
gate **4,847 passed / 8 failed / 5 errors** = the pristine baseline exactly.

Rollback: `3d0ca65d`.

---

> **The short version.** The headline finding of report 8 — that interrupts left
> the caller hearing the agent for 1.4–2.4 seconds — **was not real**. It came
> from my tool identifying the wrong stereo channel as the agent. Measured
> correctly, the caller stops hearing the agent in **17–170 ms**. Report 8 §17
> and §18 have been rewritten as a retraction. This report is the forensics of
> how that happened, what the corrected numbers are, and the two deliverables
> that did hold up: the controlled silent-Flux watchdog proof, and an
> observability gap that was making the hold fix look dead when it was working.

---

## Contents

| § | |
|---|---|
| 1 | What you asked for, and the one thing I could not do |
| 2 | The calls that already existed on `3d0ca65d` |
| 3 | **The correction** — the agent was never channel 0 |
| 4 | How it was caught, and how close it came to standing |
| 5 | Caller-speech-to-audible-stop, measured from the recording |
| 6 | Did cancelled audio ever resume? |
| 7 | **`pre_tts_hold_released` is the wrong proof metric** |
| 8 | The one timeout since the fix, and what it proves |
| 9 | The controlled silent-Flux watchdog test |
| 10 | Two analyses I built, ran, and threw away |
| 11 | The gate and the deploy |
| 12 | Pre-mortem |
| 13 | Post-mortem — four measurement bugs in one tool |
| 14 | Open items, and what one dedicated call settles |
| A | Charts — every measured figure |
| B | The corrected reconciliation, all four calls verbatim |
| C | Commands, so you can reproduce all of it |
| D | Config and kill switches |
| E | Forensic timelines — the six measured interrupts |

---

## 1. What you asked for, and the one thing I could not do

Your brief:

> Please run a new recorded call on `3d0ca65d` with at least five deliberate
> mid-sentence interruptions. Confirm `pre_tts_hold_released`, measure
> caller-speech-to-audible-stop from the recording, and check that cancelled
> audio never resumes. Next, root-cause the 1.4–2.4 second audible tails. Also
> provide a controlled silent-Flux watchdog test proving Nova takes over without
> losing or duplicating the buffered caller sentence. Please don't close these
> items using internal timings only.

**I cannot run the call.** Not as a policy dodge — as a practical fact. The test
needs a human who talks and then interrupts mid-sentence, five times. An
automated call reaches voicemail or silence and produces exactly zero
mid-sentence interruptions, which is the one thing the test is for. I also did
not start a campaign to generate traffic.

What I did instead: **six calls already existed on `3d0ca65d`** from 2026-08-20,
placed after the deploy. Four had interrupts. Everything measured below comes
from those recordings, not from internal timings — which is what you asked for,
and which is precisely why the report 8 finding collapsed.

Item by item:

| Ask | Status |
|---|---|
| Run a recorded call, ≥5 mid-sentence interruptions | **Cannot place calls.** Used 4 existing calls on `3d0ca65d`; they yield 6 genuinely mid-sentence interrupts, across calls rather than in one |
| Confirm `pre_tts_hold_released` | **Not confirmed** — and §7 shows the metric cannot prove what it was meant to. Fixed the instrumentation |
| Caller-speech-to-audible-stop, from the recording | **Done** — 17, 45, 48, 123, 149, 170 ms |
| Cancelled audio never resumes | **Done** — no resume; the one apparent case was the next reply |
| Root-cause the 1.4–2.4 s tails | **Retracted.** They were my channel-mapping error, not a product defect |
| Controlled silent-Flux watchdog test | **Done** — 0 frames lost, 0 duplicated, order preserved |

---

## 2. The calls that already existed on `3d0ca65d`

Six recordings, 2026-08-20, all after the 20:27:46 UTC deploy:

```
  2026-08-20 14:53  ceedb6eb  1,776,684 bytes
  2026-08-20 14:51  8637d426  3,148,844 bytes
  2026-08-20 14:49  8ec4c9d7    378,924 bytes
  2026-08-20 11:18  5c82cf82  9,021,484 bytes
  2026-08-20 11:13  87917db4 11,453,484 bytes
  2026-08-20 11:07  b1611909  4,052,524 bytes
```

Mapped to voice sessions and profiled:

```
  recording  voice_session  EndOfTurns  interrupt_begin  reached_gw
  87917db4   cb4bd059           20          18              8
  5c82cf82   58fcbcb5           14          13              4
  b1611909   74b976e6            2           2              1
  8637d426   8027ce6b            0           9              6
  ceedb6eb   018d5c62            0           7              5
  8ec4c9d7   bb915b7d            0           0              0
```

A practical note that costs time if you don't know it: recordings are named
after the **dialer's `calls.id`**, which is a different UUID from the voice
session every pipeline log line is keyed on. The one line carrying both:

```
bind_telephony_call voice_session=cb4bd059 -> calls.id=87917db4 pbx=talky-out-20
```

The tool resolves this automatically now.

The three calls with `EndOfTurns=0` are worth a separate look — `8637d426` in
particular ran nine interrupts and six gateway teardowns without Flux ever
emitting a text EndOfTurn. That is also the call that produced the single
pre-TTS hold timeout in §8.

---

## 3. The correction — the agent was never channel 0

The recordings are stereo, one leg per channel. That is what makes this analysis
possible at all: agent audio and caller audio can be measured separately, so
"when did the caller stop hearing the agent" is directly observable rather than
inferred.

Everything depends on knowing **which channel is which**. My tool decided it like
this:

```python
# the original, and it is wrong
agent_is_a = (ob is None) or (oa is not None and oa <= ob)
# "the agent speaks first (disclosure, then greeting) and speaks more"
```

That assumption is false on every call measured. On an outbound call the
**caller** picks up and says "hello" — *before* the recording disclosure begins.
So the earliest onset belongs to the caller, not the agent, and the tool
consistently labelled the legs backwards.

The consequence is not a small numerical error. It **inverts the meaning of the
entire report**. The caller continuing to talk for a second or two after they
barge in is completely normal — that is what barging in *is*. Read off the wrong
channel, it becomes "the agent kept playing for 1.4 seconds after we stopped
it", which is a serious defect. That is exactly the finding report 8 published,
with two proposed root causes and a task opened against it.

### The evidence-based test

The agent's audio must appear right after each synthesis start and be absent
otherwise. Score both channels against every `TTS_FMT_DEBUG` in the call,
anchoring each hypothesis on its own first onset so it is self-consistent:

```python
def _score(env, own_onset):
    org = tts_times[0] - timedelta(seconds=own_onset)
    tot = 0.0
    for t in tts_times:
        s = (t - org).total_seconds()
        # the second AFTER synthesis starts: the agent should be talking
        i0, i1 = int((s + 0.1) * 50), int((s + 1.1) * 50)
        tot += voiced_fraction(env, i0, i1)
    return tot / len(tts_times)
```

Run on all four calls:

```
  call        synthesis events   channel 0   channel 1   agent
  1e9d7faf (Aug 19)     42          38.2%       70.6%     ch1
  cb4bd059 (Aug 20)     19           4.4%       74.8%     ch1
  58fcbcb5 (Aug 20)     17           8.1%       76.1%     ch1
  74b976e6 (Aug 20)      6          36.7%       51.7%     ch1
```

**Channel 1 every time**, the opposite of what the heuristic chose. On the two
long conversational calls the separation is enormous — 4.4% vs 74.8% — and not
the sort of thing that needs a judgement call.

The tool now warns when the two channels score within 15 points of each other,
so an unseparated recording cannot silently produce numbers again.

**One honest caveat.** `74b976e6` scores 36.7% vs 51.7% — a gap of exactly 15.0
points, which does *not* trip the warning (`< 0.15` is a strict comparison) and
rests on only six synthesis events. Its single measurement should be treated as
weak evidence, and the threshold is arguably one point too permissive. I have
left it as-is rather than tune it to the one sample that provoked the question.

---

## 4. How it was caught, and how close it came to standing

It was not caught by suspecting the channel. It was caught by chasing the
finding *forward* into a root cause and having every explanation fail.

**Step 1 — the hypothesis that should have worked.** If Python streams TTS
faster than real time, a downstream buffer accumulates during a long utterance,
and an interrupt landing deep into one finds far more audio committed than the
gateway holds. Falsifiable prediction: the tail should scale with how long the
agent had been talking. Measured:

```
  interrupt     talking_for        tail      gw_ms gw_frames agent_rms
  55d44f1953a9       0.34s      1170ms       240ms        12       682
  cc3fec484c6d       1.40s       759ms       160ms         8      4985
  d317711e2e42       1.16s      1416ms         0ms         0      3635

  talking_for vs tail:  r = -0.354   (n=3)
```

Negative correlation, and the longest tail belonged to the *shortest* utterance —
0.34 s of speech cannot have accumulated 1,170 ms of buffer. Hypothesis dead.

**Step 2 — the resume check that produced a false positive.** Testing whether
cancelled audio restarted, two of three interrupts came back "RESUMED with NO new
synthesis". That looked like a second defect. Chasing the strongest case into the
raw journal:

```
  11:12:41  interrupt_step=begin interrupt_id=d317711e2e42
  11:12:41  interrupt_complete {... 'gw_frames': 0 ...}
  11:12:43  Flux EndOfTurn: 'What exact sentence did I just say?'
  11:12:44  TTS_FMT_DEBUG call=cb4bd059
```

There *is* a synthesis start at 11:12:44, inside the gap my script claimed was
empty. The synthesis events sit at:

```
  ... 99.99, 106.96, 123.43, 141.13 ...
```

and audio returned at **123.34 s** — 90 ms *before* the synthesis that produced
it. Audio cannot precede its own synthesis. That impossibility was the thread:
it meant the anchor carried a systematic bias, which meant the clock mapping was
suspect, which is what sent me to check the channel mapping underneath it.

**How close it came to standing.** If I had stopped at "the tails are real,
root cause unknown, filed as open" — which is exactly how report 8 left it — the
error would have survived indefinitely. It was only the attempt to *explain* it
that killed it. Two failed explanations were worth more than the finding.

---

## 5. Caller-speech-to-audible-stop, measured from the recording

The corrected measurement, on every interrupt across four calls where the agent
was **genuinely audible** at the moment we decided to stop:

```
  17, 45, 48, 123, 149, 170 ms          n=6   median 85ms   max 170ms
  gateway discarded alongside:          220-260ms in 5 of 6
```

Per call, and note what dominates:

**`58fcbcb5`** (Aug 20, `3d0ca65d`) — 13 interrupt events, 4 reached the gateway,
three of them genuinely mid-word:

```
  f299effcd690    45ms    260ms discarded   rms 4164   STOPPED CLEANLY
  73d9dee77ea1    48ms    240ms discarded   rms  723   STOPPED CLEANLY
  103da1b89793    17ms    220ms discarded   rms  566   STOPPED CLEANLY
```

**`cb4bd059`** (Aug 20, `3d0ca65d`) — 18 interrupt events, 8 reached the gateway,
**seven found the agent leg already silent**:

```
  55d44f1953a9   170ms    240ms discarded   rms 2285
```

**`1e9d7faf`** (Aug 19, `f750b1f2`) — 36 interrupt events, 6 reached the gateway:

```
  82daa5f70789   123ms    260ms discarded   rms 2714
  6d08744af436   149ms    260ms discarded   rms 1417
```

Two things this establishes that internal timings could not.

**The barge-in path works.** A caller who interrupts mid-word stops hearing the
agent inside a fifth of a second, worst case measured.

**The `f750b1f2` drain window is doing visible work.** In five of six cases the
gateway still held 220–260 ms of audio that had not yet played, and discarded it.
That is audio the caller was **spared** — `gw_ms` is what we threw away, not what
they heard. A correct stop is therefore a near-zero audible tail *together with*
a non-zero `gw_ms`, which is what the data shows.

Most interrupts never get that far. Since the fix:

```
  interrupt_step=begin (all)         52   100.0%
  nothing_playing shortcut           28    53.8%
  reached the gateway                24    46.2%
    of which drain-window probes      1     1.9%
```

Over half are ordinary turns with nothing playing. They cannot talk over anyone
and are excluded from the reconciliation — counting them would drown the six
measurements that matter.

---

## 6. Did cancelled audio ever resume?

No.

The first pass said otherwise, and the method was wrong in a way worth recording.
It looked for the first **200 ms** of silence on the agent leg to mark the end of
an utterance. 200 ms is shorter than an ordinary pause between clauses in
synthesised speech, so "audio returned after 220 ms" was simply the next word.

Corrected method, on two axes:

* an utterance boundary needs a **500 ms** gap, not 200 ms;
* resumed audio only counts as a resume if **no new synthesis started** in
  between — a fresh `TTS_FMT_DEBUG` in the gap means the agent's next reply,
  which is correct behaviour.

With the channel corrected as well, every case resolves to either "the agent was
already silent when we decided to stop" or "the audio that came back was the next
reply, with its own synthesis event". There is no instance of cancelled audio
restarting.

---

## 7. `pre_tts_hold_released` is the wrong proof metric

You asked me to confirm `pre_tts_hold_released` occurs. It has not occurred. Two
separate reasons, and the second one matters more than the first.

**Reason one — the sample is two.** Since the fix:

```
  holds ARMED (barge_in_ignored_final_pre_tts)   2
    -> pre_tts_hold_timeout                      1
    -> pre_tts_hold_released                     0
```

against, before the fix:

```
  holds ARMED                                   34
    -> pre_tts_hold_timeout                     16
    -> pre_tts_hold_released                     0
```

Two arms is not enough to conclude anything in either direction.

**Reason two — the metric cannot show the fix working.** This is the real
finding, and I should have seen it before proposing the metric.

`await_caller_pause` only logs a release if the hold **enters its wait loop** and
the flag drops *while it waits*. But look at the entry condition:

```python
if not getattr(session, "_defer_playback_for_caller", False):
    return 0.0
disarm_pre_tts_hold(session)
if not caller_is_speaking(session):
    return 0.0          # <-- silent
```

The better the EndOfTurn clear gets, the more often the flag is **already down**
when playback becomes ready — and that path returned silently, producing no log
line at all. So "the clear ran in time", which is the success case, was
indistinguishable in the journal from "the hold was never armed".

**Counting releases alone would read a working fix as a dead one.** That is the
same class of error as the three guards wired to constant signals: a measurement
that cannot distinguish success from absence.

Fixed by naming the good outcome:

```python
logger.info(
    "pre_tts_hold_not_needed call=%s reason=%s — caller had already "
    "yielded when playback became ready; nothing to wait for",
    call_id[:12], getattr(session, "_defer_playback_reason", "unknown"),
)
```

with a test that pins it and pins that it must **not** be counted as a release:

```python
assert "pre_tts_hold_not_needed" in caplog.text
assert "pre_tts_hold_released" not in caplog.text, (
    "an instant return must not be counted as a release — that would "
    "inflate the metric that proves the hold does its job"
)
```

The correct proof is now three numbers, not one: `armed` splits into
`not_needed` (clear won the race), `released` (the hold waited and the caller
stopped), and `timeout` (the caller never yielded). The first two are both
successes; only the third is the old failure.

---

## 8. The one timeout since the fix, and what it proves

```
pre_tts_hold_timeout call=8027ce6b-9ef waited=2.52s
    reason=barge_in_during_generation quiet_ms=160
```

`quiet_ms=160` is the new instrumentation, and it does two jobs.

**It shows this timeout was correct.** The caller's last voiced frame was 160 ms
ago — well under the 700 ms window — so they were genuinely still audible. Both
exits correctly declined to fire: no EndOfTurn had arrived, and the audio said
the caller had not stopped. Holding to the cap and then speaking is exactly the
documented fail-open behaviour for "a caller who never yields".

**It is the first live proof the acoustic signal varies.** Three guards this
month were wired to inputs that were constant in production and therefore dead.
`quiet_ms=160` is a real, non-`none`, non-zero reading taken during a live hold —
`_caller_quiet_for_s` is returning genuine data from `_caller_voice_last_at`, not
`None` and not a fixed value. Before this line existed there was no way to tell
those apart from the outside.

Note also the call it happened on: `8027ce6b`, one of the three with **zero Flux
EndOfTurns**. That is mechanism (b) from report 8 — a StartOfTurn with no
counterpart event — and the acoustic exit is the only thing that could ever
release such a hold. Here it correctly chose not to.

---

## 9. The controlled silent-Flux watchdog test

You asked for a controlled test proving Nova takes over **without losing or
duplicating the buffered caller sentence**. New file,
`test_stt_failover_no_loss_no_duplication.py`, 9 tests.

"It failed over" is the weaker claim. A failover that drops the caller's
half-finished sentence makes the agent reply to nothing; one that replays it
twice makes the agent answer the same question two ways. Both are worse than the
silent stream, because both look like the agent is *broken* rather than deaf.

**The controlled fault** is deaf in exactly the way Flux was — consumes every
chunk, returns no event, never errors. That is the failure mode both earlier
safety nets were blind to, because both were transcript-derived and a provider
returning nothing produces no transcript to be suspicious of.

**Loss and duplication are made decidable** by giving every frame its own
identity rather than counting:

```python
def _voiced(index: int) -> AudioChunk:
    """A frame of real speech energy carrying its own index."""
    samples = [index] + [3000 if i % 2 else -3000 for i in range(_SAMPLES - 1)]
    return AudioChunk(data=struct.pack(f"<{_SAMPLES}h", *samples),
                      sample_rate=16000)
```

The result:

```
  caller sentence            : 190 frames x 40ms = 7.6s
  watchdog threshold         : 6.0s of voiced audio

  deaf primary (flux) got    : frames 0..148  (149)
  nova got                   : frames 138..189  (52)
     of which replayed       : 12  (buffer = 500ms)
     of which live           : 40

  frames seen by NEITHER     : 0   <- loss at the seam
  frames nova saw TWICE      : 0   <- duplication
  nova order preserved       : True
  transcripts emitted        : ['rescued']
```

`buffered_chunks=12` matches production call `dec0bb16` exactly — the same figure
the watchdog logged when it fired unprompted on 2026-08-18.

**The seam is pinned specifically.** The chunk whose arrival trips the watchdog
must survive the handover, and it does only because of statement ordering in
`_tee_audio`:

```python
async for chunk in audio_stream:
    buffer.add(chunk)                       # <-- BEFORE
    if watchdog.observe_audio(chunk, ...):  # <-- the check
        raise STTStreamSilentError(...)
```

Reverse those two lines and a syllable is lost mid-word, with nothing in the logs
to show it. There is now a test that fails if anyone does.

**The buffer limit is pinned as a decision, not a bug.** The replay buffer holds
500 ms; caller audio older than that was consumed by the deaf primary and is
genuinely gone. Recovering it would mean buffering whole calls. The test asserts
the limit holds in *both* directions — not zero replayed, and not the whole call
— so if someone later enlarges it, they do so deliberately, and so nobody
mistakes the limit for a defect during an incident.

---

## 10. Two analyses I built, ran, and threw away

Both produced clean, plausible, quotable numbers. Both were artifacts of my own
method. Recording them because the near-miss is the point.

**Pipeline lag drift.** Pair each synthesis start with the next agent-audio onset;
if a buffer accumulates, the lag grows across the call. Output:

```
    1       11.03s       14.36s     3332ms
    2       12.82s       16.90s     4080ms
    3       18.04s       18.48s      442ms
    4       31.03s       31.32s      292ms
    5       39.61s       43.32s     3708ms
```

Bimodal — either ~300 ms or ~3,500 ms, nothing between. Real pipeline delay does
not do that. The cause was my onset splitter: a 400 ms silence threshold cut long
utterances into several "onsets", and the greedy pairing walked out of sync.
Discarded.

**End-of-send versus end-of-audible.** Compare `turn_complete` to the last
audible agent frame; the gap is the downstream depth. Output:

```
  turn_complete   25.18s -> last audible   30.82s   depth   +5639ms
  turn_complete   41.59s -> last audible   47.56s   depth   +5966ms
  turn_complete   58.92s -> last audible   64.50s   depth   +5583ms
  ...
  n=12  min +1866ms  median +5647ms  max +5966ms
```

A tight cluster at 5.6–6.0 seconds looks like a real system constant. It is my
`look=6.0` search window: the function returned the last audible frame *within
six seconds*, and with the agent speaking again by then, the answer was pinned to
the window edge. Discarded.

Neither reached you as a finding. The first would have claimed a 4-second
pipeline delay; the second a 5.6-second downstream buffer. Both would have been
completely wrong, and both were caught by the same question: *is this number
physically plausible for the thing it claims to measure?*

---

## 11. The gate and the deploy

```
8 failed, 4847 passed, 15 skipped, 1102 warnings, 5 errors in 184.72s
```

The 8 failures and 5 errors are the known pristine baseline — systemd
install-script permissions, webhook HMAC and IDOR tests needing secrets,
metrics-endpoint auth. Identical set, identical count, on unmodified HEAD.
**+10 tests** over the previous run (9 failover, 1 hold-not-needed).

Two commits:

```
4ab7d67b  fix(tools): identify the agent's channel from evidence, not from who speaks first
e00dfa2e  test(voice): pin the silent-Flux handover, and stop the hold fix being invisible
```

Deploy, gated at `active_sessions: 0`:

```
prod HEAD    e00dfa2e
import smoke import OK
services     talky-api talky-voice-worker talky-dialer-worker
             talky-reminder-worker talky-voice-gateway  = all active
health       {"ready":true,"db":"ok","redis":"ok"}
post-restart warnings/errors: none
```

Verified inside the running process, not on disk:

```
not_needed log  = True
quiet release   = 0.7
```

---

## 12. Pre-mortem

For the two changes that shipped today.

| # | Failure | Mitigation |
|---|---|---|
| 1 | The channel test itself picks wrong on a call where the agent barely speaks | It needs synthesis events to score against; with none it refuses to run rather than guessing. With few, the margin narrows and the 15-point warning fires |
| 2 | The 15-point warning threshold is mis-set | Demonstrated on `74b976e6` at exactly 15.0 points — it did **not** fire and arguably should have. Recorded in §3 rather than tuned to one sample |
| 3 | `pre_tts_hold_not_needed` floods the journal | It fires only when a hold was **armed**, which happened twice in 136 calls. Not a hot path |
| 4 | The new log is mistaken for a release and inflates the success metric | Test asserts `pre_tts_hold_released` is absent on that path |
| 5 | The failover test passes because the fault injector is more permissive than production | The deaf primary implements the real `STTProvider` interface and is driven through the real `ResilientSTTProvider`; the abstract-method requirement (`initialize`) caught the first draft doing exactly this |
| 6 | The 500 ms buffer assertion is so loose it never fails | Bounded on both sides — at least one frame replayed, at most `_BUFFERED_CHUNKS + 2` |

---

## 13. Post-mortem — four measurement bugs in one tool

`barge_in_waveform_reconcile.py` has now had four defects. Listing them together
because the pattern is more useful than any one of them:

| # | Bug | How it was caught |
|---|---|---|
| 1 | Paired each interrupt `begin` with the next `complete` **by time**, though only ~1 in 6 logs a completion | `detect_ms=492.3` repeated character-identical on twenty rows |
| 2 | Searched forward for silence without checking the agent was audible, so it measured the **next reply** | A 2,389 ms "overhang" on an interrupt with an empty queue |
| 3 | Compared the audible tail against `gw_ms` expecting them to match, when `gw_ms` is audio **discarded** | Reasoning, before publication |
| 4 | **Identified the agent's channel by who speaks first** | Two failed root-cause attempts, then an impossibility: audio 90 ms *before* its own synthesis |

Bugs 1–3 were caught because they produced something **absurd**. Bug 4 produced
something **plausible** — 1.4 seconds of talk-over is precisely the complaint
this whole line of work started from — so it agreed with what I already believed
and sailed into a published report.

That is the second time in two reports. Report 8 §13 documents the same shape:
a 15.5% unanswered-turn rate that survived a full cycle because a bad number
confirmed a bad story, while the corrected run's "100.0% unanswered" was spotted
in seconds for being obviously impossible.

**An implausible success number gets audited. An implausible failure number gets
believed.** The countermeasure is not more care — it is refusing to let a
channel, a denominator, or a matcher be settled by assumption when it can be
settled by evidence. Every one of these four bugs was an assumption standing in
for a measurement that was available all along.

What went right today: not stopping at "the tails are real, cause unknown". The
finding died because I tried to explain it, and both explanations failed. Filing
it as open — which is what report 8 did — would have preserved it indefinitely.

---

## 14. Open items, and what one dedicated call settles

| # | Item | State |
|---|---|---|
| 61 | `_caller_speaking` leak | Fixed, deployed `3d0ca65d`; live confirmation still thin (2 arms) |
| 62 | Waveform ↔ gateway reconciliation | **Done** — 17–170 ms, measured from four recordings |
| 64 | ~~1.4–2.4 s audible tails~~ | **Retracted** — channel-mapping error, closed invalid |
| — | Silent-Flux handover no-loss/no-duplication | **Done** — 0 lost, 0 duplicated, order preserved |
| 63 | STT failover still ~21% of calls post-guard | **Open** — 2 more calls failed over since the fix |
| — | `detect_ms` p50 606 ms, p90 1,058 ms, max 6.2 s | **Open** — this, not the stop, is the remaining latency |
| 44 | Recordings lost when caller barges over the disclosure | Open **by your decision** |
| 43 | Prompt prefill ~629 ms of ~641 ms TTFT | Open |
| 54 | `CredentialResolver._CACHE` keyed on `id(db_pool)` | Open; needs authorisation |
| 59 | 5 tenant configs on dead `llama-3.1-8b-instant` | Open; needs a DB write + authorisation |

**What one dedicated call adds that these four could not.** Only **two** pre-TTS
holds have been armed since the fix, in 136 calls. The hold arms when a
StartOfTurn lands while a final answer is still generating — a narrow window. To
hit it deliberately: **interrupt just as the agent is about to start speaking**,
in the beat after it stops listening, rather than mid-sentence. Mid-sentence
interruptions exercise the barge-in path, which the data now shows is healthy;
the *hold* needs the other timing.

Five of those would move `armed` from 2 to a number where `not_needed` versus
`released` versus `timeout` says something. That is the last thing standing
between the `_caller_speaking` fix and a genuine live confirmation.

Detection latency (§14, `detect_ms`) is now the largest remaining gap between the
caller's experience and ours: they had been speaking between 1 ms and 957 ms
before we reacted. The stop is fast; the noticing is not.

---

## Appendix A — Charts: every measured figure

From 37,032 journal lines covering the deploy at 2026-08-19 20:27 UTC onward.

```
==========================================================================
  CHART A - interrupt outcomes since the fix (2026-08-19 20:27 ->)
==========================================================================
  interrupt_step=begin (all)         | ########################################   52 100.0%
  nothing_playing shortcut           | ######################                     28  53.8%
  reached the gateway                | ##################                         24  46.2%
    of which drain-window probes     | #                                           1   1.9%

==========================================================================
  CHART B - detect_ms: caller's acoustic onset to our decision
==========================================================================
  detect_ms   n=24
    p50 606ms   p90 1058ms   max 6157ms   min 38ms
          0-200 ms | ########################################    6  25.0%
        200-400 ms | ####################                        3  12.5%
        400-600 ms | ####################                        3  12.5%
        600-800 ms | ########################################    6  25.0%
       800-1200 ms | ###########################                 4  16.7%
      1200-2000 ms | #######                                     1   4.2%
      2000-3000 ms |                                             0   0.0%
          3000+ ms | #######                                     1   4.2%

  speech_to_stop_ms   n=24
    p50 607ms   p90 1170ms   max 6161ms   min 40ms
          0-200 ms | #############                               2   8.3%
        200-400 ms | ########################################    6  25.0%
        400-600 ms | ###########################                 4  16.7%
        600-800 ms | #################################           5  20.8%
       800-1200 ms | #################################           5  20.8%
      1200-2000 ms | #######                                     1   4.2%
      2000-3000 ms |                                             0   0.0%
          3000+ ms | #######                                     1   4.2%

==========================================================================
  CHART C - gw_ms: audio the gateway DISCARDED (caller was spared it)
==========================================================================
  gw_ms   n=24
    p50 0ms   p90 240ms   max 260ms   min 0ms
            0-1 ms | ########################################   14  58.3%
          1-100 ms | ###                                         1   4.2%
        100-200 ms | ###########                                 4  16.7%
        200-240 ms | ###                                         1   4.2%
        240-260 ms | ######                                      2   8.3%
        260-300 ms | ######                                      2   8.3%
           300+ ms |                                             0   0.0%
    empty queue: 14/24 = 58%

==========================================================================
  CHART D - elapsed_ms: how long the teardown itself takes
==========================================================================
  elapsed_ms   n=24
    p50 3ms   p90 153ms   max 232ms   min 1ms
            0-1 ms |                                             0   0.0%
            1-2 ms | ####################                        6  25.0%
            2-5 ms | ########################################   12  50.0%
           5-10 ms |                                             0   0.0%
          10-50 ms |                                             0   0.0%
         50-150 ms | ##########                                  3  12.5%
           150+ ms | ##########                                  3  12.5%
```

### Reading these

**`detect_ms` max of 6,157 ms** is a stale-onset artifact, not a 6-second
reaction: the anchor reports the age of the current voiced run, and when a caller
has been quiet a long time that age is large and meaningless. p50 and p90 are the
usable figures.

**`gw_ms` p50 of 0** is expected — 53.8% of interrupts take the `nothing_playing`
shortcut with nothing queued. It is only interesting when the queue is empty AND
the agent is audible, and §5 shows that combination did not occur.

**`elapsed_ms` p50 of 3 ms** is the teardown itself. The three cases over 150 ms
are the gateway round-trip on a real interrupt; the teardown has never been the
bottleneck, which is why the caller-side measurement was needed at all.

---

## Appendix B — The corrected reconciliation, all four calls verbatim

Unedited tool output. `1e9d7faf` ran on `f750b1f2`; the other three on
`3d0ca65d`.

```
################ 425ddbb0-307c-498a-886a-fa57488e5f52 ################
resolved: recording 425ddbb0 (dialer calls.id) -> voice session 1e9d7faf
==============================================================================
  425ddbb0-307c-498a-886a-fa57488e5f52.wav
  16000 Hz stereo · 306.8s · 20ms frames · speech at RMS >= 500
==============================================================================

channel 0: first onset   0.06s · voiced  157.0s
channel 1: first onset   0.40s · voiced   66.0s

agent-channel test — voiced in the 1s after each of 42 synthesis starts:
    channel 0:  38.2%        channel 1:  70.6%
=> AGENT = channel 1, CALLER = channel 0

anchor: first agent audio at 0.40s in the file == 14:06:39.312 in the journal
        recording t=0 == 14:06:38.912 (+/- one 20ms frame)

36 interrupt events: 6 reached the gateway, 30 took the nothing_playing shortcut
  A shortcut interrupt had no audio to stop, so it cannot have talked
  over anyone and is not reconciled here.

  id            caller     agent    heard-after   caller     logged      gateway
                onset      stopped   decision    talked-over  spch->stop  discarded
  ----------------------------------------------------------------------------
  82daa5f70789   23.36s    23.58s       123ms        220ms      1136ms       260ms
               agent audible at rms 2714; gateway discarded 260ms (13 frames) -> caller kept hearing the agent for 123ms
  af434aead17a  233.48s   233.55s         0ms         73ms       646ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  6d08744af436  251.36s   251.98s       149ms        620ms        76ms       260ms
               agent audible at rms 1417; gateway discarded 260ms (13 frames) -> caller kept hearing the agent for 149ms
  ca4ed546fde2  269.98s   270.23s         0ms        252ms      1619ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  6fab414e68b1  282.08s   283.02s         0ms        942ms       633ms       240ms
               agent leg already silent at the decision (rms 455) — nothing was playing to stop
  80253dbbc08d  299.56s   299.73s         0ms        171ms      2303ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop

  audible tail after the stop decision: min +123ms  median +149ms  max +149ms  (n=2)
  0/2 stopped within one frame of the decision.
  gw_ms is audio the gateway DISCARDED — what the caller was spared,
  not what they heard. A clean stop is a small tail WITH a non-zero gw_ms.

pre-TTS holds
   249.78s  pre_tts_hold_timeout call=1e9d7faf-2b5 waited=2.52s reason=barge_in_during_generation — speaking anyway; a caller who never yields must not mute the agent perma
   297.29s  pre_tts_hold_timeout call=1e9d7faf-2b5 waited=2.50s reason=barge_in_during_generation — speaking anyway; a caller who never yields must not mute the agent perma

NOTE  'agent audio' is the last moment the CALLER could hear the agent,
      measured from the recording — not when Python stopped sending.

################ 87917db4-82be-4b5a-a4cd-aa61410222c3 ################
resolved: recording 87917db4 (dialer calls.id) -> voice session cb4bd059
==============================================================================
  87917db4-82be-4b5a-a4cd-aa61410222c3.wav
  16000 Hz stereo · 178.9s · 20ms frames · speech at RMS >= 500
==============================================================================

channel 0: first onset   0.26s · voiced   42.9s
channel 1: first onset   0.36s · voiced   41.6s

agent-channel test — voiced in the 1s after each of 19 synthesis starts:
    channel 0:   4.4%        channel 1:  74.8%
=> AGENT = channel 1, CALLER = channel 0

anchor: first agent audio at 0.36s in the file == 11:10:41.807 in the journal
        recording t=0 == 11:10:41.447 (+/- one 20ms frame)

18 interrupt events: 8 reached the gateway, 10 took the nothing_playing shortcut
  A shortcut interrupt had no audio to stop, so it cannot have talked
  over anyone and is not reconciled here.

  id            caller     agent    heard-after   caller     logged      gateway
                onset      stopped   decision    talked-over  spch->stop  discarded
  ----------------------------------------------------------------------------
  55d44f1953a9   15.24s    15.42s       170ms        180ms       595ms       240ms
               agent audible at rms 2285; gateway discarded 240ms (12 frames) -> caller kept hearing the agent for 170ms
  5758aba104a8   33.30s    34.26s         0ms        957ms      1003ms        40ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  cc3fec484c6d   52.42s    52.62s         0ms        201ms       928ms       160ms
               agent leg already silent at the decision (rms 192) — nothing was playing to stop
  5d371bbdd87c   84.14s    85.08s         0ms        940ms       726ms       160ms
               agent leg already silent at the decision (rms 140) — nothing was playing to stop
  7d18cdc8f8b3   86.48s    86.53s         0ms         55ms      1170ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  4ea53944f619   90.86s    90.86s         0ms          1ms       983ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  9fa31ce938dc  103.52s   103.57s         0ms         46ms       732ms       260ms
               agent leg already silent at the decision (rms 226) — nothing was playing to stop
  d317711e2e42  120.26s   120.38s         0ms        124ms       608ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop

  audible tail after the stop decision: min +170ms  median +170ms  max +170ms  (n=1)
  0/1 stopped within one frame of the decision.
  gw_ms is audio the gateway DISCARDED — what the caller was spared,
  not what they heard. A clean stop is a small tail WITH a non-zero gw_ms.

NOTE  'agent audio' is the last moment the CALLER could hear the agent,
      measured from the recording — not when Python stopped sending.

################ 5c82cf82-36e8-4ecf-a19b-ae0d2002cb39 ################
resolved: recording 5c82cf82 (dialer calls.id) -> voice session 58fcbcb5
==============================================================================
  5c82cf82-36e8-4ecf-a19b-ae0d2002cb39.wav
  16000 Hz stereo · 140.9s · 20ms frames · speech at RMS >= 500
==============================================================================

channel 0: first onset   0.12s · voiced   36.7s
channel 1: first onset   0.46s · voiced   36.9s

agent-channel test — voiced in the 1s after each of 17 synthesis starts:
    channel 0:   8.1%        channel 1:  76.1%
=> AGENT = channel 1, CALLER = channel 0

anchor: first agent audio at 0.46s in the file == 11:16:02.237 in the journal
        recording t=0 == 11:16:01.777 (+/- one 20ms frame)

13 interrupt events: 4 reached the gateway, 9 took the nothing_playing shortcut
  A shortcut interrupt had no audio to stop, so it cannot have talked
  over anyone and is not reconciled here.

  id            caller     agent    heard-after   caller     logged      gateway
                onset      stopped   decision    talked-over  spch->stop  discarded
  ----------------------------------------------------------------------------
  f299effcd690   12.92s    13.02s        45ms        100ms      1058ms       260ms
               agent audible at rms 4164; gateway discarded 260ms (13 frames) -> STOPPED CLEANLY
  73d9dee77ea1   29.48s    29.84s        48ms        360ms       555ms       240ms
               agent audible at rms 723; gateway discarded 240ms (12 frames) -> STOPPED CLEANLY
  6475376e8b3d   52.86s    53.05s         0ms        186ms       651ms         0ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop
  103da1b89793   80.90s    81.06s        17ms        160ms       607ms       220ms
               agent audible at rms 566; gateway discarded 220ms (11 frames) -> STOPPED CLEANLY

  audible tail after the stop decision: min +17ms  median +45ms  max +48ms  (n=3)
  3/3 stopped within one frame of the decision.
  gw_ms is audio the gateway DISCARDED — what the caller was spared,
  not what they heard. A clean stop is a small tail WITH a non-zero gw_ms.

NOTE  'agent audio' is the last moment the CALLER could hear the agent,
      measured from the recording — not when Python stopped sending.

################ b1611909-56a9-439d-b96a-9597ca04d276 ################
resolved: recording b1611909 (dialer calls.id) -> voice session 74b976e6
==============================================================================
  b1611909-56a9-439d-b96a-9597ca04d276.wav
  16000 Hz stereo · 63.3s · 20ms frames · speech at RMS >= 500
==============================================================================

channel 0: first onset   0.06s · voiced   14.4s
channel 1: first onset   0.48s · voiced   13.5s

agent-channel test — voiced in the 1s after each of 6 synthesis starts:
    channel 0:  36.7%        channel 1:  51.7%
=> AGENT = channel 1, CALLER = channel 0

anchor: first agent audio at 0.48s in the file == 11:06:04.018 in the journal
        recording t=0 == 11:06:03.538 (+/- one 20ms frame)

2 interrupt events: 1 reached the gateway, 1 took the nothing_playing shortcut
  A shortcut interrupt had no audio to stop, so it cannot have talked
  over anyone and is not reconciled here.

  id            caller     agent    heard-after   caller     logged      gateway
                onset      stopped   decision    talked-over  spch->stop  discarded
  ----------------------------------------------------------------------------
  679d06793e5e   15.32s    15.49s         0ms        174ms       568ms       160ms
               agent leg already silent at the decision (rms 0) — nothing was playing to stop

NOTE  'agent audio' is the last moment the CALLER could hear the agent,
      measured from the recording — not when Python stopped sending.

```
---

## Appendix C — Commands, so you can reproduce all of it

**The channel test — run this before trusting any waveform number:**

```bash
/opt/talky/backend/venv/bin/python scripts/barge_in_waveform_reconcile.py \
    /opt/talky/backend/recordings/<dialer-calls-id>.wav --since "2026-08-19 20:27"
# prints:  channel 0: 4.4%   channel 1: 74.8%  => AGENT = channel 1
```

**Map a recording to its voice session** (different UUIDs — this costs an hour
if you don't know it):

```bash
journalctl -u talky-api --no-pager --since "2026-08-19 20:27" \
  | grep bind_telephony_call
# bind_telephony_call voice_session=cb4bd059 -> calls.id=87917db4 pbx=talky-out-20
```

**Pre-TTS hold outcomes — the three-way split that now matters:**

```bash
J='journalctl -u talky-api --no-pager --since "2026-08-19 20:27"'
eval $J | grep -c barge_in_ignored_final_pre_tts   # armed
eval $J | grep -c pre_tts_hold_not_needed          # clear won the race
eval $J | grep -c pre_tts_hold_released            # hold waited, caller stopped
eval $J | grep -c pre_tts_hold_timeout             # caller never yielded
eval $J | grep -oE 'released_by=[a-z_]+' | sort | uniq -c
```

**Interrupt outcomes — most never reach the gateway:**

```bash
eval $J | grep -c 'interrupt_step=begin'
eval $J | grep -c 'interrupt_step=nothing_playing'
eval $J | grep -c 'interrupt_complete'
```

**Unanswered caller turns.** Note the matcher — the message is separated from the
logger name by `[req=]` and `[call=]`, which is what broke the report 7 figure:

```bash
eval $J | grep -c 'Flux EndOfTurn'
eval $J | grep -cE 'voice_pipeline\.turn_ender\].*turn_end$'
```

**Failover rate per DISTINCT call, not per log line:**

```bash
eval $J | grep resilient_stt_failed_over_to \
  | grep -oE '\[call=[0-9a-f]{8}' | sort -u | wc -l
```

**The controlled silent-Flux fault:**

```bash
python -m pytest tests/unit/test_stt_failover_no_loss_no_duplication.py -q
```

**Verify inside the running process, not on disk:**

```bash
cd /opt/talky/backend && venv/bin/python -c "
import inspect
from app.domain.services.voice_pipeline import playback_gate as pg
print('not_needed log =', 'pre_tts_hold_not_needed' in inspect.getsource(pg.await_caller_pause))
print('quiet release  =', pg._QUIET_RELEASE_S)
"
```

**Gate without touching prod:**

```bash
git -C /opt/talky worktree add --detach /tmp/tw HEAD
# scp modified files in, then:
cd /tmp/tw/backend && /opt/talky/backend/venv/bin/python -m pytest tests/unit -q \
    -p no:cacheprovider --ignore=tests/unit/test_dialer_redis_reliability.py
git -C /opt/talky worktree remove /tmp/tw --force
```

---

## Appendix D — Config and kill switches

| Variable | Default | Effect |
|---|---|---|
| `VOICE_PRE_TTS_QUIET_RELEASE_S` | `0.7` | Acoustic-quiet release window for the pre-TTS hold. **`0` disables it**, leaving only the EndOfTurn exit and the 2.5 s cap. |
| `VOICE_ONSET_RMS` | `500` | Frame RMS above which audio counts as the caller's voice. Shared with `resilient_stt._SPEECH_RMS_THRESHOLD` and the waveform tool, so one number moves them all. |
| `VOICE_ONSET_GAP_S` | `0.4` | Pause that ends a run of speech. Feeds `detect_ms`. |
| `VOICE_GATEWAY_DRAIN_S` | `0.6` | Window after Python's last chunk in which an interrupt still asks the gateway. `0` restores the pre-`f750b1f2` shortcut. |
| `VOICE_STT_ECHO_TAIL_S` | `0.25` | Tail after the agent stops during which audio is still treated as possible echo, for the STT watchdog. |
| `STT_FAILOVER_ENABLED` | `true` | Master switch for Flux → Nova promotion. |
| `VOICE_STT_FAULT_SILENT_CAMPAIGN` | unset | Campaign-scoped silent-Flux fault injection. Requires `_UNTIL`; fails closed. |

Hard-coded, deliberately:

| Constant | Value | Why not configurable |
|---|---|---|
| `_MAX_HOLD_S` | `2.5` | The fail-open cap. Changing it changes how long a caller can mute the agent — a code review, not a config change. |
| `_POLL_S` | `0.02` | One PCMU frame. The hold cannot add more than a single frame of latency beyond the caller stopping. |
| `audio_buffer_ms` | `500` | Replay buffer. Pinned by test in both directions; enlarging it should be deliberate. |
| `silent_stream_voiced_seconds` | `6.0` | Watchdog threshold. Chosen against two observed dead calls (17 s and 19 s wasted) and the re-greet ladder. |

**Rollback.** `git checkout 3d0ca65d` on the server and restart the four Python
services. The C++ gateway is unchanged by this deploy.

---

## Appendix E — Forensic timelines: the six measured interrupts

Every interrupt across the four calls where the agent was **genuinely audible**
at the moment we decided to stop — the only ones that can talk over anyone. Each
block pairs the raw journal window with what the recording independently says.

Two fields to read alongside each other:

* `tts_active=True` on the `begin` line — Python was still streaming when the
  caller spoke, so this was a real barge-in and not an ordinary turn.
* `dropped_frames` / `dropped_ms` on `cpp_interrupt` — audio the gateway held and
  threw away. **The caller never heard it.** This is what the drain window from
  `f750b1f2` is for, and it is the reason the audible tail is short.

`drain_ms=-` means the drain-window probe did not apply: playback was genuinely
active, so the interrupt took the ordinary path rather than the probe.

```
==============================================================================
  INTERRUPT f299effcd690   call=58fcbcb5
  recording says: caller stopped hearing the agent 45ms after the
                  decision; gateway discarded 260ms; agent leg at rms 4164
==============================================================================
  11:16:14 INFO     [deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
  11:16:14 INFO     [tts_playback] Barge-in (post-send) interrupted TTS for call 58fcbcb5-07d5-486e-8ba4-38952e66c3fd
  11:16:14 INFO     [voice_pipeline_service] barge_in_detected
  11:16:14 INFO     [interrupt] interrupt_step=begin interrupt_id=f299effcd690 call=58fcbcb5-07d reason=barge_in tts_active=True drain_ms=- <<<
  11:16:14 INFO     [interrupt] interrupt_step=state_listening interrupt_id=f299effcd690 call=58fcbcb5-07d <<<
  11:16:14 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=f299effcd690 call=58fcbcb5-07d cancelled=True <<<
  11:16:14 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  11:16:14 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=f299effcd690 call=58fcbcb5-07d local_bytes=0 pending_bytes=0 <<<
  11:16:14 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=f299effcd690 call=58fcbcb5-07d ok=True dropped_frames=13 dropped_ms=260 segmen <<<
  11:16:14 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=f299effcd690 call=58fcbcb5-07d <<<
  11:16:14 INFO     [interrupt] interrupt_complete {'interrupt_id': 'f299effcd690', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<

==============================================================================
  INTERRUPT 73d9dee77ea1   call=58fcbcb5
  recording says: caller stopped hearing the agent 48ms after the
                  decision; gateway discarded 240ms; agent leg at rms 723
==============================================================================
  11:16:31 INFO     [deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
  11:16:31 INFO     [tts_playback] Barge-in (post-send) interrupted TTS for call 58fcbcb5-07d5-486e-8ba4-38952e66c3fd
  11:16:31 INFO     [voice_pipeline_service] barge_in_detected
  11:16:31 INFO     [interrupt] interrupt_step=begin interrupt_id=73d9dee77ea1 call=58fcbcb5-07d reason=barge_in tts_active=True drain_ms=- <<<
  11:16:31 INFO     [interrupt] interrupt_step=state_listening interrupt_id=73d9dee77ea1 call=58fcbcb5-07d <<<
  11:16:31 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=73d9dee77ea1 call=58fcbcb5-07d cancelled=True <<<
  11:16:31 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  11:16:31 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=73d9dee77ea1 call=58fcbcb5-07d local_bytes=0 pending_bytes=0 <<<
  11:16:31 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=73d9dee77ea1 call=58fcbcb5-07d ok=True dropped_frames=12 dropped_ms=240 segmen <<<
  11:16:31 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=73d9dee77ea1 call=58fcbcb5-07d <<<
  11:16:31 INFO     [interrupt] interrupt_complete {'interrupt_id': '73d9dee77ea1', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<
  11:16:31 WARNING  [telephony_media_gateway] telephony_audio_gap call_id=58fcbcb5-07d5-486e-8ba4-38952e66c3fd gap_ms=184 expected_ms=40 (4.6x) total_ga

==============================================================================
  INTERRUPT 103da1b89793   call=58fcbcb5
  recording says: caller stopped hearing the agent 17ms after the
                  decision; gateway discarded 220ms; agent leg at rms 566
==============================================================================
  11:17:22 INFO     [deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
  11:17:22 INFO     [tts_playback] Barge-in (post-send) interrupted TTS for call 58fcbcb5-07d5-486e-8ba4-38952e66c3fd
  11:17:22 INFO     [voice_pipeline_service] barge_in_detected
  11:17:22 INFO     [interrupt] interrupt_step=begin interrupt_id=103da1b89793 call=58fcbcb5-07d reason=barge_in tts_active=True drain_ms=- <<<
  11:17:22 INFO     [interrupt] interrupt_step=state_listening interrupt_id=103da1b89793 call=58fcbcb5-07d <<<
  11:17:22 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=103da1b89793 call=58fcbcb5-07d cancelled=True <<<
  11:17:22 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  11:17:22 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=103da1b89793 call=58fcbcb5-07d local_bytes=0 pending_bytes=0 <<<
  11:17:22 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=103da1b89793 call=58fcbcb5-07d ok=True dropped_frames=11 dropped_ms=220 segmen <<<
  11:17:22 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=103da1b89793 call=58fcbcb5-07d <<<
  11:17:22 INFO     [interrupt] interrupt_complete {'interrupt_id': '103da1b89793', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<

==============================================================================
  INTERRUPT 55d44f1953a9   call=cb4bd059
  recording says: caller stopped hearing the agent 170ms after the
                  decision; gateway discarded 240ms; agent leg at rms 2285
==============================================================================
  11:10:56 INFO     [deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
  11:10:56 INFO     [tts_playback] Barge-in (post-send) interrupted TTS for call cb4bd059-b429-40ef-93af-7189b1522ec7
  11:10:56 INFO     [voice_pipeline_service] barge_in_detected
  11:10:56 INFO     [interrupt] interrupt_step=begin interrupt_id=55d44f1953a9 call=cb4bd059-b42 reason=barge_in tts_active=True drain_ms=- <<<
  11:10:56 INFO     [interrupt] interrupt_step=state_listening interrupt_id=55d44f1953a9 call=cb4bd059-b42 <<<
  11:10:56 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=55d44f1953a9 call=cb4bd059-b42 cancelled=True <<<
  11:10:56 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  11:10:56 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=55d44f1953a9 call=cb4bd059-b42 local_bytes=0 pending_bytes=0 <<<
  11:10:56 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=55d44f1953a9 call=cb4bd059-b42 ok=True dropped_frames=12 dropped_ms=240 segmen <<<
  11:10:56 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=55d44f1953a9 call=cb4bd059-b42 <<<
  11:10:56 INFO     [interrupt] interrupt_complete {'interrupt_id': '55d44f1953a9', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<

==============================================================================
  INTERRUPT 82daa5f70789   call=1e9d7faf
  recording says: caller stopped hearing the agent 123ms after the
                  decision; gateway discarded 260ms; agent leg at rms 2714
==============================================================================
  14:07:02 INFO     [deepgram_flux] Flux StartOfTurn - User started speaking, barge-in detected
  14:07:02 INFO     [tts_playback] Barge-in (post-send) interrupted TTS for call 1e9d7faf-2b5b-46a1-accd-955f1f402c45
  14:07:02 INFO     [voice_pipeline_service] barge_in_detected
  14:07:02 INFO     [interrupt] interrupt_step=begin interrupt_id=82daa5f70789 call=1e9d7faf-2b5 reason=barge_in tts_active=True drain_ms=- <<<
  14:07:02 INFO     [interrupt] interrupt_step=state_listening interrupt_id=82daa5f70789 call=1e9d7faf-2b5 <<<
  14:07:02 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=82daa5f70789 call=1e9d7faf-2b5 cancelled=True <<<
  14:07:02 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  14:07:02 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=82daa5f70789 call=1e9d7faf-2b5 local_bytes=0 pending_bytes=0 <<<
  14:07:02 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=82daa5f70789 call=1e9d7faf-2b5 ok=True dropped_frames=13 dropped_ms=260 segmen <<<
  14:07:02 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=82daa5f70789 call=1e9d7faf-2b5 <<<
  14:07:02 INFO     [interrupt] interrupt_complete {'interrupt_id': '82daa5f70789', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<

==============================================================================
  INTERRUPT 6d08744af436   call=1e9d7faf
  recording says: caller stopped hearing the agent 149ms after the
                  decision; gateway discarded 260ms; agent leg at rms 1417
==============================================================================
  14:10:50 INFO     [voice_pipeline_service] barge_in_detected
  14:10:50 INFO     [interrupt] interrupt_step=begin interrupt_id=6d08744af436 call=1e9d7faf-2b5 reason=barge_in tts_active=True drain_ms=- <<<
  14:10:50 INFO     [interrupt] interrupt_step=state_listening interrupt_id=6d08744af436 call=1e9d7faf-2b5 <<<
  14:10:50 INFO     [interrupt] interrupt_step=task_cancelled interrupt_id=6d08744af436 call=1e9d7faf-2b5 cancelled=True <<<
  14:10:50 INFO     [groq] llm_usage model=qwen/qwen3.6-27b partial=True prompt_tokens=0 cached_tokens=0 cache_hit_ratio=0.00 completion_tokens=0 req_id
  14:10:50 INFO     [interrupt] interrupt_step=buffers_cleared interrupt_id=6d08744af436 call=1e9d7faf-2b5 local_bytes=0 pending_bytes=0 <<<
  14:10:50 INFO     [interrupt] interrupt_step=cpp_interrupt interrupt_id=6d08744af436 call=1e9d7faf-2b5 ok=True dropped_frames=13 dropped_ms=260 segmen <<<
  14:10:50 INFO     [interrupt] interrupt_step=tts_provider_cleared interrupt_id=6d08744af436 call=1e9d7faf-2b5 <<<
  14:10:50 INFO     [interrupt] interrupt_complete {'interrupt_id': '6d08744af436', 'ok': True, 'deduped': False, 'task_cancelled': True, 'local_bytes': <<<

```

### What these six establish together

```
  interrupt      heard-after   gw discarded   agent rms   verdict
  f299effcd690        45ms         260ms         4164     STOPPED CLEANLY
  73d9dee77ea1        48ms         240ms          723     STOPPED CLEANLY
  103da1b89793        17ms         220ms          566     STOPPED CLEANLY
  55d44f1953a9       170ms         240ms         2285
  82daa5f70789       123ms         260ms         2714
  6d08744af436       149ms         260ms         1417
  ------------------------------------------------------------------
  n=6   min 17ms   median 85ms   max 170ms
```

In five of six the gateway discarded 220-260ms of queued audio that the caller
was therefore spared. The teardown itself runs in 1-3ms (Appendix A, chart D);
the residual 17-170ms is the gateway pacing its final frames plus one frame of
alignment error in the measurement.

This is the claim report 8 could not make and this one can: **not that our code
returned quickly, but that the caller stopped hearing us.**
