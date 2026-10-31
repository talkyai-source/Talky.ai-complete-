# Report 8 — The flag that was only ever lowered on the happy path

**Prod HEAD `3d0ca65d`** · deployed 2026-08-19 20:27:46 UTC · 5 services active ·
`{"ready":true,"db":"ok","redis":"ok"}` · zero post-restart warnings ·
gate 4,837 passed / 8 failed / 5 errors = the pristine baseline exactly.

Rollback: `f750b1f2`.

---

## Contents

| § | |
|---|---|
| 1 | What you asked for, and what I actually found |
| 2 | The signature: 16 timeouts, 0 releases, four days, no exceptions |
| 3 | Anatomy: the twelve exits between EndOfTurn and the clear |
| 4 | The forensics: classifying all sixteen |
| 5 | Mechanism (b) — the thirteen holds with no EndOfTurn at all |
| 6 | What the research says about the 700 ms window |
| 7 | Fix, part A — clear where the fact becomes true |
| 8 | Fix, part B — when nothing is coming, ask the audio |
| 9 | Failing safe: six ways this could have talked over people |
| 10 | The tests: twelve paths on a real `CallSession` |
| 11 | Proving the tests fail before the fix |
| 12 | The gate, the deploy, and verification inside the process |
| 13 | **Correction 1** — the "36 unanswered turns / 15.5%" claim was wrong |
| 14 | **Correction 2** — "36% → 9% failover" was a small-sample artifact |
| 15 | The waveform reconciler |
| 16 | Two measurement bugs I caught before reporting them |
| 17 | What the recording actually shows |
| 18 | **RETRACTED** — the "audible tails" finding was a channel-mapping error |
| 19 | Pre-mortem |
| 20 | Post-mortem |
| 21 | Open items, and what your next call settles |
| A | Charts — every measured figure |
| B | Commands, so you can reproduce all of it |
| C | Config and kill switches |
| D | Forensic timelines — all sixteen timeouts |

---

## 1. What you asked for, and what I actually found

Your brief:

> Please fix the `_caller_speaking` leak next. Clear the flag immediately when
> EndOfTurn is received, before any duplicate, backchannel or transcript filter
> can return. Test every early-return path using a real CallSession. This is
> currently causing 36 unanswered turns and the 2.5-second hold timeout. After
> the fix, prove that `pre_tts_hold_released` occurs and that unanswered-turn
> rate drops. Also test `f750b1f2` with one recorded call. Match the recording
> waveform with the gateway logs around a deliberate interruption so we can
> reconcile when Python finishes sending audio versus when the caller actually
> stops hearing it.

Three things in that brief turned out differently once measured.

**The leak is worse than eight early returns — it is twelve**, and four of them
sit in `transcript_handler`, before `turn_ender` is ever scheduled. My report 7
described only the eight inside `turn_ender`.

**Clearing on EndOfTurn fixes the minority of cases.** Anchored properly on the
moment each hold was armed, only **3 of 16** timeouts had an EndOfTurn that a
filter swallowed. **13 of 16 had no EndOfTurn at all** — nothing to clear,
because Flux never emitted one. The requested fix cannot touch those thirteen.
So I built a second exit for them.

**The "36 unanswered turns" figure was mine and it was wrong.** The real rate is
**9 of 853 caller turns, 1.1%**, over four days. §13 explains the measurement bug
that produced 15.5%.

A fourth correction surfaced while checking for regressions: report 7's headline
**"STT failover 36% → 9%" was a small-sample artifact.** Over a proper denominator
it is **43% → 21%**. §14.

---

## 2. The signature: 16 timeouts, 0 releases, four days, no exceptions

The pre-TTS hold exists to stop the agent starting a sentence on top of a caller
who is still mid-sentence. When it is armed, it waits for the caller to stop,
then speaks. It is capped at 2.5 s and fails open — talking over someone is bad,
never speaking again is worse.

Here is every hold that has ever run:

```
  day           timeout  released
  2026-08-13          2         0
  2026-08-17          4         0
  2026-08-18          6         0
  2026-08-19          4         0
  TOTAL              16         0

  release rate: 0/16 = 0.0%
```

Not one hold, on any day, ever ended because the caller stopped talking. Every
single one ran to the cap and spoke anyway:

```
  n=16   min 2.50s   max 2.66s   cap is 2.50s
      2.50s | #########################                      4
      2.51s | ############################################   7
      2.52s | #########################                      4
      2.66s | ######                                         1
```

A guard whose success path has fired zero times in its entire production life is
not a guard. It is a 2.5-second delay with a comment explaining what it was
supposed to do.

That is the whole finding in one number: **0 of 16**. Everything below is the
explanation.

---

## 3. Anatomy: the twelve exits between EndOfTurn and the clear

`_caller_speaking` is raised in exactly one place — `audio_ingest.
_on_barge_in_direct`, on every StartOfTurn that passes the echo gate:

```python
# audio_ingest.py — _on_barge_in_direct
from app.domain.services.voice_pipeline.playback_gate import mark_caller_speaking
mark_caller_speaking(session)
```

It was lowered in exactly one place — `turn_ender.py:456`, immediately after the
`turn_end` log:

```python
# turn_ender.py:449
# The caller has yielded the floor. Releases any playback held by
# playback_gate.await_caller_pause ...
from app.domain.services.voice_pipeline.playback_gate import mark_caller_stopped
mark_caller_stopped(session)
```

Between the EndOfTurn arriving and that line executing there are **twelve
returns**. Four in `transcript_handler.handle`:

| # | Exit | Line |
|---|------|------|
| 1 | backchannel during agent speech | `transcript_handler.py:144` |
| 2 | suppressed empty EOT marker | `transcript_handler.py:167` |
| 3 | duplicate EndOfTurn / eager promotion | `transcript_handler.py:233` |
| 4 | queued behind a pending turn (F-08) | `transcript_handler.py:250` |

and eight in `turn_ender.handle`:

| # | Exit | Line |
|---|------|------|
| 5 | empty transcript | `turn_ender.py:132` |
| 6 | turn-0 transcript rejected | `turn_ender.py:197` |
| 7 | instant opener took the turn | `turn_ender.py:211` |
| 8 | repetitive STT hallucination | `turn_ender.py:227` |
| 9 | backchannel suppressed | `turn_ender.py:316` |
| 10 | turn skipped, pending task | `turn_ender.py:366` |
| 11 | turn skipped, llm busy | `turn_ender.py:385` |
| 12 | turn skipped, self-echo | `turn_ender.py:427` |

Every one of them is individually correct. Each was added to fix a real
production defect, and several are load-bearing — path 4 is the F-08 fix that
stops a second utterance being dropped while turn 1 is thinking; path 12 stops
the agent answering its own echo. None of them should be removed.

That is precisely what made this hard to see. There is no bug in any single
exit. The bug is that the clear was placed at the end of a corridor with twelve
doors out of it.

How often are those doors actually used? Measured over four days:

```
  1  backchannel during agent speech   (transcript_handler) | ####################   33
  2  suppressed empty EOT marker       (transcript_handler) |                         0
  3  duplicate EndOfTurn               (transcript_handler) |                         0
  4  queued behind pending turn        (transcript_handler) | ###                     5
  6  turn-0 transcript rejected        (turn_ender)         |                         0
  7  instant opener                    (turn_ender)         | #####                   8
  8  repetitive hallucination          (turn_ender)         |                         0
  9  backchannel suppressed            (turn_ender)         | ###                     5
  10 turn skipped, pending task        (turn_ender)         |                         0
  11 turn skipped, llm busy            (turn_ender)         |                         0
  12 turn skipped, self echo           (turn_ender)         |                         0
```

51 uses of a leaking exit in four days, dominated by path 1. Paths 10 and 11 fire
zero times — and there is a reason, which the tests had to be rewritten to
respect (§10).

---

## 4. The forensics: classifying all sixteen

The classification question is precise: **between the moment a hold was armed and
the moment it timed out, did an EndOfTurn arrive?**

If yes, the EndOfTurn existed and a filter swallowed it before the clear —
mechanism (a), and clearing at the marker fixes it.

If no, Flux never emitted one. There was nothing to clear — mechanism (b), and
clearing at the marker cannot help, because the marker never came.

