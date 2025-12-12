"""
FastAPI Application Entry Point
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.routes import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan - startup and shutdown events.
    
    Startup:
    - Validates all provider configurations
    - Initializes session manager (Redis/in-memory)
    
    Shutdown:
    - Gracefully closes all sessions
    - Closes Redis connection
    """
    # ========================
    # STARTUP
    # ========================
    logger.info("Starting AI Voice Dialer...")
    
    # Validate provider configurations
    environment = os.getenv("ENVIRONMENT", "development")
    strict_validation = environment == "production"
    
    try:
        from app.core.validation import validate_providers_on_startup
        validate_providers_on_startup(strict=strict_validation)
    except RuntimeError as e:
        if strict_validation:
            logger.error(f"Startup failed: {e}")
            raise
        else:
            logger.warning(f"Configuration warnings (non-fatal in {environment}): {e}")
    
    # Initialize session manager
    try:
        from app.domain.services.session_manager import SessionManager
        session_manager = await SessionManager.get_instance()
        logger.info(f"SessionManager initialized (Redis: {session_manager._redis_enabled})")
    except RuntimeError as e:
        if strict_validation:
            raise
        logger.warning(f"SessionManager initialization warning: {e}")
    
    logger.info("AI Voice Dialer started successfully")
    
    yield  # Application is running
    
    # ========================
    # SHUTDOWN
    # ========================
    logger.info("Shutting down AI Voice Dialer...")
    
    try:
        from app.domain.services.session_manager import SessionManager
        session_manager = await SessionManager.get_instance()
        await session_manager.shutdown()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    
    logger.info("AI Voice Dialer shutdown complete")


app = FastAPI(
    title="AI Voice Dialer",
    description="Intelligent voice communication platform with AI agents",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MULTI-TENANT: Enabled
from app.core.tenant_middleware import TenantMiddleware
app.add_middleware(TenantMiddleware)

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
    health = {"status": "healthy"}
    
    try:
        from app.domain.services.session_manager import SessionManager
        session_manager = await SessionManager.get_instance()
        health["redis_enabled"] = session_manager._redis_enabled
        health["active_sessions"] = session_manager.get_active_session_count()
    except Exception as e:
        health["session_manager"] = f"error: {str(e)}"
    
    return health


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
