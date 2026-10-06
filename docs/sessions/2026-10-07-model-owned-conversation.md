# Model-owned conversation revision — 7 October 2026

The user explicitly requested a simpler guide, AI-selected knowledge, and removal of scripted contact and opening behavior. This revision changes the existing agent implementation within AG02–AG05. It adds no provider, vector database, service, or unrelated product feature. The production-readiness freeze remains active.

## What changed

| Area | Current behavior |
|---|---|
| Prompt | One concise conversation guide plus configured identity, campaign guidance, current runtime facts and available tools. No staged contact script, compulsory “before anything else” caller-message suffix, or model-specific spelling reminder. Native Realtime keeps its separate prompt module. |
| Knowledge | The conversational LLM selects exact section IDs from a scoped catalog. The backend reads those authored sections and their real ancestors. There is no keyword ranking, lexical threshold, query rewrite, or separate recovery LLM in the live retrieval path. |
| Contact capture | The same model interprets email/phone spelling, corrections and confirmations and calls `record_contact`. The backend validates the value, caller source/revision and current candidate, then uses existing lead persistence. Results distinguish saved, unsaved, pending and confirmed. No parser mandates how the agent must ask or read it back. |
| Conversation wording | Removed the runtime semantic judge that rewrote prices, links, relationship claims, phone readbacks, callback promises, repeated questions and closing phrases. Ordinary qualifiers such as “excluding VAT” survive cleanup. Prompt guidance and model evaluation now own those language outcomes. |
| Runtime context | Identity delivery, contact state and actual tool outcomes remain. Regex-inferred interest, provider, customer relationship, requested action and sales-stage labels no longer enter the live system prompt as facts. The model reads the original conversation. |
| Opening and pacing | Caller-first input goes directly to the conversational model; the bare-hello prebuilt opener detour and injected thinking filler are removed. Operator-configured agent-first greetings and disclosures remain. |
| Tool selection | The model sees connected capabilities without a keyword intent filter. Traditional tool dialogue permits up to three decision rounds and then a final answer; the shared default for other callers remains one. Repeated write requests reuse results. Read-only section requests rerun against the in-memory snapshot so current evidence matches their results. |
| Knowledge panel | Existing active modes display AI-selected sources. Manual source-search results, priority pins and historical hit counts are clearly described as diagnostics, not live AI retrieval or generated-answer tests. No API or saved-setting format changes. |

The backend still owns tenant/campaign access, validated arguments, privacy cleanup, source revisions, durable saving, DNC, consequential-action authorization, execution receipts and audio/turn ownership. Removing conversational heuristics does not authorize arbitrary backend actions.

## Knowledge and persistence boundaries

- Outbound/browser knowledge now uses a published-source snapshot prepared at call setup (`call_snapshot`). Existing inbound calls retain their admission snapshot (`admission_snapshot`). Mid-call edits are visible on subsequent calls, not through an automatic per-turn refresh.
- Catalog headings guide navigation; they are not factual evidence. `available` means that exact source text was read, not that it answers the original question. Missing, conflicting, malformed or oversized source context remains explicit; qualifying conditions are not silently truncated.
- Catalog pages are bounded at 8,000 content characters, selected source context at 12,000, with up to three selected sections. Large catalogs, poorly structured documents and questions requiring many sections still need representative evaluation. This is model-directed source navigation, not a claim of perfect document reasoning.
- Saved contact evidence uses existing storage/display contracts. Confirmation is explicitly attributed to model interpretation of a later caller turn; it is not a guarantee that speech recognition or interpretation was correct. Test calls retain their existing persistence exclusions. Newly spoken name/company extraction was not added; post-call business notes and imported lead fields retain their existing behavior.
- Consequential-action executors still check their existing caller confirmation and permission conditions. Model phrasing is no longer blocked by an output regex. A model could speak a premature or unsupported claim; only actual execution receipts establish backend completion. This tradeoff is deliberate and must be judged in real-model acceptance.
- Traditional natural preambles can stream before a tool completes. Realtime retains its existing matching audio/transcript buffer and transport rules; this revision does not claim native streaming-latency repair.

## Verification and review

