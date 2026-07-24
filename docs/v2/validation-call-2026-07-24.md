# Call Validation — mined evidence, and the outstanding live test

**Ticket:** TKT-004 · **Status:** 🟡 **Part 1 complete, Part 2 blocked on authorisation**
**Evidence captured:** 2026-07-24, read-only `journalctl` against production

> TKT-004 opens with an instruction worth repeating: **"Mine the existing calls first — it is free.
> This may answer the question without dialling."** It did, partly. This document is Part 1. Part 2
> is one controlled outbound call, and it needs a human to authorise the destination number before
> anything is dialled.

---

## Executive summary

| Question | Answer |
|---|---|
| Do the 2026-07-20 test calls still exist in journald? | **Yes** — retention reaches back to 2026-06-17 |
| What did they show? | **Both greetings were cut off at ~1 s**, `interrupted=True`, immediately after a barge-in event |
| Was the greeting fix deployed before or after them? | **After.** The calls are 07-20; the fix shipped 07-21 |
| Has any call happened since the fix? | **No. The last call on this box was 2026-07-20.** |
| Is the fix therefore proven? | **No.** It is justified by evidence and unproven in production |
| Were there errors during the calls? | **None** at warning level or above |
| Did the pipeline otherwise work? | **Yes** — one call ran 9 turns and persisted a transcript cleanly |

**The single most important line in this document:** every deploy since 2026-07-21 — seven of them,
including the greeting fix this ticket exists to validate — has reached production **without a single
call being placed through it.** Sprints 2 and 3 are scheduled to document and film a system that
nobody has listened to in five days.

---

## Part 1 — the 2026-07-20 window, mined

### 1.1 Evidence availability

```bash
journalctl -u talky-api --since '2026-07-20 11:20' --until '2026-07-20 11:35' --no-pager | wc -l
```
```
2736
```

Retention is comfortable:
```
earliest available : 2026-06-17T16:11:18+00:00
journal disk usage : 3.9G
```

Five weeks of history is available. This matters beyond this ticket — **VID-16 (debugging a failed
call) needs real failure material**, and the window for harvesting it is five weeks, not forever.

### 1.2 Calls in the window

Four distinct call IDs plus one pre-answer failure:

| Call ID | Outcome | Notes |
|---|---|---|
| `651ad227-2d0a-4219-b096-9ffc810e2116` | **NO_ANSWER** | Never answered; job went to `retry_scheduled` |
| `eb97b829-455a-42b2-9a74-ea7e4439272b` | Answered | Greeting **interrupted at 1107 ms** |
| `b69305d3-1989-4c0a-955c-0bdfeffd9072` | Answered | Greeting **interrupted at 906 ms** |
| `8f32f2e9-7831-4bfd-9c60-4971d6eab034` | Answered | Present in window |
| `67b94cfe-…` | Answered | Present in window |
| `780e12d7-1f3d-4d8d-b70c-98868303e28a` | **ANSWERED, completed** | 9 turns, 62 words, transcript persisted |

The no-answer path is worth noting as a positive result:

```
11:21:03 job_completion job=e7c83b00 final=retry_scheduled disposition=no_answer
11:21:03 Call 651ad227 status updated: CallOutcome.NO_ANSWER
11:21:03 call_outcome_persisted_preanswer call_id=talky-out-ab outcome=no_answer cause=Unknown
```

`call_outcome_persisted_preanswer` firing means the **instant pre-answer outcome** path works — the
outcome is written before any media session exists, and the retry is scheduled off it. That is the
behaviour the per-campaign trunk work was meant to deliver, and here it is, working, in production.

### 1.3 The greeting truncation — the reason this ticket exists

Both answered calls show the identical signature:

```
11:25:49  outbound_greeting_presynth call_id=eb97b829-455 chunks=99 text='Hi, James here from Allsta…'
11:25:50  presynth_greeting_barge_in_post_send call_id=eb97b829-455
11:25:50  outbound_greeting_presynth_done call_id=eb97b829-455 elapsed_ms=1107 interrupted=True

11:27:30  outbound_greeting_presynth call_id=b69305d3-198 chunks=94 text='Hey, this is Sarah jones f…'
11:27:31  presynth_greeting_barge_in_post_send call_id=b69305d3-198
11:27:31  outbound_greeting_presynth_done call_id=b69305d3-198 elapsed_ms=906 interrupted=True
```

Read the ordering carefully, because it is the diagnosis:

1. The greeting is pre-synthesised — 99 and 94 chunks respectively, a full sentence of audio.
2. `presynth_greeting_barge_in_post_send` fires **immediately after** the audio is sent.
3. One second later the greeting reports `interrupted=True`.

