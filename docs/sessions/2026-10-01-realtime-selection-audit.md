# GPT Realtime availability and campaign selection audit

Inspected on 2026-10-01. Read-only production inspection; no settings, calls,
messages, migrations or deployments were performed. Application source was
inspected in an isolated checkout of production revision
`a3ca1f72864f141c15d1412e38b6c2cf0a6c248e`.

## Confirmed root cause: configured Realtime is blocked before connection

Production's account configuration contains `pipeline_mode=realtime`,
`realtime_model=gpt-realtime-2`, and `realtime_voice=ash`. The saved timestamp is
2026-09-30 19:39:35 UTC. The OpenAI key is present in the server environment
file; its value was not output. The running API's provider catalog returns
HTTP 200 with GPT Realtime 2 and its voice list.

However, `backend/app/domain/services/voice_orchestrator.py:126` hard-codes
`_REALTIME_PREPLAY_GUARD_AVAILABLE = False`. At lines 552–564,
`create_voice_session` changes a requested Realtime session to `cascaded`
before reaching the Realtime builder or opening an OpenAI socket. It logs
`realtime_blocked reason=c1_preplay_guard_unavailable ... fallback=cascaded`.

This guard was introduced by commit `f940a7d8` on 2026-09-02. Its stated reason
is that Realtime audio is played before the terminal transcript is available
for action-claim validation. Without an equivalent check before playback, the
agent could claim an action succeeded before the application validates that
claim. The block applies at the shared orchestrator, including campaign tests
and phone calls; changing the API key or selecting a different voice does not
remove it.

Focused verification on the deployed revision:
`pytest tests/unit/test_voice_orchestrator.py -k realtime -q`
result: **1 passed, 34 deselected**. The test verifies that the Realtime builder
is never awaited, the selected mode becomes cascaded, and no Realtime session
or bridge exists. All providers were mocked; no external connection was made.

## Campaign selection is not implemented end to end

`Talk-Leee/src/components/campaigns/voice-provider-picker.tsx` builds provider
choices from `/ai-options/voices`, which is the standard TTS voice catalog.
It uses `tts_provider` and `voice_id`; it does not consume the separate
Realtime catalog or expose a pipeline selector.

The create/update schemas in `backend/app/api/v1/schemas/campaigns.py` expose
TTS provider/voice fields but not `pipeline_mode`, `realtime_model`, or
`realtime_voice`. The corresponding campaign handlers validate standard TTS
voices and rebuild `script_config` from prompt settings. The runtime session
builder can read Realtime overrides from `script_config`, but the supported
campaign editing flow does not write those fields. The latest 15 campaign
records inspected had no such overrides.

Consequently the account setting is inherited in principle, but there is no
working per-campaign Realtime selector, and the orchestrator still overrides
the inherited Realtime mode.

## Availability and preview are misleading

The provider endpoint advertises Realtime based on OpenAI key presence, without
checking the orchestrator guard. AI Options also has a hard-coded fallback
catalog, so even an absent backend Realtime catalog is not represented as
unavailable. Saving Realtime settings does not warn about the forced fallback.

The voice-preview implementation in
`backend/app/api/v1/endpoints/ai_options/preview.py:66` uses
`/v1/audio/speech` with `gpt-4o-mini-tts`. A successful voice preview therefore
does not validate Realtime WebSocket connectivity, model access or call routing.

The browser test backend reports its actual `pipeline_mode` in the ready event,
but `test-agent-button.tsx` does not display that field or explain the fallback.
This leaves the requested mode and actual running mode inconsistent to the user.

## Smallest coherent correction

1. Use a shared backend capability/readiness result for the catalog, settings
   validation and call setup. Show the blocked reason and actual selected mode.
2. Add typed campaign pipeline/Realtime voice settings, persist them and preserve
   them across edits. Keep Realtime voices separate from standard TTS voices.
3. Complete and verify the action-claim validation before Realtime audio playback
   before removing the hard-coded block. Merely flipping the constant would
   restore the original unvalidated-speech problem.
4. Test the Realtime handshake and then an authorized browser/phone call through
   the normal orchestrator, checking the actual provider used and saved evidence.

## Limits

Production journal access was unavailable to the SSH user; sudo required a
password. No journal-based confirmation of a particular call's fallback is
claimed. The latest inspected browser test preceded the latest settings save,
and it had no matching `call_events` rows to prove provider choice. The root
cause above is established by the deployed source, current saved configuration,
running provider endpoint and executable mocked regression.

During the initial read-only audit, no new OpenAI session was created.
Subsequent implementation verification is recorded below. Official documentation confirms GPT Realtime 2 is a
Realtime model, so the model name itself is not the identified blocker:
https://developers.openai.com/api/docs/models/gpt-realtime-2


## Implemented candidate

Branch: `codex/realtime-independent-20261001`, based on production `a3ca1f72`.
Changes are isolated in `tmp/realtime-audit-20261001`; the original dirty checkout
was not modified or merged. No production deployment or customer call occurred.

- Moved provider adapters, bridge, prompts, personas, configuration, catalog,
  preview generation and runtime assembly under `backend/app/realtime/`.
- Grouped dashboard controls under `Talk-Leee/src/components/realtime/`.
- Added independent prompt/persona fields and campaign engine selection using
  existing JSONB columns; traditional prompts and provider settings stay separate.
- Replaced the blanket fallback block with bounded, completed-response validation
  before audio playback. Setup/disconnect errors report failure without selecting
  another engine. Interrupted/incomplete audio is withheld.
- Voice previews use the Realtime model. Catalog availability and preview access
  resolve the same tenant credential as calls.
- Browser playback now receives start, interruption and audio-completion controls;
  tool continuations wait for preceding playback without blocking event handling.

## Verification and limits of the candidate

The production host established an isolated session with the reorganized adapter
loaded in memory: `gpt-realtime-2`, Ash, low reasoning. A synthetic `Hello` request
returned 6,800 bytes of complete mu-law audio and a final transcript. No caller
audio, campaign call, app-file modification or credential output was involved.
This proves provider connectivity and the completed-response protocol for that
sample, not a complete inbound/outbound call or connector workflow.

The broad backend regression selection passed 394 tests before the final browser
playback-control wiring. Follow-up results are recorded with the final delivery.
Frontend TypeScript and focused ESLint passed; 14 focused frontend checks passed.

Full-response validation adds generation delay. Telephony gateways without a
playback acknowledgement retain contact details as pending rather than claiming
confirmation. A full live phone acceptance test remains necessary before release.


The attempted local page-level Playwright review did not complete: Next.js
compilation exceeded navigation/snapshot timeouts. The local server and named
browser session were stopped. No page-level visual/save-reload pass is claimed.
Component persistence/engine-switching tests passed; production UI remains unchanged.

Final follow-up after playback-control wiring: 82 backend tests passed, including
browser gateway regression coverage and playback control/flush/ack ordering.