Three parallel implementation/review owners covered prompts, contacts/native behavior, and knowledge/session setup. Integration review found and fixed two substantive issues: the Realtime persistence pool had been removed with old retrieval setup, and repeated cached reads could leave diagnostic evidence pointing at a catalog page. Gemini's independent tool loop also required explicit multi-round support; shared-loop changes alone did not cover that provider.

A final actual-path contact review reproduced another integration defect: echo removal shortened the model's caller message but the source hash belonged to the canonical saved text. Contact tools now bind text and source from one canonical transcript projection, while the model still sees the cleaned message. Exact hash/revision checks remain. [Reproduction and repair](2026-10-07-contact-echo-binding.md) cover both plain and privacy-sanitized echo-plus-email turns.

Migrating the remaining contact tests exposed one compatibility regression: changing an email could manufacture phone-capture evidence from an untouched legacy scalar. Contact updates and source revisions now preserve the other field's original capture object. This was reproduced with an emulated legacy shape; no observed live incident is claimed.

Owner evidence is retained separately and is not added together as an integrated test count:

- [Conversation guide and prompt sizes](2026-10-07-agent-conversation-guide.md): representative composed bases fell from 1,931–2,467 words to 641–679 in traditional slot prompts; native 970 to 511 before the additional runtime-fact reduction. These are not token, latency or accuracy measurements.
- [Model contact recording](2026-10-07-model-contact-recording.md): caller ownership/revision, pending/confirmed states, persistence acknowledgement and native controls.
- [Exact source sections](2026-10-07-ag02-model-sections.md): scoped source loading, full authored ancestors, before-connect catalogs and pool propagation.
- [Gemini section browsing](2026-10-07-ag02-gemini-sections.md): bounded native tool continuation, signed parts, write deduplication and read refresh.
- [Test-contract migration](2026-10-07-contact-test-contract-migration.md): removed assertions of the intentionally retired scripted workflow; retained persistence/transport regressions.
- [Remaining prompt contracts](2026-10-07-model-owned-test-contracts.md), [knowledge contracts](2026-10-07-ag02-knowledge-test-contracts.md) and [contact/transport contracts](2026-10-07-contact-contract-test-migration.md): known affected suites now exercise the current design. Obsolete exact scripts and query-runner behavior are retired explicitly; useful data, saving, source and transport checks remain.

Final integrated verification: **1,072 passed, 472 warnings across 58 backend modules**, with unchanged source hashes during execution. Scoped CI-rule Ruff (`F`, excluding existing `F401/F841`) and diff checks pass. The exact command, tested head and inputs are in `artifacts/model-conversation-integration/verification.json`. The first combined run was 613 passed and one obsolete native semantic-judge expectation failed; that assertion was migrated to the explicitly chosen model-owned speech contract. The pre-echo repair checkpoint passed 761 tests; the post-echo, pre-contract-migration checkpoint passed 795. Both are retained separately, not added to the final count. A preliminary focused run also corrected two fixture mistakes and the old Gemini query argument fixture. No live model, telephone, browser, deployment or production-database acceptance was performed for this revision. Tests use the existing Python environment, without an exact-requirements reproducibility claim.

A whole-unit collection attempt produced no result and was terminated; it is not a full-suite pass. The known affected prompt, retrieval, contact and relationship suites are migrated and included above. Compatibility outside this focused selection remains unverified. Existing standalone legacy parser/search diagnostics do not demonstrate live enforcement.

The existing knowledge-panel component module passed **11 tests**, and scoped ESLint passed. This verifies copy and component behavior, not browser or live retrieval quality; commands and source hashes are in `artifacts/model-conversation-integration/frontend-verification.json`.

## Qualification still required

The earlier 60-question lexical scores and 80-question query-rewrite runner describe the superseded architecture. Gold questions and thresholds remain unchanged, but those results do not measure model-selected sections. The old runner's CLI now explicitly rejects execution as superseded; the prior credential/budget proposal cannot silently authorize a different profile.

Next acceptance must exercise actual models choosing sections and interpreting caller corrections on the original questions, followed by the configured campaign profile, real voice timing, interruptions, contact persistence/display and connector outcomes. Keep AG02, AG04 and AG05 open. AG03's old deterministic relationship-guard candidate is superseded and returns to in-progress qualification. No claim of 100% retrieval, hallucination-free speech, customer acceptance or production readiness is made. No push or deployment was performed.
