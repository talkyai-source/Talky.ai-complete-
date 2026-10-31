# Talky.ai — STT / LLM / TTS pipeline inspection

Audit date: 2026-09-06 UTC; handoff crosses into 2026-09-07 in Pakistan.

## 1. Verdict

Groq `openai/gpt-oss-20b` and Cerebras `gpt-oss-120b` are implemented, selectable, and accessible to the production accounts. Both answered fresh synthetic requests. The actual production Cerebras provider also returned a spoken-text response through its SDK path.

That is not equivalent to a fully hardened voice pipeline. This inspection found ten issues or explicit capability/optimization gaps. The most consequential are a confirmation budget that produces no text on Cerebras, empty/truncated completions being accepted as successful stream endings, and a cross-vendor TTS fallback that does not preserve voice-ID or sample-format contracts.

This installation is not exclusively restricted to these two models: the database still contains three Qwen-configured tenants, and alternative model/provider paths remain in code. Those are existing settings, not evidence of cross-tenant leakage.

Application and production state were not changed during this audit. The only new repository artifact is this report. No restart, deployment, migration, configuration write, telephone call, or caller-data modification was performed. Small synthetic LLM requests were sent to the existing providers for diagnosis; they can incur ordinary inference usage.

## 2. Evidence boundaries

### Audited source

