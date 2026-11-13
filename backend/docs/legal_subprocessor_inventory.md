# Subprocessor inventory — source evidence for legal

Generated from source registrations; **not proof of deployment** or contractual compliance. Supplier labels are product/vendor names, not verified legal entity names. Owner/legal review must establish active use, legal entity, DPA/SCC terms, subprocessors, data categories, region, retention and deletion arrangements before publication.

## Registered supplier candidates

| Supplier label | Registration identifiers | Families |
|---|---|---|
| AssemblyAI | assemblyai | STTFactory |
| Cartesia | cartesia | TTSFactory |
| Cerebras | cerebras | LLMFactory |
| DeepSeek | deepseek | LLMFactory |
| Deepgram | deepgram, deepgram-flux, deepgram-nova, deepgram_flux, deepgram_nova, flux, nova, nova-2, nova-3 | STTFactory |
| ElevenLabs | elevenlabs | TTSFactory |
| Google | gemini, gmail, google, google-streaming, google_calendar, google_drive | ConnectorFactory, LLMFactory, TTSFactory |
| Groq | groq | LLMFactory |
| HubSpot | hubspot | ConnectorFactory |
| Microsoft (Graph) | outlook_calendar | ConnectorFactory |
| OpenAI | openai | LLMFactory |
| Salesforce | salesforce | ConnectorFactory |
| Twilio | twilio | TelephonyProviderFactory |
| Vonage | vonage | TelephonyProviderFactory |

## Registration evidence

These are source registration sites, not a runtime registry dump. Conditional sites depend on imports/control flow; unconditional source registration also does not prove a provider is selected. `auto` is selection logic; `sip` is transport and requires an owner inventory of every actual carrier.

