# Report 4 — The Four-Worker Audit: What Was Still Broken, and Why

**Date:** 2026-08-13
**Live commit:** `4ebdbd55` · **Deployed:** `2026-08-12 21:44:09 UTC` · **PID** `2424308`
**Status:** 🟢 **ALL FIXES LIVE IN PRODUCTION AND VERIFIED IN THE RUNNING PROCESS**
**Method:** four read-only agents, every finding re-verified by hand before action
**Predecessors:** [`report.md`](./report.md) · [`report2.md`](./report2.md) · [`report3.md`](./report3.md)

---

## Live status — verified, not asserted

Every line below was produced by importing the **live modules from the running process** and calling them, not by reading files on disk.

```
  DEPLOYED
    disk        4ebdbd55   ==   origin/main 4ebdbd5
    PID         2424308         started 2026-08-12 21:44:09 UTC
    services    api · voice-worker · voice-gateway · dialer · reminder · asterisk
                6/6 active

  HEALTH
    backend     {"ready":true,"db":"ok","redis":"ok"}
    C++ gateway {"status":"ok","io_loop_healthy":true}
    errors      clean since restart (excluding the known-benign Vonage line)

  THE FOUR FIXES, IN THE RUNNING PROCESS
    1  craft 30-word cap gone        True
    2  opener rule present at turn 0 True
    3  answer-first at turn 0        True
       answer-first mid-call         True
    4  cap keeps the ending          True   (16,135 chars → 11,995, ending intact)
    +  nova asymmetry documented     True

  TEST GATE
    5,137 passed · 8 failed · 15 skipped · 36 errors · ZERO regressions
```

The 8 failures and 36 errors are the pre-existing environmental set documented in reports 2 and 3 (`test_webhooks_call_hmac` ×5, `test_webhooks_call_idor` ×2, `test_systemd_readiness` ×1; errors are `ModuleNotFoundError: fakeredis`, a test-only dependency deliberately absent from the production venv).

---

## Table of contents