- Clean `origin/main`: `2ae47a12bbbf38b6a400ced4f825f1c5717a1341`.
- Inspection worktree: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-audit-c2b53d5f`.
- Shared desktop checkout is older and dirty. Its uncommitted changes were not used as evidence for main and were not altered.
- Production checkout: `a04735220f463fc9750fa99eab0836db30e47c43`, clean at inspection.
- Production API PID: `3717848`; voice-worker PID: `3717850`; both report startup at `2026-09-05 21:16:03 UTC`.
- A source diff between production SHA and audited main returned no changes for the inspected LLM providers, voice-pipeline modules, orchestrator, resilient LLM module, and telephony session-config builder.

All source locations below refer to the clean audited SHA, not the dirty desktop files. [Frozen source tree](https://github.com/talkyai-source/Talky.ai-complete-/tree/2ae47a12bbbf38b6a400ced4f825f1c5717a1341).

### Evidence labels

- **Live reproduction:** bounded request against a provider using the server's configured account.
- **Local reproduction:** executed application code with controlled synthetic inputs; no real caller involved.
- **Source-confirmed:** a directly inspected branch or contract; not an asserted production incident.
- **Latent:** demonstrable failure under a configuration that is not proven active now.
- **Not verified:** cannot be certified from this inspection.

A successful models endpoint proves catalogue access, not response generation or call quality. A successful small text completion does not prove audio intelligibility, multi-turn adherence, concurrency capacity, or tail latency.

## 3. Actual configuration and runtime observations

### Model inventory

Authenticated GETs returned HTTP 200:

| Account | Requested model | Listed |
|---|---|---|
| Groq | `openai/gpt-oss-20b` | Yes |
| Cerebras | `gpt-oss-120b` | Yes |

The explicit model IDs are different between vendors; the application uses the correct respective IDs.

Read-only SQL used an explicit transaction and transaction-local platform RLS context. No credentials, complete prompts, recordings, or caller transcripts are included here.

The eleven saved tenant configuration rows contained:

| Saved primary | Tenants | Saved completion limits |
|---|---:|---|
| Cerebras GPT-OSS 120B | 8 | Six at 90; one at 350; pilot at 1050 |
| Groq Qwen 3.6 27B | 3 | 90, 90, 500 |
| Groq GPT-OSS 20B | 0 | Used as configured fallback, not a saved tenant primary |

All eleven tenant rows selected `pipeline_mode=cascaded`. Campaign-level overrides can still select a different pipeline; tenant rows alone are not a proof that every possible entry point is cascaded.

The production env file contained:

```text
LLM_FAILOVER_ENABLED=true
LLM_SECONDARY_PROVIDER=groq
LLM_SECONDARY_MODEL=openai/gpt-oss-20b
LLM_FIRST_TOKEN_DEADLINE_MS=<unset>
STT_FAILOVER_ENABLED=true
STT_SECONDARY_MODEL=<unset>
TTS_FAILOVER_ENABLED=<unset>
TTS_SECONDARY_PROVIDER=<unset>
```

The code's default LLM first-token deadline is 2500 ms. Post-deploy initialization logs independently show:

```text
primary=cerebras/gpt-oss-120b secondary=groq deadline_ms=2500
primary=flux-flux-general-en secondary=nova-nova-3
```

Direct `/proc/<pid>/environ` reads were denied. Therefore env-file contents are not presented as a complete dump of the running process environment. Runtime initialization logs corroborate the LLM/STT wiring. No TTS resilient-wrapper activation appeared in the inspected logs.

### Fresh synthetic completions

These used short, non-customer prompts, from the server. The two model request contracts use their respective system/user-message conventions. These are individual observations, not a controlled model-ranking benchmark.

| Provider / total completion budget | HTTP | First visible text | Total | Result |
|---|---:|---:|---:|---|
| Cerebras 120B / 90 | 200 | 248 ms | 253 ms | Appropriate question about dimensions/sketch; `stop` |
| Cerebras 120B / 1050 | 200 | 368 ms | 380 ms | Appropriate question about area/layout; `stop` |
| Cerebras 120B / 3 | 200 | None | 200 ms | Empty content; `length` |
| Groq 20B / 1114 | 200 | 86 ms | 110 ms | Appropriate question about project scope/finish; `stop` |

The 90-token Cerebras request succeeded. It is incorrect to say that every 90-token call fails. The confirmed short-budget failure is the three-token request; the wider concern is that reasoning and visible output share the same limit.

Production SDK version was `cerebras-cloud-sdk==1.91.0`, matching the pinned dependency. A direct SDK probe returned a `ChatChunkResponseChoiceDelta` and `Ready.`. Calling the actual production `CerebrasLLMProvider.stream_chat_with_timeout` also returned `Ready.`.

### Production log evidence

Eight post-deploy Cerebras cache samples were observed. Five had nonzero cache hits, with reported ratios 0.72, 0.74, 0.80, 0.85, and 0.85; three were zero. Prompt sizes in those samples ranged from 8,361 to 12,586 tokens.

This proves that prompt caching can work. It does not establish a cache SLO or explain why each miss occurred.

A bounded journal scan requested seven days but capped the result at 120,000 entries. That cap was reached. It found two `zero_token_turn` events. This proves the empty-speech phenotype has occurred in the retained sample; it does not prove those specific calls failed for the same budget reason as the synthetic probe.

Broad substring counts for `404` or `429` were deliberately excluded from conclusions: unrelated HTTP traffic, identifiers, and other log fields can match them. They are not valid model-error counts without classification.

## 4. End-to-end structure

```text
Tenant AI Options + campaign settings + explicit call direction
  -> tenant config resolution / campaign prompt composition
  -> build_telephony_session_config
  -> VoiceOrchestrator constructs per-call providers
       STT: Flux primary -> Nova fallback
       LLM: Cerebras 120B primary -> Groq 20B pre-first-token fallback
       TTS: campaign/tenant provider; optional separate resilient wrapper

Carrier PCMU 8 kHz -> Asterisk -> C++ gateway -> backend media ingress
  -> PCM16 16 kHz STT input
  -> transcript / end-of-turn / barge-in handling
  -> contact confirmation + live structured state + campaign/KB instructions
  -> LLM streamed visible text
  -> sentence assembly + action-evidence validation
  -> TTS synthesis -> configured PCM interpretation / resampling
  -> C++ gateway -> Asterisk -> caller
