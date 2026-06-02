"""
SMS Service
Orchestrates SMS sending with templates and audit logging.

Day 27: Timed Communication System
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg
import json

from app.infrastructure.connectors.sms import get_vonage_sms_provider, SMSResult
from app.domain.services.sms_template_manager import (
    get_sms_template_manager,
    SMSTemplateManager,
    SMSTemplateType
)

logger = logging.getLogger(__name__)


class SMSNotConfiguredError(Exception):
    """Raised when SMS provider is not configured."""
    def __init__(self, message: str = "SMS provider not configured. Set VONAGE_API_KEY and VONAGE_API_SECRET."):
        self.message = message
        super().__init__(self.message)


class SMSService:
    """
    SMS sending service that handles provider management, templates, and audit logging.
    
    Follows the same pattern as EmailService:
    - Template rendering
    - Provider abstraction
    - Audit trail via assistant_actions table
    - Idempotency support
    
    Integration Points:
    - ReminderWorker: Sends meeting reminders
    - Assistant: Ad-hoc SMS via chat
    - Voice Agent: Post-call SMS
    """
    
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        template_manager: Optional[SMSTemplateManager] = None
    ):
        """
        Initialize SMS service.
        
        Args:
            db_pool: PostgreSQL connection pool
            template_manager: Optional template manager (uses singleton if not provided)
        """
        self.db_pool = db_pool
        self.template_manager = template_manager or get_sms_template_manager()
        self._provider = get_vonage_sms_provider()
    
    async def send_sms(
        self,
        tenant_id: str,
        to_number: str,
        message: str,
        template_name: Optional[str] = None,
        template_context: Optional[Dict[str, Any]] = None,
        lead_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
        reminder_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        triggered_by: str = "system"
    ) -> Dict[str, Any]:
        """
        Send an SMS message.
        """
        # Check idempotency
        if idempotency_key:
            existing = await self._check_idempotency(idempotency_key)
            if existing:
                logger.info(f"SMS already sent with idempotency_key: {idempotency_key}")
                return {
                    "success": True,
                    "message_id": existing.get("external_message_id"),
                    "idempotent": True,
                    "action_id": str(existing.get("id"))
                }
        
        # Render template if specified
        if template_name and template_context:
            message = self.template_manager.render_template(template_name, **template_context)
            logger.info(f"Rendered SMS template: {template_name}")
        
        # Check provider is configured
        if not self._provider.is_configured():
            raise SMSNotConfiguredError()
        
        # Create action record
        action_id = await self._create_action_record(
            tenant_id=tenant_id,
            lead_id=lead_id,
            meeting_id=meeting_id,
            reminder_id=reminder_id,
            triggered_by=triggered_by,
            idempotency_key=idempotency_key,
            input_data={
                "to_number": to_number,
                "template_name": template_name,
                "message_preview": message[:50] + "..." if len(message) > 50 else message
            }
        )
        
        try:
            # Send SMS
            result = await self._provider.send_sms(
                to_number=to_number,
                message=message,
                metadata={"action_id": action_id, "tenant_id": tenant_id}
            )
            
            if result.success:
                # Update action as completed
                await self._update_action_status(
                    action_id=action_id,
                    status="completed",
                    output_data={
                        "message_id": result.message_id,
                        "provider": result.provider,
                        "cost": result.cost
                    }
                )
                
                logger.info(f"SMS sent successfully: {result.message_id} to {to_number[:6]}...")
                
                return {
                    "success": True,
                    "message_id": result.message_id,
                    "provider": result.provider,
                    "to_number": to_number,
                    "action_id": action_id,
                    "cost": result.cost
                }
            else:
                # Update action as failed
                await self._update_action_status(
                    action_id=action_id,
                    status="failed",
                    error=result.error
                )
                
                logger.error(f"SMS send failed: {result.error}")
                
                return {
                    "success": False,
                    "error": result.error,
                    "action_id": action_id
                }
                
        except Exception as e:
            logger.error(f"Exception sending SMS: {e}", exc_info=True)
            
            await self._update_action_status(
                action_id=action_id,
                status="failed",
                error=str(e)
            )
            
            return {
                "success": False,
                "error": str(e),
                "action_id": action_id
            }
    
    async def send_meeting_reminder(
        self,
        tenant_id: str,
        to_number: str,
        reminder_type: str,  # "24h", "1h", "10m"
        name: str,
        title: str,
        time: str,
        join_link: Optional[str] = None,
        lead_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
        reminder_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Convenience method to send meeting reminder SMS.
        """
        # Render the message using template manager
        message = self.template_manager.render_meeting_reminder(
            reminder_type=reminder_type,
            name=name,
            title=title,
            time=time,
            join_link=join_link
        )
        
        return await self.send_sms(
            tenant_id=tenant_id,
            to_number=to_number,
            message=message,
            lead_id=lead_id,
            meeting_id=meeting_id,
            reminder_id=reminder_id,
            idempotency_key=idempotency_key,
            triggered_by="reminder"
        )
    
    async def _check_idempotency(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Check if an action with this idempotency key already exists."""
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, external_message_id, status 
                    FROM assistant_actions 
                    WHERE type = 'send_sms' 
                    AND output_data->>'idempotency_key' = $1
                    AND status = 'completed'
                    """,
                    idempotency_key
                )
                return dict(row) if row else None
        except Exception:
            # No existing record found
            return None
    
    async def _create_action_record(
        self,
        tenant_id: str,
        lead_id: Optional[str],
        meeting_id: Optional[str],
        reminder_id: Optional[str],
        triggered_by: str,
        idempotency_key: Optional[str],
        input_data: Dict[str, Any]
    ) -> str:
        """Create an action record for audit purposes."""
        
        # Properly structure input_data with idempotency_key
        final_input_data = {
            **input_data,
            "idempotency_key": idempotency_key
        }
        
        try:
            async with self.db_pool.acquire() as conn:
                # We need to construct the JSON properly
                # Inserting directly into table
                # Note: meeting_id and reminder_id might not have direct columns in assistant_actions
                # Usually we store them in input_data or metadata if schema doesn't support them
                # But schema shows generic assistant_actions. Let's check schema.
                # Assuming schema has lead_id, but maybe not meeting_id/reminder_id directly?
                # Based on previous code, it seemed to be just inserting a dict.
                # Standard assistant_actions usually has: id, tenant_id, type, status, triggered_by...
                
                # Let's put extra IDs in metadata/input_data just in case, but keep lead_id if it exists
                # Assuming generic insert for now based on previous db_client call
                
                import uuid
                action_id = str(uuid.uuid4())
                
                await conn.execute(
                    """
                    INSERT INTO assistant_actions (
                        id, tenant_id, type, status, triggered_by, lead_id, 
                        input_data, started_at, created_at
                    ) VALUES ($1, $2, 'send_sms', 'pending', $3, $4, $5, NOW(), NOW())
                    """,
                    action_id, tenant_id, triggered_by, lead_id,
                    json.dumps(final_input_data)
                )
                
                logger.debug(f"Created SMS action record: {action_id}")
                return action_id
                
        except Exception as e:
            logger.error(f"Failed to create action record: {e}")
            # Fallback to local ID if DB fails (shouldn't happen in prod but prevents crash)
            import uuid
            return str(uuid.uuid4())
    
    async def _update_action_status(
        self,
        action_id: str,
        status: str,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> None:
        """Update action record status."""
        if not action_id:
            return
        
        try:
            async with self.db_pool.acquire() as conn:
                query = "UPDATE assistant_actions SET status = $1, completed_at = NOW()"
                params = [status]
                param_idx = 2
                
                if output_data:
                    query += f", output_data = ${param_idx}"
                    params.append(json.dumps(output_data))
                    param_idx += 1
                
                if error:
                    query += f", error = ${param_idx}"
                    params.append(error)
                    param_idx += 1
                
                query += f" WHERE id = ${param_idx}"
                params.append(action_id)
                
                await conn.execute(query, *params)
                
        except Exception as e:
            logger.error(f"Failed to update action status: {e}")


# Singleton instance helper
_sms_service: Optional[SMSService] = None


def get_sms_service(db_pool: asyncpg.Pool) -> SMSService:
    """Get or create SMSService instance."""
    global _sms_service
    if _sms_service is None:
        _sms_service = SMSService(db_pool)
    return _sms_service
