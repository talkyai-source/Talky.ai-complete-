# AG01 native Realtime verification

Source: working tree based on `7c0ffac7`, with uncommitted AG01 repairs. No provider API requests, customer calls, production changes, or deployment occurred. The parent verification manifest supplies the final integrated revision.

## Repairs and evidence

- Campaign prompt precedence now honors explicit top-level campaign prompt, explicit nested campaign prompt, persisted campaign prompt, then account prompt. Partial settings no longer introduce a schema-default empty prompt. Actual campaign create, JSON reload, unrelated edit, nested edit, and runtime serializer tests cover the boundary.
- The initial prompt identity now includes the capability instructions actually sent. Later instruction submissions log a separate full digest. Neither log contains prompt text. Knowledge identity is a validated 64-hex snapshot checksum where available, otherwise explicitly unversioned.
- xAI's explicitly selected voice reaches its existing serializer. Known OpenAI voices, blank/padded identifiers, and invalid xAI settings fail validation before credentials or connection. Numeric VAD strings become numeric JSON fields; fractional durations and malformed values are refused. Existing pinned model `grok-voice-think-fast-1.0` is unchanged.
- Unsupported saved OpenAI voices now require reselection. The former automatic replacement with Marin was removed; ordinary defaults are unchanged.
- The single reasoning compatibility retry occurs only for structured `unknown_parameter` or `unsupported_parameter` on exactly `session.reasoning` or `session.reasoning.effort`. Auth, model, rate-limit, unrelated-field, unstructured errors and timeout never drop selected reasoning or retry. Requested, submitted and provider-echoed settings are separate; omitted echo fields stay unknown.
- OpenAI noise reduction off is explicitly `null`. The earlier omission could match a fresh-session default, so this is contract precision rather than proof of a prior live denoising failure. OpenAI stored reasoning `none` means omit/provider default (the UI uses that label); xAI `none` is an explicit wire value. Temperature is inapplicable to this native path.
- API-handler save → actual SQL serialization → fake storage → API-handler reload → campaign assignment → runtime → real serializer covers all eight offered OpenAI voices. Two xAI profiles are included as hidden opt-in configurations, not new advertised offerings. The preview handler is also tested with fake native audio and credential/unknown-voice failures, without TTS substitution.

## Evidence limits

`native-realtime-profiles.json` contains ten sanitized profiles, codec/rate, prompt version and full digest, exact serialized controls/tool names, and synthetic acknowledgement status. It does not contain raw instructions, API keys, audio or customer information. Synthetic acknowledgements prove serializer behavior, not live provider acceptance, quality, latency or account availability. SQL transport is fake: this slice is not a database/RLS acceptance test. No pinned KB snapshot is present in the assembly fixtures. Pinned xAI 1.0 availability is unverified; current xAI documentation names a newer model, and this work does not migrate it. Legacy xAI rows storing Marin need an explicit xAI voice selection.

## Primary references

- OpenAI Realtime session reference: https://platform.openai.com/docs/api-reference/realtime?lang=javascript — noise reduction supports explicit null.
- OpenAI Realtime conversations: https://developers.openai.com/api/docs/guides/realtime-conversations
- OpenAI VAD: https://developers.openai.com/api/docs/guides/realtime-vad
- OpenAI Realtime API guidance: https://developers.openai.com/blog/realtime-api
- xAI speech-to-speech: https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech

The compatibility retry is a conservative application rule, not a claim that every provider error schema or model supports reasoning. Unclassified rejection remains a failure.
