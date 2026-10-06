# Knowledge tests aligned with model-selected source sections

Base: `2d17c523e05b43c07d8d9c96942c6c5dff56dd20`. Reviewed tests-only source: `ee31fd102ce1bf78c5d47f2f62ffa5d2c15dee8c`. Eight test files changed; no application, provider, gold fixture, retrieval threshold, or release gate changed. This migration removes expectations for live lexical injection, query reformulation recovery, per-turn database refresh, and scripted speech substitution that the approved model-selected section architecture replaced.

| Test module | Preserved or replacement contract |
|---|---|
| `test_agent_knowledge_repairs.py` | Keep independent source passage budgets, diagnostic ranking, and pure financial helper tests. The live test now checks navigation without body injection, followed by a complete authored section including its late warranty exclusion. Retained pure helper tests do not imply those helpers still rewrite live speech. |
| `test_kb_injection_budget.py` | Replace partial body injection with paged catalog navigation and an explicit `too_large` result for a section whose full conditions cannot fit. |
| `test_kb_source_first_all_paths.py` | Keep pure source renderer checks. Both actual traditional and native adapters must read authored text instead of conflicting generated derivatives, return the body once, and refuse an empty authored section. The removed injection path and appended price-guard script are no longer contracts. |
| `test_knowledge_default_recovery.py` | Model-selected sections are the primary path for all three enabled saved modes, independent of the retired flag. No live or pinned lexical search is invoked. Keep original question/context, clarification without false evidence, and the existing action dispatch boundary. Bounded catalog/read continuation and natural preambles are also covered by the existing `test_model_driven_voice_turn.py`. |
| `test_knowledge_tool.py` | Scoped catalog plus provider tool support controls availability. Exact arguments reject legacy queries; reads use the prepared snapshot. Preserve admin diagnostic hit-count options and generic Groq assembly/direct/tool continuation controls. |
| `test_knowledge_tool_evidence_lifecycle.py` | Actual Groq/streamer reads replace earlier evidence; catalog navigation, unavailable and oversized results clear factual grounding. New turns do not inherit a previous read as current evidence. Available means a section was read, not that a model answer is correct. |
| `test_knowledge_uncertainty_contract.py` | Preserve privacy, poisoned governing-condition refusal, inbound/outbound tenant/campaign boundaries, and source revision proof. Existing calls retain their snapshot; a new call may get a new revision. The emitted pre-model profile truthfully says `not_retrieved_this_turn`; final session evidence separately reflects subsequent reads. |
| `voice_pipeline/test_turn_streamer_kb_prefetch.py` | Replace the old serial-fetch timing test with a deterministic check that the model starts from a prepared catalog without the retired per-turn fetch or another setup load. No latency improvement is inferred from this test. |

The [function inventory](artifacts/ag02-knowledge-test-contracts/retirement-inventory.json) records 17 unchanged test functions, six updated names, 44 retired/renamed functions and 22 new/replacement names. Parameterized case totals differ from function totals. Original source remains in the base commit; nothing is skipped, xfailed or excluded to make this run green.

The same bounded eight-module selection produced:

- Unchanged baseline: **79 failed, 35 passed, 104 warnings in 7.74s**. These are stale-contract failures, not a newly reproduced application defect.
- First migrated collection: **two errors, one warning in 3.12s** because `request` is reserved by pytest. The collection snapshots are retained.
- Corrected fixture run: **90 passed, three failed, 52 warnings in 3.98s**. The test initially expected a pre-model profile to contain a later read status and expected a schema-rejected legacy query to dispatch the reader. Both expectations were corrected without application changes; source snapshots are retained.
- Final frozen source: **93 passed, 52 warnings in 4.63s**. These 93 checks are synthetic source/path checks, not model semantic quality or customer acceptance. Counts overlap earlier tests and are not additive.

Ruff `--select F` on the eight changed files and `git diff --check` passed. The [final result](artifacts/ag02-knowledge-test-contracts/unit-verified-result.json) records 21 selected source/test/config/runner LF hashes unchanged before/after, zero prohibited socket/async transport attempts, and 98 internal event-loop socket pairs. The runner denies network access, including Windows async transports. No database or provider call ran.

[Commands](artifacts/ag02-knowledge-test-contracts/commands.json) retain the exact interpreter, explicit worktree `PYTHONPATH`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, module selection and exit codes. The existing Python 3.12 virtual environment and actual installed package versions are recorded; this is not a claim of dependency equality with `requirements.txt`. Earlier failed phases retain their logs, results and runner snapshot. The shared model-turn helper and relevant implementation inputs are hashed; the source map is not an exhaustive transitive dependency inventory.

Independent review and root review cover the tests-only migration. The production-readiness freeze, external acceptance requirements and separate live answer/voice qualification remain unchanged.

The final 20 tracked recorded inputs match their Git blobs at the tests-only source commit after LF normalization; the 21st input is the evidence runner. The baseline 20 tracked inputs likewise match the base commit. This binds the recorded executions to those commits without claiming a rerun.
