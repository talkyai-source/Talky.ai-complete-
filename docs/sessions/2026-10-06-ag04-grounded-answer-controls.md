# AG04 non-price grounded-answer offline controls - 2026-10-06

This slice adds a useful, non-price source answer and an absent-link containment control to the existing native and traditional replay corpora. It changes six test/fixture/runner files only. Application code, prompts, retrieval thresholds, models, matrix definitions, evaluator logic and tracker statuses are unchanged.

## Exercised boundary

The caller asks what the terminal requires for contactless payments. A pinned synthetic source says: “The terminal requires a compatible internet connection to accept contactless payments.” The actual retrieval and shared evidence preparation must report `matched`, preserve the source/node identity and versions, and place that text inside the actual `<company_knowledge>` data block delivered to the provider. Gemini's source is checked in its actual `config.system_instruction`, not only its conversational contents.

The positive control requires the entire direct answer and connection qualification in submitted speech and the intended assistant history. Native history also carries the fixture's correlated completed `transport_played` receipt. This synthetic acknowledgement is not evidence of human hearing.

The negative appends “I can provide a download link for the setup guide.” as the reachable second sentence. Native withholds the entire unsupported candidate and requests one repair without submitting its audio or recording it as spoken. Traditional preserves the useful source answer and replaces the unavailable offer with “I can't confirm an available download link.” Neither path attempts an external action or ends the call.

Common-control mutation checks deliberately remove the source, replace the answer with blanket abstention, move source text outside an unrelated empty data fence, and remove the native assistant-history projection. They must produce failing controls, never semantic approval. The existing price case `native.grounded_answer` retains its `ag04.supported_price` mapping.

## Preserved initial evidence

- [fixture-gap-before.txt](artifacts/ag04-grounded-answer/fixture-gap-before.txt): 14 missing-fixture failures before the extension. These are coverage gaps, not production runtime defects.
- [focused-initial.txt](artifacts/ag04-grounded-answer/focused-initial.txt): 9 passed / 5 failed on the first authored expectations. The Gemini check incorrectly looked only in messages; the initial traditional negative put its offer after the existing two-sentence cap, so it did not exercise resource containment.
- [traditional-initial-observations.json.gz](artifacts/ag04-grounded-answer/traditional-initial-observations.json.gz) and [initial-observations-console.txt](artifacts/ag04-grounded-answer/initial-observations-console.txt) preserve those actual synthetic observations. The final fixture uses one useful source sentence plus the offer, and inspects the full actual serialized request. No application behavior was changed to satisfy the control.

## Verification and limits

The final three-module qualification suite passed 153 tests with zero skips; scoped Ruff F and `git diff --check` passed. [qualification-tests-final.txt](artifacts/ag04-grounded-answer/qualification-tests-final.txt), [lint-final.txt](artifacts/ag04-grounded-answer/lint-final.txt), and [dependency-parity.json](artifacts/ag04-grounded-answer/dependency-parity.json) retain evidence. The exact pinned overlay satisfies all 62 active directly declared requirements; this is not a fresh transitive dependency resolution.

The single common CLI run used committed candidate `daf092b43ef84783888ff42d6396f57cbc9596e6`: **240 rows, 2,278 runtime checks, zero control failures, zero evidence errors and zero network attempts**. Its expected exit code is 1 because the historical Groq semantic failure remains failed; 239 other semantic findings are unreviewed. The 12 new rows cover one canonical condition, bringing the computed aggregate mapping to 43/50. Seven conditions remain unmapped; this does not establish complete coverage for any profile. [verification.json](artifacts/ag04-grounded-answer/verification.json), [replay.json.gz](artifacts/ag04-grounded-answer/replay.json.gz), and [new-controls.json.gz](artifacts/ag04-grounded-answer/new-controls.json.gz) retain the exact source provenance and results. New profile repetitions and negative variants are one canonical condition, not additional independent human scenarios. All new semantic results remain `unreviewed`; live, human and acoustic qualification is not run. The captured historical Groq comprehension failure remains failed.

This does not test model comprehension or universal factual entailment. An arbitrary invented benefit without a deterministic link/figure/action/relationship violation is outside this containment proof. Synthetic pinned source selection does not prove live database retrieval, document quality, knowledge approval, real provider behavior, audio quality, customer effects or durability. No network, provider, telephone or database operations were authorized or performed in this slice. No package acceptance is closed.
