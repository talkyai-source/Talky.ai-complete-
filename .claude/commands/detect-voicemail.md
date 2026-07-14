---
description: Improve or verify real-time VOICEMAIL / answering-machine detection (AMD) — the high-precision phrase gate that hangs up on a recording instead of talking to it.
argument-hint: "<goal> e.g. 'add a greeting phrase', 'a live call got hung up on (false positive)', 'we talked to a voicemail (miss)', 'check AMD precision'"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---
When an outbound call hits a voicemail, the agent must **not** hold a
conversation with the recording (product policy 2026-07-07: burns minutes, we
leave no message). AMD inspects the FIRST caller-side transcript; a match hangs
up immediately and marks the call VOICEMAIL (reschedules the lead +24h). This
skill tunes it for the goal in $ARGUMENTS.

## Who owns AMD (read these first)
- `backend/app/domain/services/voice_pipeline/transcript_heuristics.py` —
  `is_voicemail_greeting(text)` + the `_VOICEMAIL_PHRASES` tuple. **The core.**
  Pure, stateless, substring match on normalised text.
- `backend/app/domain/services/voice_pipeline/voicemail_detector.py` —
  `detect_and_hang_up_voicemail(call_id, text, turn_index)`. Only fires on
  `turn_index <= _MAX_TURN_INDEX_FOR_AMD (1)` (a greeting is the *first* thing
  heard). Flags the session `_amd_voicemail`, hangs up via the adapter, returns
  True so the caller skips the LLM. Fully fail-soft.
- `backend/app/domain/services/telephony/outcome_resolver.py` —
  `resolve_call_outcome(...)` + `_VOICEMAIL_HINTS` + `_looks_like_voicemail`.
  The *fallback* classifier (records VOICEMAIL from transcript/metadata even if
  real-time AMD didn't fire). Both call sites (cascaded `transcript_handler`,
  realtime `realtime_bridge`) invoke the detector.

## The one rule: PRECISION FIRST
A false positive **hangs up on a live prospect** — far worse than staying on a
voicemail a beat too long. `_VOICEMAIL_PHRASES` deliberately EXCLUDES ambiguous
wording ("you've reached" — a business greeting; "please leave" — "leave me
alone"; "is not available" — a receptionist). Only add a phrase that a *recorded
greeting* uses and a *live human answering an unexpected call would never say in
their first breath.*

## Goal → move
| Goal | Move |
|---|---|
| **Add a greeting phrase** (real voicemails we missed) | Add the lowercased substring to `_VOICEMAIL_PHRASES`; also add to `_VOICEMAIL_HINTS` if it should feed the fallback. Justify why it's unambiguous. |
| **False positive** (hung up on a live person) | Find which phrase matched (`/dbq` that call's `transcript_json`); REMOVE or tighten it (make the phrase longer/more specific). Precision > recall. |
| **Miss** (agent talked to a machine) | Check whether the greeting arrived after `turn_index 1` (then it's out of window — usually correct, don't widen) or used novel wording (add the phrase). Do NOT raise `_MAX_TURN_INDEX_FOR_AMD` casually — later turns let a live "leave a message" trip it mid-call. |
| **Audit precision** | Pull recent VOICEMAIL-outcome calls and recent short ANSWERED calls; eyeball for wrong hangups. |

## Change recipe (safe)
1. **Evidence from real transcripts** — `/dbq`:
   `SELECT id, outcome, left(transcript, 400) FROM calls WHERE outcome='VOICEMAIL' ORDER BY created_at DESC LIMIT 30`
   and the mirror query for very short ANSWERED calls (possible false positives).
2. **Edit the pure phrase list** in `transcript_heuristics.py`. Add/adjust a case
   in a unit test asserting the new phrase matches AND that a near-miss human
   phrase does NOT (guard the precision boundary explicitly).
3. Run the unit tests. Confirm `outcome_resolver` still classifies consistently
   (real-time hangup and the fallback should agree).
4. Verify on the `/voice-eval` overlay if behaviour-level confidence is needed.
5. Report added/removed phrases + the precision rationale. Deploy **only when asked**.

Never test AMD by calling a real voicemail out of hours — use recorded
transcripts and the overlay.
