# AG01 audio profile evidence

Date: 2026-10-05. Worktree: `tmp/production-ready-20261004`, based on `7c0ffac7db5ea02baca152a91f291fc83e605f1a` plus the integrated AG01 changes. This is offline source and synthetic test evidence, not a production observation or live-provider acceptance result.

## Reproduced defects and bounded repairs

1. **Private ElevenLabs voice selection bypass.** Before repair, the catalog returned the unfiltered account list when ownership lookup failed; a known foreign clone ID passed campaign validation, cached preview returned without an ownership read, and runtime forwarded that voice ID. `voice_eligibility.py` now checks existing tenant ownership before ElevenLabs use. Missing ownership authority fails closed. Catalog, config save, campaign create/update/bulk apply, sample, preview, prefetch, test synthesis, primary runtime and configured fallback share this policy. Stock/public voices need positive provider metadata; absence from the ownership table alone is insufficient. A registered foreign clone remains denied even when provider sharing is public. Generic voice labels are not authorization evidence.
2. **Orphan clone lifecycle.** Uncertain provider deletion previously removed local ownership anyway. The delete endpoint now returns an unsuccessful result and retains ownership until provider absence is confirmed. Provider-created but unrecorded private clones are excluded by the same eligibility policy; no new ownership table or notification/recovery service was introduced. This does not promise recovery of an already orphaned provider resource.
3. **Saved STT selection ignored by cloud builders.** A synthetic saved Nova/es/900 ms configuration produced Flux/en/1500 ms in both Twilio and Vonage. Both now reuse `resolve_stt_selection` and async tenant tuning, preserving the existing non-English-to-Nova policy. Explicit unknown engines are rejected; existing Nova aliases and genuinely absent legacy defaults remain supported. The legacy `stt_model` field is derived from the selected engine. This does not claim those alternate cloud bridges are currently deployed or establish their separate inbound admission support.
4. **Invalid tuning accepted at save.** Cast-only coercion admitted NaN, infinity and out-of-range values. API validation rejects invalid known fields and validates the effective eager/EOT pair against the tenant's env/default base before replacing its saved override. Legacy malformed individual fields retain the documented log-and-skip behavior; an invalid final threshold pair is rejected before connection. Current app-supported ranges remain EOT 0.5–0.9, eager 0.3–0.9 or null, silence 500–10000 ms, turn-0 confidence 0–1, and alpha-character floor 1–10. Defaults were not changed. Flux-specific controls do not configure Nova acoustic endpointing.
5. **Unsupported ElevenLabs rate silently relabeled.** For a requested 32000 Hz, the old adapter serialized `pcm_24000` but labeled output chunks 32000 Hz. The regression failed first at the captured HTTP request. Initialization and synthesis now reject unsupported rates before provider I/O. Positive tests preserve model, voice and matching output rate at 8000, 16000, 22050, 24000 and 44100 Hz. Cartesia validates its documented raw PCM rates at initialization and payload construction: 8000, 16000, 22050, 24000, 44100 and 48000 Hz; 32000 is rejected. No new UI rate choices were added.

## Intentional effective transport values

| Path | Effective STT/TTS rate | Reason |
| --- | --- | --- |
| Main SIP and campaign browser builder | 16000 Hz | Existing mono PCM gateway contract |
| Twilio | 8000 Hz | Existing mu-law transport; STT consumes decoded linear16 |
| Vonage | 16000 Hz | Existing linear16 WebSocket transport |

A saved synthesis rate is preserved on save/reload but can differ from these effective transport rates. This repair retains the existing transport conversions. Google raw PCM requests are serialized at the effective rate; Cartesia/Google adapters emit float32 chunks to the pipeline, while Deepgram/ElevenLabs emit linear16 chunks. This evidence does not establish that a remote provider accepted a particular request or that a caller heard it correctly.

