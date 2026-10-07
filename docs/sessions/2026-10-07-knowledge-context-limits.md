# Bounded knowledge browsing and context recovery

Base: `6c9eed991a7c69afd70536379427933f099052d9`. Source/tests: `fe1d95c92ebb602ce5e43dca1f85b55d84e3fdd4` (seven application paths, two test paths; root and independent source review clear). This is local repair evidence within the existing freeze, not model-answer or paid-use acceptance.

The model can now browse a source's actual branches directly with `catalog_parent`, instead of scanning every preceding child in a flat catalog. Existing flat-list calls still work; flat sources remain visible and malformed ancestry remains explicitly unreadable. Exact section IDs, source revisions, tenant/campaign scope and full ancestor checks are unchanged. A temporary source/path index avoids rescanning every node for each ancestor.

Oversized source contexts can be read as contiguous `source_page` results with character offsets, a stable scoped digest and provenance spans. Each body is at most 12,000 UTF-8 bytes; the selected context is at most 48,000 bytes. The entire context is validated before any fragment is returned. Every fragment remains `context_complete=false`, including the last; fragments never enter verified grounding. The guide asks the model to read all contiguous parts with the same digest, including conditions. This is guidance, not proof of model compliance. Whole sources that fit remain `available`; unavailable, unsafe and above-limit sources remain explicit failures. Dashboard reads still recheck the current scoped snapshot and reject stale IDs.

Traditional context preflight now avoids a provider request already over the existing estimate: an oversized newest exchange asks for one narrower question, while an oversized setup reports unavailable. Canonical caller words stay unchanged; a later narrower question can proceed. The chars/4 estimate and model registry are unchanged. This does not promise exact tokenizer fit or unlimited memory. Broad flat catalogs, deep navigation plus many source pages, contexts above 48,000 bytes, and true model capacity can still exceed the finite seven-decision limit. No new model, search/ranking fallback, service, persistence, schema migration or provider was added.

Verification (all synthetic, no provider or DB calls):

- Baseline: two knowledge failures and two separately reproduced context failures. Initial affected run: 203 passed, one failure and two errors caused by test fixture import/Windows parameter-ID mistakes; next run: 204 passed and one missing test-acquisition fixture failure. Those logs are retained; no application defect is inferred from fixture mistakes.
- Final frozen source: **209 passed**, 93 deprecation warnings, 18.52s across eight modules, including 19 new controls. Earlier green totals overlap and are not added. Actual traditional streamer/shared loop, native adapter and dashboard dispatch are exercised through synthetic ports. Scoped Ruff F and `git diff --check` passed.
- One-shot local root-page timings for 500/2,000/5,000 shallow sections: before index 0.082920/1.226239/10.898275 seconds; after 0.003022/0.037839/0.066934 seconds. The inline command is preserved as `benchmark.py`; it used the uncommitted full-scan draft, then the indexed draft in the source commit (only a module docstring changed subsequently). Pre-run source hashes were not captured. These are bounded local measurements, not production latency qualification.

From this worktree's `backend`, with `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH` set to its absolute `backend` directory, the existing repository `.venv/Scripts/python.exe -B` ran:

```text
-m pytest tests/unit/test_knowledge_limits.py tests/unit/test_agent_context_window.py tests/unit/test_catalog_navigation.py tests/unit/test_knowledge_model_sections.py tests/unit/test_assistant_knowledge_authorization.py tests/unit/test_model_driven_voice_turn.py tests/unit/test_gemini_tools.py tests/unit/test_realtime_profile_contract.py -q
-m ruff check --select F app/services/scripts/knowledge/sections.py app/domain/services/voice_pipeline/knowledge_tool.py app/domain/services/voice_pipeline/turn_streamer.py app/infrastructure/llm/streaming.py app/infrastructure/assistant/agent.py app/infrastructure/assistant/tools/campaign_admin.py app/infrastructure/assistant/tools/llm_schemas.py tests/unit/test_knowledge_limits.py tests/unit/test_assistant_knowledge_authorization.py
```

Logs: [artifacts/knowledge-context-limits](artifacts/knowledge-context-limits/). Network was not instrumented; no measured-zero-network claim is made. Reviewer reads are separate from owner execution. Live/profile/audio and customer acceptance remain open, with no package/gate/freeze closure.
