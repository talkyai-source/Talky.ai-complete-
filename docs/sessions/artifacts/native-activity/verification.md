# Native caller activity bookkeeping

The native bridge accepted fresh caller turns without updating the real `CallSession.last_activity_at`. The telephony watchdog could consequently classify that session as inactive. This is a local activity-bookkeeping repair, not a new silence policy or AG04 acoustic pass.

Base: `1f337029`. The final source SHA, exact commands, environment and canonical source hashes are recorded in [manifest.json](manifest.json). Only `backend/app/realtime/bridge.py` and the new `backend/tests/unit/test_native_caller_activity.py` change source/tests. Existing qualification runners, corpora, matrix, evaluator, thresholds and captured Groq failure are unchanged.

## Reproduction and final contract

The controls use the actual OpenAI and xAI event parsers and `RealtimeBridge`, with a real `CallSession` in the same contact/action-session roles used by `realtime/runtime.py`. SDK/socket and media ports are synthetic. A 301-second-old activity timestamp is seeded; after a current admitted caller question and completed synthetic model response, the old code leaves that timestamp unchanged. The actual `_collect_expired_sessions` classifier then returns `stale` using the existing 300-second inactivity threshold, with a 600-second absolute limit and the optional soft cap disabled to isolate inactivity. No five-minute call, real watchdog termination or provider connection occurred.

[admission-contract-before.txt](admission-contract-before.txt) preserves the final-policy baseline: **6 failed, 26 passed**. Both provider parsers fail the fresh-current-final, legacy-admitted-first-final and fresh-activity/absolute-limit controls.

[initial.txt](initial.txt) preserves the earlier **8 failed, 24 passed** exploratory contract. Two of those initial failures expected current-item ASR replacement to refresh activity. Review rejected that expectation: a correction can arrive late and does not establish a new caller turn. The retained replacement control instead requires unchanged activity, and the final-policy baseline above supersedes that expectation. No application change was made between those two baseline runs.

The repair calls the existing session `update_activity()` only after a newly admitted, nonempty **current** caller final, before any awaited downstream work. The runtime passes the same real session for contact and action processing. Optional synthetic/legacy bridge objects without that callable remain supported; no replacement timer is invented. Historical finals, current-item corrections/retractions, duplicate or unowned input, stopped input, VAD without a final, raw PCM and assistant output cannot refresh the clock through this change. `started_at`, inactivity thresholds and absolute/soft-duration policy are unchanged.

## Verification scope

The new module has **32 parametrized controls** across OpenAI and xAI. It verifies current-turn activity and legacy admission, correction/retraction exclusion, late historical and older revised input, unknown/duplicate identity, empty/whitespace finals, stopped input, raw quiet/loud PCM, assistant output, genuine no-input expiry and the independent absolute-duration limit. The raw PCM controls prove transport forwarding does not itself update activity; they do not classify noise, music, echo or a real speaker. Every owned case runs within the existing offline network guard and asserts zero attempted network operations.

The final nine-module regression passed **270 tests, with zero failures or skips**, in 16.40 seconds ([final-regression.txt](final-regression.txt)); it includes all 32 new controls. The 740 warnings are retained in that log, predominantly existing `datetime.utcnow()` deprecations, including calls in the new clock fixtures. CI-rule Ruff passed ([ruff.txt](ruff.txt)). Existing unit suites cover native relationship/contact/revision behavior, closure, lifecycle and the common native runner. Overlapping red and final runs are not additive acceptance counts.

This does **not** add native acoustic echo rejection: no such classification is supplied by this repair. A provider's newly admitted false-positive transcript could still appear to be caller speech; provider/source attribution remains the external acceptance gap described in [unmapped-inventory.md](unmapped-inventory.md). The seven unmapped AG04 conditions and 43/50 aggregate mapping remain unchanged. No database, provider, browser, human or deployment acceptance was run, and no new call-duration policy was approved.

Root and RT independently reviewed the current-only admission boundary and final delta before commit; their review scope and final command results are recorded in the manifest. The source commit and evidence commit are separate.
