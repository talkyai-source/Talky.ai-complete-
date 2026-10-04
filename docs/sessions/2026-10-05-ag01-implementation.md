# AG01 saved and effective AI profiles

Implemented locally on 5 October 2026 in the existing isolated worktree, branch `codex/production-ready-20261004`. Base: `7c0ffac7db5ea02baca152a91f291fc83e605f1a`. Implementation candidate: **`4f47123f18af3cb01f2330138c635c76aae7182c`**.

AG01 remains **in progress**, with a locally tested implementation. This is not deployment, live provider acceptance, voice-quality certification or full production readiness. No model migration, default-model reset, new provider, database migration, customer call, real provider request, push or deployment was performed.

## What was wrong and how it was repaired

The selected settings could change between the dashboard, storage, campaign and provider request. Some failures were therefore wiring and validation problems, rather than evidence that temperature or model size caused poor comprehension.

| Proven problem | Repair |
|---|---|
| Viewing saved settings could replace a missing voice with the first live-catalog voice and persist that change. The page independently replaced a missing model/voice and reset the sample rate. | Existing configurations are read without catalog-driven writes. The page retains saved selections and displays unavailable choices. Defaults apply on an explicit provider change; selecting another voice from the same provider preserves the selected model and rate. Explicit saves validate availability. |
| The frontend response parser discarded `voice_tuning`, including the meaningful `null` that disables eager mode. Save responses could also differ from the canonical values actually stored. | The parser preserves every supported tuning field. The save response and reload agree on the canonical persisted values. Invalid numbers, unsupported ranges and inconsistent end/eager thresholds produce validation errors. |
| AI Options used account-independent query keys, a one-time prefetch flag and a draft seeded once. Delayed work could survive an account switch. | Config and both catalogs use verified tenant/user keys. Departing queries are cancelled and removed, the editor remounts per scope, and late save/test/preview results are fenced. New previews close prior audio contexts; superseded preview responses cannot start audio. Missing verified tenant identity starts no settings queries. |
| Campaign Realtime prompt precedence could lose a campaign-specific nested prompt. Initial prompt hashes omitted the action-capability instructions added afterward. | Explicit campaign prompts retain precedence through create, edit and reload. The native initial identity covers the instructions actually submitted; later instruction submissions have separate full digests. |
| Runtime ignored a selected xAI voice, and unsupported saved OpenAI voices could silently become Marin. | Explicit xAI voices are forwarded. Unsupported voices fail clearly without replacement. Legacy xAI rows carrying an inherited OpenAI voice require explicit reselection. Existing pinned models and ordinary defaults remain unchanged. |
| Realtime retried unrelated handshake failures after dropping reasoning. | Only a structured rejection naming the exact unsupported reasoning field permits one compatibility retry. Auth, model, rate, unrelated and unclassified errors do not trigger that fallback. Requested, submitted and acknowledged controls are recorded separately; missing echo fields remain unknown. |
| Cerebras and Gemini could treat truncated output or bare stream EOF as successful, including proceeding with incomplete tool output. | Terminal status is required before publishing/executing tool decisions. Truncation, filtering and absent terminal proof raise the shared incomplete-stream failure without replaying the tool. Successful text, nullable arguments and continuations retain regression coverage. |
| Save and startup could disagree about allowed LLM combinations, while some explicit reasoning overrides were ignored. | Both use the existing offered-plus-legacy model contract. Invalid selections fail before provider initialization. Unsupported explicit reasoning controls fail instead of being silently ignored. |
| Twilio and Vonage setup could ignore selected speech engine, language and tuning. Invalid engines/rates could silently select a different effective value. | Both reuse the existing STT selection policy and tenant tuning. Unknown explicit engines and unsupported synthesis rates are rejected. Required phone transport conversions remain explicit. Flux-specific controls are inactive in the UI when the effective recognizer is Nova. |
| Shared ElevenLabs catalogs, preview caches, samples, campaign assignment and runtime had inconsistent private-voice checks. A failed deletion could remove the ownership record and make a private orphan appear shared. | Those boundaries share ownership eligibility checks. Unknown/private unregistered voices are denied unless provider metadata positively identifies a public voice; registered foreign clones remain denied. Ownership lookup failure fails closed. Uncertain provider deletion retains the ownership record. |

The new diagnostics contain allowlisted settings and hashes, not prompt text, transcripts, contacts, tool arguments or credentials. Traditional request records distinguish configured temperature from an effective override and visible-answer target from the provider completion ceiling. The envelope digest includes actual messages, instructions, tools and continuation results. A diagnostic serialization failure reports an unavailable digest without blocking dispatch.

Knowledge metadata records an existing valid snapshot checksum or passage-version digest when available. Otherwise it explicitly says **unversioned**. A full prompt digest identifies assembled input; it does not recreate the input or prove that retrieval selected correct facts. Knowledge correctness remains AG02 work.

## Preserved contracts and compatibility effects

- Traditional temperature still defaults to `0.6`; Groq QA mode can intentionally submit `0` with its configured seed. This does not guarantee deterministic or correct responses.
- Luna keeps reasoning `none`. GPT-OSS keeps its supported low reasoning policy and headroom. Gemini 3.8 keeps its separate low-thinking request shape. The existing default remains Cerebras `gpt-oss-120b`.
- Native OpenAI Realtime has no temperature field. Its separate VAD, noise, speed and token settings remain separate from traditional controls. Noise off is explicitly `null`; OpenAI stored reasoning `none` continues to mean provider default, as labelled in the UI. xAI `none` is an explicit provider-specific setting.
- Saved audio rates are retained. SIP/Vonage use the existing 16 kHz transport path and Twilio uses 8 kHz; native Realtime uses its existing PCMU wire format. These conversions are not treated as ignored preferences. Unsupported rates can no longer silently label audio with a different rate.
- Invalid legacy settings now require correction instead of automatic voice/model substitution. Unregistered private voices require legitimate ownership repair; this work does not guess an owner or rewrite customer records.