The arm is visible in the journal as `barge_in_ignored_final_pre_tts`, logged
where `handle_barge_in` decides to protect an in-flight final answer rather than
cancel it. That is the exact call site that arms the hold.

Anchored on that arm, across all sixteen:

```
  (a) EndOfTurn arrived but a filter swallowed it :  3
  (b) no EndOfTurn ever arrived                   : 13
  total                                           : 16
```

A worked example of each. Mechanism (a), call `b3350aee`:

```
  21:04:06  EndOfTurn      'Before we finish, summarize the issues I reported, a'
  21:04:06  turn_end       -> mark_caller_stopped(): flag lowered
  21:04:15  EndOfTurn      'That will be all. Thank you.'
  21:04:15  turn_end       -> mark_caller_stopped(): flag lowered
  21:04:23  EndOfTurn      'Nothing. Thank you.'
  21:04:23  turn_end       -> mark_caller_stopped(): flag lowered
  21:04:23  EndOfTurn      'Goodbye.'
> 21:04:23  EARLY RETURN   turn_queued_behind_pending  (path 4)
  21:04:26  HOLD TIMEOUT   waited=2.50s
```

The caller said "Goodbye." Flux delivered the EndOfTurn. It was queued behind the
still-running turn — correctly, that is the F-08 fix — and `turn_ender.handle`
was never called for it, so line 456 never ran. Three seconds later the hold
burned its cap. The caller had finished speaking before the hold even started.

Mechanism (b), call `06a6f8c9`, and this is the one that changes the design:

```
  19:58:54  turn_end       -> mark_caller_stopped(): flag lowered
> 19:58:54  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
  19:58:55  audio_level
  19:58:56  audio_level
  19:58:57  HOLD TIMEOUT   waited=2.51s
```

The previous turn cleared the flag correctly at 19:58:54. In the same second a
StartOfTurn raised it again and armed the hold. Then **no EndOfTurn ever
arrived**. Not late — never. Nothing in the system was ever going to lower that
flag again.

Note the earlier figure in my last message was 5/9 rather than 3/13. That used a
looser rule — any EndOfTurn within four seconds of the timeout, not anchored on
the arm — which counted EndOfTurns belonging to the *previous* turn. The
arm-anchored classification above is the correct one and it makes mechanism (b)
even more dominant: **81% of all timeouts.**

---

## 5. Mechanism (b) — the thirteen holds with no EndOfTurn at all

Why would a StartOfTurn have no matching EndOfTurn? Because the two events do
not mean symmetric things.

Flux raises **StartOfTurn** on any speech-like sound — a cough, a chair, a word
of a background conversation, a syllable the caller aborts, the carrier's own
line noise crossing threshold. It is deliberately eager, because barge-in has to
be fast.

Flux raises **EndOfTurn** only when it believes a *turn* has ended — a semantic
judgement about a complete utterance. Sounds that were never a turn never get
one.

So StartOfTurn and EndOfTurn are not a matched pair, and any design that lowers a
flag *only* on EndOfTurn will leak every time the caller makes a noise that
isn't a turn. Over four days that was 13 of 16 holds.

This is a design-level mismatch, not a coding slip, and it is why the fix has two
parts. Part A makes the EndOfTurn path airtight. Part B accepts that for most
holds no EndOfTurn is coming and finds another witness.

The only remaining witness is the audio itself. And there is a genuinely clean
window in which to read it: **the hold runs before `session.tts_active = True`**.

```python
# tts_playback.py:91
from app.domain.services.voice_pipeline.playback_gate import await_caller_pause
await await_caller_pause(session, call_id=call_id)

# Mark TTS as active here so handle_turn_end skips if a greeting
# or a previous turn is already speaking.
session.tts_active = True
```

During the hold the agent is silent by construction. On a 2-wire PSTN line
without client-side AEC, the agent's own voice echoing back at RMS 700–4200 is
normally the thing that poisons any acoustic measurement — that is exactly what
broke the STT watchdog and required the echo guard. Here it cannot: there is no
agent audio to echo. Whatever is on the line during a pre-TTS hold is the caller.

---

## 6. What the research says about the 700 ms window

Before picking a number I checked it against both the voice-agent industry and
the conversation-analysis literature, because a threshold like this is exactly
where a plausible-sounding guess does damage.

