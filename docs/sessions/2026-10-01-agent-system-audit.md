# Agent system readiness audit — 1 October 2026

## Assessment and scope

The system is not ready to describe as fully functional for end users. The
infrastructure is running, but reproducible failures remain between recognition,
model output, knowledge retrieval, audio playback, action execution and storage.
A larger model or lower temperature would not repair those boundaries.

This was an investigation, not an implementation or deployment. Three specialist
agents audited LLM/knowledge/prompts, STT/TTS/media, and Realtime; the primary
agent checked production, recent conversations, assistant actions and connectors.
No customer calls, messages, calendar/CRM writes, credential refreshes, service
restarts or application changes were performed. Production SQL used read-only
transactions. This report excludes credentials and client contact values.

Evidence sources:

- Initial production checkout: `a3ca1f72864f141c15d1412e38b6c2cf0a6c248e`, clean.
- A separate deployment occurred during this audit. Final production checkout:
  `7bc9d3b688c7b459baed48f982df40550690896d`, clean. API, voice and dialer workers
  restarted on 30 September at 22:23:53 UTC (1 October 03:23:53 PKT).
- Latest candidate: `7bc9d3b6`, branch `codex/realtime-independent-20261001`.
  The audit began at `b5977f9f`; another session committed two configuration fixes
  during inspection. They were included in the final assessment.
- Main desktop checkout is an older branch with substantial uncommitted work.
  It was not treated as the deployed version and was not changed.
- Read-only production health, account configuration, connector state, latest
  campaign transcripts, journal counts, source tracing and offline reproductions.
- Current official OpenAI, Groq, Deepgram, ElevenLabs, Cartesia and xAI guidance.

Final campaign-history recheck: 30 September 22:29:49 UTC (1 October 03:29:49 PKT).
The two latest campaign calls were still the pre-deployment tests below.

P1 below means a release blocker for the affected feature. P2 means a significant
reliability or configuration defect. A reproduction proves the stated code path;
it does not prove that path caused a particular historical call.

## What is actually running

Production DB and Redis probes returned `ok`; dialer, voice and reminder
heartbeats were healthy. Healthy infrastructure is not feature acceptance.

The inspected account currently saves these settings:

| Setting | Saved value |
|---|---|
| Requested pipeline | Realtime |
| Realtime model / voice | `gpt-realtime-2` / `ash` |
| Realtime VAD / noise | high eagerness / far field |
| Traditional LLM | Cerebras `gpt-oss-120b` |
| Traditional temperature / max tokens | `0.7` / `1050` |
| STT | Deepgram Flux; ancillary stored model field is `nova-3` |
| TTS | ElevenLabs `eleven_v4` |

At the first inspection, the deployed orchestrator hard-coded the Realtime
preplay guard as unavailable and converted requested Realtime sessions to
cascaded. That explains the earlier release/configuration mismatch. During this
audit a separate deployment published `7bc9d3b6`; the final source recheck confirms
the old hard-coded guard is absent and the independent Realtime code is now on
the server. It must not be described as still unshipped. Final DB/Redis and worker
health checks remained healthy. No post-deployment end-to-end call was observed
by this audit, and the Realtime defects below are in this newly deployed code.
See `2026-10-01-realtime-selection-audit.md` for the original guard diagnosis.

The environment file contains `VOICE_KB_MODE=tool`, `STT_FAILOVER_ENABLED=true`
and `LLM_FAILOVER_ENABLED=true`. The TTS failover flag/provider are unset and no
secondary voice map is configured. These are file values, not a claim that the
running process environment was read. Never infer runtime behavior from YAML
comments alone: the normal call builder uses the saved tenant settings.

## Recent conversation evidence

Latest campaign test: `169081a4-fbf0-4220-bb15-eb97defe6736`, 30 September
19:32:28 UTC, 124 seconds, `lead_gen@7`.

- Several opening greetings/Hello prompts precede the substantive exchange.
- During an outbound session the agent asks who is calling; the caller points
  out that the agent called them.
- After repeated denials of familiarity with the payment service, the agent
  repeatedly asserts that it was set up for the caller's business.
- It continues qualification without first resolving that disputed premise.

