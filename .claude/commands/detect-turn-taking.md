---
description: Diagnose or tune the voice agent's TURN-TAKING / endpointing — the caller-first silence monitor, barge-in, and OpenAI Realtime semantic_vad/server_vad. The #1 quality lever.
argument-hint: "<symptom or goal> e.g. 'agent cuts callers off', 'laggy replies', 'dead air on pickup', 'tune server_vad for noisy line'"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---
Turn-taking is Talky's **#1 conversational-quality lever**: the agent feeling
human depends on knowing when the caller has *finished* vs. is *pausing*, and on
stopping instantly when barged in. This skill diagnoses a symptom in $ARGUMENTS
and makes a **precision-first, fail-soft** change.

## Who owns turn-taking (read these first)
- **Cascaded pipeline (Deepgram Flux STT):**
  - `backend/app/domain/services/voice_pipeline/audio_ingest.py` — the pure
    `silence_action(...)` decision (grace / opening / mid / nudge-gap / 60s
    hangup) + the caller-first silence monitor loop. **Two clocks:** `_last_caller_at`
    drives the 60s hangup and is *not* reset by nudges; `_silence_since` drives
    nudges. Tunables: `_OPENING_HELLO_S=10`, `_MID_NUDGE_S=10`,
    `_SILENCE_HANGUP_S=60`, `_TTS_GRACE_S=3`, `_NUDGE_MIN_GAP_S=12`.
  - `backend/app/domain/services/voice_pipeline/turn_ender.py` — end-of-turn
    guards: turn-0 confidence floor (`_should_reject_turn_0`), repetitive-STT
    skip, backchannel suppression, self-echo strip, barge-in epoch/event handling.
  - `backend/app/services/scripts/interruption_filter.py` — `is_backchannel`
    ("hmm/yeah/uh-huh" are NOT turns; never suppress the caller's FIRST utterance).
  - `backend/app/services/scripts/interruption_classifier.py` —
    `classify_interruption` / `is_false_interruption` (a *false* interruption =
    barge-in guard fired too eagerly; that's the metric to watch).
- **Realtime pipeline (OpenAI gpt-realtime):**
  - `backend/app/infrastructure/realtime/openai_realtime.py` — `_DEFAULT_TURN_DETECTION
    = {"type": "semantic_vad", "eagerness": "medium"}`. Accepts a bare eagerness
    string, a `semantic_vad` dict, OR a full `server_vad` object
    `{"type":"server_vad","threshold":0.6,"prefix_padding_ms":300,"silence_duration_ms":700}`
    for noisy telephony. `turn_detection` flows from per-session settings.
- Tests: `backend/tests/unit/test_silence_action.py`,
  `backend/tests/unit/test_realtime_quality_pass.py`,
  `backend/tests/unit/test_telephony_session_config.py`.

## Symptom → lever
| Symptom | Likely cause | Lever |
|---|---|---|
| **Agent cuts the caller off** (premature endpoint) | eagerness too high / `silence_duration_ms` too short / barge-in too eager | realtime: eagerness `medium→low` or server_vad `silence_duration_ms 700→900`; cascaded: check `is_false_interruption` rate in logs, loosen barge-in |
| **Laggy replies** (agent waits too long after caller stops) | `silence_duration_ms` too long / semantic_vad too conservative | realtime: eagerness `low→medium/high` or `silence_duration_ms 900→600`; verify against latency P95 |
| **Dead air right after pickup** | opening nudge too slow, or first utterance suppressed as backchannel | cascaded: `_OPENING_HELLO_S`; confirm turn-0 backchannel exception is intact in `turn_ender` |
| **Agent talks over the caller / won't stop** | barge-in event/epoch not clearing | cascaded: trace `_barge_in_events` + turn epoch in `turn_ender`; realtime: confirm interruption is wired |
| **Nudges too frequent / naggy** | `_NUDGE_MIN_GAP_S` too small | raise the gap; never let a nudge reset `_last_caller_at` |
| **Noisy line trips VAD on background noise** | semantic_vad can't reject noise | switch that session to `server_vad` with higher `threshold` (0.6→0.7) |

## Change recipe (safe)
1. **Reproduce from real calls first** — `/dbq` the recent bad calls
   (`SELECT id, transcript_json, duration_seconds FROM calls ORDER BY created_at DESC LIMIT 20`)
   and confirm the symptom is real and which pipeline (cascaded vs realtime) served it.
2. **Change the pure core or the config default, not the wiring.** For cascaded
   timing, edit the `_..._S` constants / `silence_action`; add/adjust a case in
   `test_silence_action.py` and run it BEFORE anything else. For realtime, change
   `_DEFAULT_TURN_DETECTION` or the per-session mapping and extend
   `test_realtime_quality_pass.py`.
3. **Never regress the invariants:** grace suppresses everything; the 60s caller
   hangup is driven only by caller silence (nudges don't reset it); the caller's
   FIRST utterance is never suppressed; agent-first calls never use the opening path.
4. **Verify on the overlay** with `/voice-eval` (multi-turn LLM-caller + judge on
   `/tmp/talky-verify`) — never place an out-of-hours call to a real prospect.
5. Report the before/after and the exact tunable moved. Deploy **only when asked**.

Prefer moving ONE tunable at a time; turn-taking is a trade-off curve (cut-offs ↔
lag) and two-variable changes hide which one helped.
