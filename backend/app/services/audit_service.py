"""
AuditService - Centralized Audit Logging

Provides comprehensive audit trail for all actions and security events.
Records who triggered what, when, and the outcome.
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, date
from uuid import uuid4
from dataclasses import dataclass
from enum import Enum
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)


class OutcomeStatus(str, Enum):
    """Possible outcomes for audited actions."""
    SUCCESS = "success"
    FAILED = "failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    PERMISSION_DENIED = "permission_denied"
    REPLAY_REJECTED = "replay_rejected"
    CONNECTOR_REQUIRED = "connector_required"
    VALIDATION_ERROR = "validation_error"
    TIMEOUT = "timeout"


class SecurityEventType(str, Enum):
    """Types of security events to audit."""
    TOKEN_REFRESH = "token_refresh"
    TOKEN_REFRESH_FAILED = "token_refresh_failed"
    CONNECTOR_CONNECTED = "connector_connected"
    CONNECTOR_DISCONNECTED = "connector_disconnected"
    CONNECTOR_REVOKED = "connector_revoked"
    REPLAY_ATTEMPT = "replay_attempt"
    AUTH_FAILURE = "auth_failure"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass
class AuditEntry:
    """Represents an audit log entry."""
    id: str
    tenant_id: str
    action_type: str
    triggered_by: str
    outcome_status: str
    created_at: datetime
    user_id: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None


class AuditService:
    """
    Centralized audit logging service.
    
    Usage:
        service = get_audit_service(db_client)
        
        # Log an action
        action_id = await service.log_action(
            tenant_id="...",
            action_type="send_email",
            triggered_by="assistant",
            outcome_status="success",
            input_data={"to": ["user@example.com"]},
            output_data={"message_id": "abc123"}
        )
        
        # Log a security event
        await service.log_security_event(
            tenant_id="...",
            event_type=SecurityEventType.TOKEN_REFRESH,
            details={"connector_id": "..."}
        )
    """
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
    
    async def log_action(
        self,
        tenant_id: str,
        action_type: str,
        triggered_by: str,
        outcome_status: str,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        call_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        duration_ms: Optional[int] = None
    ) -> Optional[str]:
        """
        Create an audit log entry for an action.
        
        Args:
            tenant_id: Tenant ID
            action_type: Type of action (send_email, book_meeting, etc.)
            triggered_by: Who triggered the action (chat, call_outcome, schedule, api)
            outcome_status: Result (success, failed, quota_exceeded, etc.)
            input_data: Action input parameters
            output_data: Action result/response
            error: Error message if failed
            user_id: User who triggered the action
            conversation_id: Related conversation ID
            lead_id: Related lead ID
            campaign_id: Related campaign ID
            connector_id: Related connector ID
            call_id: Related call ID
            ip_address: Client IP address
            user_agent: Client user agent
            request_id: Request correlation ID
            idempotency_key: Idempotency key for replay protection
            duration_ms: Execution duration in milliseconds
            
        Returns:
            Action ID if successful, None otherwise
        """
        try:
            # Sanitize input data (remove sensitive fields)
            sanitized_input = self._sanitize_data(input_data) if input_data else None
            sanitized_output = self._sanitize_data(output_data) if output_data else None
            
            action_data = {
                "tenant_id": tenant_id,
                "type": action_type,
                "triggered_by": triggered_by,
                "status": "completed" if outcome_status == "success" else "failed",
                "outcome_status": outcome_status,
                "input_data": sanitized_input,
                "output_data": sanitized_output,
                "error": error,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "lead_id": lead_id,
                "campaign_id": campaign_id,
                "connector_id": connector_id,
                "call_id": call_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "duration_ms": duration_ms,
                "completed_at": datetime.utcnow().isoformat()
            }
            
            # Remove None values
            action_data = {k: v for k, v in action_data.items() if v is not None}
            
            response = self.db_client.table("assistant_actions").insert(action_data).execute()
            
            if response.data:
                action_id = response.data[0]["id"]
                logger.info(f"Audit log created: {action_type} - {outcome_status} (ID: {action_id})")
                return action_id
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to create audit log: {e}")
            return None
    
    async def log_security_event(
        self,
        tenant_id: str,
        event_type: SecurityEventType,
        details: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Optional[str]:
        """
        Log a security-related event.
        
        Args:
            tenant_id: Tenant ID
            event_type: Type of security event
            details: Event details
            user_id: User involved (if any)
            ip_address: Client IP address
            
        Returns:
            Action ID if successful, None otherwise
        """
        return await self.log_action(
            tenant_id=tenant_id,
            action_type=f"security:{event_type.value}",
            triggered_by="system",
            outcome_status="logged",
            input_data=details,
            user_id=user_id,
            ip_address=ip_address
        )
    
    async def log_token_rotation(
        self,
        tenant_id: str,
        connector_id: str,
        success: bool,
        error: Optional[str] = None
    ) -> Optional[str]:
        """Log a token rotation event."""
        return await self.log_security_event(
            tenant_id=tenant_id,
            event_type=SecurityEventType.TOKEN_REFRESH if success else SecurityEventType.TOKEN_REFRESH_FAILED,
            details={
                "connector_id": connector_id,
                "success": success,
                "error": error
            }
        )
    
    async def log_connector_event(
        self,
        tenant_id: str,
        connector_id: str,
        event: str,  # connected, disconnected, revoked
        reason: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Optional[str]:
        """Log a connector lifecycle event."""
        event_map = {
            "connected": SecurityEventType.CONNECTOR_CONNECTED,
            "disconnected": SecurityEventType.CONNECTOR_DISCONNECTED,
            "revoked": SecurityEventType.CONNECTOR_REVOKED
        }
        
        return await self.log_security_event(
            tenant_id=tenant_id,
            event_type=event_map.get(event, SecurityEventType.CONNECTOR_DISCONNECTED),
            details={
                "connector_id": connector_id,
                "event": event,
                "reason": reason
            },
            user_id=user_id
        )
    
    async def log_replay_attempt(
        self,
        tenant_id: str,
        idempotency_key: str,
        action_type: str,
        ip_address: Optional[str] = None
    ) -> Optional[str]:
        """Log a replay attack attempt."""
        return await self.log_security_event(
            tenant_id=tenant_id,
            event_type=SecurityEventType.REPLAY_ATTEMPT,
            details={
                "idempotency_key": idempotency_key,
                "action_type": action_type
            },
            ip_address=ip_address
        )
    
    async def get_audit_log(
        self,
        tenant_id: str,
        action_type: Optional[str] = None,
        outcome_status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Query audit log for a tenant.
        
        Args:
            tenant_id: Tenant ID
            action_type: Filter by action type
            outcome_status: Filter by outcome status
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            limit: Maximum records to return
            
        Returns:
            List of audit log entries
        """
        try:
            query = self.db_client.table("assistant_actions").select(
                "id, type, triggered_by, status, outcome_status, input_data, output_data, error, "
                "user_id, created_at, completed_at, duration_ms"
            ).eq("tenant_id", tenant_id)
            
            if action_type:
                query = query.eq("type", action_type)
            if outcome_status:
                query = query.eq("outcome_status", outcome_status)
            if from_date:
                query = query.gte("created_at", f"{from_date}T00:00:00")
            if to_date:
                query = query.lte("created_at", f"{to_date}T23:59:59")
            
            response = query.order("created_at", desc=True).limit(limit).execute()
            
            return response.data or []
            
        except Exception as e:
            logger.error(f"Error querying audit log: {e}")
            return []
    
    async def get_actions_summary(
        self,
        tenant_id: str,
        target_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get summary of actions for a day.
        
        Args:
            tenant_id: Tenant ID
            target_date: Date to summarize (defaults to today)
            
        Returns:
            Summary with counts by type and outcome
        """
        target_date = target_date or date.today().isoformat()
        
        try:
            response = self.db_client.table("assistant_actions").select(
                "type, outcome_status", count="exact"
            ).eq("tenant_id", tenant_id).gte(
                "created_at", f"{target_date}T00:00:00"
            ).lte(
                "created_at", f"{target_date}T23:59:59"
            ).execute()
            
            # Count by type and outcome
            by_type: Dict[str, int] = {}
            by_outcome: Dict[str, int] = {}
            
            for action in response.data or []:
                action_type = action.get("type", "unknown")
                outcome = action.get("outcome_status", "unknown")
                
                by_type[action_type] = by_type.get(action_type, 0) + 1
                by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            
            return {
                "date": target_date,
                "total_actions": response.count or 0,
                "by_type": by_type,
                "by_outcome": by_outcome
            }
            
        except Exception as e:
            logger.error(f"Error getting actions summary: {e}")
            return {
                "date": target_date,
                "total_actions": 0,
                "by_type": {},
                "by_outcome": {}
            }
    
    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive fields from data before logging."""
        sensitive_fields = {
            "password", "token", "access_token", "refresh_token",
            "secret", "api_key", "private_key", "credential"
        }
        
        sanitized = {}
        for key, value in data.items():
            if any(s in key.lower() for s in sensitive_fields):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_data(value)
            else:
                sanitized[key] = value
        
        return sanitized


# Singleton instance
_audit_service: Optional[AuditService] = None


def get_audit_service(db_client: Client) -> AuditService:
    """Get or create AuditService instance."""
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService(db_client)
    return _audit_service