Previous test: `98b83aaf-2f47-4db0-8c6d-a0817f26b9c7`, 19:24:44 UTC,
202 seconds. It asks for the same name it later gives as its own identity,
repeats payment-provider questions, says goodbye, then resumes qualification
when the caller speaks again.

The current campaign guidance explicitly assumes an existing or former client
whose consultant relationship is verified. Its recovery example repeats that
the records show a prior relationship. That is an identifiable source for the
assumption. A campaign-wide instruction is not evidence that this particular
test caller has that relationship. Resolve disputed identity/relationship before
continuing the sales flow; do not merely add stronger persuasion wording.

Both tests predate the 19:45 production restart, the Realtime setting save
recorded at 19:39, and the subsequent 22:23 deployment. They do not demonstrate
the new Realtime implementation.
The name-placeholder repair is already in the current production revision; the
earlier transcript is not proof that repair failed. Exact audio was not reviewed,
so Hello repetition alone is not attributed to STT, TTS or packet loss.

Neither test contains a newly supplied email/phone. Zero captured-contact rows
for these two tests is expected, not evidence of lost supplied contacts. Both
have summaries. The earlier 26 September referral/contact problem remains
separate historical evidence, documented in its dedicated diagnosis.

## Confirmed defects in the traditional pipeline

### A1 — P1: STT disconnects can silently stop listening

`backend/app/infrastructure/stt/deepgram_flux.py:734`, `:999`, `:1132` catches
sender/receiver socket failures and lets the stream finish normally. The outer
loop breaks; `backend/app/domain/services/resilient_stt.py:660` consequently
treats completion as healthy and does not invoke Nova fallback.

Reproduction using the real Flux provider and resilience wrapper with a fake
socket closing with code 1011: one socket opened, zero fallback calls, zero
transcripts, no exception. Continuing input audio did not recover listening.

Fix: distinguish caller cancellation/input exhaustion from unexpected provider
termination; propagate the latter to one owner of bounded reconnect/failover.

### A2 — P1: a TTS timeout can produce silence without recovery

`backend/app/domain/services/voice_pipeline/tts_playback.py:402` breaks after the
last inter-chunk timeout without marking delivery failure or invoking emergency
audio. A synthetic stalled provider reproduced two attempts over approximately
six seconds: zero sent chunks, zero emergency clips, no recorded silent-turn
failure and `stop_turn=False`.

Fix: return an explicit incomplete-delivery outcome for the final timeout and
use the existing recovery/observability path. Do not record the sentence as heard.

### A3 — P1: partial TTS output can become a complete sentence in history

`tts_playback.py:648` handles generic provider failures differently from gateway
delivery failures. `turn_streamer.py:1224` and `:1436` can then append the full
sentence to spoken history. A real `ElevenLabsPartialAudioError` after one chunk
reproduced `stop_turn=False` and no delivery-failed flag.

Symptom: caller hears only part of an answer/readback, but subsequent reasoning
and extraction behave as if the whole sentence was heard. Fix provider errors,
timeouts and gateway errors through one explicit delivery-result contract.

### A4 — P2: Cartesia close without completion looks successful

`backend/app/infrastructure/tts/cartesia.py:385` uses an async WebSocket iterator.
aiohttp ends that iterator on CLOSE/CLOSED, so the in-loop CLOSED branch cannot
handle that termination. A close before the matching Cartesia `done` yielded
zero audio without error. Require a matching completion event after iteration;
keep deliberate cancellation separate. This is dormant for the current
ElevenLabs account but affects Cartesia configurations.

These four audio defects exist in both the former production `a3ca1f72` and the
newly deployed `7bc9d3b6`.
Production journal aggregation since 27 September found one Flux send error and
one TTS inter-chunk timeout. These events were not tied to either latest test.

### K1 — P1: the price guard accepts unsupported amounts

`backend/app/domain/services/voice_pipeline/grounded_figures.py:73` skips checks
when speech contains no digit, while the prompt instructs spoken number words.
At `:60`, any number in the grounding strings is accepted as evidence;
`turn_streamer.py:821` supplies the entire assembled system prompt.