A barge-in detected within ~1 second of the agent starting to speak, on **both** calls, with two
different personas, is not two callers coincidentally interrupting at the same moment. **The agent
heard its own greeting and yielded to itself.** The caller received roughly one second of a
multi-second introduction — enough for "Hi, James here from Allsta—" and then silence.

That is a call-ruining defect. The opening line is where the agent establishes who it is and why it
is calling; truncating it at one second produces exactly the confused "hello? hello?" opening that
makes a prospect hang up.

### 1.4 What the pipeline did well

The event histogram across the window:

```
     33 turn_ender          21 tts_playback       20 first_bytes
     12 turn_end             8 turn_complete       8 first_speaker
      6 turn_streamer        5 tts_total_ms        5 tts_first_chunk_ms
      4 stt_resilient_wrapper_active               4 eot_timeout_ms
      4 eot_threshold        4 barge_in_detected   3 tts_inference_warmed
      3 turn_queued_next_dispatch                  3 turn_queued_behind_pending
```

Several things are working, and it is as important to record those as the defect:

- **`stt_resilient_wrapper_active` ×4** — the STT failover wrapper is engaged on every call, not
  bypassed.
- **`tts_inference_warmed` ×3** — TTS pre-warming is running, so the first chunk is not paying a cold
  connection cost.
- **`turn_queued_behind_pending` / `turn_queued_next_dispatch`** — the turn arbiter is actively
  serialising overlapping turns rather than letting them race. This is the machinery that stops the
  agent talking over itself, and it fired in real traffic.
- **`eot_threshold` / `eot_timeout_ms`** — endpointing is configured per call, not defaulted.

And the completed call:

```
11:26:52 AsteriskAdapter: session ended channel=talky-out-34 reason=StasisEnd
11:26:52 AsteriskAdapter: session ended channel=talky-out-34 reason=ChannelHangupRequest
11:26:52 save_call_transcript_on_hangup persisted calls.id=780e12d7 turns=9 words=62
11:26:52 job_completion job=da74da34 final=completed disposition=answered
11:26:52 Call 780e12d7 status updated: CallOutcome.ANSWERED
```

**Nine turns and a persisted transcript.** Despite a truncated greeting, the conversation ran, the
turn loop held, the hangup was clean, and the post-call chain — the *real* one,
`save_call_transcript_on_hangup`, not the dead `post_call_analyzer.py` — persisted the transcript.
This corroborates revision **R-3** from live evidence: the chain that actually runs is
`lifecycle → call_transcript_persister → call_summary`.

62 words across 9 turns is about 7 words per turn. Short, clipped exchanges — consistent with a call
that opened badly and never recovered its footing.

### 1.5 Errors

```bash
journalctl -u talky-api --since '2026-07-20 11:20' --until '2026-07-20 11:35' -p warning --no-pager
```
```
-- No entries --
```

**Zero warnings or errors across four calls.** The greeting defect produced no error line — which is
precisely why it survived to be found by reading `interrupted=True` rather than by an alert. A
correctness bug that logs nothing is invisible to every monitoring layer this system has.

Worth carrying into DOC-09: `interrupted=True` on a greeting is not an error condition in the code's
own judgement, and it should be. A greeting cut off inside its first second is never legitimate.

### 1.6 Historical trend — the fix is not yet visible

Looking further back at every greeting completion on record:

| Date | Call | `elapsed_ms` | `interrupted` |
|---|---|---|---|
| Jul 10 | `f251d5eb` | 1816 | **True** |
| Jul 14 | `abc695c7` | 1227 | False |
| Jul 17 | `f4de3024` | 2451 | False |
| Jul 20 | `eb97b829` | 1107 | **True** |
| Jul 20 | `b69305d3` | 906 | **True** |

The pattern is **intermittent, not monotonic**. Two clean greetings on 14 and 17 July sit between
three truncated ones. That matters for how the fix gets validated: a single successful call proves
very little against a defect that already appeared to pass twice by chance. Two of five historical calls
completed their greeting without any fix at all.

**A single clean validation call is therefore weak evidence.** Two or three, or one call with a
deliberate mid-greeting barge-in to prove the opposite direction, is the minimum that would actually
distinguish "fixed" from "lucky".

### 1.7 The deployment timeline that makes this ticket urgent

```
2026-07-20 11:25   last real calls on this box  ← the evidence above
2026-07-21 06:43   deploy — dialer liveness, watchdog
2026-07-21 07:53   deploy — passkeys, cleanup worker
2026-07-21 08:58   deploy — Stripe prod-gate, tenant defence
                   … including 33fee92c, the greeting echo-immunity fix
2026-07-24         (today) — still zero calls since 07-20
```

