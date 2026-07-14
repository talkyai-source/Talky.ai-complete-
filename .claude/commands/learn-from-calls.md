---
description: Human-like LEARNING LOOP — mine recent real calls, extract recurring failure patterns, and propose targeted, tested detector/prompt/config changes. Run this weekly; it is how every detector improves.
argument-hint: "[focus] e.g. 'last 7 days', 'turn-taking only', 'campaign b6a61ac6', 'voicemail misses'"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, WebSearch
---
This is the loop that makes Talky's agent get *better with experience* like a
human rep reviewing their own calls: look at what actually happened, find the
recurring mistakes, and fix the underlying detector/prompt — never guess from
intuition. Run it as a periodic pass ($ARGUMENTS scopes it; default = last 7 days,
all detectors). It writes findings + patches to propose; it does **not** deploy.

## The loop (five steps)
1. **GATHER — pull real calls (read-only).** `/dbq`:
   ```sql
   SELECT id, campaign_id, outcome, duration_seconds, transcript, transcript_json
   FROM calls
   WHERE created_at > now() - interval '7 days' AND transcript IS NOT NULL
   ORDER BY created_at DESC LIMIT 200;
   ```
   Cross with `/prodlogs` for the same window: `voice_slow_turn`,
   `Repetitive STT`, `turn_0_transcript_rejected`, `backchannel_suppressed`,
   `interruption_escalation`, `voicemail_detected`, `false_interrupt`.
2. **CLASSIFY — bucket every failure by owning detector.** For each bad call, tag
   the root cause to exactly one skill: turn-taking (cut-off/lag/dead-air),
   voicemail (false-hangup/miss), intent (wrong outcome/missed escalation),
   quality (loop/hallucination/slow), or prompt (wrong content/persona). Count them.
   *A pattern is ≥3 calls with the same root cause* — one-offs are noise.
3. **DIAGNOSE — for each pattern, find the exact line.** Read the owning file
   (see the sub-skill), pull the 3–5 concrete calls, and state the precise trigger:
   which phrase matched, which threshold flipped, which turn was dropped. Quote the
   transcript. No fix without a reproduction.
4. **PROPOSE — the smallest precision-first change + its test.** One tunable/phrase
   at a time. Write the unit test that captures the real failing call FIRST
   (`backend/tests/unit/`), then the change. Predict the trade-off (e.g. "+3
   voicemail catches, risk 0 false hangups because the phrase is unambiguous").
5. **VERIFY & REPORT.** Run the new unit tests; if behaviour-level, run `/voice-eval`
   on the isolated overlay (never a live out-of-hours call). Produce a short
   report: patterns found (with counts + example call IDs), the patch for each,
   the test proving it, and the expected effect. **Deploy only when the user asks.**

## What "human-like" means here
- **Evidence over intuition** — every change traces to ≥3 real calls, quoted.
- **Precision first** — a fix that risks hanging up / cutting off a live prospect
  is rejected unless the trigger is unambiguous (see `/detect-voicemail`'s rule).
- **One change at a time** — so the next week's pass can attribute the effect.
- **Close the loop** — a fix that doesn't reduce its pattern's count next pass is
  reverted or rethought. Track the counts across passes.
- **Prompts count too** — some failures are content, not detection: route those to
  the persona/prompt system (`campaigns.script_config`, `compose_prompt`,
  `tenant_ai_configs`) rather than a heuristic.

## Persisting what's learned
When a pass finds a durable, non-obvious lesson (a phrase that caused false
positives, a threshold that fixed cut-offs, a persona quirk), write it to the
project memory so future sessions inherit it — update the relevant memory file
(e.g. `voice-latency`, `prompt-system-safety`, `llm-menu-and-compliance-floor`)
with the absolute date. Don't record what the git history already captures.

Start by asking, from $ARGUMENTS, the window + focus, then run step 1.
