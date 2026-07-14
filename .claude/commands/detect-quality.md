---
description: Detect CALL-QUALITY problems and regressions automatically — dead air, cut-offs, loops, STT hallucination, slow turns, and cross-call P95 latency drift.
argument-hint: "<goal> e.g. 'find bad calls from last night', 'why did P95 spike', 'detect repeated/looping agent', 'add a dead-air metric'"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---
This skill finds calls that went badly and hardens the automatic detectors that
catch them, so quality regressions surface without a human listening to every
call. Work the goal in $ARGUMENTS.

## Who owns quality detection (read these first)
- `backend/app/domain/services/voice_pipeline/latency_alerter.py` —
  `record_turn_latency_ms(ms)`: process-wide rolling-P95 watcher with hysteresis +
  cooldown. Tunables (env): `VOICE_P95_WINDOW=120`, `VOICE_P95_WINDOW_S=300`,
  `VOICE_P95_MIN_SAMPLES=20`, `VOICE_P95_ALERT_MS=1500`, `VOICE_P95_CLEAR_MS=1100`,
  `VOICE_P95_COOLDOWN_S=120`. Fires a WARNING + gauge when P95 degrades.
- `backend/app/domain/services/voice_pipeline/turn_ender.py` — emits
  `voice_slow_turn` WARNING when `response_start_ms > 1500` (Twilio mouth-to-ear
  limit ~1400ms; Hamming 2026 P95 ~4.3s), with the full stt/llm/tts breakdown.
- `backend/app/domain/services/voice_pipeline/transcript_heuristics.py` —
  `is_repetitive_transcript` (Deepgram Flux hallucination guard, GitHub #1524).
- `turn_ender.py` guards: turn-0 floor, backchannel suppression, self-echo strip —
  each already emits a structured log (`turn_0_transcript_rejected`,
  `backchannel_suppressed`, `turn_skipped_self_echo`) that is a quality signal.
- Metrics: `backend/app/infrastructure/metrics/voice_metrics.py`
  (`record_turn_0_rejection`, `record_interruption`, `observe_turn_latency_seconds`).
- Live logs: use `/prodlogs` to grep the server journal.

## Quality signal → where to look
| Symptom | Signal to grep / query |
|---|---|
| **Slow / laggy calls** | `voice_slow_turn` logs + the P95 alerter gauge; correlate with provider (stt/llm/tts breakdown in the log) |
| **Dead air** | long gaps between `turn_end` and `llm_response`; opening-nudge fires; see `/detect-turn-taking` |
| **Agent looping / repeating** | compare consecutive ASSISTANT turns in `transcript_json`; add a repetition check if none exists |
| **STT garbage reaching the LLM** | `Repetitive STT transcript` warnings + `turn_0_transcript_rejected` rate |
| **False barge-ins** (guard too eager) | `record_interruption(..., false_interrupt=True)` rate |
| **Cut-offs** | premature-endpoint pattern → `/detect-turn-taking` |

## Recipe: find bad calls
1. `/prodlogs` grep for `voice_slow_turn|P95|Repetitive STT|turn_skipped_self_echo|
   interruption_escalation` over the window.
2. `/dbq` the suspect calls: `SELECT id, duration_seconds, outcome, transcript_json
   FROM calls WHERE created_at > now() - interval '1 day' ORDER BY duration_seconds
   DESC LIMIT 30` — short-but-ANSWERED and very-long calls are both suspicious.
3. Read the transcripts; classify the failure (which detector should have caught it).

## Recipe: add/harden a detector
1. Put the check in a **pure function** next to the existing heuristics
   (e.g. an agent-repetition detector beside `is_repetitive_transcript`), with a
   unit test covering a real bad transcript and a good one.
2. Wire it to **emit a metric/log first** (observability), and only escalate to an
   ACTION (skip turn / alert) once its false-positive rate is proven low.
3. Prefer tuning the P95 alerter envs over code when the goal is alert sensitivity.
4. Verify on the `/voice-eval` overlay. Report what you added and its precision.
   Deploy **only when asked**.

The goal is that a bad call trips a log/metric on its own — feed anything you find
here into `/learn-from-calls` so the fix is systematic, not one-off.