```

Upsampling an 8 kHz telephone signal to 16 kHz satisfies the internal STT interface; it does not restore high-frequency information lost on the carrier leg.

Important source boundaries:

- `backend/app/domain/services/telephony_session_config.py:1555`: provider selected from resolved AI configuration, not hardcoded to Groq.
- `telephony_session_config.py:1576`: STT engine maps to its appropriate model.
- `telephony_session_config.py:1623`: provider/model passed into session configuration.
- `backend/app/domain/services/voice_orchestrator.py:1384`: primary LLM construction.
- `voice_orchestrator.py:1450`: secondary LLM selection.
- `backend/app/domain/services/voice_pipeline/audio_ingest.py:999`: STT stream consumption and transcript dispatch.
- `backend/app/domain/services/voice_pipeline/turn_streamer.py:562`: turn prompt assembly.
- `turn_streamer.py:656`: action-evidence validation before TTS.
- `turn_streamer.py:732`: tool-enabled path; `:742`: ordinary streaming path.
- `backend/app/domain/services/voice_pipeline/tts_playback.py:224`: TTS stream consumption.

The two directions share media/provider infrastructure, but that sharing is not itself direction mixing. Direction-specific prompt/session assembly precedes the shared pipeline.

## 5. Findings with proof and consequence

### F01 — Cerebras confirmation budget is too small to reliably produce a label

**Priority: high. Live and local reproduction.**

`voice_pipeline/confirm_llm.py:74` asks for a confirmation label with `max_tokens=3`. `llm/cerebras.py:216` sends that unchanged as `max_completion_tokens`; Groq adds 1024 tokens of headroom for GPT-OSS in `llm/groq.py:943`.

Request-capture proof for the same requested 90-token answer:

```json
{"provider":"groq","max_completion_tokens":1114,"reasoning_effort":"low"}
{"provider":"cerebras","max_completion_tokens":90,"reasoning_effort":"low"}
```

The live three-token Cerebras request returned empty content and `finish_reason=length`. Usage reported three completion tokens and zero reasoning tokens for that particular request; do not relabel all three as reasoning tokens. The broader contract still counts generated reasoning within the completion limit.

The actual confirmation helper was then invoked against both live providers with the same synthetic readback and reply:

```json
{"provider":"cerebras","utterance":"that matches what I gave you","result":"unclear"}
{"provider":"groq","utterance":"that matches what I gave you","result":"affirm"}
```

The helper is connected: `voice_pipeline/turn_runner.py:334-351` calls it for ambiguous email/phone readback confirmations. Clear deterministic confirmations are handled before this branch; they are not all broken.

**Consequence:** unnecessary repeated readbacks, added latency, and inconsistent capture behavior when switching between the two target models. It fails closed rather than confirming an unverified value.

**Root-cause direction:** separate the semantic answer limit from the provider's total generation allowance, including classification turns. Preserve an explicit deadline and fail-closed confirmation semantics. A larger pilot setting does not fix this helper's hardcoded three-token override.

**Acceptance proof:** the same confirmation fixtures must produce usable labels through both real SDK contracts, including ambiguous, corrective, and adversarial answers; no unconfirmed field may be persisted.

### F02 — Empty budget-exhausted completion bypasses LLM failover

**Priority: high. Local reproduction; related phenotype observed in production logs.**

`llm/cerebras.py:304-328` extracts content/tool fragments but does not expose a typed incomplete-completion outcome. `resilient_llm.py:261` treats a clean zero-token iterator end as a normal completion.

Controlled reproduction supplied a no-content completion ending with `length`:

```json
{"empty_length_completion":[],"secondary_called":false}
```

The normal turn streamer compensates at `turn_streamer.py:990` with a generic repeat request. The retained production log sample contains two `zero_token_turn` events.

**Consequence:** the primary can fail to supply a usable answer without the configured secondary getting a chance. The caller may be asked to repeat something STT already understood.

**Root-cause direction:** distinguish completed text, tool-only output, budget exhaustion, empty unusable output, and cancellation. Retry/fail over only when safe and before committed speech. Do not indiscriminately classify legitimate tool-only responses as failures.

**Acceptance proof:** an empty `length` result invokes the secondary; a valid tool result and caller cancellation do not trigger duplicate actions or unwanted speech.

### F03 — Mid-response stalls are converted into ordinary stream completion

**Priority: high. Local reproduction through both providers' timeout wrappers.**

`llm/groq.py:581-615` and `llm/cerebras.py:392-439` break without an error after a partial-output timeout. The turn streamer can then synthesize the remaining unpunctuated buffer at `turn_streamer.py:944`.

The controlled generator emitted `Your appointment is`, stalled past a shortened test deadline, and would then have emitted the rest of the sentence. Both wrappers returned:

```json
{"content":"Your appointment is","exception":null}
```

**Consequence:** incomplete answers are indistinguishable from normal completion to downstream code. This affects conversational coherence and can make important qualifications disappear. No particular live appointment was shown to be affected.

**Root-cause direction:** propagate an explicit incomplete-stream state. Preserve already-spoken content, do not replay the whole answer mid-utterance, and suppress unsafe incomplete tails. Recording an interruption reason is different from automatically switching vendors after speech.

**Acceptance proof:** stall after one fragment, after one complete sentence, and during a negation; assert exactly what may be spoken and recorded in history.

### F04 — Groq has nested retry ownership

**Priority: medium. Executable HTTP-transport reproduction.**

`llm/groq.py:467` constructs `AsyncGroq` without disabling SDK retries. Its installed SDK reports `max_retries=2`. The provider also has its own three-attempt loop at `:956`.

A mock HTTP transport returned 500 on every request. Retry sleeps were set to zero only in that isolated diagnostic process. Application source was not edited.

```json
{"synthetic_500_http_requests":9,"sdk_max_retries":2,"final_exception":"RuntimeError"}
```

**Consequence:** amplified requests and potentially amplified latency during provider degradation. The outer voice first-token deadline can cancel the sequence earlier; nine is not a claim that every live turn executes nine requests.

Cerebras already sets SDK `max_retries=0`, so the two adapters have drifted on retry ownership.

**Root-cause direction:** one retry owner, classified retryable failures, and a shared remaining-time budget. Authentication/invalid-request failures should not consume the same recovery strategy as transient transport faults.

### F05 — Provider cleanup does not explicitly close SDK clients

**Priority: medium. Local reproduction; production resource exhaustion not demonstrated.**

`llm/groq.py:1116` only clears `_client`; `_clients_by_key` still holds clients. `llm/cerebras.py:456` likewise clears the reference without awaiting SDK close.

Captured SDK clients remained open after invoking each provider's cleanup:

```json
{"provider_cleanup":"groq","sdk_client_is_closed":false}
{"cerebras_after_cleanup_sdk_client_closed":false}
```

A real Groq SDK + in-memory HTTP transport probe also showed that closing the application generator immediately after the first token left the underlying response open at the observation point. The SDK has its own finalizer; this finding is about deterministic release, not a claim that garbage collection never closes anything.

**Consequence:** connection lifetime depends on later finalization rather than the call lifecycle. Under repeated interruption/cancellation, that is a capacity risk. This audit did not measure a growing production FD count or prove an OOM.

**Root-cause direction:** own SDK stream/client lifetimes explicitly; close exactly once on success, failure, cancellation, and session teardown. Preserve intentional shared-client ownership where applicable.

### F06 — Current LLM fallback policy is one-way

**Priority: medium. Production configuration plus executable constructor reproduction.**

The configured secondary is always Groq 20B. `voice_orchestrator.py:1471` deliberately skips a secondary that equals the primary.

Using the current secondary settings and choosing Groq 20B as primary produced:

```json
{"primary":"groq/openai/gpt-oss-20b","secondary_constructed":false}
```

**Consequence:** Cerebras 120B → Groq 20B is configured; Groq 20B → Cerebras 120B is not. Simply choosing the other target in AI Options changes the resilience available to the call.

**Root-cause direction:** explicit allowed provider/model pairs and a direction-aware fallback policy for provider selection, with per-tenant credentials preserved. This means primary/fallback direction, not inbound/outbound telephone direction.

**Acceptance proof:** test both primary choices, missing secondary credentials, same-model rejection, and cancellation; log the effective primary and fallback per call.

### F07 — Cross-vendor TTS fallback has incompatible voice and audio assumptions

**Priority: high before enabling; latent in current inspected deployment.**

`resilient_tts.py:195` passes through the primary voice ID when no mapping exists. `voice_orchestrator.py:1629-1649` also initializes secondary providers with the primary session's voice ID.

Cartesia yields float32 bytes (`tts/cartesia.py:402-405`); Deepgram and ElevenLabs use PCM16. The gateway format is selected once from the primary provider at `voice_orchestrator.py:2006`. The wrapper does not convert secondary output or update that format.

Controlled Deepgram → Cartesia fallback produced:

```json
{"tts_fallback_voice_sent_to_cartesia":["aura-2-thalia-en"],"float_bytes_passed_unchanged":true}
```

Four float samples became eight unrelated int16 values when interpreted using the primary's format. This is a byte-contract reproduction, not a claim about an actual recorded caller's audio.

**Consequence:** invalid-voice errors or corrupted fallback audio; changing the model cannot fix it. Same sample rate alone is insufficient.

**Root-cause direction:** validate cross-vendor voice mappings before enabling fallback and normalize every TTS output to a declared internal format. Keep emergency prerecorded audio independent of both failed synthesis providers.

**Premortem:** missing mapping must be rejected during readiness/configuration, not discovered after a caller has been answered. Failover after already-played audio must not stitch different voices into the same sentence.

### F08 — Tool support and tool-turn resilience are not equivalent

**Priority: medium; capability gap, not proof of unguarded action execution.**

`action_tools.py:379` allows live action tools for Groq/Gemini, not Cerebras. Cerebras supports lower-level streamed tool fragments, but does not implement the same executable `stream_chat_with_tools` contract as Groq.

For `Please send me an email.`:

```json
{"provider":"groq","action_tools":["send_email"],"wrapped_action_tools":["send_email"]}
{"provider":"cerebras","action_tools":[],"wrapped_action_tools":[]}
```

Furthermore, `resilient_llm.py:289` delegates tool turns straight to the primary. The ordinary wrapper's first-token failover is not applied to that branch.

Important qualification: callback scheduling, email delivery, form submission, and controlled transfer currently return unavailable results in this voice-action module. They are not implemented live executors merely because Groq can call their schemas. `end_call` has an executable finisher path.

Action-claim validation is genuinely wired before TTS at `turn_streamer.py:656`, including ordinary non-tool responses. Cerebras is not therefore free to claim every action succeeded.

**Root-cause direction:** define provider capabilities and tool-result semantics at the common interface. Add parity only with deterministic result delivery, action idempotency, and explicit rules forbidding action replay after a side effect. Do not enable transfer as a side effect of LLM refactoring.

### F09 — Saved STT language is not carried into the telephony session

**Priority: medium for multilingual use. Local reproduction.**

`AIProviderConfig.stt_language` accepts a language string (`ai_config.py:233`). The session builder maps the engine to a model but does not carry that language into the STT stream call. Nova's streaming method defaults to English, and `audio_ingest.py:999` does not pass a language argument.

```json
{"saved_language":"es","session_language_field":"ABSENT","actual_stt_model":"nova-3"}
```

**Consequence:** a saved language choice can look accepted while the call keeps the default behavior. This is not evidence that current English calls are broken.

The database's `stt_model=nova-3` beside `stt_engine=deepgram_flux` is a separate, confusing storage convention. The telephony builder explicitly translates the engine to `flux-general-en`, and logs confirm Flux primary/Nova secondary. It is not proof that Nova is accidentally being sent to the Flux endpoint.

**Root-cause direction:** one validated STT capability contract carrying engine, supported model and language through save → session → primary/fallback connection. Reject unsupported combinations rather than silently ignoring settings.

### F10 — Two response paths pass different cache-routing metadata

**Priority: low; source-confirmed optimization gap.**

`voice_pipeline/llm_response.py:128` passes `campaign_id` into the LLM call. The main streaming calls at `turn_streamer.py:732-749` do not. `llm/cerebras.py:279` can only derive `prompt_cache_key` when one of those arguments is present.

**Consequence:** the claimed campaign cache-routing hint is not consistently wired across response paths. Prefix caching still works without the hint, as the observed production cache samples demonstrate. This is not a finding that caching is broken.

**Root-cause direction:** centralize turn-request metadata while preserving tenant/campaign separation. Verify actual cached-token usage before and after; a faster individual reply is not cache proof.

## 6. Agent behavior: what is actually protected

### Campaign prompts are not simply dropped

The session builder carries the resolved model and campaign/session configuration. The turn streamer composes campaign instructions with state and knowledge. Cerebras sends system instructions as a system message. Groq's GPT-OSS branch deliberately embeds them in the latest user-message envelope (`groq.py:346` and `:817`).

Groq's published reasoning guidance describes this user-message convention; it is a provider adaptation, not proof of a forgotten prompt. [Groq reasoning documentation](https://console.groq.com/docs/reasoning).

That proves construction and delivery shape, not guaranteed semantic obedience. A full multi-turn adversarial evaluation on the user's real campaign instructions was not performed.

### Generic speech has identifiable code sources

| Situation | Response source |
|---|---|
| LLM times out before TTS | Repeat-request fallback in `turn_streamer.py:905` |
| LLM raises before TTS | Processing-error fallback at `:909` |
| LLM completes with no usable speech | Generic repeat request at `:990` |
| Model claims an unproved action succeeded | Deterministic safe replacement at `:656` |
| Contact readback/correction | Backend capture state machine and confirmation path |
| TTS fails | Independent emergency-audio path in `tts_playback.py` |

These must not all be diagnosed as the model ignoring the campaign. Conversely, their existence does not excuse the upstream failure that caused recovery speech.

### Useful verified protections

- Tenant model/provider isolation is covered by `test_tenant_ai_config_isolation.py`, including two-tenant resolution and campaign voice precedence.
- Direction and opening mode remain separate inputs to the common session builder.
- The STT silent-stream watchdog counts voiced input, not just elapsed caller silence; it excludes agent-speaking periods and replays buffered audio on promotion. Relevant tests include `test_a_quiet_caller_never_trips_it`, `test_silent_primary_fails_over_and_the_caller_is_heard`, and `test_failover_replays_the_buffered_utterance`.
- Flux's `confidence=None` is explicitly neutral in `contact_capture.py:599`, not automatically treated as low-confidence speech.
- Contact capture has explicit status, validation and confirmation timestamps. Unconfirmed data is not automatically promoted just because the helper failed.
- Action-evidence guards have live-path tests, including `test_normal_cascaded_stream_blocks_unproved_claim_before_tts`.
- TTS emergency clips have checksum/frame-boundary tests in `test_emergency_voice_clips.py`.
- Barge-in and spoken-history handling are present; the interrupted path stores what was spoken instead of blindly committing all generated text.

All listed unit tests are part of the full suite run described below. They do not replace a real audio call.

## 7. Duplication versus legitimate separation

Not every duplicate-looking file is a workaround. Vendor clients must differ where their APIs differ, and speech-to-speech realtime is genuinely different from cascaded STT → LLM → TTS.

The harmful duplication is visible in contracts that should agree but do not:

1. Groq/Cerebras timeout wrappers both hide partial-stall completion state.
2. Reasoning-budget treatment differs between adapters and small classification calls.
3. Tool orchestration is implemented for only some providers.
4. Ordinary and alternate response paths pass different cache metadata.
5. SDK retry ownership differs between providers.

The `LLMProvider` abstract interface declares the basic stream/init/cleanup contract but not the complete timeout/tool/completion-state contract used by the voice pipeline. This allows a provider to satisfy the interface without proving feature parity.

Other distinct paths still exist:

- `voice_worker.py:114` creates Groq 20B at worker startup and supplies shared providers to its queued pipeline path at `:265`.
- The main telephony orchestrator constructs providers from call configuration.
- `infrastructure/assistant/llm_client.py` has separate model-to-vendor adaptation.
- The offline summarizer uses a separate Groq client; its code default is GPT-OSS 20B through `structured_output.py:121`, with an independent environment override.
- Realtime and alternative LLM/provider menus remain supported.

These paths were identified in source; this report does not falsely label each one as active traffic or dead code. A strict two-model product policy requires an entry-point inventory and migration decisions, not merely deleting the other classes.

### Workaround verdict

- The generic empty-response apology is a recovery workaround, not a repair for empty generation.
- Treating a partial timeout as normal EOF hides failure information rather than resolving it.
- A large per-tenant token setting can reduce exposure but does not repair the inconsistent budget contract or the hardcoded confirmation override. No claim is made about why the operator chose 1050.
- Groq's message-role adaptation and intentional refusal to replay speech/actions are legitimate provider/safety decisions.
- The earlier suspicion that ordinary raw logging necessarily exposes PII was ruled out by checking the global redaction hook.

## 8. Premortem and repair sequence — recommendations only

| Order | Failure to prevent | Root-cause approach | Proof required before rollout |
|---:|---|---|---|
| 1 | Empty classification/reply accepted as success | Model-aware generation budgets plus typed finish outcomes | Both SDKs, length/empty/tool-only/cancel cases, no false confirmations |
| 2 | Half-sentence treated as complete | Propagate incomplete state to speech/history handling | Stall before/after speech and across a negation; no replay |
| 3 | Provider outage multiplies attempts | One retry owner and remaining-deadline accounting | Count actual HTTP attempts and cancellation cleanup |
| 4 | Interrupted calls retain clients/connections | Explicit stream/client ownership and close behavior | Repeated barge-in soak; bounded FDs/tasks; shared clients not closed prematurely |
| 5 | Selecting Groq disables fallback | Explicit two-model primary/secondary policy | Both directions, missing credentials, bad models, no same-target fallback |
| 6 | TTS recovery changes format or uses invalid voice | Validated mapping and canonical audio format | Each enabled vendor pair, first/mid-stream errors, no malformed PCM |
| 7 | Tool behavior changes with provider | Shared capability/result contract | Side-effect idempotency, result-before-claim, no unsafe tool replay |
| 8 | UI language selection silently ignored | End-to-end validated STT configuration | Non-English fixture, primary/fallback parameter capture, unsupported-language rejection |
| 9 | Optimizations differ by entry point | Shared request metadata and traceable entry-point routing | Campaign/cache IDs captured, tenant isolation, measured cache usage |
| 10 | Tests pass but calls sound wrong | Controlled full audio evaluation and concurrency tests | Real STT/TTS audio, inbound/outbound, packet loss, accents, DNC, capture and barge-in |

Do not enable TTS cross-vendor failover merely because its flag exists. Do not introduce post-action retries that can duplicate an email/callback/transfer. Do not treat model throughput figures as a call-latency guarantee.

## 9. Corrections and ruled-out suspicions

1. **Cerebras SDK decoding:** a synthetic local stream was decoded into a different union shape. Production SDK and actual provider probes returned the expected typed delta and `Ready.`. The initial fake-stream result is not used as evidence of a production decoding defect.
2. **PII warning logs:** `app/core/log_redact.py` installs a process-wide redactor. A probe using the TTS warning format produced `text='[redacted chars=31 sha=3b101e08]'`; the raw test email did not survive. Raw logging call sites alone were insufficient evidence of a leak. Production flag overrides were not fully enumerated.
3. **Cartesia normal-path format:** Cartesia intentionally converts provider PCM16 to float32 before yielding it. The normal gateway declaration matches that. The confirmed mismatch is cross-vendor fallback while retaining the primary format.
4. **Flux/Nova stored model mismatch:** the engine is explicitly normalized by the telephony builder; logs corroborate the actual engines.
5. **Three-token failure:** the API reported zero reasoning tokens in that sample. The proof is empty content at a three-token total-generation cap, not an invented accounting explanation.
6. **No model outage inferred from HTTP substrings:** unrelated 404/429 matches were not attributed to LLM vendors.

## 10. Verification performed

From the clean main worktree, using the existing backend virtual environment:

```text
python -m pytest tests/unit tests/security -q -p no:cacheprovider
8773 passed, 7 skipped, 1472 warnings in 369.55s (0:06:09)

