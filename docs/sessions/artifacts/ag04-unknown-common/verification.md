# AG04 unresolved-action common replay

This extension exercises the existing actual traditional and native speech guards with explicit synthetic `unknown` and `in_progress` receipts. It does not perform, retry or reconcile an external action. The connected executor port is intercepted, and its original request identity/status/null message ID are retained. Application grammar repair belongs to the separately committed shared guard change, not this test-only extension.

## Initial observations

The probe at base `add699c4f7bf1fbf603f078e862f7229a04406c8` observed the same gap through all six existing adapter profiles for both statuses: positive completion was blocked, but `The email could not be sent.` and `Please send the email again.` reached submitted speech even though the result remained unresolved. The twelve observed rows are preserved in `initial-observed.json`; the corresponding `initial-probe.py` uses actual methods under the common socket/DNS guard. The probe records observations; its exit zero is not a safety pass. Native empty expectation lists in that diagnostic likewise do not count as passed controls.

For reproduction, place the probe in ignored `backend/tmp/unknown_common_probe.py`, then run `python -m tmp.unknown_common_probe` from `backend`. The shared guard owner's unit reproduction separately establishes failing expectations before repair. `first-draft-replay.txt` records twelve passing new common tests against the initial uncommitted guard draft; it is intermediate evidence, not the final source qualification.

Independent app review also found three mixed-sentence escapes in the draft: uncertainty followed by a positive completion in the same sentence, uncertainty followed by `so resend the email`, and a reported quoted resend followed by an independent `I will resend the email now`. These were sent to the guard owner for bounded correction. No caller-intent or global call-control helper was edited by this runner work.

Final bounded app review found those cases corrected. Seven independent pure-validator probes checked four mixed-sentence escapes (including both `but` and `and` positive completions) and three preservation cases: a whole quoted repeat, a coordinated explicit prohibition on repeating, and uncertainty followed by an ordinary offer to explain the next step. The first four were blocked and the latter three allowed. This is a small actual-method check, not another suite, provider or database run.

That review was insufficient to freeze the first app candidate. Root subsequently reproduced four positive-claim regressions outside the common cases: comma-parenthetical callback/email/form completions and a coordinated email object no longer matched after global clause splitting. The first exact common run at test-only commit `3b00bc687c5117c38db1bda7cd82b9a79dc052f2` was therefore explicitly rejected as final qualification despite zero common control failures. `pre-predicate-fix-cli.txt`, `pre-predicate-fix-summary.json` and `pre-predicate-fix-tests.txt` retain the intermediate results. This illustrates why replay counts alone do not establish complete safety.

The guard follow-up at `39b9548080277ee9ddf2d39d16b26a5b33ea9873` restores matching against whole original positive predicates. Only unresolved negative/repeat qualifier filtering keeps the offset-preserving normalized view. Independent actual-validator checks in `independent-predicate-review.json` passed all nineteen controls: the four root regressions across absent/unknown/failed receipts, honest embedded-comma uncertainty, mixed positive/repeat clauses, whole quoted repeats, explicit prohibitions and honest check-before-retry wording. The common runner source did not change for this follow-up. This review remains a bounded phrase check, not general language comprehension proof.

## Controls and limits

Each of the two status variants executes the synthetic email action once. Subsequent caller turns challenge the unresolved result with false success, false definitive failure and action-specific resend language. Common controls require the original receipt to remain unchanged, zero accepted effects, no unsafe text in committed speech/history and the truthful response on each turn.

The native sequence uses the existing per-caller-turn repair budget: each unsupported candidate is withheld, then the authored honest repair is admitted. Traditional runtime guards replace each challenged statement with the existing truthful fallback. Its first sentence is submitted under the existing sentence cap; a final authored response separately verifies that the full check-before-retry statement is admitted. This extension does not change that cap or claim that every fallback speaks both sentences.

Traditional replay now emits optional canonical `semantic_ids`, matching the existing native row contract. Both status variants map to the single existing `ag04.action_unknown_outcome` scenario. The evaluator, canonical matrix, thresholds and captured Groq sample are unchanged. Repeated adapters and statuses are not new human scenarios.

One executor invocation in an authored sequence does not prove durable idempotency or safe repeated requests after a real timeout. These fixtures do not test actual provider delivery, receipt reconciliation, database failure/restart, model comprehension, human hearing or telephone acoustics. All new semantic findings remain unreviewed.

## Focused verification

Final source candidate: `39b9548080277ee9ddf2d39d16b26a5b33ea9873`, including test-only source `3b00bc687c5117c38db1bda7cd82b9a79dc052f2`. Working directory: `tmp/unresolved-action-speech-20261005/backend`. Interpreter: existing repository `backend/.venv/Scripts/python.exe`, Python 3.12.12. Environment: `ENVIRONMENT=test`, `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test` (synthetic unreachable port). The final pytest/CLI commands used existing exact-requirements and Lua test-dependency directories on `PYTHONPATH`, in this order:

```text
tmp/postgres-matrix-20261005/tmp/exact-requirements-20261005/packages
tmp/production-ready-ag07-20261005/tmp/op02-testdeps
```

```text
python -m pytest tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_qualification_gate.py tests/unit/test_ag05_native_contact_revision.py tests/unit/test_customer_claim_admission.py -q -o addopts=
180 passed, 0 skipped; 1842 existing datetime.utcnow deprecation warnings

python -m ruff check tests/qualification/ag04_traditional.py tests/unit/test_ag04_native_qualification.py tests/unit/test_ag04_traditional_qualification.py --select F
All checks passed
```

Logs: `final.txt` and `ruff.txt`. Counts overlap the prior parity suites and must not be added to them as independent coverage.

## Final common evaluation

```text
python -m scripts.evaluate_ag04_conversations --output ../tmp/ag04-unknown-common.json
exit 1
```

The evaluator recorded the exact final source candidate above with no uncommitted source changes. All 228 declared/observed profile-scenario rows and 2,146 runtime control checks completed, with zero control failures or evidence errors. There are 80 distinct replay scenario IDs across engines. The preserved Groq prior-question semantic failure remains failed; the other 227 findings remain unreviewed. Production approval is false, and live profile, human rubric, telephone audio/hearing/latency and external-effect verification remain `not_run`. Provider calls, telephone calls and attempted blocked network operations were all zero.

The canonical union now maps 42 of the 50 proposed conditions, adding only the existing unresolved-action condition. Traditional configurations map 33 conditions each; native configurations map 36 each. Mapping is observed replay coverage, not semantic approval, and does not fill the other engine's gaps. Eight global conditions remain unmapped: background speech, echo reentry, grounded nonprice answer, hold music, sensitive data offer, silence without request, unclear name and unrelated request. The canonical matrix and its ratification status are unchanged.

`common-summary.json` retains counts, failed semantic evidence, exact candidate provenance and per-profile matrix gaps. `common-new-controls.json` retains the twelve added rows, including submitted speech/history, original receipts, per-turn controls and observed state. The complete generated report remains at ignored `tmp/ag04-unknown-common.json`; the full source-file hash map and repetitive request bodies are omitted from the checked-in excerpts. `common-cli.txt` preserves the command output. No profile is approved and no readiness gate is lowered.

The sibling guard owner independently read the five-file runner/fixture/test delta and found no additional material defect. That review did not execute another suite or provider/database operation.
