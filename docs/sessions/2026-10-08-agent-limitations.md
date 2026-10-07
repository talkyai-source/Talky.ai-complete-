# Remaining agent limitations — local repairs and acceptance boundary

The user requested fixing the limitations remaining after the [7 October cleanup and residual repairs](2026-10-07-agent-residual-repairs.md). This checkpoint extends the existing AG02–AG05 and AG07 work; it adds no provider, service, schema, search engine or unrelated feature. The previous scripted contact and conversation machinery stays retired.

Base: `6c9eed991a7c69afd70536379427933f099052d9`. Final combined tested candidate: **`408481f1041a26ea53a55dea8d2ad63f313147cd`**, branch `codex/production-ready-20261004`, isolated worktree `tmp/production-ready-20261004`. Later checkpoint changes are documentation/evidence only. The original checkout's unrelated application changes are preserved; it receives committed documentation only. Nothing was pushed or deployed.

## What changed and why

| Observed limitation | Root cause or finding | Resulting behavior |
|---|---|---|
| Spoken client name/company had no dependable live saving path. | `record_contact` accepted only email/phone, and business-note extraction excluded identity. Existing lead-detail storage already supported text. | The model can set, confirm, correct and withdraw `full_name` and `company_name` through the same recording tool and existing persistence. The Captured Details panel displays the values, actual confirmation/withdrawal state and source evidence. No name parser or name splitting was introduced. |
| A restart could leave a saved summary with missing business notes; users could see an old analysis after a source revision. | Recovery from committed summary evidence already worked, but was unproved at this boundary. The summary endpoint lacked a final current-source check and completeness metadata. | Recovery of missing notes from a durable summary is verified without another model call, including after process-local state is cleared. The endpoint returns a summary only when it still matches the persisted summary and current source hash. It exposes transcript completeness and currentness. |
| An incomplete transcript or unavailable analysis could appear more trustworthy than its source. | Summary surfaces did not consistently expose saved-source state, and the list preview could reuse an old headline. | Summary card and list preview show partial/failed/unknown-source warnings and suppress stale summaries. An explicit reload uses existing saved evidence. Existing independent processing and transcript-save statuses remain intact. |
| A large catalog or authored section could not be read within the old flat navigation/source bounds. | Unrelated sections consumed navigation rounds, and oversized sections had only an unavailability result. A draft branch renderer also repeatedly scanned the whole catalog. | The existing tool supports direct branch browsing and contiguous, Unicode-safe source pages. Full selected context is validated before any page is returned. A per-render source/path index avoids repeated full scans. No search/ranking fallback was added. |
| A single oversized newest exchange could still be sent to a provider and fail there. | Whole-exchange trimming intentionally kept current caller words even when the request exceeded the existing estimate. | Traditional preflight detects this condition before dispatch and asks for one narrower question. An oversized setup reports unavailable. Canonical history and caller quotes are preserved. |
| Released Gemini providers left their network clients open. | Cleanup only discarded the client reference. | Cleanup closes both SDK-owned asynchronous and synchronous transports, is safe to repeat, and attempts synchronous closure even if asynchronous closure fails. |

Name and company each have one current entry. Additional entries remain supported only for email/phone. Manual edits retain precedence; confirmations must correspond to the selected value and current caller-source revision, and success requires that field's persistence acknowledgement. These are storage/ownership controls, not a backend interpretation of the caller's meaning. Existing imported/generated scalar lead display names remain independent; this change does not copy or split names into those fields.

The traditional and separate native guide paragraphs describe the same identity tool. Governed prompt versions are `lead_gen@15`, `customer_support@13`, `receptionist@13` and `realtime@10`. The knowledge guide was shortened when the combined regression exposed its budget overrun; the existing 9,500-character test budget was preserved, with the fixture now at 9,326 characters.

Knowledge bounds are explicit: at most three selected sections, 48,000 UTF-8 bytes of validated selected context, and 12,000 UTF-8 bytes of source text per fragment, plus metadata and JSON framing. Every page is marked incomplete, including the final page; fragments are never recorded as complete verified grounding. The guide tells the model to read all contiguous parts with the same digest, including applicable conditions. In traditional live turns, advancing navigation may use the existing bounded credits, with at most seven tool decisions and one tool-less final response. Dashboard and native execution retain their separate loop controls. Deep or broad sources can still exceed the applicable bounds. The unchanged chars/4 context estimate is not a provider tokenizer or an unlimited-memory promise.

