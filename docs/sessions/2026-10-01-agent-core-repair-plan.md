# Today's goal: reliable agent system, repaired at the source

Date: 1 October 2026. Status: implementation and integration verification in progress; not released.

## Objective

Repair every confirmed defect in the [agent-system audit](2026-10-01-agent-system-audit.md)
so that supported inbound and outbound calls hear the caller, use the correct
company evidence, speak truthfully, preserve corrections, execute authorized
actions once, and show saved results consistently in Leads and call history.
Keep traditional and Realtime voice implementations separate while sharing
business facts, authorization, persistence and execution services.

Today's target is the complete repair and acceptance sequence below. Completion
is based on evidence, not the clock, a green health endpoint or a test count.
An unavailable external account or a failed phone acceptance case remains
explicitly unfinished; it is not relabeled complete by hiding a control.

The user has activated the expanded production-readiness goal. The tracker is
active. Completion still requires the release and live acceptance evidence below.

## Baseline and ownership

- Production rechecked at planning start: clean `7bc9d3b688c7b459baed48f982df40550690896d`;
  DB/Redis readiness passed. Recheck again immediately before release.
- Dedicated repair branch: `codex/agent-core-repair-20261001`.
- Worktree: `tmp/agent-core-repair-20261001`, starting at that exact SHA.
- Original dirty checkout and the separate Realtime worktree remain intact.
- Keep the initial provider/model/temperature settings fixed during correctness
  work so results are attributable. Measure tuning separately afterward.
- Existing baseline: 532 focused tests passed, two skipped; new fault scenarios
  must reproduce their failures before being used to verify repairs.
- Runtime repairs and additive migrations are being implemented in this isolated
  branch. This repair branch has not yet been deployed or used for customer actions.

Follow-up from the user: Google Gemini 3.8 Flash is enabled. The separate Realtime
worktree included `61d5945c` (greeting/script behavior) and `4e88992a` (Gemini
3.8 Flash catalog); both are merged into the repair branch. A later production
recheck found clean `b93b23de72fe6fe0fe84d3244c5f59e83401ff74`, with all five
runtime services active and zero sessions on /health. Preserve the subsequent
Gemini/Realtime, Luna and conversation/knowledge fixes before release. Include
`gemini-3.8-flash` alongside Groq GPT-OSS 20B and
Cerebras GPT-OSS 120B in the final traditional-provider acceptance matrix.
The user has now authorized adding OpenAI GPT-6 Luna as a selectable fourth
traditional provider, simplifying the shared LLM structure and prompts, and
implementing the confirmed repairs below. Keep native Realtime separate and
preserve existing saved selections. Benchmark before claiming any model is fastest.

Four concurrent ownership tracks keep file changes controlled:

| Owner | Primary work | Shared-file rule |
|---|---|---|
| Audio agent | Flux, STT recovery, TTS delivery, Cartesia completion | Root integrates changes to `turn_streamer.py` and orchestrator |
| Knowledge/LLM agent | Retrieval, passage fitting, grounding, provider tool handling, traditional prompts | Root owns the final turn loop and action wiring |
| Realtime/media agent | Realtime lifecycle/configuration, browser/Twilio/Asterisk acknowledgements, C++ protocol | Coordinate gateway interface once with audio owner |
| Root integration | Actions, permissions, connectors, CRM receipts, lead/UI integration, release | One owner for shared interfaces, migrations and final integration |

Agents may implement non-overlapping files in parallel. Contract changes land
first; callers then adopt the agreed contract. Each completed slice receives a
review from a different track and a focused regression check. No agent deploys
independently or changes another owner's files without coordination.

## Simplicity rules

1. Repair existing implementations and remove the superseded branch of logic.
   Do not add another rule layer that hides the original defect.
2. Reuse the current Postgres, workers, provider adapters and connector services.
   No new agent framework, vector database or additional voice-turn model call.
3. Give each concern one owner: recovery, evidence rendering, effective settings,
   action execution and saved facts. Provider wire formats stay provider-specific.
4. Prompts express behavior and use current state. They do not stand in for a
   database write, permission, provider receipt or verified customer relationship.
5. Recovery is bounded. Never replay a completed action or automatically restart
   an utterance after part of it has already been sent.
6. Keep uncertainty explicit: pending contact, incomplete playback, unknown
   provider outcome and missing business evidence are valid states.
7. A small additive table or field is acceptable when needed for a real durable
   receipt. Do not build a generic workflow/identity/event-sourcing platform.

