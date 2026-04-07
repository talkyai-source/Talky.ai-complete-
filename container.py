"""
Dependency Injection Container
Manages creation and lifecycle of all services and providers.

FIX: Redis connection now reads REDIS_PASSWORD from environment and
     builds the authenticated connection URL correctly.
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


def _build_redis_url() -> str:
    """
    Build a Redis connection URL from individual env vars or REDIS_URL.

    Priority:
      1. REDIS_URL if set (user-provided full URL)
      2. Constructed from REDIS_HOST + REDIS_PORT + REDIS_PASSWORD

    The docker-compose.yml sets requirepass, so REDIS_PASSWORD must be
    included in the URL for authentication to succeed.
    """
    # If a full URL is explicitly set, use it as-is
    explicit_url = os.getenv("REDIS_URL", "").strip()
    if explicit_url:
        return explicit_url

    host     = os.getenv("REDIS_HOST", "localhost")
    port     = os.getenv("REDIS_PORT", "6379")
    db       = os.getenv("REDIS_DB", "0")
    password = os.getenv("REDIS_PASSWORD", "").strip()

    if password:
        # redis://:password@host:port/db
        return f"redis://:{password}@{host}:{port}/{db}"
    else:
        return f"redis://{host}:{port}/{db}"


class ServiceContainer:
    """
    Central container for all application services.

    Provides:
    - Singleton service instances
    - Async startup/shutdown lifecycle
    - Proper resource cleanup
    """

    def __init__(self):
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
        if self._initialized:
            logger.warning("Container already initialized")
            return

        logger.info("Initializing service container...")

        # 1. PostgreSQL
        self._db_pool = await init_db_pool()
        self._db_client = Client(self._db_pool)
        logger.info("PostgreSQL connection pool initialized")

        # 2. Redis (with auth)
        await self._initialize_redis()

        # 3. Queue
        await self._initialize_queue_service()

        # 4. CallService
        self._initialize_call_service()

        # 5. Session manager
        try:
            from app.domain.services.session_manager import SessionManager
            self._session_manager = await SessionManager.get_instance()
            logger.info(f"SessionManager initialized (Redis: {self._session_manager._redis_enabled})")
        except Exception as e:
            logger.warning(f"SessionManager initialization warning: {e}")

        # 6. VoiceOrchestrator
        self._initialize_voice_orchestrator()

        # 7. Adapter health monitor
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
        logger.info("Shutting down service container...")

        if self._adapter_registry_started:
            try:
                from app.infrastructure.telephony.adapter_factory import AdapterRegistry
                await AdapterRegistry.stop()
                logger.info("Adapter registry stopped")
            except Exception as e:
                logger.error("Adapter registry stop error: %s", e)
            self._adapter_registry_started = False

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

        await close_db_pool()
        self._db_client = None
        logger.info("PostgreSQL pool closed")

        self._initialized = False
        logger.info("Service container shutdown complete")

    # ── Accessors ─────────────────────────────────────────────────

    @property
    def db_pool(self) -> asyncpg.Pool:
        if not self._db_pool:
            raise RuntimeError("Container not initialized. Call startup() first.")
        return self._db_pool

    @property
    def db_client(self):
        if not self._db_client:
            raise RuntimeError("Container not initialized. Call startup() first.")
        return self._db_client

    @property
    def redis(self) -> Optional["redis.Redis"]:
        return self._redis

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None

    @property
    def queue_service(self):
        if not self._queue_service:
            raise RuntimeError("Queue service not initialized")
        return self._queue_service

    @property
    def session_manager(self):
        if not self._session_manager:
            raise RuntimeError("SessionManager not initialized")
        return self._session_manager

    @property
    def call_service(self):
        if not self._call_service:
            raise RuntimeError("CallService not initialized")
        return self._call_service

    @property
    def voice_orchestrator(self):
        if not self._voice_orchestrator:
            raise RuntimeError("VoiceOrchestrator not initialized")
        return self._voice_orchestrator

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # ── Private initializers ──────────────────────────────────────

    async def _initialize_redis(self) -> None:
        """Initialize Redis with password authentication."""
        if not REDIS_AVAILABLE:
            logger.warning("redis.asyncio not installed — queue features will use fallback")
            return

        redis_url = _build_redis_url()
        # Log URL with password masked
        log_url = redis_url.replace(
            os.getenv("REDIS_PASSWORD", "NOPASSWORD"),
            "***"
        ) if os.getenv("REDIS_PASSWORD") else redis_url

        try:
            self._redis = redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._redis.ping()
            logger.info(f"Redis connected: {log_url}")
        except Exception as e:
            logger.warning(f"Redis not available ({e}) — using in-memory fallback")
            self._redis = None

    async def _initialize_queue_service(self) -> None:
        try:
            from app.domain.services.queue_service import DialerQueueService
            self._queue_service = DialerQueueService(redis_client=self._redis)
            await self._queue_service.initialize()
            logger.info("Queue service initialized")
        except Exception as e:
            logger.warning(f"Queue service initialization warning: {e}")

    def _initialize_call_service(self) -> None:
        try:
            from app.domain.services.call_service import CallService
            self._call_service = CallService(
                db_client=self.db_client,
                queue_service=self._queue_service,
            )
            logger.info("CallService initialized")
        except Exception as e:
            logger.warning(f"CallService initialization warning: {e}")

    def _initialize_voice_orchestrator(self) -> None:
        try:
            from app.domain.services.voice_orchestrator import VoiceOrchestrator
            self._voice_orchestrator = VoiceOrchestrator(db_client=self.db_client)
            logger.info("VoiceOrchestrator initialized")
        except Exception as e:
            logger.warning(f"VoiceOrchestrator initialization warning: {e}")


# ── Singleton ──────────────────────────────────────────────────────

_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container


def reset_container() -> None:
    """Reset global container — for testing only."""
    global _container
    _container = None


def get_db_pool_from_container() -> asyncpg.Pool:
    container = get_container()
    if not container.is_initialized:
        raise RuntimeError("Container not initialized")
    return container.db_pool


def get_db_client_from_container():
    container = get_container()
    if not container.is_initialized:
        raise RuntimeError("Container not initialized")
    return container.db_client
