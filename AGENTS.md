# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

Talky.ai is an AI-powered voice dialer platform with three main components:

- **backend/** - FastAPI Python backend with modular, provider-agnostic architecture for STT, TTS, LLM, and telephony
- **Talk-Leee/** - Next.js 15 frontend application (user-facing dashboard)
- **Admin/** - Vite + React admin panel

## Development Commands

### Backend (Python/FastAPI)

```bash
# Setup
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Tests
pytest                    # Run all tests
pytest tests/unit/        # Unit tests only
pytest tests/integration/ # Integration tests only
pytest -k "test_name"     # Run specific test by name

# Code quality
black app/               # Format code
mypy app/                # Type checking
```

### Frontend (Talk-Leee, Next.js)

```bash
cd Talk-Leee
npm install

npm run dev              # Development server (port 3000)
npm run build            # Production build
npm run lint             # ESLint
npm run typecheck        # TypeScript checking
npm run test             # Unit tests (Node test runner with tsx)
npm run test:visual      # Playwright visual/e2e tests
npm run storybook        # Component development (port 6006)
npm run check-branding   # Check white-label branding
npm run docs:openapi     # Generate OpenAPI docs
```

### Admin Panel (Vite + React)

```bash
cd Admin/frontend
npm install

npm run dev              # Development server
npm run build            # TypeScript compile + Vite build
npm run lint             # ESLint
```

### Docker

```bash
# Start backend + Redis
docker-compose up -d

# Restart after env changes
docker-compose restart backend
```

## Architecture

### Backend Provider Pattern

The backend uses a provider-agnostic architecture. All external services implement abstract interfaces in `app/domain/interfaces/`:

- `stt_provider.py` - Speech-to-Text (Deepgram Flux)
- `tts_provider.py` - Text-to-Speech (Cartesia)
- `llm_provider.py` - Language Models (Groq)
- `telephony_provider.py` - Telephony (Vonage)

Provider implementations live in `app/infrastructure/{stt,tts,llm,telephony}/`. Switch providers by editing `config/providers.yaml` - no code changes required.

### Backend Service Container

`app/core/container.py` manages dependency injection and service lifecycle:
- Supabase client (storage)
- Redis client (sessions, queue)
- Queue service, Session manager, Call service

Access via `get_container()` or `app.state.container`.

### Backend Structure

```
backend/app/
├── api/v1/          # HTTP + WebSocket endpoints
├── core/            # Config, DI container, tenant middleware, validation
├── domain/
│   ├── interfaces/  # Abstract provider contracts
│   ├── models/      # Domain models
│   ├── services/    # Core business logic (provider-independent)
│   └── repositories/
├── infrastructure/  # Provider implementations (stt, tts, llm, telephony, storage)
├── services/        # Application services
├── workers/         # Background job processors
└── utils/           # Shared utilities
```

### Frontend Structure (Talk-Leee)

Next.js App Router with feature-based organization:

```
Talk-Leee/src/
├── app/             # Routes (ai-options, analytics, auth, calls, campaigns, etc.)
├── components/
│   ├── ui/          # Shadcn-style primitives
│   └── {feature}/   # Feature-specific components
└── lib/             # API client, auth, http-client, env utilities
```

API client (`src/lib/api.ts`) uses Zod schemas for response validation. Dev mode has auth stubs that bypass actual authentication.

### Multi-Tenant Architecture

The backend supports multi-tenancy via `TenantMiddleware` in `app/core/tenant_middleware.py`.

## Configuration

### Backend Environment Variables

Required API keys in `.env`:
- `DEEPGRAM_API_KEY` - STT
- `GROQ_API_KEY` - LLM
- `CARTESIA_API_KEY` - TTS
- `VONAGE_API_KEY`, `VONAGE_API_SECRET` - Telephony
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` - Storage
- `REDIS_HOST`, `REDIS_PORT` (optional)

See `.env.docker` for full list with defaults.

### Provider Configuration

Edit `config/providers.yaml` to switch active providers:

```yaml
providers:
  stt:
    active: "flux"  # or other STT provider
  tts:
    active: "cartesia"
  llm:
    active: "groq"
  telephony:
    active: "vonage"
```

## API

- Backend API: `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- WebSocket voice streaming: `WS /api/v1/ws/voice/{call_id}`

## Testing

### Backend

Tests in `backend/tests/` with pytest. Uses `pytest-asyncio` with auto mode. Third-party deprecation warnings are filtered in `pytest.ini`.

### Frontend

- Unit tests: Node test runner with `@testing-library/react`
- Visual/E2E tests: Playwright (runs dev server on port 3100)

## Key Documentation

- `backend/docs/websocket_protocol.md` - WebSocket streaming protocol
- `backend/docs/diagrams/` - Message flow and data structure diagrams
- `Admin/*.md` - Admin panel specs, API integration, security guides
- `white_label.md` - White-label customization guide