**Industry guidance on endpointing thresholds** converges on a band. Setting the
silence threshold as short as 200 ms cuts users off during natural pauses and
hesitations; setting it to 800 ms or more makes the agent feel laggy, "like a
lagged phone call". The recommended practice is to tune for the caller-visible
mistake rather than the knob, and increasingly to use semantic endpointing —
judging whether a *thought* is complete — rather than silence alone.
([Cekura](https://www.cekura.ai/blogs/endpointing-in-voice-ai-turn-detection),
[AssemblyAI](https://www.assemblyai.com/blog/turn-detection-endpointing-voice-agent),
[LiveKit](https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection),
[Decagon](https://decagon.ai/glossary/what-is-endpointing))

**Conversation analysis** gives the human baseline. Median inter-speaker gaps in
Dutch, English and Swedish corpora run **110–130 ms**, with the mode around
**200 ms**. A gap under about **120 ms** is not perceived as a gap at all, and
perceptual "no gap" is estimated at **150–250 ms**. Because producing a spoken
word takes ~600 ms of planning, listeners routinely begin formulating their reply
*before* the speaker finishes.
([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0010027722000257),
[Frontiers in Psychology](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2015.00731/full))

So 700 ms is roughly **3–6× a natural human turn gap**. If this were the primary
turn-detection mechanism it would be far too slow and the agent would feel
sluggish.

It is not the primary mechanism, and that distinction is the whole justification.
Flux's semantic endpointer remains in charge of turn-taking and is untouched. The
700 ms window only decides **how long a hold that nothing else will ever release
should keep waiting**. Its competitor is not "the ideal response latency" — its
competitor is the **2,500 ms cap that currently fires 100% of the time**.

Against that baseline, 700 ms is a 1.8-second improvement in the worst case, and
it is deliberately set at the conservative end of the industry band so it cannot
start clipping callers mid-pause. If measurement later shows it is still too
eager or too slow, it is one environment variable.

---

## 7. Fix, part A — clear where the fact becomes true

The clear moves to the moment the provider says the turn ended, before any filter
can return.

First, which chunk *is* the EndOfTurn? Flux emits **two** chunks per turn — the
text, then an empty control marker:

```python
# deepgram_flux.py:891
elif event == "EndOfTurn":
    if transcript_text and transcript_text.strip():
        chunk = TranscriptChunk(text=transcript_text.strip(), is_final=True, ...)
        await transcript_queue.put(chunk)
    # Signal end of turn
    end_chunk = TranscriptChunk(text="", is_final=True, ...)
    await transcript_queue.put(end_chunk)
```

and `detect_turn_end` is true only for the second:

```python
# stt_provider.py:43 — the provider-agnostic default
def detect_turn_end(self, transcript_chunk) -> bool:
    return transcript_chunk.is_final and not transcript_chunk.text
```

That matters for two reasons. It is the *marker*, not the text chunk, that must
trigger the clear — clearing on a text chunk would release the hold while the
caller is still talking, which is the talk-over the gate exists to prevent. And
because this default lives on the base `STTProvider`, it is provider-agnostic:
the resilient wrapper inherits it, so the clear works identically after a
failover to Nova.

The change, at the top of `transcript_handler.handle`, before the backchannel
guard and before the empty-marker suppression:

```python
# Computed once and reused at the dispatch branch below: detect_turn_end
# is pure (`is_final and not text`), but reading it twice invites the two
# reads drifting apart later.
try:
    _is_turn_end = bool(self._p.stt_provider.detect_turn_end(transcript))
except Exception:  # noqa: BLE001 — an odd chunk must not kill the turn
    _is_turn_end = False
if _is_turn_end:
    from app.domain.services.voice_pipeline.playback_gate import (
        mark_caller_stopped,
    )
    mark_caller_stopped(session)
```

and the dispatch branch 80 lines lower now reuses the value rather than asking
again:

```python
-       if self._p.stt_provider.detect_turn_end(transcript):
+       if _is_turn_end:
```

Three deliberate choices:

**The clear in `turn_ender` stays.** It is not redundant. The queued-turn
dispatch at `turn_ender.py:921` re-enters `handle_turn_end` *without* a fresh
EndOfTurn arriving, so the second clear is the only one that covers that path.
Defence in depth, with a distinct job.

**It is wrapped in `try`.** `detect_turn_end` is on the hot path, called for every
transcript chunk on every call. A provider or test double that throws must cost
the clear, not the call.

**It is computed once.** Two independent reads of the same predicate in one
function is how they drift apart six months later.

---

## 8. Fix, part B — when nothing is coming, ask the audio

For the thirteen holds with no EndOfTurn, the acoustic signal.

The stamp already existed and was already being written on every voiced frame —
`note_voice_activity`, on the hot ingest path:

```python
# audio_ingest.py:48
def note_voice_activity(session, sum_sq: float, sample_count: int) -> None:
    if sample_count <= 0:
        return
    try:
        if (sum_sq / sample_count) ** 0.5 < _VOICE_ONSET_RMS:
            return
        now = time.monotonic()
        last = getattr(session, "_caller_voice_last_at", None)
        if last is None or (now - last) > _VOICE_ONSET_GAP_S:
            session._caller_voice_onset_at = now
        session._caller_voice_last_at = now
    except Exception:
        pass
```

Called unconditionally at `audio_ingest.py:360` for every 16-bit PCM frame.
`_VOICE_ONSET_RMS` is 500 — the same threshold `resilient_stt` uses, so one
number moves the whole audio path.

**This is the point where three guards died this month**, so it gets checked
explicitly: does this signal actually vary in production? Yes, provably. It is
the same writer whose sibling field `_caller_voice_onset_at` produced 183
`detect_ms` samples spanning 19 ms to 34 s in this window. A constant does not
produce a distribution.

The reader:

```python
def _caller_quiet_for_s(session) -> Optional[float]:
    """Seconds since the caller's last voiced frame, or None if unmeasurable.

    None means "no acoustic evidence" and MUST be read as *no opinion* — the
    hold then behaves exactly as it did before this signal existed. Never
    substitute 0.0 (which reads as "speaking right now") and never substitute a
    large number (which would release the hold on missing data — the failure
    mode that actually talks over people). Browser / ask-AI sessions never run
    the telephony ingest loop, so None is their normal state, not an error.
    """
    last = getattr(session, "_caller_voice_last_at", None)
    if last is None:
        return None
    try:
        age = time.monotonic() - float(last)
    except (TypeError, ValueError):
        return None
    return age if age >= 0.0 else None
```

and the hold gains a second exit:

```python
while caller_is_speaking(session):
    waited = time.monotonic() - started
    if waited >= _MAX_HOLD_S:
        logger.warning("pre_tts_hold_timeout ... quiet_ms=%s ...", ...)
        return waited
    # No EndOfTurn is coming for 13 of every 16 holds. Ask the audio.
    quiet = _caller_quiet_for_s(session)
    if _QUIET_RELEASE_S > 0.0 and quiet is not None and quiet >= _QUIET_RELEASE_S:
        released_by = "acoustic_quiet"
        quiet_at_release = quiet
        break
    await asyncio.sleep(_POLL_S)
```

**Every release now names the exit that fired.** This is the single most
important line in the change, and it is there because of the pattern in §20:

```python
logger.info(
    "pre_tts_hold_released call=%s waited_ms=%.0f reason=%s released_by=%s "
    "quiet_ms=%s — caller finished; starting playback without talking over "
    "them", ...
)
```

`released_by=end_of_turn` and `released_by=acoustic_quiet` are separately
countable. If part A works and part B is dead, or vice versa, the journal says so
per call instead of leaving it to be inferred from an aggregate.

The timeout log carries `quiet_ms` too, so a hold that *does* hit the cap now
states whether the caller was genuinely still audible — the difference between
"a caller who never yields", which is the documented correct behaviour, and "a
flag nothing ever lowered", which was the bug.

And `none` is never rendered as `0`:

```python
def _fmt_ms(seconds: Optional[float]) -> str:
    """`none` and `0` mean opposite things here — one is "we could not tell",
    the other is "the caller is making noise this instant" — and collapsing
    them is how a dead signal reads as a healthy one for days."""
    return "none" if seconds is None else f"{seconds * 1000.0:.0f}"
```

That is the `cached=None` vs `cached=0` lesson from the prompt-cache
investigation, applied before it can cost anything.

---

## 9. Failing safe: six ways this could have talked over people

The gate's entire purpose is to avoid talking over callers. A fix for it that
introduces a new way to talk over callers is worse than the bug. Every path was
enumerated and tested.

| Scenario | Behaviour | Test |
|---|---|---|
| No stamp at all (browser / ask-AI sessions never run the telephony ingest loop) | `None` → no opinion → full 2.5 s cap, exactly as before | `test_a_session_with_no_audio_measurement_behaves_exactly_as_before` |
| Garbage stamp (`"not-a-number"`) | `None` → no opinion, no exception | `test_a_garbage_stamp_is_no_opinion` |
| Clock ran backwards (negative age) | `None`, **not** `0` — 0 would read as "speaking right now" | `test_a_clock_that_went_backwards_is_no_opinion` |
| Line noise above RMS 500 keeps the stamp fresh | Never releases early → degrades to the old 2.5 s cap. **Fails in the safe direction** | `test_line_noise_degrades_to_the_old_behaviour` |
| Caller genuinely still talking | Held right to the cap, verified against a task stamping a voiced frame every 20 ms | `test_a_caller_still_talking_is_still_protected` |
| The whole feature is wrong | `VOICE_PRE_TTS_QUIET_RELEASE_S=0` restores the exact previous behaviour, no redeploy | `test_the_kill_switch_restores_the_exact_previous_behaviour` |

The asymmetry is deliberate throughout: **missing or unusable data always means
"keep holding"**, never "go ahead and speak".

---

## 10. The tests: twelve paths on a real `CallSession`

You asked for every early-return path tested against a real `CallSession`. 35
tests, in two files.

The reason the model type matters is not pedantry. `CallSession` is a pydantic v2
model without `extra="allow"`:

```python
def test_the_flag_survives_a_real_pydantic_model():
    """The trap, pinned. Underscore-prefixed names reach the private store; the
    public form raises. This is why the fix is invisible-until-production if it
    is only ever tested against SimpleNamespace."""
    s = real_session()
    mark_caller_speaking(s)
    assert s._caller_speaking is True
    assert isinstance(s._caller_speaking_since, float)

    with pytest.raises(ValueError, match="has no field"):
        s.caller_speaking = True
```

Underscore-prefixed names are routed to pydantic's private-attribute store; an
undeclared public attribute raises. `types.SimpleNamespace` accepts both, which is
how `last_audio_rms` and `caller_voice_onset_at` shipped dead for days with green
tests. The existing `test_interrupt_concurrency_and_pre_tts.py` still uses
`SimpleNamespace`; the new files do not.

The EndOfTurn is delivered exactly as production delivers it — the empty control
marker through the real dispatcher:

```python
def eot_marker() -> _Chunk:
    """Flux emits TWO chunks per EndOfTurn — the text, then this empty final.
    Only this one satisfies `detect_turn_end` (`is_final and not text`), so this
    is the chunk the clear has to key on."""
    return _Chunk(text="", is_final=True)


async def deliver_end_of_turn(pipeline, session):
    await TranscriptHandler(pipeline).handle(session, eot_marker())
    await drain(pipeline)
```

with the stub pipeline routing to the **real** `TurnEnder` so its eight exits are
genuinely exercised rather than simulated:

```python
async def handle_turn_end(self, session, websocket=None, **k):
    """Route to the REAL TurnEnder so its eight early returns are exercised."""
    return await TurnEnder(self).handle(session, websocket, **k)
```

Two tests deserve singling out.

**The load-bearing negative** — the fix must not clear on ordinary transcripts:

```python
async def test_a_text_chunk_does_not_clear_only_the_marker_does():
    """The caller is mid-utterance: interim and final TEXT chunks are not a turn
    boundary. Clearing on those would release the hold while they are talking —
    the exact talk-over the gate exists to prevent."""
    await TranscriptHandler(p).handle(s, _Chunk(text="what does", is_final=False))
    assert caller_is_speaking(s) is True

    await TranscriptHandler(p).handle(s, _Chunk(text="what does it cost", is_final=True))
    assert caller_is_speaking(s) is True, (
        "a text-bearing final is Flux's transcript, not its turn boundary"
    )
```

**The defect itself, pinned** — so that if someone later "helpfully" adds a clear
to `turn_ender`'s early returns, the note explaining why the fix moved gets
revisited rather than silently invalidated:

```python
async def test_turn_ender_alone_cannot_clear_on_an_early_return():
    ...
    assert caller_is_speaking(s) is True, (
        "if this ever goes False, turn_ender learned to clear on its early "
        "returns and the note in transcript_handler needs revisiting"
    )
```

### The test that passed for the wrong reason

`test_path_11_turn_skipped_llm_busy` passed against the *pre-fix* code. That is a
failure of the test, not a success of the code, and chasing it down changed it.

To reach `turn_skipped_llm_busy`, `session.llm_active` must be true **and**
`pending_task is not current_task`. But if a different task holds the slot, the
guard 20 lines earlier (`turn_skipped_pending_task`) returns first. So the branch
is only reachable when the slot is *empty* — and the route through
`transcript_handler` always registers the task in `_pending_llm_tasks` before the
body runs, making `pending_task is current_task`.

The branch is therefore unreachable through the EndOfTurn path. Which is exactly
what production shows: **zero** `turn_skipped_*` in four days (§3, chart 3).

Rewritten to drive it directly and assert on the log, so it can never again pass
without reaching the branch:

```python
    s.llm_active = True
    p._pending_llm_tasks.clear()
    with caplog.at_level("INFO"):
        await TurnEnder(p).handle(s, None, user_text="a real question")

    assert "turn_skipped_llm_busy" in caplog.text, "never reached the branch"
    assert caller_is_speaking(s) is False, "the early return re-raised the flag"
```

---

## 11. Proving the tests fail before the fix

A test that passes before and after the change proves nothing. Both source files
were reverted to pristine `f750b1f2` in the verify worktree and the suite re-run.

```
FAILED test_path_01_backchannel_during_agent_speech
FAILED test_path_02_suppressed_empty_eot_marker
FAILED test_path_03_duplicate_end_of_turn
FAILED test_path_04_queued_behind_pending
FAILED test_path_05_empty_transcript
FAILED test_path_06_turn_0_transcript_rejected
FAILED test_path_07_instant_opener
FAILED test_path_08_repetitive_hallucination
FAILED test_path_09_backchannel_suppressed
FAILED test_path_10_turn_skipped_pending_task
FAILED test_path_12_turn_skipped_self_echo
FAILED test_a_provider_that_raises_does_not_kill_the_turn
FAILED test_clearing_is_idempotent
13 failed, 5 passed
```

**Eleven of the twelve paths fail against the old code.** (Path 11 is the one
discussed above; it now asserts on the log line instead.)

The five that pass pre-fix all pass *deliberately* — they are invariants that must
hold in both worlds: the happy path already cleared, text chunks must not clear,
`turn_ender` alone must not clear, and the pydantic trap is a property of the
model. If any of those had failed, the change would have been altering behaviour
it had no business touching.

The acoustic-release file cannot even import against pristine code — `_QUIET_RELEASE_S`,
`_caller_quiet_for_s` and `_fmt_ms` do not exist there. That is the correct
failure for a new-capability test file.

---

## 12. The gate, the deploy, and verification inside the process

Full unit suite, in a worktree at prod HEAD with only these files changed:

```
8 failed, 4837 passed, 15 skipped, 1101 warnings, 5 errors in 202.20s
```

The 8 failures and 5 errors are the known pristine baseline — systemd install-script
permissions, webhook HMAC and IDOR tests needing secrets, metrics-endpoint auth.
Identical set, identical count, on unmodified HEAD.

Adjacent suites specifically re-run for regressions, since this touches shared
state: `test_interrupt_concurrency_and_pre_tts.py` and
`test_interrupt_gateway_drain.py` — **60 passed** with the new files.

Deploy:

```
prod HEAD    3d0ca65d   (f750b1f2 -> a88e6369 -> 3d0ca65d)
import smoke import OK
active_sessions 0        (checked before restart)
services     talky-api talky-voice-worker talky-dialer-worker
             talky-reminder-worker talky-voice-gateway  = all active
health       {"ready":true,"db":"ok","redis":"ok"}
post-restart warnings/errors: none
```

And — the part that has caught mistakes before — verified **inside the running
process**, not on disk:

```
_QUIET_RELEASE_S      = 0.7
_MAX_HOLD_S           = 2.5
_caller_quiet_for_s   = True
clears on EndOfTurn   = True
reuses _is_turn_end   = True
```

---

## 13. Correction 1 — the "36 unanswered turns / 15.5%" claim was wrong

Report 7 stated that 36 of 232 caller turns (15.5%) produced no reply, and
attributed it to this leak. **That measurement was wrong.** Measured correctly:

```
  day           turns  no reply    rate
  2026-08-13      270         3    1.1%
  2026-08-17       98         2    2.0%
  2026-08-18      272         2    0.7%
  2026-08-19      213         2    0.9%
  TOTAL           853         9    1.1%
```

On 2026-08-18 specifically — the day report 7 analysed — it was **2 of 272
(0.7%)**, not 36 of 232 (15.5%).

The cause was a log-matching bug of mine. The turn_end line renders as:

```
[app.domain.services.voice_pipeline.turn_ender] [req=-] [call=06a6f8c9-...] turn_end
```

with `[req=]` and `[call=]` injected *between* the logger name and the message. A
matcher looking for the literal `turn_ender] turn_end` finds **zero** matches, so
every caller turn is scored as unanswered. My first run of the corrected script
this session returned "583 of 583, 100.0%" — an obviously impossible number that
made the bug visible immediately.

The nine genuine unanswered turns, listed:

```
  08-17 21:25:26  b6d354d9  'Okay.'
  08-17 21:27:12  b6d354d9  'take care, and goodbye.'
  08-18 19:32:50  a6d90aa2  'Can you hear me?'
  08-18 19:35:45  35367a41  'Hello?'
  08-19 12:53:30  c1c91eb7  'Okay.'
  08-19 13:51:18  e83a8f17  'Richter works now.'
```

Mostly end-of-call pleasantries after the agent had already wrapped up. Two —
"Can you hear me?" and "Hello?" — look like genuine drops and are worth a look,
but they are two, not thirty-six.

**What this means for the fix.** The leak is fully responsible for the hold
timeouts: 16 of 16, 100%, no ambiguity. It is *not* responsible for a 15%
unanswered-turn rate, because there is no such rate. So `pre_tts_hold_released`
appearing is the meaningful proof, and the unanswered-turn rate should be
expected to stay near 1% rather than to drop — I set that expectation wrongly in
report 7 and I am setting it correctly now.

---

## 14. Correction 2 — "36% → 9% failover" was a small-sample artifact

While checking for regressions I re-measured the STT echo guard from report 7,
which claimed the failover rate fell from 36% to 9%. Per **distinct answered
call**:

```
  day            answered failed over     rate
  2026-08-13           38           0       0%
  2026-08-17            8           3      38%
  2026-08-18           28          10      36%
  2026-08-19           16           4      25%

  Aug 18 spans the echo-guard deploy (f05629f5, 20:24:24). Split:
    before 20:24:24 : 20 answered,  9 failed over  ( 45%)
    after  20:24:24 :  8 answered,  1 failed over  ( 12%)
```

The immediate post-deploy window does reproduce the claim — 45% → 12%. But it was
**8 calls and 1 failover**. The next full day, with the guard live throughout, ran
**4 of 16 = 25%**.

Pooling all post-guard traffic honestly:

```
  pre-guard  (Aug 17 + Aug 18 before 20:24) : 12 / 28  =  43%
  post-guard (Aug 18 after 20:24 + Aug 19)  :  5 / 24  =  21%
```

**43% → 21%**, not 36% → 9%. The echo guard is real and it roughly halved the
failover rate — that part stands. But it did not take it to single digits, and I
should not have published a headline number off eight calls. A fifth of calls
still abandon Flux, which is worth its own investigation and is **not** something
today's change addresses.

The 2026-08-13 row showing 0% is not a comparison point: the watchdog shipped
that evening, so most of that day had no failover path at all.

---

## 15. The waveform reconciler

Your second request: reconcile when Python stops sending audio against when the
caller actually stops hearing it.

Every latency figure the pipeline reports about an interruption is measured
inside Python. `detect_ms` is the caller's acoustic onset to our decision.
`speech_to_stop_ms` is onset to teardown complete. `gw_ms` is what the C++
gateway still held when asked to stop. None of them is the caller's experience.

The recording is. And it turns out to be in the best possible form:

```
  16000 Hz stereo · 306.8s · 20ms frames · speech at RMS >= 500

  channel 0: first onset   0.06s · voiced  157.0s
  channel 1: first onset   0.40s · voiced   66.0s
  => AGENT = channel 0, CALLER = channel 1 (agent speaks first: disclosure, then greeting)
```

**One leg per channel**, so agent and caller separate cleanly and the moment the
agent's audio truly ceases is directly measurable.

`scripts/barge_in_waveform_reconcile.py` computes a 20 ms RMS envelope per
channel, identifies which leg is the agent (it speaks first — the disclosure, then
the greeting — and speaks more), and anchors the recording's clock to the journal
by matching the first agent onset to the first TTS send:

```
  anchor: first agent audio at 0.06s in the file == 14:06:39.312 in the journal
          recording t=0 == 14:06:39.252 (+/- one 20ms frame)
```

One practical trap worth recording: recordings are named after the dialer's
`calls.id`, which is a **different UUID** from the voice-session id that every
pipeline log line is keyed on. The single line carrying both is:

```
bind_telephony_call voice_session=1e9d7faf -> calls.id=425ddbb0 pbx=talky-out-20
```

The script resolves it automatically.

---

## 16. Two measurement bugs I caught before reporting them

The first version of this tool produced a confident, plausible, completely wrong
table. Both bugs are worth recording because both would have shipped as findings.

**Bug 1 — pairing interrupts by time.** The tool matched each
`interrupt_step=begin` with the next `interrupt_complete` in time. But most
interrupts never log a completion:

```
  interrupt_step=begin (all events)          |  924  100.0%
  nothing_playing shortcut                   |  640   69.3%
  reached the gateway (interrupt_complete)   |  284   30.7%
```

Only 30.7% reach the gateway; the rest take the `nothing_playing` shortcut and
return early. So one real interrupt's numbers got attributed to every no-op
before it. The tell was in the output and I nearly missed it —
`detect_ms=492.3ms`, character-identical, on twenty consecutive rows. Real
measurements do not repeat to one decimal place. Now paired on `interrupt_id`.

**Bug 2 — measuring the agent's next reply.** The tool searched forward from the
interrupt for the first 200 ms of silence on the agent leg. When the gateway
queue was already empty and the agent was *not* audible at the decision, that
search ran straight past the gap and found the end of the agent's **next reply** —
reporting it as a 2,389 ms "overhang". Now gated on whether the agent leg was
actually above threshold at the decision instant:

```python
agent_rms = agent.at(t_rel)
audible = agent_rms >= SPEECH_RMS
```

**Bug 3, conceptual — I had the reconciliation equation backwards.** I initially
compared the audible tail against `gw_ms` expecting them to match. They should
not. `gw_ms` is audio the gateway **discarded** — audio the caller was *spared*,
not audio they heard. A correct stop is therefore a **near-zero audible tail
together with a non-zero `gw_ms`**. The summary now says so explicitly rather than
leaving it to be misread by the next person.

---

## 17. What the recording actually shows

> **RETRACTED AND REPLACED 2026-08-20.** The tables originally printed here, and
> the entire "new open finding" that followed them in §18, were produced with the
> agent and caller channels **swapped**. Everything reported as "the agent kept
> playing after we stopped it" was the **caller's own voice continuing after they
> barged in** — which is normal behaviour, not a defect. The cause and the
> corrected numbers are below. §18 is now the retraction notice.

### How the error happened

The reconciler identified the agent's leg with a heuristic: *the agent speaks
first — disclosure, then greeting — and speaks more.* That is wrong on every call
measured. The **caller** answers the phone and says "hello" before the agent's
disclosure begins.

On the Aug 19 call the voiced totals were lopsided enough (157 s vs 66 s) that the
result looked authoritative. On the Aug 20 calls they were nearly equal — 42.9 s
vs 41.6 s — which should have been the warning that the heuristic had no signal
to work with.

The sound test, now in the tool, scores each channel against every synthesis
event in the call: the agent's audio must appear right after each
`TTS_FMT_DEBUG` and be absent otherwise. It is not marginal:

```
  agent-channel test — voiced in the 1s after each of 19 synthesis starts:
      channel 0:   4.4%        channel 1:  74.8%
  => AGENT = channel 1, CALLER = channel 0
```

Run on all three calls, the answer is channel 1 every time — the opposite of what
the heuristic chose:

```
  call 1e9d7faf (Aug 19)   ch0 38.2%   ch1 70.6%   -> agent = ch1
  call cb4bd059 (Aug 20)   ch0  4.4%   ch1 74.8%   -> agent = ch1
  call 58fcbcb5 (Aug 20)   ch0  8.1%   ch1 76.1%   -> agent = ch1
```

The tool now also warns when the two channels score within 15 points of each
other, so an unseparated recording cannot silently produce numbers again.

### The corrected measurements

**Call `1e9d7faf`** (Aug 19, on `f750b1f2`) — 36 interrupt events, 6 reached the
gateway:

```
  id            heard-after decision   gateway discarded   agent at decision
  82daa5f70789          123ms                260ms         audible, rms 2714
  6d08744af436          149ms                260ms         audible, rms 1417
  af434aead17a            0ms                  0ms         already silent
  ca4ed546fde2            0ms                  0ms         already silent
  6fab414e68b1            0ms                240ms         already silent
  80253dbbc08d            0ms                  0ms         already silent
```

**Call `cb4bd059`** (Aug 20, on `3d0ca65d`) — 18 interrupt events, 8 reached the
gateway. Seven found the agent leg already silent; the one where the agent was
genuinely mid-word:

```
  55d44f1953a9          170ms                240ms         audible, rms 2285
```

**Call `58fcbcb5`** (Aug 20, on `3d0ca65d`) — 13 interrupt events, 4 reached the
gateway, three of them genuinely mid-word:

```
  f299effcd690           45ms                260ms         audible, rms 4164  STOPPED CLEANLY
  73d9dee77ea1           48ms                240ms         audible, rms  723  STOPPED CLEANLY
  103da1b89793           17ms                220ms         audible, rms  566  STOPPED CLEANLY
```

Pooled — every interrupt across all three calls where the agent was actually
audible at the moment we decided to stop:

```
  caller-speech-to-audible-stop:  17, 45, 48, 123, 149, 170 ms
  n=6   min 17ms   median 85ms   max 170ms
  gateway discarded alongside:    220-260ms in 5 of 6 cases
```

The barge-in path is working, and the drain fix from `f750b1f2` is visible in
the data: 220–260 ms of queued audio discarded before it could reach the caller,
with the caller's ear going quiet inside 170 ms.

---

## 18. RETRACTED — the "audible tails with an empty gateway queue"

This section previously reported three interrupts leaving the caller hearing the
agent for 603 ms, 1,447 ms and 2,389 ms, described it as a new open finding with
`gw_frames=0` as its signature, and proposed two candidate root causes.

**None of it was real.** Those were the caller's own voice, on the channel I had
mislabelled as the agent. Measured on the correct channel the same three
interrupts show `0 ms`, `0 ms` and `0 ms` — the agent leg was already silent at
the decision in every one.

Task #64, opened off this finding, is closed as invalid.

### What went wrong, and why it survived

This was the **fourth** measurement bug in the same tool, and the only one that
reached a published report. The first three were caught because they produced
something visibly absurd — `detect_ms=492.3` repeated on twenty rows, a 2,389 ms
"overhang" on an interrupt with an empty queue. This one produced numbers that
looked *plausible*: 1.4 s of talk-over is exactly the complaint the work started
from, so the result confirmed the story instead of contradicting it.

That is the same failure mode as the 15.5% unanswered-turn figure in §13, one
report apart. Both times a wrong number survived because it agreed with what I
already believed. The countermeasure is not more care; it is refusing to let a
channel, a denominator, or a matcher be decided by an assumption when it can be
decided by evidence — which is what the agent-channel test now does.

What genuinely remains open is the *detection* latency, not the stop: the caller
had been speaking for 1 ms to 957 ms before we reacted, which is the `detect_ms`
distribution already tracked in Appendix A.

---


## 19. Pre-mortem

Written before the code, per standing instruction. Each row is how the fix could
be sitting broken in production a week from now.

| # | Failure | Mitigation | Landed? |
|---|---|---|---|
| 1 | Nova (post-failover) never emits the empty final, so the clear never runs | `detect_turn_end` is the base-class default, inherited by the resilient wrapper; part A degrades to old behaviour, part B is provider-independent | Holds — no provider-specific code added |
| 2 | `_caller_voice_last_at` absent on browser / ask-AI sessions | Returns *no opinion* → identical to previous behaviour | Tested |
| 3 | We release early and talk over the caller | 700 ms vs 110–130 ms median human gaps and a 200 ms perceptual floor (§6); competitor is a 2,500 ms cap that fires 100% of the time | Argued from literature, not judgement |
| 4 | The pydantic trap again — assignment silently raises inside a `try` | All three names underscore-prefixed; tests build a real `CallSession` and pin the raise | Tested |
| 5 | **Guard wired to a signal that is constant in production** — three times this month | Same writer produced 183 `detect_ms` samples spanning 19 ms–34 s; and the release log names the exit per call | Both |
| 6 | Line noise above RMS 500 keeps the stamp permanently fresh | Degrades to the 2.5 s cap. Fails safe | Tested |
| 7 | Hot-path cost — `detect_turn_end` now runs on every chunk | It was already running on every chunk; the change computes it *once* instead of once per branch | Net neutral |
| 8 | A provider raises inside `detect_turn_end` | Wrapped; costs the clear, not the call | Tested |

The one I got wrong: I did not pre-mortem **my own measurement**. Both corrections
in §13 and §14 are analysis bugs, not code bugs, and neither had a pre-mortem row.
§20 addresses that.

---

## 20. Post-mortem

**What worked.**

Going to the journal before trusting my own report 7. That single decision
produced everything of value here — it exposed the 13-of-16 case the requested fix
would have missed entirely, and it caught two wrong numbers I had published.

Refusing to report the first waveform table. It was formatted, confident and
wrong. The tell was cosmetic — `492.3ms` repeated verbatim on twenty rows — and
the instinct to treat a *too-tidy* number as suspicious is what saved it.

Classifying before fixing. Had I shipped part A alone and declared the leak fixed,
the next call would have produced 13 more timeouts and no explanation.

**What did not work.**

*I shipped a wrong number in report 7 and it survived a full report cycle.* The
15.5% unanswered-turn rate came from a matcher that silently matched nothing.
Today the same class of bug produced "100.0% unanswered" and I spotted it in
seconds — because 100% is absurd and 15.5% is merely bad. **An implausible
success number gets audited; an implausible failure number confirms the story you
are already telling.** That asymmetry is the lesson, and it is why §13 and §14
exist in this report at all.

*I published 36% → 9% off eight calls.* The direction was right, the magnitude was
not. A rate computed over single-digit denominators should never have been a
headline.

*My first classification used a loose four-second window* rather than anchoring on
the arm event, giving 5/9 instead of the correct 3/13. I reported the loose number
to you in chat before the rigorous one existed. It did not change the decision —
part B was already justified — but it was a number stated more precisely than the
method supported.

**Carried forward.**

Three guards this month were wired to signals that never varied in production. The
countermeasure that has actually worked is not more tests — all three had thorough
tests that verified the branch and never the wire. It is **logging the wiring per
call**, which is why `released_by=` exists in this change. The equivalent
discipline for analysis is a sanity check on the denominator and a moment spent
asking whether a rate is physically plausible before it becomes a headline.

---

## 21. Open items, and what your next call settles

| # | Item | State |
|---|---|---|
| 61 | `_caller_speaking` leak | **Fixed**, deployed `3d0ca65d`, awaiting live confirmation |
| 62 | Waveform ↔ gateway reconciliation | Tool built and validated; awaiting one call on `3d0ca65d` |
| — | ~~Audible tails of 1.4–2.4 s with `gw_frames=0`~~ (§18) | **RETRACTED** — channel-mapping error; measured stop is 17–170 ms |
| — | STT failover still ~21% of calls post-guard (§14) | **Open**, was believed to be 9% |
| 44 | Recordings lost when caller barges over the disclosure | Open **by your decision** — product/compliance call |
| 43 | Prompt prefill p50 ~629 ms of ~641 ms TTFT | Open; needs an A/B or a caching model |
| 54 | `CredentialResolver._CACHE` keyed on `id(db_pool)` | Open; credential path, needs authorisation |
| 59 | 5 tenant configs on the dead `llama-3.1-8b-instant` | Open; needs a DB write + authorisation |
| — | `detect_ms` p50 751 ms, p90 4,270 ms, 18% over 3 s | Open; energy-triggered barge-in still deferred |

**What one call settles.** Place a call and interrupt the agent mid-sentence,
twice.

1. **`pre_tts_hold_released` appears at all** — it never has, in 16 attempts over
   four days. The line names which exit fired, so part A and part B are counted
   separately rather than inferred.
2. **The 0-of-16 becomes something other than zero.** That single ratio is the
   entire fix.
3. **The recording reconciles.** Mid-sentence matters: an interrupt during silence
   takes the `nothing_playing` shortcut and produces no data. Two mid-sentence
   interruptions give the §18 investigation the correlated waveform it needs.

I have not started any campaign to generate this traffic.

---

## Appendix A — Charts: every measured figure

All from a single pass over 342,349 journal lines covering 2026-08-13 to
2026-08-19. The generator is reproduced in Appendix B.

```
==============================================================================
  CHART 1 - pre-TTS hold outcomes, per day (the whole history)
==============================================================================
  day           timeout  released
  2026-08-13          2         0  |##########                    | timeouts
  2026-08-17          4         0  |####################          | timeouts
  2026-08-18          6         0  |##############################| timeouts
  2026-08-19          4         0  |####################          | timeouts
  TOTAL              16         0

  release rate: 0/16 = 0.0%

==============================================================================
  CHART 2 - how long each hold waited before giving up
==============================================================================
  n=16   min 2.50s   max 2.66s   cap is 2.50s
      2.50s | #########################                      4
      2.51s | ############################################   7
      2.52s | #########################                      4
      2.66s | ######                                         1
  Every hold ran to the cap. None ended because the caller stopped.

==============================================================================
  CHART 3 - the twelve early-return paths, occurrences in production
==============================================================================
  1  backchannel during agent speech   (transcript_handler) | ####################   33
  2  suppressed empty EOT marker       (transcript_handler) |                         0
  3  duplicate EndOfTurn               (transcript_handler) |                         0
  4  queued behind pending turn        (transcript_handler) | ###                     5
  6  turn-0 transcript rejected        (turn_ender)    |                         0
  7  instant opener                    (turn_ender)    | #####                   8
  8  repetitive hallucination          (turn_ender)    |                         0
  9  backchannel suppressed            (turn_ender)    | ###                     5
  10 turn skipped, pending task        (turn_ender)    |                         0
  11 turn skipped, llm busy            (turn_ender)    |                         0
  12 turn skipped, self echo           (turn_ender)    |                         0
  (path 5, empty transcript, returns at DEBUG and is not in the journal)

==============================================================================
  CHART 4 - caller turns that got no reply within 12s, per day
==============================================================================
  day           turns  no reply    rate
  2026-08-13      270         3    1.1%  |##########################|
  2026-08-17       98         2    2.0%  |#########                 |
  2026-08-18      272         2    0.7%  |##########################|
  2026-08-19      213         2    0.9%  |####################      |
  TOTAL           853         9    1.1%

==============================================================================
  CHART 5 - interrupt events: how many actually stopped audio
==============================================================================
  interrupt_step=begin (all events)          | ############################################   924  100.0%
  nothing_playing shortcut                   | ##############################                 640   69.3%
  reached the gateway (interrupt_complete)   | ##############                                 284   30.7%

==============================================================================
  CHART 6 - detect_ms and speech_to_stop_ms
==============================================================================
  detect_ms   n=183
    p50 751ms   p90 4270ms   max 34271ms   min 19ms
           0-200 ms | ############################################   51   27.9%
         200-400 ms | #####                                           6    3.3%
         400-600 ms | ###############                                17    9.3%
         600-800 ms | ####################                           23   12.6%
        800-1200 ms | ##########                                     12    6.6%
       1200-2000 ms | #################                              20   10.9%
       2000-3000 ms | ##################                             21   11.5%
           3000+ ms | ############################                   33   18.0%

  speech_to_stop_ms   n=183
    p50 779ms   p90 4272ms   max 34273ms   min 21ms
           0-200 ms | ############################################   42   23.0%
         200-400 ms | ###############                                14    7.7%
         400-600 ms | #################                              16    8.7%
         600-800 ms | #######################                        22   12.0%
        800-1200 ms | ###############                                14    7.7%
       1200-2000 ms | ######################                         21   11.5%
       2000-3000 ms | ######################                         21   11.5%
           3000+ ms | ###################################            33   18.0%

==============================================================================
  CHART 7 - gw_ms: audio the gateway DISCARDED (what the caller was spared)
==============================================================================
  gw_ms   n=284
    p50 0ms   p90 260ms   max 300ms   min 0ms
             0-1 ms | ############################################  164   57.7%
           1-100 ms | ##                                              8    2.8%
         100-200 ms | ##                                              8    2.8%
         200-240 ms | ##                                              6    2.1%
         240-260 ms | ############                                   46   16.2%
         260-300 ms | #############                                  48   16.9%
         300-400 ms | #                                               4    1.4%
            400+ ms |                                                 0    0.0%
    zero-queue interrupts: 164/284 = 57.7%
    A zero queue WITH the agent still audible is the open finding in S16.

==============================================================================
  CHART 8 - STT failovers per day (echo-guard regression check)
==============================================================================
  day           calls  failovers
  2026-08-13       38          0  |                        |
  2026-08-17        8          3  |#######                 |
  2026-08-18       28         10  |########################|
  2026-08-19       15          4  |##########              |
```

### A note on two of these numbers

**`detect_ms` max of 34,271 ms** is a measurement artifact, not a 34-second
reaction. The onset anchor is capped at 60 s and reports the age of the *current*
voiced run; when a caller is silent for a long stretch and the interrupt fires on
a stale run, the age is large but meaningless. The p50 and p90 are the usable
figures; anything past a few seconds should be read as "no clean onset", which is
itself worth fixing and is why energy-triggered barge-in remains deferred.

**`gw_ms` p50 of 0** with 57.7% of interrupts finding an empty queue is expected,
not alarming, on its own: 69.3% of interrupt events are ordinary turns that took
the `nothing_playing` shortcut. It only becomes the §18 finding when the queue is
empty **and** the recording shows the agent still audible.
---

## Appendix B — Commands, so you can reproduce all of it

Nothing in this report is a claim you have to take on trust. Every figure came
from one of these.

**The signature — hold outcomes, all time:**

```bash
journalctl -u talky-api --no-pager -o cat --since 2026-08-13 \
  | grep -oE "pre_tts_hold_(timeout|released|error)" | sort | uniq -c
```

**The raw timeout lines with their waited values:**

```bash
journalctl -u talky-api --no-pager --since 2026-08-13 | grep "pre_tts_hold"
```

**Which exit each hold took (after this deploy) — the new attribution:**

```bash
journalctl -u talky-api --no-pager --since today \
  | grep -oE "released_by=[a-z_]+" | sort | uniq -c
```

**Unanswered caller turns.** Note the matcher: the message is separated from the
logger name by `[req=]` and `[call=]`, which is what broke the report 7 figure.

```bash
journalctl -u talky-api --no-pager -o short-iso --since 2026-08-13 > /tmp/j.txt
grep -c "Flux EndOfTurn" /tmp/j.txt
grep -cE "voice_pipeline\.turn_ender\].*turn_end$" /tmp/j.txt
```

**Failover rate per distinct answered call** (not per log line — that was the
report 7 error):

```bash
grep "resilient_stt_failed_over_to" /tmp/j.txt \
  | grep -oE "\[call=[0-9a-f-]{8}" | sort -u | wc -l
```

**Interrupt outcomes — how many actually stopped audio:**

```bash
grep -c "interrupt_step=begin"          /tmp/j.txt
grep -c "interrupt_step=nothing_playing" /tmp/j.txt
grep -c "interrupt_complete"             /tmp/j.txt
```

**The waveform reconciliation:**

```bash
# recordings are named after the dialer's calls.id, not the voice session
ls -lt /opt/talky/backend/recordings/*.wav | head

/opt/talky/backend/venv/bin/python scripts/barge_in_waveform_reconcile.py \
    /opt/talky/backend/recordings/<calls-id>.wav --since 2026-08-19
```

**Mapping a recording to its voice session by hand, if ever needed:**

```bash
journalctl -u talky-api --no-pager --since 2026-08-19 \
  | grep "bind_telephony_call"
# bind_telephony_call voice_session=1e9d7faf -> calls.id=425ddbb0 pbx=talky-out-20
```

**Verify the change is live inside the running process, not merely on disk:**

```bash
cd /opt/talky/backend && venv/bin/python -c "
from app.domain.services.voice_pipeline import playback_gate as pg
from app.domain.services.voice_pipeline import transcript_handler as th
import inspect
print('_QUIET_RELEASE_S    =', pg._QUIET_RELEASE_S)
print('_caller_quiet_for_s =', callable(pg._caller_quiet_for_s))
src = inspect.getsource(th.TranscriptHandler.handle)
print('clears on EndOfTurn =', 'mark_caller_stopped(session)' in src)
"
```

**Run the gate without touching prod:**

```bash
git -C /opt/talky worktree add --detach /tmp/talky-localfix HEAD
# scp the modified files in, then:
cd /tmp/talky-localfix/backend && /opt/talky/backend/venv/bin/python -m pytest \
    tests/unit -q -p no:cacheprovider \
    --ignore=tests/unit/test_dialer_redis_reliability.py
git -C /opt/talky worktree remove /tmp/talky-localfix --force
```

*(`test_dialer_redis_reliability.py` is excluded because `fakeredis` is not in the
production venv — it is an environment gap, not a failure.)*

**Just the two new suites:**

```bash
python -m pytest \
  tests/unit/test_caller_speaking_cleared_on_end_of_turn.py \
  tests/unit/test_pre_tts_hold_acoustic_release.py -q
```

---

## Appendix C — Config and kill switches

Everything introduced in this change can be turned off from the environment
without a redeploy. A restart is enough.

| Variable | Default | Effect |
|---|---|---|
| `VOICE_PRE_TTS_QUIET_RELEASE_S` | `0.7` | Acoustic-quiet release window. **`0` disables the new exit entirely**, restoring the exact pre-`a88e6369` behaviour — EndOfTurn exit plus the 2.5 s cap. |
| `VOICE_ONSET_RMS` | `500` | Frame RMS above which audio counts as the caller's voice. Shared with `resilient_stt._SPEECH_RMS_THRESHOLD` and `audio_ingest` so one number moves the whole audio path. Raising it makes the acoustic release *more* eager; lowering it makes it more conservative. |
| `VOICE_ONSET_GAP_S` | `0.4` | Pause that ends a run of speech, so the next voiced frame counts as a new utterance. Feeds `detect_ms`. |

Not introduced here, but adjacent and relevant if you are tuning this area:

| Variable | Default | Effect |
|---|---|---|
| `VOICE_GATEWAY_DRAIN_S` | `0.6` | Window after Python's last chunk in which an interrupt still asks the gateway rather than assuming nothing is playing (`f750b1f2`). `0` restores the pre-fix shortcut. |
| `VOICE_STT_ECHO_TAIL_S` | `0.25` | Tail after the agent stops during which audio is still treated as possible echo, for the STT watchdog (`f05629f5`). |
| `STT_FAILOVER_ENABLED` | `true` | Master switch for Flux → Nova promotion. |

Hard-coded, deliberately, in `playback_gate.py`:

| Constant | Value | Why not an env var |
|---|---|---|
| `_MAX_HOLD_S` | `2.5` | The fail-open cap. Changing it changes how long a caller can mute the agent; it should be a code review, not a config change. |
| `_POLL_S` | `0.02` | One PCMU frame. The hold cannot add more than a single frame of latency beyond the caller actually stopping. |

**Rollback.** `git checkout f750b1f2` on the server and restart the four Python
services. The C++ gateway is unchanged by this deploy and does not need
restarting.

---

## Appendix D — Forensic timelines: all sixteen timeouts

```
Every pre-TTS hold timeout in the journal, with the 20 seconds before it.
Classification rule: did an EndOfTurn arrive between the arm and the
timeout? If yes, a filter swallowed it (mechanism a). If no, Flux never
emitted one and there was nothing to clear (mechanism b).

----------------------------------------------------------------------------
TIMEOUT 1   call=9b4a1297  2026-08-13 14:11:17  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     14:11:07  EndOfTurn      'What did I ask you?'
     14:11:07  turn_end       -> mark_caller_stopped(): flag lowered
     14:11:13  EndOfTurn      'No. Before that.'
     14:11:13  turn_end       -> mark_caller_stopped(): flag lowered
  >> 14:11:14  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     14:11:17  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 2   call=460d3b50  2026-08-13 14:23:21  waited=2.52s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     14:23:17  EndOfTurn      'When you, uh, completed your intro, I asked you what'
     14:23:17  turn_end       -> mark_caller_stopped(): flag lowered
  >> 14:23:18  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     14:23:21  HOLD TIMEOUT   waited=2.52s

----------------------------------------------------------------------------
TIMEOUT 3   call=b3350aee  2026-08-17 21:04:26  waited=2.50s
            (a) EndOfTurn arrived and was swallowed
----------------------------------------------------------------------------
     21:04:06  EndOfTurn      'Before we finish, summarize the issues I reported, a'
     21:04:06  turn_end       -> mark_caller_stopped(): flag lowered
     21:04:15  EndOfTurn      'That will be all. Thank you.'
     21:04:15  turn_end       -> mark_caller_stopped(): flag lowered
     21:04:23  EndOfTurn      'Nothing. Thank you.'
     21:04:23  turn_end       -> mark_caller_stopped(): flag lowered
  >> 21:04:23  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     21:04:23  EndOfTurn      'Goodbye.'
  >> 21:04:23  EARLY RETURN   turn_queued_behind_pending  (path 4)
     21:04:26  HOLD TIMEOUT   waited=2.50s

----------------------------------------------------------------------------
TIMEOUT 4   call=ddf2c92c  2026-08-17 21:21:39  waited=2.66s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     21:21:29  turn_end       -> mark_caller_stopped(): flag lowered
     21:21:36  turn_end       -> mark_caller_stopped(): flag lowered
  >> 21:21:36  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     21:21:39  HOLD TIMEOUT   waited=2.66s

----------------------------------------------------------------------------
TIMEOUT 5   call=b6d354d9  2026-08-17 21:27:36  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     21:27:32  turn_end       -> mark_caller_stopped(): flag lowered
  >> 21:27:33  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     21:27:36  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 6   call=b6d354d9  2026-08-17 21:27:41  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     21:27:32  turn_end       -> mark_caller_stopped(): flag lowered
  >> 21:27:33  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     21:27:36  HOLD TIMEOUT   waited=2.51s
     21:27:38  turn_end       -> mark_caller_stopped(): flag lowered
  >> 21:27:38  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     21:27:41  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 7   call=9bf01238  2026-08-18 19:47:57  waited=2.51s
            (a) EndOfTurn arrived and was swallowed
----------------------------------------------------------------------------
     19:47:37  EndOfTurn      'Wait. What was your name again?'
     19:47:37  turn_end       -> mark_caller_stopped(): flag lowered
     19:47:45  EndOfTurn      'k. Continue with what I was telling you before I int'
     19:47:45  turn_end       -> mark_caller_stopped(): flag lowered
     19:47:54  EndOfTurn      'The project is commercial in Chicago.'
     19:47:54  turn_end       -> mark_caller_stopped(): flag lowered
  >> 19:47:54  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     19:47:57  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 8   call=9bf01238  2026-08-18 19:50:41  waited=2.52s
            (a) EndOfTurn arrived and was swallowed
----------------------------------------------------------------------------
     19:50:22  EndOfTurn      'Okay. That will be all. Thank you.'
     19:50:22  turn_end       -> mark_caller_stopped(): flag lowered
     19:50:29  EndOfTurn      'Nothing. Thank you.'
     19:50:29  turn_end       -> mark_caller_stopped(): flag lowered
     19:50:38  EndOfTurn      'Thank you. Good night.'
     19:50:38  turn_end       -> mark_caller_stopped(): flag lowered
  >> 19:50:38  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     19:50:39  EndOfTurn      'and take care. Goodbye.'
  >> 19:50:39  EARLY RETURN   turn_queued_behind_pending  (path 4)
     19:50:41  HOLD TIMEOUT   waited=2.52s

----------------------------------------------------------------------------
TIMEOUT 9   call=06a6f8c9  2026-08-18 19:56:43  waited=2.50s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     19:56:28  turn_end       -> mark_caller_stopped(): flag lowered
  >> 19:56:37  EARLY RETURN   backchannel_suppressed  (path 9)
     19:56:39  turn_end       -> mark_caller_stopped(): flag lowered
  >> 19:56:40  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     19:56:43  HOLD TIMEOUT   waited=2.50s

----------------------------------------------------------------------------
TIMEOUT 10  call=06a6f8c9  2026-08-18 19:58:57  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     19:58:50  turn_end       -> mark_caller_stopped(): flag lowered
     19:58:54  turn_end       -> mark_caller_stopped(): flag lowered
  >> 19:58:54  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     19:58:57  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 11  call=522720bb  2026-08-18 20:04:36  waited=2.50s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     20:04:26  EndOfTurn      'And you forgot about one question.'
     20:04:26  turn_end       -> mark_caller_stopped(): flag lowered
     20:04:32  EndOfTurn      "What's seventeen times six?"
     20:04:32  turn_end       -> mark_caller_stopped(): flag lowered
  >> 20:04:33  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     20:04:36  HOLD TIMEOUT   waited=2.50s

----------------------------------------------------------------------------
TIMEOUT 12  call=522720bb  2026-08-18 20:06:23  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     20:06:19  turn_end       -> mark_caller_stopped(): flag lowered
  >> 20:06:20  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     20:06:23  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 13  call=4b068695  2026-08-19 12:49:39  waited=2.51s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     12:49:25  EndOfTurn      "Well, I'm a developer, so I'm actually working on th"
     12:49:25  turn_end       -> mark_caller_stopped(): flag lowered
     12:49:35  EndOfTurn      'So, actually, we are testing it to'
     12:49:35  turn_end       -> mark_caller_stopped(): flag lowered
  >> 12:49:36  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     12:49:39  HOLD TIMEOUT   waited=2.51s

----------------------------------------------------------------------------
TIMEOUT 14  call=a686957d  2026-08-19 13:58:29  waited=2.52s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     13:58:14  EndOfTurn      'And which day would be better?'
     13:58:14  turn_end       -> mark_caller_stopped(): flag lowered
     13:58:26  turn_end       -> mark_caller_stopped(): flag lowered
  >> 13:58:26  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     13:58:29  HOLD TIMEOUT   waited=2.52s

----------------------------------------------------------------------------
TIMEOUT 15  call=1e9d7faf  2026-08-19 14:10:48  waited=2.52s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     14:10:36  turn_end       -> mark_caller_stopped(): flag lowered
     14:10:45  turn_end       -> mark_caller_stopped(): flag lowered
  >> 14:10:45  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     14:10:48  HOLD TIMEOUT   waited=2.52s

----------------------------------------------------------------------------
TIMEOUT 16  call=1e9d7faf  2026-08-19 14:11:36  waited=2.50s
            (b) no EndOfTurn ever arrived
----------------------------------------------------------------------------
     14:11:18  turn_end       -> mark_caller_stopped(): flag lowered
     14:11:32  turn_end       -> mark_caller_stopped(): flag lowered
  >> 14:11:33  HOLD ARMED     StartOfTurn during generation -> _caller_speaking = True
     14:11:36  HOLD TIMEOUT   waited=2.50s

============================================================================
  (a) EndOfTurn arrived but a filter swallowed it :  3
  (b) no EndOfTurn ever arrived                   : 13
  total                                           : 16
============================================================================
```