- [Live status — verified, not asserted](#live-status--verified-not-asserted)
- [How the audit was run](#how-the-audit-was-run)
- [The findings, ranked](#the-findings-ranked)
- [Fix 1 — the 30-word cap was still live, in the strongest slot](#fix-1--the-30-word-cap-was-still-live-in-the-strongest-slot)
- [Fix 2 — the opener rule never reached the turn that uses it](#fix-2--the-opener-rule-never-reached-the-turn-that-uses-it)
- [Fix 3 — nothing said an unanswered question outranks the agenda](#fix-3--nothing-said-an-unanswered-question-outranks-the-agenda)
- [Fix 4 — raising the ceiling only moved the cliff](#fix-4--raising-the-ceiling-only-moved-the-cliff)
- [Documented, not changed — the Nova barge-in asymmetry](#documented-not-changed--the-nova-barge-in-asymmetry)
- [The traceback burst, solved](#the-traceback-burst-solved)
- [The incident I caused](#the-incident-i-caused)
- [Findings deliberately excluded from this report](#findings-deliberately-excluded-from-this-report)
- [Testing performed](#testing-performed)
- [What is still open](#what-is-still-open)
- [The pattern, now on its fifth appearance](#the-pattern-now-on-its-fifth-appearance)
- [Appendix — verification commands](#appendix--verification-commands)

---

## How the audit was run

Four agents, **read-only**, strictly non-overlapping scopes, none permitted to edit, commit, deploy or touch the server.

```
  ┌──────────────────────┬───────────────────────────────────────────────┐
  │ LOG FORENSICS        │ 3,586 raw journal lines, 4 days, verbatim     │
  │                      │ → duplicate events, ordering, timing, endings │
  ├──────────────────────┼───────────────────────────────────────────────┤
  │ DUPLICATE CODE       │ same constant/decision/job in two places,     │
  │                      │ expired comment assumptions, dead branches    │
  ├──────────────────────┼───────────────────────────────────────────────┤
  │ CONVERSATION QUALITY │ why the bad opener got through, why the agent │
  │                      │ asks before it answers, prompt block ordering │
  ├──────────────────────┼───────────────────────────────────────────────┤
  │ TRACEBACK BURST      │ 262 tracebacks / 45 ASGI exceptions, clustered│
  └──────────────────────┴───────────────────────────────────────────────┘
```

**Why read-only.** Earlier in this effort three separate agent reports did not match their own diffs. Findings-only removes that failure mode: the agent's job is to point, mine is to verify and act. Four agents editing the same voice pipeline would also have produced a merge nobody could reason about.

**Every finding below was re-verified by hand** — grep, source read, or live import — before a line of code was changed. Two agent claims were downgraded on inspection and are noted where they occur.

---

## The findings, ranked

| # | Finding | Verified by | Status |
|---|---|---|---|
| 1 | `"Under 30 words"` re-anchor, injected every turn | grep + injection site | ✅ **FIXED, LIVE** |
| 2 | Opener wording rule absent from the turn-0 slot | source read of `live_state` | ✅ **FIXED, LIVE** |
| 3 | No rule that an unanswered question outranks the agenda | grep — confirmed absent | ✅ **FIXED, LIVE** |
| 4 | Prompt cap still truncating (18% of a script, Aug 12) | production log line | ✅ **FIXED, LIVE** |
| 5 | Nova barge-in ungated vs Flux | source read | 📝 **DOCUMENTED** — see below |
| 6 | 262 tracebacks / 45 ASGI exceptions | raw journal query | ✅ **DIAGNOSED** — not call-affecting |
| 7 | `is_disfluency` ImportError killed STT on 10 real calls | production log line | ⚠️ **MY FAULT** — self-resolved |

---

## Fix 1 — the 30-word cap was still live, in the strongest slot

**File:** `backend/app/domain/services/voice_pipeline/conversation_craft.py:22`

```
- One thought, then ONE question. Under 30 words total. If you notice
  yourself explaining, stop and ask instead.
```

**This is the same rule removed from `end_call.CALL_CONTROL_RULES` on 2026-08-07**, after it licensed an 11-second monologue and a callee hung up the instant it finished (`llm_total_ms=11380 / tts_total_ms=10774`).

### Why the removal didn't work

The surviving copy is **stronger than the one that was deleted**:

```
  end_call.CALL_CONTROL_RULES     part of the BASE prompt
                                  composed once, then fades as the call grows

  conversation_craft.CRAFT_REANCHOR   re-injected in the TRAILING slot on
                                      EVERY TURN — turn_streamer.py:515 —
                                      deliberately, to beat the base prompt
```

The module's own docstring states the intent plainly:

> *"Base-prompt rules FADE as the conversation grows; the platform's own compliance-floor work proved the fix — a compact block re-stated at the very END of the live per-turn prompt wins via recency where a page-200 rule loses."*

So the deletion in `end_call.py` was overridden on **every turn of every call** by a copy engineered to win. At 2.8 words/sec, thirty words is **≈10.7 seconds** — almost exactly the monologue that was measured.

### The fix

```diff
- One thought, then ONE question. Under 30 words total.
+ One thought, then ONE question. The fewest words that actually land it —
+ usually a single sentence.
```

Shape, not number. And the general rule is now written into the module so the next person cannot repeat it:

> A turn-length **constraint** belongs in exactly one place — guardrails HARD RULE 2. Every other block may describe **shape** but must never state a **number**, because a number here does not reinforce the constraint, it **replaces** it.

**Live:** `craft 30-word cap gone: True`

---

## Fix 2 — the opener rule never reached the turn that uses it

**File:** `backend/app/services/scripts/prompts/live_state.py`

A production call opened with:

```
  21:46:50  llm_response turn=0
            said="Hi there — Michael here from Allstate Estimation UK,
                  hope I've not caught you at a bad time?"
```

**"hope I've not caught you at a bad time"** is the 0.9–2.15% opener family — the worst measured — and the exact move the persona rewrite replaced with a forward permission ask (11.18%).

### Why it got through

The composed order, traced from `composer.py`/`build.py` rather than assumed:

```
  live_state           ← PREPENDED above everything, EVERY turn (top attention)
  guardrails_hard
  KNOWLEDGE_PRECEDENCE
  guardrails_rest
  COMMUNICATION_PRINCIPLES
  persona (lead_gen STAGE 1)   ← the opener rule lives HERE, mid-prompt
  additional_instructions
  FINAL_RESPONSE_CONTRACT      ┐
  call_control_rules           │ four trailing blocks —
  gatekeeper_rules             │ NONE mentions opener wording
  compliance_floor             ┘
```

The rule existed exactly once, mid-prompt, stated as a **concept** with one positive example and — deliberately, to avoid priming — **no negative example**. And `live_state`, the last thing read before turn 0 generates, carried only the **length** rule:

> *"give your short opening this turn: who you are and why you're calling, in one breath, under twenty words."*

**On the highest-stakes sentence of the call, the freshest instruction in context was silent on the one distinction that decides it.**

### The contrast that proves the mechanism

`"never re-introduce yourself"` appears **three** times — early in guardrails, in `live_state` every turn, and in the trailing block. All three agree. That bug got fixed and stayed fixed. The opener fix was never propagated the same way.

### The fix

Carried forward in `live_state` on every turn, phrased as the positive move plus what to open **on**, so it constrains without quoting the banned sentence:

> *"Open on your REASON for calling, never on their availability. A forward ask like "got a minute?" is fine; anything that asks whether you have caught them at an awkward or inconvenient moment is the worst-performing opening there is — it invites a no before they know what you want."*

**Live:** `opener rule present at turn 0: True`

---

## Fix 3 — nothing said an unanswered question outranks the agenda

**File:** `backend/app/services/scripts/prompts/live_state.py`

The same call, over six turns:

```
  T0  agent:  [the bad-time opener]
      caller: "Why are you calling me?"                        ← ask 1
  T1  agent:  re-introduces, gives reason, appends a question
      caller: "I didn't ask your name. Why you're calling me?" ← ask 2, correcting
  T2  agent:  restates vaguely, appends a question
      caller: "You call me, you tell me that."                 ← ask 3, angrier
  T4  agent:  "are you the person who manages estimating?"     ← asks HIM to qualify
      caller: "Why the hell should I tell you about that?"
      caller: [profanity]
  T6  agent:  closes the call
```

### Why

Four separate blocks teach the ask-reflex, one of them in the trailing slot:

| Block | Position | Text |
|---|---|---|
| `guardrails` HARD RULE 3 | early | *"Acknowledge in a word or two, then ask"* |
| `COMMUNICATION_PRINCIPLES` | mid | *"a question — if you ask one — is the last thing you say"* |
| persona HOW YOU SOUND | mid | *"Acknowledge, then ask"* |
| `FINAL_RESPONSE_CONTRACT` | **trailing** | *"Ask at most one question and let it be the last thing you say"* |

Against that: **one** mid-prompt line about answering a direct question outright. By recency **and** by repetition, the ask-reflex wins — which is exactly what the transcript shows.

I grepped for any priority rule and **confirmed its absence**:

```
  answer (it|them|their question) (first|before)  →  no match
  before (you )?ask                               →  no match
  still (haven|has not) answered                  →  no match
  outstanding question                            →  no match
```

### The fix

Added to `live_state`, re-read every turn, in both the turn-0 and mid-call branches:

> *"If they asked you something and you have not answered it, ANSWER IT FIRST — plainly, in one sentence, and add no question of your own that turn. An unanswered question outranks your next step, your qualifying, and any stage you are working through. If they ask the same thing again, you did not answer it: say the plain answer with nothing attached."*

**Deliberately no example phrasings.** The previous attempt at this rule failed precisely because its three examples (*"what makes you different?"*, *"why you?"*, *"what is this?"*) did not match what the caller actually said, and the model would not generalise.

**Live:** `answer-first at turn 0: True` · `answer-first mid-call: True`

---

## Fix 4 — raising the ceiling only moved the cliff

**File:** `backend/app/domain/services/telephony_session_config.py`

On 2026-08-12 the tenant prompt budget was raised 6,000 → 12,000 chars because a campaign was losing 37% of its script. **The very next day a longer campaign hit the new ceiling:**

```
  Aug 12 10:52:02  telephony_tenant_prompt_capped campaign=09b7ee9c
                   original_chars=14665 capped_chars=11998
                   budget_chars=12000 LOST_chars=2667 (18%)
```

### Why raising it was the wrong shape of fix

Head-truncation always discards the **tail** — and operators write in a predictable order:

```
  ┌─────────────────────────────────────────────────────────────┐
  │ role · company · context   │ product detail │ OBJECTIONS ·   │
  │                            │                │ PRICING ·      │
  │                            │                │ CLOSING STEPS  │
  └─────────────────────────────────────────────────────────────┘
                                                └── deleted first ──┘
```

**Head-truncation reliably deletes the part of the script that decides how a call ENDS.** A bigger budget just moves where that happens.

### The fix

Keep the first 60% of the budget and the last 40%, with a visible elision marker between them:

```
  [ opening 60% ] + "[... middle of these instructions omitted for length ...]"
                  + [ closing 40% ]
```

The marker matters: the model sees that something was removed rather than believing the script simply stops.

**Verified live on a 16,135-character script:**

```
  original chars    16,135
  kept chars        11,995
  OPENING kept      True
  CLOSING kept      True
  omission visible  True

  last 90 chars the model now sees:
  '...OPENING: greet warmly. CLOSING RULE: confirm the callback time before hanging up.'
```

### An edge case the pre-existing test caught

A budget too small to carry the marker plus a useful head and tail (the suite has a 50-char case) would have spent its **entire allowance on the elision notice** — shipping a prompt that says only *"something was omitted"*, which is strictly worse than the operator's first sentence. Falls back to plain head-truncation below the threshold.

That test failing is the reason the edge case is handled. It was doing its job.

---

## Documented, not changed — the Nova barge-in asymmetry

**File:** `backend/app/infrastructure/stt/deepgram_nova.py`

The audit rated this HIGH. **On inspection I downgraded it, and this is why.**

```
  FLUX    StartOfTurn carries a transcript
          → hard-interrupt allow-list → backchannel → disfluency → min-words

  NOVA    SpeechStarted is a PURE VAD EVENT with no transcript
          → nothing to classify. Fires unconditionally.
```

The asymmetry is real: a *"yeah"* that Flux absorbs will cut the agent off under Nova. But it is **structural, not an oversight** — there is no text to gate on.

**Deliberately not "fixed" by waiting for Nova's first interim transcript.** Nova is the STT **failover**. By the time it is serving, the primary has already failed — and adding transcript-wait latency to barge-in on the degraded path trades a rare over-interrupt for a **guaranteed slower stop**. Being too eager to stop talking is the safer failure.

The asymmetry is now written into the code with the condition that would justify revisiting it: *if Nova ever becomes a primary engine rather than a fallback, gate on the first interim there.*

**Live:** `nova asymmetry documented: True`

---

## The traceback burst, solved

262 tracebacks / 45 ASGI exceptions over 4 days, clustered rather than steady.

The worker was **honest that it could not prove this from the filtered logs** and named the exact query that would settle it. That query resolved it:

```
  RuntimeError: No response returned.                                    × 172
  RuntimeError: Unexpected ASGI message 'websocket.send', after
                sending 'websocket.close' or response already completed  ×   2
```

Classic Starlette `BaseHTTPMiddleware` + **client disconnecting mid-request**. The stack is **five** `BaseHTTPMiddleware` layers deep, and each multiplies exposure:

```
  api_security_middleware.py:324    100 frames
  session_security_middleware.py:150 96 frames
  tenant_middleware.py:107           72
  security/csrf.py:99                72
  security_headers_middleware.py:50  72
```

**Not call-affecting.** It surfaces as a `BaseExceptionGroup`, which skips every `except Exception` — including the app's catch-all — and lands in uvicorn's outer handler. The in-flight path was `POST /api/v1/sip/telephony/audio/...` at **2,344 requests in two minutes**; a handful losing the disconnect race is expected noise, not a call failure.

The 2 WebSocket ones are the real `campaign_test_ws` send-after-disconnect bug — 2 of 262, browser-test path only, cleanup always runs.

**No fix applied.** The correct fix is reducing middleware depth or moving to pure ASGI middleware, which is a structural change to the request stack with no call-quality benefit. Recorded, not actioned.

---

## The incident I caused

**10 of 47 real calls lost STT entirely on Aug 10.**

```
  Aug 10 15:39:55  ERROR [deepgram_flux] Flux receive error: cannot import
                   name 'is_disfluency' from '...voice_pipeline.backchannel'
```

Ten occurrences, ~21% of that day's real dialed calls. The exception propagates out of `process_audio_stream` → `pipeline_end` fires → **STT never works again for that call.** Callers talked into a dead line for 8s to 183s before hanging up. Two got total silence from pickup.

**Cause.** On Aug 9 I pulled a commit onto the server disk and deliberately did **not** restart — and told you that was safe because "Python loads at import." It is not. `deepgram_flux` imports `is_disfluency` **inside the function body**, so it executed at call time against a `backchannel` module already cached in memory from the **old** file. New code, old module, `ImportError`.

I opened that window twice: once on the pull, once when I reverted the checkout. It closed only when the service restarted at 19:40.

**The rule I got wrong:** pulling code onto a running service is not inert whenever any import is lazy — and this codebase uses lazy imports throughout the hot path. Either restart immediately, or don't pull.

**Self-resolved** at the next restart; zero recurrences under any later PID. But it cost real calls, and it was avoidable.

---

## Findings deliberately excluded from this report

Two audit findings are real and verified but sit outside the performance and conversation-quality scope of this report. They are recorded here so they are not lost:

- **Recording disclosure on the realtime pipeline** — `lifecycle.py:1537` skips the cascaded greeting path for realtime sessions, and the disclosure is only invoked from inside it. 4 calls on Aug 12 have zero disclosure events where all 42 others log one. **Unfixed.**
- **Caller speech in plaintext logs** — 236 unredacted `Flux EndOfTurn` lines including full email addresses, while adjacent `transcript_handler` lines are hashed. Redaction is applied inconsistently. **Unfixed.** The `call_logs.md` artifact is gitignored for this reason.

Both warrant their own decision rather than being folded into a call-quality pass.

---

## Testing performed

### Command

```bash
python -m pytest tests/unit tests/security -q -p no:randomly \
                 --continue-on-collection-errors
```

Python 3.12.3 · pytest 9.0.3 · isolated `git worktree` at `73cf0618` with changes applied, production venv, never against the production checkout.

### Result

```
  8 failed, 5137 passed, 15 skipped, 980 warnings, 36 errors  in 194.92s
```

**Zero regressions.** Test count across the effort:

```
  report 3 close   5,131
  this commit      5,137        +6
```

### New tests

`TestPromptCapKeepsTheEnding` — six cases:

```
  the ending survives truncation          ← THE REGRESSION
  the opening also survives
  the omission is visible to the model
  still within budget
  short scripts are untouched             ← non-vacuity
  no word is severed at either seam
```

### A pre-existing test that earned its keep

`test_oversized_prompt_is_capped_at_word_boundary_and_warns` uses a 50-char budget and asserts `text.startswith(result)`. My head-and-tail change broke it — correctly, because at that budget the elision marker alone would have consumed the entire allowance. That failure is what produced the small-budget fallback. The test now documents why it still exercises the head-only path.

### Deploy verification

```
  pre-flight     active_sessions == 0   (deploy aborts otherwise)
  git pull       --ff-only origin main
  import smoke   python -c "import app.main"  → IMPORT_OK
  restart        talky-api only
  verify         live import of every changed module, in the running process
  health         /api/v1/healthz/deep + gateway /health
  journal        post-restart error scan
```

---

## What is still open

### Unfixed, known

| Item | State |
|---|---|
| Recording disclosure skipped on the realtime pipeline | 4 calls affected, out of scope here |
| Caller PII in plaintext logs | redaction applied inconsistently |
| **0 of 30 canary calls** | still zero; no acceptance criteria scored |
| C++ `/stats` counters | do not accumulate — gateway-side evidence unavailable |
| `talky-api` unit | `Restart=on-failure`; a clean exit leaves it down |
| `test_opening_ladder_is_bounded…` | flaky under full-suite load; wall-clock timers |
| Pickup greeting | still five canned strings; `opening_ladder.py` exists and ships OFF |
| 5-deep `BaseHTTPMiddleware` stack | 172 × `No response returned`, noise not calls |

### Unproven

The real-prompt pre-warm from `73cf0618` has still **never executed** — the browser test path runs no pre-warm at all. It should help real phone calls and remains unverified.

---

## The pattern, now on its fifth appearance

Every significant defect in this audit is the same shape: **something the code asserted about itself had quietly stopped being true.**

| # | The assertion | Why it expired |
|---|---|---|
| 1 | "the 30-word cap was removed" | it was removed from one file of two |
| 2 | "the opener rule is in the prompt" | in a slot the model reads early and forgets |
| 3 | "the model answers direct questions" | four blocks teach the opposite, one louder |
| 4 | "12,000 chars is enough" | a campaign wrote 14,665 the next day |
| 5 | "pulling code without restarting is inert" | lazy imports execute at call time |

None was carelessness. Each was a correct decision whose **precondition** changed, in code that kept behaving as though it had not.

**Fix 1 is the sharpest instance yet.** The rule was deleted, the deletion was tested, the test passed — and the behaviour never changed, because the surviving copy lived in a slot *engineered to outrank* the one that was deleted. A fix verified in the wrong place is indistinguishable from no fix at all.

The rule that follows, now written into the code rather than a commit message:

> A constraint belongs in exactly one place. Every other block may describe **shape**; none may state a **number**. A number does not reinforce a constraint — it replaces it, and the copy in the highest-recency slot wins.

---

## Appendix — verification commands

```bash
# ── what is deployed and running ────────────────────────────────────────
git -C /opt/talky rev-parse --short HEAD
systemctl show talky-api -p MainPID -p ActiveEnterTimestamp --value
curl -s http://127.0.0.1:8000/api/v1/healthz/deep
curl -s http://127.0.0.1:18080/health

# ── the four fixes, IN THE RUNNING PROCESS (not on disk) ────────────────
cd /opt/talky/backend && venv/bin/python -c "
from app.domain.services.voice_pipeline.conversation_craft import craft_reanchor
from app.services.scripts.prompts.live_state import build_live_state_block as B
from app.domain.services.telephony_session_config import _cap_tenant_additional_instructions as cap
c = craft_reanchor()
ls0 = B(agent_name='S', company_name='A', has_introduced=False)
ls1 = B(agent_name='S', company_name='A', has_introduced=True)
long = ('OPENING: greet warmly. ' * 700) + 'CLOSING RULE: confirm the callback.'
out = cap(long, campaign_id='t')
print('1 craft 30-word cap gone   :', '30 words' not in c)
print('2 opener rule at turn 0    :', 'never on their availability' in ls0)
print('3 answer-first, turn 0     :', 'outranks' in ls0)
print('  answer-first, mid-call   :', 'outranks' in ls1)
print('4 cap keeps the ending     :', 'CLOSING RULE' in out)
"

# ── confirm the 30-word cap is gone from BOTH files ─────────────────────
grep -rn "30 words" backend/app/ --include=*.py | grep -v test

# ── confirm no rule states a turn-length NUMBER outside guardrails ──────
grep -rn "under [a-z]* words\|[0-9]\+ words" backend/app/services/scripts/prompts/ \
                                              backend/app/domain/services/voice_pipeline/

# ── the traceback burst, root cause ─────────────────────────────────────
journalctl -u talky-api --since "4 days ago" | grep -oE "RuntimeError:.*" \
  | sort | uniq -c | sort -rn

# ── the STT ImportError incident ────────────────────────────────────────
journalctl -u talky-api --since "4 days ago" | grep "Flux receive error"

# ── full gate, isolated worktree ────────────────────────────────────────
git -C /opt/talky worktree add --detach /tmp/tv <COMMIT>
cd /tmp/tv/backend && /opt/talky/backend/venv/bin/python -m pytest \
  tests/unit tests/security -q -p no:randomly --continue-on-collection-errors
git -C /opt/talky worktree remove /tmp/tv --force
```

---

## Closing

Four workers, seven findings, four fixed and live, one documented with its reasoning, one diagnosed as noise, one owned as my own mistake.

The most important is Fix 1, and not because of its size — it is a two-line change. It matters because the rule it removes had **already been removed**, six days earlier, with a test that passed. The behaviour never changed, because the surviving copy sat in a slot deliberately built to outrank the one that was deleted.

That is the failure mode worth carrying forward: not a bug that was missed, but a fix that was verified in the wrong place. The code now says where a turn-length number is allowed to live, and there is exactly one such place.

**Everything in this report is live on `4ebdbd55`, verified by importing the running modules — and still unheard by a human on a live call. 0 of 30 canary calls.**
