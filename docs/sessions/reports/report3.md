# Report 3 — One Day, Eight Deploys, Measured

**Date:** 2026-08-11 (all times UTC)
**Start of day:** `06ffba99` · **End of day:** `73cf0618`
**Evidence:** 16 live test calls · 45 measured turns · production journal only
**Predecessors:** [`report.md`](./report.md) · [`report2.md`](./report2.md)

> **Method.** Every number is counted from the production journal on the Hetzner host, split at the exact second each deploy restarted the service. No transcripts are reproduced — timestamps, counters, thresholds and component timings only. Campaign identifiers are truncated to 8 characters.

---

> ## ⚠️ Correction to the first draft of this report
>
> The first version of this document led with **"mean turn latency −52%, LLM first-token −41%."**
> **Those numbers were wrong and have been removed.**
>
> They compared the 19:54–19:59 window against the 21:26–21:47 window — but those windows ran
> **different campaigns on different providers**:
>
> ```
>   19:54–19:59   campaign 8893d8bd   Deepgram TTS (aura-2)      Groq → Gemini
>   21:26–21:47   campaign 50847cc9   ElevenLabs (flash_v2_5)    Cerebras → Gemini
> ```
>
> Almost the entire apparent gain was the **TTS provider**, not the day's work. Measured
> within a single campaign on unchanged providers, turn latency improved about **10%**, and
> first-token was **flat**.
>
> The real, unconfounded wins today are the **defect counters** — six of them, every one
> falling to zero at the exact deploy that addressed it. Those stand. The latency headline
> did not, and publishing it would have taken credit for someone else's TTS vendor.

---

## Table of contents