## Repair sequence and completion evidence

### 0. Freeze the baseline and capture the failures

Record call direction, actual engine, provider/model, voice, prompt version/hash,
effective settings, codec/sample rates and application/gateway versions using
existing call/event metadata. Exclude secrets and client contact values.

Turn the audit's synthetic reproductions into meaningful regressions using real
adapters with fake sockets/providers at the network boundary. Keep fixtures
small, deterministic and free of real customer data. Replay redacted latest-call
scenarios separately from deterministic transport tests.

Acceptance: each confirmed defect has an issue ID, failing reproduction, owner,
expected behavior and linked check. Historical symptoms already fixed are not
reintroduced as fresh bugs. A new deployment during work requires a baseline
comparison before applying the candidate.

### 1. STT recovery: keep listening or report a clear terminal failure

Audit coverage: A1.

Files: `deepgram_flux.py`, `resilient_stt.py`; integration in audio ingestion,
voice pipeline lifecycle and orchestrator.

- Distinguish normal input exhaustion, caller cancellation and unexpected
  provider termination. A socket close/error must not look like successful STT.
- Let the existing resilience wrapper own bounded retry/fallback; remove the
  ineffective competing reconnect path in Flux.
- Preserve the call-owned input queue when a provider sender is cancelled.
  Otherwise a new fallback socket can start with an already-dead audio iterator.
- Bound buffered audio and discard already committed turns. No duplicated
  transcripts, repeated LLM turns or replay of confirmed caller speech.
- Restore the correct language, keyterms and contact-capture mode on replacement.
- If recovery cannot reconstruct missing speech, request repetition. If all
  configured recovery fails, surface the existing terminal failure outcome.

Acceptance: send/receive failure and fatal provider event each activate bounded
recovery; Nova receives subsequent frames; committed turns are not duplicated;
normal hangup never reconnects; missing secondary fails visibly; no leaked task.

### 2. TTS delivery: never record a complete sentence after incomplete output

Audit coverage: A2, A3, A4 and conditional TTS fallback configuration.

Files: `tts_playback.py`, `turn_streamer.py`, `resilient_tts.py`,
`infrastructure/tts/cartesia.py`, `voice_orchestrator.py`.

- Centralize terminal timeout, empty stream, provider synthesis error and gateway
  delivery failure through the existing delivery-failed/stop-turn mechanism.
  Preserve failure reasons separately from caller interruptions.
- Permit one bounded retry or configured fallback before any original audio is
  submitted. After partial submission, stop the utterance and do not replay it.
- Use the existing emergency clip after exhausted no-audio recovery; record
  whether that recovery itself was submitted/completed or failed.
- Exclude an incomplete sentence from fully delivered conversational history.
  Without reliable word alignment, retain it as partial/unknown evidence rather
  than inventing an exact spoken prefix.
- Close synthesis iterators/contexts explicitly on failure, interruption and
  cancellation. Align outer timeouts with provider recovery under one total
  silence deadline rather than stacking independent retry budgets.
- Require Cartesia's matching `done` event after WebSocket iteration. A normal
  socket close without that event is incomplete synthesis.
- Before enabling cross-vendor fallback, validate the secondary provider's voice,
  credentials, model and format. Never reuse another vendor's voice ID. The
  current unset TTS failover flag stays off until this path passes its own test.

Acceptance: no-audio stall produces explicit recovery/failure; partial ElevenLabs
failure does not become a complete spoken sentence; close without Cartesia done
fails; stale context completion is ignored; next turn works; cancellation releases
resources; no replay or voice stitching after partial delivery.

### 3. Knowledge: deliver the actual answer with its source and restrictions

Audit coverage: K2, K3, all three runtime knowledge consumers.

Files: `knowledge/retrieval.py`, `voice_pipeline/kb_budget.py`,
`knowledge_tool.py`, `turn_streamer.py`, `realtime/bridge.py`, ingestion parser
only where its heading/long-section behavior prevents useful passages.

- Keep current Postgres retrieval and pinned inbound snapshots. Normalize content
  words consistently before ranking and coverage. Use deterministic tie-breaking.
- Replace prefix-only truncation with query-relevant complete sentence/paragraph
  groups. Preserve nearby negation, eligibility, currency, period and exclusions.
- A generic voice summary is optional context, not a substitute for the requested
  fact. Do not label weak matches as official answers.
