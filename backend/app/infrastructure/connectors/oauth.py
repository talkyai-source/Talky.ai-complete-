"""
OAuth State Manager
Manages OAuth state parameters with PKCE support for secure authorization flows.

Security Features:
- State stored in Redis with 5-minute TTL (falls back to in-memory if Redis unavailable)
- PKCE (Proof Key for Code Exchange) with S256 challenge
- Tenant binding validation
- One-time use (deleted after validation)

Day 24: Unified Connector System
"""
import os
import uuid
import hashlib
import secrets
import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# In-memory fallback storage (for development without Redis)
_memory_state_storage: Dict[str, Dict[str, Any]] = {}


class OAuthStateError(Exception):
    """Raised when OAuth state validation fails"""
    pass


class OAuthStateManager:
    """
    Manages OAuth state parameters with PKCE support.
    
    Flow:
    1. create_state() - Generate state, code_verifier, code_challenge
    2. User completes OAuth flow
    3. validate_state() - Verify state and retrieve code_verifier
    
    Security:
    - State is random UUID (128 bits of entropy)
    - Code verifier is 64 bytes URL-safe random
    - Code challenge uses SHA256 (S256 method)
    - State expires after 5 minutes
    - State is deleted after single use
    """
    
    STATE_TTL_SECONDS = 300  # 5 minutes
    STATE_KEY_PREFIX = "oauth_state:"
    
    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize OAuth state manager.
        
        Args:
            redis_url: Redis connection URL (defaults to REDIS_URL env)
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = None
        self._use_memory = False  # Will be set if Redis connection fails
    
    async def _get_redis(self):
        """Get or create Redis connection, fallback to memory if unavailable."""
        if self._use_memory:
            return None
            
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                # Test connection
                await self._redis.ping()
                logger.info("Connected to Redis for OAuth state storage")
            except Exception as e:
                logger.warning(f"Redis not available ({e}), using in-memory state storage")
                self._use_memory = True
                self._redis = None
                return None
        return self._redis
    
    @staticmethod
    def _generate_code_verifier() -> str:
        """
        Generate PKCE code verifier.
        
        RFC 7636 requires 43-128 characters from unreserved set.
        We use 64 characters for strong security.
        """
        return secrets.token_urlsafe(48)[:64]
    
    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        """
        Generate PKCE code challenge using S256 method.
        
        challenge = BASE64URL(SHA256(verifier))
        """
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        # Base64 URL-safe encoding without padding
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    
    async def create_state(
        self,
        tenant_id: str,
        user_id: str,
        provider: str,
        redirect_uri: str,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Create and store OAuth state.
        
        Args:
            tenant_id: Tenant ID for binding
            user_id: User ID for binding
            provider: Connector provider name
            redirect_uri: OAuth callback URI
            extra_data: Additional data to store
            
        Returns:
            Dict with state, code_verifier, code_challenge
        """
        state = str(uuid.uuid4())
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        
        state_data = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "provider": provider,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(seconds=self.STATE_TTL_SECONDS)).isoformat(),
            **(extra_data or {})
        }
        
        try:
            redis = await self._get_redis()
            
            if redis:
                # Use Redis
                await redis.setex(
                    f"{self.STATE_KEY_PREFIX}{state}",
                    self.STATE_TTL_SECONDS,
                    json.dumps(state_data)
                )
            else:
                # Use in-memory storage
                _memory_state_storage[state] = state_data
                # Clean up expired states
                now = datetime.utcnow()
                expired_keys = [
                    k for k, v in list(_memory_state_storage.items())
                    if datetime.fromisoformat(v.get("expires_at", now.isoformat())) < now
                ]
                for k in expired_keys:
                    del _memory_state_storage[k]
            
            logger.info(
                f"Created OAuth state for {provider}, "
                f"tenant {tenant_id[:8]}..., expires in {self.STATE_TTL_SECONDS}s"
            )
            
            return {
                "state": state,
                "code_verifier": code_verifier,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256"
            }
            
        except Exception as e:
            logger.error(f"Failed to create OAuth state: {e}")
            raise OAuthStateError(f"Failed to create OAuth state: {e}")
    
    async def validate_state(
        self,
        state: str,
        expected_tenant_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate OAuth state and return stored data.
        
        This is a one-time operation - state is deleted after validation.
        
        Args:
            state: State parameter from callback
            expected_tenant_id: Optional tenant ID to validate
            
        Returns:
            Stored state data including code_verifier
            
        Raises:
            OAuthStateError: If state is invalid, expired, or tenant mismatch
        """
        try:
            redis = await self._get_redis()
            
            if redis:
                # Use Redis
                key = f"{self.STATE_KEY_PREFIX}{state}"
                data = await redis.get(key)
                
                if not data:
                    logger.warning(f"OAuth state not found or expired: {state[:8]}...")
                    raise OAuthStateError("OAuth state not found or expired")
                
                state_data = json.loads(data)
                
                # Delete state (one-time use)
                await redis.delete(key)
            else:
                # Use in-memory storage
                if state not in _memory_state_storage:
                    logger.warning(f"OAuth state not found: {state[:8]}...")
                    raise OAuthStateError("OAuth state not found or expired")
                
                state_data = _memory_state_storage.pop(state)
                
                # Check expiration
                if "expires_at" in state_data:
                    expires_at = datetime.fromisoformat(state_data["expires_at"])
                    if datetime.utcnow() > expires_at:
                        logger.warning(f"OAuth state expired: {state[:8]}...")
                        raise OAuthStateError("OAuth state not found or expired")
            
            # Validate tenant binding if provided
            if expected_tenant_id and state_data.get("tenant_id") != expected_tenant_id:
                logger.warning(
                    f"OAuth state tenant mismatch: "
                    f"expected {expected_tenant_id[:8]}..., "
                    f"got {state_data.get('tenant_id', 'none')[:8]}..."
                )
                raise OAuthStateError("OAuth state tenant mismatch")
            
            logger.info(
                f"Validated OAuth state for {state_data.get('provider')}, "
                f"tenant {state_data.get('tenant_id', 'unknown')[:8]}..."
            )
            
            return state_data
            
        except OAuthStateError:
            raise
        except Exception as e:
            logger.error(f"Failed to validate OAuth state: {e}")
            raise OAuthStateError(f"Failed to validate OAuth state: {e}")
    
    async def delete_state(self, state: str) -> bool:
        """
        Explicitly delete OAuth state.
        
        Use when OAuth flow is cancelled or fails.
        
        Returns:
            True if state was deleted, False if not found
        """
        try:
            redis = await self._get_redis()
            
            if redis:
                result = await redis.delete(f"{self.STATE_KEY_PREFIX}{state}")
                return result > 0
            else:
                if state in _memory_state_storage:
                    del _memory_state_storage[state]
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to delete OAuth state: {e}")
            return False
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None


# Singleton instance
_oauth_state_manager: Optional[OAuthStateManager] = None


def get_oauth_state_manager() -> OAuthStateManager:
    """Get singleton OAuth state manager."""
    global _oauth_state_manager
    if _oauth_state_manager is None:
        _oauth_state_manager = OAuthStateManager()
    return _oauth_state_manager