With a synthetic company prompt containing no prices, an invented forty-nine
pounds per month passed. So did 10 pounds per month, because rule number 10 was
present. An unsupported 900 pounds was blocked. This is not robust grounding.

Fix: check normalized amounts against approved business facts/tool results,
including currency and conditions. Prompt numbering and examples are not facts.

### K2 — P1: retrieval hits can lose their answer before reaching the LLM

`backend/app/domain/services/voice_pipeline/kb_budget.py:48` and `:73` retain the
beginning of a node plus its generic voice answer rather than a query-relevant
passage. Both injection and tool retrieval use this budgeter.

A matched 1,506-character node contained a precise 37-month battery warranty.
After the 600-character fitting/summary path, the LLM received no warranty length,
despite the block being presented as official answers. The search succeeded but
the evidence delivery failed.

Fix: select relevant source sentences with their qualifications before applying
the existing budget, or split oversized sections. A new database is unnecessary.

### K3 — P1: inbound ranking can prioritize common question words

`backend/app/services/scripts/knowledge/retrieval.py:306`, `:323`, `:340` rank
stopwords before excluding them from the later coverage calculation.

For a battery-warranty question, three unrelated nodes beginning with similar
question wording outranked the exact warranty fact and filled the top three.
Fix ranking to prioritize content terms and use a consistent relevance policy
for pinned inbound snapshots and outbound retrieval.

### K4 — P2: tool retrieval can be disabled or dropped unexpectedly

`knowledge_tool.py:155` compares literal provider names; a resilient Groq wrapper
has a different name and loses capability detection. Separately,
`voice_pipeline_service.py:306` / `turn_streamer.py:461,491` let legacy end-call
handling suppress knowledge tools on ordinary campaign turns. The current
GPT-OSS/Cerebras paths are also explicitly excluded from this tool mode.

This means the saved `tool` preference often uses injection instead. Retrieval
is not necessarily absent; the effective mode is misleading.

For supported Groq/Gemini tool paths, `groq.py:726` and `gemini.py:584` can return
after spoken content even when a tool call accompanies it. A synthetic preamble
plus lookup request executed zero tools. Process all collected tool calls and
their results, even when the model also supplies a short preamble.

### P1 — Prompt contradictions and unverified caller assumptions

`prompts/personas/lead_gen.py:163` describes a real person while hard rules require
AI honesty. Examples at `:290` and `:302` suggest email/callback actions which have
no working voice executor. `voice_pipeline/conversation_craft.py:78` says the
phone is already known and never to ask, even when that state is not established.

Actual synthetic base compositions before campaign guidance, knowledge and
history: outbound 28,749 characters / 5,097 words; inbound 13,492 characters /
2,316 words. The outbound composition has nine layers and four copies of the
one-question rule. Length alone is not a proven failure, but conflicting examples
and unconditional state assertions are concrete problems.

Fix one owner per behavior, condition state-dependent instructions on saved
facts, and remove examples that imply unavailable actions. The previously fixed
whole compliance-floor duplication is not reported as a remaining defect.

## Realtime release blockers

These findings apply to the independent Realtime code now deployed as
`7bc9d3b6`. They do not explain the earlier calls which ran the traditional
pipeline. Their deployment during this audit did not resolve the reproduced
defects or establish live-call acceptance.

### R1 — P1: contact correction can terminate playback and the call

`backend/app/realtime/bridge.py:740` clears queued gateway audio for a contact
directive without cancelling/awaiting the running playback task.
`openai.py:659` interrupts only while provider generation is active, although
buffered audio can still be playing after generation completes.

An offline late-transcript/contact-correction sequence reproduced the exact
terminal error: `Realtime responses overlapped during playback` (`bridge.py:410`).
Fix ownership/cancellation for playback independently of generation state.

### R2 — P1: telephone contact confirmation lacks playback acknowledgements

`bridge.py:535` advances the contact readback gate only after a successful playback
acknowledgement. `telephony_media_gateway.py` has no such interface. Twilio
inherits browser waiting but sends no matching completion mark and ignores mark
frames at `twilio_bridge.py:529`.

The caller can hear a readback and say yes while the backend keeps the value
pending. Implement transport-specific utterance acknowledgements and test the
actual phone path. Successfully queueing audio is not proof it was played.