- Use one small shared evidence result: status, source/node/version IDs, selected
  passages and coverage. Status distinguishes useful evidence, weak match,
  no match and unavailable retrieval.
- Supply that result to traditional injection, traditional tools and Realtime
  tools. Keep prompt wording and provider serialization separate.
- Validate oversized/malformed-heading documents; chunk useful complete passages
  without separating a price from its restrictions. Re-ingest only affected
  sources with existing IDs/version/snapshot rules respected.
- Preserve tenant/campaign authorization, invalidation and admitted snapshot
  consistency. A transient DB error must not silently enable guessing.

Acceptance: the warranty fact beyond character 1,200 reaches all consumers;
shipping distractors lose to the exact subject; unrelated questions get no match;
negation and fees retain their conditions; topic changes do not inherit unrelated
previous questions; conflicting sources remain unresolved; timeout is explicit;
new calls see committed edits while admitted snapshots follow the existing rule.

### 4. Factual grounding: use business evidence, not numbers in instructions

Audit coverage: K1.

Files: `grounded_figures.py`, `grounded_links.py`, turn-streamer validation and
the existing Realtime completed-response guard.

- Remove the entire system prompt from the financial-evidence input. Rule
  numbers, examples, campaign sales claims and caller guesses are not authority.
- Pass selected approved passages and successful fact-returning tool results
  with provenance. Keep amount, currency, unit and applicable qualification tied.
- Normalize common supported spoken and digit forms. Ambiguous/unsupported
  amounts remain unverified; do not claim a universal multilingual number parser.
- Do not derive totals, add-ons, discounts or eligibility without an approved
  source or an existing deterministic business calculation.
- Use the existing truthful recovery response for unsupported claims. Preserve
  non-price speech, phone readbacks, dates and product identifiers.
- Reuse the same evidence rules for Realtime before releasing validated audio.
  No second LLM verifier is added.

Acceptance: a rule numbered 10 does not authorize a ten-pound price; spoken
forty-nine fails without evidence; wrong currency/unit fails; separate fees do
not authorize an invented total; valid facts pass with required caveats; phone
numbers and statements such as no fixed price are not falsely rejected.

### 5. Tool calling and failover: execute requests once and return their results

Audit coverage: K4 and provider capability gaps affecting C1.

Files: Groq, Cerebras and Gemini adapters; `resilient_llm.py`,
`knowledge_tool.py`, `action_tools.py`, turn loop; a small interface capability
property only if needed.

- Replace provider-name string guesses with explicit capabilities delegated
  through wrappers. Record requested versus effective knowledge mode and reason.
- Remove the coupling between legacy end-call handling and knowledge lookup.
- Execute collected tool calls even when accompanied by a spoken preamble.
  Assemble fragmented JSON, validate arguments, store assistant/tool messages
  and return results in the provider's supported format.
- Complete the current Cerebras tool path using its existing fragment support
  and a small bounded orchestration helper shared with compatible Groq behavior.
  Do not switch the user's model just to avoid implementing its tool contract.
- Bound decision/result rounds and tool timeouts. Unsupported sequences get an
  explicit outcome rather than an unbounded agent loop.
- On model failover, preserve completed tool results. A model retry must never
  become a second email, booking, form submission or transfer attempt.

Acceptance: text-only, tool-only, preamble-plus-tool, fragmented arguments,
unknown tool, timeout, cancellation, wrapped provider, primary failure before
execution and failure after execution. Assert backend action count and receipt,
not only the assistant's text. Verify current Cerebras and Groq fallback paths.

### 6. Prompts: concise behavior based on current caller and backend state

Audit coverage: traditional prompt contradictions and latest-call relationship
handling; preserve existing inbound/outbound and name-placeholder repairs.

Files: traditional composer, guardrails, persona and conversation-craft modules;
campaign brief/session assembly; independent `app/realtime/prompts.py` only for
its own wording and the shared factual state it consumes.

- Retain identity, direction, objective, concise conversation, evidence use,
  contact confirmation and actual available actions. Give each rule one owner.
- Remove human-identity wording and examples promising unavailable execution.
- Make known-phone and known-customer statements conditional on evidence.
  Caller ID is not automatically an approved alternate callback number.
- A campaign's intended audience is not proof that the person answering belongs
  to it. Unlinked browser tests start with unknown customer relationship.
- If the caller disputes the relationship, acknowledge uncertainty and stop
  repeating it. Preserve the original CRM record while recording the dispute.
