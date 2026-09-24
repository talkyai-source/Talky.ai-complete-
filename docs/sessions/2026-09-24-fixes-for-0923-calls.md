# 2026-09-24 — Fixes for the defects found on the 2026-09-23 calls

## What happened

On 23 September there were 13 calls: 8 real phone calls (5 inbound from the owner's softphone to extension 940003, and 3 Dojo-PC outbound to +16478471491) and 5 browser Test-agent sessions.

A forensic pass read every call's transcript and logs, and a separate skeptic agent tried to refute each finding. It confirmed 13 defects. None was the agent hanging up on anyone: every call was ended by the caller or tester.

Each fix below was built test-first in its own isolated worktree, then reviewed adversarially. Where a review found real problems, the group went back for rework.

In total 32 commits went to `main`, all Python and no migration. The full backend suite result and the deploy result are recorded under [Verification](#verification).

## Fixes, by consequence

### 1. The dialer tried to redial someone who had just answered

**What went wrong.** `Database.update()` renumbers query placeholders with a find-and-replace. With 6 values plus 4 conditions, `$4` became `$10`, and then replacing `$1` also turned `$10` into `$70`.

**Consequence.** Every answered outbound call failed its job-status write with "could not determine data type of parameter $10". The job was then treated as "outcome unknown", reclaimed, and an automatic redial was scheduled. At 18:09:39 on 23 September that happened while the call was still connected. The operator's Stop button prevented it.

**What changed.**
- The placeholders are now shifted in a single pass (`core/db.py`).
- The lead cooldown is only cleared when there is genuinely no answered or live call inside the window. Before, it was cleared unconditionally.
- A cooldown now schedules one retry, for when it actually expires. Before, it retried every 5 minutes, which inflated the attempt count to about 25.
- The fix was checked against a real Postgres 16. The first version of the cooldown query used `make_interval(hours => float)`, which does not exist; the review caught it and it was corrected.

**Commits:** `5767df77`, `e8e93870`, `9a6d06f6`, `e692cd8d`.

### 2. Audio from before the recording notice was kept (callee-first mode)

**What went wrong.** On 6e0e221b the policy required a spoken notice. The call was answered at 18:09:18, but the notice only started at 18:09:26, and the whole 27.4 s recording was kept. The consent check only asked whether the notice was spoken at some point in the call.

**Consequence.** No harm on that call, because the number is the team's own. On a real campaign with a two-party policy in callee-first mode, it would record without notice.

**What changed.** The retention gate is reset at the moment the notice starts, so nothing from before it survives. The notice itself is kept as proof. True-inbound calls, agent-first calls and one-party policies behave as before.

**Commit:** `867466b8`.

### 3. Summaries stated things that never happened

**What went wrong.**
- 6aaeb4dd's summary said "Booked urgent appointment tomorrow", listed an invalid phone number, and listed an email nobody had confirmed.
- a5e033c7's said "scheduled root canal appointment". No booking system exists.

**What changed.** The summariser now receives the contact details that were actually confirmed and the actions that actually ran. Its rules say:
- Never write "booked", "scheduled", "confirmed" or "sent" unless an action result shows it; write "requested" instead.
- Mark any contact detail that was not confirmed as "(unconfirmed)".

**The promise-then-retraction on inbound calls.** A CALLBACK POLICY line in the agent's instructions now covers this. The agent may ask for and note a preferred callback time. It never says a callback or booking is scheduled; it offers to pass the details on "so the team can follow up".

**Commits:** `76afc83b`, `477df18b`, `55afa32f`, `3dd2d6d8`.

### 4. The caller's "Yes." was ignored, leaving silence (36357ad0, 2b36df30)

**What went wrong.** A short "yes" only counted as an answer if the agent's last line ended in "?". The Dojo opener "…got a minute? We're checking in on your payment setup." ends in a full stop.

**What changed.** A question in any sentence of the agent's last turn now counts. **Commit:** `4830baa1`.

### 5. "Hello??" was said while the caller was mid-sentence (7dbf415f)

**What went wrong.** The silence timer only looked at loudness once a second, so a pause between words read as silence.

**What changed.**
- Every speech-start event now marks the caller's turn as open. No nudge plays until the turn ends.
- There is a 12-second safety limit, so a lost end-of-turn event can't silence nudges for the rest of the call.
- Nudges no longer affect the turn latency figures.

**Commits:** `cd6904f6`, `70a3ae51`.

### 6. The callee said "Hello?", heard a bare hello, then 15.7 s of silence (6e0e221b)

**What went wrong.** The quick opener played a pre-recorded "Hi, hello." and then skipped the AI's reply for that turn. Since the August opener redesign, that greeting has no name and no reason for calling.

**What changed.** After the quick greeting, the normal reply runs straight away, so the name and reason follow. **Commit:** `ff11dd59`.

### 7. When the caller spoke in bursts, their words were forgotten (51450718, b97ce4c5)

**What went wrong.** A barge-in that came before the first sentence finished also deleted the caller's own message from the agent's memory.

**What changed.** The caller's message is now kept. **Commit:** `283efa88`. There is a known remaining gap, listed under [Not done](#not-done).

### 8. An invalid phone number was "confirmed" (6aaeb4dd)

**What went wrong.** The capture step flagged the 11-digit number as invalid, but the model read it back anyway ("923 016 253 19, correct?"), the caller said yes, and nothing was saved.

**What changed.** A fixed check now replaces any such read-back with "Sorry, could you say the complete phone number again, one digit at a time, including the country code?" A validated read-back passes untouched; the correct read-back on b97ce4c5 still works.

**Commits:** `e07a953a`, `0ba3e42b`, `6592685a`.

### 9. The email spelling loop never gave up (6aaeb4dd, 4 requests)

**What changed.** After 3 attempts the agent stops asking the caller to spell. It reads back its best understanding once, or moves on. After that the field is closed. Phone capture has the same limit.

**Commits:** `319cc598`, `d3ee6f70`.

### 10. The agent spoke an invented fragment ("…each month? few.", 3a17c06c)

**What went wrong.** This was a hole in the 23 September self-dialogue fix. The cut stopped the current sentence but kept reading the model's output.

**What changed.** The cut now stops the whole stream. **Commit:** `d3a475c8`.

### 11. The transcript was missing the opening line and the recording notice

**What changed.**
- Telephony: the greeting and the notice are now written to the stored transcript (`0479191a`).
- Browser Test agent: the greeting is written too, and an in-flight reply is cancelled before the transcript is saved (`4d269660`).

### 12. Text that was never played was saved as spoken (6aaeb4dd)

**What went wrong.**
- The hang-up cleanup tried to cancel the in-flight reply using the phone channel's ID rather than the pipeline's own ID, so it never found the reply.
- A failed audio delivery was still stored as a spoken line.

**What changed.** Both are fixed. **Commits:** `cfa59da2`, `91b61694`, `e66e7d8f`.

### 13. "Campaign started/stopped" never reached the activity feed

**What went wrong.** Four handlers wrote the event without the tenant context that row-level security requires, so every write was rejected.

**What changed.** They now use the tenant-scoped helper. **Commit:** `d2c4a5a7`.

### Smaller fixes

- **Suspicious-session warning:** an IP mismatch is now logged once, as the device-fingerprint mismatch already was (`26d00b51`).
- **Latency figures:** they can no longer go negative (`75062dfe`).
- **Misleading "pre-answer terminal" dispatch:** it no longer fires for bridge and media channels (`3021de62`).
- **Browser Test agent echo:** with barge-in off, the microphone now stays muted for every reply until the browser confirms playback has finished. Before, it was muted only during the greeting (`91b61694`, `6071a788`).

## Not done

All four items below were started in the second round but not finished before the session ended. None of them is on `main`.

- **The rest of #7.** A second rollback, in `VoicePipelineService._cancel_pending_turn`, can still remove the caller's message when a cancelled reply is slow to stop.
- **Transcript gap for cut-off replies.** A reply the caller partly heard before a barge-in is kept in the agent's memory but not in the stored transcript. The tests pinning the current behaviour say so explicitly.
- **Speech-start partials.** When the first word the speech recogniser reports is a filler ("Yeah", "Um"), it doesn't mark the caller's turn as open. That caller can still get a nudge.
  - Separately, the quick opener should not continue into a second reply when `TELEPHONY_LLM_OPENER_ENABLED` is on. That flag is off in production.
- **Two small cleanups.**
  - The recording notice still counts towards turn latency.
  - The "response start" figure has no staleness guard.
  - Three sibling `startswith("talky-out")` checks in `asterisk_adapter.py` were not narrowed.

## Not verified on live calls

- I tried to place scripted test calls into extension 940003 through Asterisk. The safety classifier blocked them because they need root on the production server, and I did not work around it.
- What is verified on the live server is that the new code is loaded and behaves correctly (see below). Live-call confirmation needs a real test call.
- **What to watch for:**
  - `phone_readback_blocked`: the invalid-number guard stepped in.
  - `model_wrote_caller_turn`: the agent was stopped from speaking the caller's line.
  - `backchannel_allowed reason=answers_agent_question`: a short "Yes." was treated as an answer.
  - `nudge SUPPRESSED`: a nudge was held while the caller was speaking.
  - The opening line should now appear in stored transcripts.

## Verification

- **Full backend suite (`tests/unit tests/security`, final tree):** 9,481 passed, 0 failed, 16 skipped. An earlier full run had one failure, the order-dependent timing test `test_opening_nudge_waits_for_caller_audio`. It passes 8 of 8 on its own, and a reviewer saw the same failure on the untouched base, so it is not caused by these changes.
- **Ruff gate:** clean.
- **Deployed:** production moved from `76426ed6` to `864325f7` (talky-api restarted Thu 2026-09-24 07:38:13 UTC). All five services are active, and the health, deep-health and workers endpoints all return 200. All 21 checks run on the server against the deployed code passed, and there were 0 errors after the restart. Rollback: `git checkout --detach 76426ed6`, then restart the four Python services. No migration was run.

## First live call on the new build, and the fix it led to

**Call c54579ea** was made at 07:51 UTC on 24 September, from softphone 940007 to extension 940003, and lasted 59 s.

**Confirmed live:**
- The agent's opening line, "Thanks for calling. How can I help?", is now the first row of the stored transcript (#11).
- There were 0 errors and 0 service restarts after the deploy.

**New problem found.**

1. At 07:51:27 Deepgram Flux stopped returning transcripts for 6 s of speech. The watchdog switched the call to Nova, as designed.
2. At 07:51:45.8, Nova's acoustic "speech started" event cancelled the reply to "I'm the existing patient." 0.5 s into playback. No words followed that event.
3. Nothing re-issued the cancelled reply, so the agent was silent for 14 s until the caller hung up.
4. The same word-less events also kept re-stamping the new "caller turn open" flag from #5. That would hold off the "Still there?" recovery indefinitely.

**Fix, commit `252887fa`, deployed at 08:19 UTC:**
- A barge-in with no caller words within 2 s (`VOICE_FALSE_BARGE_IN_WINDOW_S`) now re-issues the cancelled reply. It does not if a new turn is already running, or if the call has ended.
- When a barge-in cancels a reply, the caller's own message is now kept in history. This closes the round-two #7 gap.
- A "turn open" stamp with no caller words behind it counts for at most 3 s (`VOICE_CALLER_TURN_NO_TEXT_S`).

**Tests:**
- New tests in `test_false_barge_in_resume.py` (4) and `test_regreet_ladder_over_open_caller_turn.py` (1) fail on the previous code and pass on the new.
- Full suite: 9,485 passed, 16 skipped, and 1 failure: the known intermittent timing test, which passes 8 of 8 on its own.
- Ruff: clean.
- All 23 checks on the deployed code passed, with 0 errors after the restart.
