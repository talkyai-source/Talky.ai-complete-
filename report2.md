# Report 2 — Review Findings, Fixes, and Production Deployment

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