- Revise the affected campaign's denial-handling wording as a reviewable,
  versioned change; a generic prompt fix alone cannot remove saved contradictions.
- Retain full contact readbacks and required source qualifications when applying
  short-answer limits. Avoid repeated greetings and renewed qualification after
  a terminal closing; a substantive caller correction is handled distinctly.
- Keep model and temperature fixed for the initial evaluation. Reduce redundant
  prompt content against behavior tests, not an arbitrary word-count target.

Acceptance: multiple-run conversation evaluations for you called me, disputed
relationship, unknown name, alternate phone, unavailable action, no-interest,
interruption and goodbye. Composition tests alone are insufficient evidence.

### 7. Realtime ownership: generation and playback have separate lifecycles

Audit coverage: R1 and prerequisites for R2/R3/R6.

Files: `realtime/openai.py`, `realtime/bridge.py`, playout buffer and existing
media gateway interfaces. Keep complete-response validation initially.

- One small current-utterance object carries response/item IDs, utterance ID,
  generation number, generation outcome, playback outcome and terminal receipt.
- `response.done` means generation finished, not playback finished.
- All barge-ins, contact corrections and guard replacements use one cancellation
  method: invalidate the utterance, cancel/await playback, clear output, and only
  then permit replacement audio. Ignore late events from the previous generation.
- Keep event reception non-blocking while waiting for bounded playback results.

Acceptance: late caller transcript while prior validated audio plays, contact
correction, interruption during generation, interruption during playback, duplicate
terminal events, disconnect and cancellation. None may cause overlapping playback
or let stale speech advance contact confirmation.

### 8. Media receipts: represent what each transport can actually prove

Audit coverage: R2, R3, R6; shared requirement with traditional TTS/contact state.

Files: browser gateway and audio worklet, Twilio gateway/webhook, telephony
gateway, existing C++ utterance/chunk protocol and session capability handshake.

- Extend existing begin/chunk/finish/cancel handling with a correlated terminal
  receipt. Include call, utterance and generation; reject stale/duplicate events.
- Browser: correlate actual worklet completion/interruption with its utterance.
- Twilio: send named completion marks and process returned marks. Invalidate
  before clear; a mark returned because audio was cleared is not successful playout.
- Asterisk/C++: reuse existing utterance/chunk fences and frame counters. Add a
  final marker and terminal status through existing control paths, not a service.
- Label evidence precisely: generated, submitted, transmitted, transport-played,
  interrupted, failed or unknown. RTP frames sent do not prove the callee heard
  them; neither does elapsed time or an empty queue.
- For contact confirmation, require the receipt type allowed by the supported
  transport policy, bound to that exact readback, and caller confirmation. When
  transmission is the strongest evidence, do not manufacture heard status:
  use explicit full-value caller repeat-back to confirm the value independently,
  otherwise preserve pending status and show the reason.
- On interruption, cancel provider generation if needed and truncate OpenAI's
  unplayed item using the supported position. Never use the complete generated
  duration as played position. Unknown/estimated position stays labeled and uses
  conservative handling rather than assuming words were heard.
- End-call lifecycle tracks the new final goodbye, waits for its terminal receipt
  or bounded timeout, then invokes the owning call termination service. Cover
  browser, Twilio and Asterisk rather than calling an absent gateway method.

Acceptance: completed, partial and never-played readbacks; generic yes after a
cancelled readback cannot confirm it; full-value repeat-back is value-bound;
clear-returned Twilio mark is rejected; stale acknowledgements cannot release
new audio; goodbye precedes termination; tool results survive interruption.

Rollout dependency: Python and the C++ binary need compatible capability versions.
Build/test both in one frozen release. Older gateway versions must report the
missing capability explicitly. Use the existing supported deployment procedure
to publish compatible components under its drain/restart checks; do not rsync
individual files or independently replace a live gateway mid-call.

### 9. Realtime settings and limits: one effective configuration

Audit coverage: R4, R5, R7 and latency part of R6.

Files: Realtime configuration, campaign assembly, provider serializers, preview,
AI Options controls and diagnostics.

- Return normalized settings from one boundary used by save, preview and calls.
  Apply defaults to the real wire payload and show those effective values.
- Canonicalize off to disabled noise reduction; remove GA temperature from the
  OpenAI payload; migrate legacy dictionaries explicitly. Model supported VAD
  shapes consistently. Invalid settings fail before call admission with a reason.
