---
description: Index/router for the voice-agent DETECTION skill suite (turn-taking, voicemail, intent, quality) + the human-like learning loop and stay-current pass.
argument-hint: "[turn-taking | voicemail | intent | quality | learn | current]  — or leave blank for the map"
allowed-tools: Read, Grep, Glob
---
The **detection suite** is a set of process skills that keep Talky's live voice
agent's real-time *perception* sharp and human-like. Each sub-skill owns one
detector, names the exact files that implement it, the tunables, and a
precision-first way to change it **safely** (fail-soft, verify on an isolated
overlay, never regress a live call).

Route $ARGUMENTS to the right skill:

| Area | Skill | What it makes better |
|---|---|---|
| **Turn-taking / endpointing** | `/detect-turn-taking` | When the agent knows the caller finished vs. is pausing — barge-in, semantic_vad/server_vad, the caller-first silence monitor. The project's **#1 quality lever**. |
| **Voicemail / AMD** | `/detect-voicemail` | Spotting an answering machine at pickup so we hang up instead of talking to a recording. High-precision phrase gate. |
| **Intent / sentiment / outcome** | `/detect-intent` | Reading caller intent, objections, escalation and the final call outcome from transcript + interruption signals. |
| **Call quality / regressions** | `/detect-quality` | Catching a bad call automatically — dead air, cut-offs, loops, STT hallucination, slow turns, P95 drift. |
| **Human-like learning loop** | `/learn-from-calls` | Mine recent real calls → extract failure patterns → propose targeted, tested detector/prompt changes. Run this weekly; it is how every detector above improves. |
| **Stay current** | `/detection-current` | Re-check 2026 provider capabilities (OpenAI Realtime VAD, Deepgram Flux endpointing, model IDs) and reconcile our thresholds with what the platforms now support. |

## The five rules every detection change obeys
1. **Precision over recall on destructive actions.** Any detector that *hangs up*,
   *suppresses a turn*, or *stops the agent* must be high-precision: a false
   positive on a **live prospect** is worse than a beat of a machine/dead air.
   (This is why the voicemail phrase list excludes ambiguous wording, and why
   turn-0 has a confidence floor.)
2. **Fail-soft, always.** Every detector wraps its body so a hiccup returns the
   safe default (keep the call alive) and never raises into the pipeline. Match
   the existing `try/except → return False` / `"wait"` idiom.
3. **Pure core + thin wiring.** Put the decision in a pure, unit-testable
   function (see `silence_action`, `is_voicemail_greeting`, `classify_interruption`)
   and keep I/O in the caller. Add a unit test in `backend/tests/unit/` first.
4. **Verify on the isolated overlay, never prod.** Use `/voice-eval` (rsync of
   live code to `/tmp/talky-verify`, run detached) and `/dbq` against real
   transcripts. Never place an out-of-hours call to a real prospect to test
   (compliance).
5. **Learn from real calls, not intuition.** Before tuning a threshold, pull the
   calls that would have flipped and confirm the change helps more than it hurts
   (`/learn-from-calls`).

Read the specific sub-skill file for the owning code, current tunables, and the
change recipe. If $ARGUMENTS is blank, show this map and ask which to run.
