# AG02 Gemini section-browsing parity — 2026-10-07

Source `0754382629dd8760dc8a5317efab665b9a68779b` adds the same bounded tool dialogue to Gemini's existing native loop. The shared OpenAI-style loop did not control Gemini, so a catalog request was followed immediately by a tool-less answer and the model could not read its selected section. This is a two-file provider/test follow-up to the shared section reader; no new provider or adapter framework.

`max_tool_rounds` defaults to one and accepts only integers 1–3. Each decision keeps its original native model parts and thought signatures in the next request. Identical writes reuse their result across rounds; explicitly read-only tools rerun so a read → catalog → repeated read restores current evidence. After the finite decision budget there is one tool-less final answer. Existing direct-answer, no-tool and optional strict-content-buffer behavior remains.

Eight new controls reproduce the missing behavior: valid baseline **8 failed / 4 passed**, focused **12 passed**, final guarded four-module run **94 passed, zero warnings, 4.61 seconds**. The final scope is Gemini tools, Gemini provider, shared provider continuation and traditional wire-profile tests. Scripted SDK types and transport invoke the actual adapter, with the actual shared section helper in catalog→read; this is no provider/API acceptance or model-answer-quality claim. The first attempt's incomplete old query-fixture migration produced 9 failed / 3 passed and is retained separately. Counts overlap and must not be added.

All 16 final input hashes were stable during execution. The guard recorded 144 internal socketpairs and 0 prohibited network attempts. No live provider or database was used. Existing venv versions/executable/environment are recorded, without claiming requirements parity. The two files retain 21 existing Ruff diagnostics with identical rule counts; diff-check passed.

Both root and independent reviewer cleared the source. After testing, they requested restoring accidentally corrupted UTF-8 bytes in test comments/docstring. 14 tracked inputs match the source commit exactly; the remaining test's preserved tested snapshot matches the final test hash and its AST equals the committed test after excluding docstrings. `source-binding.json` records that deliberate distinction; no post-correction owner rerun is claimed. The parent integrated regression is separate.

This does not qualify semantic answer fidelity, latency, native speech, account/model availability or paid production readiness. Immediate model preambles are an explicit separate product direction, not a receipt guarantee.

Artifacts: `docs/sessions/artifacts/ag02-gemini-sections/manifest.json`.