- Retain GPT Realtime 2 and low reasoning. Do not enlarge model/token settings
  to conceal failures. Keep the bounded audio buffer and validation protection.
- Token exhaustion/overflow withholds incomplete audio and permits one shorter
  retry using canonical instructions and stored tool results. A repeated failure
  follows a specific bounded recovery/termination path; never repeats side effects.
- Expose only verified token bounds and settings combinations in AI Options.
- Give xAI its own serializer under the existing adapter interface. Validate the
  selected model/protocol without silently upgrading it. Keep an explicit
  unavailable reason until its actual protocol/audio check passes; this is an
  interim restriction, not closure of the xAI compatibility issue.
- Separate provider first-generated-audio, validation-ready, first transmission,
  transport-playback and interruption-stop timings. Missing evidence is unknown.

Acceptance: empty/partial settings, save/reload/preview/runtime equivalence, legacy
settings, invalid noise type, budget exhaustion, second failure, no repeated tool,
provider-specific wire-contract checks and synthetic provider smoke tests.

### 10. Actions and permissions: connect real operations through existing services

Audit coverage: C1, C2, C3 and capability-dependent prompt/tool behavior.

Files: `voice_pipeline/action_tools.py`, assistant registry/dispatch/proposals,
canonical RBAC, communications/calendar/meeting/reminder services, existing
transfer coordinator and connector resolver.

- A small shared capability check uses the actual executor, tenant connection,
  campaign allowance and authorization. Both prompts/UI and model schemas consume
  it. Missing capabilities explain their reason.
- Add the narrowly required send/meeting/support authority to canonical RBAC.
  Configuring a connector must not automatically grant permission to send.
  Preserve current actor-bound preview/apply behavior for dashboard mutations.
- Voice calls use server-owned tenant/campaign context and confirmed caller
  intent, never an LLM-supplied user identity or fabricated dashboard actor.
- Validate exact recipient/destination/time and confirmation revision immediately
  before execution. A later correction or withdrawal invalidates the old request.
- Wire email to the existing communications service; booking to the meeting
  service; callback requests to the existing scheduling/dialer path with explicit
  time/timezone and consent. A recorded request is not a scheduled callback.
- Wire transfer to the existing validated, tenant-scoped telephony coordinator.
  Do not start a second transfer implementation or permit arbitrary destinations.
- Form submission uses only a configured form/destination and validated fields.
  If no submission target is configured, retain a reviewable request; do not call
  a saved note a submitted form. Implement the narrow missing adapter needed for
  the advertised operation, not a new form-builder product.
- Audit SMS, issue reporting, meeting changes, reminders and existing plan-step
  actions before exposing them. A stub that only writes completed status must
  be replaced by provider-backed execution and a receipt.
- Use the central refreshing connector resolver for calendar reads and writes;
  remove the separate raw-token path. Handle revoked grants separately from
  refreshable expiry, with a clear reconnect status.
- Preserve request IDs, parameter/confirmation revisions and results durably in
  the existing action records, with a minimal schema extension if required.
  Retries use the same idempotency key. Ambiguous external timeout is unknown,
  not permission to send/book again automatically.

Acceptance: unauthorized actor, missing/revoked connector, token refresh, duplicate
request, changed recipient, withdrawn consent, failure before send, success before
local receipt failure, worker restart and provider timeout. Verify provider result
and saved receipt. Provider acceptance is not recipient delivery unless a delivery
receipt exists. No live message is sent to an arbitrary customer for testing.

Temporary capability hiding protects users while executors are unfinished. Full
completion still requires the promised actions to work under supported configured
conditions; unconnected accounts remain explicit external acceptance dependencies.

### 11. CRM sync: provider-specific receipts and durable retry

Audit coverage: C4.

Files: `crm_sync_service.py`, CRM base/HubSpot/Salesforce adapters, existing
workers and additive migration if no suitable per-provider delivery record exists.

- Store remote contact/call ID and sync result per tenant, call and provider.
  An ID for Salesforce must never be reused for HubSpot.
- Implement HubSpot summary update and treat false/no-op results as unsuccessful.
- Persist pending work before acknowledging scheduling. Reuse an existing durable
  worker mechanism; if absent for CRM, add one narrow Postgres delivery table and
  worker polling rather than a new broker or generic workflow engine.
- Use unique delivery keys, leases, bounded retry/backoff and visible failures.
  Record the remote result before deciding whether another send is safe; reconcile
  ambiguous outcomes rather than blindly creating duplicates.
