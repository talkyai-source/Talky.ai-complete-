# Current-to-Target Code Migration Map

This file maps existing backend code to the target production telephony architecture.

## Rule

- No hard deletions in early phases.
- First: add new path and route traffic gradually.
- Then: deprecate old path.
- Finally: remove old code after stability window.

## Keep (Core)

These remain primary:

- `backend/app/domain/services/voice_orchestrator.py`
- `backend/app/domain/services/voice_pipeline_service.py`
- `backend/app/infrastructure/stt/deepgram_flux.py`
- `backend/app/infrastructure/tts/deepgram_tts.py`
- `backend/app/infrastructure/llm/groq.py`
- `backend/app/core/postgres_adapter.py`

## Keep and Refactor (Telephony Integration)

- `backend/app/api/v1/endpoints/freeswitch_bridge.py`
  - Keep endpoint family
  - Refactor to provider-neutral call identifiers and transfer APIs

- `backend/app/infrastructure/telephony/freeswitch_esl.py`
  - Keep as FreeSWITCH call-control client
  - Extend attended transfer and bridge outcome handling

- `backend/app/infrastructure/telephony/freeswitch_audio_bridge.py`
  - Keep bidirectional media bridge
  - Add stronger backpressure and jitter-safe buffering

- `backend/app/infrastructure/telephony/factory.py`
  - Keep factory pattern
  - Move toward explicit `freeswitch` first-class gateway path

## Deprecate (Later Remove)

- `backend/app/infrastructure/telephony/vonage_media_gateway.py`
- `backend/app/api/v1/endpoints/webhooks.py` (voice-specific Vonage path only)
- `backend/config/vonage_private.key.example`

Note: Vonage SMS connector can remain if SMS still needed.

## Evaluate Usage Before Decision

- `backend/app/infrastructure/telephony/sip_media_gateway.py`
- `backend/app/infrastructure/telephony/rtp_media_gateway.py`
- `backend/config/sip_config.yaml`
- `backend/app/workers/voice_worker.py`

If these are not in active production path after cutover, deprecate and remove.

## Naming Cleanup (Provider Neutral)

- `backend/app/domain/models/session.py`
  - Rename `vonage_call_uuid` -> `provider_call_id`
  - Keep alias/backward compatibility in one release cycle

- `backend/app/core/validation.py`
  - Remove hard requirement that telephony must be Vonage-only
  - Validate based on active telephony provider

- `backend/config/providers.yaml`
  - Move telephony defaults from fixed Vonage assumptions to migration profile

## New APIs to Add

- Transfer endpoint(s), e.g.:
  - `POST /api/v1/sip/freeswitch/transfer/blind`
  - `POST /api/v1/sip/freeswitch/transfer/attended/start`
  - `POST /api/v1/sip/freeswitch/transfer/attended/complete`
  - `POST /api/v1/sip/freeswitch/transfer/cancel`

- Tenant trunk provisioning endpoints (later phase)

## Test Plan Alignment

Keep and extend:

- `backend/tests/unit/test_voice_orchestrator.py`
- `backend/tests/unit/test_sip_bridge_api.py`
- `backend/tests/unit/test_latency_tracker.py`
- `backend/tests/integration/test_flux_pipeline.py`

Add:

- transfer success/failure tests
- attended transfer lifecycle tests
- regression for barge-in during transfer
- p95 latency budget tests under load
