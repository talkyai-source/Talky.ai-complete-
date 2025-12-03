# AI Voice Dialer - Backend

## Overview
AI-powered voice dialer with modular, provider-agnostic architecture. Easily swap STT, TTS, LLM, and telephony providers without changing core business logic.

## Project Structure
```
backend/
├── app/
│   ├── core/              # Core framework (config, DI container)
│   ├── domain/            # Business logic (provider-independent)
│   │   ├── models/        # Domain models
│   │   ├── services/      # Core services
│   │   └── interfaces/    # Provider interfaces (contracts)
│   ├── infrastructure/    # Provider implementations
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

## Architecture Principles

### 1. Provider Pattern
All external services (STT, TTS, LLM, Telephony) implement abstract interfaces defined in `app/domain/interfaces/`.

### 2. Dependency Injection
Services receive dependencies rather than creating them, managed by the DI container in `app/core/container.py`.

### 3. Configuration-Driven
Providers are selected via `config/providers.yaml`. Change providers by editing config, not code.

### 4. Zero Core Logic Impact
Business logic in `app/domain/services/` is completely independent of provider implementations.

## Switching Providers

### Example: Switch from Deepgram to Whisper for STT

1. Edit `config/providers.yaml`:
```yaml
providers:
  stt:
    active: "whisper"  # Changed from "deepgram"
```

2. Ensure API key is set in `.env`:
```bash
OPENAI_API_KEY=sk-...
```

3. Restart server:
```bash
uvicorn app.main:app --reload
```

That's it! No code changes required.

## Adding New Providers

See `docs/provider-guide.md` for detailed instructions on implementing new providers.

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