Everything shipped in that wave — including the fix whose entire justification is the
`interrupted=True` evidence above — is **unvalidated on real audio**. Nothing has been heard.

---

## Part 2 — the live validation call · **NOT DONE, awaiting authorisation**

This half cannot proceed without a human decision, and deliberately so: it places a real telephone
call to a real number.

### What is needed before dialling

1. **The destination number, named by the user.** The ticket records `+442046132300` as
   known-good, with caller-ID `+442046132301`. **Both must be re-confirmed** — an earlier
   `+4420461323` was one digit short and produced a silent failure class that took real time to
   diagnose.
2. **Confirmation of the tenant and campaign context** — `info@allstateestimation.co.uk`
   (`790ca2db-6696-4fe9-9a2c-cd690c414a1e`).
3. **A decision on the LLM.** The ticket specifies `qwen/qwen3.6-27b`. That model was withdrawn once
   before, on 2026-06-27, for evading the AI-disclosure question and hallucinating. If it is used, the
   disclosure question must be asked directly on the call, and a rollback is a legitimate outcome.

### Absolute constraints

- ❌ **Never start a campaign to test.** The allstate campaign holds ~691 real leads. One call, to one
  number, placed deliberately.
- ❌ Do not restart any service mid-call.
- ❌ Do not change AI Options without saying which setting changed and why.

### What Part 2 must prove — both directions

| # | Test | Expected | Why it matters |
|---|---|---|---|
| 1 | Greeting completes | `interrupted=False` | The fix's entire purpose |
| 2 | Time to first agent audio after answer | recorded either way | No budget is established; this call sets it |
| 3 | **Caller barges in mid-greeting** | agent **does** yield | **Equally important as test 1** |
| 4 | Post-hangup | `ENDED`, transcript persisted, summary generated | The chain from §1.4, end to end |
| 5 | `journalctl -p err` during the call | no entries | Baseline is zero, from §1.5 |
| 6 | AI-disclosure asked directly | answered honestly | Compliance, and Qwen's specific history |

**Test 3 is not optional.** The greeting fix works by making the agent ignore its own echo. If that
immunity over-fires, the agent stops hearing the human — trading a truncated greeting for a deaf
agent, which is strictly worse. One call must demonstrate both that the agent finishes its greeting
when undisturbed *and* that it still yields when genuinely interrupted. Proving only test 1 would be
a false pass.

### Recommendation

Given §1.6 — two of five historical greetings completed cleanly with no fix present — **one call is
not enough to call this proven.** Proposed minimum:

1. **Call A:** undisturbed. Expect `interrupted=False`.
2. **Call B:** deliberate barge-in ~1.5 s into the greeting. Expect the agent to yield promptly.

Two calls, one number, both authorised in advance. Archive both recordings — **VID-14 and VID-16 both
need real call material**, and capturing it here costs nothing extra.

---

## Checklist — TKT-004

- [x] 2026-07-20 11:25–11:30 calls pulled and analysed
- [ ] Call setup confirmed with the user before dialling — **BLOCKED, awaiting authorisation**
- [ ] Caller-ID verified as 12 digits — to be done at dial time
- [ ] One call placed; full log window captured — **BLOCKED**
- [ ] Greeting: `outbound_greeting_presynth_done … interrupted=False` — **BLOCKED**
- [ ] Turn-taking: no dead air, no talk-over, no self-interrupt — **BLOCKED**
- [ ] Qwen answers checked for AI-disclosure evasion — **BLOCKED**
- [ ] Recording archived for VID-14/VID-16 — **BLOCKED**
- [x] `docs/v2/validation-call-2026-07-24.md` written (Part 1)
- [ ] Peer-reviewed

## Findings raised

| ID | Sev | Finding |
|---|---|---|
| **F-19** | 🟠 High | Greeting truncation confirmed on **both** 2026-07-20 calls — `interrupted=True` at 1107 ms and 906 ms, each immediately preceded by `presynth_greeting_barge_in_post_send`. Self-echo barge-in. Fix shipped 07-21, **unvalidated**. |
| **F-20** | 🟡 Med | A greeting interrupted inside its first second logs at **INFO** and raises nothing. Zero warnings across four calls including two broken greetings. Invisible to every monitoring layer. Should be a warning at minimum. |
| **F-21** | 🟡 Med | **No call has been placed on this box since 2026-07-20.** Seven deploys have landed since, unheard. |
| **F-22** | ⚪ Low | Greeting truncation is **intermittent** — 2 of 5 historical greetings completed cleanly with no fix. A single clean validation call is therefore weak evidence of a fix. |
