# Bounded catalog navigation repairs

Source: `8676d0a7f870482406fc2b22e2f0caadb8d3b87c`, based on `1750341b`. Root and account_review independently reviewed the source. No providers, database, deployment or gold-data changes.

An oversized heading previously prevented the first catalog page from returning any usable section reference. Separately, three sequential catalog requests exhausted the traditional tool budget before an exact source read. The corrected baseline reproduced both failures through the shared reader/tool loop.

- Catalog headings and path labels now have an explicit `labels_truncated` marker and a 160-character navigation bound. Exact section references and full authored section/ancestor reads remain unchanged. An unfit metadata entry returns `too_large`, its skipped offset and an advancing continuation, so following sections are still browsable.
- A knowledge-owned callback credits only one actual advancing catalog result at the expected cursor of the current scoped snapshot. Repeated/nonadvancing pages, invalid results, source reads and mixed action rounds consume the ordinary budget. Tool loops independently allow at most four navigation credits, retaining three ordinary decisions: at most **seven tool decisions and one final answer**. Defaults remain unchanged, write deduplication remains across rounds, and Gemini keeps the provider's signed parts.
- This does not guarantee every catalog is reachable in one turn. Full source beyond the existing 12,000-character read bound remains explicitly withheld; no factual conditions are clipped. The root separately owns live TurnStreamer callback wiring and its integration test. This source commit's provider-loop checks alone do not prove that wiring.

Verification (synthetic model/SDK and storage ports, not semantic or live-service qualification): **128 passed, 25 warnings, 15.56s**, five modules, including 33 new controls. Actual shared/Groq/Cerebras/OpenAI/Gemini loops cover continuation, repeated pages, mixed duplicate writes, forced tool-less final and cancellation; native and dashboard boundaries cover shortened labels with complete source. Counts overlap existing controls and must not be added to other runs.

Run from this worktree's `backend`, with `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH` equal to that directory, using `C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe -B`:

```text
-m pytest tests/unit/test_catalog_navigation.py -q
-m pytest tests/unit/test_catalog_navigation.py tests/unit/test_knowledge_model_sections.py tests/unit/test_gemini_tools.py tests/unit/test_model_driven_voice_turn.py tests/unit/test_assistant_knowledge_authorization.py -q
-m ruff check --select F app/services/scripts/knowledge/sections.py app/domain/services/voice_pipeline/knowledge_tool.py app/infrastructure/llm/streaming.py tests/unit/test_catalog_navigation.py
git diff --check
```

Final scoped Ruff and diff checks passed. Including `gemini.py` reports its unchanged pre-existing `last_err` F841. Final post-run changes were docstrings and a fixture-import lint annotation only. No network-attempt instrumentation was added, so no measured zero-network counter is claimed.

Preserved logs: [first baseline](artifacts/catalog-navigation-repairs/baseline-first.txt) has the heading failure plus a synthetic call missing `arguments_raw`; [corrected baseline](artifacts/catalog-navigation-repairs/baseline-corrected.txt) has two genuine boundary failures. The [first affected run](artifacts/catalog-navigation-repairs/focused-first.txt) has 121 passes and five test-only wording mismatches (`excluding tax` versus supplied `exclude tax`). The [final run](artifacts/catalog-navigation-repairs/focused-final.txt) fixes that assertion and includes two additional native/dashboard boundary controls. No application repair was inferred from those fixture mistakes.