### R3 — P1: interruption retains unheard content in provider history

`openai.py:876` and `bridge.py:416` clear local buffers/playback but do not send
`conversation.item.truncate` with played duration. A speech-started event
reproduced zero WebSocket commands. The model can therefore remember its entire
generated response even when only part was heard. Retain item IDs and playback
position, then truncate the unplayed provider-side content.

### R4 — P1/P2: token and buffer limits can end a call

The UI permits 64 output tokens. `openai.py:831` treats token-budget incomplete
responses as terminal errors; `bridge.py:496` tears down the session. The
playout buffer also rejects output beyond 30 seconds. Reproduce budget exhaustion
and recover explicitly without playing unvalidated partial output.

### R5 — P2: displayed defaults differ from effective settings

`config.py:18` defaults to near-field noise handling and 1024 output tokens,
but validation at `:39` discards normalized defaults. Empty settings reach
`openai.py:397` as far-field with no output-token cap. Accepted noise setting
`off` is serialized as an invalid noise-reduction type instead of disabled.

Normalize once and use the normalized object for preview, save and calls.
Legacy settings containing temperature/transcription-model/server-VAD structures
are also rejected by the new strict schema; migrate those at the boundary.
The inspected production dictionary contains only accepted eagerness/noise keys,
so that legacy migration problem was not observed for this account.

### R6 — P2: ending and latency reporting do not follow actual playback

`bridge.py:922` can request a goodbye continuation then wait for the previous
utterance and hang up. Browser/Twilio lack the called hangup capability.
Use an explicit final-utterance/lifecycle sequence with a bounded deadline.

`openai.py:753` records the first generated chunk as first-audio latency, while
full-response validation prevents playback until completion. Measure generated,
approved and played audio separately. The current safeguard intentionally adds
delay; its cost is hidden by the existing metric.

### R7 — xAI compatibility remains unverified

`xai.py:153` inherits OpenAI payload structure, transcription and reasoning
defaults which differ from current xAI documentation. Passing tests assert our
payload, not live provider compatibility. Verify a provider-specific contract
before offering this opt-in path. No claim is made that its older model was
removed, and no model migration is needed to repair GPT Realtime.

## Actions, connectors and saved leads

### C1 — P1: advertised business actions have no working voice executor

`backend/app/domain/services/voice_pipeline/action_tools.py:261` discards arguments
and returns unavailable for callback, email, form and transfer. Realtime exposes
these contracts; current Cerebras voice does not expose action tools. Neither
route implements those requested business outcomes. Expose actual capabilities
and wire required ones to existing services with authorization and durable receipts.

### C2 — P1: dashboard assistant advertises denied actions

`backend/app/infrastructure/assistant/agent.py:85` promises email functionality,
but `tools/dispatch.py:75` explicitly disables email, SMS and issue submission.
Six meeting/workflow tools also lack authorization policies/model exposure.
Offline dispatch of all nine returned `tool_authorization_policy_unavailable`.
Do not remove authorization checks; implement policies/executors or remove the
unsupported capability promises.

### C3 — P1 for booking: duplicate calendar authorization paths

`backend/app/services/meeting_service.py:79` reads token expiry but ignores it
and directly installs the access token at `:100`. Calendar reading elsewhere
uses the refreshing connector resolver. Consequently calendar read and booking
can behave differently for the same connection. Reuse the existing resolver.

Current account state: Gmail expired; Salesforce pending with no connected
account; no HubSpot connection. Calendar and Drive are active but have expired
access tokens with refresh tokens available. No refresh/provider request was
attempted: these two are unverified, not declared broken solely from expiry.

### C4 — P1 for multi-CRM: wrong IDs and misleading success

`backend/app/services/crm_sync_service.py:252` uses one call-record ID across
providers. A synthetic summary update sent the Salesforce ID to both Salesforce
and HubSpot. HubSpot inherits the base no-op `update_call_log` (`crm/base.py:100`)
but sync still adds it to successful providers at `crm_sync_service.py:287`.
The same single receipt also prevents independent settlement retries.

