# Model-owned conversation: obsolete test contracts

Base: `2d17c523e05b43c07d8d9c96942c6c5dff56dd20`. This is a tests-only migration after the approved conversation-guide and model-selected-section changes. No application, provider, gold fixture, threshold, or release gate changed.

Source commit: `c3a58c3be23bf9d65e55ab3dc02738576e7e5df7`. Root and an independent reviewer cleared the five-file test delta; the reviewer did not rerun tests.

The unchanged six-module baseline produced **59 failed, 57 passed in 6.35s**. Failures expected the retired query CLI, query-shaped tool calls, exemplars, exact turn/opening scripts, duplicate compliance reanchors, or inline keyword fallback. The baseline also recorded pending-generator diagnostics from the obsolete query-adapter cases; these remain in the log. Compatibility imports still resolved, so this was not a collection or import failure.

| Module | Treatment |
|---|---|
| `test_ag02_semantic_qualification.py` | Keep the historical fixed corpus, evaluator-label isolation, original-question preservation, bounded profile, and dotenv-denial checks. Replace CLI success/overwrite expectations with rejection before runtime, credentials, or output for both dry and execution arguments. Retire query-adapter/continuation/transport expectations; current adapter behavior remains covered by the shared provider modules. No reconstructed historical runtime or synthetic semantic-success fixture was added. |
| `test_prompt_small_dialogue.py` | Retire the obsolete exemplar measurements, parser self-tests, fragment/opener scripts, recency-slot rules, and mandatory thinking-filler machinery. Existing conversation-guide tests cover neutral introduction state and natural wording. |
| `test_prompt_brevity_and_narration.py` | Replace sentence quotas, exemplar caps, forced silence/fillers and opener choreography with six assembly cases: the same concise, natural communication guide appears once for each persona on both slot-based and knowledge-driven paths. |
| `test_prompt_audit_fixes.py` | Keep independent disclosure-warning positives/negatives. Check that model-specific readback scripts remain absent and source guidance preserves original-question conditions. Retire duplicate reanchor and inline-fallback expectations; exact-section tests already cover unsafe source handling. |
| `test_prompt_consistency.py` | Keep opening-mode separation, shared opening construction, engineering-date exclusion and the existing voicemail check. Agent-first context must not invent a played receipt. Retire rigid wrong-person and silence wording. |
| `test_prompt_cache_shape.py` | Unchanged, including all six existing checks. Git-normalized bytes verified against the base. |

The [exact function inventory](artifacts/model-owned-test-contracts/retirement-inventory.json) distinguishes retained names from retired/renamed names; renamed assertions represent the replacements described above. Original assertions remain available in the base commit.

Final focused run: **50 passed in 3.64s**, across the five retained assessed modules plus the unchanged `test_agent_conversation_guide.py`. This includes the existing guide controls; counts are not additive to prior integration runs. Ruff `--select F` on the four edited test files and `git diff --check` passed.

Evidence: [baseline](artifacts/model-owned-test-contracts/baseline.txt), [final output](artifacts/model-owned-test-contracts/final.txt), [exact pytest arguments, interpreter and stable source hashes](artifacts/model-owned-test-contracts/final.json). Commands used the existing backend `.venv` Python 3.12 environment, `-B`, `PYTHONUTF8=1`, explicit worktree backend `PYTHONPATH`, `ENVIRONMENT=test`, and pytest `-q --tb=short --disable-warnings -o addopts=`. A process-local socket/DNS/async-transport guard blocked external connections while allowing the event loop's internal socket pair. No environment files were intentionally loaded; the dotenv control reads only its synthetic temporary file in a child process and expects rejection.

These are local prompt/data/retirement checks, not model adherence, semantic retrieval accuracy, audio, browser, database, customer acceptance, or production-readiness evidence. The retired profile's budgets do not authorize a replacement provider run.
