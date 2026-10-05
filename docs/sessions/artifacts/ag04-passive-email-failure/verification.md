# Honest passive email-failure guard repair

Source candidate: `5a6fa48ec706a5bf2df017a3b4e88517b3f9cf0e`, based on `be44bf7267ee2aa96ca851c8f0acdbc9e5733502`.

The baseline `be44bf72` actual OpenAI and xAI parser/bridge probes reproduced an output-gate error. After a synthetic definitive failed email receipt, `The email was sent to you.` was correctly blocked. The exact honest repair `The email could not be sent.` was also classified as a completion claim, exhausting the existing one-repair budget and leaving no submitted speech. `native-initial.json` retains both observations, the original synthetic request identity and failed receipt. No provider or database was used.

The bounded fix adds a direct email-subject modal-failure span to the existing completion classifier. Its exemption requires the entire completion match to fit inside that negative predicate, so a nearby quoted, hypothetical or actual negative does not license another positive completion. Existing action-family negation handling remains unchanged. Three unused imports in the touched module were removed after the F lint check reported them. No prompt, model, repair budget or executor policy changed.

Initial tests produced **12 failed / 8 passed** (`initial.txt`): ten modal variants across absent/failed receipts and the exact native phrase through two actual parsers. Review then found that applying full containment to every existing negative span would reject truthful coordinated negatives. That draft produced **3 failed / 25 passed** (`containment-review-red.txt`) and was rejected. The final implementation applies containment only to the new modal form; the original callback/form/email compound negatives remain supported. Mixed positive/negative clauses, both orders, quoted negatives and hypothetical negatives retain their completion checks.

## Verification

Working directory: isolated worktree `tmp/ag04-native-parity-20261005/backend`. Interpreter: existing `backend/.venv/Scripts/python.exe`, Python 3.12.12. Environment: `ENVIRONMENT=test`, `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test` (intentionally unavailable synthetic database).

```text
python -m pytest tests/unit/test_passive_email_failure_guard.py tests/unit/test_voice_action_contract.py tests/unit/test_llm_guardrails.py tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_qualification_gate.py -q -o addopts=
224 passed, 0 skipped, 1328 existing datetime.utcnow deprecation warnings

python -m ruff check app/domain/services/llm_guardrails.py tests/unit/test_passive_email_failure_guard.py --select F
All checks passed
```

The final batch includes 28 new regression cases; its exact log is `final.txt`. Earlier logs are retained as intermediate evidence, not additive test counts. `git diff --check` was clean. The actual native controls assert one synthetic executor attempt, zero accepted effects, exact original failed receipt retained, one repair, only the honest failure submitted, and no false completion in history.

Independent review checked the new grammar and identified the rejected containment regression before the final correction. Final read review found no additional material defect; the reviewer did not execute the suite or provider/database checks. Final scope is a bounded classifier repair, not a general natural-language negation guarantee. Compound new modal wording such as `could not be sent or delivered` is not established by this change. No durable duplicate-effect prevention, external email delivery, live model semantics or audio quality is certified. The preserved historical Groq failure and AG04 gate remain unchanged.