| Factory / identifier | Conditional source site | Potential processing category | Evidence |
|---|---|---|---|
| ConnectorFactory / gmail | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/email/gmail.py:525](../../backend/app/infrastructure/connectors/email/gmail.py#L525) |
| ConnectorFactory / google_calendar | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/calendar/google_calendar.py:445](../../backend/app/infrastructure/connectors/calendar/google_calendar.py#L445) |
| ConnectorFactory / google_drive | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/drive/google_drive.py:375](../../backend/app/infrastructure/connectors/drive/google_drive.py#L375) |
| ConnectorFactory / hubspot | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/crm/hubspot.py:370](../../backend/app/infrastructure/connectors/crm/hubspot.py#L370) |
| ConnectorFactory / outlook_calendar | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/calendar/outlook_calendar.py:428](../../backend/app/infrastructure/connectors/calendar/outlook_calendar.py#L428) |
| ConnectorFactory / salesforce | no | Authorized connector account and selected CRM/calendar/mail/file data | [backend/app/infrastructure/connectors/crm/salesforce.py:679](../../backend/app/infrastructure/connectors/crm/salesforce.py#L679) |
| LLMFactory / cerebras | yes | Conversation text, prompts and requested model output | [backend/app/infrastructure/llm/factory.py:52](../../backend/app/infrastructure/llm/factory.py#L52) |
| LLMFactory / deepseek | yes | Conversation text, prompts and requested model output | [backend/app/infrastructure/llm/factory.py:58](../../backend/app/infrastructure/llm/factory.py#L58) |
| LLMFactory / gemini | yes | Conversation text, prompts and requested model output | [backend/app/infrastructure/llm/factory.py:46](../../backend/app/infrastructure/llm/factory.py#L46) |
| LLMFactory / groq | yes | Conversation text, prompts and requested model output | [backend/app/infrastructure/llm/factory.py:40](../../backend/app/infrastructure/llm/factory.py#L40) |
| LLMFactory / openai | no | Conversation text, prompts and requested model output | [backend/app/infrastructure/llm/factory.py:36](../../backend/app/infrastructure/llm/factory.py#L36) |
| STTFactory / assemblyai | no | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:50](../../backend/app/infrastructure/stt/factory.py#L50) |
| STTFactory / deepgram | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:66](../../backend/app/infrastructure/stt/factory.py#L66) |
| STTFactory / deepgram-flux | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:55](../../backend/app/infrastructure/stt/factory.py#L55) |
| STTFactory / deepgram-nova | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:76](../../backend/app/infrastructure/stt/factory.py#L76) |
| STTFactory / deepgram_flux | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:56](../../backend/app/infrastructure/stt/factory.py#L56) |
| STTFactory / deepgram_nova | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:77](../../backend/app/infrastructure/stt/factory.py#L77) |
| STTFactory / flux | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:59](../../backend/app/infrastructure/stt/factory.py#L59) |
| STTFactory / nova | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:79](../../backend/app/infrastructure/stt/factory.py#L79) |
| STTFactory / nova-2 | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:67](../../backend/app/infrastructure/stt/factory.py#L67) |
| STTFactory / nova-3 | yes | Call audio and transcription results | [backend/app/infrastructure/stt/factory.py:78](../../backend/app/infrastructure/stt/factory.py#L78) |
| TTSFactory / cartesia | yes | Synthesis text and resulting audio | [backend/app/infrastructure/tts/factory.py:37](../../backend/app/infrastructure/tts/factory.py#L37) |
| TTSFactory / elevenlabs | yes | Synthesis text and resulting audio | [backend/app/infrastructure/tts/factory.py:58](../../backend/app/infrastructure/tts/factory.py#L58) |
| TTSFactory / google | yes | Synthesis text and resulting audio | [backend/app/infrastructure/tts/factory.py:44](../../backend/app/infrastructure/tts/factory.py#L44) |
| TTSFactory / google-streaming | yes | Synthesis text and resulting audio | [backend/app/infrastructure/tts/factory.py:51](../../backend/app/infrastructure/tts/factory.py#L51) |
| TelephonyProviderFactory / auto | no | Selection mode / customer-configured carrier; no supplier inferred | [backend/app/infrastructure/telephony/provider_factory.py:278](../../backend/app/infrastructure/telephony/provider_factory.py#L278) |
| TelephonyProviderFactory / sip | no | Selection mode / customer-configured carrier; no supplier inferred | [backend/app/infrastructure/telephony/provider_factory.py:278](../../backend/app/infrastructure/telephony/provider_factory.py#L278) |
| TelephonyProviderFactory / twilio | no | Call signalling, telephone identifiers and media | [backend/app/infrastructure/telephony/provider_factory.py:278](../../backend/app/infrastructure/telephony/provider_factory.py#L278) |
| TelephonyProviderFactory / vonage | no | Call signalling, telephone identifiers and media | [backend/app/infrastructure/telephony/provider_factory.py:278](../../backend/app/infrastructure/telephony/provider_factory.py#L278) |

## Supplemental integrations outside those factories

This is an explicitly bounded source review, not an exhaustive supplier registry. Presence below does not establish enablement. Hosting, customer-selected carriers, SMTP/storage endpoints, extensions and downstream sub-subprocessors require owner confirmation. A commented SMTP ConnectorFactory registration is not a registered connector.

| Candidate / source seam | Review limitation | Evidence |
|---|---|---|
| AWS KMS | Optional key-management backend; deployment and region are not inspected. | [backend/app/core/kms.py:116](../../backend/app/core/kms.py#L116) |
| Anthropic credential check (anthropic) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:25](../../backend/app/domain/services/provider_key_validation.py#L25) |
| AssemblyAI credential check (assemblyai) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:31](../../backend/app/domain/services/provider_key_validation.py#L31) |
| Cartesia credential check (cartesia) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:27](../../backend/app/domain/services/provider_key_validation.py#L27) |
| Cerebras credential check (cerebras) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:20](../../backend/app/domain/services/provider_key_validation.py#L20) |
| Cloudflare Turnstile | Optional CAPTCHA validation; deployment and transferred data require owner review. | [backend/app/core/security/auth_abuse.py:161](../../backend/app/core/security/auth_abuse.py#L161) |
| Configured S3-compatible storage | Recording object storage supports a custom endpoint. Do not infer AWS from the S3 protocol. | [backend/app/domain/services/recording_service.py:281](../../backend/app/domain/services/recording_service.py#L281) |
| Configured SMTP provider | Account email sender; source default is Microsoft 365. Actual host/account and message retention require owner confirmation. | [backend/app/domain/services/email_service.py:48](../../backend/app/domain/services/email_service.py#L48) |
| DeepSeek credential check (deepseek) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:22](../../backend/app/domain/services/provider_key_validation.py#L22) |
| Deepgram credential check (deepgram) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:26](../../backend/app/domain/services/provider_key_validation.py#L26) |
| ElevenLabs credential check (elevenlabs) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:29](../../backend/app/domain/services/provider_key_validation.py#L29) |
| Google credential check (gemini) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:24](../../backend/app/domain/services/provider_key_validation.py#L24) |
| Groq credential check (groq) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:19](../../backend/app/domain/services/provider_key_validation.py#L19) |
| Hetzner deployment documentation | Repository environment claim only; confirm current host, legal entity, region and subcontractors with the owner. | [CLAUDE.md:59](../../CLAUDE.md#L59) |
| Legacy AWS SES helper | Private legacy helper only; active account-mail routing must be verified independently. | [backend/app/domain/services/notification_service.py:221](../../backend/app/domain/services/notification_service.py#L221) |
| Legacy SendGrid helper | Private legacy helper only. Current account-mail dispatch delegates to EmailService; this is not an active-supplier assertion. | [backend/app/domain/services/notification_service.py:131](../../backend/app/domain/services/notification_service.py#L131) |
| OpenAI credential check (openai) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:21](../../backend/app/domain/services/provider_key_validation.py#L21) |
| Sentry | Optional telemetry initialization; enabled deployment, redaction and retention require separate verification. | [backend/app/core/sentry_init.py:94](../../backend/app/core/sentry_init.py#L94) |
| Slack | Optional webhook notification seam; enabled use and payload/retention require owner confirmation. | [backend/app/domain/services/notification_service.py:282](../../backend/app/domain/services/notification_service.py#L282) |
| Stripe | Billing API integration; owner must establish the active merchant account and data/contract scope. | [backend/app/domain/services/billing_checkout.py:218](../../backend/app/domain/services/billing_checkout.py#L218) |
| Supabase compatibility | Compatibility storage path remains in source; neither active nor unused is established. | [backend/app/domain/services/recording_service.py:693](../../backend/app/domain/services/recording_service.py#L693) |
| Vercel deployment descriptor | Repository hosting descriptor only; actual deployment, contracting entity and hosting scope are unverified. | [Admin/frontend/vercel.json:2](../../Admin/frontend/vercel.json#L2) |
| xAI credential check (xai) | Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness. | [backend/app/domain/services/provider_key_validation.py:23](../../backend/app/domain/services/provider_key_validation.py#L23) |

## Code and privacy-policy discrepancies

- **gmail_body_access**: Policy says never email body content; get_email requests format=full and parses body/body_html. Metadata-only list previews do not remove this path. Code: [backend/app/infrastructure/connectors/email/gmail.py:338](../../backend/app/infrastructure/connectors/email/gmail.py#L338), [backend/app/infrastructure/connectors/email/gmail.py:339](../../backend/app/infrastructure/connectors/email/gmail.py#L339), [backend/app/infrastructure/connectors/email/gmail.py:351](../../backend/app/infrastructure/connectors/email/gmail.py#L351), [backend/app/infrastructure/connectors/email/gmail.py:502](../../backend/app/infrastructure/connectors/email/gmail.py#L502), [backend/app/infrastructure/connectors/email/gmail.py:503](../../backend/app/infrastructure/connectors/email/gmail.py#L503). Policy: [Talk-Leee/src/app/privacy/privacy-content.ts:255](../../Talk-Leee/src/app/privacy/privacy-content.ts#L255).
- **drive_read_scope**: Policy limits access to app-created files; the requested drive.readonly scope permits reading beyond that set (alongside drive.file). Actual granted scopes are unverified. Code: [backend/app/infrastructure/connectors/drive/google_drive.py:48](../../backend/app/infrastructure/connectors/drive/google_drive.py#L48). Policy: [Talk-Leee/src/app/privacy/privacy-content.ts:123](../../Talk-Leee/src/app/privacy/privacy-content.ts#L123), [Talk-Leee/src/app/privacy/privacy-content.ts:256](../../Talk-Leee/src/app/privacy/privacy-content.ts#L256).

Gmail body retention and downstream copies are **not established** by this inventory. Legal must review tool/conversation storage and actual granted OAuth scopes separately. The policy text is unchanged; neither scope reduction nor legal publication is performed here.

## Unresolved registrations

None within the parsed registration forms. New unknown/dynamic identities refuse document write/check until reviewed.

## Reproduce and review

From the repository root:

```text
python backend/scripts/generate_subprocessor_inventory.py
python backend/scripts/generate_subprocessor_inventory.py --check
python backend/scripts/generate_subprocessor_inventory.py --write
```

Default prints to stdout. --write changes only this document; --check detects drift. The generator uses Python AST and fixed source reads, never application imports, settings, dotenv, provider requests or subprocesses. No credentials are read or printed.

Coverage limits: named register calls in the three factory files and connector Python modules, the literal telephony list, the optional fixed credential-check map, and the listed supplemental source seams. Aliased factories, runtime monkey patches, plugin discovery and arbitrary external services are not proven covered. Unknown values in recognized registrations fail closed; new integration mechanisms need a separate review. Data categories describe potential code functionality, not verified deployed flows.

Owner/legal must reconcile this with the current privacy policy and Terms, including the QA12 questions about calling hours, recording defaults, billing timing, deletion/backups, DPA availability, trials and uptime. No DPA, SCC, residency, supplier contract, retention promise or production configuration has been verified by this tool.
