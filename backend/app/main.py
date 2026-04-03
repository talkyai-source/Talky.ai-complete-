"""
FastAPI Application Entry Point
"""
import asyncio
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
# Configure root logger so all app.* loggers emit DEBUG/INFO to console.
# Uvicorn only configures its own loggers; without this, app loggers
# default to WARNING and nothing is visible.
_log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.DEBUG),
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy third-party loggers
for _noisy in ("httpcore", "httpx", "hpack", "urllib3", "websockets"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan - startup and shutdown events.
    
    Startup:
    - Initializes ServiceContainer (Redis, PostgreSQL, Queue, Sessions)
    - Validates all provider configurations
    
    Shutdown:
    - Gracefully closes all services via container
    """
    from app.core.container import get_container
    
    # ========================
    # STARTUP
    # ========================
    logger.info("Starting AI Voice Dialer...")
    
    environment = os.getenv("ENVIRONMENT", "development")
    strict_validation = environment == "production"
    
    # Initialize service container
    container = get_container()
    try:
        await container.startup()
        app.state.container = container  # Make available via app.state
    except Exception as e:
        if strict_validation:
            logger.error(f"Container startup failed: {e}")
            raise
        logger.warning(f"Container startup warning: {e}")
    
    # Validate provider configurations
    try:
        from app.core.validation import validate_providers_on_startup
        validate_providers_on_startup(strict=strict_validation)
    except RuntimeError as e:
        if strict_validation:
            logger.error(f"Provider validation failed: {e}")
            raise
        logger.warning(f"Configuration warnings (non-fatal in {environment}): {e}")
    
    logger.info("AI Voice Dialer started successfully")

    # Auto-connect telephony bridge so campaigns can originate calls immediately
    from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
    from app.api.v1.endpoints import telephony_bridge as _tb

    _dialer_task = None
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

    # Start dialer worker as background asyncio task
    _dialer_worker = None
    try:
        from app.workers.dialer_worker import DialerWorker
        _dialer_worker = DialerWorker()
        _dialer_task = asyncio.create_task(_dialer_worker.run(), name="dialer-worker")

        def _on_dialer_done(task: asyncio.Task) -> None:
            """Log any unhandled exception from the dialer worker task."""
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.error("Dialer worker task exited with exception: %s", exc, exc_info=exc)
            elif task.cancelled():
                logger.info("Dialer worker task was cancelled")
            else:
                logger.warning("Dialer worker task exited unexpectedly (no exception)")

        _dialer_task.add_done_callback(_on_dialer_done)
        logger.info("Dialer worker started as background task")
    except Exception as e:
        logger.warning(f"Dialer worker failed to start (non-fatal): {e}")

    yield  # Application is running

    # ========================
    # SHUTDOWN
    # ========================
    logger.info("Shutting down AI Voice Dialer...")

    # Stop dialer worker
    if _dialer_task and not _dialer_task.done():
        try:
            _dialer_worker.running = False
            _dialer_task.cancel()
            await _dialer_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Dialer worker stopped")

    # Disconnect telephony bridge
    if _tb._adapter and _tb._adapter.connected:
        try:
            await _tb._adapter.disconnect()
            _tb._adapter = None
            logger.info("Telephony bridge disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting telephony bridge: {e}")

    try:
        await container.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

    logger.info("AI Voice Dialer shutdown complete")


app = FastAPI(
    title="AI Voice Dialer",
    description="Intelligent voice communication platform with AI agents",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware — restricted to known origins
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# MULTI-TENANT: Enabled
from app.core.tenant_middleware import TenantMiddleware
app.add_middleware(TenantMiddleware)

# Day 5: Session Security Middleware
from app.core.session_security_middleware import SessionSecurityMiddleware
app.add_middleware(SessionSecurityMiddleware)

# Day 6: API Security Middleware (request validation, security headers)
from app.core.api_security_middleware import APISecurityMiddleware
app.add_middleware(APISecurityMiddleware)

# Rate limiting — register the slowapi error handler
from app.api.v1.endpoints.auth import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "AI Voice Dialer API", "status": "running"}


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns basic health status and Redis connectivity.
    """
    from app.core.container import get_container
    
    health = {"status": "healthy"}
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
    """
    Prometheus scrape endpoint for WS-K telephony SLO metrics.

    Optional protection:
    - Set TELEPHONY_METRICS_TOKEN
    - Scraper must send matching X-Metrics-Token header.
    """
    if not is_metrics_request_authorized(x_metrics_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid metrics token",
        )

    from app.core.container import get_container

    container = get_container()
    if container.is_initialized:
        await refresh_telephony_slo_metrics(container.db_pool)

    return Response(
        content=render_prometheus_metrics(),
        media_type=prometheus_content_type(),
    )


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
