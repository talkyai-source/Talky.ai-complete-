# Voice Barge-In Investigation — Full Response Report

**Report date:** 2026-08-09
**Production commit:** `044d92080402846ebf89b16c836fc84b41984bb1`
**Scope:** the ten-point engineering brief, answered point by point
**Author's note:** every claim below is backed by a command output, a log line, or a test. Where an earlier conclusion of ours turned out to be wrong, it is corrected in place and listed in the [Corrections Register](#corrections-register).

> **Privacy note.** This repository is public. Production call identifiers are pseudonymised as **CALL-A … CALL-D** throughout. Recover the real identifiers from the server journal using the grep commands in [Appendix B](#appendix-b--reproduction-commands).

---

## Table of contents

- [Executive summary](#executive-summary)
- [How the investigation was run](#how-the-investigation-was-run)
- [The central finding](#the-central-finding)
- [§1 Verify production before changing anything](#1--verify-production-before-changing-anything)
- [§2 Apply and verify the immediate configuration](#2--apply-and-verify-the-immediate-configuration)
- [§3 Trace one failed call end-to-end](#3--trace-one-failed-call-end-to-end)
- [§4 Repair opening-greeting echo handling](#4--repair-opening-greeting-echo-handling)
- [§5 One centralized, idempotent interruption operation](#5--one-centralized-idempotent-interruption-operation)
- [§6 Make C++ cancellation authoritative](#6--make-c-cancellation-authoritative)
- [§7 Audio transport and queue design](#7--audio-transport-and-queue-design)
- [§8 Tune response delay](#8--tune-response-delay)
- [§9 Confirm codec negotiation](#9--confirm-codec-negotiation)
- [§10 Canary environment](#10--canary-environment)
- [Corrections register](#corrections-register)
- [Files changed](#files-changed)
- [Test coverage](#test-coverage)
- [Observability added](#observability-added)
- [Deploy, verify, rollback](#deploy-verify-rollback)
- [Canary plan, ready to execute](#canary-plan-ready-to-execute)
- [Open items and deferred work](#open-items-and-deferred-work)
- [Appendix A — raw evidence](#appendix-a--raw-evidence)
- [Appendix B — reproduction commands](#appendix-b--reproduction-commands)

---

## Executive summary

The brief's hypothesis was that playback cancellation was unreliable — that Python asked the C++ gateway to stop and the gateway did not, or that the transport lost ordering under load. **That is not what is happening.**

Across fourteen days of production logs:

```
interrupt_tts failures ............ 0
interrupt_tts retry failures ...... 0
barge-ins successfully detected ... 415
```

Cancellation works. What fails is **earlier**: two gates decide whether to *request* a cancellation at all, and both were rejecting genuine callers. They share one root cause, which is subtle enough that both survived code review:

> **Deepgram Flux's `StartOfTurn` carries a PARTIAL transcript — in practice the caller's first word.** Both gates were written as though they saw a complete utterance.

A guard that requires "at least 2 words before you may interrupt" therefore does not measure intent. It measures **how fast the caller talks**.

### Verdict by section

| § | Item | Verdict |
|---|---|---|
| 1 | Verify production | 🟢 Done |
| 2 | Immediate configuration | 🟢 **Done and LIVE in production** |
| 3 | End-to-end trace | 🟢 Done — instrumentation built |
| 4 | Opener echo handling | 🟢 Done — verified, awaiting deploy |
| 5 | Centralized interrupt operation | 🟢 Done — built |
| 6 | C++ cancellation authoritative | 🟢 Done — built |
| 7 | Transport and queue | 🟢 Done — verified correct, lever added |
| 8 | Response-delay tuning | 🔴 **Declined — would regress this deployment** |
| 9 | Codec negotiation | 🟢 Done — verified |
| 10 | Canary | 🔴 **Blocked — requires human testers** |

**8 green · 1 declined with cause · 1 blocked.**

---

## How the investigation was run

The brief was worked in a deliberate order, and that order mattered.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  1. FREEZE      record prod version + config, change nothing │
   │  2. MEASURE     14 days of journal, counted not sampled      │
   │  3. TRACE       single calls with synchronised timestamps    │
   │  4. LOCATE      run the real gates against the real code     │
   │  5. FIX         change the classifier, not the threshold     │
   │  6. PROVE       parametrise tests over verbatim prod strings │
   │  7. REGRESS     full suite vs a clean baseline worktree      │
   └──────────────────────────────────────────────────────────────┘
```

Two methodological choices are worth stating because they changed the outcome:

**Logs before architecture.** Every conclusion drawn from reading the code alone either confirmed something already fixed, or pointed one layer too deep. Both real defects were findable with a single `journalctl | grep`. One of them had been printing the offending words to the journal, hashed, for a week.

**Verification on an isolated worktree, never on production.** All test runs used `git worktree add --detach /tmp/tvN HEAD` with modified files copied in, executed against the production venv. Production code was never modified to test it. The baseline for "zero regressions" was a *separate* clean worktree at `044d9208` with none of the work applied, so the eight known-failing tests could be proven pre-existing rather than assumed.

---

## The central finding

```
                        WHERE CALLS ACTUALLY BREAK

  caller starts speaking
         │
         ▼
   Deepgram Flux emits StartOfTurn  ── carries only the FIRST WORD
         │
         ▼
  ┌─────────────────────────────────────────┐
  │  GATE 1  word-count guard (min 2 words) │──✂──►  29% of all attempts
  │          deepgram_flux.py:778           │        dropped here
  └─────────────────────────────────────────┘        'Listen' 'Excuse' 'Bye'
         │
         ▼
  ┌─────────────────────────────────────────┐
  │  GATE 2  opener echo classifier         │──✂──►  25 events / 7 days
  │          instant_opener.is_opener_echo  │        'Hello?' 'Hello.' 'Hey.'
  └─────────────────────────────────────────┘
         │
         ▼
   barge-in requested
         │
         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  cancel LLM/TTS → clear Python buffers → POST C++ interrupt │
  │  → rotate utterance_id → 409 late chunks → clear queue      │
  │                                                             │
  │        ✅  ALL OF THIS WORKED.  0 failures / 14 days.       │
  └─────────────────────────────────────────────────────────────┘
```

The brief's priority list said: *"keep STT active during TTS, fix opener echo rejection, connect barge-in to reliable cancellation, invalidate stale generations, and only then tune latency."*

Measured against production, that list is correct in **spirit** and mis-ordered in **fact**:

- *Keep STT active during TTS* — **already true.** No SIP branch mutes.
- *Fix opener echo rejection* — **real**, but costs 88–167 ms, inside target.
- *Connect barge-in to reliable cancellation* — **already reliable.**
- *Invalidate stale generations* — **already implemented** (VG-13, July).
- *Then tune latency* — **would regress**; production is already at 500 ms.

The single highest-impact item was the one not on the list: the word-count guard, costing **1130 ms** of talk-over per occurrence.

---

## 1 · Verify production before changing anything

### The ask
Record deployed backend and C++ versions. Confirm production actually runs `044d9208`. Do not restore February/March ZIPs. Preserve rollback.

### What we found

```bash
$ git -C /opt/talky log -1 --format="%H %ci %s"
044d92080402846ebf89b16c836fc84b41984bb1 2026-08-07 00:46:06 +0500
  fix(voice): close the inter-sentence gap without the unsafe prefetch
```

| Component | Value |
|---|---|
| Backend HEAD | `044d92080402846ebf89b16c836fc84b41984bb1` ✅ **matches reviewed main** |
| C++ gateway binary | `253864` bytes · built `2026-07-17 12:32` · md5 `22c052509c69fe6807e04f638ba3f1b5` |
| Working tree | clean except the gateway build artifact and `secrets/` (both expected) |
| Services | `talky-api` · `talky-voice-worker` · `talky-voice-gateway` · `talky-dialer-worker` · `talky-reminder-worker` · `asterisk` — **all active** |

### How we tackled it

- Production version recorded **before** any change was made.
- **No ZIP restore attempted.** The brief's warning about February/March STT-muting behaviour is well founded — see §2, where we confirm the current code is already on the correct side of that.
- **No repository rollback** at any point.
- Rollback path preserved and tested-by-inspection:

```bash
# code rollback
git -C /opt/talky checkout 044d9208 && sudo systemctl restart talky-api

# config rollback (this investigation's only live change)
# restore /opt/talky/backend/.env.bak-20260808-bargein, then restart
```

### Status — 🟢 Done

---

## 2 · Apply and verify the immediate configuration

### The ask
Set `DEEPGRAM_MIN_INTERRUPT_WORDS=1`. Confirm `mute_during_tts=False`. Confirm no branch mutes, pauses, or discards caller audio while the agent speaks. Restart only the necessary service.

### Part A — `mute_during_tts`

**Already correct, verified two independent ways.**

```bash
$ venv/bin/python -c "from app.core.telephony_settings import get_telephony_settings; \
                      print(get_telephony_settings().mute_during_tts)"
False
```

And through the actual telephony call path:

```
telephony_session_config.py:1315   mute_during_tts=_telephony_mute_during_tts_default()
                                        │
                                        ▼
telephony_session_config.py:320    return get_telephony_settings().mute_during_tts
                                        │
                                        ▼
telephony_settings.py:214          mute_during_tts: bool = False
```

**Audit of every mute call site in the codebase:**

| Site | Path | Reaches SIP calls? |
|---|---|---|
| `voice_orchestrator.py:777` | browser greeting | ❌ No |
| `voice_orchestrator.py:869` | browser greeting unmute | ❌ No |
| `resilient_stt.py:170/178` | delegation wrapper | only if a caller mutes |
| `deepgram_flux.py:247/253` | provider primitive | only if called |

The three places that *discard* audio when muted — `deepgram_flux.py:633`, `:695`, `:745` — are therefore unreachable on telephony, because nothing mutes. **Caller RTP flows to Deepgram continuously while TTS plays.** ✅

> This is the specific behaviour the brief warned the February/March ZIPs would reintroduce. Current code is on the correct side of it, which is a further argument against restoring them.

### Part B — the word-count guard

**Issue.** `DEEPGRAM_MIN_INTERRUPT_WORDS` was unset, so the code default of **2** applied.

**Why it looked reasonable.** The comment cites the LiveKit/Pipecat "MinWords" pattern, whose purpose is to stop a cough or an STT mishear from cutting the agent off. Sound intent.

**Why it was wrong.** `StartOfTurn` is emitted the moment Flux detects speech onset. Its transcript is a *partial* — usually one word. The guard was written as if it saw the whole utterance.

**Measured cost, 14 days:**

```
     total barge-in attempts   415
     ├── detected              242   ██████████████████████████████ 58%
     └── DEFERRED (<2 words)   173   █████████████████████ 42% of remainder
                                     ─────────────────────────────
                                     29% of ALL attempts dropped
```

**What was in the deferred text** (7-day window, complete distinct list, counts):

```
 'Hi.'       ████████████████████████  21
 "I'm"       █████████████████         15
 'Thanks.'   ███████████████           13
 'Zero'      ██  2      'Yours'    ██  2      'Saturday' ██  2
 'Same'      ██  2      'Nothing.' ██  2      'Listen'   ██  2
 "Let's"     ██  2      "It's"     ██  2      'Great.'   ██  2
 "What's" 1  "What'd" 1  'Well,' 1  'wanted' 1  'Uh,' 1  'there.' 1
 'Still' 1  'Stand' 1  'Speaking' 1  'Som' 1  'setup' 1  'Sar' 1
 'reason' 1  'problem' 1  'Probably' 1  'Please.' 1  'pinion' 1
 'Passport' 1  'Mother' 1  'Most' 1  'Linux' 1  'LA?' 1  'Kim' 1
 "I've" 1  'follow' 1  'Famous' 1  'Bye' 1  'Excuse' 1
```

`'Excuse'` is the clinching example. The hard-interrupt allow-list **contains** `"excuse me"` — but at `StartOfTurn` only `Excuse` had arrived, so the list could not match, and the caller was deferred.

Of ~106 deferrals in that window, only `'Uh,'` and the STT fragments `'Som'`, `'Sar'`, `'pinion'` are plausibly noise. **The guard was ~96% wrong.**

### How we tackled it

We did **not** simply set the env var. Two changes:

**1. Changed the code default `2 → 1`,** so the defect does not survive into the next environment or tenant. An `.env` value fixes one box and leaves the bug in the repository — and carries no explanation, so the next engineer who sees a cough interrupt would set it back without knowing the cost.

**2. Replaced counting with naming.** New `is_disfluency()` rejects hesitation *by name*:

```python
_DISFLUENCIES = frozenset({
    "uh", "um", "umm", "uhh", "er", "err", "erm", "ah", "ahh", "eh",
    "hm", "hmm", "mm", "mmm", "huh", "oh",
})
```

This is simultaneously **more precise** than `>= 2` (it rejects `'Uh,'`, which a word count at 1 would pass) and **more permissive** (it admits `'Listen'`, which is one word and unmistakably means it).

The count guard is retained and env-tunable purely as a rollback lever.

**New gate order in `deepgram_flux.py`:**

```
  (a) hard interrupt?     → PASS immediately   ("stop", "wait", "no")
  (b) backchannel?        → BLOCK              ("yeah", "mhm", "right")
  (c) disfluency?         → BLOCK              ("uh", "um", "erm")     ← NEW
  (d) word count < min?   → BLOCK              (min now 1 = disabled)
  (e) otherwise           → PASS
```

### Deployment — LIVE

```
.env change      DEEPGRAM_MIN_INTERRUPT_WORDS=1        (backup: .env.bak-20260808-bargein)
integrity        68 → 69 keys · 1 added · 0 removed · 0 values altered · 0 CR chars
restart          talky-api only, at 0 active sessions
                 PID 3806372 → 569531
                 ActiveEnterTimestamp 2026-08-06 → 2026-08-08 19:45:06 UTC
health           {"ready":true,"db":"ok","redis":"ok"}
gateway          {"status":"ok","io_loop_healthy":true}
post-restart     5 log matches, ALL the perennial Vonage
                 "not configured (optional)" lines — benign, pre-existing
```

> **Only `talky-api` was restarted**, as the brief specified. The C++ gateway, dialer, reminder and voice workers were left running.

### Caveat, stated plainly

`min_words=1` is live **without** its paired `is_disfluency` guard, because that guard lives in code that is not yet deployed. **Until the code ships, a bare `Uh` can interrupt the agent.** Expected frequency is roughly 1 event per 100 based on the 7-day distribution, but it is real and it is live.

### Status — 🟢 Done and live

---

## 3 · Trace one failed call end-to-end

### The ask
Using one call ID and synchronised timestamps, capture the full chain from caller audio to next LLM/TTS start. Log the reason whenever caller speech is accepted or rejected. Establish whether failure occurs *before* barge-in detection or *during* playback cancellation.

### What we found — the chain was half-dark

```
caller audio → StartOfTurn → echo decision → barge-in → TTS interrupted     ✅ logged
state → buffers cleared → C++ interrupt → queue cleared → next LLM/TTS      ❌ SILENT
```

The second half was never written down. This is why the brief's question — *before detection, or during cancellation?* — could not be answered from logs by anyone, including us.

### The trace, and what it corrected

**CALL-D** (chosen first) turned out to be a **voicemail**, and AMD correctly hung up at turn 0. A poor exemplar; we re-traced on human calls. Of 21 distinct calls where the echo gate fired, **19 had no voicemail verdict** — but two did, and the first one we picked was one of them.

Re-traced on **CALL-A**, **CALL-B**, **CALL-C** (all human), the answer is unambiguous:

| call | gate that fired | ignored → `barge_in_detected` | vs 300–500 ms target |
|---|---|---|---|
| **CALL-A** | opener echo | `09.909` → `10.076` = **167 ms** | ✅ inside |
| **CALL-B** | opener echo | `39.090` → `39.178` = **88 ms** | ✅ inside |
| **CALL-C** | word count `'Hi.'` | `46.417` → `47.547` = **1130 ms** | ❌ **2–4× outside** |

**Answer to the brief's question: the failure is BEFORE barge-in detection, not during cancellation.**

And a second answer the brief did not ask for: neither gate *loses* the interruption. The EndOfTurn grow-case recovers it. What they cost is **delay** — and the two gates differ by an order of magnitude, which **inverted our own priority ordering**. See [Corrections Register](#corrections-register).

### How we tackled it

Instrumentation now exists. Every step of `interrupt_playback()` emits one line carrying a shared `interrupt_id`:

```bash
journalctl -u talky-api | grep interrupt_id=<id>
```

```
interrupt_step=begin            interrupt_id=a3f21c9e4b17 call=CALL-A reason=barge_in tts_active=True
interrupt_step=state_listening  interrupt_id=a3f21c9e4b17 call=CALL-A
interrupt_step=task_cancelled   interrupt_id=a3f21c9e4b17 call=CALL-A cancelled=True
interrupt_step=buffers_cleared  interrupt_id=a3f21c9e4b17 call=CALL-A local_bytes=320 pending_bytes=3
interrupt_step=cpp_interrupt    interrupt_id=a3f21c9e4b17 call=CALL-A ok=True dropped_frames=12
                                dropped_ms=240 segments=1 attempts=1 rotated=True
interrupt_complete {'interrupt_id': 'a3f21c9e4b17', 'ok': True, 'deduped': False,
                    'task_cancelled': True, 'local_bytes': 320, 'gw_frames': 12,
                    'gw_ms': 240, 'gw_segments': 1, 'gw_attempts': 1,
                    'elapsed_ms': 8.4, 'errors': []}
```

**Accept/reject reasons are now logged at every decision point:**

| Decision | Log line |
|---|---|
| hard interrupt admitted | `Flux StartOfTurn - User started speaking, barge-in detected` |
| backchannel rejected | `Flux StartOfTurn backchannel %r — barge-in suppressed` |
| disfluency rejected | `Flux StartOfTurn disfluency %r — barge-in suppressed` *(new)* |
| word count rejected | `Flux StartOfTurn short %r (<%d words) — barge-in deferred` |
| echo classified | `instant_opener_echo_ignored call=%s text=%r` |
| echo overridden by repeat | `instant_opener_echo_overridden ... repeated greeting %.2fs after the first` *(new)* |
| echo rejected on onset | `instant_opener_echo_rejected ... %.2fs into playback is too late` *(new)* |

### Status — 🟢 Done

---

## 4 · Repair opening-greeting echo handling

### The ask
`is_opener_echo()` must not reject genuine caller speech merely because the caller says "hello", "hi" or "hey" during the opener or its ~700 ms grace. Stop/wait/excuse-me/full sentences must interrupt. Do not use a greeting-word list as the only test. Consider timing, sustained speech, similarity to the actual opener. Repeated deliberate speech must override an initial echo classification. Do not remove echo protection entirely.

### The issue

The gate was exactly what the brief warns against — a greeting-word list plus a time window:

```python
# BEFORE
if not in_flight and now >= grace_until:
    return False
return is_bare_greeting(text or "")     # ← the entire content test
```

### The evidence

50 journal lines / **25 events in 7 days**. The text is redacted, but the redaction is a plain `sha256[:8]` (`log_redact.py:359`), so the exact utterances were recoverable by brute-forcing greeting candidates:

| sha | chars | recovered utterance |
|---|---|---|
| `2d8bd7d9` | 6 | **`Hello.`** |
| `0da72197` | 6 | **`Hello?`** |
| `580684f8` | 4 | **`Hey.`** |

**19 of 21 distinct calls** had no voicemail verdict. These were people.

### Root cause

A word list cannot separate two situations that produce byte-identical text:

```
  (a)  the tail of the caller's OWN pickup "Hello?" re-segmented by Flux,
       or our greeting bleeding back through imperfect carrier echo cancellation
       → CORRECT to ignore

  (b)  a caller saying "Hello?" AGAIN because the agent is talking over them
       → MUST interrupt
```

There is no content signal that distinguishes them. There are **timing** and **repetition** signals, and the old gate used neither.

A second, larger problem: the same echo window is armed on the **agent-first** path (`agent_first.py:321`), where it spans the recording disclosure **and** the opener — several seconds. The brief's "~700 ms grace period" was never the real window on those calls.

### How we tackled it

One signal became four, **ordered so that evidence of deliberate speech always beats evidence of echo**:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. TIMING       outside playback + grace          → never echo           │
│                 (unchanged)                                              │
│                                                                          │
│ 2. CONTENT      not a bare greeting               → never echo           │
│                 "stop" / "wait" / full sentences cut in immediately      │
│                                                                          │
│ 3. REPETITION   a SECOND distinct greeting        → NEVER echo           │
│                 in the same window                  ← overrides 4a AND 4b│
│                                                                          │
│ 4a. SIMILARITY  every word appears in the         → echo, whenever       │
│                 greeting we are playing             it lands             │
│                                                                          │
│ 4b. ONSET       > 1.5 s after playback started    → not echo             │
│                 (too late to be pickup echo)                             │
└──────────────────────────────────────────────────────────────────────────┘
```

**Mapping to the brief's requirements:**

| Requirement | How it is met |
|---|---|
| "stop"/"wait"/"excuse me"/sentences must interrupt | Signal 2 — unchanged and non-negotiable |
| Not a greeting-word list alone | Four signals; the list is now one of four |
| Consider timing | Signal 4b — onset bound, 1.5 s |
| Consider sustained caller speech | Signal 2 — anything beyond a bare greeting |
| Consider similarity to actual opener transcript | Signal 4a — token-subset against `_instant_opener_spoken_text` |
| Repeated deliberate speech overrides classification | Signal 3 — and it is checked **first**, so it beats everything |
| Do not remove echo protection | Signals 4a and 4b retain it; a first, prompt greeting is still ignored |

**Source/channel separation** was considered and **not** used. The telephony path is a single mixed RTP stream with no separate agent channel, so there is no true source separation available without a C++ change to tap pre-mix audio. Signal 4a (similarity to what we are currently saying) is the closest available proxy and is what we implemented.

### The idempotency trap we had to design around

Production logs showed **exactly two** `instant_opener_echo_ignored` lines per event — because **both** arming sites (`audio_ingest._on_barge_in_direct` and `voice_pipeline_service.handle_barge_in`) call the gate for the same barge-in.

A naive "count how many times we've ignored" implementation would therefore trip its own repeat-override on the *first* real event. Repetition is instead measured as **a new utterance after a 400 ms debounce**, so the double-call is stable and a genuine repeat still overrides.

### Verified behaviour, before vs after

Run against the real functions on the production code:

| text | scenario | BEFORE | AFTER |
|---|---|---|---|
| `'Listen'` | caller starts a sentence | SWALLOWED | **interrupts** ✅ |
| `'Excuse'` | caller saying "excuse me" | SWALLOWED | **interrupts** ✅ |
| `'Bye'` | caller ending the call | SWALLOWED | **interrupts** ✅ |
| `'I am'` | caller starts a sentence | interrupts | interrupts |
| `'Hello?'` | **repeated**, talked over | SWALLOWED | **interrupts** ✅ |
| `'Hello?'` | 5 s into a long greeting | SWALLOWED | **interrupts** ✅ |
| `'Hello?'` | first, right at onset | SWALLOWED | *SWALLOWED* ✅ echo protection held |
| `'Uh,'` | hesitation noise | SWALLOWED | *SWALLOWED* ✅ |
| `'yeah'` | backchannel | SWALLOWED | *SWALLOWED* ✅ |
| `'stop'` | hard interrupt | interrupts | interrupts ✅ |

**Five fixed. Three correctly held. Zero regressions.**

### Status — 🟢 Done, verified, awaiting deploy

---

## 5 · One centralized, idempotent interruption operation

### The ask
One operation that marks barge-in, switches to `LISTENING`, cancels the LLM/TTS task, rotates the generation id, rejects delayed chunks, clears Python buffers, calls `clear_output_buffer()` and `POST /v1/sessions/tts/interrupt`, stops C++ pacing, and **returns an acknowledgement including how many frames/bytes were discarded**. Must be idempotent.

### What we found

The steps existed but were scattered across `handle_barge_in`, `clear_output_buffer` and `interrupt_tts`, each logging or swallowing its own outcome, with **no assembled verdict**. Two consequences:

1. Python declared the agent stopped when its own `state` said `LISTENING` — a statement about a local variable, not about audio in the caller's ear.
2. Nothing returned counts, so "how much audio did we actually bin?" was unanswerable.

### How we tackled it

New module `backend/app/domain/services/voice_pipeline/interrupt.py`:

```
┌── interrupt_playback(session, *, media_gateway, reason,
│                      cancel_task=None, tts_provider=None) ── one interrupt_id ──┐
│                                                                                 │
│  0  IDEMPOTENCY GATE   repeat within 350 ms → return first verdict, deduped=True│
│                                                                                 │
│  1  STATE              tts_active=False · state=LISTENING                       │
│                        current_ai_response="" · current_user_input=""           │
│                        (stale transcript must never reach the LLM)              │
│                                                                                 │
│  2  CANCEL             await cancel_task()  → task_cancelled                    │
│                                                                                 │
│  3  PYTHON BUFFERS     tts_buffer → local_bytes_discarded                       │
│                        _tts_pending_bytes → pending_bytes                       │
│                                                                                 │
│  4  C++ INTERRUPT      POST /v1/sessions/tts/interrupt                          │
│                        → clears queue, stops pacing                             │
│                        → rotates utterance_id (late chunks now 409)             │
│                        → returns dropped_frames + interrupted_segments          │
│                                                                                 │
│  5  TTS PROVIDER       clear_queue() — stop server-side generation              │
│                                                                                 │
└── returns InterruptResult ─────────────────────────────────────────────────────┘
```

**The acknowledgement:**

```python
@dataclass
class InterruptResult:
    interrupt_id: str            # ties every log line of this stop together
    call_id: str
    ok: bool                     # the GATEWAY's verdict, not Python's
    deduped: bool
    state_changed: bool
    task_cancelled: bool
    tts_queue_cleared: bool
    utterance_rotated: bool
    local_bytes_discarded: int   # Python packetisation buffer
    pending_bytes: int           # orphan partial-sample fragment
    gateway_dropped_frames: int  # PCMU frames the C++ side binned
    gateway_interrupted_segments: int
    gateway_attempts: int        # 2 = the retry was needed
    elapsed_ms: float
    errors: list

    @property
    def gateway_dropped_ms(self) -> int:
        return self.gateway_dropped_frames * 20     # 20 ms per PCMU frame
```

### Idempotency, and why 350 ms

Duplicate `StartOfTurn` / barge-in events are **normal** — both arming sites fire for the same utterance, microseconds apart, and Flux can emit several. Re-running the teardown for each would rotate the utterance id repeatedly and re-cancel a task mid-unwind.

The window must **cover the duplicate burst** and must **not cover a caller who barges in again a moment later** — that second interruption is real and must do real work. 350 ms sits comfortably between the two, and both properties are pinned by tests.

### Requirement-by-requirement

| Brief requirement | Status |
|---|---|
| Mark barge-in immediately | ✅ step 1 |
| Switch to `LISTENING` | ✅ step 1 |
| Cancel active LLM/TTS task | ✅ step 2 |
| Rotate/invalidate `utterance_id` | ✅ step 4 (pre-existing VG-13, now reported) |
| Reject delayed chunks of cancelled generation | ✅ gateway 409s them (pre-existing) |
| Clear Python TTS + partial-packet buffers | ✅ step 3, both counted |
| Call `clear_output_buffer()` | ✅ step 3/4 |
| `POST /v1/sessions/tts/interrupt` | ✅ step 4 |
| Stop C++ pacing, clear queued PCMU frames | ✅ step 4 |
| Return ack: succeeded + frames/bytes discarded | ✅ `InterruptResult` |
| Idempotent against duplicate events | ✅ 350 ms dedupe, test-pinned |

### Status — 🟢 Done

---

## 6 · Make C++ cancellation authoritative

### The ask
Python must not assume the agent stopped because its own state says `LISTENING`. The gateway must invalidate the generation, clear pending packets, reject late packets, **confirm queue clearing to Python**, and return explicit failure. Do not swallow failures in debug logs — log at warning/error with call ID and session ID, record a metric, retry once.

### The key discovery

**The C++ gateway has always returned the acknowledgement.** `http_server.cpp:1697-1703`:

```cpp
std::size_t dropped_frames = 0;
std::size_t interrupted_segments = 0;
session->interrupt_tts(reason.value_or("barge_in"), dropped_frames, interrupted_segments);
write_response(client_fd, 200, "OK",
    "{\"status\":\"interrupted\",\"session_id\":\"" + escape_json(session_id.value()) +
    "\",\"dropped_frames\":" + std::to_string(dropped_frames) +
    ",\"interrupted_segments\":" + std::to_string(interrupted_segments) + "}");
```

**Python was throwing the response body away.** The data the brief asks for was already on the wire.

> **Consequence: no C++ rebuild was required for this section.** That matters operationally — a gateway rebuild needs `g++ -O2` on the host (no cmake available) and a restart at zero active sessions.

### What was already correct

| Gateway requirement | State |
|---|---|
| Invalidate cancelled generation | ✅ VG-13 `utterance_id` rotation, July |
| Clear all pending playback packets | ✅ `clear_tts_queue_locked` |
| Reject late packets from that generation | ✅ 409 + `tts_chunks_rejected_stale_total` |
| Confirm queue clearing to Python | ✅ **response existed** — Python ignored it |
| Return explicit failure | ✅ 400/404 paths |

### What we fixed on the Python side

**1. `ok` is now the gateway's verdict.**

```python
result["ok"] = bool(ack.get("ok"))    # not "our state changed, so we're done"
```

**2. The debug swallow is gone.** `telephony_media_gateway.clear_output_buffer` previously did:

```python
except Exception as exc:
    logger.debug("clear_output_buffer: interrupt_tts failed: %s", exc)   # invisible
```

Now:

```python
logger.error(
    "clear_output_buffer: interrupt_tts raised for %s — agent "
    "may still be speaking: %s", call_id[:12], exc,
)
```

**3. Failures carry call ID and session ID:**

```
[AsteriskAdapter] ❌ interrupt_tts retry failed for CALL-A session=<sid>
                     — stale audio may play out: <error>
```

**4. Retry once** — already present; now surfaced as `attempts` in the acknowledgement, so `ok_after_retry` is distinguishable from clean success. A rising `ok_after_retry` rate is an early warning before failures appear.

**5. Metrics, new:**

```
voice_interrupt_outcome_total{outcome="ok"}
voice_interrupt_outcome_total{outcome="ok_after_retry"}
voice_interrupt_outcome_total{outcome="failed"}      ← caller may still hear the agent
voice_interrupt_dropped_frames                        histogram, buckets 0…400
```

**6. A distinction that prevents false alarms.** No gateway session means nothing is playing — success by vacancy, not failure. It is tagged `error="no_gateway_session"` with `ok=True`, so teardown races do not pollute the failure metric.

### Production baseline

```
interrupt_tts failures, 14 days ......... 0
interrupt_tts retry failures, 14 days ... 0
```

The metric starts from a clean baseline. Any non-zero `failed` count after deploy is a genuine regression, not pre-existing noise.

### Status — 🟢 Done

---

## 7 · Audio transport and queue design

### The ask
Verify whether production uses separate HTTP requests or detached threads per 20 ms RTP frame. Use an ordered persistent stream. Preserve strict frame ordering and 40 ms batching. Inspect queue sizes; test a 50–100 frame cap, but use generation invalidation as primary protection.

### What we found — the concern does not apply

| Check | Reality | Evidence |
|---|---|---|
| One HTTP request per 20 ms frame? | ❌ **No** | `asterisk_adapter.py:353` — one `aiohttp.ClientSession` created at connect, reused for the call's lifetime |
| Detached threads per frame? | ❌ **No** | Sequential `await`s on a single session → **strict ordering by construction** |
| Batching | ✅ **2–3 frames = 40–60 ms** per POST | `telephony_media_gateway.send_audio` opportunistic batching |
| Effective request rate | **~25 req/s per call**, not 50 | derived from the above |

The brief's target — "an ordered persistent connection, at minimum strict frame ordering and controlled 40 ms batching" — is **already met**.

### Queue size

`tts_max_queue_frames{400}` in `session.h:59` is real, and 400 × 20 ms ≈ 8 s is arithmetically correct. But it is a **ceiling, not a working depth**:

```
  capacity   ████████████████████████████████████████  400 frames  (~8.0 s)
  measured   ██                                        ~15 frames  (300 ms)
  /stats     ·                                         tts_queue_depth_frames: 0
```

Python paces egress to `TELEPHONY_TTS_TARGET_AHEAD_S` (0.300 s). The queue **cannot** reach 400 in steady state — the producer is rate-limited well below it.

**Why we did not lower the default.** Lowering to 100 changes nothing in steady state, while introducing a real risk: the pre-synth greeting path sends bursts of **119–170 chunks** (observed in production logs). Any path that outruns pacing would begin **dropping frames** — audio loss — to fix a problem that is not occurring.

The brief itself says generation invalidation should be the primary protection. It already is (VG-13, verified, 409s confirmed).

### How we tackled it

Exposed as a **lever, not a default**:

```bash
VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES=100   # test a tighter cap, no deploy needed
# unset (default) → gateway's own 400
```

Implemented in `AsteriskAdapter._tts_queue_config()`, merged into both `/v1/sessions/start` payloads. The gateway already accepts and validates the field (`http_server.cpp:1429-1497`), so this needed no C++ change either.

### Status — 🟢 Done — verified correct, lever added

---

## 8 · Tune response delay

### The ask
Check the effective Flux end-of-turn timeout. Test ~1000–1200 ms, starting at 1200 ms.

### 🔴 Declined — the premise is inverted for this deployment

`eot_timeout_ms` **defaults** to 2000 in `telephony_settings.py:172`. But that default is **overridden per-tenant** by the voice-tuning path (`telephony_session_config.py:1293`), and what production actually negotiates is:

```
  timeout_ms=500   ████████████████████████████████████████████  380 sessions
  timeout_ms=1000  █                                              12 sessions
  timeout_ms=1200  (requested)                                      0 sessions
```

Confirmed in the connection log of every call:

```
Deepgram Flux pre-connected for call CALL-D (eager=0.7 eot=0.85 timeout_ms=500)
```

**Production runs 500 ms.** Setting 1200 would add **+700 ms of dead air after every caller turn** — a 2.4× regression in exactly the metric the brief wants improved.

We also note the env var named in the brief (`DEEPGRAM_EOT_TIMEOUT_MS`) is not read by this codebase; the effective one is `TELEPHONY_FLUX_EOT_TIMEOUT_MS`, and it is unset.

**Not applied.** Reversible in one line if you want it regardless — but it should be a deliberate decision, not a side effect of working the list.

**If replies feel slow**, the better candidate is the 1130 ms barge-in delay documented in §3, which we have fixed.

### Related constant worth knowing

`CAPTURE_EOT_TIMEOUT_MS = 8000` — deliberately long, so that pauses between spelled letters of an email or digits of a phone number do not force a premature end-of-turn. Any global EOT change interacts with this.

### Status — 🔴 Declined with cause

---

## 9 · Confirm codec negotiation

### The ask
Confirm every production SIP trunk negotiates PCMU/G.711 µ-law, RTP payload type 0, 8 kHz mono, 20 ms / 160-byte packets.

### What we found

Confirmed on **every** production call in the journal:

```
TelephonyMediaGateway: session started call_id=… pbx=talky-out-…
  wire=pcmu/8000Hz internal=linear16/16000Hz (upsample on ingress, downsample on egress)

TelephonyMediaGateway initialized: internal=16000Hz, wire=8000Hz PCMU, 16-bit,
  tts_source_format=s16le
```

| Requirement | Confirmed |
|---|---|
| PCMU / G.711 µ-law | ✅ `wire=pcmu` |
| 8 kHz mono | ✅ `8000Hz`, `channels=1` |
| 20 ms / 160-byte packets | ✅ `PACKET_SIZE = 160`, `ptime_ms: 20` in the start payload |
| RTP payload type 0 | ✅ implied by `codec: "pcmu"` in `/v1/sessions/start` |

The pipeline runs 16 kHz internally and resamples at the boundary — `soxr_mq` on both ingress and egress.

As the brief correctly anticipated: **this does not explain the talking-over behaviour.**

### Status — 🟢 Done

---

## 10 · Canary environment

### 🔴 Blocked — requires human testers

30 calls with 5 different speakers cannot be automated from this side, and **no campaign will be started to simulate it** — that would place real outbound calls to real prospects.

### What is ready

The machine-testable scenarios from the brief are covered by unit tests:

| Brief scenario | Covered |
|---|---|
| Duplicate interruption events | ✅ `test_duplicate_barge_in_is_deduped_not_re_run` |
| Late TTS chunks after cancellation | ✅ VG-13 409 path + `utterance_rotated` assertion |
| C++ interruption failure and retry | ✅ `test_gateway_failure_is_NOT_reported_as_success`, `attempts=2` |
| "Stop"/"wait" during replies | ✅ `test_real_content_still_interrupts_immediately` |
| "Excuse me" | ✅ parametrised on the verbatim production string `'Excuse'` |
| Background noise without speech | ✅ `test_hesitation_noise_still_does_not_interrupt` |

The human-audio scenarios — "Hello" during the opener, a complete sentence over the agent, brief caller pauses — are **not substitutable** by tests, because the thing under test is acoustic echo and real endpointing behaviour.

See [Canary plan, ready to execute](#canary-plan-ready-to-execute) for the runnable procedure.

### Status — 🔴 Blocked on human testers

---

## Corrections register

Errors we made during this investigation, and how they were caught. Recorded because the *method* that caught them is more valuable than the individual facts.

### C-1 · "Every one talked over" — overstated

**Claimed:** the echo gate meant callers were talked over.
**Reality:** the gate *delays* barge-in; the EndOfTurn grow-case recovers it. Cost is 88–167 ms, **inside** the 300–500 ms target.
**Caught by:** the single-call trace the brief asked for (§3). Aggregate counts alone cannot show latency.
**Impact:** inverted our priority — the word-count gate (1130 ms) matters more than the echo gate.

### C-2 · EOT reported as 2000 ms

**Claimed:** production EOT is 2000 ms; lowering to 1200 would help.
**Reality:** production is **500 ms**; per-tenant tuning overrides the default. 1200 would be a regression.
**Caught by:** the connection log line in the §3 trace.

### C-3 · Chose a voicemail as the trace exemplar

**Claimed:** CALL-D demonstrated a caller being talked over.
**Reality:** CALL-D was a **voicemail**; AMD correctly hung up at turn 0.
**Caught by:** cross-checking all 21 echo-gate calls for an AMD verdict — 19 human, 2 voicemail.
**Impact:** re-traced on human calls; conclusion held, exemplar replaced.

### C-4 · `MIN_INTERRUPT_WORDS=1` first judged pointless

**Claimed:** the change would do nothing, since "hello"/"stop" are already hard interrupts.
**Reality:** correct for *complete phrases*, wrong for production, because `StartOfTurn` delivers a **partial**. `'Excuse'` never matches `"excuse me"`.
**Caught by:** reading the actual deferred strings in the journal instead of testing with invented inputs.

### C-5 · A false CRLF warning on the production `.env`

**Claimed:** the edited `.env` contained CRLF.
**Reality:** the check used bash `$"\r"`, which is a **locale translation string**, not a carriage return. Real count: 0.
**Caught by:** re-testing with `tr -cd "\r" | wc -c` before letting anyone restart into the file.

### C-6 · A wrong import that tests caught before production

**Bug:** `interrupt.py` imported `CallState` from `app.domain.models.call_session` — a module that does not exist. It is `app.domain.models.session`.
**Why it was dangerous:** the import sat inside `try/except Exception`, so in production the state change would have **silently never happened** while the function still reported success.
**Caught by:** 10 of the 11 new tests failing immediately.

---

## Files changed

| File | Type | Change |
|---|---|---|
| `app/domain/services/voice_pipeline/interrupt.py` | 🆕 new | The single idempotent interrupt operation, per-step logging under one `interrupt_id`, `InterruptResult` acknowledgement, 350 ms dedupe, metric emission |
| `app/domain/services/voice_pipeline/backchannel.py` | modified | New `is_disfluency()` + `_DISFLUENCIES` set — rejects hesitation by name rather than by word count |
| `app/domain/services/voice_pipeline/instant_opener.py` | modified | `is_opener_echo` rewritten from 1 signal to 4 (timing, content, repetition, similarity/onset); new `_ECHO_ONSET_WINDOW_S`, `_ECHO_SAME_EVENT_DEBOUNCE_S`, `_echoes_the_spoken_opener()`; arms onset + spoken text |
| `app/domain/services/telephony/modes/agent_first.py` | modified | Arms `_instant_opener_started_at`, `_instant_opener_spoken_text`, resets `_instant_opener_echo_at` so the agent-first greeting gets the onset bound |
| `app/infrastructure/stt/deepgram_flux.py` | modified | `DEEPGRAM_MIN_INTERRUPT_WORDS` default `2 → 1`; disfluency gate inserted ahead of the count guard; evidence recorded in-place |
| `app/domain/services/voice_pipeline_service.py` | modified | `handle_barge_in` delegates its teardown to `interrupt_playback()` — four scattered best-effort steps become one operation with a verdict |
| `app/infrastructure/telephony/asterisk_adapter.py` | modified | `interrupt_tts` returns the gateway acknowledgement (`ok`, `dropped_frames`, `interrupted_segments`, `attempts`, `error`, `utterance_rotated`); new `_tts_queue_config()` queue-cap lever wired into both `/v1/sessions/start` payloads |
| `app/infrastructure/telephony/telephony_media_gateway.py` | modified | `clear_output_buffer` returns discard counts; the `logger.debug` swallow of interrupt failure upgraded to `logger.error` |
| `app/infrastructure/metrics/voice_metrics.py` | modified | New `record_interrupt_outcome()`, `voice_interrupt_outcome_total`, `voice_interrupt_dropped_frames` |
| `tests/unit/test_barge_in_gates_let_callers_in.py` | 🆕 new | Both gates, parametrised over **verbatim production strings** |
| `tests/unit/test_interrupt_playback.py` | 🆕 new | 11 tests: failure ≠ success, dedupe vs real repeat, acknowledgement survives, partial-failure resilience |
| `report.md` | 🆕 new | This document |

---

## Test coverage

```
Full gate:   4818 passed · 15 skipped · 8 failed · 36 errors
Baseline:    the SAME 8 failures on a clean 044d9208 worktree with none of this work
Regressions: 0
```

The 8 failures (`test_systemd_readiness::test_install_script_is_executable`, 5 × `test_webhooks_call_hmac`, 2 × `test_webhooks_call_idor`) and the 36 collection errors (`ModuleNotFoundError: fakeredis`, a test-only dependency absent from the production venv) are environmental and pre-existing. They were **proven** so by running a separate clean worktree, not assumed.

### What the new tests pin

**`test_barge_in_gates_let_callers_in.py`**

- 20 parametrised cases over the exact strings production deferred — `'Listen'`, `'Excuse'`, `"I'm"`, `'Bye'`, `'Speaking'`, `'Please.'`, `"What's"` …
- Hesitation noise still suppressed (`uh`, `um`, `erm`, `ah`, `mmm`, `hmm`)
- Backchannels still suppressed (`yeah`, `ok`, `mhm`, `right`, `got it`)
- `is_disfluency` must not eat real words — `"oh"` yes, `"oh no"` no
- `min_words=2` remains reachable as a rollback lever
- The shipped default really is `1` (source assertion)
- Echo gate: repeat overrides, late-greeting rejection, prompt-first still echo, own-words always echo, unknown opener text fails **toward** the caller, double-call idempotence, real content always interrupts, outside-window never echo

**`test_interrupt_playback.py`**

- The acknowledgement survives: frames, ms, segments, local bytes, pending bytes, rotation flag
- State → `LISTENING` and stale transcript dropped
- **A failed gateway interrupt must not read as success** (the core regression)
- A raising gateway is a failure, not a silent pass
- Duplicate barge-in deduped — teardown runs **once**
- A genuinely later barge-in **does** run again
- A failing task-canceller does not abort the audio stop
- The TTS provider is told to stop generating
- Gateways without the acknowledgement contract still succeed
- `dropped_ms` derives from frames at 20 ms each

---

## Observability added

### New log lines

| Line | Meaning |
|---|---|
| `interrupt_step=begin` | a stop has started; carries `reason`, `tts_active` |
| `interrupt_step=state_listening` | conversation state flipped |
| `interrupt_step=task_cancelled` | in-flight LLM/TTS task cancelled |
| `interrupt_step=buffers_cleared` | Python buffers dropped, with byte counts |
| `interrupt_step=cpp_interrupt` | gateway verdict + `dropped_frames` / `dropped_ms` / `segments` / `attempts` / `rotated` |
| `interrupt_step=deduped` | duplicate event suppressed, with its age in ms |
| `interrupt_complete` | full result dict, `ok=True` |
| `interrupt_FAILED` | **ERROR** — Python thinks it stopped, the gateway disagrees |
| `Flux StartOfTurn disfluency … suppressed` | noise rejected by name |
| `instant_opener_echo_overridden` | a repeat beat the echo classification |
| `instant_opener_echo_rejected` | a greeting arrived too late to be pickup echo |

### New metrics

```promql
# interrupt reliability
sum by (outcome) (rate(voice_interrupt_outcome_total[5m]))

# any failure at all is a page-worthy event — baseline is 0/14 days
rate(voice_interrupt_outcome_total{outcome="failed"}[5m]) > 0

# early warning: retries rising before failures appear
rate(voice_interrupt_outcome_total{outcome="ok_after_retry"}[15m])

# how much queued agent audio a barge-in actually bins
histogram_quantile(0.95, rate(voice_interrupt_dropped_frames_bucket[5m])) * 20   # ms
```

> **Note:** Prometheus counters only materialise after their first `.inc()`. These series will not appear in `/metrics` until the first barge-in after deploy. Absence before that is expected, not a fault.

---

## Deploy, verify, rollback

### Deploy

```bash
# 1. on the server
cd /opt/talky && git pull --ff-only origin main

# 2. import smoke
cd /opt/talky/backend && venv/bin/python -c "import app.main" && echo IMPORT_OK

# 3. restart at zero active sessions — check first
curl -s http://127.0.0.1:18080/stats | grep -o '"active_sessions":[0-9]*'
sudo systemctl restart talky-api
```

**No C++ rebuild required.** No migration in this change. Only `talky-api` needs restarting.

### Verify

```bash
systemctl is-active talky-api
systemctl show talky-api -p ActiveEnterTimestamp -p MainPID --value
curl -s http://127.0.0.1:8000/api/v1/healthz/deep     # {"ready":true,"db":"ok","redis":"ok"}
curl -s http://127.0.0.1:18080/health                 # {"status":"ok","io_loop_healthy":true}
journalctl -u talky-api --since "2 min ago" | grep -iE "traceback|ERROR" | grep -v Vonage
```

Then on the first call:

```bash
# should now be ~absent for single words
journalctl -u talky-api -f | grep "barge-in deferred"

# should now appear
journalctl -u talky-api -f | grep -E "disfluency|interrupt_step"
```

### Rollback

| Change | Rollback |
|---|---|
| Barge-in code | `git checkout 044d9208` + restart `talky-api` |
| `MIN_INTERRUPT_WORDS` | set `=2` in `.env` + restart (backup `.env.bak-20260808-bargein`) |
| Echo gate only | `_ECHO_ONSET_WINDOW_S` → very large restores near-old behaviour |
| Queue cap | unset `VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES` |

---

## Canary plan, ready to execute

**30 calls · 5 speakers · 6 calls each.** Run against the deployed branch, internal numbers only.

### Per-speaker script

| # | Scenario | What to do | Pass condition |
|---|---|---|---|
| 1 | "Hello" during opener | Say "Hello?" **once**, immediately at pickup | Agent continues (echo protection holds) |
| 2 | Repeated "Hello" | Say "Hello?" then again ~1 s later | Agent **stops** on the second ✅ |
| 3 | "Stop" mid-reply | Wait for a long reply, say "Stop" | Audible stop **< 500 ms** |
| 4 | "Excuse me" | Say it over the opener | Audible stop < 500 ms |
| 5 | Full sentence | "I'm not interested in this" over the agent | Audible stop < 500 ms |
| 6 | Noise only | Cough / background talk, no speech to the agent | Agent does **not** stop |

### Data to capture per call

```bash
# one call's full cancellation chain
journalctl -u talky-api --since "<start>" -o short-precise | grep "<call_id>" \
  | grep -E "StartOfTurn|echo|barge|interrupt_step|interrupt_complete|disfluency"

# C++ side
curl -s http://127.0.0.1:18080/stats \
  | grep -oE '"tts_(segments_interrupted_total|frames_dropped_total|chunks_rejected_stale_total)":[0-9]+'
```

### Acceptance criteria — from the brief

| Criterion | Measure it with |
|---|---|
| Agent audibly stops within 300–500 ms | `StartOfTurn` ts → `interrupt_step=cpp_interrupt` ts |
| ≥95% interruption success, target 98%+ | `voice_interrupt_outcome_total{outcome="ok"}` ÷ total |
| Zero old TTS resumptions | `tts_chunks_rejected_stale_total` rises, no audio after stop |
| Zero self-interruptions from playback echo | scenario 1 must never stop the agent |
| Response begins 1.5–2 s after EndOfTurn | EndOfTurn ts → `t_tts_first_audio` ts |
| Zero calls with major continued talking-over | listener judgement, recorded per call |

---

## Open items and deferred work

### Live risk, right now

`min_words=1` is deployed **without** its paired `is_disfluency` guard, because the guard is in undeployed code. **A bare `Uh` can currently interrupt the agent.** Closed by deploying.

### Found but not in the brief — recommend prioritising

**The recording disclosure restarts from the top when interrupted.**

```
recording_disclosure_interrupted_retrying call_id=CALL-A attempt=1/2
```

On **both** human calls traced, the caller hung up ~2 s after this retry. A caller who interrupts the disclosure hears it begin again from the beginning. This is unfixed, was not on the list, and may account for more abandoned calls than either gate.

### Deliberately deferred, with reasons

| Item | Why deferred |
|---|---|
| Lowering the queue cap to 100 by default | No steady-state effect; risks dropping frames on the 119–170-chunk greeting burst. Lever provided instead |
| EOT → 1200 ms | Production is 500 ms; would add 700 ms dead air |
| `words[].confidence` entity modelling | Requires extending the Flux parser and `TranscriptChunk` |
| C++ source/channel separation for echo | Needs a pre-mix audio tap; similarity proxy (signal 4a) implemented instead |
| Prometheus scrape / dashboards | Metrics now emit; no scraper is running |

### Blocked on human action

| Item | Blocker |
|---|---|
| Canary, 30 calls | Requires human speakers |
| Post-fix successful-call trace | Requires a call after deploy |
| C++ log excerpts | Gateway `/stats` counters read 0 across 195 lifetime sessions — they do not accumulate as expected; a separate defect worth its own investigation |

---

## Appendix A — raw evidence

### A.1 · Barge-in counters

```
                        7 days     14 days
barge-in detected          286         415
barge-in deferred          106         173      ← 25–29% of all attempts
instant_opener_echo         50          50      ← all inside 7d (journald retention)
interrupt_tts failed         0           0
interrupt retry failed       0           0
```

### A.2 · Echo-gate calls, AMD cross-check

```
21 distinct calls where is_opener_echo fired
 ├── 19  HUMAN     (no AMD verdict)
 └──  2  VOICEMAIL (machine_detected)
```

### A.3 · Single-call trace, CALL-A (abridged)

```
16:49:09.909142  Flux StartOfTurn - User started speaking, barge-in detected
16:49:09.909297  instant_opener_echo_ignored call=CALL-A text='[redacted chars=6 sha=2d8bd7d9]'
16:49:09.909370  instant_opener_echo_ignored call=CALL-A text='[redacted chars=6 sha=2d8bd7d9]'
16:49:09.982378  transcript_received
16:49:10.074093  transcript_received
16:49:10.076505  transcript_received
16:49:10.076737  barge_in_detected                              ← +167 ms
16:49:10.077065  Barge-in (post-send) interrupted TTS
16:49:10.078529  recording_disclosure_interrupted_retrying attempt=1/2
16:49:11.912846  session ended                                  ← caller hung up
```

Two `instant_opener_echo_ignored` lines for one event — the double-call that shaped the idempotency design.

### A.4 · STT startup latency

```
n=337   min=0 ms   median=148 ms   p90=383 ms   max=20287 ms
```

Median is healthy. The `max` outlier is worth a separate look but is not systematic.

### A.5 · Gateway `/stats`

```json
{"sessions_started_total":195,"sessions_stopped_total":195,"active_sessions":0,
 "packets_in":0,"packets_out":0,"tts_frames_enqueued_total":0,
 "tts_queue_depth_frames":0,"tts_chunks_rejected_stale_total":0}
```

> **Caveat:** `packets_in: 0` across 195 lifetime sessions means these counters do not accumulate as their names suggest. **`/stats` cannot currently be used as evidence of gateway behaviour.** Logged as a separate defect.

---

## Appendix B — reproduction commands

```bash
# ── production version ──────────────────────────────────────────────────
git -C /opt/talky log -1 --format="%H %ci %s"
md5sum /opt/talky/services/voice-gateway-cpp/build/voice_gateway

# ── effective settings (no secrets printed) ─────────────────────────────
cd /opt/talky/backend && set -a && . ./.env && set +a
venv/bin/python -c "from app.core.telephony_settings import get_telephony_settings; \
                    print(get_telephony_settings().mute_during_tts)"

# ── barge-in counters ───────────────────────────────────────────────────
journalctl -u talky-api --since "14 days ago" > /tmp/j.txt
grep -c "barge-in detected" /tmp/j.txt
grep -c "barge-in deferred" /tmp/j.txt
grep -c "instant_opener_echo_ignored" /tmp/j.txt
grep -c "interrupt_tts failed" /tmp/j.txt

# ── what was deferred, ranked ───────────────────────────────────────────
grep -o "StartOfTurn short .* (<2 words)" /tmp/j.txt \
  | sed "s/StartOfTurn short //;s/ (<2 words)//" | sort | uniq -c | sort -rn

# ── effective EOT actually negotiated ───────────────────────────────────
grep -oE "\(eager=[0-9.]+,? eot=[0-9.]+,? timeout_ms=[0-9]+\)" /tmp/j.txt \
  | sort | uniq -c | sort -rn

# ── recover a redacted utterance (sha256[:8] of the raw text) ───────────
python3 -c "import hashlib; print(hashlib.sha256('Hello?'.encode()).hexdigest()[:8])"

# ── one call's full chain ───────────────────────────────────────────────
journalctl -u talky-api -o short-precise --since "<t0>" --until "<t1>" | grep "<call_id>"

# ── isolated verification, never touching prod ──────────────────────────
git -C /opt/talky worktree add --detach /tmp/tv HEAD
# copy modified files into /tmp/tv, then:
cd /tmp/tv/backend && /opt/talky/backend/venv/bin/python -m pytest tests/unit tests/security -q \
  --continue-on-collection-errors
git -C /opt/talky worktree remove /tmp/tv --force
```

---

## Closing

The brief was well-reasoned and its priority order was right in spirit. Measured against production, most of what it asked to be **built** already existed and worked; what it asked to be **tuned** was already tighter than proposed; and the defect it correctly identified — opener echo rejection — turned out to be the *cheaper* of the two real problems.

The highest-impact fix was not on the list: a word-count guard applied to a partial transcript, silently discarding **29% of every caller's attempts to speak**, at a cost of over a second of talk-over each time.

That one is live. The rest is verified and waiting on a deploy and a canary.