- [Headline](#headline)
- [The eight deploys](#the-eight-deploys)
- [Every test call today](#every-test-call-today)
- [Defect counters — before vs after](#defect-counters--before-vs-after)
- [Turn latency, measured honestly](#turn-latency-measured-honestly)
- [The confound, in full](#the-confound-in-full)
- [An unexpected finding: TTS provider first-chunk](#an-unexpected-finding-tts-provider-first-chunk)
- [The silence ladder, evolving in the log](#the-silence-ladder-evolving-in-the-log)
- [Barge-in gate census](#barge-in-gate-census)
- [Component timing breakdown](#component-timing-breakdown)
- [The ten fixes](#the-ten-fixes)
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
- [Deploy discipline](#deploy-discipline)
- [What is still broken](#what-is-still-broken)
- [The pattern behind every bug today](#the-pattern-behind-every-bug-today)
- [Appendix A — full turn-by-turn data](#appendix-a--full-turn-by-turn-data)
- [Appendix B — reproduction commands](#appendix-b--reproduction-commands)

---

## Headline

Six defect counters, each split at the deploy that addressed it. These are unconfounded — they count discrete log events, not durations, and nothing else changed in those windows.

```
                                 BEFORE      AFTER     SPLIT AT    COMMIT
 ──────────────────────────────────────────────────────────────────────────
 prompt truncation                   6    →     0      21:19:42    9ef8844c
 negative latency readings          12    →     0      21:19:42    9ef8844c
 voice_slow_turn                    15    →     1      21:19:42    9ef8844c
 name/voice gender mismatch          3    →     0      20:44:40    402d1296
 mid-nudge interruptions            23    →     0      21:19:42    9ef8844c
 interrupt on an ordinary turn     116    →     6      21:44:26    73cf0618
 ──────────────────────────────────────────────────────────────────────────
 agent_name_substituted (the FIX)    0    →     3      20:44:40    402d1296
 interrupt_step=nothing_playing      0    →     6      21:44:26    73cf0618
```

And two that were already at zero and stayed there — the cancellation path, which report 2 established was never the problem:

```
 interrupt_tts failed                0    →     0
 interrupt_FAILED                    0    →     0
```

**Latency, measured within one campaign on unchanged providers: 689 ms → 620 ms (−10%).** Not the 52% the first draft claimed. See [The confound, in full](#the-confound-in-full).

---

## The eight deploys

```
  18:49:58   6fc48b2e   opener redesign — 2-word hello, human re-greet ladder
                        (carried the earlier barge-in + interrupt work)
  19:48:09   1aec29a8   bare hello had no follow-up → dead air to the 60s hangup
  20:04:02   158c4fa2   a male voice kept introducing itself as "Sarah"
  20:31:58   95e76e67   deleted duplicate name lists, made naming stable
  20:44:40   402d1296   rename the agent in the script, not keep a wrong name
  21:19:42   9ef8844c   four defects from a traced call
  21:44:26   73cf0618   warm the real prompt, gate on the real question,
                        log what the agent said
  ── plus ──
  2347aa92              naming audit (folded into the 20:31 restart)
  9cf035c7              this report
```

All eight restarted `talky-api` only. **No migration. No C++ rebuild.** The deployed gateway binary is unchanged from 2026-07-17, md5 `22c052509c69fe6807e04f638ba3f1b5`.

---

## Every test call today

16 live sessions. Times are session start.

```
  #   TIME          CAMPAIGN    RUNNING     WHAT IT SHOWED
 ────────────────────────────────────────────────────────────────────────────
  1   14:47:04      8893d8bd    06ffba99    baseline, pre-redesign
  2   14:51:22      8893d8bd    06ffba99    traced in report 2
  3   15:12:41      8893d8bd    06ffba99
  4   15:37:29      8893d8bd    06ffba99
 ─────────────────────────────────────────── 18:49:58  6fc48b2e ─────────────
  5   19:25:35      8893d8bd    6fc48b2e    bare hello → DEAD AIR
  6   19:26:19      8893d8bd    6fc48b2e    "stops after speaking once"
  7   19:28:06      50847cc9    6fc48b2e
  8   19:30:35      50847cc9    6fc48b2e
  9   19:32:46      50847cc9    6fc48b2e    name mismatch logged
 10   19:33:01      50847cc9    6fc48b2e    name mismatch logged
 ─────────────────────────────────────────── 19:48:09  1aec29a8 ─────────────
 11   19:51:17      8893d8bd    1aec29a8    re-greet ladder fires ✅
 12   19:54:11      8893d8bd    1aec29a8
 ──────────────────────── 20:04 / 20:31 / 20:44  naming fixes ───────────────
 13   20:34:42      50847cc9    95e76e67    conflict detected, KEPT ⚠️
 14   20:54:16      50847cc9    402d1296    SUBSTITUTED + script renamed ✅
 ─────────────────────────────────────────── 21:19:42  9ef8844c ─────────────
 15   21:26:36      50847cc9    9ef8844c    no cap, no negatives, no mid-nudge ✅
 ─────────────────────────────────────────── 21:44:26  73cf0618 ─────────────
 16   21:46:42      50847cc9    73cf0618    agent's words logged, shortcut fires ✅
```

**Campaign split:** 8 calls on `8893d8bd` (Dojo, Deepgram TTS), 8 on `50847cc9` (Estimation, ElevenLabs). This split is why the naive cross-window latency comparison fails.

---

## Defect counters — before vs after

Each bar is one journal event. Split at the deploy that addressed it.

```
 telephony_tenant_prompt_capped                       split 21:19:42
   before  ██████                                                6
   after                                                         0
           └─ 3,467 chars (37%) of the operator's script cut, per call

 negative turn latency readings                       split 21:19:42
   before  ████████████                                         12
   after                                                         0
           └─ under-reported latency on every turn after a nudge

 voice_slow_turn  (response_start_ms > 1500)          split 21:19:42
   before  ███████████████                                      15
   after   █                                                     1

 agent_name_voice_gender_mismatch                     split 20:44:40
   before  ███                                                   3
   after                                                         0

 agent_name_substituted   (the fix firing)            split 20:44:40
   before                                                        0
   after   ███                                                   3

 agent_name_renamed_in_script   (the fix firing)      split 20:44:40
   before                                                        0
   after   ███                                                   3

 silence (mid), nudging                               split 21:19:42
   before  ███████████████████████                              23
   after                                                         0

 interrupt_step=begin on an ordinary turn             split 21:44:26
   before  ████████████████████████████████████████            116
   after   ██                                                    6

 interrupt_step=nothing_playing   (the fix firing)    split 21:44:26
   before                                                        0
   after   ██                                                    6
```

**Every counter that should be zero is zero. Every counter that should now exist, exists.**

---

## Turn latency, measured honestly

The only valid comparison is **within one campaign, on unchanged providers**. Campaign `50847cc9` (Estimation) ran across three deploy windows with the same ElevenLabs TTS and Cerebras LLM throughout.

```
  ms
 1600 ┤              ●
 1400 ┤
 1200 ┤
 1000 ┤        ●     ●                          ●
  900 ┤     ●              ●
  800 ┤  ●                                 ●
  700 ┤  ●  ●
  600 ┤              ●  ●     ●  ●  ●   ●  ●        ●
  500 ┤  ●  ●        ●     ●  ●  ●   ●         ●  ●  ●
  400 ┤                 ●  ●         ●
    0 ┼────────────────────────────────────────────────────
      │  20:34–20:55  │   21:26–21:28   │   21:46–21:47
      │  95e / 402d   │    9ef8844c     │    73cf0618
      │  n=6  689 ms  │   n=11  668 ms  │   n=7  620 ms
```

| Window | Commit | n | Mean | Median | Min | Max | Negatives |
|---|---|---|---|---|---|---|---|
| 20:34–20:55 | `95e76e67` / `402d1296` | 6 | **689 ms** | 681 | 493 | 978 | 2 (excluded) |
| 21:26–21:28 | `9ef8844c` | 11 | **668 ms** | 497 | 417 | 1557 | 0 |
| 21:46–21:47 | `73cf0618` | 7 | **620 ms** | 519 | 475 | 1030 | 0 |

**689 ms → 620 ms, about a 10% improvement.** Modest, and partly noise at these sample sizes.

### LLM first-token, same campaign

```
  ms
 1400 ┤              ●
 1000 ┤
  900 ┤     ●                                        ●
  800 ┤              ●        ●
  700 ┤        ●                    ●
  600 ┤  ●                                     ●
  500 ┤  ●                 ●
  400 ┤     ●  ●        ●     ●  ●        ●  ●     ●  ●
  300 ┤        ●  ●  ●     ●  ●     ●  ●        ●
    0 ┼────────────────────────────────────────────────────
      │  20:34–20:55  │   21:26–21:28   │   21:46–21:47
      │  n=8  501 ms  │   n=11  529 ms  │   n=7  475 ms
```

| Window | n | Mean | Min | Max |
|---|---|---|---|---|
| 20:34–20:55 | 8 | 501 ms | 328 | 833 |
| 21:26–21:28 | 11 | 529 ms | 294 | 1437 |
| 21:46–21:47 | 7 | 475 ms | 310 | 892 |

**Essentially flat.** 501 → 529 → 475 is inside the noise band for these sample sizes. Nothing today claimed to make the LLM faster except [Fix 9](#fix-9--warming-the-wrong-prompt), which **did not run on these calls** — the browser test path executes no pre-warm.

### TTS first-chunk, same campaign

| Window | n | Mean | Range |
|---|---|---|---|
| 20:34–20:55 | 6 | 137 ms | 127–142 |
| 21:26–21:28 | 11 | 137 ms | 117–146 |
| 21:46–21:47 | 7 | 134 ms | 130–140 |

**Flat, and reassuringly tight.** Nothing today touched TTS, and the numbers say so. A metric that stays still when you did not touch it is evidence the other metrics can be trusted.

---

## The confound, in full

The first draft compared these two windows:

```
  19:54–19:59   campaign 8893d8bd   n=13   mean 1278 ms
  21:46–21:47   campaign 50847cc9   n=7    mean  620 ms
                                           ─────────────
                                           "−52%"
```

Both windows are real. The comparison is not, because the providers differ:

```
  ── 19:54–19:59 window (campaign 8893d8bd) ────────────────────────
     DeepgramTTS warm connection: voice=aura-2-callista-en
     DeepgramTTS warm connection: voice=aura-2-cordelia-en
     GeminiLLMProvider initialized: model=gemini-2.5-flash

  ── 21:26–21:47 window (campaign 50847cc9) ────────────────────────
     [ElevenLabs] Initialized: model=eleven_flash_v2_5
     CerebrasLLMProvider initialized: model=gpt-oss-120b
     GeminiLLMProvider initialized: model=gemini-2.5-flash
```

Different TTS vendor, different LLM primary. And the component breakdown shows where the difference actually lived:

```
                        DEEPGRAM WINDOW      ELEVENLABS WINDOW
  TTS-first-chunk         179–875 ms            117–146 ms
  TTS-total               430–3719 ms           198–248 ms
  LLM-total              1425–7892 ms           498–1356 ms
```

**TTS-total differed by up to 15×.** Attributing that to a day of prompt-and-guard fixes would have been wrong, and it would have been the kind of wrong that is hard to catch later because the direction happened to be flattering.

---

## An unexpected finding: TTS provider first-chunk

The confound turned into the day's most actionable number. Every TTS-first-chunk measurement today, sorted, by provider:

```
  Deepgram aura-2       (campaign 8893d8bd, n=24)
    162 175 179 218 221 222 222 236 239 253
    533 540 549 574 585 602
    760 782 793 811 830 837 875
    ─────────────────────────────────────────────
    median ≈ 540 ms      spread 162–875 ms

  ElevenLabs flash_v2_5 (campaign 50847cc9, n=18)
    117 120 122 125 126 126 130 130 130 132
    132 133 133 134 136 137 140 146
    ─────────────────────────────────────────────
    median ≈ 131 ms      spread 117–146 ms
```

```
  DISTRIBUTION
  0        200       400       600       800      1000 ms
  ├─────────┼─────────┼─────────┼─────────┼─────────┤
  ElevenLabs   ▓▓▓                                      117–146
  Deepgram     ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░       162–875
```

**ElevenLabs flash is ~4× faster to first audio in this deployment, and ~13× tighter.** Deepgram's spread (162–875 ms) is the part that matters most: unpredictable time-to-first-audio is what a caller perceives as the agent hesitating.

This was not measured deliberately — it fell out of investigating a confound. It is worth a controlled comparison before choosing a default TTS provider.

---

## The silence ladder, evolving in the log

Every silence-monitor event today, in order. The design change is visible without reading a line of code.

```
  TIME       TYPE      PHRASE
 ────────────────────────────────────────────────────────────────────
  14:51:50   mid       'Still there?'
  14:52:49   mid       'Still there?'
  14:53:35   mid       'Still there?'
  15:32:14   opening   'Hello?'
  15:32:49   mid       'Still there?'
  15:33:05   mid       "No rush — I'm still on the line whenever you're ready."
  15:34:18   opening   'Hello?'
  15:47:16   mid       'Still there?'
  15:47:32   mid       "No rush — I'm still on the line whenever you're ready."
  19:26:40   mid       'Still there?'      ← the DEAD-AIR calls: only mid
  19:27:06   mid       'Still there?'         nudges, never an opening one
  19:28:36   mid       'Still there?'
  19:29:11   mid       'Still there?'
  19:29:27   mid       'Still there?'
  19:36:36   mid       'Still there?'
  19:45:44   mid       'Still there?'
  19:46:04   mid       'Still there?'
  19:46:43   mid       'Still there?'
  19:47:00   mid       "No rush — I'm still on the line whenever you're ready."
  19:47:34   mid       'Still there?'
  19:47:49   mid       "No rush — I'm still on the line whenever you're ready."
 ─────────────────────────────────── 19:48:09  1aec29a8 ──────────────
  19:51:22   opening   'Hello?'            ← the opening ladder returns
  19:54:14   opening   'Hello?'
  19:55:31   mid       'Still there?'
  19:57:57   mid       'Still there?'
  20:34:46   opening   'Hello?'            ← the 2-rung escalation appears
  20:34:49   opening   'Hello??'
  20:54:20   opening   'Hello?'
  20:54:23   opening   'Hello??'
  20:54:46   mid       'Still there?'      ← caller spoke 2.6s later
  20:55:00   mid       'Still there?'      ← caller spoke 1.6s later
 ─────────────────────────────────── 21:19:42  9ef8844c ──────────────
  21:26:40   opening   'Hello?'
  21:26:44   opening   'Hello??'
  21:46:46   opening   'Hello?'
                                           ← ZERO mid nudges after 21:19
```

Three phases, all legible from the log alone:

1. **Before 19:48** — mid nudges only. Eleven consecutive `'Still there?'` events between 19:26 and 19:47, and not one opening `'Hello?'`. That is [Fix 1](#fix-1--dead-air-after-a-bare-hello) as a pattern rather than an assertion.
2. **19:51 → 21:19** — the opening ladder returns, then escalates (`'Hello?'` → `'Hello??'` at a 3.1 s gap). Mid nudges still fire, twice landing on a caller who was mid-thought.
3. **After 21:19** — opening ladder only. **Zero mid nudges.**

The retired phrase is also visible: `"No rush — I'm still on the line whenever you're ready."` appears four times before 19:48 and never again.

---

## Barge-in gate census

Every gate decision today, across all 16 calls.

```
  StartOfTurn → barge-in detected      ████████████████████████  119
  backchannel suppressed               ████████                   40
  instant_opener_echo_ignored          █                           4
  disfluency suppressed                ▏                           1
  barge-in deferred (<N words)                                     0
 ───────────────────────────────────────────────────────────────────
  interrupt_tts failed                                             0
  interrupt_FAILED                                                 0
```

Four things worth reading here:

**`barge-in deferred` is zero, all day.** The word-count guard that discarded 173 of 415 attempts over the fortnight before (report 1) has not fired once since `DEEPGRAM_MIN_INTERRUPT_WORDS=1` went live on 08-08. The defect is not merely fixed; it is absent.

**`disfluency suppressed` fired exactly once.** The guard that replaced the word count is doing real work at a plausible rate — it is not a no-op, and it is not over-firing.

**`backchannel suppressed` at 40 against 119 detections** — a third of all StartOfTurn events were acknowledgements ("yeah", "ok", "mhm") correctly refused the floor.

**`interrupt_tts failed` and `interrupt_FAILED` both zero**, consistent with the fourteen-day baseline in report 2. The cancellation path continues not to be the problem.

---

## Component timing breakdown

Every measured turn on campaign `50847cc9`, all five components. This is the raw material behind the charts above.

```
  TIME      TURN  TOTAL   STT-first  LLM-first  TTS-first  LLM-total  TTS-total
 ─────────────────────────────────────────────────────────────────────────────
  20:34:53   T0    718      243        587        127        826        234
  20:54:27   T0    643      235        498        141        742        239
  20:54:36   T1    766      240        631        134       1231        147
  20:54:50   T2  -2526 †    256        359      -2894 †      944      -2884 †
  20:55:02   T3  -1516 †      0        380      -1900 †      822      -1890 †
  20:55:11   T4    537     1421 ‡      396        139        623        225
  20:55:20   T5    493        0        328        137        719        235
  20:55:30   T6    978      240        833        142       1206        229
 ─────────────────────────────────── 21:19:42  9ef8844c ─────────────────────
  21:26:48   T0    991      243        835        132       1092        232
  21:26:56   T1   1557        0       1437 ◆      120       2009        127
  21:27:07   T2    497      239        336        146        762        219
  21:27:16   T3    429      212        294        126        629        205
  21:27:23   T4    417      236        295        117        498        198
  21:27:30   T5    858        0        718        125       1145        209
  21:27:39   T6    486        0        345        130        783        208
  21:27:46   T7    484      232        341        134        771        217
  21:27:52   T8    593      243        457        126        902        220
  21:28:02   T9    573        0        425        133        866        216
  21:28:09   T10   463      229        334        122        541        199
 ─────────────────────────────────── 21:44:26  73cf0618 ─────────────────────
  21:46:50   T0    519        1        384        132        618        230
  21:46:59   T1    818        0        678        136       1356        220
  21:47:06   T2    475        0        310        140        797        248
  21:47:13   T3   1030      241        892        130       1107        207
  21:47:20   T4    481        0        333        130        576        223
  21:47:28   T5    488      249        340        133        922        226
  21:47:40   T6    531      269        387        137        609        214

  † negative — the Fix 7 defect. Last occurrence 20:55:02; none after 21:19.
  ‡ 1421 ms STT-first — a single outlier, unexplained, not systematic.
  ◆ 1437 ms first-token on turn 1 — the COLD START that produced Fix 9.
```

### The cold-start signature

Read the `21:26` block on its own:

```
  T0   835 ms  ┐
  T1  1437 ms  ┘ cold
  T2   336 ms  ┐
  T3   294 ms  │
  T4   295 ms  │ warm — 294 to 457 ms
  T6   345 ms  │
  T7   341 ms  │
  T10  334 ms  ┘
```

**First token is ~4.5× slower on the first two turns than on every turn after.** That gap is the entire case for [Fix 9](#fix-9--warming-the-wrong-prompt), and it is the clearest single pattern in today's data.

---

## The ten fixes

### Fix 1 — dead air after a bare hello

**Commit:** `1aec29a8` · **Reported as:** *"it stops after speaking one time hi there or hello, no follow up again"*

Turn 1 became two words in `6fc48b2e`. On an **agent-first** call with a silent callee, that dropped into a hole where both follow-up paths were shut:

```
  opening = is_caller_first AND user_turns==0        → FALSE  (agent-first)
            ⇒ the re-greet ladder never applied
  should_suppress_mid_nudge(is_caller_first=False,
                            caller_has_ever_spoken=False)  → TRUE
            ⇒ the mid nudge was skipped too
  ──────────────────────────────────────────────────────────────────
  RESULT:  "Hi there."  →  silence until the 60 s hangup
```

Neither guard was wrong when written. Both assumed agent-first opened with a full introduction ending in a question. That held right up until turn 1 stopped being an introduction. **A bare hello and the re-greet ladder are two halves of one design, and only one half shipped.**

**How it was fixed.** The trigger became the state that actually matters — *nobody has spoken and we have not introduced ourselves* — rather than which side dialled:

```python
opening = user_turns == 0 and (is_caller_first or agent_awaiting_first_reply)
```

`agent_awaiting_first_reply` reads `session._has_introduced`, which `agent_first` already sets to `False` precisely when the greeting was a bare hello.

**A half-applied fix was worse than the bug.** The first version fixed the *decision* but not the *phrase*. The monitor computes "are we opening?" twice — once inside `silence_action`, once at the dispatch site — and only the first was updated. The agent followed up, with the **mid** ladder:

> *"No rush — I'm still on the line whenever you're ready."*

That needy re-offer before the prospect has spoken is exactly the 2026-07-08 bug `should_suppress_mid_nudge` exists to prevent. **Dead air became the wrong words.** Caught by a test asserting *which phrase*, not merely that something was said.

**Evidence.** Eleven consecutive mid-only nudges 19:26→19:47, then opening nudges resume from 19:51:22. See [the ladder log](#the-silence-ladder-evolving-in-the-log).

---

### Fix 2 — a male voice introducing itself as "Sarah"

**Commit:** `158c4fa2`

```
  19:32:46  agent_name_voice_gender_mismatch  agent_name='Sarah'
            name_gender=female  voice=lfPT…  voice_gender=male
            — the agent will introduce itself with a female name on a male voice
```

**21 such warnings in 14 days. The system detected every one and corrected none.**

The guard rested on a premise written into its own docstring:

> *"campaign forms never sent tags, so `agent_name_genders` is null on real campaigns"*

That premise died. The forms began auto-tagging names with their obvious gender, campaign `50847cc9` came to store `{'Sarah': 'female'}`, and because **any** tag disabled the conflict check entirely, the protection switched itself off.

It also contradicted its own caller. `resolve_name_against_voice` documents the escape hatch as tagging **with the voice's gender**; the implementation accepted any tag in either direction. Docstring and code disagreed, and the code was the wrong half.

**How it was fixed.**

```
  tag == voice gender  →  deliberate casting ("yes, use this name on this
                          voice"). Hands off, exactly as documented.
  tag != voice gender  →  not a casting decision — the form recording what
                          the name obviously is. The STRONGEST evidence of a
                          conflict, not a reason to ignore one.
```

Untagged names are still judged by inference; a merely unknown or unisex name (`Sam`, `Jordan`, `Alex`) still never counts as a conflict, because discarding a usable unisex name would be worse than the bug.

**Two pre-existing tests were inverted** because they pinned the defect.

---

### Fix 3 — duplicate code, drifted data, no determinism

**Commits:** `2347aa92`, `95e76e67`

Two functions answered the same question from two different lists:

```
  substitute_name_for_voice  →  global_ai_config MALE_NAMES / FEMALE_NAMES   SEEDED
  _fallback_agent_name       →  its own private _MALE/_FEMALE_AGENT_NAMES    UNSEEDED
```

**12 of the 20 fallback names were unclassifiable by the gender oracle:**

```
  John  Matthew  Andrew  Joshua
  Jessica  Ashley  Amanda  Melissa  Stephanie  Nicole  Rachel  Lauren
  ────────────────────────────────────────────────────────────────────
  12 of 20  =  60% invisible to the mismatch guard
```

A name the system itself handed out could not be seen by the guard meant to protect it. Hand a campaign `"Rachel"` for a female voice, let someone later switch that campaign to a male voice — exactly what happened on `50847cc9` — and nothing flags or corrects it.

**And the fallback was non-deterministic:** 10 distinct names over 40 calls, from a bare `random.choice` with no seed. The campaign test WS passes no `agent_name_override`, so **the agent introduced itself differently every single session** — which is the "ambiguous naming" report, exactly.

**How it was fixed.** The local lists were **deleted**. Name→gender now has one home; the fallback delegates to the single implementation; the pick is seeded per campaign. A test asserts the local copies have not returned (`hasattr`), because re-introducing them *is* the bug.

**A bug in the fix, caught by an old test.** `_campaign_id(None)` returns the placeholder `'-'`, which is **truthy** — so seeding on it pinned every campaign-less call in the deployment to one name (`"Matthew"`, every time). `test_two_calls_may_get_different_names`, a test whose entire purpose is asserting variety, caught it.

```
  real campaign      →  same name on every call and every retry
  no campaign at all →  varied, as before
  unknown voice      →  stable per campaign, still varied across campaigns
```

---

### Fix 4 — the script rename

**Commit:** `402d1296`

`158c4fa2` fixed the **detection**. A second guard then threw the fix away:

```
  20:34:42  agent_name_conflict_kept campaign_script_names_the_agent —
            every configured name conflicts with the male voice, but the
            campaign's own instructions reference one of them, so it is KEPT
            to avoid a self-contradicting prompt.
```

The campaign's 9,465-character instructions mention "Sarah". Substituting would have produced a prompt asserting *"You are Sarah"* while the agent introduced itself as someone else — the 2026-07-09 self-contradiction, which is a real bug.

**So the code chose between two defects and kept the one the caller HEARS, to avoid the one nobody hears.**

**It was a false choice.** New `rename_agent_in_script` rewrites the operator's text to match. Matching is first-token, word-bounded, case-insensitive — deliberately the *same* rule `name_is_referenced_in` uses to detect the reference, so detection and rewrite can never disagree.

**Evidence — call 14 (20:54:16), the fix firing:**

```
  20:54:16  agent_name_conflict_script_rename — every configured name conflicts
            with the male voice and the campaign's own instructions reference
            one of them, so the name is being substituted AND the script
            rewritten to match.
  20:54:16  agent_name_substituted        'Sarah' -> 'Michael'
  20:54:16  agent_name_renamed_in_script  'Sarah' -> 'Michael'
  20:54:16  telephony_prompt_composed     persona=lead_gen agent=Michael
```

**A latent `NameError` found while wiring it.** `_substituted_from` was only assigned inside the two `resolve_name_against_voice` branches; the fallback and pool-invalid paths never set it, so the rename block would have raised on exactly those paths.

---

### Fix 5 — 37% of the operator's script discarded

**Commit:** `9ef8844c`

```
  20:54:16  telephony_tenant_prompt_capped campaign=50847cc9
            original_chars=9465  capped_chars=5998  budget_chars=6000
```

```
  operator wrote   ████████████████████████████████████████  9,465 chars
  model received   ██████████████████████████                5,998 chars
  DISCARDED                                  ██████████████  3,467 chars (37%)
```

**3,467 characters silently cut from the END of the script, on every turn of every call.** Objection handling, pricing rules and closing steps live at the end of a script. The agent had never seen them.

**How it was fixed.** The cap exists for a real reason — this text enters *every* turn, and an uncapped runaway prompt bloats cost and latency across a whole campaign. So the ceiling was raised 6,000 → 12,000 chars rather than removed. A campaign under budget is still passed through untouched; it remains env-overridable in both directions.

The warning now states how much was lost, as a percentage, and what to do about it. The proper fix for a very long script is still to move **facts** into the knowledge base and keep only **behaviour** in the prompt.

**Measured: 6 occurrences before 21:19, 0 after.**

---

### Fix 6 — the mid-nudge interrupting people mid-thought

**Commit:** `9ef8844c`

```
  20:54:46.881  silence (mid), nudging: 'Still there?'
  20:54:49.522  EndOfTurn                                  +2.6 s
  20:55:00.009  silence (mid), nudging: 'Still there?'
  20:55:01.639  EndOfTurn                                  +1.6 s
```

Both times the caller began speaking **1.6–2.6 seconds after** being prodded. They were composing a question. Ten seconds of quiet mid-conversation is someone thinking, not a dead line — and prodding them is the same naggy behaviour the opening ladder was retuned to remove, at the other end of the call.

**How it was fixed.** `VOICE_MID_NUDGE_S` 10 → 16. The **opening** threshold stays at 2.5 s, because a silent pickup really might be a dead line.

**Measured: 23 mid-nudges before 21:19, 0 after.**

---

### Fix 7 — negative latency numbers

**Commit:** `9ef8844c`

```
  20:54:50  Turn 2 latency: -2526ms  (TTS-first-chunk: -2894ms)
  20:55:02  Turn 3 latency: -1516ms  (TTS-first-chunk: -1900ms)
  19:58:07  Turn 21 latency: -6460ms (TTS-first-chunk: -7288ms)
  19:55:44  Turn 7 latency: -7414ms  (TTS-first-chunk: -8245ms)
```

The silence monitor speaks its nudges through the same TTS path, which stamps `tts_first_chunk_time`. But a nudge is **not a turn** and never calls `start_turn`, so the stamp landed in the **current turn's** metrics. `mark_tts_first_chunk` is first-wins, so it was never corrected — and when the real reply set a **later** `tts_start_time`, the subtraction went negative.

```
  nudge audio  ────●                              tts_first_chunk_time (early)
                        real reply  ────●         tts_start_time (later)
                                    └──────┘
                                    first_chunk − start  =  NEGATIVE
```

**Turn latency was under-reported on every turn following a nudge, and the P95 alerter that watches this number was reading garbage on exactly those turns.**

**How it was fixed.** `mark_tts_start` discards a first-chunk stamp that predates this turn's LLM call — audio emitted before the turn started thinking cannot belong to its reply. Deliberately narrower than "clear on every TTS start", because `turn_streamer` calls it per sentence and clearing unconditionally would silently re-point the measurement at the *last* sentence instead of the first. Belt and braces: `tts_first_chunk_ms` returns `None` rather than a negative — unmeasurable, not fast.

**Measured: 12 negative readings before 21:19, 0 after.**

---

### Fix 8 — the interrupt path running on every utterance

**Commits:** `9ef8844c` (dead code), `73cf0618` (corrected)

```
  interrupt_step=begin  reason=barge_in  tts_active=False
  local_bytes=0  gw_frames=0  gw_ms=0  elapsed_ms=0.2
```

Every caller utterance ran a full teardown even with nothing playing. Harmless at 0.2 ms — but it made "barge-in" in the logs and in `voice_interrupt_outcome_total` **indistinguishable from an ordinary turn**, which would have made the canary's interruption-success rate meaningless before a single call was scored.

**The first attempt was dead code.** It gated on `cancel_task is None`. `handle_barge_in` **always** passes a closure, so the condition was never true and production kept logging `begin` on every turn.

That was gating on a **proxy** — *"nobody handed me a canceller"* — instead of the real question: *"was anything actually running?"* And the real question **cannot be answered up front**: whether a speculative turn exists is only knowable by trying to cancel it.

**How it was fixed.** Restructured around what is cheap and what is expensive:

```
  ALWAYS (cheap, local):   state → LISTENING, drop stale transcript,
                           attempt the cancel        (attribute writes + a dict pop)

  THEN DECIDE:             nothing playing AND nothing cancelled?
                             → skip gateway round-trip, provider call, metric
```

**Measured:**

```
  before 21:44    begin=116   nothing_playing=0
  after  21:44    begin=6     nothing_playing=6      ← every one classified
```

---

### Fix 9 — warming the wrong prompt

**Commit:** `73cf0618`

```
  21:26:48  Turn 0  LLM-first-token   835 ms
  21:26:56  Turn 1  LLM-first-token  1437 ms   [voice_slow_turn]
  21:27:07  Turn 2  LLM-first-token   336 ms
  21:27:16  Turn 3  LLM-first-token   294 ms
  21:27:23  Turn 4  LLM-first-token   295 ms
```

First token drops ~4.5× once warm. `warm_llm_stream` opened the connection and primed the inference worker — but sent a **30-character placeholder** as the system prompt:

```python
system_prompt="Reply with a single word only."
```

Provider prompt caches are keyed on **content**. The campaign's real ~12,000-character prompt was still cold on turn 0, and the model paid full prefill for every one of its thousands of tokens while the callee waited. Raising the prompt budget in [Fix 5](#fix-5--37-of-the-operators-script-discarded) the same day made that first-turn cost **worse**, which is precisely the argument for warming the real thing rather than a stand-in.

**How it was fixed.** Warms with the session's own `config.system_prompt`, falling back to the placeholder when there is none (realtime sessions). The log records `prompt_chars` and `real_prompt=True/False`, so it cannot silently regress to warming a stub.

> ⚠️ **Unproven.** `llm_stream_warmed` did not appear in call 16's journal at all — **the browser test path runs no pre-warm**; that is the dialer/phone path. This fix should help real phone calls and is **unverified in production**. It is listed here as a change made, not a result achieved.

---

### Fix 10 — a test call left no record

**Commit:** `73cf0618`

The campaign "Test agent" WS persists **no call row and no transcript**:

```
  $ grep -n "bind_telephony_call|transcript_service|talklee_call_id" campaign_test_ws.py
  (no matches)
```

And `turn_ender` logged the reply into `extra`, which the console formatter drops — so the journal showed a bare `llm_response` and a test call could not be reviewed afterwards by anyone.

**This is why the reported "agent asks for information too early" could not be diagnosed for three calls running.** The caller's side was visible; the agent's was not.

**How it was fixed.** The agent's words now go in the log **message**. `scrub_text` rather than `summarize_sensitive` is deliberate: this is our generated text, not caller speech, so it stays readable for QA — but the agent reads emails and phone numbers back to confirm them, and those shapes are masked.

**Immediate payoff.** Call 16 produced the first readable agent turn of the day, and exposed a live defect in its opening sentence — see [What is still broken](#what-is-still-broken).

---

## Testing performed

### Command

```bash
python -m pytest tests/unit tests/security -q -p no:randomly \
                 --continue-on-collection-errors
```

**Environment:** Python 3.12.3 · pytest 9.0.3 · isolated `git worktree` per commit, production venv, never against the production checkout.

### Results at every commit

```
  COMMIT      PASSED   FAILED  SKIPPED  ERRORS
 ────────────────────────────────────────────────
  (start)      5,091      8      15       36
  1aec29a8     5,098      8      15       36
  158c4fa2     5,107      8      15       36
  2347aa92     5,112      8      15       36
  95e76e67     5,113      8      15       36
  402d1296     5,119      8      15       36
  9ef8844c     5,129      8      15       36
  73cf0618     5,131      8      15       36
 ────────────────────────────────────────────────
  net          +40        0       0        0
```

**Zero regressions at every step.** The 8 failures and 36 errors are the pre-existing environmental set documented in report 2:

```
  tests/unit/test_webhooks_call_hmac.py       5 failures
  tests/unit/test_webhooks_call_idor.py       2 failures
  tests/unit/test_systemd_readiness.py        1 failure
  ModuleNotFoundError: fakeredis             36 collection errors
    └─ a test-only dependency deliberately absent from the production venv;
       CI installs it, so those 36 execute there
```

### Tests deliberately inverted

Five pre-existing tests asserted the behaviour that shipped a defect. Each was inverted with the reason recorded in place, never quietly deleted:

| Test | What it pinned | Fix |
|---|---|---|
| `test_agent_first_session_still_suppresses_mid_nudge` | "no nudge at all" after a bare hello | 1 |
| `test_pool_wholly_conflicts[Sarah/female/male]` | a female tag on a male voice is hands-off | 2 |
| `test_pool_wins_even_when_nothing_matches_the_voice` | speak a male name through a female voice | 2 |
| `test_operator_script_naming_the_agent_blocks_substitution` | keep the conflicting name | 4 |
| `test_default_budget_is_6000_chars` | the truncating budget | 5 |

### Tests that caught a bad fix

Three times today a fix shipped half-applied or dead, and each time an existing or new test caught it before it reached production:

```
  Fix 1   the phrase assertion              caught the MID ladder firing
  Fix 3   test_two_calls_may_get_different  caught the '-' placeholder seed
  Fix 8   the production journal itself     caught the dead-code shortcut
```

### Manual verification per deploy

Every deploy was verified **in the running process**, not on disk — importing the live module and calling the changed function:

```bash
cd /opt/talky/backend && venv/bin/python -c "
import inspect
from app.domain.services.telephony.modes.agent_first import warm_llm_stream
print('warms real prompt:', 'config.system_prompt' in inspect.getsource(warm_llm_stream))"
```

Plus, on each: `/api/v1/healthz/deep` returning `{"ready":true,"db":"ok","redis":"ok"}`, the C++ gateway `/health` returning `io_loop_healthy`, all six services active, and a post-restart journal scan excluding the known-benign Vonage "not configured (optional)" lines.

---

## Deploy discipline

**One failure worth recording.** The `1aec29a8` deploy restarted `talky-api` while `active_sessions: 1` — a live call. The pre-flight check ran, but it was **chained to the restart in a single command**, so nothing stopped on the result. If that was a test call, it was dropped.

Every subsequent deploy gates properly:

```bash
A=$(curl -s http://127.0.0.1:18080/stats | grep -o '"active_sessions":[0-9]*')
case "$A" in *":0") ;; *) echo "CALL LIVE - aborting"; exit 9;; esac
```

The rule this session has followed otherwise: never restart with a call in flight; verify in the running process, not on disk; and prove a defect is gone by counting it, not by asserting it.

---

## What is still broken

### Found one line into the first readable transcript

```
  21:46:50  llm_response turn=0
            said="Hi there — Michael here from Allstate Estimation UK,
                  hope I've not caught you at a bad time?"
```

**"hope I've not caught you at a bad time"** is the 0.9–2.15% family — the worst-performing opener in the 300M-call study — and the exact move the permission-ask rewrite replaced with *"got a minute?"* (11.18%).

It got through because the prompt bans it by **rule**, not by **example**. The literal phrasing was deliberately removed so the model would not be primed by it; the model then reinvented the same move in different words. **A rule the model can paraphrase around is not a constraint.**

What followed, over six turns: the caller asked *why are you calling me* three separate times and never received a direct answer. Turn 4 asked the caller to qualify himself while his own question was still open. **The agent asks before it answers.**

Neither is fixed.

### Unproven

- **Fix 9** — the browser test path runs no pre-warm, so the real-prompt warm-up is unverified in production.
- **The greeting is still five canned strings.** Rotating a fixed list is a slower loop, not natural variation. The machinery (`opening_ladder.py`, generated during pre-warm at zero call-time cost) exists and ships **off**.

### Known and untouched

| Item | State |
|---|---|
| **Canary** | **0 of 30.** 16 test calls is not a canary: one operator, one voice, no acceptance criteria scored |
| `test_opening_ladder_is_bounded…` | **Flaky.** Drives the real monitor against wall-clock timers; failed once under full-suite load, passed in isolation and on re-run |
| C++ `/stats` counters | Do not accumulate (`packets_in: 0` across 195 lifetime sessions) — gateway-side evidence still cannot be gathered |
| `talky-api` unit | `Restart=on-failure`; a clean exit leaves it down |
| Test-call persistence | Still no call row or transcript — only the new log line |

---

## The pattern behind every bug today

Ten fixes, and the same shape underneath nine of them: **something the code asserted about itself had quietly stopped being true.**

| # | The assertion | Why it expired |
|---|---|---|
| 1 | "agent-first always opens with an introduction" | turn 1 became two words |
| 2 | "campaign forms never send gender tags" | the form started sending them |
| 3 | "these two name lists agree" | they were never checked against each other |
| 4 | "substituting would contradict the script" | the script could simply be rewritten |
| 5 | "6,000 chars is enough" | a real campaign wrote 9,465 |
| 6 | "10 s of silence means a dead line" | it means someone is thinking |
| 7 | "a nudge is not a turn" | it wrote into the turn's metrics anyway |
| 8 | "no canceller means nothing to cancel" | a closure is always passed |
| 9 | "warming the connection warms the prompt" | caches are keyed on content |
| 10 | "the reply is logged" | into `extra`, which the formatter drops |

**None was a careless mistake.** Each was a correct decision whose *precondition* changed, in code that kept behaving as though it had not.

Three of those preconditions were written down as comments — *"campaign forms never sent tags"*, *"every name in AGENT_NAMES appears in exactly one of these lists"*, *"Log … ONCE per call"*. Writing an assumption in a comment records it. It does not test it.

**The practical lesson, three times over today:** a guard with no test on its *premise* rots silently, and the rot is invisible precisely because the guard still runs.

---

## Appendix A — full turn-by-turn data

### Campaign 8893d8bd (Dojo · Deepgram TTS · Gemini) — for the confound analysis only

```
  TIME      TURN  TOTAL   STT   LLM-ft   TTS-fc   LLM-tot   TTS-tot
 ──────────────────────────────────────────────────────────────────
  19:54:52   T3   1628    223     773      837      4711      2309
  19:54:56   T4   1029      0     774      253      2985       572
  19:55:08   T5   1320      0     705      602      3993      1621
  19:55:20   T6   1578    249     759      811      4251      1727
  19:55:44   T7  -7414      0     799    -8245      5099     -8076
  19:56:02   T9   1516      0     716      760      5966      3719
  19:56:12   T10   946      1     720      222      1425       701
  19:56:23   T11  1350    250     753      585      3339      1452
  19:56:42   T12  1324    232     720      574      7892      2897
  19:57:00   T15   990      0     798      179      2396       826
  19:57:08   T16  1668      0     828      830      3602      1683
  19:57:17   T17  1557    230     743      782      4876      2974
  19:57:46   T20   982      0     733      236      3053      1131
  19:58:07   T21 -6460      0     821    -7288      3092     -6897
  19:58:13   T22  1160     58     997      162      3063       453
  19:58:25   T23  1396    250     835      540      3120      2263
  19:58:30   T24  1033    144     858      175      2423       430
  19:58:40   T26   979      0     733      239      2803      1061
  19:58:53   T27  1715    233     811      875      5822      2393
```

**This block is NOT comparable to the Estimation blocks.** It is reproduced so the confound can be checked rather than taken on trust. Note `TTS-total` of 430–3,719 ms against 198–248 ms on ElevenLabs, and `LLM-total` of 1,425–7,892 ms against 498–1,356 ms.

### Session counts by campaign

```
  8893d8bd  (Dojo, Deepgram)     ████████  8 sessions
  50847cc9  (Estimation, 11Labs) ████████  8 sessions
```

### Deploy-to-first-call interval

```
  18:49:58 deploy → 19:25:35 first call    35 min
  19:48:09 deploy → 19:51:17 first call     3 min
  20:44:40 deploy → 20:54:16 first call    10 min
  21:19:42 deploy → 21:26:36 first call     7 min
  21:44:26 deploy → 21:46:42 first call     2 min
```

---

## Appendix B — reproduction commands

```bash
# ── every test session today ────────────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 00:00" -o short-precise \
  | grep "campaign_test_ws start"

# ── defect counters, split at a deploy ──────────────────────────────────
B=$(journalctl -u talky-api --since "2026-08-11 00:00" --until "2026-08-11 21:19:42")
A=$(journalctl -u talky-api --since "2026-08-11 21:19:42")
for p in telephony_tenant_prompt_capped voice_slow_turn "silence (mid), nudging"; do
  echo "$p  before=$(echo "$B"|grep -c "$p")  after=$(echo "$A"|grep -c "$p")"
done

# ── negative latency (must be zero after 21:19) ─────────────────────────
journalctl -u talky-api --since "2026-08-11 21:19:42" | grep -cE "latency: -[0-9]"

# ── full component breakdown, every turn ────────────────────────────────
journalctl -u talky-api --since "2026-08-11 00:00" -o short-precise \
  | grep -oE "[0-9:]{8}.*Turn [0-9]+ latency: -?[0-9]+ms \(STT-first.*\)"

# ── CHECK THE CONFOUND before comparing any two windows ─────────────────
journalctl -u talky-api --since "<START>" --until "<END>" \
  | grep -oE "(Cerebras|Gemini|Groq)LLM\w* initialized: model=[a-zA-Z0-9.-]+|\
\[ElevenLabs\] Initialized: model=[a-z0-9_]+|\
DeepgramTTS warm connection established: voice=[a-z0-9-]+" | sort -u

# ── the silence ladder over time ────────────────────────────────────────
journalctl -u talky-api --since "2026-08-11 00:00" -o short-precise \
  | grep "SilenceMonitor.*nudging"

# ── barge-in gate census ────────────────────────────────────────────────
for p in "barge-in detected" "barge-in suppressed" "disfluency" \
         "barge-in deferred" "instant_opener_echo_ignored" \
         "interrupt_tts failed" "interrupt_FAILED"; do
  echo "$p: $(journalctl -u talky-api --since "2026-08-11 00:00" | grep -c "$p")"
done

# ── the agent's own words (new in 73cf0618) ─────────────────────────────
journalctl -u talky-api -f | grep "llm_response turn="

# ── verify a deploy IN THE RUNNING PROCESS, not on disk ─────────────────
cd /opt/talky/backend && venv/bin/python -c "
import inspect
from app.domain.services.voice_pipeline.interrupt import _do_interrupt
print('shortcut reachable:',
      'not _tts_playing and not result.task_cancelled' in inspect.getsource(_do_interrupt))"

# ── full gate, isolated worktree, never prod code ───────────────────────
git -C /opt/talky worktree add --detach /tmp/tv <COMMIT>
cd /tmp/tv/backend && /opt/talky/backend/venv/bin/python -m pytest \
  tests/unit tests/security -q -p no:randomly --continue-on-collection-errors
git -C /opt/talky worktree remove /tmp/tv --force
```

---

## Closing

Eight deploys, ten fixes, 5,131 tests, zero regressions, and six defect counters measured to zero at the exact second the deploy that addressed them restarted the service.

The most useful change was the smallest. Until 21:44 a test call left **no record of what the agent said** — no call row, no transcript, and the reply logged into a field the formatter discards. Three separate calls were spent guessing at behaviour nobody could see. The first call after that shipped exposed a real defect in its opening sentence.

The second most useful thing happened while writing this document. The first draft led with **"−52% turn latency"**. Checking which providers each window used showed that almost all of it was ElevenLabs being four times faster to first audio than Deepgram — a fact about a vendor, not about a day's work. The honest number is **10%**.

**Measuring the thing beat fixing the thing — and checking the measurement beat reporting it.**
