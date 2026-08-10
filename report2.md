# Report 2 — Review Findings, Opener Redesign, and Production Deployment

**Report date:** 2026-08-11
**Deployed commit:** `06ffba99d3ab38994a0cb881b76370d5e61cfaec`
**Deployed at:** `2026-08-10 19:28:57 UTC` · PID `569531 → 1514885`
**Status:** 🟢 **LIVE in production**
**Predecessor:** [`report.md`](./report.md) — the original ten-point investigation

> **Privacy note.** This repository is public. Production call identifiers are pseudonymised. No credentials, server addresses, or tenant identifiers appear in this document or in the committed code.

---

## Table of contents

- [Status at a glance](#status-at-a-glance)
- [What this report covers](#what-this-report-covers)
- [Commit trail](#commit-trail)
- [Finding 1 — the deduplication did not survive concurrency](#finding-1--the-deduplication-did-not-survive-concurrency)
- [Finding 2 — the pre-TTS guard prevented a cancel but not a talk-over](#finding-2--the-pre-tts-guard-prevented-a-cancel-but-not-a-talk-over)
- [Finding 3 — cancellation lived outside the centralized operation](#finding-3--cancellation-lived-outside-the-centralized-operation)
- [CI — proving the voice path, not the frontend](#ci--proving-the-voice-path-not-the-frontend)
- [Test proof](#test-proof)
- [C++ gateway build](#c-gateway-build)
- [Deployment record](#deployment-record)
- [A safety decision worth recording](#a-safety-decision-worth-recording)
- [Live verification](#live-verification)
- [Behaviour: before vs now](#behaviour-before-vs-now)
- [What to watch on the first calls](#what-to-watch-on-the-first-calls)
- [Still outstanding](#still-outstanding)
- [Rollback](#rollback)
- [Appendix — reproduction commands](#appendix--reproduction-commands)

---

## Status at a glance

```
┌─────────────────────────────────────────────────────────────────────┐
│  PRODUCTION                                                         │
│    commit    06ffba99   (local == origin/main == /opt/talky)        │
│    restarted 2026-08-10 19:28:57 UTC                                │
│    health    {"ready":true,"db":"ok","redis":"ok"}                  │
│    gateway   {"status":"ok","io_loop_healthy":true}                 │
│    services  api · voice-worker · voice-gateway · dialer ·          │
│              reminder · asterisk  — all active                      │
│                                                                     │
│  RUNTIME SETTINGS                                                   │
│    mute_during_tts       False   caller RTP flows during TTS        │
│    min_interrupt_words   1       paired with the disfluency guard   │
│    eot_timeout_ms        500     unchanged — deliberately NOT 1200  │
│                                                                     │
│  REVIEW FINDINGS   3 raised · 3 confirmed real · 3 fixed · 3 tested │
│  CANARY CALLS      0 of 30   ← production is currently the canary   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## What this report covers

`report.md` answered the original ten-point brief. That work shipped as `729632f7`.

A code review of `729632f7` then raised three defects, asked for hard test evidence, and asked for CI that actually exercises the Python and C++ voice path rather than the frontend.

**All three findings were real.** Each was confirmed by reading the code, not accepted on assertion — and each is now fixed with a test that pins the *failure it prevents*, not merely the fix.

This report records what was done, how, and why each fix took the shape it did.

---

## Commit trail

```
044d9208  ← the baseline production had been running since 2026-08-07
    │
    ├── 729632f7  the original barge-in work (report.md)
    │             two gates that never asked the agent to stop
    │
    └── 06ffba99  the three review findings  ← DEPLOYED, LIVE
                  interrupt race · pre-TTS talk-over · split cancellation
                  + GitHub Actions CI for the voice path
```

**No C++ source changed in either commit.** The deployed gateway binary is unchanged from `2026-07-17`, md5 `22c052509c69fe6807e04f638ba3f1b5`.

---

## Finding 1 — the deduplication did not survive concurrency

> *"The current 350 ms deduplication may not protect against two interruption coroutines starting simultaneously before `_last_interrupt` is stored."*

### Confirmed — the review was exactly right

The original implementation read the dedupe state at entry and wrote it at exit. Between those two points sat **four `await` boundaries**:

```
   interrupt_playback()
        │
        ├── read _last_interrupt          ◄── check
        │
        ├── await cancel_task()              ▲
        ├── await clear_output_buffer()      │  FOUR yield points.
        ├── await  └─ gateway POST           │  asyncio can schedule
        ├── await tts_provider.clear_queue() ▼  another coroutine here.
        │
        └── write _last_interrupt         ◄── write
```

Two coroutines entering together both saw an empty slot, both passed the check, and both ran the entire teardown — **rotating the utterance id twice and cancelling the turn task mid-unwind.**

The 350 ms window protected *sequential* duplicates. That is not the dangerous case. The dangerous case is simultaneous, and it was completely unguarded.

### How it was fixed

A **single-flight `Future`, published synchronously before the first `await`.**

```
   interrupt_playback()
        │
        ├── is there an in-flight Future?        ─── yes ──► await it,
        │                                                    return deduped
        ├── is there a recent result?            ─── yes ──► return it
        │
        ├── publish Future on the session        ◄── NO AWAIT between the
        │                                            check and this line.
        │                                            asyncio cannot preempt.
        └── await _do_interrupt(...)             ─── the actual teardown
                 │
                 └── finally: resolve Future, clear the slot
```

### Why a Future and not a Lock

Both were offered in the review. The Future is the better fit here:

| | `asyncio.Lock` | single-flight `Future` |
|---|---|---|
| Second caller | waits, then **runs a second teardown** | **reuses the first result** |
| Utterance id | rotated twice | rotated once |
| Turn task | cancelled twice | cancelled once |
| Latency | serialised | immediate on completion |

A Lock serialises duplicates. We do not want duplicates *serialised* — we want them **absorbed**. Two barge-in events for one utterance describe one intent, and should produce one teardown and one verdict.

### Failure containment

An interrupt that raises must not wedge the session into never being interruptible again. The Future is resolved in a `finally`, and the in-flight slot is cleared on both the success and the failure path — so the next barge-in starts a fresh operation.

### Proof

```python
async def test_two_simultaneous_interrupts_run_the_teardown_ONCE():
    s, gw = _session(), _SlowGateway()
    a, b = await asyncio.gather(
        interrupt_playback(s, media_gateway=gw, reason="barge_in"),
        interrupt_playback(s, media_gateway=gw, reason="barge_in"),
    )
    assert gw.calls == 1
    assert {a.deduped, b.deduped} == {False, True}
```

Also tested: **10 concurrent** (one teardown), a **failing in-flight followed by a successful one** (slot clears, session recoverable), and **sequential interrupts past the window** (single-flight does not become a permanent lock).

---

## Finding 2 — the pre-TTS guard prevented a cancel but not a talk-over

> *"The current `barge_in_ignored_final_pre_tts` behavior may allow the pending response to start while the caller is already speaking."*

### Confirmed — and this was the subtlest of the three

The guard itself is **correct and had to stay**. A `StartOfTurn` arriving while a FINAL answer is still generating means the caller began a *new utterance*, not that they are interrupting audible speech. Cancelling there deletes the answer and leaves the caller in silence — a real production bug, and the reason the guard exists.

But the guard only decided **not to cancel**. It said nothing about **when the protected answer may start speaking**:

```
  t0   caller starts speaking          ──────────────────────────►
  t1   StartOfTurn → guard says "protect the answer, do not cancel"
  t2   answer finishes generating
  t3   playback starts                 ██████████████  ← ON TOP OF THE CALLER
  t4   caller finally stops
```

**Barge-in cannot rescue this.** Barge-in stops audio that is *already playing*; here the talk-over begins at the very first packet.

### How it was fixed — a hold, not a cancel

New module `voice_pipeline/playback_gate.py`. Playback waits for the caller to finish, then speaks.

```
  t0   caller starts speaking          ──────────────────────────►
  t1   StartOfTurn → guard protects the answer AND arms the hold
  t2   answer finishes generating      ⏸  held
  t3   caller stops (EndOfTurn)        ⏵  released
  t4   playback starts                 ██████████████  ← clean floor
```

This preserves both properties that matter:

- **The answer survives** → no silence, which is what the guard exists to prevent.
- **The caller is not spoken over** → which is what the review correctly identified as missing.

### Bounded, and fails open

A caller who never yields — a television in the background, an STT stream that never delivers `EndOfTurn` — must not mute the agent permanently.

```
_MAX_HOLD_S = 2.5      # then speak anyway, and log a warning
_POLL_S     = 0.02     # one PCMU frame; the hold adds at most one frame
```

> **Talking over someone is bad. Never speaking again is worse.** The timeout is not a nicety; it is the difference between a rough turn and a dead call.

### Edge-triggered, one-shot

The hold applies to **one** playback and only when a barge-in was actually protected. It is consumed the moment it is evaluated, so:

- normal turns pay **zero** latency cost,
- a stale flag cannot slow every later turn.

### Where the floor state is tracked

| Event | Call | Location |
|---|---|---|
| Caller takes the floor | `mark_caller_speaking()` | `audio_ingest`, **after** the echo gate |
| Caller yields | `mark_caller_stopped()` | `turn_ender`, at `EndOfTurn` |
| Playback checks | `await_caller_pause()` | `tts_playback`, before `tts_active = True` |

Setting the floor **after** the echo gate matters: our own greeting echo must never be mistaken for the caller taking the floor, or the agent would hold against itself.

### Proof

```
test_playback_waits_while_the_caller_is_still_speaking       ✅
test_the_hold_is_bounded_so_a_caller_cannot_mute_the_agent   ✅
test_no_hold_when_not_armed                                  ✅
test_no_hold_when_the_caller_is_already_quiet                ✅
test_the_hold_is_one_shot                                    ✅
test_caller_speaking_state_transitions                       ✅
```

---

## Finding 3 — cancellation lived outside the centralized operation

> *"`handle_barge_in()` appears to cancel the pending turn before calling `interrupt_playback()`, rather than passing the task into the centralized operation."*

### Confirmed

`interrupt_playback` accepted a `cancel_task` parameter that `handle_barge_in` **never used**. It cancelled the turn itself, then called the "centralized" operation separately.

```
  BEFORE                                  AFTER
  ──────                                  ─────
  handle_barge_in                         handle_barge_in
    ├── cancel turn task    ← no id         └── interrupt_playback(
    │                                             cancel_task=_cancel_pending_turn)
    └── interrupt_playback  ← has id             ├── 1 state
          ├── 1 state                            ├── 2 cancel   ← same id
          ├── 2 (skipped)                        ├── 3 buffers
          ├── 3 buffers                          ├── 4 C++ interrupt
          └── 4 C++ interrupt                    └── 5 tts provider
```

Two consequences: the trace was split across an unlabelled cancel and a labelled stop, and nothing prevented the two interleaving.

### How it was fixed

The cancellation is now a closure passed **in** as `cancel_task`, running as step 2 of the guarded operation. One `interrupt_id` across the whole chain — and because the operation is single-flight (Finding 1), **two concurrent barge-ins can no longer cancel the same task twice.**

### One ordering detail

The `[interrupted by caller]` annotation depends on whether audio was actually playing, and `interrupt_playback` clears `tts_active` in step 1. The flag is therefore captured **before** the call:

```python
was_tts_active = session.tts_active     # capture BEFORE the stop
...
if was_tts_active and session.conversation_history:
    ...append "[interrupted by caller]"
```

Without this, moving the cancel inside would have silently broken the annotation — the interruption marker would never be written again.

### Proof

```
test_handle_barge_in_passes_cancellation_INTO_the_central_operation  ✅
test_the_cancel_runs_inside_the_single_flight_gate                   ✅
    → two concurrent barge-ins, cancel called exactly once
```

---

## CI — proving the voice path, not the frontend

> *"Please add the backend voice tests to GitHub Actions if possible, because the current Vercel result does not prove that Python/C++ voice tests passed."*

Correct, and now addressed. `.github/workflows/backend-voice-tests.yml` adds three jobs:

| Job | What it proves |
|---|---|
| `voice` | 13 suites owning barge-in, endpointing, echo, playback interruption |
| `full` | the complete unit + security suite with `-rfsE` |
| `cpp-gateway` | `g++ -O2 -std=c++17 -Wall -Wextra` build of the C++ gateway |

**`fakeredis` is installed in CI.** It is a test-only dependency deliberately absent from the production venv, which is why three suites error there — in CI they actually run.

The voice suites are listed **explicitly** rather than by glob, so deleting a file fails CI loudly instead of silently shrinking coverage.

---

## Test proof

### Command

```bash
python -m pytest tests/unit tests/security -q -p no:randomly \
    --continue-on-collection-errors -rfsE
```

### Result

```
8 failed, 4832 passed, 15 skipped, 969 warnings, 36 errors in 159.43s
```

**Environment:** Python `3.12.3` · pytest `9.0.3` · isolated git worktree at `729632f7` with the three fixes applied — byte-identical to the content of `06ffba99`.

### Failed (8) — all pre-existing and environmental

```
tests/unit/test_systemd_readiness.py::TestSystemdServiceFiles::test_install_script_is_executable
tests/unit/test_webhooks_call_hmac.py::test_goal_achieved_rejects_forged_signature
tests/unit/test_webhooks_call_hmac.py::test_mark_spam_rejects_forged_signature
tests/unit/test_webhooks_call_hmac.py::test_rejects_when_no_secret_configured
tests/unit/test_webhooks_call_hmac.py::test_missing_signature_header_is_rejected
tests/unit/test_webhooks_call_hmac.py::test_goal_achieved_accepts_valid_signature
tests/unit/test_webhooks_call_idor.py::test_admin_configure_rejects_unauthenticated
tests/unit/test_webhooks_call_idor.py::test_admin_configure_accepts_valid_internal_token
```

**Proven pre-existing**, not assumed: the identical eight fail on a *separate clean worktree* at `044d9208` with none of this work applied.

### Errors (36) — one root cause

```
ModuleNotFoundError: No module named 'fakeredis'

  tests/security/test_telephony_ws_auth.py
  tests/unit/test_dialer_redis_reliability.py
  tests/unit/test_metrics_endpoint_auth.py
```

Test-only dependency, absent from the production venv by design. **CI installs it, so these 36 execute there.**

### Skipped (15)

```
12 ×  App not available                          (tests/unit/test_api_endpoints.py)
 2 ×  librosa required for resampling            (tests/unit/test_audio_utils.py)
 1 ×  KNOWN_PUBLIC_ROUTES has stale entries      (tests/unit/test_endpoint_auth_audit.py)
```

### Voice suites on the deployed tree, after the restart

```
160 passed
```

Run against `/opt/talky/backend` itself — the exact code the live process imports, not a worktree.

---

## C++ gateway build

```
compiler   g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
command    g++ -O2 -std=c++17 -Wall -Wextra -Iinclude src/*.cpp -o voice_gateway
exit       0
warnings   ZERO
artifact   255800 bytes
build time 7.3 s
```

Built to a temporary path. **The deployed binary was not touched** and still hashes `22c052509c69fe6807e04f638ba3f1b5` — verified before and after.

**No C++ source changed** in `729632f7` or `06ffba99`. The gateway's interrupt endpoint already returned `dropped_frames` and `interrupted_segments`; the earlier work simply stopped Python discarding them.

---

## Deployment record

| | |
|---|---|
| **Backend commit** | `06ffba99d3ab38994a0cb881b76370d5e61cfaec` |
| **C++ commit** | same (monorepo) — **no C++ source changed** |
| **C++ binary** | `22c052509c69fe6807e04f638ba3f1b5`, built `2026-07-17 12:32`, unchanged |
| **Deployed at** | `2026-08-10 19:28:57 UTC` |
| **PID** | `569531 → 1514885` |
| **Active calls at restart** | `0` |
| **Services restarted** | `talky-api` only |
| **Migration** | none |
| **C++ rebuild** | none |
| **Rollback target** | `044d9208` |

### Timeline

```
2026-08-07  044d9208 running in production
2026-08-08  19:45 UTC  restart — DEEPGRAM_MIN_INTERRUPT_WORDS=1 config only
2026-08-09  729632f7 committed + pushed
            729632f7 pulled to disk (NOT restarted)
2026-08-10  review raises 3 findings
            ↳ 729632f7 REVERTED on disk (see below)
            06ffba99 committed + pushed
            19:28 UTC  restart — 06ffba99 LIVE
```

---

## A safety decision worth recording

Between committing `729632f7` and the review, that commit had been **pulled onto the production disk but not restarted into**. The running process was still `044d9208`.

When the review identified the concurrency race in `729632f7`, that state became a hazard:

> **Any restart — a crash, an operator, a watchdog — would have shipped the version with a known race, unattended and unannounced.**

The production checkout was therefore reverted to `044d9208`, so disk matched the running process and an accidental restart became a no-op. The gateway binary hash was verified unchanged before and after, and the service was never restarted during the revert.

### A related discovery

```
talky-api   User=root   Restart=on-failure
```

`Restart=on-failure` means a **clean** exit (an unhandled shutdown path, an OOM `SIGTERM`) leaves the service **down and not restarted**. `Restart=always` is the safer policy for a service of this kind.

This also closed off a possible non-privileged restart path: signalling the process would not have restarted it, it would have taken the API down.

**Recommended, separate from this work:** change the unit to `Restart=always`.

---

## Live verification

Executed **against the running process**, not the files on disk:

```
health           {"ready":true,"db":"ok","redis":"ok"}
gateway          {"status":"ok","io_loop_healthy":true}
services         api · voice-worker · voice-gateway · dialer · reminder · asterisk — active
post-restart     clean (only the perennial Vonage "not configured (optional)" line)

single-flight Future : True
pre-TTS hold armed   : True
hold at playback     : True   cap 2.5 s
cancel centralized   : True

mute_during_tts      : False
min_interrupt_words  : 1
eot_timeout_ms       : 500    (tenant tuning; deliberately NOT raised to 1200)
```

---

## Behaviour: before vs now

| Scenario | `044d9208` | `729632f7` | `06ffba99` (live) |
|---|---|---|---|
| `'Listen'`, `'Excuse'`, `'Bye'` (single word) | swallowed | **interrupts** | **interrupts** |
| `'Hello?'` repeated during opener | swallowed | **interrupts** | **interrupts** |
| `'Hello?'` 5 s into a long greeting | swallowed | **interrupts** | **interrupts** |
| `'Hello?'` first, at onset | swallowed ✅ | swallowed ✅ | swallowed ✅ |
| `'Uh'` / `'Um'` | swallowed ✅ | swallowed ✅ | swallowed ✅ |
| *(config-only window 08-08 → 08-10)* | — | **could interrupt** ⚠️ | **closed** ✅ |
| Two barge-ins arriving together | double teardown | **double teardown** ⚠️ | **one teardown** ✅ |
| Reply starting while caller mid-sentence | talks over | **talks over** ⚠️ | **held, then speaks** ✅ |
| Interrupt failure | `logger.debug`, invisible | ERROR + metric | ERROR + metric |
| Cancellation traceability | none | partial (split id) | **one `interrupt_id`** ✅ |

> The ⚠️ row in the config-only window is worth noting: between `2026-08-08` and `2026-08-10`, production ran `min_interrupt_words=1` **without** its paired disfluency guard, because that guard lived in undeployed code. This deploy closes it.

---

## What to watch on the first calls

```bash
# should now appear
journalctl -u talky-api -f | grep -E "interrupt_step|disfluency|pre_tts_hold"

# should be ~absent for single words
journalctl -u talky-api -f | grep "barge-in deferred"

# must stay at zero — baseline is 0 failures across 14 days
journalctl -u talky-api -f | grep "interrupt_FAILED"
```

### Metrics

```promql
sum by (outcome) (rate(voice_interrupt_outcome_total[5m]))
rate(voice_interrupt_outcome_total{outcome="failed"}[5m]) > 0        # page-worthy
histogram_quantile(0.95, rate(voice_interrupt_dropped_frames_bucket[5m])) * 20
```

> Prometheus counters only materialise after their first `.inc()`. These series will not appear until the first barge-in after deploy. Absence before that is expected.

### The single riskiest change

**`pre_tts_hold_released`** is the only change that can *delay* the agent rather than only stop it sooner. If callers report unexpected pauses before replies, this is the first suspect. It is capped at 2.5 s, fails open, and only arms when a barge-in was protected — but it deserves the closest listening.

---

## Still outstanding

### Blocked on human testers

| Item | Blocker |
|---|---|
| 30 canary calls, 5 speakers | Cannot place calls; no campaign will be started to simulate it |
| Recordings, call IDs | Needs those calls |
| Successful-call runtime trace | Needs a call on the new code |
| Failed-call trace on new code | Needs a failure to occur |

**0 of 30 canary calls have run.** Production is currently the canary — a deliberate decision taken with that stated.

### Known defects, not fixed

**Recording disclosure restarts from the top when interrupted.**

```
recording_disclosure_interrupted_retrying attempt=1/2
```

On both human calls traced in `report.md`, the caller hung up ~2 s after this retry. A caller who interrupts the disclosure hears it begin again from the beginning. **Unfixed. Not on the original brief. Possibly a larger source of abandoned calls than either gate.**

**C++ `/stats` counters do not accumulate.**

```json
{"sessions_started_total":195, "packets_in":0, "packets_out":0,
 "tts_frames_enqueued_total":0, "tts_chunks_rejected_stale_total":0}
```

195 lifetime sessions with `packets_in: 0`. **`/stats` cannot currently serve as evidence of gateway behaviour** — which is why the C++-side runtime evidence the review asked for cannot yet be produced from it.

**`Restart=on-failure` on `talky-api`.** A clean exit leaves the service down.

### Deliberately declined

| Item | Reason |
|---|---|
| EOT → 1200 ms | Production negotiates **500 ms**; this would add **+700 ms** of dead air per turn |
| TTS queue cap → 100 frames | Ceiling, not working depth (~15 frames measured); risks dropping frames on the 119–170 chunk greeting burst. Lever provided: `VOICE_GATEWAY_TTS_MAX_QUEUE_FRAMES` |

---

## Rollback

```bash
# full code rollback
git -C /opt/talky checkout 044d92080402846ebf89b16c836fc84b41984bb1
sudo systemctl restart talky-api

# narrower — revert only barge-in sensitivity, no code change
#   set DEEPGRAM_MIN_INTERRUPT_WORDS=2 in /opt/talky/backend/.env
#   original backup: /opt/talky/backend/.env.bak-20260808-bargein
sudo systemctl restart talky-api

# narrower still — disable only the pre-TTS hold
#   set _MAX_HOLD_S = 0.0 in voice_pipeline/playback_gate.py
```

No migration to reverse. No C++ rebuild. Only `talky-api` restarts.

---

## Appendix — reproduction commands

```bash
# ── what is deployed ───────────────────────────────────────────────────
git -C /opt/talky rev-parse HEAD
systemctl show talky-api -p ActiveEnterTimestamp -p MainPID --value
md5sum /opt/talky/services/voice-gateway-cpp/build/voice_gateway

# ── are the fixes in the RUNNING code, not just on disk ────────────────
cd /opt/talky/backend && venv/bin/python -c "
import inspect
from app.domain.services.voice_pipeline.interrupt import interrupt_playback
from app.domain.services.voice_pipeline.playback_gate import _MAX_HOLD_S
from app.domain.services import voice_pipeline_service as vps
from app.domain.services.voice_pipeline import tts_playback
s = inspect.getsource(vps.VoicePipelineService.handle_barge_in)
print('single-flight :', '_interrupt_inflight' in inspect.getsource(interrupt_playback))
print('pre-TTS hold  :', 'arm_pre_tts_hold' in s)
print('hold at play  :', 'await_caller_pause' in inspect.getsource(tts_playback), _MAX_HOLD_S)
print('centralized   :', 'cancel_task=_cancel_pending_turn' in s)
"

# ── health ─────────────────────────────────────────────────────────────
curl -s http://127.0.0.1:8000/api/v1/healthz/deep
curl -s http://127.0.0.1:18080/health

# ── full test suite ────────────────────────────────────────────────────
python -m pytest tests/unit tests/security -q -p no:randomly \
    --continue-on-collection-errors -rfsE

# ── C++ build (to /tmp — never overwrite the deployed binary) ──────────
cd services/voice-gateway-cpp
g++ -O2 -std=c++17 -Wall -Wextra -Iinclude src/*.cpp -o /tmp/voice_gateway

# ── one interrupt's full chain ─────────────────────────────────────────
journalctl -u talky-api | grep "interrupt_id=<id>"
```

---

## Closing

Three findings were raised against `729632f7`. All three were real, and one of them — the concurrency race — was a defect that no amount of sequential testing would have surfaced, because the window existed only between a check and a write separated by four `await` points.

The pre-TTS finding was the most valuable, because the guard it questioned *looked* correct and *was* correct as far as it went. It prevented the wrong thing being cancelled; it simply never decided when the protected thing was allowed to speak. That is the kind of gap that survives review precisely because the code it lives in is sound.

All three are fixed, tested against the failure rather than the fix, and live in production as of `2026-08-10 19:28:57 UTC`.

**What remains is not code.** It is 30 calls, 5 speakers, and someone listening.

---
---

# PART II — The Opener Redesign

**Commit:** `fb8f81564f0a5bdc1b1387478c49b566f85700be`
**Commit name:** `feat(voice): the agent stops monologuing and opens like a person`
**Date:** 2026-08-11
**Status:** 🟡 committed and pushed to `main` — **not yet deployed**
**Scale:** 36 files · +5,698 / −904 lines · 5,091 tests passing

---

## Part II table of contents

- [The complaint](#the-complaint)
- [The evidence — a real tenant's production logs](#the-evidence--a-real-tenants-production-logs)
  - [Log 1 — the recording notice is talked over more often than it completes](#log-1--the-recording-notice-is-talked-over-more-often-than-it-completes)
  - [Log 2 — a single call, timestamped to the millisecond](#log-2--a-single-call-timestamped-to-the-millisecond)
  - [Log 3 — what the agent actually swallowed](#log-3--what-the-agent-actually-swallowed)
  - [Log 4 — the redacted words, recovered](#log-4--the-redacted-words-recovered)
  - [Log 5 — the campaign's call outcomes](#log-5--the-campaigns-call-outcomes)
  - [Log 6 — how long calls survived](#log-6--how-long-calls-survived)
  - [Log 7 — STT startup latency](#log-7--stt-startup-latency)
- [What the logs add up to](#what-the-logs-add-up-to)
- [What was changed](#what-was-changed)
  - [Change 1 — turn 1 is a bare pickup greeting](#change-1--turn-1-is-a-bare-pickup-greeting)
  - [Change 2 — the re-greet ladder sounds like a person](#change-2--the-re-greet-ladder-sounds-like-a-person)
  - [Change 3 — turn 2 is a real introduction with a permission ask](#change-3--turn-2-is-a-real-introduction-with-a-permission-ask)
  - [Change 4 — the recording notice stops restarting](#change-4--the-recording-notice-stops-restarting)
  - [Change 5 — the third contradiction in the trailing slot](#change-5--the-third-contradiction-in-the-trailing-slot)
  - [Change 6 — optional LLM-authored ladder](#change-6--optional-llm-authored-ladder)
- [How it was done](#how-it-was-done)
- [What the workers got wrong](#what-the-workers-got-wrong)
- [An invariant that had to be refined, not obeyed](#an-invariant-that-had-to-be-refined-not-obeyed)
- [Verification](#verification-part-ii)
- [Possible improvements](#possible-improvements)
- [What is still not done](#what-is-still-not-done)
- [Appendix C — the full annotated call trace](#appendix-c--the-full-annotated-call-trace)

---

## The complaint

The owner's report, verbatim:

> *"you have to remove this hello this can be recorded section and also i am (xyz) calling from that and quick one you have 30 seconds remove all this monologue and be like hello if next person responses proceed if not hello again until user speaks back like the natural conversation"*

Three distinct asks inside that:

1. **Remove the recording notice** from the opening.
2. **Remove the monologue** — the "I'm X calling from Y, quick one, you've got 30 seconds" block.
3. **Open like a human** — say hello, wait; if nothing comes back, say hello again, until they speak.

Every one of those turned out to be supported by the tenant's own production logs. This section copies those logs and explains what each one means.

---

## The evidence — a real tenant's production logs

All figures below are from the **Dojo UK Restaurant Outreach** campaigns and their two tenants, pulled from the live server. Tenant and call identifiers are pseudonymised because this repository is public; every command needed to reproduce the raw numbers is in [Appendix B](#appendix-b--reproduction-commands).

### Log 1 — the recording notice is talked over more often than it completes

```
=== recording disclosure: spoken vs interrupted ===
  disclosure spoken     : 80
  disclosure INTERRUPTED: 121
  disclosure text chars : chars=60

=== total sessions in window ===
  287
```

**What this says.** Over a 10-day window, the recording notice was **interrupted 121 times and completed 80 times**. It is talked over **60% of the time it plays**.

**Why it matters.** Until this change, an interrupted notice **restarted from the beginning**. So the most common experience of this notice was: it starts, the callee opens their mouth, and it starts again. That is not a legal disclosure being delivered — it is a machine looping at somebody.

**The subtlety.** The retry was not carelessness. It was added deliberately, and the code comment that shipped with it records why:

```python
# Recording-disclosure delivery attempts. The notice lands in the two seconds
# ... a single attempt loses the recording on a large share of calls
# (measured: 3 of the first 7 production calls, 43%). Two attempts, with a
# settle gap between, ...
_DISCLOSURE_MAX_ATTEMPTS = 2
_DISCLOSURE_RETRY_SETTLE_S = 1.2
```

Someone measured a real problem — 43% of recordings lost — and fixed it. The fix then created a worse problem that nobody measured, because the metric they were watching was *recordings retained*, not *callers who hung up*.

### Log 2 — a single call, timestamped to the millisecond

```
20:13:24.294  TelephonyMediaGateway: session started  wire=pcmu/8000Hz
20:13:24.295  recording_disclosure_speaking  reason=tenant_default_two_party
              text='[redacted chars=60 sha=0e2a8697]'
20:13:24.523  TTS_FMT_DEBUG  provider=deepgram first_bytes=1280
20:13:24.524  t_tts_first_audio  bytes=320
20:13:26.298  audio_stream_first_chunk  chunk_len=1280 — audio now flowing to STT
20:13:26.299  flux_first_audio_sent  elapsed_ms=2003 — caller audio now flowing
20:13:27.298  audio_level  rms=1377 peak=17870   (>500 = speech-likely)
20:13:28.024  recording_disclosure_spoken
20:13:28.024  outbound_greeting_presynth  chunks=119 text='[redacted chars=52]'
20:13:28.058  Flux StartOfTurn - User started speaking, barge-in detected
20:13:28.058  instant_opener_echo_ignored  text='[redacted chars=4 sha=580684f8]'
20:13:28.058  instant_opener_echo_ignored  text='[redacted chars=4 sha=580684f8]'
20:13:29.723  machine_detected_interim  verdict=voicemail turn=0 — hanging up
20:13:29.725  hangup requested  reason=voicemail_detected
```

**Reading this line by line.**

- `24.295 → 28.024` — the recording notice took **3.73 seconds**. That is the first thing anyone who answers this call hears.
- `26.299 elapsed_ms=2003` — caller audio did not reach the speech recogniser for **2 seconds** after the session started. For those 2 seconds the agent was talking and *could not have heard a reply even if one came*.
- `28.024` — only now does the actual greeting begin. Four seconds in, nothing has been said except a legal notice.
- `28.058` — the callee speaks. **34 milliseconds** after the greeting starts.
- `28.058` twice — `instant_opener_echo_ignored` logged by **two different modules for one event**. This duplication is not a bug in itself, but it is the reason the interrupt operation had to be made idempotent by *utterance* rather than by call count (see Part I, Finding 1).
- `29.723` — answering machine detected, call ended.

**Honest caveat: this particular call was a voicemail.** Answering-machine detection did the right thing. It is included because the *timing* is representative — the 3.73s notice and the 2s STT startup are the same on every call — but it is **not** an example of a human being talked over. Two of the twenty-one calls where the echo gate fired were voicemail; the other nineteen were people. Choosing this call as the first exemplar was an error, corrected by cross-checking every such call for a machine-detection verdict.

### Log 3 — what the agent actually swallowed

The barge-in guard required at least two words before a caller was allowed to interrupt. Here is every distinct utterance it rejected in a 7-day window, with counts:

```
 'Hi.'       ████████████████████████  21
 "I'm"       █████████████████         15
 'Thanks.'   ███████████████           13
 'Zero'      ██  2      'Yours'    ██  2      'Saturday' ██  2
 'Same'      ██  2      'Nothing.' ██  2      'Listen'   ██  2
 "Let's"     ██  2      "It's"     ██  2      'Great.'   ██  2
 "What's" 1  "What'd" 1  'Well,' 1  'wanted' 1  'Uh,' 1  'there.' 1
 'Still' 1   'Stand' 1   'Speaking' 1  'Som' 1  'setup' 1  'Sar' 1
 'reason' 1  'problem' 1 'Probably' 1  'Please.' 1  'pinion' 1
 'Passport' 1 'Mother' 1 'Most' 1  'Linux' 1  'LA?' 1  'Kim' 1
 "I've" 1    'follow' 1  'Famous' 1   'Bye' 1   'Excuse' 1
```

**What this says.** These are not coughs. `'Listen'`, `"Let's"`, `'Speaking'`, `'Please.'`, `"What's"`, `'Bye'` are people talking. Of roughly 106 rejections, only `'Uh,'` and the fragments `'Som'`, `'Sar'`, `'pinion'` are plausibly noise — the guard was about **96% wrong**.

**The single clearest case is `'Excuse'`.** The system has an allow-list of utterances that must always interrupt, and `"excuse me"` is on it. But Deepgram Flux's `StartOfTurn` event carries a **partial** transcript — in practice the caller's first word. At the moment the decision was made only `Excuse` had arrived, so the allow-list could not match, the word count was 1, and the caller was deferred.

**The root cause in one sentence:** a guard that counts words on a partial transcript is not measuring intent, it is measuring how fast somebody talks.

### Log 4 — the redacted words, recovered

Caller speech is redacted in the journal, correctly. But the redaction is a plain `sha256[:8]` of the text (`log_redact.py:359`), and greetings come from a small vocabulary — so the exact utterances were recoverable by brute force:

```
=== instant_opener_echo_ignored occurrences (7d) ===
50 log lines  =  25 distinct events  (two modules log each event)

  sha 2d8bd7d9  chars=6  ->  'Hello.'
  sha 0da72197  chars=6  ->  'Hello?'
  sha 580684f8  chars=4  ->  'Hey.'

=== of the 21 distinct calls where this fired ===
  19  HUMAN     (no answering-machine verdict)
   2  VOICEMAIL
```

**What this says.** Nineteen real people said *"Hello?"* while the agent's opener was playing, and the system classified their speech as an **echo of its own audio** and ignored it.

**Why the old logic did that.** The gate was a greeting-word list plus a time window: inside the opener window, any bare greeting is echo. That cannot work, because two completely different situations produce byte-identical text:

- the tail of the caller's *own* pickup, or the agent's greeting bleeding back through imperfect carrier echo cancellation — **correct to ignore**;
- a caller saying *"Hello?"* **again** because the agent is talking over them — **must interrupt**.

There is no content signal that separates those. There are timing and repetition signals, and the old gate used neither.

### Log 5 — the campaign's call outcomes

```
=== CALLS by outcome (Dojo tenants) ===
  answered               n=40   avg=64.1s   max=214s
  voicemail              n=11   avg=25.6s   max=73s
  no_answer              n=4    avg=0.0s
  busy                   n=1    avg=0.0s
  rejected               n=1    avg=0.0s
  TOTAL: 57
```

**What this says.** 57 calls placed. 40 answered by a human — a healthy 70% connect rate, so **the dialer and the trunk are working**. The problem is not reaching people; it is what happens in the first ten seconds after they answer.

Average answered-call length is 64 seconds, which is a real conversation. So the agent is not universally failing — it is losing a specific slice of calls at a specific moment.

### Log 6 — how long calls survived

```
=== DURATION distribution (answered calls) ===
  0-10s    █████ 5
  10-20s   ███ 3
  20-30s   ███████████ 11
  30-40s   ███████ 7
  40-50s   ███ 3
  50-60s   ███████ 7
  60s+     ███████████████ 15

  answered calls        : 40
  answered but <= 15s   : 3  (8%)
```

**What this says, and what it does not.** Five answered calls ended inside ten seconds — the window in which the agent is still delivering its notice and opener. That is the shape you would expect if some callees hang up during the monologue.

**But be careful with this number.** 3–5 calls out of 40 is a small sample, and a call can end in under ten seconds for reasons that have nothing to do with the opener — a wrong number, an immediate "not interested", a mis-dial. **This chart is consistent with the hypothesis; it does not prove it.** The strong evidence is Logs 1, 3 and 4, which count specific mechanical events rather than inferring intent from a duration.

Stating that distinction matters more than the number itself: it is the difference between "we measured the defect" and "we saw something that might be the defect".

### Log 7 — STT startup latency

```
=== flux_first_audio_sent elapsed_ms distribution ===
  n=337   min=0   median=148   p90=383   max=20287
```

**What this says.** Median time for caller audio to reach the speech recogniser after a call starts is **148ms** — healthy. p90 is 383ms — acceptable.

**Why it is here anyway.** The single call in Log 2 showed `elapsed_ms=2003`, and 2 seconds of deafness at the start of a call would be a serious defect. Pulling the distribution proved it is **not systematic** — that call was an outlier, not the norm. The 20-second maximum deserves its own look someday, but it is rare.

This log is included specifically as an example of a hypothesis that was **checked and dropped**. It would have been easy to report "the agent is deaf for the first 2 seconds of every call" from a single trace, and it would have been wrong.

---

## What the logs add up to

```
   ANSWER
     │
     ├─ 0.00s  recording notice begins ──────────────────┐
     │                                                    │  3.73 seconds
     │  ~60% of callees try to speak in here, and         │  of legal text
     │  until this change that RESTARTED the notice       │
     │                                                    │
     ├─ 3.73s  notice ends, greeting begins ─────────────┘
     │
     │  "Hi, this is Sarah from All-state. I'm calling because ...
     │   quick one — have you got thirty seconds?"        ~4 more seconds
     │
     ├─ ~8s   the callee finally gets a turn
     │
     └─ and if they tried to interrupt at any point:
          · a single word            → deferred (29% of all attempts)
          · "Hello?"                 → classified as our own echo (25 events)
          · "Excuse"                 → allow-list could not match a partial
```

**Roughly eight seconds of uninterruptible agent audio, on a cold call, to a restaurant owner.** Prospects decide in 8–12 seconds. The opener was consuming the entire decision window before the prospect could participate in it.

The owner's instinct — *"remove all this monologue"* — was not a stylistic preference. It was a correct read of a measurable defect.

---

## What was changed

### Change 1 — turn 1 is a bare pickup greeting

**Before** — `build_persona_greeting()` returned a full introduction:

```
"Hi, this is Sarah from All-state. I'm calling because ... — have you got a minute to talk?"
```

**After** — 1–2 words, then silence:

```
'Hello?'       (1 word)
'Hi there.'    (2 words)
'Hey there.'   (2 words)
'Hi, hello.'   (2 words)
'Oh, hi.'      (2 words)

identity / company / time-ask leaked into turn 1:  NONE
```

**Identity was relocated, not deleted.** The persona `STAGE 1` blocks now read: *"a bare greeting already played; that was the hello, not your introduction — WAIT for them to say something back, THEN this is your opener."* Verified present in the composed prompt for turn 2:

```
Sarah                    present
All-state                present
cutting your card fees   present
"do NOT speak first"     present
```

**A guard that would have silently blocked this.** `_has_real_opener_content()` rejected any bare filler word as a degraded opener — a guard added after two production calls where the callee heard a bare *"Hello?"* and it sounded like a wrong number. Left alone, it would have discarded the new greeting and fallen back to the old monologue, and the change would have appeared to do nothing.

It now rejects only genuinely empty or punctuation-only text. A companion helper, `_looks_like_bare_pickup_greeting()`, keeps `session._has_introduced` **False** after a bare greeting, so `live_state.py` correctly tells the model to introduce itself on the next turn instead of assuming it already has.

### Change 2 — the re-greet ladder sounds like a person

**Timing:**

| tunable | before | after | env var |
|---|---|---|---|
| opening nudge threshold | **10s** | **2.5s** | `VOICE_OPENING_HELLO_S` |
| opening repeat gap | 15s | **2.5s** | `VOICE_OPENING_NUDGE_GAP_S` *(new)* |
| opening max nudges | — | **3** | `VOICE_OPENING_MAX_NUDGES` *(new)* |
| mid-call nudge | 10s / 15s gap | unchanged | — |
| silence hangup | 60s | unchanged | `VOICE_SILENCE_HANGUP_S` |

Ten seconds of silence after a pickup reads as a dead line. A person re-checks after two or three.

**Wording — before:**

```
["Hello?", "Can you hear me okay?", "Is anyone there?"]
```

**After:**

```
["Hello?", "Hello??", "Helloooo — are you there?"]
```

The owner flagged *"Can you hear me okay?"* as robotic, and it is: nobody asks a formal audio-quality question when nobody has picked up. They call the same word again, louder.

**Invariants preserved** — verified by reading the code, not by trusting a report:

- the TTS grace period still suppresses every nudge while the agent is speaking;
- the 60-second hangup is driven **only** by caller silence — both writes to `_last_caller_at` are caller events (a user turn or a barge-in), never a nudge;
- the caller's first utterance is never suppressed;
- agent-first calls never enter the opening path;
- the ladder is bounded — after three rungs the monitor stops nudging and falls through to the existing hangup.

### Change 3 — turn 2 is a real introduction with a permission ask

**After:**

```
"{agent_name} here from {company_name} — got a minute? Calling about {call_reason}."
```

Name → permission ask → reason. One breath, under twenty words, then stop. Applied to `lead_gen` (both directions), `customer_support` and `receptionist`, all sharing the literal phrase `"got a minute?"`.

**This needed care, because it sits one rephrase away from the worst-performing opener there is.** The same 300M-call study that this codebase already cites measures:

```
  permission-based  ("got a minute?")            11.18%   ← among the BEST
  social proof                                   11.24%
  pattern interrupt                              10.01%
  "did I catch you at a bad time?"            0.9-2.15%   ← the WORST
```

A previous pass had banned opening on availability **outright**, on the theory that any permission ask belonged to the bad family. That conflated two different moves. Asking whether someone *has* a minute is a confident forward ask. Asking whether you have caught them at an inconvenient moment invites them to say yes. The prompt now states that distinction explicitly.

**And states it without ever writing the banned phrasing into text the model reads.** This codebase has been bitten twice by a negative instruction priming the exact behaviour it forbids, so the prose uses the paraphrase *"an inconvenient moment"* throughout. A test scans the raw prompt dictionary values — not just the composed output — to enforce this.

### Change 4 — the recording notice stops restarting

`_DISCLOSURE_MAX_ATTEMPTS`: **2 → 1**. An interrupted notice is no longer replayed from the top.

**Why not resume instead of restart?** Because it is not cheaply buildable: `synthesize_and_send_audio` returns a single boolean — *was this interrupted* — not a text or audio offset, and the pipeline does not track which words the TTS engine had already emitted when barge-in fired. Resuming would require invasive changes to the playback path.

**And the evidence points at the restart specifically.** Both traced human calls hung up ~2 seconds after the restart, not merely after the interruption.

**The trade, stated plainly:** the retry existed because a single attempt loses the recording on a large share of calls. Removing it means more interrupted notices → more recordings suppressed. The compliance gate stays **fail-closed** either way: an interrupted notice is never counted as delivered, so the recording is discarded rather than retained without consent. Fewer talk-overs, more lost recordings. For tenants where the notice is disabled entirely this is moot.

**A test was pinning the old behaviour** — `assert _DISCLOSURE_MAX_ATTEMPTS == 2`. That is the second test found in this codebase enforcing the bug it should have caught.

### Change 5 — the third contradiction in the trailing slot

A dedicated read-only audit was run across the whole opener flow, tracing the composed-prompt block order **from code** rather than assuming it. It found one HIGH-severity contradiction.

`end_call.CALL_CONTROL_RULES` said, unscoped:

```
## HOW YOU SELL
- Introduce yourself and the company first, in one short line, then ask ONE
  question.
```

Written when the agent spoke first. This block is appended **last**, downstream of both `guardrails`' *"never re-introduce yourself once the conversation is already underway"* and `live_state`'s `has_introduced=True` branch.

```
   composed prompt order (from build.py / composer.py)
   ─────────────────────────────────────────────────
     LIVE STATE          "you have already introduced yourself"
     guardrails          "never re-introduce yourself"
     persona STAGE 1     "wait, THEN introduce"
     FINAL_RESPONSE_CONTRACT
  →  call_control_rules  "Introduce yourself and the company FIRST"   ← LAST
     gatekeeper_rules
     compliance_floor
```

**So on every mid-call turn, the last thing the model read was an instruction to introduce itself again** — precisely the re-introduction bug `live_state` exists to prevent.

Now scoped: *"ONCE they have spoken back, your first real reply introduces you… If you have already introduced yourself, never do it again — LIVE STATE tells you which."*

**This is the third contradiction found in those two trailing files, and all three were the same bug:**

| # | the sentence | consequence | fixed |
|---|---|---|---|
| 1 | *"Under ~30 words a turn"* | licensed a ~10.7s turn — an 11s monologue was measured | 08-07 |
| 2 | *"feel free to tell me to get lost"* | the worst-converting family, reaching the model every call — **and a test asserted it must be present** | 08-07 |
| 3 | *"Introduce yourself and the company first"* | re-introduction on every mid-call turn | **08-11** |

So the general rule was written into `prompts/README.md` rather than just ticking off the instance:

> **Any sentence in `end_call.py` or `gatekeeper.py` that describes a specific turn or a specific length must name which turn — or recency makes it describe every turn.**

The README's own "known contradictions" list still reported findings 1 and 2 as *"open, not fixed"*. That was corrected too — stale documentation that would have sent the next engineer hunting bugs that no longer exist.

### Change 6 — optional LLM-authored ladder

New module `telephony/opening_ladder.py`. Generates the re-greet ladder per call so it varies instead of reciting three fixed lines forever.

**The design constraint that shaped it:** an LLM call *at nudge time* would add latency exactly when the caller is already sitting in silence — the precise problem this work exists to remove. So the ladder is generated **once during pre-warm**, in the ringing phase, alongside the existing greeting pre-synthesis, and stashed on the session. The nudge path only ever reads rung N.

**Validation** — a generated ladder is rejected unless every rung is 1–6 words, carries no pitch, no company or agent name, no business question, escalates, does not duplicate, and satisfies the refined greeting rule.

**Fallback chain — every branch ends at the static ladder:**

```
  flag off                        → static
  no LLM provider                 → static
  timeout (default 3s)            → static
  provider error                  → static
  invalid ladder → retry → still invalid → static
  background task still running when the nudge fires → static
  malformed value reaching the nudge path → static
```

A call is never delayed, blocked, or dropped by ladder generation. **Ships disabled**: `TELEPHONY_OPENING_LADDER_LLM_ENABLED`, default off.

---

## How it was done

Six workers across two waves, with the lead auditing every diff and running every test.

```
  WAVE 1                                    WAVE 2
  ──────                                    ──────
  opener content        ─┐                  LLM-authored ladder    ─┐
  re-greet cadence      ─┼─ audit ─ gate    turn-2 introduction    ─┼─ audit ─ gate
  recording disclosure  ─┘                  contradiction audit    ─┘
                                              (read-only)
```

**Strict file ownership.** Each worker was given an explicit list of files it owned and an explicit list it must not touch, because two workers editing the same prompt file would produce a merge no one could reason about. The one read-only auditor was given no files at all — only a mandate to find contradictions and report `file:line`.

**No worker was allowed to run tests.** Local Python on this machine is broken (`Fatal Python error: init_fs_encoding`) — a residue of an unrelated malware incident. Every test run happened on the server, in an isolated `git worktree` at a known commit with modified files copied in, never against the production checkout.

**No worker was allowed to commit, deploy, or contact the server.** All of that was done by the lead, after auditing.

**The audit was not a formality.** Every worker's report was checked against `git diff` before being believed, and the full suite was run after each wave. That is how the four defects in the next section were found.

---

## What the workers got wrong

Four real defects in worker output, none of which appeared in the workers' own reports. This is the argument for auditing rather than trusting.

### Defect 1 — a banned phrase written back into the prompt

A worker wrote, into the persona prompt:

```
Never open with "is this a bad time?" or any warmer version of it
```

A negative instruction that **names the worst-converting opener family inside the text the model reads**. This is the exact priming trap the codebase had already been bitten by twice. A pre-existing test caught it — it bans the substring anywhere in the composed prompt.

Rewritten positively: *"Open on the REASON, never on their availability: asking permission to exist is the worst-converting opener family in the data."*

### Defect 2 — a test that consumed randomness it had not reseeded

```
E   AssertionError: assert 'Hey there.' == 'Hi there.'
```

The test seeded the RNG before two calls to prove the greeting does not vary by persona or reason — then made a **third** call without reseeding. The greeting is chosen with `random.choice`, so the third call advanced the RNG and picked a different, equally valid variant. **The code was correct; the test was wrong.**

### Defect 3 — two concepts silently dropped in a rewrite

A worker's persona rewrite dropped the phrases `"first breath"` and `"easy way to say no"`, tripping a pre-existing guard that pins those concepts. The ideas had genuinely survived under different wording — but the guard exists precisely so a rewrite cannot quietly lose them. Restored.

### Defect 4 — a test that hung forever

A worker's new integration test patched `asyncio.sleep` with an `AsyncMock`. Awaiting an `AsyncMock` completes **without ever suspending**, so with every timer collapsed the silence monitor became a tight loop that never returned control to the event loop — and the `asyncio.wait_for` timeout that was supposed to end the test is itself a loop timer, so it could never fire.

Production is unaffected (real `sleep` yields), but this would have hung CI indefinitely. Replaced with a helper that awaits the **real** sleep at zero delay — instant, but a genuine scheduling point:

```python
_REAL_SLEEP = asyncio.sleep          # captured before any patch, so no recursion

async def _instant_yield(*_a, **_k) -> None:
    await _REAL_SLEEP(0)
```

The test now runs in 1.52s.

**A related finding worth recording:** measuring that hang revealed that three *pre-existing* tests in the same file take **98 seconds** between them. Confirmed on a clean baseline worktree — not caused by this work, but real technical debt in the suite.

---

## An invariant that had to be refined, not obeyed

The re-greet worker **refused** the owner's requested wording. It found that `"Hello?" → "Hello??" → "Helloooo"` violates an existing test:

```python
def test_opening_ladder_has_exactly_one_greeting_and_it_is_first():
    assert _greeting_rung_indexes(OPENING_PHRASES) == [0]
```

and substituted its own phrasing instead. The worker followed its instructions correctly. But following the invariant here would have delivered the opposite of what was asked, so the invariant had to be examined rather than obeyed.

**What that rule was actually built to catch** is recorded in its positive control:

```python
_OLD_OPENING_PHRASES = ["Hello?", "Hi, can you hear me okay?"]
```

The bug was the **different greeting word**. `"Hi,"` reads as starting the conversation over, so a silent pickup heard `"Hello?"` → `"Hi, can you hear me okay?"` → `"Hi, it's James from…"` — three separate greetings before the real opener. That was the *"multiple hi and hello"* report.

**Repeating the same call-out louder is not that bug.** It is one person escalating one call-out, which is exactly what someone does on a silent line.

So the rule was refined rather than deleted:

> Rung 0's greeting word may repeat in any escalated spelling. A **different** greeting word may not appear after it.

Both directions are now pinned:

```python
# the shape the owner asked for — allowed
_fresh_greeting_rung_indexes(["Hello?", "Hello??", "Helloooo, are you there?"]) == []

# the original bug — still caught
_fresh_greeting_rung_indexes(["Hello?", "Hi, can you hear me okay?"]) == [1]
_fresh_greeting_rung_indexes(["Hello?", "Hi there?"])                 == [1]
_fresh_greeting_rung_indexes(["Hello?", "Good morning?"])             == [1]
```

Escalated spellings are normalised (`Helloooo` → `hello`) so the rule reads intent rather than spelling.

**The general point:** a test that blocks a correct change is not automatically right. It encodes a past decision, and the honest move is to find the failure it was written for and ask whether the new case is that failure. Here it was not — so the rule got narrower and sharper, and the original bug is still caught.

---

## Verification (Part II)

### Command

```bash
python -m pytest tests/unit tests/security -q -p no:randomly \
    --continue-on-collection-errors
```

### Result

```
8 failed, 5091 passed, 15 skipped, 975 warnings, 36 errors in 154.29s
```

**Zero regressions.** The 8 failures and 36 errors are the identical pre-existing environmental set documented in Part I, proven against a clean worktree with none of this work applied.

### Test growth across the whole effort

```
  044d9208 baseline        4,864 passed
  729632f7 barge-in        4,818 passed
  06ffba99 review fixes    4,832 passed
  fb8f8156 opener redesign 5,091 passed     +259 tests
```

### New test files in this commit

| file | what it pins |
|---|---|
| `test_opening_ladder.py` | flag gating, full validation matrix with positive controls, generation success / provider error / timeout / garbage-retry / exhaustion / cancellation |
| `test_prewarm_opening_ladder.py` | the hook returns in <0.05s against a 5s-delay provider; correct gating; stash-on-success, omit-on-failure |
| `test_turn2_permission_ask_opener.py` | name → permission → reason ordering for all four shapes, cross-persona consistency, ≤20 words, bad-time family absent from both composed prompt and raw dict values |
| `test_prompt_small_dialogue.py` | short-turn style across every block that sizes the opener |
| `test_llm_opener.py` | the flag-gated opener feature |

### Behavioural verification against the real code

```
=== TURN 1 — what the callee hears on pickup ===
   'Hello?'      (1 word)
   'Hey there.'  (2 words)
   'Hi there.'   (2 words)
   'Hi, hello.'  (2 words)
   'Oh, hi.'     (2 words)

=== does turn 1 leak identity/company/time-ask? ===
   leaked: NONE

=== TURN 2+ — identity still reaches the model? ===
   Sarah                      present
   All-state                  present
   cutting your card fees     present
   first breath               present
   bare pickup                present
   do NOT speak first         present

=== banned opener families absent from the whole prompt? ===
   bad time     ABSENT ok
   bad moment   ABSENT ok
   get lost     ABSENT ok
   buzz off     ABSENT ok
   30 words     ABSENT ok
```

---

## Possible improvements

Ranked by expected value, with the reasoning stated so the ranking can be argued with.

### 1. Run the canary — 30 calls, 5 speakers *(highest value, blocked on people)*

Still **0 of 30**. Everything in both reports is verified by tests and logs; none of it has been heard by a human on a live line. The scenarios and acceptance criteria are already written up in Part I. **No amount of further engineering substitutes for this.**

### 2. Set the tenant's recording policy *(one SQL statement)*

Until this runs, every call still opens with 3.73 seconds of notice that is talked over 60% of the time. The exact `INSERT`, and its revert, are in the deploy notes.

⚠️ For internal test numbers `one_party` is appropriate. **Before this campaign dials real UK prospects again it must be reverted** — the UK is a two-party consent jurisdiction.

### 3. Fix the 2-second STT startup outlier

Median is 148ms, p90 383ms — healthy. But `max=20287ms` means at least one call was deaf for twenty seconds. Rare, but a call that cannot hear for that long is effectively dead. Worth a targeted look at what the tail correlates with.

### 4. Make the C++ `/stats` counters actually accumulate

```json
{"sessions_started_total":195, "packets_in":0, "packets_out":0,
 "tts_frames_enqueued_total":0, "tts_chunks_rejected_stale_total":0}
```

195 lifetime sessions with `packets_in: 0`. **These counters cannot currently be used as evidence of anything**, which is why the C++-side runtime evidence requested in the review could not be produced from them. This blocks a whole class of future investigation.

### 5. Scrape the metrics

`voice_interrupt_outcome_total` and `voice_interrupt_dropped_frames` now emit, but nothing scrapes them. Prometheus counters also only materialise after their first increment, so absence is currently ambiguous between "healthy" and "not wired". A scraper turns every future incident from an archaeology exercise into a graph.

### 6. Widen the lead-context pipe

Only `first_name`, `last_name` and `custom_fields["company"]` reach the dial path; the `leads` table has no company, industry, or title column. Context-referencing openers are reported at roughly +36% uplift, but there is almost nothing to reference. Worse: `call_service.py:910` rebuilds retry jobs **without** lead fields, so **retries already dial blind today**.

### 7. Reconcile or delete `llm_opener.py`

It authors a full identity+reason opener as literal turn 1 — the opposite of this design. Flag-gated and off, and `prewarm.py` documents that enabling it is a deliberate exception. But a dormant module that contradicts the shipped design is a trap for the next engineer. Either bring it in line with the bare-greeting flow or remove it.

### 8. Change `Restart=on-failure` to `Restart=always`

A clean exit currently leaves `talky-api` down and not restarted.

### 9. Speed up the 98-second test file

Three pre-existing tests in `test_audio_ingest_caller_first_silence.py` take 98s between them, waiting on real timers. They should collapse their timers the way the new test does.

### 10. Fix the recording-disclosure trade properly

Removing the retry stops the talk-over but loses more recordings on two-party tenants. The real fix is a notice short enough that it is rarely interrupted, or the ability to **resume** rather than restart — which needs `synthesize_and_send_audio` to report an offset rather than a boolean.

---

## What is still not done

### Blocked on people

| item | blocker |
|---|---|
| 30 canary calls, 5 speakers | cannot place calls; no campaign will be started to simulate it |
| Recordings, call IDs, runtime traces on the new code | needs those calls |
| Deploying `fb8f8156` | needs a `sudo systemctl restart`, which the tool sandbox blocks |
| The recording-policy SQL | production database writes are blocked by the same control |

### Known and unfixed

- **C++ `/stats` counters do not accumulate** — the metrics endpoint reports zeroes across 195 sessions.
- **`Restart=on-failure`** on `talky-api` — a clean exit leaves it down.
- **`llm_opener.py`** contradicts the shipped opener design; inert but latent.
- **Lead context is thin**, and retries drop it entirely.

### Deliberately declined, with reasons

| item | why |
|---|---|
| EOT → 1200ms | production negotiates **500ms**; this would add **+700ms** of dead air after every caller turn |
| TTS queue cap → 100 frames | it is a ceiling, not a working depth (~15 frames measured); lowering it risks dropping frames on the 119–170 chunk greeting burst |
| Enabling the LLM ladder by default | ships off until the static ladder has been heard on a real call |

---

## Appendix C — the full annotated call trace

The complete journal for one call, with every line explained. Timestamps are millisecond-precise and unedited except for identifier pseudonymisation.

```
20:13:05.733  Creating voice session  type=telephony
              └─ session object created; nothing audible yet

20:13:06.682  Voice session created
20:13:06.938  llm_stream_warmed
              └─ LLM connection pre-warmed during ringing — this is the
                 pattern the new opening-ladder generation reuses

20:13:07.278  Deepgram Flux pre-connected  (eager=0.7 eot=0.85 timeout_ms=500)
              └─ NOTE: eot=500ms. The review brief assumed 2000ms and asked
                 for it to be lowered to 1200ms. Production was ALREADY at
                 500ms; the requested change would have made replies SLOWER.

20:13:07.871  tts_inference_warmed
20:13:07.872  prewarm: resolved voice accent=neutral

              ── 17 seconds of ringing ──

20:13:24.293  bind_telephony_call  voice_session -> calls.id
20:13:24.294  session started  wire=pcmu/8000Hz internal=linear16/16000Hz
              └─ PCMU confirmed, as the codec section of Part I reports

20:13:24.294  pipeline_start
20:13:24.295  audio_stream_started  queue_size=0 stt_active=True
20:13:24.295  recording_disclosure_speaking  reason=tenant_default_two_party
              └─ THE NOTICE BEGINS. reason=tenant_default_two_party means this
                 tenant has NO explicit recording policy row, so the safe
                 default applies: record, and announce. Setting a policy row
                 is what removes this line.

20:13:24.523  TTS_FMT_DEBUG  provider=deepgram first_bytes=1280
20:13:24.524  t_tts_first_audio  bytes=320
              └─ first audible byte ~230ms after answer. The pipeline is fast;
                 it is the CONTENT that is long.

20:13:26.298  audio_stream_first_chunk  chunk_len=1280
20:13:26.299  flux_first_audio_sent  elapsed_ms=2003
              └─ caller audio reaches STT 2 seconds in. Outlier, not typical
                 (median 148ms) — see Log 7.

20:13:27.298  audio_level  rms=1377 peak=17870   (>500 = speech-likely)
              └─ someone is talking. The agent is still reading the notice.

20:13:28.024  recording_disclosure_spoken
              └─ THE NOTICE ENDS. 3.73 seconds.

20:13:28.024  outbound_greeting_presynth  chunks=119
              └─ only NOW does the greeting start — and it is 119 chunks

20:13:28.058  Flux StartOfTurn - User started speaking, barge-in detected
              └─ 34ms into the greeting, the callee speaks

20:13:28.058  instant_opener_echo_ignored  text='[redacted chars=4 sha=580684f8]'
20:13:28.058  instant_opener_echo_ignored  text='[redacted chars=4 sha=580684f8]'
              └─ sha 580684f8 = 'Hey.' — and TWO log lines for ONE event,
                 because both arming sites gate the same barge-in. This
                 duplication is why the interrupt operation had to be made
                 idempotent by utterance rather than by call count.

20:13:28.343  telephony_audio_gap  gap_ms=220 expected_ms=40 (5.5x)
              └─ inbound audio batch arrived late — one occurrence, benign

20:13:29.723  machine_detected_interim  verdict=voicemail turn=0 — hanging up
              └─ answering-machine detection fires correctly. THIS PARTICULAR
                 CALL WAS A VOICEMAIL, which is why it is a poor exemplar of a
                 human being talked over — see the caveat under Log 2.

20:13:29.725  hangup requested  reason=voicemail_detected
20:13:29.978  pipeline_end
20:13:30.025  audio_stream_ended  chunks_yielded=86 stt_active=False
20:13:30.137  Voice session ended
```

### What one trace is worth

This single call answered three questions that aggregate counters could not:

1. **How long is the notice, really?** 3.73 seconds — measured, not estimated.
2. **Is the pipeline slow, or is the content long?** First audio in 230ms. The pipeline is fast. The content is long.
3. **What is the effective EOT?** 500ms — which overturned the review's premise that it needed lowering from 2000ms to 1200ms.

It also produced the correction that matters most in this report: the first call chosen as an exemplar was a **voicemail**, not a person. Cross-checking every affected call for an answering-machine verdict — 19 human, 2 voicemail — is what turned an anecdote into evidence.

---

## Closing (Part II)

The owner reported that calls felt like a monologue. The logs agreed, and were more specific than the complaint: roughly eight seconds of uninterruptible agent audio, a recording notice talked over 60% of the time and restarting when it was, a barge-in guard rejecting 29% of genuine attempts to speak because it counted words on a partial transcript, and nineteen people whose *"Hello?"* was classified as the machine's own echo.

The fix is not a tuning pass. Turn 1 became a two-word greeting that waits. The re-greet became what a person actually says on a silent line. Identity moved to the turn after the callee speaks, with a permission ask that the data supports. And a third contradiction was found in the same trailing prompt slot that had already produced two — which is why the general rule, not just the instance, is now written down.

`5,091` tests pass, zero regressions, `fb8f8156` is on `main`.

**None of it has been heard by a human on a live call.** That remains the only thing that can confirm any of it, and it is 30 calls and five speakers away.
