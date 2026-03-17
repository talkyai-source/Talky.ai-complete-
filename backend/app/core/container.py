"""
Dependency Injection Container
Manages creation and lifecycle of all services and providers.

Uses asyncpg-backed PostgreSQL connections.

Usage:
    from app.core.container import get_container

    # In startup:
    container = get_container()
    await container.startup()

    # Access services:
    db_pool = container.db_pool
    queue = container.queue_service

    # In shutdown:
    await container.shutdown()
"""
import os
import logging
from typing import Optional
from functools import lru_cache

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

import asyncpg
from app.core.db import init_db_pool, close_db_pool
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class ServiceContainer:
    """
    Central container for all application services.

    Provides:
    - Singleton service instances
    - Async startup/shutdown lifecycle
    - Proper resource cleanup

    Services managed:
    - asyncpg connection pool
    - Redis client (optional, for queue/session)
    - Queue service (DialerQueueService)
    - Session manager (SessionManager)
    """

    def __init__(self):
        """Initialize container - services are created lazily on startup()."""
        self._db_pool: Optional[asyncpg.Pool] = None
        self._db_client: Optional[Client] = None
        self._redis: Optional["redis.Redis"] = None
        self._queue_service = None
        self._call_service = None
        self._session_manager = None
        self._voice_orchestrator = None
        self._adapter_registry_started: bool = False
        self._initialized = False

    async def startup(self) -> None:
        """
        Initialize all services.

        Called during FastAPI lifespan startup.
        Order matters — dependencies are initialized first.
        """
        if self._initialized:
            logger.warning("Container already initialized")
            return

        logger.info("Initializing service container...")

        # 1. Initialize PostgreSQL connection pool
        self._db_pool = await init_db_pool()
        self._db_client = Client(self._db_pool)
        logger.info("PostgreSQL connection pool initialized")

        # 2. Initialize Redis (optional — graceful fallback)
        await self._initialize_redis()

        # 3. Initialize queue service (uses Redis if available)
        await self._initialize_queue_service()

        # 4. Initialize CallService
        self._initialize_call_service()

        # 5. Session manager
        try:
            from app.domain.services.session_manager import SessionManager
            self._session_manager = await SessionManager.get_instance()
            logger.info(f"SessionManager initialized (Redis: {self._session_manager._redis_enabled})")
        except Exception as e:
            logger.warning(f"SessionManager initialization warning: {e}")

        # 6. Initialize VoiceOrchestrator
        self._initialize_voice_orchestrator()

        # 7. Start adapter health monitor (non-blocking background task)
        try:
            from app.infrastructure.telephony.adapter_factory import AdapterRegistry
            interval = float(os.getenv("ADAPTER_HEALTH_INTERVAL", "30"))
            AdapterRegistry.start_monitor(interval=interval)
            self._adapter_registry_started = True
            logger.info("Adapter health monitor started (interval=%.0fs)", interval)
        except Exception as e:
            logger.warning("Adapter health monitor could not start: %s", e)

        self._initialized = True
        logger.info("Service container startup complete")

    async def shutdown(self) -> None:
        """
        Gracefully shutdown all services.

        Called during FastAPI lifespan shutdown.
        """
        logger.info("Shutting down service container...")

        # Stop adapter health monitor and disconnect cached adapters first
        if self._adapter_registry_started:
            try:
                from app.infrastructure.telephony.adapter_factory import AdapterRegistry
                await AdapterRegistry.stop()
                logger.info("Adapter registry stopped")
            except Exception as e:
                logger.error("Adapter registry stop error: %s", e)
            self._adapter_registry_started = False

        if self._voice_orchestrator:
            try:
                logger.info("VoiceOrchestrator shutdown complete")
            except Exception as e:
                logger.error(f"VoiceOrchestrator shutdown error: {e}")

        if self._session_manager:
            try:
                await self._session_manager.shutdown()
                logger.info("SessionManager shutdown complete")
            except Exception as e:
                logger.error(f"SessionManager shutdown error: {e}")

        if self._queue_service:
            try:
                await self._queue_service.close()
                logger.info("Queue service closed")
            except Exception as e:
                logger.error(f"Queue service close error: {e}")

        if self._redis:
            try:
                await self._redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Redis close error: {e}")

        # Close PostgreSQL pool
        await close_db_pool()
        self._db_client = None
        logger.info("PostgreSQL pool closed")

        self._initialized = False
        logger.info("Service container shutdown complete")

    # =====================================
    # Service Accessors
    # =====================================

    @property
    def db_pool(self) -> asyncpg.Pool:
        """Get asyncpg connection pool."""
        if not self._db_pool:
            raise RuntimeError("Container not initialized. Call startup() first.")
        return self._db_pool

    # Keep .db_client as an alias pointing to db_pool for backward compat
    # (services that do `container.db_client` should receive adapter client)
    @property
    def db_client(self):
        """Backward-compat alias → returns Postgres adapter client."""
        if not self._db_client:
            raise RuntimeError("Container not initialized. Call startup() first.")
        return self._db_client

    @property
    def redis(self) -> Optional["redis.Redis"]:
        """Get Redis client (None if not available)."""
        return self._redis

    @property
    def redis_enabled(self) -> bool:
        """Check if Redis is available."""
        return self._redis is not None

    @property
    def queue_service(self):
        """Get DialerQueueService instance."""
        if not self._queue_service:
            raise RuntimeError("Queue service not initialized")
        return self._queue_service

    @property
    def session_manager(self):
        """Get SessionManager instance."""
        if not self._session_manager:
            raise RuntimeError("SessionManager not initialized")
        return self._session_manager

    @property
    def call_service(self):
        """Get CallService instance."""
        if not self._call_service:
            raise RuntimeError("CallService not initialized")
        return self._call_service

    @property
    def voice_orchestrator(self):
        """Get VoiceOrchestrator instance."""
        if not self._voice_orchestrator:
            raise RuntimeError("VoiceOrchestrator not initialized")
        return self._voice_orchestrator

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # =====================================
    # Private Initializers
    # =====================================

    async def _initialize_redis(self) -> None:
        """Initialize Redis connection with graceful fallback."""
        if not REDIS_AVAILABLE:
            logger.warning("redis.asyncio not installed — queue features will use fallback")
            return

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

        try:
            self._redis = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            await self._redis.ping()
            logger.info(f"Redis connected: {redis_url}")
        except Exception as e:
            logger.warning(f"Redis not available ({e}) — using in-memory fallback")
            self._redis = None

    async def _initialize_queue_service(self) -> None:
        """Initialize DialerQueueService."""
        try:
            from app.domain.services.queue_service import DialerQueueService
            self._queue_service = DialerQueueService(redis_client=self._redis)
            await self._queue_service.initialize()
            logger.info("Queue service initialized")
        except Exception as e:
            logger.warning(f"Queue service initialization warning: {e}")

    def _initialize_call_service(self) -> None:
        """Initialize CallService with db_pool and queue dependencies."""
        try:
            from app.domain.services.call_service import CallService
            self._call_service = CallService(
                db_client=self.db_client,
                queue_service=self._queue_service
            )
            logger.info("CallService initialized")
        except Exception as e:
            logger.warning(f"CallService initialization warning: {e}")

    def _initialize_voice_orchestrator(self) -> None:
        """Initialize VoiceOrchestrator."""
        try:
            from app.domain.services.voice_orchestrator import VoiceOrchestrator
            self._voice_orchestrator = VoiceOrchestrator(db_client=self.db_client)
            logger.info("VoiceOrchestrator initialized")
        except Exception as e:
            logger.warning(f"VoiceOrchestrator initialization warning: {e}")


# =====================================
# Singleton Container Instance
# =====================================

_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    """
    Get the global container instance.

    Creates the container if it doesn't exist.
    Call container.startup() before use.
    """
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container


def reset_container() -> None:
    """
    Reset global container (for testing only).

    WARNING: Only use in tests!
    """
    global _container
    _container = None


# =====================================
# FastAPI Dependencies
# =====================================

def get_db_pool_from_container() -> asyncpg.Pool:
    """
    FastAPI dependency — get asyncpg pool from container.

    Usage:
        @router.get("/example")
        async def example(pool: asyncpg.Pool = Depends(get_db_pool_from_container)):
            async with pool.acquire() as conn:
                ...
    """
    container = get_container()
    if not container.is_initialized:
        raise RuntimeError("Container not initialized")
    return container.db_pool


def get_db_client_from_container():
    """FastAPI dependency - get adapter client from container."""
    container = get_container()
    if not container.is_initialized:
        raise RuntimeError("Container not initialized")
    return container.db_client
