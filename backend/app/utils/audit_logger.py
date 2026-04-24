import json
import logging
from uuid import UUID
from typing import Optional, Any, Dict
from datetime import datetime
from fastapi import Request
from app.core.db import get_db

logger = logging.getLogger(__name__)

class AuditLogger:
    @staticmethod
    async def log(
        user_id: UUID,
        action_type: str,
        tenant_id: Optional[UUID] = None,
        target_type: Optional[str] = None,
        target_id: Optional[UUID] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        previous_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "success"
    ) -> Optional[UUID]:
        """
        Log an audit event to the audit_logs table.
        """
        try:
            async with get_db() as conn:
                query = """
                INSERT INTO audit_logs (
                    user_id, tenant_id, action_type, target_type, target_id, 
                    description, metadata, previous_values, new_values,
                    ip_address, user_agent, status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
                """
                
                # Convert dicts to JSON strings for asyncpg
                metadata_json = json.dumps(metadata or {})
                prev_json = json.dumps(previous_values or {})
                new_json = json.dumps(new_values or {})
                
                row = await conn.fetchrow(
                    query,
                    user_id,
                    tenant_id,
                    action_type,
                    target_type,
                    target_id,
                    description,
                    metadata_json,
                    prev_json,
                    new_json,
                    ip_address,
                    user_agent,
                    status
                )
                return row['id'] if row else None
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            return None

    @staticmethod
    async def log_security_event(
        event_type: str,
        severity: str,
        description: str,
        user_id: Optional[UUID] = None,
        tenant_id: Optional[UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Optional[UUID]:
        """
        Log a security event to the security_events table.
        """
        try:
            async with get_db() as conn:
                query = """
                INSERT INTO security_events (
                    event_type, severity, description, user_id, tenant_id,
                    metadata, ip_address, user_agent
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """
                
                metadata_json = json.dumps(metadata or {})
                
                row = await conn.fetchrow(
                    query,
                    event_type,
                    severity,
                    description,
                    user_id,
                    tenant_id,
                    metadata_json,
                    ip_address,
                    user_agent
                )
                return row['id'] if row else None
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
            return None

async def audit_log_request(
    request: Request,
    user_id: UUID,
    action_type: str,
    tenant_id: Optional[UUID] = None,
    target_type: Optional[str] = None,
    target_id: Optional[UUID] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    previous_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    status: str = "success"
) -> Optional[UUID]:
    """
    Helper to log an audit event using information from the FastAPI Request.
    """
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    
    return await AuditLogger.log(
        user_id=user_id,
        action_type=action_type,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
        description=description,
        metadata=metadata,
        previous_values=previous_values,
        new_values=new_values,
        ip_address=ip_address,
        user_agent=user_agent,
        status=status
    )
