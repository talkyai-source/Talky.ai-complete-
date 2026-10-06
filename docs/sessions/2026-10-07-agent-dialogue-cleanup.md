# Remaining dialogue override cleanup — 2026-10-07

Source: `c89d58cc0c430252c707ad81957e3c3cd4af7ba4`, based on `458c6798`. This implements the user's simpler, model-owned conversation direction. It is not production or model-quality acceptance.

Removed ordinary wrong-business/wrong-person/ambiguous-number classifiers, fixed identity replies and forced hangups; phantom hangup retry prompts/canned replies; silence check-in/opening phrase ladders and their optional background generation; unused preceding-relationship metadata; stale service imports. An unauthorized legacy end action now retains already-spoken prose without generating another reply.

Directed opt-outs still persist before the model turn. The model receives a neutral recorded/unconfirmed result, while the caller's continued question remains in context. Existing tenant/phone binding, failed-write retry, conservative failure farewell and caller end authorization remain. Actual audio ordering, interruption, caller turn ownership, recording disclosures and configured agent-first greeting remain. STT lost-input, LLM/provider failure and DNC failure recovery still have operational fallback wording; this patch does not claim every fixed line is removed.

The silence monitor now enforces disconnection only. Current caller text/turn activity, backchannels and active AI playback still protect the call. After a long AI answer, the configured idle interval starts anew when playback finishes; the caller does not inherit the time spent listening.

Deleted app files: `backend/app/domain/services/telephony/opening_ladder.py` and `backend/app/domain/services/voice_pipeline/turn_director.py`. Deleted obsolete script-only test modules: `test_opening_ladder.py`, `test_prewarm_opening_ladder.py`, `test_turn_director.py`, `test_greeting_duplication.py`, `test_no_hangup_on_an_open_question.py`, `test_silence_nudge_latency_not_tracked.py`. Their phrase ladders, exact persona/exemplar scripts and question/contact/first-turn close-veto assertions no longer describe the intended runtime. The manifest inventories exact retired/renamed test functions. Retained/migrated tests exercise caller authorization, DNC, accepted caller ordering/cancellation/replay, ordinary model wording, real ingest task/STT event ownership and the silence deadline.

Validation (synthetic providers/ports; no actual provider, network, DB or call):

- New actual-turn baseline: **4 failed, 1 passed** (8.31s). Three identity bypasses and one scripted retry reproduced. Initial import-path collection error is retained separately.
- First focused source check: **28 passed**. Initial wider check: **23 failed, 161 passed**; this includes a missing Message import introduced during editing, stale classifier/intent/prompt assertions and three base-branch cap dependencies. All owner issues were fixed; this history is retained.
- Final aggregate: **212 passed, 3 deselected, 670 warnings, 17.86s**, 13 modules. The explicit exclusions are the two sentence-budget tests deleted by root and the full-pricing-output test dependent on root's cap removal. They are not counted as passed here; root owns a merged run without those exclusions.
- Afterwards, only two new DNC test assertions moved outside the fake provider generator so runtime exception handling cannot swallow assertion failure: **7 passed, 60 warnings, 9.15s**. No app source changed after the aggregate. Counts overlap and must not be added.
- Scoped Ruff F checks and `git diff --check` passed. UTF-8 comment corruption during local editing was corrected before qualifying runs. The final lint log also records unused-import fixes; no behavior was changed by them.
- Root and independent CRM source review cleared the bounded change, including the long-playback idle reset.

Exact commands, raw results, current source/deletion hashes and retired-function inventory: [artifacts](artifacts/agent-dialogue-cleanup/manifest.json), [commands](artifacts/agent-dialogue-cleanup/commands.txt). These controls verify local orchestration and persistence boundaries, not whether a live model always chooses an appropriate sentence or whether real acoustic/customer acceptance passes.
