---
description: Improve INTENT / SENTIMENT / OUTCOME detection — reading caller intent, objections, escalation and the final call outcome from transcript + interruption signals.
argument-hint: "<goal> e.g. 'detect angry callers', 'classify escalation', 'outcome resolver marks real conversations NO_ANSWER', 'add objection detection'"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---
Intent/outcome detection turns raw transcript + call signals into *decisions*:
should the agent escalate, is the caller objecting, and what happened on the call.
This skill improves that for the goal in $ARGUMENTS — precision-first, fail-soft.

## Who owns intent/outcome (read these first)
- `backend/app/services/scripts/interruption_classifier.py` — `InterruptionType`
  (enum incl. ESCALATION), `classify_interruption(text)`, `is_false_interruption`.
  Wired in `turn_ender.py`: when the agent was barged in, the caller's utterance is
  classified and recorded; ESCALATION is logged for review/handoff. This is the
  live, per-turn intent signal.
- `backend/app/domain/services/telephony/outcome_resolver.py` —
  `resolve_call_outcome(...)`, `CallOutcome` (ANSWERED / NO_ANSWER / VOICEMAIL /
  BUSY…), `_NO_ANSWER_CAUSES`, `_looks_like_voicemail`, `_conversation_user_turns`,
  `_wall_clock_seconds`. Decides the FINAL outcome from PBX cause code + transcript
  + how many real user turns happened. **A real conversation must never resolve to
  NO_ANSWER** (that mis-reschedules the lead) — the user-turn count guards this.
- `backend/app/domain/services/voice_pipeline/transcript_heuristics.py` — shared
  greeting/repetition heuristics.
- Data: `calls.transcript_json` (per-turn roles/text) + `calls.outcome`; per-tenant
  behaviour comes from `campaigns.script_config` and `tenant_ai_configs`.
- Tests: `backend/tests/unit/test_call_outcome.py`,
  `backend/tests/unit/test_outcome_resolver.py`.

## Goal → move
| Goal | Move |
|---|---|
| **Add/refine an intent class** (objection, buying-signal, wrong-number) | Extend `InterruptionType` + `classify_interruption`; keep each class high-precision and add both a positive and a near-miss test. Log new classes before acting on them (observe first). |
| **Escalation/angry-caller detection** | ESCALATION already exists and is logged in `turn_ender`; refine its trigger phrases. Only wire it to an ACTION (handoff/tone-change) after confirming precision on real transcripts. |
| **Outcome mislabelled** (real conversation → NO_ANSWER, or missed VOICEMAIL) | In `outcome_resolver`, check `_conversation_user_turns` / `_wall_clock_seconds` thresholds and cause-code trust order (PBX BUSY/NO_ANSWER is trusted over heuristics). Adjust the guard, add a regression test capturing that call. |
| **Sentiment over a whole call** | Prefer a post-call transcript pass (batch, cheap) over adding live LLM latency; feed it from `transcript_json`, not the hot path. |

## Precision + safety rules
1. **Observe before you act.** A new intent class should first only be *recorded*
   (as ESCALATION is) so you can measure its false-positive rate on real calls
   before it changes agent behaviour or the call outcome.
2. **Never add LLM calls to the live turn for intent** unless latency budget
   allows — a slow intent check re-introduces the dead-air the pipeline fights.
   Batch/post-call classification is the default for anything non-urgent.
3. **Outcome changes are lead-scheduling changes.** A wrong outcome reschedules a
   real prospect wrong; always add a regression test for the specific call.
4. Fail-soft: a classifier hiccup returns the neutral/default class, never raises.

## Change recipe
1. `/dbq` the mislabelled or target calls: `SELECT id, outcome, transcript_json
   FROM calls ORDER BY created_at DESC LIMIT 30` — confirm the pattern is real.
2. Edit the pure classifier / resolver; add positive + near-miss + regression tests.
3. Run the unit tests; if behaviour-level, verify on the `/voice-eval` overlay.
4. Report the class/threshold moved and its measured precision. Deploy **only when asked**.