The OpenAI native temperature and VAD treatment follows [OpenAI's Realtime guidance](https://developers.openai.com/blog/realtime-api) and [VAD documentation](https://developers.openai.com/api/docs/guides/realtime-vad). Provider-specific references and exact evidence limits are in the [native notes](artifacts/ag01/native-realtime-notes.md) and [audio notes](artifacts/ag01/audio-profile-notes.md). Current xAI documentation does not certify availability of the application's unchanged pinned 1.0 model.

## Verification

The [verification manifest](artifacts/ag01/verification.json) pins commands, results, source integrity and artifacts. All 63 changed source/test files match the implementation commit after newline normalization.

| Check | Result |
|---|---|
| Final integrated backend selection, 42 modules | 716 passed, 1 skipped; 337 dependency/deprecation warnings |
| Additional non-overlapping traditional adapter regressions, 9 modules | 102 passed; 24 dependency/deprecation warnings |
| Final frontend page/API/query/controls/temperature and adjacent auth regressions, 7 modules | 38 passed, no skips |
| Full frontend TypeScript and Next production build | Passed; existing custom Cache-Control warning retained |
| Backend CI-rule Ruff (`F`, excluding existing `F401/F841` policy) | Passed |
| Changed frontend ESLint | Passed with one numeric-ref cleanup warning; the counter intentionally invalidates pending previews on unmount |
| Broader exploratory frontend suite | 673 passed, 1 failed, 2 database cases skipped; not a green full-suite result |
| Full repository frontend lint | Blocked by the existing ESLint plugin configuration error; changed TypeScript files were linted separately |

The single backend skip is the menu assertion for a hidden, no-longer-offered model. No skipped test is counted as acceptance.

The broader frontend failure is a stale campaign test expecting a `Row actions` menu that the table no longer renders. It reproduced in isolation. All 21 resolved source/setup/config/lock files match the starting commit; see the [baseline comparison](artifacts/ag01/frontend-campaign-baseline.json). No separate historical checkout was executed. The full-lint configuration also matches the starting commit; see [lint evidence](artifacts/ag01/lint-baseline.json). These outstanding repository checks remain visible, rather than being described as passing. An initial unrestricted parallel frontend run was stopped because of host contention; the bounded run above is the recorded broader result.

The contract probes include:

- Four offered traditional LLMs paired with all four TTS provider families: actual save validation, SQL argument serialization, reload, campaign assignment and session configuration in 16 combinations. Separate adapter tests inspect the real request builders using fake external transports.
- All eight offered OpenAI Realtime voices and two existing hidden xAI configurations: API handlers, serialized storage, campaign/runtime assembly and native request fields. Provider acknowledgement is synthetic.
- Actual Flux query arguments, Nova SDK options, Cartesia JSON, Deepgram audio query parameters, ElevenLabs formats and Google protobuf configuration, with transport adaptations recorded.
- Negative ownership, incomplete-stream, unsupported-setting, missing-credential, handshake, account-switch, late-response and preview-audio cases. Actual AuthProvider tests cover identity transitions; StrictMode replay is also exercised.

These tests use synthetic identities, credentials, catalogs and content. SQL transport capture is not a fresh real-database/RLS integration run. Dynamic catalog entries and live account/model availability are not certified individually. No latency or subjective audio-quality measurement was made.

## Remaining acceptance and plan position

1. Deploy a matching frontend/backend candidate to the designated environment and verify selected released profiles with designated credentials. Verify owned-number calls, audio, interruption and action outcomes under AG04/OP03 and the release gates; these offline results do not certify them.
2. CP08 still owns shared HTTP identity attribution: a cookie can change in another tab before its identity marker arrives, and AI Options responses do not carry authenticated owner stamps. The verified A-to-B cache lifecycle is repaired; universal cross-tab response attribution is not claimed. Do not treat this remaining identity boundary as production-closed.
3. AG02 still owns retrieval correctness/version coverage; AG03 owns correction memory beyond the rolling buffer; AG04 owns semantic comprehension and voice qualification. This package does not claim to repair every wrong answer or lost lead field.
4. Resolve the existing full-lint configuration and stale campaign test in their applicable verification/support work. Full repository tests, remote CI and deployed acceptance remain required.

Before this package started there were **31 packages: 6 in progress and 25 planned**. After this local AG01 implementation there are **7 in progress and 24 planned**, with no package newly marked production-closed. The 24 include the 20 later packages (AG02–AG07, OP01–OP12, RG02–RG03) and CP05, CP06, CP08, CP09. Only CP05, CP06 and CP09 were explicitly deferred by the user. CP08 remains planned and unstarted. All 15 final acceptance gates remain `not_run`; the feature freeze remains active. The next planned agent package is AG02.

Rollback should restore a compatible frontend/backend pair while preserving saved records. Do not restore automatic voice replacement, incomplete-tool execution or private-voice cache bypasses. If a particular profile cannot be supported safely, keep that profile unavailable with a precise reason rather than selecting another identity/model. The original checkout's unrelated application changes remain untouched.
