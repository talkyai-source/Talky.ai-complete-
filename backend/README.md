# AI Voice Dialer - Backend

## Overview
AI-powered voice dialer with traditional STT → LLM → TTS and separate native
Realtime engines. Existing interfaces provide provider boundaries, but session
assembly still selects concrete providers and shares lifecycle resources.

## Project Structure
```
backend/
├── app/
│   ├── core/              # Core framework (config, DI container)
│   ├── domain/            # Business services, session assembly and contracts
│   │   ├── models/        # Domain models
│   │   ├── services/      # Core services
│   │   └── interfaces/    # Provider interfaces (contracts)
│   ├── realtime/          # Native session protocols, prompts, tools and bridge
│   ├── infrastructure/    # Traditional and telephony provider implementations
│   │   ├── stt/          # Speech-to-Text providers
│   │   ├── tts/          # Text-to-Speech providers
│   │   ├── llm/          # Language Model providers
│   │   ├── telephony/    # Telephony providers
│   │   └── storage/      # Storage providers
│   ├── api/              # HTTP + WebSocket API
│   ├── workers/          # Background job processors
│   └── utils/            # Shared utilities
├── tests/                # Tests
├── config/               # Configuration files
└── requirements.txt      # Python dependencies
```

## Quick Start

### 1. Install Dependencies
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Access API
- API: http://localhost:8000
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Metrics: http://localhost:8000/metrics

## Runtime and configuration ownership

`app/core/container.py` owns shared application services. The traditional
`VoiceOrchestrator` also constructs STT/TTS providers and uses the LLM and media
gateway factories. Native assembly in `app/realtime/runtime.py` uses its own
provider protocols and prompt configuration, while sharing `VoiceSession`,
`VoiceSessionConfig`, media gateways and durable business services. Some existing
knowledge/action paths resolve dependencies through the container. The domain
folder is therefore not a strictly provider-independent layer.

Configuration is resolved for each entry path; `config/providers.yaml` is not the
sole authority for a tenant's live call:

| Source | Role and precedence |
| --- | --- |
| `tenant_ai_configs` | Stores a tenant's saved provider/model, engine, voice and tuning. `TenantAIConfigResolver` reads the tenant row without caching; `VoiceTuningResolver` resolves its separate tuning fields. Strict callers reject an unavailable lookup. A successful lookup with no saved row uses defaults. |
| Campaign configuration | `build_telephony_session_config` consumes an explicitly resolved AI profile plus campaign identity, voice and prompt settings. Supported campaign overrides take precedence for their fields. A native campaign is sent to `app/realtime/campaign_config.py` before traditional prompt composition. |
| Inbound admission snapshot | True inbound telephony constructs its session from the admitted campaign, tenant AI profile, tuning, opening and route snapshot. It must not substitute a newly fetched campaign midway through admission. |
| Process defaults and environment | `AIProviderConfig()` supplies the process default through `get_global_config`; environment-backed tuning and operational limits have separate readers. Defaults are not proof of a saved tenant selection. |
| `config/providers.yaml` | Supplies configuration to the consumers that read it, including cached Flux base/capture keyterms. Its `active` labels do not override all session builders. |
| Credentials | Resolve separately through the credential resolver using the owning tenant. A selected model, available credential and successfully opened provider connection are different checks. |

Known-tenant outbound prewarm and campaign browser tests require available AI and
tuning lookups. The legacy Twilio/Vonage helper also requires the AI lookup once
ownership resolves. Failed or unwired lookups stop these setup paths; a successful
lookup showing no saved row still permits defaults. This admission correction is
recorded in the [known-tenant profile report](../docs/sessions/2026-10-05-known-tenant-profile-admission.md).

Selected outbound sessions now survive long ringing while the provider channel
is live or its presence is unknown. A known outbound call that loses its prepared
session fails through the existing terminal path instead of rebuilding defaults;
duplicate and terminal callbacks cannot replace its selected owner. See the
[warmup lifecycle report](../docs/sessions/2026-10-05-selected-outbound-warmup-lifecycle.md).
This is process-local ownership, not restartable session recovery.

Legacy Twilio/Vonage callbacks and media paths are blocked in production even
when their legacy flags are enabled. Explicit nonproduction qualification remains
available. The cloud DID helper can still return tenantless defaults without
resolved ownership; a signed callback's destination alone does not establish the
outbound tenant. The current campaign worker uses SIP/Asterisk with a planned
call identity. This containment does not implement cloud campaign routing. The
dashboard cloud activation promise and saved-selection admission mismatch remain
under repair. Existing enabled cloud sessions must drain before rollout; see the
[production boundary report](../docs/sessions/2026-10-05-legacy-cloud-production-boundary.md).

Select an existing supported profile through AI Options, save and reload it, then
assign the intended engine/voice/prompt to the campaign. Confirm the effective
request/profile diagnostics on that exact call path before release. Native
Realtime has separate prompt and session controls; traditional temperature is
not a native temperature setting. Provider-specific validation and wire
serialization remain necessary; adding an arbitrary YAML provider name is not a
supported provider installation.

The traditional live turn is `TranscriptHandler / TurnEnder → TurnRunner →
TurnStreamer → TtsPlayback`. Native calls use `RealtimeBridge` and their selected
native protocol. Both use existing tenant-scoped knowledge and effect services.
An accepted tool request is not evidence that an external effect completed.

Live sockets, tasks, provider objects and transient contact state are not a
restartable call snapshot. Successfully committed durable records, including
transcript, Lead, DNC and action evidence, survive process loss; incomplete/unknown
outcomes remain explicit.
See the [current ownership diagram](docs/diagrams/message_flow.md) and the
[fixed production-readiness plan](../docs/production%20ready.md).

## Documentation

### Protocol Specifications
- **[WebSocket Protocol](docs/websocket_protocol.md)** - Complete WebSocket streaming protocol specification
- **[Message Flow Diagrams](docs/diagrams/message_flow.md)** - Sequence diagrams for all call flows
- **[Data Structures](docs/diagrams/data_structures.md)** - Binary formats and message schemas

### Provider changes
Use existing provider interfaces, implementations and factory registrations as
the code reference. The production-readiness feature freeze excludes adding new
providers or offerings; qualify the existing supported selections first.

## Development

### Run Tests
```bash
pytest
```

### Code Formatting
```bash
black app/
```

### Type Checking
```bash
mypy app/
```

## API Endpoints

### Campaigns
- `GET /api/v1/campaigns` - List campaigns
- `POST /api/v1/campaigns` - Create campaign
- `POST /api/v1/campaigns/{id}/start` - Start campaign
- `POST /api/v1/campaigns/{id}/pause` - Pause campaign

### Webhooks
- `POST /api/v1/webhooks/vonage/answer` - Vonage answer webhook
- `POST /api/v1/webhooks/vonage/event` - Vonage events webhook

### WebSocket
- `WS /api/v1/ws/voice/{call_id}` - Voice streaming

## License
MIT
