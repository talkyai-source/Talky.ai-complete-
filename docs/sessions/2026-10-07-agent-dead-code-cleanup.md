# Retired voice-agent code cleanup

Source `d69cc8b13a4c37e1be8b046d821fff3129f92c82` from `458c6798` removes retired parsing and speech-rewriting code after the model-driven flow replaced its application callers. It changes 15 application paths and 54 test/fixture paths, deleting 5 application files and 21 obsolete test modules.

## What remains

- `CallState`, contact/source/readback evidence models and their historical persistence shape remain. Actual `record_contact` syntax validation, caller revision checks and save acknowledgements are unchanged.
- Shared number normalization and contact formatting remain for action authorization and technical-disclosure exemptions. They no longer extract contacts or prescribe a dialogue.
- Live prompt state contains runtime identity, confirmed contact snapshots and actual tool outcomes. Formatting, control-artifact/audio-tag cleanup and privacy cleanup remain.
- Supplied URL host extraction remains for streaming sentence segmentation. Durable opt-out, action authorization and receipt ownership are outside this deletion and unchanged here.

## Removed

The five deleted application modules are `confirm_llm`, `conversation_craft`, `conversation_guards`, `grounded_figures` and `readback_guard`. The contact modules lose transcript parsers, classification and scripted capture reducers. The spoken-email module loses contact extraction. `llm_guardrails` loses unused fallback tables/config, truncation and semantic response validation. Prompt modules lose no-op contact directives and compatibility-only price/reanchor/model-addendum exports. URL speech rewriting and inferred relationship/provider/interest/sales-stage state are removed.

Mixed tests preserve current model-tool, caller ownership/revision, persistence/revocation/serialization, action-capability, prompt-data, transport and formatting checks. Storage tests now construct explicit producer state instead of calling a deleted parser. Old-only classifier/speech-rewrite tests are retired rather than skipped or supported by compatibility stubs. The frozen historical qualification corpus is unchanged; its obsolete aggregate all-green assertion is retired. The actual NativeReplay transport fixture remains for current contact/revision tests; only its removed relationship-state projection is deleted. This does not establish semantic or acoustic quality.

## Verification and coordination

The initial 12-module run produced **261 passed, 9 failed, 62 warnings** in 16.82 seconds. All nine failures stopped at the same `LLMGuardrailsConfig` import in the separately owned service cleanup. They are preserved in [initial.txt](artifacts/agent-dead-code-cleanup/initial.txt), not described as passing. The socket/async transport guard recorded 245 internal loop pairs and **0 prohibited attempts**. The final source commit follows that run; only EOF whitespace was trimmed afterward in three test files.

Ruff F checks on changed surviving Python paths and `git diff --check` passed. The source/reference audit scanned application, scripts and test imports. Remaining removed-symbol imports are confined to coordinated service/transcript/dispatch/end-call edits and the knowledge-injection test owned by the other agents. This source snapshot therefore requires those integration dependencies and the root's combined regression before a complete verification claim.

Exact invocation/environment and runtime versions: [result.json](artifacts/agent-dead-code-cleanup/result.json). The existing shared Python venv was used; no fresh dependency-install parity is claimed. No providers, real database, browser, end-user audio or live model evaluation ran.
