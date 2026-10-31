# Agent remnants cleanup — 7 October 2026

**Follow-up:** The later [remaining agent repairs](2026-10-07-agent-residual-repairs.md) reproduce and fix six defects still present after this checkpoint: idle/numeric replies, active-caller closing, earlier-contact targeting, the fixed history cutoff and catalog navigation. The result below is historical cleanup evidence, not a claim that every agent defect or acceptance gap was closed.

Follow-up to the [model-owned conversation revision](2026-10-07-model-owned-conversation.md), at the user's request to clean up its remaining complexity and unused files. Implementation is on `codex/production-ready-20261004` in `tmp/production-ready-20261004`, from baseline `458c6798`. Unrelated changes in the original checkout are preserved. This is local implementation and regression evidence; no push, deployment or production-readiness closure is implied.

## Resulting behavior

- Ordinary identity misunderstandings, clarifications and responses go to the conversational model. The backend no longer generates fixed wrong-number replies, forces the corresponding farewell, or inserts a phantom-hangup recovery script.
- Sentence-count cutoffs are removed from configuration and streaming. Model responses reach speech subject to the existing token limits, interruption and delivery handling. AskAI receives its small existing product fact sheet consistently instead of a keyword-triggered insertion.
- Ordinary silence no longer triggers check-in phrases or a generated opening ladder. Configured agent-first greetings and disclosures remain. The silence deadline still disconnects an inactive call; after the agent finishes a long response the caller receives the configured quiet interval instead of inheriting time spent listening.
- The dashboard assistant's existing knowledge tool now lists sections and reads the model's exact selection, using the shared source reader. Each invocation checks authenticated tenant/campaign access and reads currently published source. Calls continue to use their existing call/admission snapshots. Stale section references fail explicitly and require a new catalog read.
- Old contact parsing, relationship/interest/provider inference, price/link/readback speech judges, contact-directive interruption code and compatibility stubs are removed. Unused filler generators and acknowledgement strippers are deleted; active accent guidance remains. The model interprets caller meaning and contact values; backend validation and saving remain.

No new provider, database, service, migration or customer feature was added. Exact source selection remains bounded by the existing catalog/context/tool-round limits; this cleanup does not claim perfect retrieval or model comprehension.

## Deletion and retained responsibilities

The [deletion inventory](artifacts/agent-cleanup/deletions.json) records every removed path relative to the baseline. **Eleven unused application modules, one superseded query-qualification runner and 32 obsolete-only test modules are deleted.** They include the unused conversation engine/intent detector, opening ladder/turn director, sentence-budget helpers, contact confirmation helper and retired conversation/readback/figure guards. Mixed test modules retain current source, saving, authorization, audio and persistence assertions. Historical gold data, qualification corpora and recorded failures are preserved.

Reduced shared files remain where they still serve real callers: contact/source/readback data models and storage compatibility, number/email formatting for validation and authorization, runtime identity and confirmed-contact facts, technical/privacy cleanup, URL segmentation, and the scoped source loader/manual knowledge diagnostics. None of those is evidence that the removed parser still governs dialogue.

The backend still owns tenant permissions, source revisions, contact save acknowledgements, consequential-action authorization and receipts, opt-out persistence, caller turn ordering, interruption and actual audio delivery. STT/provider failures, empty native responses and failed opt-out writes still have operational recovery paths. This is not a claim that every fixed line or every backend check has been deleted.

## Verification and limits

Final candidate **`afb2eaf6ce978fa47f91059526ed5baa4d5eb15f` passed 1,554 tests across 101 modules**, with 1,243 warnings, no skips/deselection, unchanged source hashes and **zero prohibited network attempts** (17.39 seconds reported by pytest). The [combined verification manifest](artifacts/agent-cleanup/verification.json) records the exact command, selected modules, source hashes and result. The selection covers all surviving affected unit/security modules plus the prior model-conversation regression and important contact/native/action paths. Full Ruff `F` checks pass on all 93 changed surviving Python files. The final [static checks](artifacts/agent-cleanup/checks.json) parse 1,381 Python files without errors or direct imports of deleted modules. Source/report diff checks pass; original diagnostic whitespace in captured raw logs is preserved and excluded from that whitespace check.

The preceding combined run passed 1,553 tests across 100 modules. The final run additionally covers the dashboard graph import after removing 19 unused imports. These runs overlap and are not additive. Warnings and the existing dependency environment are retained in the evidence; this is not warning-free or exact-lock qualification.

The first integrated run had **1,482 passed and 40 failed**. The failures exposed stale contracts in mixed tests: literal script wording, stripped acknowledgements, a generated filler, inferred relationship fields, query/matched source statuses, transcript-keyword tool filtering, and the pre-contact-tool schema. One historical replay comparator also raised on a removed relationship field instead of reporting unsupported evidence as a failed control. These are retained in [initial output](artifacts/agent-cleanup/initial.txt) and [initial inputs](artifacts/agent-cleanup/initial-verification.json). Tests are migrated to current behavior while keeping actual authorization, effects, delivery and source assertions. Old semantic approval is not recreated by changing the gold corpus.

Independent reviews checked dialogue ordering and the silence timer, retained contact/persistence/native APIs, full-response streaming and delivery history, exact source permissions, and callers of deleted modules. Review caught a prompt encoding defect, fixed before final integration. Root review identified and repaired the long-answer silence-timer issue. An import audit parsed 1,381 application/script/test Python files and found no direct references to deleted modules or removed exports at that checkpoint.

Owner results are supporting evidence and overlap the integrated selection; do not add their counts:

- [Dialogue cleanup](2026-10-07-agent-dialogue-cleanup.md): scripted identity/silence removal, retained DNC/end/audio boundaries and idle-timer regression.
- [Dead code cleanup](2026-10-07-agent-dead-code-cleanup.md): parsers, speech guards and retained storage/format contracts.
- [Retained test migration](2026-10-07-agent-dead-code-followup.md): exact-section source/fence/history controls and current capability/receipt contracts; original corpora remain unchanged.
- [Knowledge cleanup](2026-10-07-agent-knowledge-cleanup.md): dashboard exact-source tools, permissions and retired query runner.

These tests use synthetic collaborators and the existing Python environment. They do not prove exact dependency-lock parity, whole-suite compatibility, actual model answers, acoustic quality, browser journeys, live connector effects or customer acceptance. No live provider/database qualification ran for this cleanup. The production-readiness feature freeze and outstanding AG02–AG05 acceptance remain in force.
