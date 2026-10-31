# AG02 enrichment response ownership and fail-soft repair

Base: `731759b1c5b53c29f45f56ef16d760341acf5d6f`. One application file (`enricher.py`) and one new focused test module. Final source commit and exact LF hashes are recorded in manifest.json. This is a local ingestion boundary repair, not retrieval-quality or customer acceptance.

## Reproduced root cause

The batch and single-retry decoders previously accepted any integer index within the whole input list. They did not require membership in the current provider request. Python booleans also passed the integer check. A response for a later section could overwrite another section's already accepted metadata, and a response could enrich a short/empty section that was never sent.

The actual enrich_nodes -> ingestion _search_text -> retrieve_pinned_knowledge -> prepare_knowledge_evidence control demonstrates the effect: a response for Billing rewrites Support's keywords to orbital/guidance. Although the Support source body is unchanged and unrelated, its generated aliases give full query coverage and a matched evidence status. Both the normal batch and malformed-batch/single-retry paths reproduce this. Source-only factual rendering alone does not prevent incorrect derivative terms from granting evidence admission.

The old loop also applied each item immediately. A later malformed item could raise after an earlier poisoned item was already stored, leaving partial metadata even when all retries returned empty. Single-retry JSON decoding was inside the try, but decoded-object/item/list parsing was outside it; valid JSON with a malformed shape could escape the documented fail-soft behavior and fail ingestion. String/dict/numeric metadata could also be coerced into misleading text or individual keyword characters.

## Small repair

A private decoder validates the complete response into a temporary mapping before any output mutation. It requires a JSON object with a node list, dictionary items, unique exact-int indices belonging to that request, string text fields, and lists of strings for aliases/questions. Booleans, duplicates, wrong-request indices and malformed values reject the response. Missing or null optional derivatives still mean empty values. Existing text/list length and count caps are unchanged.

Both batch and single-retry paths call this decoder inside their existing failure handlers. A malformed batch retains its existing bounded per-node retry; malformed single responses leave raw-source fallback. Valid out-of-order responses preserve original positions. The old provider/model/prompt configuration, timeouts, retry counts, original document content, ingestion transaction behavior and readiness state semantics were not rewritten. No backfill or automatic reenrichment occurs.

## Evidence

- Original source with the new final 48-control module: **36 failed / 12 passed**, preserved in baseline.txt. These are variants of routing, atomicity, malformed-shape and coercion defects, not 36 independent production bugs or 36 actual bad calls.
- Final affected run: **111 passed / 0 failed / 0 skipped / 0 warnings**, seven modules in 3.74 seconds, including all 48 new controls. Existing enrichment model/bounds, staged ingestion, reenrichment script, relevance, evidence budget and gold-evaluator controls are included.
- Ruff F-rule and diff check passed. No tests were rerun merely to repeat passing results.
- New controls call the actual enrichment function with synthetic AsyncGroq completion responses. They assert request indices, unchanged original content, accepted metadata ownership, recovery, field caps and actual downstream source indexing/evidence behavior. Socket calls are denied during those controls. No real Groq, PostgreSQL or other service was called.

Exact executed argv, interpreter, cwd and explicit environment are in commands.json. record_evidence.py freezes the two changed source/test hashes plus 13 unchanged directly relevant source/test/fixture hashes, then verifies frozen bytes against source Git bytes when recording manifest.json. Artifact hashes normalize CRLF to LF only. Existing Python overlays are documented; no dependency was installed or changed. Root and independent account_review source reviews are clear; the independent reviewer executed no tests or edits. The frozen application and test did not change after the final passing run or reviews.

## Qualification remains separate

This repair does not add synonyms, stemming, embeddings, relaxed coverage thresholds, question aliases or gold edits. It does not establish that correctly routed model-generated aliases are semantically approved. A content owner still needs to review the representative corpus and acceptable answers, including conflicting products, exclusions and unsupported claims.

Preserved raw-source results remain pinned 42/45 expected-source recall and 32/45 sufficient passages; SQL 40/45 and 31/45. Pinned misses are Q06/Q18/Q21; the ten found-but-weak pinned cases retain their declared source fragments, so their historical failures are principally lexical recall/coverage rather than lost passage text. The two rejected normalization proposals remain rejected because unrelated source passages newly became answerable. The existing gold integrity test still confirms a failed quality gate.

Content coverage, lexical recall, evidence admission and output fidelity are different checks: a ready upload or valid enrichment response cannot approve facts; retrieving a related source cannot prove answerability; source-only evidence does not prove faithful model output. No live model answer, submitted speech, human content approval, browser/call acceptance or readiness-gate promotion is claimed. Root's separate passage/ranking investigation is outside this owner patch.
