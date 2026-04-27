"""
FastAPI Application Entry Point
"""
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.dotenv_compat import load_dotenv

# Load backend .env regardless of current working directory.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=_BACKEND_ROOT / ".env", override=False)

from app.api.v1.routes import api_router
from app.core.config import ConfigManager, get_settings
from app.core.telephony_observability import (
    is_metrics_request_authorized,
    prometheus_content_type,
    refresh_telephony_slo_metrics,
    render_prometheus_metrics,
)

# ── Logging ──────────────────────────────────────────────────────
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()   # was DEBUG — changed to INFO for prod
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy third-party loggers
for _noisy in ("httpcore", "httpx", "hpack", "urllib3", "websockets", "opentelemetry", "groq._base_client", "groq"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan - startup and shutdown events.

    Startup order:
      1. OpenTelemetry (must come first — instruments everything below)
      2. ServiceContainer (Redis, PostgreSQL, Queue, Sessions)
      3. Provider configuration validation

    Shutdown order (reverse):
      3. ServiceContainer graceful drain
      2. OTel flush (must come last — ensures all spans are exported)
    """
    from app.core.container import get_container
    from app.core.prod_gate import enforce_production_gate
    from app.core.sentry_init import init_sentry
    from app.core.telemetry import setup_telemetry, shutdown_telemetry

    environment = os.getenv("ENVIRONMENT", "development")
    strict_validation = environment == "production"

    # ── 0. Production gate (T0.2 + T0.3) ─────────────────────────
    # Refuse to boot in production if any obvious fatal misconfig is
    # present — dev-bypass flags still set, default PBX passwords,
    # missing JWT_SECRET, mock-mode billing, etc. Fail LOUD before the
    # service container brings Redis/DB up; no silent "mostly working"
    # production deploys.
    enforce_production_gate()

    # ── 0.5. Sentry (T2.3) ───────────────────────────────────────
    # Before FastAPI middleware / OTEL so Sentry's integrations see
    # every request. No-op when SENTRY_DSN is unset.
    init_sentry()

    # ── 1. OpenTelemetry ─────────────────────────────────────────
    # Must be set up BEFORE the container so that asyncpg and Redis
    # auto-instrumentation patches are in place before first use.
    setup_telemetry(app)

    # ── 2. Service container ──────────────────────────────────────
    logger.info("Starting Talky.ai AI Voice Dialer...")
    container = get_container()
    try:
        await container.startup()
        app.state.container = container
    except Exception as e:
        if strict_validation:
            logger.error(f"Container startup failed: {e}")
            raise
        logger.warning(f"Container startup warning: {e}")

    # ── 2.5. Redis durability probe (T2.4) ──────────────────────
    # Loud WARN in prod when both AOF and RDB are off — dialer jobs
    # would vanish on any Redis restart. Non-fatal: an operator might
    # intentionally be running a cache-only Redis, in which case they
    # can set the env.
    try:
        from app.core.redis_durability import probe_redis_durability
        redis_client = getattr(container, "redis", None)
        durability = await probe_redis_durability(redis_client)
        app.state.redis_durability = durability
    except Exception as exc:
        logger.warning("redis_durability_probe_raised err=%s", exc)

    # ── 2.6. Legacy-campaign audit (T2.6) ───────────────────────
    # Count campaigns still falling through to the hardcoded
    # estimation prompt. Loud WARN in prod when any are present so
    # operators can migrate before we delete the fallback.
    try:
        from app.core.legacy_campaign_audit import (
            audit_legacy_campaigns,
            log_audit_summary,
        )
        result = await audit_legacy_campaigns(getattr(container, "db_pool", None))
        log_audit_summary(result)
        app.state.legacy_campaign_audit = result
    except Exception as exc:
        logger.debug("legacy_campaign_audit_raised err=%s", exc)

    # ── 3. Provider validation ────────────────────────────────────
    try:
        from app.core.validation import validate_providers_on_startup
        validate_providers_on_startup(strict=strict_validation)
    except RuntimeError as e:
        if strict_validation:
            logger.error(f"Provider validation failed: {e}")
            raise
        logger.warning(f"Configuration warnings (non-fatal in {environment}): {e}")

    logger.info("Talky.ai started successfully")

    # ── 4. Restore saved AI config ────────────────────────────────
    # Load the most-recently saved tenant config from DB so the global AI
    # config (TTS provider, voice, LLM model) survives server restarts and
    # hot-reloads without requiring the user to re-visit AI Options first.
    try:
        from app.domain.services.global_ai_config import set_global_config
        from app.domain.models.ai_config import AIProviderConfig
        db_client = container.db_client
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT llm_provider, llm_model, llm_temperature, llm_max_tokens,
                       stt_provider, stt_model, stt_language,
                       tts_provider, tts_model, tts_voice_id, tts_sample_rate
                FROM tenant_ai_configs
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
                """
            )
        if row:
            saved = AIProviderConfig(
                llm_provider=row["llm_provider"],
                llm_model=row["llm_model"],
                llm_temperature=row["llm_temperature"],
                llm_max_tokens=row["llm_max_tokens"],
                stt_provider=row["stt_provider"],
                stt_model=row["stt_model"],
                stt_language=row["stt_language"],
                tts_provider=row["tts_provider"],
                tts_model=row["tts_model"],
                tts_voice_id=row["tts_voice_id"],
                tts_sample_rate=row["tts_sample_rate"],
            )
            set_global_config(saved)
            logger.info(
                "AI config restored from DB: tts=%s voice=%s llm=%s",
                saved.tts_provider, saved.tts_voice_id, saved.llm_model,
            )
    except Exception as exc:
        logger.warning("Could not restore AI config from DB (using defaults): %s", exc)

    # Auto-connect telephony bridge so campaigns can originate calls immediately.
    # Must happen after container startup (needs event loop to be running).
    from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
    from app.api.v1.endpoints import telephony_bridge as _tb

    try:
        if not (_tb._adapter and _tb._adapter.connected):
            adapter_type = os.getenv("TELEPHONY_ADAPTER", "auto")
            _tb._adapter = await CallControlAdapterFactory.create(adapter_type)
            _tb._adapter.register_call_event_handlers(
                on_new_call=_tb._on_new_call,
                on_call_ended=_tb._on_call_ended,
                on_audio_received=_tb._on_audio_received,
            )
            if hasattr(_tb._adapter, "set_global_session_start_callback"):
                _tb._adapter.set_global_session_start_callback(_tb._on_ws_session_start)
            await _tb._adapter.connect()
            logger.info(f"Telephony bridge auto-connected: {_tb._adapter.name}")
        else:
            logger.info("Telephony bridge already connected — skipping auto-connect")
    except Exception as e:
        logger.warning(f"Telephony bridge auto-connect failed (non-fatal): {e}")

    yield

    # Disconnect telephony bridge on shutdown.
    # FIX 5 — End active voice sessions first so recordings are saved and the PBX
    # receives a hangup signal.  Without this, callers hear abrupt disconnect and
    # the PBX holds channels open until its own ringing/idle timeout.
    if _tb._telephony_sessions:
        logger.info(
            "Shutdown: ending %d active telephony session(s) gracefully",
            len(_tb._telephony_sessions),
        )
        for call_id in list(_tb._telephony_sessions.keys()):
            try:
                await _tb._on_call_ended(call_id)
            except Exception as shutdown_err:
                logger.warning(
                    "Shutdown: error ending call %s: %s", call_id[:12], shutdown_err
                )

    if _tb._adapter and _tb._adapter.connected:
        try:
            await _tb._adapter.disconnect()
            _tb._adapter = None
            logger.info("Telephony bridge disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting telephony bridge: {e}")

    # ── Shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down Talky.ai...")
    try:
        await container.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

    # Flush all pending OTel spans before the process exits
    shutdown_telemetry()
    logger.info("Talky.ai shutdown complete")


