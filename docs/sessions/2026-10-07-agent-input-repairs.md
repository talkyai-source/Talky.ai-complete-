# Caller input and close-floor repairs — 2026-10-07

Source commit: `ced8874d28995203438d903a3aafa448e809171d`, based on `1750341b`. This bounded change fixes three reproduced input-handling defects; it does not qualify model understanding, live audio, provider behavior or customer acceptance.

## Symptoms, causes and repairs

- A finalized “Yes”, “Okay” or “Sure” after an agent statement could disappear without a model reply. Listener-acknowledgement suppression ran even when the agent was idle and depended on previous question punctuation. It now applies only during actual TTS overlap; idle finalized replies reach the model. Existing overlap/question handling remains.
- A first answer such as “7”, “42”, a signed decimal or a phone number failed the alphabetic-character floor. Decimal numeric content now satisfies the meaningful-input check. The existing recognition-confidence check remains, and punctuation-only input and low-confidence numbers are still rejected.
- Fresh caller speech beginning with “Okay”, “Thank you” or a goodbye could lose to a previously requested hang-up. Fresh bounded caller-floor evidence now precedes partial courtesy exemptions. Quiet or stale speaking flags still permit an independently caller-authorized close.

Reference review found no consumers of `agent_left_a_question_open` or `contact_capture_open` under app, scripts or tests; those dead helpers and their private sentinel pattern were removed. No authorization, DNC, provider, database, contact-persistence or audio-transport machinery was replaced.

## Qualification

The new 18 controls exercise actual TurnEnder/TurnRunner admission and model streaming, plus the actual pending-close decision with synthetic model/TTS/shutdown ports. Before app edits, **11 failed / 7 passed** (90 warnings, 12.61s): three idle replies, five numeric first answers and three fresh-speech closes reproduced the defects. The seven unchanged positives covered actual overlap, noise/confidence and quiet/stale authorized closes.

- Focused final: **18 passed**, 157 warnings, 4.19s.
- Affected final: **257 passed**, 768 warnings, 24.94s across ten modules, no exclusions. The 18 focused cases are included in this count.
- Scoped Ruff F and `git diff --check`: passed.

Two existing test expectations were migrated: idle acknowledgements are admitted, and the courtesy-close positive explicitly represents a quiet caller. The older log-only compound-question fixture remains a boundary check; the new actual-path controls supply downstream behavior evidence.

All pytest runs used the existing Python 3.12.12 virtual environment, not a newly resolved requirements installation. The archived runner denied provider/database socket and async transport connections, permitting only internal Windows event-loop socketpair construction. Prohibited attempts: **0** on baseline, focused and affected runs. All recorded source inputs were unchanged during each run. This is offline unit evidence, not a real call, browser, provider or database test.

## Reproduction and artifacts

Run from this worktree with `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, `ENVIRONMENT=test`, `PYTHONPATH=<worktree>/backend`, and `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`. The runner requires no `backend/.env`, refuses to overwrite an existing phase report and records interpreter, exact pytest arguments, raw input hashes and transport counters.

```powershell
& '<repository>/backend/.venv/Scripts/python.exe' -B docs/sessions/artifacts/agent-input-repairs/runner.py affected `
  backend/tests/unit/test_agent_input_repairs.py `
  backend/tests/unit/test_voice_pipeline_turn_0_floor.py `
  backend/tests/unit/test_no_hangup_over_caller.py `
  backend/tests/unit/test_backchannel_after_compound_question.py `
  backend/tests/unit/test_caller_speaking_cleared_on_end_of_turn.py `
  backend/tests/unit/test_caller_authorized_end_call.py `
  backend/tests/unit/test_voice_pipeline_service.py `
  backend/tests/unit/test_dialogue_cleanup.py `
  backend/tests/unit/test_backchannel.py `
  backend/tests/unit/test_interrupt_concurrency_and_pre_tts.py
```

Pytest options were `-q --tb=short --disable-warnings -o addopts=`. Baseline and focused phases used the same runner with only `test_agent_input_repairs.py`. Existing phase reports are historical artifacts; use a new phase name for another run.

Evidence: [baseline](artifacts/agent-input-repairs/baseline.txt), [focused](artifacts/agent-input-repairs/focused.txt), [affected](artifacts/agent-input-repairs/affected.txt), matching JSON provenance beside each log, and [runner](artifacts/agent-input-repairs/runner.py). CRM independently reviewed the frozen source/tests without rerunning them; root cleared the source commit. No plan gate is closed by these unit results alone.