Owner details and bounded reproductions:

- [Durable summary recovery and source-aware UI](2026-10-07-agent-capture-recovery.md).
- [Model-driven name/company storage and display](2026-10-07-model-identity-capture.md).
- [Knowledge branches, complete-context validation, source pages and context preflight](2026-10-07-knowledge-context-limits.md).

## Combined verification

| Check | Result and scope |
|---|---|
| Final backend affected regression | **2,015 passed**, 124 modules, 1,780 warnings, zero failures/skips/deselection; 71.26 seconds reported by pytest. Source hashes unchanged; 0 prohibited transports and 2,243 internal event-loop socketpairs. This is an affected selection, not the whole backend suite. |
| Actual disposable PostgreSQL | **41 passed**, 41 warnings, 74.99 seconds across existing lead evidence, new model identity, and restart recovery modules. Actual storage, restricted-role/tenant behavior and API projection use synthetic callers/tool arguments. All 108 public table contents, schema, migration head, roles and schema names matched before/after. |
| Dashboard DOM tests | **50 passed**, 0 failed/skipped, 16.49 seconds across summary card, actual call-list preview and Captured Details. These use rendered React components, not a deployed browser journey. |
| Dashboard types/lint | TypeScript and scoped ESLint passed for the changed frontend paths. No production build or browser E2E is claimed for this checkpoint. |
| Static checks | Full Ruff `F` passed on all 37 changed surviving backend Python files; backend/frontend diff check passed. |
| Current evaluation preparation | All **80 original gold/holdout questions** produced current-agent dry requests; no case is semantically approved. Twenty evaluator controls pass within the combined run. |

The [combined manifest](artifacts/agent-limitations/verification.json), [output](artifacts/agent-limitations/affected.txt), [static/source record](artifacts/agent-limitations/checks.json), [PostgreSQL manifest](artifacts/agent-limitations/postgres-first.json), [PostgreSQL output](artifacts/agent-limitations/postgres-first.txt), and [dashboard output](artifacts/agent-limitations/frontend-final.txt) preserve exact commands/results and limitations. The existing Python 3.12 and installed frontend dependencies were used; this is not a clean-install/exact-lock qualification. Counts from separate owner/focused runs overlap and are not additive.

The SQL run used only the prechecked disposable `127.0.0.1:55434/cp04_acceptance_test`, migration `0061_dnc_runtime_contract`, unique synthetic fixtures and restricted roles, under the existing exact-destination transport/DNS guard. It recorded 89 designated database transports, 84 internal socketpairs and zero prohibited attempts. Its source manifest belongs to candidate `3d1a7f4f` with the recorded evaluator draft. Subsequent source changes affect guide wording, evaluator, Gemini cleanup, frontend and tests. Final hashes confirm the tested persistence paths and all three SQL modules are unchanged; a later whole-database run is not claimed.

Preserved failures explain why checks changed:

- [First combined run](artifacts/agent-limitations/combined-first.txt): 2,003 passed / 10 failed. One actual guide-budget defect was fixed by shortening the guide. Old branch/page expectations and privacy fixtures were migrated to the current model-tool contract while preserving exact source/revision, redaction, save and incomplete-grounding assertions.
- [Second combined run](artifacts/agent-limitations/combined-second.txt): 2,014 passed / 1 failed. The remaining security assertion referenced the guide's old sentence. It now checks the equivalent current instruction, “Headings are navigation, not answers.” The independent source-as-data assertion and poisoned-source controls remain unchanged.
- [First dashboard run](artifacts/agent-limitations/frontend-focused.txt): 32 passed / 4 failed. Four new assertions expected a headline the summary card never renders. Corrected tests assert the actual body and retain source-state warnings; stale negatives now check that body as well. The original shell wrapper masked the Node exit, so the recorded test output is authoritative. Final wrappers explicitly propagate the actual exit status.
- [Existing SQL baseline](artifacts/agent-limitations/leads-postgres-baseline.txt): 26 passed / 4 failed. Native fixtures still assumed retired implicit parsers. They now call the real model-owned recording boundary and retain durable revision/failure/tenant checks.
- [Gemini cleanup baseline](artifacts/agent-limitations/gemini-cleanup-baseline.txt): both new lifecycle controls failed before repair. [Focused provider/evaluator verification](artifacts/agent-limitations/gemini-cleanup-focused.txt) passed 61 tests after repair, with zero prohibited transports.
- Initial evaluator failures and intermediate outputs remain adjacent as `qualification-*.txt`. Missing synthetic source identity and a mock stream-capture mismatch were fixed; unchanged gold/holdout questions and review labels remain separate from model input.