`audio-wire-profile.json` records sanitized real serializers behind fake transports: Flux connection parameters, Nova SDK connection arguments, Cartesia JSON, Deepgram TTS WebSocket query and Google streaming protobuf fields. The ElevenLabs parametrized test captures the actual HTTP request and yielded chunk metadata at every app-supported rate. The artifact includes source-defined voice IDs/names/languages (8 Cartesia fallback voices, 8 Google voices and 91 Deepgram entries across languages). Dynamic account catalogs, availability, and tenant clone IDs were not fetched. The app filters offered catalogs further, so this static inventory is not an account availability claim.

No new traditional speed control was introduced. Google continues serializing its existing 1.0 speaking rate; Cartesia omits generation speed when unset; ElevenLabs uses provider defaults except its existing v3 stability configuration. Existing Nova endpointing/operator environment settings remain unchanged. Native realtime settings are covered by the separate realtime agent evidence.

## Checks and reproduction

All tests used the existing original backend virtualenv on Windows/Python 3.12.12, from the isolated worktree's `backend` directory. Provider calls, clone mutations and downloads were mocked; no customer call, live provider request, database migration or production change occurred.

```powershell
$env:AG01_AUDIO_PROFILE_OUTPUT = '../docs/sessions/artifacts/ag01/audio-wire-profile.json'
& 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe' -m pytest tests/unit/test_audio_profile_contract.py tests/unit/test_voice_eligibility_boundaries.py tests/unit/test_voice_clone.py tests/unit/test_voice_tuning.py tests/unit/test_voice_tuning_db.py tests/unit/test_tenant_ai_config_isolation.py tests/unit/test_twilio_bridge.py tests/unit/test_orchestrator_credential_wiring.py tests/unit/test_orchestrator_failover_wiring.py tests/unit/test_campaign_create_direction.py tests/unit/test_legacy_campaign_direction_races.py tests/unit/test_two_model_pipeline.py tests/unit/test_elevenlabs_partial_audio_retry.py tests/unit/test_google_tts_streaming_hardening.py tests/unit/test_cartesia_bargein_context_isolation.py -q --disable-warnings
```

Result: **203 passed, 8 warnings, 10.40 s**, saved in `audio-tests.txt`. Pytest reported 8 warnings; this command suppressed their expanded details, and they were not counted as failures. Separate final fixture follow-up: `python -m pytest tests/unit/test_telephony_session_config.py -q --disable-warnings`, **68 passed, 1.89 s**, in `audio-builder-regression.txt`. Its MagicMock configs now explicitly declare the intended engine/language instead of generating arbitrary mock attributes. The existing source-string language check was replaced by actual session-builder behavior; media-format fallback controls remain intact.

CI-equivalent static check: `python -m ruff check app/ --select F --extend-ignore F401,F841` passed. Independent review of the completed voice authority changes found no further material gap in these changed paths. Broader integrated CI and live transport/provider qualification remain separate acceptance evidence.

## Primary reference checks

- [Deepgram Flux configuration](https://developers.deepgram.com/docs/flux/configuration): provider ranges and eager threshold ordering. The app intentionally retains its narrower existing supported subset.
- [Cartesia raw output schema](https://github.com/cartesia-ai/cartesia-python/blob/main/src/cartesia/types/raw_output_format_param.py): supported raw PCM rates. [WebSocket API](https://docs.cartesia.ai/api-reference/tts/websocket) documents the serialized output format.
- [ElevenLabs streaming TTS](https://elevenlabs.io/docs/api-reference/text-to-speech/stream): output format identifies codec and rate. [Voice metadata](https://elevenlabs.io/docs/api-reference/voices/get) supplies category and public-library sharing facts; runtime eligibility still requires tenant ownership for registered platform clones.
- [Google Text-to-Speech RPC reference](https://docs.cloud.google.com/text-to-speech/docs/reference/rpc/google.cloud.texttospeech.v1): streaming audio configuration and PCM encoding fields. No new provider model or feature was adopted.
