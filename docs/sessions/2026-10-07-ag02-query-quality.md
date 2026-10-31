# AG02 query quality and evidence lifecycle — 7 October 2026

The local knowledge-tool repairs are committed and verified. **The retrieval-quality gap is not closed.** The unchanged raw-source matrix still has 42/45 expected-source recall and 32/45 sufficient passages (93.33% and 71.11%, against 90% and 85%). All 60 case results match the preserved baseline. The earlier SQL result remains 40/45 and 31/45; SQL was not rerun for these prompt and in-memory lifecycle changes.

## Root cause and changes

In `retrieve`/`map_retrieve` mode, default injection searches the caller's wording. Several failures require semantic reformulation: for example, “money back” versus “refund.” The existing conversational model can author a search through `lookup_company_knowledge`, but only under the existing `VOICE_KB_MODE=tool` setting. A high lexical score for a rewritten question can still support the wrong original intent. Automatic recovery has therefore not been enabled on the default path, and no new model, vector database, service, synonym catalogue or lower threshold was introduced.

The traditional tool instructions now require a current-turn lookup for concrete company facts rather than allowing the model's own confidence or an earlier assistant answer to substitute for evidence. Reformulation must preserve products, locations, timing, negation and relationships; uncertain audio or ambiguous references require clarification. Factual confirmations still require lookup; contact confirmations do not. Returned sources must address the original question and its conditions. Native Realtime has equivalent concise rules in its separate prompt and tool schema, with version `realtime@8` and the existing prompt hash mechanism. These are verified instruction/serialization changes, not proof that a real model complies. See the [native evidence](2026-10-07-ag02-native-query-intent.md) and [official Realtime prompting guidance](https://developers.openai.com/api/docs/guides/voice-prompting).

Actual call-path tests reproduced and repaired three evidence problems:

- A tool-mode turn that made no lookup retained the previous turn's facts. Each turn now starts without inherited knowledge authorization.
- Weak/unavailable results were classified as successful because their returned text differed from the no-facts sentinel. Success now requires the shared `matched` status; other statuses remain distinct.
- Knowledge-only decision prose could reach speech before lookup completion. The existing strict buffering now applies to knowledge tools too. Successive lookups also replace their local grounding, so an earlier successful lookup cannot survive a later weak, missing or different result.

The [lifecycle evidence](2026-10-07-ag02-tool-evidence-lifecycle.md) retains baseline failures and 22 new controls. Existing action-tool behavior is preserved. Buffering may increase time before speech on tool turns; actual voice latency has not been measured. Related but incorrect products can still coexist in current retrieved evidence, so lifecycle correctness does not certify semantic relevance.

## Integrated verification

Candidate `c0f0429895f87dcf4ec7d6b1e75c2dda42740af4` passed **534 tests across 22 modules**, with 186 warnings, zero skips, in 28.95 seconds. All 37 recorded inputs matched committed Git source before execution and remained unchanged afterward. The offline guard recorded no prohibited socket or asynchronous transport attempts; 600 internal socketpairs were allowed. The existing Python environment was used; this is not a new exact-requirements or full-backend qualification. No database, provider, browser, audio or customer acceptance was performed.

The initial combined run retained 530 passes and one stale source-inspection assertion: it required an unused `render_node_answer` import. That test was replaced with actual inject/tool/native delivery checks against authored prices, conflicting generated prices and missing authored text. The price guard is checked in the actual tool result. No application, gold or threshold change was made to resolve that test failure. The earlier 76-test prompt run and both combined attempts are preserved in the [integration manifest](artifacts/ag02-query-integration/manifest.json). Counts overlap the owner runs and must not be added together.

## Concrete next step

The [bounded semantic runner](2026-10-07-ag02-semantic-qualification.md) and [integrated dry plan](artifacts/ag02-query-integration/provider-dry-plan.json) are ready. They use the existing Groq `openai/gpt-oss-20b` adapter and actual knowledge tool, with 60 unchanged gold questions and 20 predeclared synthetic controls. The model receives original questions, never expected answers, grading fragments or a hand-authored query table. Every rewritten query, source/version, serialized request and final answer is retained for review. Mechanical source scores cannot automatically award answer fidelity.

The proposed single run is bounded to 160 text requests including retries, 204,800 reserved completion tokens including reasoning, temperature 0.4, configured answer target 256 and effective request ceiling 1,280. The integrated dry plan records exact input hashes and Python/Groq/httpx versions. No provider request has been made. An asynchronous request asks the user to designate the existing Groq credential source; the process has no Groq key. The detected `.env.docker` and `backend/.env.local-testsprite` declarations were not used for execution, and keys were not printed.

After designation, execute this one bounded synthetic run and inspect every answer against the original question, particularly product identity, exclusions, timing, corrections and ambiguity. Do not infer completion from a rewritten-query match or silently increase the run budget. Qualification of the complete campaign prompt, approved customer facts, SQL path, native voice behavior and deployed timing remains separate. AG02, all unrun acceptance gates, deferred packages and the feature freeze retain their previous status. Nothing was pushed or deployed.
