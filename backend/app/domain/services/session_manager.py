"""
Session Manager
Manages CallSession lifecycle with Redis backing
"""
import asyncio
import json
from typing import Dict, Optional
from datetime import datetime
import logging

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

from fastapi import WebSocket

from app.domain.models.session import CallSession, CallState
from app.core.config import ConfigManager

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Singleton session manager
    Manages in-memory sessions with Redis synchronization
    """
    
    _instance: Optional["SessionManager"] = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        """Private constructor - use get_instance()"""
        self._sessions: Dict[str, CallSession] = {}
        self._redis_client: Optional[any] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._config = ConfigManager()
        self._redis_enabled = REDIS_AVAILABLE
    
    @classmethod
    async def get_instance(cls) -> "SessionManager":
        """Get singleton instance (async factory pattern)"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance._initialize()
        return cls._instance
    
    async def _initialize(self):
        """Initialize Redis connection and start sync task"""
        if not self._redis_enabled:
            logger.warning("Redis not available - running in memory-only mode")
            return
        
        try:
            # Get Redis URL from config
            redis_url = self._config.get("redis_url", "redis://localhost:6379")
            
            # Create async Redis client
            self._redis_client = await redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            
            # Test connection
            await self._redis_client.ping()
            
            # Start periodic sync task
            self._sync_task = asyncio.create_task(self._periodic_sync())
            
            logger.info(f"SessionManager initialized with Redis: {redis_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.warning("Running in memory-only mode")
            self._redis_client = None
            self._redis_enabled = False
    
    async def create_session(
        self,
        call_id: str,
        campaign_id: str,
        lead_id: str,
        vonage_call_uuid: str,
        system_prompt: str,
        voice_id: str,
        websocket: WebSocket,
        language: str = "en",
        tenant_id: Optional[str] = None
    ) -> CallSession:
        """
        Create a new call session
        
        Args:
            call_id: Unique call identifier
            campaign_id: Campaign ID
            lead_id: Lead ID
            vonage_call_uuid: Vonage's call UUID
            system_prompt: AI system prompt
            voice_id: TTS voice ID
            websocket: Active WebSocket connection
            language: Language code (default: "en")
            tenant_id: Tenant ID (optional, for multi-tenancy)
        
        Returns:
            CallSession: Created session
        """
        # Create session object
        session = CallSession(
            call_id=call_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            vonage_call_uuid=vonage_call_uuid,
            system_prompt=system_prompt,
            voice_id=voice_id,
            language=language,
            websocket=websocket,
            state=CallState.ACTIVE
        )
        
        # Initialize buffers
        session.audio_input_buffer = asyncio.Queue(maxsize=100)
        session.audio_output_buffer = asyncio.Queue(maxsize=100)
        session.transcript_buffer = asyncio.Queue(maxsize=50)
        
        # Store in memory
        self._sessions[call_id] = session
        
        # Sync to Redis immediately
        if self._redis_enabled:
            await self._sync_session_to_redis(call_id)
        
        logger.info(f"Created session: {call_id}")
        return session
    
    async def get_session(self, call_id: str) -> Optional[CallSession]:
        """
        Get session by call_id
        First checks in-memory, then Redis
        
        Args:
            call_id: Call identifier
        
        Returns:
            CallSession if found, None otherwise
        """
        # Check in-memory first
        if call_id in self._sessions:
            return self._sessions[call_id]
        
        # Try to load from Redis
        if self._redis_enabled:
            session = await self._load_session_from_redis(call_id)
            if session:
                self._sessions[call_id] = session
                return session
        
        return None
    
    async def update_session(self, call_id: str, **updates):
        """
        Update session fields
        
        Args:
            call_id: Call identifier
            **updates: Fields to update
        """
        session = await self.get_session(call_id)
        if not session:
            raise ValueError(f"Session not found: {call_id}")
        
        # Update fields
        for key, value in updates.items():
            if hasattr(session, key):
                setattr(session, key, value)
        
        # Update activity timestamp
        session.update_activity()
        
        # Sync to Redis (async, don't wait)
        if self._redis_enabled:
            asyncio.create_task(self._sync_session_to_redis(call_id))
    
    async def end_session(self, call_id: str, reason: str = "completed"):
        """
        End a call session
        Persists to database and removes from memory/Redis
        
        Args:
            call_id: Call identifier
            reason: Reason for ending (completed, error, timeout, hangup)
        """
        session = await self.get_session(call_id)
        if not session:
            logger.warning(f"Session not found for ending: {call_id}")
            return
        
        # Update state
        session.state = CallState.ENDED
        session.update_activity()
        
        # Persist to database
        await self._persist_session_to_db(session, reason)
        
        # Remove from memory
        if call_id in self._sessions:
            del self._sessions[call_id]
        
        # Remove from Redis
        if self._redis_enabled:
            await self._remove_session_from_redis(call_id)
        
        logger.info(f"Ended session: {call_id} (reason: {reason})")
    
    async def cleanup_stale_sessions(self, timeout_seconds: int = 300):
        """
        Clean up sessions that have been inactive
        
        Args:
            timeout_seconds: Inactivity timeout (default: 5 minutes)
        """
        stale_call_ids = []
        
        for call_id, session in self._sessions.items():
            if session.is_stale(timeout_seconds):
                stale_call_ids.append(call_id)
        
        for call_id in stale_call_ids:
            logger.warning(f"Cleaning up stale session: {call_id}")
            await self.end_session(call_id, reason="timeout")
    
    async def _sync_session_to_redis(self, call_id: str):
        """Sync single session to Redis"""
        session = self._sessions.get(call_id)
        if not session or not self._redis_client:
            return
        
        try:
            # Serialize to JSON
            session_dict = session.model_dump_redis()
            session_json = json.dumps(session_dict, default=str)
            
            # Store in Redis with TTL (1 hour)
            redis_key = f"session:{call_id}"
            await self._redis_client.setex(
                redis_key,
                3600,  # 1 hour TTL
                session_json
            )
        except Exception as e:
            logger.error(f"Error syncing session to Redis: {e}")
    
    async def _load_session_from_redis(self, call_id: str) -> Optional[CallSession]:
        """Load session from Redis"""
        if not self._redis_client:
            return None
        
        try:
            redis_key = f"session:{call_id}"
            session_json = await self._redis_client.get(redis_key)
            
            if session_json:
                session_dict = json.loads(session_json)
                session = CallSession.from_redis_dict(session_dict)
                logger.info(f"Loaded session from Redis: {call_id}")
                return session
        except Exception as e:
            logger.error(f"Error loading session from Redis: {e}")
        
        return None
    
    async def _remove_session_from_redis(self, call_id: str):
        """Remove session from Redis"""
        if not self._redis_client:
            return
        
        try:
            redis_key = f"session:{call_id}"
            await self._redis_client.delete(redis_key)
        except Exception as e:
            logger.error(f"Error removing session from Redis: {e}")
    
    async def _periodic_sync(self):
        """Periodically sync all sessions to Redis"""
        while True:
            try:
                await asyncio.sleep(5)  # Sync every 5 seconds
                
                for call_id in list(self._sessions.keys()):
                    await self._sync_session_to_redis(call_id)
                
                # Also cleanup stale sessions
                await self.cleanup_stale_sessions()
                
            except asyncio.CancelledError:
                logger.info("Periodic sync task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic sync: {e}")
    
    async def _persist_session_to_db(self, session: CallSession, reason: str):
        """
        Persist session to Supabase database
        TODO: Implement database persistence
        """
        # This will be implemented in a later task
        # For now, just log
        logger.info(f"TODO: Persist session {session.call_id} to database (reason: {reason})")
        logger.info(f"  Duration: {session.get_duration_seconds():.2f}s")
        logger.info(f"  Turns: {session.turn_id}")
        logger.info(f"  Messages: {len(session.conversation_history)}")
        logger.info(f"  Avg STT latency: {session.get_average_latency('stt'):.2f}ms")
        logger.info(f"  Avg LLM latency: {session.get_average_latency('llm'):.2f}ms")
        logger.info(f"  Avg TTS latency: {session.get_average_latency('tts'):.2f}ms")
    
    async def shutdown(self):
        """Graceful shutdown - persist all sessions"""
        logger.info("Shutting down SessionManager...")
        
        # Cancel sync task
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        
        # Persist all active sessions
        for call_id in list(self._sessions.keys()):
            await self.end_session(call_id, reason="shutdown")
        
        # Close Redis connection
        if self._redis_client:
            await self._redis_client.close()
        
        logger.info("SessionManager shutdown complete")
    
    def get_active_session_count(self) -> int:
        """Get number of active sessions"""
        return len(self._sessions)
    
    def get_session_stats(self) -> dict:
        """Get session statistics"""
        return {
            "active_sessions": len(self._sessions),
            "call_ids": list(self._sessions.keys()),
            "redis_enabled": self._redis_enabled,
            "states": {
                state.value: sum(1 for s in self._sessions.values() if s.state == state)
                for state in CallState
            }
        }
