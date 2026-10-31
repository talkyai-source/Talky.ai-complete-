# 2026-09-07 — Knowledge retrieval + the two-model (Cerebras 120B / Groq 20B) pipeline

Owner ask: "check why knowledge retrieval is not working in the voice pipeline,
make sure only gpt-oss-120b (Cerebras) and gpt-oss-20b (Groq) are live, and make
the coding best for those two models."

## What the evidence said (prod, read-only, 7 days)

| Fact | Evidence |
|---|---|
| Retrieval itself runs and is fast | 24 `KB_DEBUG` lookups, every one `HITS=3` in 19–52 ms; hybrid FTS + trigram SQL in `knowledge/retrieval.py` |
| Both target models use the same inject path | `knowledge_tools_for` returns None for Cerebras and for Groq `openai/gpt-oss-*` → `_knowledge_block_for_turn` |
| **The answer was cut off before the model saw it** | Dojo node `"What are your rates?"` is 1,501 chars; first figure at char 654; `_KB_CHUNK_CHARS=350` source-first trim delivered only the preamble ("CRITICAL RULE: never quote a rate…") plus an ellipsis. The node's 133-char `voice_answer` (the actual answer) was appended only "if there is room" — i.e. never on long nodes. |
| **Cerebras paid for reasoning out of the answer budget** | Live probe on the prod key: `max_completion_tokens=3` → `finish_reason=length`, `text=None`; `3+1024` → `"yes"` (25 reasoning tokens + answer). Groq has reserved +1024 since the GPT-OSS rollout; Cerebras did not. Tenants run `llm_max_tokens=90`. |
| Empty completions never failed over | `resilient_llm._attempt` treated a zero-token stream as "nothing to say"; 2 `zero_token_turn` recovery lines in the retained journal |
| Failover was one-way | `LLM_SECONDARY_*` = Groq 20B; a tenant choosing Groq 20B as primary got "same as primary — skipping" → no fallback |
| Cache key inconsistent | `llm_response.py` passed `campaign_id`; the two main `turn_streamer` calls did not |
| Models live | `tenant_ai_configs`: 8× cerebras/gpt-oss-120b, **3× groq/qwen3.6-27b**, env secondary groq/gpt-oss-20b; AI Options menu already offers only the two |

Also seen, not changed: single-word queries ("yes", "bye") match via the OR-tsquery/trigram fallback and inject the most-hit node as "official knowledge". Noise, not a miss; left for a relevance floor later.

## Changes (all on `main`)

| File | Change |
|---|---|
| `voice_pipeline/kb_budget.py` | `_KB_CHUNK_CHARS` 350→600, `_KB_TOTAL_CHARS` 1500→2000; new `fit_kb_body(rendered, node, limit)`: trims the source but **always appends the node's `voice_answer`** after the ellipsis, inside the budget |
| `voice_pipeline/turn_streamer.py`, `knowledge_tool.py` | both delivery paths use `fit_kb_body` (shared renderer kept); both LLM calls pass `campaign_id` (Cerebras `prompt_cache_key`) |
| `infrastructure/llm/cerebras.py` | `_THINKING_RESERVE_TOKENS` (env `CEREBRAS_THINKING_RESERVE_TOKENS`, 1024) added to `max_completion_tokens` whenever reasoning cannot be off — fixes the 3-token confirmation probe and 90-token turns |
| `domain/services/resilient_llm.py` | zero-token primary completion → `_FirstTokenMiss` → secondary takes the turn (nothing spoken yet); secondary empty → clean end (recovery line) |
| `domain/services/voice_orchestrator.py` | `_pick_secondary_llm` + `_LLM_PAIR_FALLBACK`: Cerebras 120B ↔ Groq 20B back each other up when env names the primary itself; `cerebras` default secondary model added |
| tests | `test_two_model_pipeline.py` (11), resilient timeout tests re-stated for the new contract, two turn-streamer fakes accept `**kwargs` |
| data | `tenant_ai_configs`: 3 qwen tenants → cerebras/gpt-oss-120b (backup table `tenant_ai_configs_backup_20260907`) |

Not changed (documented in the 09-06 audit): F03 mid-stream stall = EOF, F04 Groq SDK retries, F05 client close, F07 TTS fallback contracts, F08 tool parity, F09 `stt_language`.

## Verification

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8856 passed, 7 skipped in 598.80s (0:09:58)

ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

live Cerebras probe (prod key, gpt-oss-120b, reasoning_effort=low):
cap=3     finish=length  text=None   completion_tokens=3  reasoning_tokens=0
cap=1027  finish=stop    text='yes'  completion_tokens=36 reasoning_tokens=25
cap=90    finish=stop    text='yes'  completion_tokens=36 reasoning_tokens=25
```

Three test doubles (`_FakeLLMProvider` ×2, `_CapturingLLM`) encoded the old
call shape without `**kwargs`; they now accept keyword arguments like the real
providers do. No production test was loosened.

## Second pass (same day) — best-practice fixes + dead model code

| Audit item | Change |
|---|---|
| F03 stall = EOF | `LLMStreamStalled(LLMTimeoutError)` in `llm/groq.py`; both timeout wrappers (Groq, Cerebras) raise it when the stream stalls or the wait budget expires AFTER tokens were yielded. The turn streamer's existing timeout handler then drops the unfinished tail (or, if nothing was spoken yet, replaces the fragment with the repeat-request line) instead of voicing "Your appointment is" as a complete answer. |
| F04 nested retries | `AsyncGroq(..., max_retries=0)` — the provider's own attempt loop + breaker + first-token deadline are the single retry owner, as Cerebras already was. |
| F05 client lifetime | `cleanup()` on both providers now awaits the SDK client's `close()` (all key-bound Groq clients), then drops references. |
| F07 TTS fallback contracts | `ResilientTTSProvider` normalises the secondary's PCM to the primary's declared format (float32 ↔ int16) so the gateway's once-per-session `tts_source_format` stays true, and warns when a cross-vendor fallback runs with no voice mapping. |
| F09 STT language | `VoiceSessionConfig.stt_language` ← `AIProviderConfig.stt_language`; `CallSession.stt_language`; `audio_ingest` passes `language=` on every stream; a non-English language forces the Nova-3 primary (Flux is English-only) in both the session builder and the orchestrator. |
| Dead model code | Groq Qwen-only request branch (`_is_qwen3_model`, top_p 0.8, `reasoning_format=hidden`) removed; `GroqModel.QWEN_3_6_27B` and the qwen/llama entries of `GROQ_MODELS_HIDDEN` removed (all tenant rows migrated, backup `tenant_ai_configs_backup_20260907`); `LLMTestRequest` default → gpt-oss-20b; AI Options latency advice no longer recommends the 404'ing llama; unused `dental_workflow.py` (hardcoded llama-3.1-8b-instant, zero importers) deleted. |

Kept on purpose: Gemini provider module and prompt addenda (inert — not offered, not defaulted), the realtime add-on pipeline, F08 Cerebras tool parity (action executors other than end_call are stubs; adding tool orchestration to the primary would change behaviour for no delivered capability).

Tests: `test_two_model_pipeline.py` grew to cover stall→incomplete on both providers, retry ownership, client close, language plumbing and PCM conversion; `test_groq_llm` / `test_groq_model_menu` / `test_ai_options` re-stated for the two-model contract (no assertion loosened — each now asserts the new invariant).

Second-pass verification (real output): `pytest tests/unit tests/security` → **8865 passed, 7 skipped in 641.04s**; Ruff F-gate → All checks passed!