- Backfill old receipts only when the provider is known. A legacy shared ID with
  ambiguous ownership needs review, not copying to every provider.
- Make settlement and summary updates independent per destination and revision.

Acceptance: both CRMs connected, only one connected, one provider failing, later
summary, restart after remote success, duplicate job, retry exhaustion and ambiguous
legacy ID. The UI's success state must match the actual provider receipt.

### 12. Leads: save supported details and show their real status

Audit coverage: C5, contact consequences of audio/Realtime defects, and the
previously documented missing referral/follow-up information.

Files: contact state/capture service, existing post-call summary/extraction flow,
Leads API and the shared lead details panel. Review schema before adding fields.

- Preserve the current caller-derived email/phone live writer. Pending remains
  visible and unusable as confirmed contact; confirmation is bound to its value.
- Add a fixed, small set of already-requested business details through the
  existing post-call analysis: need, current provider, preferred channel,
  requested callback wording/time/timezone, next owner/action and referral.
  Do not add another model request to ordinary voice turns or parse every custom
  instruction as a new data schema.
- Keep caller and referral subjects separate. A friend's number cannot replace
  the caller's canonical number. Use an additive subject reference only if the
  current schema cannot represent that distinction safely.
- Attach source call/turn evidence, status and update revision. Post-call recovery
  may propose missed facts; it must not invent live confirmation.
- Resolve callback time only with adequate date/timezone context. Otherwise keep
  original wording and needs-review status. Requested, scheduled, sent and
  completed are different statuses backed by different evidence.
- Protect manual corrections and later caller corrections from older workers.
  Refresh current facts for an existing lead independently of first-time sales
  qualification. A follow-up request must not disappear behind no-interest text.
- Use the same saved fields in Leads, Contacts and call details. Show pending,
  processing failure and no information distinctly; verify after a full reload.

Acceptance: new and existing lead, corrected email, alternate phone, separate
referral, uncertain number, Thursday/time/timezone ambiguity, callback withdrawal,
disconnect before/after confirmation, duplicate post-call job, out-of-order jobs,
manual correction, tenant isolation and consistent UI reload. Historical replay
must not send messages or schedule outreach.

### 13. Remove obsolete duplication and measure the final configuration

- Remove superseded retry branches, duplicate token acquisition, prefix-only
  evidence rendering, conflicting prompt examples and repeated capability checks
  once their replacement and all callers pass. Avoid unrelated file cleanup.
- Preserve intentional separation: traditional versus Realtime prompts and
  provider wire adapters are not harmful duplication.
- Verify microphone/wire/provider codecs and rates through real boundaries.
  Include 16 kHz linear16 and supported 8 kHz PCMU telephony without relabeling
  bytes as another format. Reject incompatible configuration before audio starts.
- Measure recognition/end-of-turn, model, synthesis, validation and delivery
  separately. Compare p50/p95 against the recorded baseline and a documented
  budget set from those measurements, not an unsupported universal latency claim.
- Only after correctness: compare Flux chunk sizing, VAD eagerness and existing
  TTS options using the same recordings/scenarios. Keep the current model/voice
  unless repeatable evidence supports a change within the user's controls.

## Acceptance matrix

Run offline fault tests first, then service integration with a test database,
then provider smoke checks with synthetic data, then controlled end-to-end calls.
Record which transports/providers were actually exercised; never infer phone
readiness from a browser test or all accounts from one credential.

| Scenario | Required result |
|---|---|
| Flux socket dies during input | Bounded recovery continues receiving frames or fails visibly; no duplicated turn |
| No TTS output / partial TTS output | Explicit recovery/failure; no false complete spoken history |
| Correct fact deep in a long source | Relevant fact and necessary caveat reach all knowledge consumers |
| Unknown, conflicting or injected source | Honest unavailable/uncertain answer; source text cannot override system policy |
| Unsupported digit/word price | No invented amount, unit, currency or derived total |
| Caller disputes identity/relationship | Agent stops repeating the disputed premise |
| Preamble plus lookup / provider failover | Tool result consumed; side effect executes at most once |
| Caller interrupts a readback | Stale audio stopped; provider history corrected; value remains pending unless independently confirmed |
| Late acknowledgement or Twilio clear mark | Cannot complete the replacement utterance or confirm a contact |
| Realtime token exhaustion | One bounded shorter recovery; no action replay or unvalidated audio |
| Callback/email/form/transfer | Correct permission, target, confirmation, actual execution and durable result |
| Calendar token expired/revoked | Refresh succeeds or explicit reconnect state; no silent raw-token failure |
| Two CRMs and a restart | Correct provider IDs, independent retries and no duplicate activity |
| Contact correction/referral/follow-up | Correct subject, evidence/status saved and shown after reload |
| Goodbye | Final utterance followed by actual lifecycle end; no unwanted qualification restart |
| Tenant boundary/manual edit | No cross-tenant access or late-worker overwrite |

