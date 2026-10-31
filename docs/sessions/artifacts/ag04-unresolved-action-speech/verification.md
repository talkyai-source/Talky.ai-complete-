# Unresolved action speech verification

Final source: `39b9548080277ee9ddf2d39d16b26a5b33ea9873`, correcting initial source `7e6a96bfab298fa254feb5470b51dd030e612c1c`, based on `add699c4`. The initial source alone is not the integration candidate. Separate common replay tests are commit `3b00bc68` between these two source commits.

The actual speech validator distinguished unconfirmed success from a confirmed success but discarded negative completion phrases before consulting the receipt. With `unknown` or `in_progress`, it rejected “The email was sent to you” yet allowed “The email was not sent,” “The email could not be sent,” and “Please send the email again.” This contradicted the existing unresolved receipt's instruction to review before repeating. The probe does not demonstrate a duplicate external effect: the durable executor's claim and replay policy are unchanged.

The bounded repair checks only existing `unknown`/`in_progress` action receipts for recognized direct failure and action-named repeat claims. It reuses the existing assertion filter for quoted, reported, hypothetical and negated mentions. It preserves definitive-failure and absent-receipt behavior, unrelated conversation, and generic “try again” wording that has no identified action. The existing uncertainty prefix also accepts “cannot confirm whether.” A separate qualifier-scope view retains offsets and full quote spans for unresolved negative/repeat filtering. Positive completion matching preserves the original text and original full predicate patterns: an uncertainty exemption is withheld if a second full action predicate follows an independent boundary within a greedy match. The shared caller assertion policy itself is unchanged.

The returned reason remains `action_failed:<action>:<status>`. Existing traditional safe speech, native one-repair budget, prompts, provider defaults, action executor and durable receipt policy are unchanged. This is a bounded language guard, not a general natural-language understanding or retry-authorization guarantee.

## Preserved reproduction and checks

- `unit-initial.txt`: **40 failed / 39 passed** before application edits at the baseline.
- `unit-first-fixed.txt`: first draft **79 passed**; not final acceptance.
- `uncertainty-mixed-red.txt`: **2 failed / 82 passed** after adding same-clause qualifier controls.
- `clause-review-red.txt`: expanded independent review controls **6 failed / 86 passed**, including uncertainty/reported prefixes leaking into a later repeat request.
- `guard-final.txt`: intermediate four-module batch **202 passed**.
- `guard-final-frozen.txt`: initial candidate four-module batch **206 passed / 0 skipped**, including **96** new cases; superseded by the review failure and correction below.
- `root-clause-regression.json`: root's exact baseline/candidate probe showed four positive statements newly admitted by splitting punctuation and coordinated objects. `predicate-span-red.txt` reproduces **12 failed / 112 passed**, testing each across absent, unknown and failed receipts.
- `predicate-span-final.txt`: a nearest-predicate prototype passed **234** checks, but was rejected when `nearest-predicate-rejected.txt` then reproduced **2 failed / 124 passed** for a negative modal followed by an unproved delivery claim. The final code restores the original positive matching patterns rather than shipping that prototype.
- `predicate-preserved-final.txt`: corrected final four-module batch **236 passed / 0 skipped**, including **126** new cases; 28 existing datetime deprecation warnings. Earlier runs are not additive test counts.
- `lint.txt`: Ruff F checks passed; `git diff --check` passed before the source commit. The new unit file was formatted with Black; unrelated source formatting was retained.
- `probe-before.json`, `probe-after.json` and `probe-final.json`: 39 synthetic receipt/text combinations each. The middle file retains the initial candidate; only `probe-final.json` uses the corrected final source. `probe.py --source-ref add699c4` loads the baseline module verbatim from local Git into an isolated module; a probe without that flag imports the actual working-tree module. Each artifact records its source SHA-256 and execution distinction. No model, database or network calls occur.

Commands ran from `tmp/unresolved-action-speech-20261005/backend`, with the original repository's `backend/.venv/Scripts/python.exe` (Python 3.12), `ENVIRONMENT=test`, and an intentionally unavailable synthetic `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`. The test `PYTHONPATH` used the existing exact-requirements overlay first, followed by the existing Lua test-dependency overlay:

```text
tmp/postgres-matrix-20261005/tmp/exact-requirements-20261005/packages
tmp/production-ready-ag07-20261005/tmp/op02-testdeps

python -m pytest tests/unit/test_unresolved_action_speech.py tests/unit/test_passive_email_failure_guard.py tests/unit/test_voice_action_contract.py tests/unit/test_llm_guardrails.py -q -o addopts=
python -m ruff check app/domain/services/llm_guardrails.py tests/unit/test_unresolved_action_speech.py --select F
python -m black tests/unit/test_unresolved_action_speech.py

python ../docs/sessions/artifacts/ag04-unresolved-action-speech/probe.py --source-ref add699c4 --output ../docs/sessions/artifacts/ag04-unresolved-action-speech/probe-before.json
python ../docs/sessions/artifacts/ag04-unresolved-action-speech/probe.py --output ../docs/sessions/artifacts/ag04-unresolved-action-speech/probe-final.json
```

For direct probe execution, the backend current directory was also placed on `PYTHONPATH`; it contains no third-party dependency overlay. The probe's fallback helper is unchanged from the baseline.

Independent review by `/root/audio_audit` first reproduced the mixed-clause gaps and rechecked seven synthetic cases; root's subsequent broader predicate review found the four regressions above. After the follow-up, Audio independently ran 19 actual-validator controls covering the root positives across absent/unknown/failed receipts, embedded-comma uncertainty, mixed claims/repeats, quotes and negative repeats. All 19 passed, with no additional material defect found in the final bounded read review. The reviewer did not run the full guard suite, a provider or PostgreSQL; its probe artifact belongs to the separate common-replay evidence directory.

## Boundaries

Only `llm_guardrails.py` and the new focused unit module belong to this source commit. Separate native/traditional common replay fixtures are owned and committed independently. No model selection, prompt, executor, durable receipt, provider action or production configuration was changed. No acoustic, live-model, external delivery, operator-resolution or durable duplicate-effect acceptance is inferred from these tests. Unrecognized paraphrases and action-free pronouns are not comprehensively classified. The historical Groq semantic failure and other AG04/live/profile acceptance gates remain open; the production-readiness feature freeze is unchanged.
