# 2026-09-20 — The first inbound call on an internal PBX extension

Call `69f4de94`, 21:45:26 UTC, tenant `1845a165` (AllStateEstimation), campaign
`6cc54935`, `called_did = ext:940003`, caller `+442046132301`.
**status=ended outcome=answered duration=204s.**

## What it settles

Extension addressing works end to end, and the carrier really does deliver an
extension-to-extension call to this server — the one thing no amount of code
could prove. The chain ran: carrier INVITE → `exten => 940003` →
`Stasis(talky_ai,inbound,ext:940003,…)` → adapter → admission → the extension
branch of the assignment lookup → the right tenant, campaign and agent.

The agent greeted, named itself, disclosed it was an AI, refused off-topic
questions instead of inventing answers, answered from its knowledge base
(£55 check-ups), captured intent, offered a transfer, asked for a contact, and
read the address back so the caller could catch a mishearing. Summary:
"Qualified — wants client management info".

## Defect 1 (major, confirmed): a guard replaces a whole turn with a fixed line

The caller corrected a misheard email — "No. It is all state estimation at
Gmail dot com" — and got back
`"Sorry, I'm still here — what else can I help you with?"`.

That string is `_PHANTOM_GOODBYE_RECOVERY`
(`voice_pipeline/turn_runner.py:51`), spoken at `:437` and returned at `:456`.
It is not the silence monitor, not an empty-stream fallback and not a content
guardrail. The model had emitted an `end_session` envelope — it read the leading
"No." as the caller declining — and `should_honor_end_session`
(`end_session_action.py:44-72`) correctly refused to hang up. But the guard then
substitutes one fixed, context-free sentence and `return`s **without re-prompting
the model**, so a suppressed turn yields no substantive reply no matter what the
caller said.

It compounds twice:

* The filler is appended to history (`:440-442`) but `_SILENCE_CHECK_RE`
  (`:76-81`) matches "still **there**", not "still **here**", so
  `_agent_read_back_email` (`:154-186`) treats the filler as the read-back and
  closes the email confirm gate for the caller's repeat.
* The early `return` at `:456` skips `capture_mode.maybe_enter` (`:510`) and
  `update_state_from_agent_turn` (`:487`) entirely.

**Verified consequence:** no contact was captured. `leads` on campaign
`6cc54935` = 0, and no lead anywhere holds that address — while the summary's
action item says "email client management details to allstate estimation at
gmail dot com". The agent promised a follow-up nobody could send.

**Caveat, checked before claiming:** *no* inbound call has ever produced a lead —
34 inbound calls, 0 with `lead_id`. So inbound contact capture is a general gap,
not something this call broke. Both facts are true and should not be conflated.

### Sibling defect (latent)

`capture_mode` is armed only by the AGENT's outgoing text matching `_EMAIL_ASK`
(`capture_mode.py:28-39`, called only at `turn_runner.py:510`). Ran against the
four real agent lines: the two *asks* match; the two *read-backs* do not. So the
one turn where a caller re-spells an address — the correction after a read-back —
is exactly the turn that never gets relaxed endpointing. It did not truncate this
call, but it will.

## Defect 2 (minor): verbatim repetition

Three different off-topic questions returned the identical sentence three times.
Correct in substance, robotic in delivery.

## Defect 3 (minor): a truncated reply

`llm_response turn=3 said='Thing—what do you need help with today?'` — the engine
emitted the fragment, so it is not a TTS artefact.

## Defect 4 (operational)

* `inbound_recording_emergency_stop_applied active_calls=1` re-fires every ~30 s
  for the whole call rather than latching once.
* 35 × `telephony_audio_gap`, `gap_ms` 130–370 against `expected_ms=40`, with
  `arrived_ratio ≈ 1.000` — jitter in arrival timing, not packet loss. Whether
  the caller heard it is unconfirmed; only the caller can say.

## Not defects

The dental persona is that campaign's own content. Recording was off
deliberately. The speech-recognition mishearing ("waltz eight estimation") is
provider accuracy — and the confirm-loop did its job by reading it back, which is
how the caller caught it. The failure was everything after the correction.

## Fix first

`turn_runner.py:427-456`: re-prompt the model instead of substituting a fixed
line, and add the phantom line to `_SILENCE_CHECK_RE` so it can never masquerade
as a read-back.