For conversational behavior, run the same multi-turn scenarios repeatedly with
fixed model/settings and record source use, correction handling, action truth,
saved results and interruption behavior. Passing string assertions on prompts
does not establish that the model follows them.

Controlled call coverage includes traditional and Realtime, inbound and outbound,
browser tests and the actual Asterisk/PSTN path. Twilio and xAI require their own
acceptance if retained as supported enabled options. Use designated test numbers,
test mailboxes/calendars and CRM test records. Customer outreach is not a test.

## Release and rollback

1. Integrate the four tracks into one reviewed candidate. Run targeted regression
   suites, then the required backend/security, frontend type/lint/component and
   C++ protocol checks for the final changes. Run migrations against a realistic
   database copy and verify old/new code compatibility.
2. Prepare the exact SHA, changed schema/protocol/settings, release manifest,
   verification results and rollback commands before a production action.
3. Follow [the existing deployment procedure](../DEPLOYMENT.md), including its
   traffic drain, gateway build/version, migration and service checks. If an
   operator-supplied drain artifact or interactive step is required, present a
   concrete release package; do not bypass the gate or invent approval.
4. Enable a controlled test campaign first. Verify the full call, final transcript,
   stored fields and action/CRM receipts on that exact release. Recheck engine
   selection and provider payloads rather than relying on a successful preview.
5. Expand only after acceptance. Roll back on new dropped calls, false confirmations,
   duplicate effects, lost persistence or materially worse measured latency.
   Keep additive evidence/receipt data; do not destructively reverse migrations
   or resend side effects during rollback.

External dependencies are recorded separately: Gmail reconnect, completion of
Salesforce OAuth, an intended HubSpot connection, provider credentials/test
accounts, designated call targets and any transport limitation. Expired access
tokens alone do not prove a refreshable connection is broken. These dependencies
do not prevent unrelated local repairs, but their end-to-end gates cannot be
claimed complete without actual access.

## Progress and definition of done

- [x] Audit reconciled with current production revision.
- [x] Separate integration branch/worktree prepared.
- [x] Detailed design, ownership, failure cases and acceptance plan recorded.
- [ ] Regressions reproduce every confirmed code defect on the baseline.
- [ ] STT/TTS recovery and delivery history repaired.
- [ ] Knowledge ranking/passage delivery and grounding repaired.
- [ ] Model capabilities/tool continuation and action replay behavior repaired.
- [ ] Traditional prompts and caller-relationship handling repaired.
- [ ] Realtime lifecycle, media receipts, truncation, limits and settings repaired.
- [ ] Actions, authorization, token refresh and CRM durability repaired.
- [ ] Supported lead details persist and display with correct evidence/status.
- [ ] Obsolete conflicting code removed; effective settings/timings verified.
- [ ] Required regression, database, provider and controlled-call acceptance pass.
- [ ] Exact release deployed through the supported procedure and verified.

Close an issue only with the smallest repair, its regression, integration proof
and relevant live acceptance. A temporary disabled capability is containment,
not completion of its implementation. Report remaining account/transport blocks
explicitly. Do not call the system bulletproof or promise universal model accuracy.

## Provider references retained from the audit

- [OpenAI Realtime prompting](https://developers.openai.com/api/docs/guides/voice-prompting)
  and [conversation/interruption handling](https://developers.openai.com/api/docs/guides/realtime-conversations).
- [Groq local tool calling](https://console.groq.com/docs/tool-use/local-tool-calling).
- [Deepgram Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart).
- [ElevenLabs model guidance](https://elevenlabs.io/docs/eleven-api/choosing-the-right-model).
- [Cartesia contexts](https://docs.cartesia.ai/use-the-api/tts-websocket/contexts).
- [xAI speech-to-speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech).

Recheck the precise provider contract at implementation time. Public documentation
does not prove this account's model access or successful provider connectivity.