python -m ruff check app/ --select F --extend-ignore F401,F841
All checks passed!
```

Bytecode writing was disabled for the audit's Python commands. The inspected worktree remained clean.

Additional diagnostics executed in this turn:

- Authenticated provider model catalogues.
- Four bounded synthetic HTTP streaming completions.
- Direct production Cerebras SDK shape and actual-provider reply checks.
- Actual confirmation helper against both live target models.
- Local request-budget and message-role capture.
- Local empty-length completion/fallback probe.
- Local partial-stall probes for both timeout wrappers.
- Mock HTTP 500 retry counting through the real Groq SDK/provider.
- SDK cleanup and Groq early-response-release checks.
- Real session-builder language propagation probe.
- TTS fallback voice-ID and byte-format probe.
- LLM secondary-construction probe under production-equivalent secondary settings.
- PII-redaction probe through the installed logging hook.
- Read-only tenant-config SQL and sanitized production journal inspection.

These diagnostic probes do not become regression tests automatically. Before implementing repairs, turn them into durable failing tests, then re-run the canonical suites after integration.

## 11. Not done / cannot honestly certify

- No fixes, commits, pushes or deployments in this audit.
- No new PSTN inbound/outbound call originated.
- No fresh full STT → LLM → TTS audio loop, recording playback, accent/noise test, or subjective voice/gender assessment.
- No production saturation test, provider quota stress test, or statistically useful p95/p99 latency measurement.
- No exhaustive adversarial campaign-prompt compliance evaluation.
- No proof that all saved third-model configurations should be migrated without tenant/operator agreement.
- No complete live process environment read; permission denied.
- Journal sampling was bounded; absence from that sample is not absence from all history.
- No frontend/Admin canonical rerun: no UI files changed and this was a backend/provider diagnosis, not a release claim.
- No claim that the open source fixes from the prior task are deployed merely because main contains them.

## 12. Primary documentation used

- [Cerebras reasoning](https://inference-docs.cerebras.ai/capabilities/reasoning): GPT-OSS reasoning levels, separated reasoning/content, and total-generation accounting.
- [Cerebras chat-completion API](https://inference-docs.cerebras.ai/api-reference/chat-completions): completion-token limit includes reasoning; request/stream/tool parameters.
- [Groq reasoning](https://console.groq.com/docs/reasoning): GPT-OSS low/medium/high support, reasoning output controls and message guidance.

Source documentation was used to check vendor contracts, not to infer account health. Health observations came from the live diagnostic responses; application behavior findings came from the inspected code and executed probes.