Independent read-only reviews covered summary/store behavior, identity capture, branch indexing and bounds, context handling, evaluation transport/evidence, privacy contract migration, real preview wiring, and Gemini cleanup. `/root/account_review` confirmed against installed Google SDK source that asynchronous and synchronous closes release distinct owned transports. Review is not counted as an additional test run.

## Actual-model evaluation prepared, not executed

The retired keyword/query evaluator is not restored. `backend/scripts/qualify_agent_sections.py` prepares or executes the existing traditional `VoicePipelineService`/`TurnStreamer`, current composed guide and actual source-tool adapters against the unchanged 60 gold questions plus 20 holdouts. Synthetic STT/TTS/media ports isolate source-answer evaluation; this cohort does not test contact extraction, native audio, databases or connector effects.

The [80-case dry plan](artifacts/agent-limitations/section-dry-plan.json) is bound to the final tested source and fixture hashes. `gemini / gemini-3.8-flash` is an existing registry profile used only to prepare an example; it is not a user-designated or remotely verified campaign/model. Dry mode uses no provider credential and sends no provider request. The stored “Prepared only.” responses are placeholders, not test answers or scores.

Live execution requires an explicit existing provider/model, `--execute-provider`, and the name of the designated credential environment variable. There is no ambient credential fallback, dotenv load, warmup or model-discovery call. The evaluator permits only the exact provider HTTPS endpoint and model, with redirects/proxies disabled. It records wire request/response evidence without authorization headers, effective temperature/output limits, source and fixture hashes, SDK versions and errors by type rather than potentially sensitive exception text.

Default ceilings are 160 HTTP attempts (retries count), 204,800 reserved output tokens, 256 KiB per request, 16 MiB total request bytes, 35 seconds per case and 20 minutes per run. The response evidence capture is bounded. Raised CLI request/token ceilings remain bounded at 640 / 819,200. Requests and budgets are evidence ceilings, not cost or latency promises. Provider errors, incomplete evidence or budget exhaustion remain visible. Human semantic review stays pending and `production_approved` remains false even after execution.

From the worktree's backend, the dry command is:

```text
python -B -m scripts.qualify_agent_sections --provider gemini --model gemini-3.8-flash --output <new-output-file.json>
```

The script refuses to replace an existing output file. Task-specific normal/edge/failure cases and human review follow [OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices); provider function calls must be executed and their results returned as described by [Google's function-calling documentation](https://ai.google.dev/gemini-api/docs/function-calling). These references do not certify this application or an unexecuted profile.

## What remains and what cannot honestly be guaranteed

1. **Designated live profile/account and owned destinations.** The two in-session questions identifying campaign/model/credential source and test phone/inbox/calendar/CRM sandbox are still unanswered. The presence of an ambient credential is not designation. No provider, telephone, email, calendar or CRM operation was performed for this checkpoint.
2. **Actual comprehension and spoken behavior.** Execute and review the bounded source evaluation for the designated existing profile, then the existing conversation matrix for contact interpretation, corrections, uncertainty, interruptions, timing and call ending. A green tool test does not prove a model will use the tool correctly.
3. **Integrated deployed outcome.** Verify saved Leads/Sales Hub evidence and real connector receipts from owned end-to-end calls on a recorded release candidate, including the release/rollback and customer acceptance gates already in the plan. No deployment, live browser journey or paid-use acceptance is inferred from local tests.
4. **Finite capacity and lost unsaved input.** No model has unlimited context, and no backend can reconstruct words or a model command that never reached durable storage as if they were confirmed facts. Recovery here applies to committed evidence; incomplete source remains visibly incomplete. The final summary observation can also become old after a subsequent revision and must be refreshed.

The [production plan](../production%20ready.md) and [execution tracker](../production-readiness/execution-tracker.json) retain all open acceptance gates and the feature freeze. CP05, CP06 and CP09 remain user-deferred. These local repairs close the reproduced code defects and extend persistence evidence; they do not close the entire production-readiness goal or establish 100% answer accuracy.
