"""
ReplayProtectionService - Action Replay Attack Prevention

Prevents replay attacks by:
- Validating idempotency keys
- Checking request timestamps
- Rejecting duplicate requests
"""
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta
from app.core.postgres_adapter import Client

from app.services.audit_service import get_audit_service

logger = logging.getLogger(__name__)


class ReplayProtectionService:
    """
    Prevents replay attacks on action requests.
    
    Features:
    - Idempotency key validation (prevents duplicate requests)
    - Timestamp validation (rejects old requests)
    - Audit logging for replay attempts
    
    Usage:
        service = get_replay_protection_service(db_client)
        
        # Validate before executing action
        is_valid, error = await service.validate_request(
            idempotency_key="unique-key-123",
            request_timestamp=datetime.utcnow(),
            tenant_id="..."
        )
        
        if not is_valid:
            return {"success": False, "error": error}
    """
    
    # Maximum age for valid requests (5 minutes)
    MAX_REQUEST_AGE_SECONDS = 300
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
        self.audit = get_audit_service(db_client)
    
    async def validate_request(
        self,
        tenant_id: str,
        idempotency_key: Optional[str] = None,
        request_timestamp: Optional[datetime] = None,
        action_type: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that a request is not a replay.
        
        Args:
            tenant_id: Tenant ID
            idempotency_key: Unique key for this request
            request_timestamp: When the request was created
            action_type: Type of action (for logging)
            ip_address: Client IP (for logging)
            
        Returns:
            Tuple of (is_valid, error_message)
            is_valid is True if request should be processed
            error_message is None if valid, otherwise reason for rejection
        """
        # Check timestamp age
        if request_timestamp:
            age = (datetime.utcnow() - request_timestamp).total_seconds()
            if age > self.MAX_REQUEST_AGE_SECONDS:
                logger.warning(
                    f"Replay attempt: request too old ({age:.0f}s) "
                    f"for tenant {tenant_id}"
                )
                await self._log_replay_attempt(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    action_type=action_type,
                    reason="request_too_old",
                    ip_address=ip_address
                )
                return False, f"Request too old ({int(age)}s) - possible replay attack"
            
            if age < 0:
                # Request from the future - suspicious
                logger.warning(
                    f"Replay attempt: request timestamp in future "
                    f"for tenant {tenant_id}"
                )
                await self._log_replay_attempt(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    action_type=action_type,
                    reason="future_timestamp",
                    ip_address=ip_address
                )
                return False, "Invalid request timestamp"
        
        # Check idempotency key
        if idempotency_key:
            is_duplicate, original_action_id = await self.is_duplicate(
                idempotency_key=idempotency_key,
                tenant_id=tenant_id
            )
            
            if is_duplicate:
                logger.warning(
                    f"Replay attempt: duplicate idempotency key '{idempotency_key}' "
                    f"for tenant {tenant_id} (original: {original_action_id})"
                )
                await self._log_replay_attempt(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    action_type=action_type,
                    reason="duplicate_key",
                    ip_address=ip_address
                )
                return False, f"Duplicate request (key: {idempotency_key})"
        
        return True, None
    
    async def is_duplicate(
        self,
        idempotency_key: str,
        tenant_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an idempotency key has already been used.
        
        Args:
            idempotency_key: The idempotency key to check
            tenant_id: Tenant ID
            
        Returns:
            Tuple of (is_duplicate, original_action_id)
        """
        try:
            response = self.db_client.table("assistant_actions").select(
                "id"
            ).eq("tenant_id", tenant_id).eq(
                "idempotency_key", idempotency_key
            ).limit(1).execute()
            
            if response.data:
                return True, response.data[0]["id"]
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error checking idempotency key: {e}")
            # On error, allow the request (fail open)
            return False, None
    
    async def register_key(
        self,
        idempotency_key: str,
        tenant_id: str,
        action_type: str
    ) -> bool:
        """
        Register an idempotency key before processing.
        
        This creates a pending action record to prevent race conditions.
        
        Args:
            idempotency_key: The idempotency key
            tenant_id: Tenant ID
            action_type: Type of action
            
        Returns:
            True if key was registered, False if already exists
        """
        try:
            # Try to insert - will fail on duplicate
            self.db_client.table("assistant_actions").insert({
                "tenant_id": tenant_id,
                "type": action_type,
                "status": "pending",
                "idempotency_key": idempotency_key,
                "triggered_by": "api"
            }).execute()
            
            return True
            
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                return False
            logger.error(f"Error registering idempotency key: {e}")
            return True  # Allow on unexpected errors
    
    async def _log_replay_attempt(
        self,
        tenant_id: str,
        idempotency_key: Optional[str],
        action_type: Optional[str],
        reason: str,
        ip_address: Optional[str]
    ) -> None:
        """Log a replay attempt for security monitoring."""
        try:
            await self.audit.log_replay_attempt(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key or "none",
                action_type=action_type or "unknown",
                ip_address=ip_address
            )
        except Exception as e:
            logger.error(f"Failed to log replay attempt: {e}")
    
    def generate_idempotency_key(self, *components: str) -> str:
        """
        Generate an idempotency key from components.
        
        Useful for creating deterministic keys based on action parameters.
        
        Args:
            components: String components to hash
            
        Returns:
            Idempotency key string
        """
        import hashlib
        
        combined = ":".join(str(c) for c in components)
        return hashlib.sha256(combined.encode()).hexdigest()[:32]


# Singleton instance
_replay_protection_service: Optional[ReplayProtectionService] = None


def get_replay_protection_service(db_client: Client) -> ReplayProtectionService:
    """Get or create ReplayProtectionService instance."""
    global _replay_protection_service
    if _replay_protection_service is None:
        _replay_protection_service = ReplayProtectionService(db_client)
    return _replay_protection_service
