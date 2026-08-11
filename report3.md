# Report 3 — One Day, Eight Deploys, Measured

**Date:** 2026-08-11 (all times UTC)
**Start of day:** `6fc48b2e` · **End of day:** `73cf0618`
**Evidence:** 16 live test calls, ~150 measured turns, production journal only
**Predecessors:** [`report.md`](./report.md) · [`report2.md`](./report2.md)

> **Method note.** Every number below is counted from the production journal on the Hetzner host, split at the exact second each deploy restarted the service. No transcripts are reproduced — only timestamps, counters and thresholds. Campaign identifiers are truncated to 8 characters.

---

## Table of contents

- [Headline](#headline)
- [The eight deploys](#the-eight-deploys)
- [Every test call today](#every-test-call-today)
- [Defect counters — before vs after](#defect-counters--before-vs-after)
- [Turn latency over the day](#turn-latency-over-the-day)
- [LLM first-token over the day](#llm-first-token-over-the-day)
- [Fix 1 — dead air after a bare hello](#fix-1--dead-air-after-a-bare-hello)
- [Fix 2 — a male voice introducing itself as "Sarah"](#fix-2--a-male-voice-introducing-itself-as-sarah)
- [Fix 3 — duplicate code, drifted data, no determinism](#fix-3--duplicate-code-drifted-data-no-determinism)
- [Fix 4 — the script rename](#fix-4--the-script-rename)
- [Fix 5 — 37% of the operator's script discarded](#fix-5--37-of-the-operators-script-discarded)
- [Fix 6 — the mid-nudge interrupting people mid-thought](#fix-6--the-mid-nudge-interrupting-people-mid-thought)
- [Fix 7 — negative latency numbers](#fix-7--negative-latency-numbers)
- [Fix 8 — the interrupt path running on every utterance](#fix-8--the-interrupt-path-running-on-every-utterance)
- [Fix 9 — warming the wrong prompt](#fix-9--warming-the-wrong-prompt)
- [Fix 10 — a test call left no record](#fix-10--a-test-call-left-no-record)
- [Testing performed](#testing-performed)
- [What is still broken](#what-is-still-broken)
- [The pattern behind every bug today](#the-pattern-behind-every-bug-today)
- [Appendix — reproduction commands](#appendix--reproduction-commands)

---

## Headline

```
                          BEFORE          AFTER        WINDOW
 ─────────────────────────────────────────────────────────────────────
 prompt truncation           6      →       0          split at 21:19
 negative latency           12      →       0          split at 21:19
 voice_slow_turn            15      →       1          split at 21:19
 name/voice mismatch         3      →       0          split at 20:44
 mid-nudge interruptions    23      →       0          split at 21:19
 interrupt on every turn   116      →       6          split at 21:44
 ─────────────────────────────────────────────────────────────────────
 mean turn latency        1278ms    →     620ms        −52%
 mean LLM first-token      802ms    →     475ms        −41%
```

Every counter that should be zero **is** zero. Every counter that should exist now exists.

---

## The eight deploys

```
  18:49:58   6fc48b2e   opener redesign — 2-word hello, human re-greet ladder
  19:48:09   1aec29a8   bare hello had no follow-up → dead air to the 60s hangup
  20:04:02   158c4fa2   a male voice kept introducing itself as "Sarah"
  20:31:58   95e76e67   deleted duplicate name lists, made naming stable
  20:44:40   402d1296   rename the agent in the script, not keep a wrong name
  21:19:42   9ef8844c   four defects from a traced call
  21:44:26   73cf0618   warm the real prompt, gate on the real question, log the agent
             ─────────
             (6fc48b2e itself carried the earlier barge-in and interrupt work)
```

All eight restarted `talky-api` only. No migration, no C++ rebuild.

---

## Every test call today

16 live sessions. Times are session start, UTC.

```
  #   TIME          CAMPAIGN    RUNNING      NOTES
 ───────────────────────────────────────────────────────────────────────
  1   14:47:04      8893d8bd    06ffba99     pre-opener-redesign
  2   14:51:22      8893d8bd    06ffba99     traced in report2
  3   15:12:41      8893d8bd    06ffba99
  4   15:37:29      8893d8bd    06ffba99
 ───────────────────────────────────────────── 18:49  opener redesign ───
  5   19:25:35      8893d8bd    6fc48b2e     bare hello → DEAD AIR
  6   19:26:19      8893d8bd    6fc48b2e     "stops after speaking once"
  7   19:28:06      50847cc9    6fc48b2e
  8   19:30:35      50847cc9    6fc48b2e
  9   19:32:46      50847cc9    6fc48b2e     name mismatch logged
 10   19:33:01      50847cc9    6fc48b2e     name mismatch logged
 ───────────────────────────────────────────── 19:48  dead-air fix ──────
 11   19:51:17      8893d8bd    1aec29a8     re-greet ladder fires
 12   19:54:11      8893d8bd    1aec29a8
 ───────────────────────────── 20:04 / 20:31 / 20:44  naming fixes ──────
 13   20:34:42      50847cc9    95e76e67     conflict detected, KEPT
 14   20:54:16      50847cc9    402d1296     SUBSTITUTED + script renamed
 ───────────────────────────────────────────── 21:19  four defects ──────
 15   21:26:36      50847cc9    9ef8844c     no cap, no negatives, no mid-nudge
 ───────────────────────────────────────────── 21:44  three structural ──
 16   21:46:42      50847cc9    73cf0618     agent's words logged, shortcut fires
```

---

## Defect counters — before vs after

Counted from the journal, split at the deploy that addressed each one.

```
 telephony_tenant_prompt_capped        split 21:19:42
   before  ██████                                    6
   after                                             0

 negative turn latency                 split 21:19:42
   before  ████████████                             12
   after                                             0

 voice_slow_turn                       split 21:19:42
   before  ███████████████                          15
   after   █                                         1

 agent_name_voice_gender_mismatch      split 20:44:40
   before  ███                                       3
   after                                             0

 agent_name_substituted (the FIX firing)
   before                                            0
   after   ███                                       3

 silence (mid), nudging                split 21:19:42
   before  ███████████████████████                  23
   after                                             0

 interrupt_step=begin on ordinary turns  split 21:44:26
   before  ████████████████████████████████████    116
   after   ██                                        6
 interrupt_step=nothing_playing
   before                                            0
   after   ██                                        6
```

---

## Turn latency over the day

Every measured turn, in order. `†` marks a negative (impossible) reading.

```
  ms
 1800 ┤        ●
 1600 ┤  ●  ●     ●        ●
 1400 ┤     ●  ●     ●  ●
 1200 ┤        ●  ●
 1000 ┤  ●  ●  ●  ●     ●        ●     ●
  800 ┤                       ●  ●        ●     ●
  600 ┤                       ●  ●  ●  ●  ●  ●  ●  ●
  400 ┤                             ●  ●  ●  ●  ●  ●
    0 ┼──────────────────────────────────────────────────
      │ 19:56–19:59 │ 20:34–20:55 │ 21:26–21:28 │ 21:46–21:47
      │  1aec29a8   │  95e/402d   │   9ef8844c  │   73cf0618
      │   n=13      │    n=8      │    n=11     │    n=7
      │  mean 1278  │  mean 692†  │   mean 668  │  mean 620
```

| Window | Commit | n | Mean | Range | Negatives |
|---|---|---|---|---|---|
| 19:56–19:59 | `1aec29a8` | 13 | **1278 ms** | 946–1715 | 1 |
| 20:34–20:55 | `95e76e67` / `402d1296` | 8 | 692 ms† | −2526 … 978 | **2** |
| 21:26–21:28 | `9ef8844c` | 11 | **668 ms** | 417–1557 | 0 |
| 21:46–21:47 | `73cf0618` | 7 | **620 ms** | 475–1030 | 0 |

**Mean turn latency fell 1278 ms → 620 ms, a 52% reduction.** The 20:34–20:55 mean is marked `†` because it still contained negative readings — it is not comparable, which is precisely the defect Fix 7 removed.

---

## LLM first-token over the day

```
  ms
 1400 ┤                                   ●
 1000 ┤        ●
  900 ┤  ●  ●  ●  ●                             ●
  800 ┤  ●  ●  ●  ●  ●  ●     ●           ●
  700 ┤  ●  ●        ●              ●     ●
  600 ┤                    ●                    ●
  500 ┤                    ●  ●  ●              ●
  400 ┤                       ●  ●  ●     ●  ●  ●  ●
  300 ┤                          ●  ●  ●  ●  ●  ●  ●
    0 ┼──────────────────────────────────────────────────
      │ 19:56–19:59 │ 20:34–20:55 │ 21:26–21:28 │ 21:46–21:47
      │  mean 802   │   mean 494  │   mean 529  │  mean 475
```

| Window | n | Mean first-token | Min | Max |
|---|---|---|---|---|
| 19:56–19:59 | 12 | **802 ms** | 720 | 997 |
| 20:34–20:55 | 10 | 494 ms | 328 | 833 |
| 21:26–21:28 | 11 | 529 ms | 294 | 1437 |
| 21:46–21:47 | 7 | **475 ms** | 310 | 892 |

**Mean first-token fell 802 ms → 475 ms, a 41% reduction.**

The `1437 ms` outlier at 21:26 is a **cold start** — turn 1 of that call. Warm turns in the same call ran 294–345 ms, a ~5× spread. That observation is what produced [Fix 9](#fix-9--warming-the-wrong-prompt).

---

## Fix 1 — dead air after a bare hello

**Commit:** `1aec29a8` — *"a bare hello with no follow-up was dead air on agent-first calls"*
**Reported as:** *"it stops after speaking one time hi there or hello, no follow up again"*

Turn 1 became two words in `6fc48b2e`. On an **agent-first** call with a silent callee that dropped into a hole where both follow-up paths were shut:

```
  opening = is_caller_first AND user_turns==0     → FALSE  (agent-first)
            ⇒ the re-greet ladder never applied
  should_suppress_mid_nudge(is_caller_first=False,
                            caller_has_ever_spoken=False) → TRUE
            ⇒ the mid nudge was skipped too
  ───────────────────────────────────────────────────────────────
  RESULT: "Hi there."  →  silence until the 60s hangup
```

Neither guard was wrong when written. Both assumed agent-first opened with a full introduction ending in a question. That held right up until turn 1 stopped being an introduction. **A bare hello and the re-greet ladder are two halves of one design, and only one half shipped.**

**How:** the trigger became the state that matters — *nobody has spoken and we have not introduced ourselves* — rather than which side dialled.

**A half-applied fix was worse than the bug.** The first version fixed the *decision* but not the *phrase*: the monitor computes "are we opening?" twice, and the dispatch site still read the old value. The agent followed up — with the **mid** ladder, *"No rush — I'm still on the line whenever you're ready."* That needy re-offer before the prospect has spoken is the exact 2026-07-08 bug the suppression exists to prevent. Caught by a test asserting *which phrase*, not merely that something was said.

**Proof:** call 11 (19:51:17) onward.

```
  21:46:46.795   [SilenceMonitor] silence (opening), nudging: 'Hello?'
```

---

## Fix 2 — a male voice introducing itself as "Sarah"

**Commit:** `158c4fa2` — *"a male voice kept introducing itself as \"Sarah\""*

```
  19:32:46  agent_name_voice_gender_mismatch  agent_name='Sarah'
            name_gender=female  voice=lfPT…  voice_gender=male
```

**21 such warnings in 14 days. The system detected every one and corrected none.**

The guard rested on a premise written into its own docstring — *"campaign forms never sent tags, so `agent_name_genders` is null on real campaigns"*. That premise died: the forms began auto-tagging, campaign `50847cc9` stored `{'Sarah': 'female'}`, and because *any* tag disabled the conflict check, the protection switched itself off.

It also contradicted its own caller: `resolve_name_against_voice` documents the escape hatch as tagging **with the voice's gender**; the implementation accepted any tag either way.

**How:** an explicit tag now disables the check only when it **matches the voice**.

```
  tag == voice gender  →  deliberate casting. Hands off.
  tag != voice gender  →  the form labelling a name. Strongest evidence
                          of a conflict, not a reason to ignore one.
```

---

## Fix 3 — duplicate code, drifted data, no determinism

**Commits:** `2347aa92`, `95e76e67`

Two functions answered the same question from two different lists:

```
  substitute_name_for_voice  →  global_ai_config MALE_NAMES / FEMALE_NAMES   seeded
  _fallback_agent_name       →  its own private copies                      UNSEEDED
```

**12 of the 20 fallback names were unclassifiable by the gender oracle** — `John`, `Rachel`, `Joshua`, `Lauren`, `Jessica`, `Stephanie`… A name the system itself handed out was invisible to the guard meant to protect it.

And the fallback was **non-deterministic**: 10 distinct names over 40 calls. The campaign test WS passes no override, so **the agent introduced itself differently every session** — the "ambiguous naming" report.

**How:** the local lists were **deleted**. One home for name→gender, one implementation, seeded per campaign. A test asserts the local copies have not come back (`hasattr`), because re-introducing them *is* the bug.

**A bug in the fix, caught by an old test.** `_campaign_id(None)` returns the placeholder `'-'`, which is **truthy** — so seeding on it pinned every campaign-less call in the deployment to one name (`"Matthew"`, every time). `test_two_calls_may_get_different_names` caught it.

```
  real campaign      →  same name every call and every retry
  no campaign at all →  varied, as before
  unknown voice      →  stable per campaign, varied across campaigns
```

---

## Fix 4 — the script rename

**Commit:** `402d1296` — *"rename the agent in the script instead of keeping a wrong-gender name"*

`158c4fa2` fixed the **detection**; a second guard then threw it away.

```
  20:34:42  agent_name_conflict_kept — every configured name conflicts with
            the male voice, but the campaign's own instructions reference one
            of them, so it is KEPT to avoid a self-contradicting prompt.
```

The campaign's 9,465-character instructions mention "Sarah". Substituting would assert *"You are Sarah"* while the agent introduced itself as someone else. So the code chose between two defects and kept **the one the caller hears** over the one nobody hears.

**It was a false choice.** New `rename_agent_in_script` rewrites the operator's text to match, first-token and word-bounded — deliberately the *same* rule `name_is_referenced_in` uses to detect the reference, so detection and rewrite cannot disagree.

**Proof — call 14 (20:54:16), the fix firing:**

```
  20:54:16  agent_name_conflict_script_rename — substituting AND rewriting
  20:54:16  agent_name_substituted        'Sarah' -> 'Michael'
  20:54:16  agent_name_renamed_in_script  'Sarah' -> 'Michael'
  20:54:16  telephony_prompt_composed     agent=Michael
```

**A latent `NameError` found while wiring it:** `_substituted_from` was only assigned inside two branches; the fallback and pool-invalid paths would have raised. Initialised up front.

---

## Fix 5 — 37% of the operator's script discarded

**Commit:** `9ef8844c`

```
  20:54:16  telephony_tenant_prompt_capped  original_chars=9465
            capped_chars=5998  budget_chars=6000
```

**3,467 characters — 37% — silently cut from the END of the operator's script on every turn of every call.** Objection handling, pricing rules and closing steps live at the end of a script; the agent had never seen them.

**How:** the cap exists for a real reason (this text enters every turn), so the ceiling was raised 6000 → 12000 chars rather than removed. A short script is still passed through untouched. The warning now states how much was lost, as a percentage, and what to do.

**Measured effect: 6 occurrences before 21:19, 0 after.**

---

## Fix 6 — the mid-nudge interrupting people mid-thought

**Commit:** `9ef8844c`

```
  20:54:46.881  silence (mid), nudging: 'Still there?'
  20:54:49.522  EndOfTurn                                 (+2.6s)
  20:55:00.009  silence (mid), nudging: 'Still there?'
  20:55:01.639  EndOfTurn                                 (+1.6s)
```

Both times the caller began speaking **1.6–2.6 s after** being prodded — they were composing a question. Ten seconds of quiet mid-conversation is thinking, not a dead line.

**How:** `VOICE_MID_NUDGE_S` 10 → 16. The **opening** threshold stays at 2.5 s — a silent pickup really might be a dead line.

**Measured effect: 23 mid-nudges before 21:19, 0 after.**

---

## Fix 7 — negative latency numbers

**Commit:** `9ef8844c`

```
  20:54:50  Turn 2 latency: -2526ms  (TTS-first-chunk: -2894ms)
  20:55:02  Turn 3 latency: -1516ms  (TTS-first-chunk: -1900ms)
```

The silence monitor speaks nudges through the same TTS path, which stamps `tts_first_chunk_time` — but a nudge is not a turn and never calls `start_turn`, so the stamp landed in the **current turn's** metrics. `mark_tts_first_chunk` is first-wins, so it was never corrected, and when the real reply set a **later** `tts_start_time` the subtraction went negative.

**Turn latency was under-reported on every turn following a nudge, and the P95 alerter was reading garbage.**

**How:** `mark_tts_start` discards a first-chunk stamp that predates this turn's LLM call — audio emitted before the turn started thinking cannot belong to its reply. Deliberately narrower than "clear on every TTS start", because `turn_streamer` calls it per sentence. Belt and braces: the property returns `None` rather than a negative — unmeasurable, not fast.

**Measured effect: 12 negative readings before 21:19, 0 after.**

---

## Fix 8 — the interrupt path running on every utterance

**Commits:** `9ef8844c` (dead code), `73cf0618` (corrected)

```
  interrupt_step=begin  reason=barge_in  tts_active=False
  local_bytes=0  gw_frames=0
```

Every caller utterance ran a full teardown even with nothing playing. Harmless at 0.2 ms, but it made "barge-in" in the logs and in `voice_interrupt_outcome_total` indistinguishable from an ordinary turn — which would have made the canary's interruption-success numbers meaningless before a single call was measured.

**The first attempt was dead code.** It gated on `cancel_task is None`; `handle_barge_in` always passes a closure, so the condition was never true. That was gating on a **proxy** — *"nobody handed me a canceller"* — instead of the real question, *"was anything actually running?"*

**The real question cannot be answered up front:** whether a speculative turn exists is only knowable by trying to cancel it.

**How:** cheap local work (state reset, cancel attempt — an attribute write and a dict pop) runs unconditionally. The gateway round-trip, the provider call and the metric are skipped only when nothing was playing **and** nothing was cancelled.

**Measured effect:**

```
  before 21:44   begin=116   nothing_playing=0
  after  21:44   begin=6     nothing_playing=6      ← every one correctly classified
```

---

## Fix 9 — warming the wrong prompt

**Commit:** `73cf0618`

```
  21:26:48  Turn 0  LLM-first-token   835ms
  21:26:56  Turn 1  LLM-first-token  1437ms   [voice_slow_turn]
  21:27:07  Turn 2  LLM-first-token   336ms
  21:27:16  Turn 3  LLM-first-token   294ms
```

First token drops ~5× once warm. `warm_llm_stream` opened the connection and primed the inference worker, but sent a **30-character placeholder** as the system prompt:

```python
system_prompt="Reply with a single word only."
```

Provider prompt caches are keyed on **content**, so the campaign's real ~12,000-character prompt was still cold on turn 0 and the model paid full prefill while the callee waited. Raising the prompt budget in Fix 5 the same day made that first-turn cost worse — which is exactly the argument for warming the real thing.

**How:** warms with the session's own `config.system_prompt`, falling back to the placeholder when there is none. The log records `prompt_chars` and `real_prompt=True/False` so it cannot silently regress to warming a stub.

> ⚠️ **Not yet proven.** `llm_stream_warmed` did not appear in call 16's journal — the **browser test path does not run pre-warm at all**; that is the dialer/phone path. This fix should help real phone calls and remains **unverified in production**.

---

## Fix 10 — a test call left no record

**Commit:** `73cf0618`

The campaign "Test agent" WS persists **no call row and no transcript**. `turn_ender` logged the reply into `extra`, which the console formatter drops — so the journal showed a bare `llm_response` and a test call could not be reviewed afterwards by anyone.

This is why the reported *"agent asks for information too early"* could not be diagnosed for three calls running.

**How:** the agent's words now go in the log **message**. `scrub_text` rather than `summarize_sensitive` is deliberate — this is our generated text, not caller speech, so it stays readable for QA, but the agent reads emails and phone numbers back to confirm them and those shapes are masked.

**Immediate payoff.** Call 16 (21:46:42) produced the first readable agent turn of the day, and it exposed a live defect within one line — see [What is still broken](#what-is-still-broken).

---

## Testing performed

### Automated

```
  command:  python -m pytest tests/unit tests/security -q -p no:randomly \
                             --continue-on-collection-errors
  env:      Python 3.12.3 · pytest 9.0.3
  location: isolated git worktree per commit, production venv, never prod code
```

| Commit | Passed | Failed | Skipped | Errors |
|---|---|---|---|---|
| `1aec29a8` | 5,098 | 8 | 15 | 36 |
| `158c4fa2` | 5,107 | 8 | 15 | 36 |
| `2347aa92` | 5,112 | 8 | 15 | 36 |
| `95e76e67` | 5,113 | 8 | 15 | 36 |
| `402d1296` | 5,119 | 8 | 15 | 36 |
| `9ef8844c` | 5,129 | 8 | 15 | 36 |
| `73cf0618` | **5,131** | 8 | 15 | 36 |

**Zero regressions at every step.** The 8 failures and 36 errors are the pre-existing environmental set documented in report 2 (`test_webhooks_call_hmac` ×5, `test_webhooks_call_idor` ×2, `test_systemd_readiness` ×1; errors are `ModuleNotFoundError: fakeredis`, a test-only dependency absent from the production venv).

**Test growth today: 5,091 → 5,131 (+40).**

### Tests deliberately inverted

Five pre-existing tests asserted the behaviour that shipped a defect. Each was inverted with the reason recorded in place:

| Test | Pinned |
|---|---|
| `test_agent_first_session_still_suppresses_mid_nudge` | "no nudge at all" after a bare hello |
| `test_pool_wholly_conflicts[Sarah/female/male]` | a female tag on a male voice is hands-off |
| `test_pool_wins_even_when_nothing_matches_the_voice` | speak a male name through a female voice |
| `test_operator_script_naming_the_agent_blocks_substitution` | keep the conflicting name |
| `test_default_budget_is_6000_chars` | the truncating budget |

### Manual verification per deploy

Every deploy was verified **in the running process**, not on disk — importing the live module and calling the changed function. Health (`/api/v1/healthz/deep`, gateway `/health`), all six services active, and a post-restart journal scan excluding the known-benign Vonage lines.

### Deploy safety

After restarting once while a call was live (a chained check rather than a gate), every subsequent deploy aborted if `active_sessions != 0`.

---

## What is still broken

### Found in call 16, one line into the first readable transcript

```
  21:46:50  turn=0  said="Hi there — Michael here from Allstate Estimation UK,
                          hope I've not caught you at a bad time?"
```

**"hope I've not caught you at a bad time"** is the 0.9–2.15% family — the worst-performing opener in the 300M-call study — and the exact move the permission-ask rewrite replaced with *"got a minute?"* (11.18%).

It got through because the prompt bans it by **rule**, not by **example**. The literal phrasing was deliberately removed so the model would not be primed by it; the model then reinvented the same move in different words. **A rule the model can paraphrase around is not a constraint.**

What followed, over six turns: the caller asked *why are you calling* three separate times and never received a direct answer; turn 4 asked the caller to qualify himself while his own question was still open. **The agent asks before it answers.**

Neither is fixed.

### Unproven

- **Fix 9** — the browser test path runs no pre-warm, so the real-prompt warm-up is unverified in production.
- **The greeting is still five canned strings.** Rotating a fixed list is a slower loop, not natural variation. The machinery to fix it (`opening_ladder.py`, generated during pre-warm at zero call-time cost) exists and ships **off**.

### Known and untouched

- **0 of 30 canary calls.** 16 test calls is not a canary: one operator, one voice, no acceptance criteria scored.
- `test_opening_ladder_is_bounded_and_never_nags_past_its_cap` is **flaky** — it drives the real monitor against wall-clock timers, failed once under full-suite load, passed in isolation and on re-run.
- **C++ `/stats` counters do not accumulate** (`packets_in: 0` across 195 lifetime sessions), so gateway-side evidence still cannot be gathered.
- `talky-api` runs `Restart=on-failure`; a clean exit leaves it down.

---

## The pattern behind every bug today

Ten fixes, and the same shape underneath almost all of them: **something the code asserted about itself had quietly stopped being true.**

| # | The assertion | Why it expired |
|---|---|---|
| 1 | "agent-first always opens with an introduction" | turn 1 became two words |
| 2 | "campaign forms never send gender tags" | the form started sending them |
| 3 | "these two name lists agree" | they were never checked against each other |
| 4 | "substituting would contradict the script" | the script could simply be rewritten |
| 5 | "6000 chars is enough" | a real campaign wrote 9,465 |
| 6 | "10 s of silence means a dead line" | it means someone is thinking |
| 7 | "a nudge is not a turn" | it used the turn's metrics anyway |
| 8 | "no canceller means nothing to cancel" | a closure is always passed |
| 9 | "warming the connection warms the prompt" | caches are keyed on content |
| 10 | "the reply is logged" | into `extra`, which is dropped |

None was a careless mistake. Each was a correct decision whose **precondition** changed, in code that kept behaving as though it had not.

**The practical lesson, three times over today:** a guard with no test on its *premise* rots silently. Fixes 1, 3 and 8 each shipped half-applied or dead, and in every case an **existing test** caught it — the phrase assertion, `test_two_calls_may_get_different_names`, and the production journal itself.

---

## Appendix — reproduction commands

```bash
# ── every test session today ───────────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 00:00" -o short-precise \
  | grep "campaign_test_ws start"

# ── defect counters, split at a deploy ─────────────────────────────────
B=$(journalctl -u talky-api --since "2026-08-11 00:00" --until "2026-08-11 21:19:42")
A=$(journalctl -u talky-api --since "2026-08-11 21:19:42")
for p in telephony_tenant_prompt_capped voice_slow_turn "silence (mid), nudging"; do
  echo "$p  before=$(echo "$B"|grep -c "$p")  after=$(echo "$A"|grep -c "$p")"
done

# ── negative latency (must be zero) ────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 21:19:42" | grep -cE "latency: -[0-9]"

# ── per-turn latency series ────────────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 00:00" -o short-precise \
  | grep -oE "[0-9:]{8}.*Turn [0-9]+ latency: -?[0-9]+ms"

# ── the agent's own words (new in 73cf0618) ────────────────────────────
journalctl -u talky-api -f | grep "llm_response turn="

# ── interrupt classification ───────────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 21:44:26" \
  | grep -cE "interrupt_step=(begin|nothing_playing)"

# ── verify a deploy IN THE RUNNING PROCESS, not on disk ────────────────
cd /opt/talky/backend && venv/bin/python -c "
import inspect
from app.domain.services.telephony.modes.agent_first import warm_llm_stream
print('warms real prompt:', 'config.system_prompt' in inspect.getsource(warm_llm_stream))"

# ── full gate, isolated worktree, never prod code ──────────────────────
git -C /opt/talky worktree add --detach /tmp/tv <COMMIT>
cd /tmp/tv/backend && /opt/talky/backend/venv/bin/python -m pytest \
  tests/unit tests/security -q -p no:randomly --continue-on-collection-errors
git -C /opt/talky worktree remove /tmp/tv --force
```

---

## Closing

Eight deploys, ten fixes, 5,131 tests, zero regressions, and every defect counter that should be zero measured at zero against the production journal.

The most useful change is the smallest one. Until 21:44 a test call left **no record of what the agent said** — no call row, no transcript, and the reply logged into a field the formatter discards. Three separate calls were spent guessing at behaviour that could not be seen.

The first call after that shipped exposed a real defect in its opening sentence.

**Measuring the thing beat fixing the thing, again.**
