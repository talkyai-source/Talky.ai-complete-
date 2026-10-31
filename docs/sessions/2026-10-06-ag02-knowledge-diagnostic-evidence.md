# AG02: make the existing knowledge preview reflect source evidence

The existing “test a question” preview discarded the evidence boundary used by the voice agent. It returned only raw candidate headings, generated summaries/voice answers and ranking scores. The panel displayed generated phrasing without a weak/no-match distinction or source revision, and promised follow-up when no candidates were found. An unenriched source could show no useful answer text even when its original content was usable.

Two pre-fix controls reproduce this through the actual endpoint and SQL retriever, replacing only fetched database rows: a weak candidate and a strong candidate with contradictory generated phrasing both lacked the shared source evidence. Their original source said `$20 per month, excluding tax`; the generated fields said `$999` or “Everything is free.” Both failed the expected evidence contract. The retained baseline log is not a model-quality or production-data test.

## Bounded repair

- The existing `/campaigns/{id}/knowledge/test` response now includes the result of the same `prepare_knowledge_evidence` helper used by voice consumers: status, admitted source passages, node/source versions and measured coverage. Existing raw `hits` remain unchanged for compatibility.
- The existing frontend API method validates that contract, including the returned query, coherent status/passages and nullable provenance. Missing/invalid evidence is an error; it cannot fall back to generated raw-hit phrasing. No threshold is duplicated in the client.
- The existing panel displays original source passages, explicit weak/no-match wording and exact source/section revisions. Missing provenance is shown as unavailable. A successful lookup is described as a retrieval check, not proof of a correct generated answer or a heard call. No-match promises no follow-up.
- Changing the query, campaign or source invalidates prior diagnostics. Old in-flight responses/errors are ignored; tests are disabled while a source mutation is pending.

No new endpoint, permission, provider, retriever, dependency, migration or feature was added. Ranking, thresholds, the raw gold fixture, source publication and enrichment were unchanged. Existing read authorization, tenant predicates, database-error propagation and no-hit-count-write behavior remain in place. The frontend intentionally rejects an older backend response without the additive evidence field.

## Verification

- Baseline: **2 failed**, 4 deselected, 3.90 seconds, before production edits, both missing `evidence`.
- Focused backend: **13 passed**, including **9 new** controls.
- Final affected backend: **156 passed, 11 warnings, 13.20 seconds**, eight modules; includes all 13 focused controls. The runner recorded 20 input LF hashes before/after, with no changes, and zero prohibited socket/async transport attempts.
- Frontend: **25 passed, 0 failed, 0 skipped**, including **22 new** component/API controls. The first run passed with React act diagnostics; the final run used explicit awaited act boundaries and had zero such diagnostics. This was a test-harness correction, not a product failure.
- Full TypeScript check, targeted frontend lint, backend Ruff `F` and whitespace checks passed. The production frontend build passed: compilation 3.3 minutes, its TypeScript stage 92 seconds, and 70/70 static pages generated. The existing Cache-Control configuration warning is retained in the log; it was not changed here.

Controls cover source-only output despite contradictory derivatives, unenriched content, missing coverage/provenance, weak versus strong passages, injection/budget/empty-source withholding, no-match, invalid API shapes, wrong-query responses, current request errors, and stale query/campaign/mutation results. The actual source-admission helper is used rather than a substitute implementation. Existing permission and ingestion/enrichment controls are included in the affected backend run.

The owner run is on `c01175c15679d881f591c7c5d0a5e17d5af4c465` plus this six-file source/test change. It predates the separately owned authored-source coverage and obligation-preservation integrations. Those require root's combined checks; this preview repair does not claim their results. Source/evidence commit binding and LF hashes are in the manifest; committing is not a rerun.

## Limits

All provider/model and database behavior in these checks was synthetic. No live AI, telephony or connector provider, database mutation, browser acceptance, call, push or deployment was performed. The frontend build is an installed-dependency check; Google font assets used by the existing application are separate from provider inference. Python uses the existing venv and the frontend uses the existing local node_modules via a junction. Recorded runtime versions/locations establish provenance, not exact-requirements or lockfile parity.

AG02's raw-source quality gate remains open and was not rerun or tuned here. Displaying a related passage cannot establish semantic correctness. Broader business acceptance and the feature freeze remain unchanged.

Evidence: [manifest](artifacts/ag02-diagnostic-evidence/manifest.json), [commands](artifacts/ag02-diagnostic-evidence/commands.json), [pre-fix reproduction](artifacts/ag02-diagnostic-evidence/baseline.txt), [backend regression](artifacts/ag02-diagnostic-evidence/unit-final.txt), [frontend checks](artifacts/ag02-diagnostic-evidence/frontend-focused.txt). Root and an independent agent reviewed the final bounded source/test change; the reviewer did not execute tests or access providers/databases.