app = FastAPI(
    title="Talky.ai — AI Voice Dialer",
    description="Intelligent voice communication platform with AI agents",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware stack (order matters — outermost first) ────────────
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

from app.core.tenant_middleware import TenantMiddleware
app.add_middleware(TenantMiddleware)

from app.core.session_security_middleware import SessionSecurityMiddleware
app.add_middleware(SessionSecurityMiddleware)

from app.core.api_security_middleware import APISecurityMiddleware
app.add_middleware(APISecurityMiddleware)

from app.api.v1.endpoints.auth import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routes ────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "Talky.ai API", "status": "running"}


@app.get("/health")
async def health_check():
    from app.core.container import get_container
    health: dict = {"status": "healthy"}
    container = get_container()
    if container.is_initialized:
        health["container"] = "initialized"
        health["redis_enabled"] = container.redis_enabled
        if container._session_manager:
            health["active_sessions"] = container.session_manager.get_active_session_count()
    else:
        health["container"] = "not_initialized"
    return health


@app.get("/metrics")
async def prometheus_metrics(
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token")
):
    """Prometheus scrape endpoint for telephony SLO metrics."""
    if not is_metrics_request_authorized(x_metrics_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token")
    from app.core.container import get_container
    container = get_container()
    if container.is_initialized:
        await refresh_telephony_slo_metrics(container.db_pool)
    return Response(content=render_prometheus_metrics(), media_type=prometheus_content_type())


if __name__ == "__main__":
    import uvicorn
    websocket_config = ConfigManager().get_websocket_config()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws="websockets",
        ws_ping_interval=float(websocket_config.get("heartbeat_interval_seconds", 30)),
        ws_ping_timeout=float(websocket_config.get("heartbeat_timeout_seconds", 5)),
    )
