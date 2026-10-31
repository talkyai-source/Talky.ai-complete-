# AG07: remove unused traditional generation and KB trimming paths

Base: `d8ecd6d0d183e304e21453857619f36330f836bd`. Work is isolated on
`codex/production-ready-ag07-20261005`. No provider calls, DB operations, deployment,
or customer data were used. This is the bounded A05 cleanup, not closure of every
architecture or production-readiness item in AG07.

The executable-source search found no runtime caller of
`VoicePipelineService.get_llm_response` or `generate_llm_response`; all callers were
tests. The real traditional path remains `TranscriptHandler / TurnEnder →
VoicePipelineService._run_turn → TurnRunner → TurnStreamer → TTS submission`.
Native Realtime keeps its separate session protocol. The live
`response_max_sentences_for_turn` helper remains in its existing module unchanged.

The deleted `fit_kb_body` and `_trim_kb_body` helpers also had no runtime callers.
Their old behavior could combine truncated source with generated phrasing. The
existing live `prepare_knowledge_evidence` path already selects complete source
passages and qualifications and remains unchanged. Configured budgets, timeouts,
provider choices, prompts, effect execution and audio behavior are unchanged.

`mechanical-ast.json` compares all remaining function ASTs in the five edited app
files against the base: all match. Only the four named functions/methods were
removed. Caller searches cover tracked Python, TypeScript, TSX, shell and PowerShell
source across the repository, not just the backend app. Historical audit notes are
retained as history rather than rewritten to hide the prior path.

## Tests and their limits

The old compatibility integration module had eight skips and three baseline
failures referring to an absent `prompt_manager`. It has been replaced with six
offline controls through actual `TurnRunner` and `TurnStreamer`: two-turn prepared
context and submitted history, explicit relationship denial, unavailable action
claim, incomplete stream, cancellation, and zero-output recovery. Provider outputs
and the TTS submission port are synthetic. Submission is not a playback receipt
or proof that a human heard audio. The action control proves no successful effect
receipt was invented; it does not exercise a real external provider.

Before app cleanup, these new controls gave **5 passed, 1 failed**. The exact new
failure is preserved in `live-path-before-cleanup.txt`: after “I am not your
customer,” typed state correctly says denied, but the candidate reply “As our
existing customer, your account is ready” passed the existing claim matcher. Parent
authorized a separate follow-up repair. In this mechanical commit the exact test
is a strict expected failure, not substituted with an easier phrase or counted as
passed.

Other migrated checks use the live streaming path for first-token tracking,
reasoning suppression and grounded pricing, and the actual evidence preparer for
qualification preservation and rejection of generated replacement facts. The old
compatibility formatter removed numbered list markers; the live streamer retains
inline numbering after normalization. That obsolete compatibility-only expectation
was removed; the meaningful grounding, no-reasoning and submitted-text checks stay.
No formatting behavior was changed. `scoped-after.txt` records that migration
mismatch before its test correction.

Final mechanical command from `backend` using the original backend venv:

```text
python -m pytest -q tests/unit/test_voice_pipeline_llm_response.py tests/unit/test_voice_pipeline_runtime.py tests/unit/test_live_structured_state.py tests/unit/test_kb_injection_budget.py tests/unit/test_two_model_pipeline.py tests/unit/test_voice_pipeline_service.py tests/integration/test_voice_pipeline_conversation.py tests/unit/test_kb_evidence_contract.py tests/unit/test_ag03_cascaded_relationship.py tests/unit/test_ag03_caller_dispatch_order.py tests/unit/test_ag03_relationship_acceptance.py tests/unit/test_ag04_traditional_qualification.py tests/unit/test_ag04_native_qualification.py --disable-warnings
```

Result: **223 passed, 1 xfailed, 1,454 warnings** in 10.75 seconds. The warnings are
reported rather than hidden from the result count. Exact CI Ruff command
`python -m ruff check app/ --select F --extend-ignore F401,F841` and `git diff --check`
passed. Source evidence is under `docs/sessions/artifacts/ag07/`.

This does not claim live semantic qualification, acoustic quality, restartable call
state, full AG07 closure, or removal of all architectural coupling. Reverting this
mechanical commit restores only the unused compatibility helpers/tests; there is
no migration, durable-state rollback, or provider setting change.