Track provider-specific receipts and respect failed/no-op updates. The current
`asyncio.create_task` scheduling (`:588`) is not a durable outbox: restart and
ambiguous provider-success/local-save failures remain loss/duplication risks.

### C5 — Lead capture does not implement arbitrary campaign fields

`backend/app/domain/services/voice_pipeline/lead_slot_capture.py:69` intentionally
writes email and phone only; its September 28 comment records a scope decision.
Other needs, referral identity, channel, provider and callback time are not a
generic live extraction feature just because a campaign asks for them.

Pending caller-derived contacts now persist in the allowed states; do not repeat
the older blanket claim that unconfirmed contacts are never stored. Separate
captured/confirmed/saved/action-completed states in both UI and language. Widening
business capture requires agreeing the supported fields and writing them through
the existing evidence-backed service, not additional prompt promises.

## Current provider guidance and what it changes

- OpenAI recommends clear, concise voice instructions and well-defined action
  triggers. Keep the independent Realtime prompt. Its WebSocket protocol requires
  truncating unplayed audio on interruption. GA Realtime temperature is not the
  knob to force deterministic speech. Sources: [voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting),
  [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations),
  [Realtime developer notes](https://developers.openai.com/blog/realtime-api).
- Groq's local-tool loop requires application execution and returned results.
  Current GPT-OSS tool support means the application's exclusion is a product
  decision, not a universal provider limitation. Sources: [local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling),
  [tool support](https://console.groq.com/docs/tool-use/overview),
  [reasoning guide](https://console.groq.com/docs/reasoning).
- Deepgram recommends 80 ms Flux chunks; the code uses 40 ms. This is tuning to
  measure, not a demonstrated cause. English-only Flux comments are also stale.
  Source: [Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart).
- ElevenLabs v4 is supported by streaming speech; it is not an invalid model.
  The provider positions conversational models differently from narration-quality
  models. Benchmark latency and quality before changing the current choice.
  Sources: [model guidance](https://elevenlabs.io/docs/eleven-api/choosing-the-right-model),
  [v4 product documentation](https://elevenlabs.io/docs/eleven-creative/playground/text-to-speech).
- Cartesia completion/context rules and aiohttp iterator behavior support the
  incomplete-stream finding. Sources: [contexts](https://docs.cartesia.ai/use-the-api/tts-websocket/contexts),
  [aiohttp implementation](https://docs.aiohttp.org/en/stable/_modules/aiohttp/client_ws.html).
- Opt-in xAI serialization needs independent verification against its own
  [speech-to-speech documentation](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech).

## Verification and smallest repair sequence

Focused existing suites: **532 passed, 2 skipped** across the four audit tracks:
LLM/knowledge/actions 122; STT/TTS 216 plus two skips; Realtime 60; assistant,
calendar, CRM and lead capture 134. Warnings included an audio fake-listener
teardown warning. These passes do not cover the newly reproduced failing seams.
No end-to-end phone call, browser acceptance, live OAuth refresh, CRM write or
audio recording review was performed during this audit.

1. Freeze one release candidate and show its effective engine/model/config in
   test results. Keep each production revision and its call evidence distinct;
   a deployment during an audit must be rechecked before issuing conclusions.
2. Repair STT termination and all unsuccessful TTS delivery paths. Verify that
   conversation history contains only appropriately delivered assistant speech.
3. Repair knowledge ranking/passage fitting and typed factual grounding; replay
   relevant, unrelated, missing and conflicting-source questions.
4. Remove traditional prompt contradictions and condition relationship/contact
   instructions on actual caller state. Keep Realtime wording independent.
5. Finish Realtime playback ownership, transport acknowledgements and interruption
   truncation before considering the deployed phone path ready for end users.
6. Align advertised tools with authorization and working executors; share token
   refresh and maintain per-provider CRM receipts.
7. Run one controlled inbound and outbound acceptance flow on the exact candidate:
   interruption, silence/provider failure, known/unknown facts, corrected contact,
   persistence/reload and an actually supported action with its receipt.

No new agent framework, larger LLM, vector database or extra prompt overlay is
required to begin these repairs. Readiness is established by the complete flow,
not a successful preview, provider handshake or aggregate unit-test count.
