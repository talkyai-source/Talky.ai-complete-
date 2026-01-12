"""
SMS Service
Orchestrates SMS sending with templates and audit logging.

Day 27: Timed Communication System
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from supabase import Client

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
        supabase: Client,
        template_manager: Optional[SMSTemplateManager] = None
    ):
        """
        Initialize SMS service.
        
        Args:
            supabase: Supabase client for database operations
            template_manager: Optional template manager (uses singleton if not provided)
        """
        self.supabase = supabase
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
        
        Args:
            tenant_id: Tenant ID for multi-tenancy
            to_number: Recipient phone number
            message: SMS content (ignored if using template)
            template_name: Optional template to use
            template_context: Variables for template rendering
            lead_id: Optional lead ID for linking
            meeting_id: Optional meeting ID for linking
            reminder_id: Optional reminder ID for linking
            idempotency_key: Key to prevent duplicate sends
            triggered_by: Trigger source (system, assistant, call_outcome)
            
        Returns:
            Dict with success status, message_id, and details
            
        Raises:
            SMSNotConfiguredError: If SMS provider is not configured
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
                    "action_id": existing.get("id")
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
        
        Args:
            tenant_id: Tenant ID
            to_number: Recipient phone number
            reminder_type: Type of reminder ("24h", "1h", "10m")
            name: Recipient name
            title: Meeting title
            time: Meeting time (formatted)
            join_link: Video conference link
            lead_id: Optional lead ID
            meeting_id: Optional meeting ID
            reminder_id: Optional reminder ID
            idempotency_key: Key to prevent duplicates
            
        Returns:
            SMS send result
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
            response = self.supabase.table("assistant_actions").select(
                "id, external_message_id, status"
            ).eq("type", "send_sms").eq(
                "output_data->>idempotency_key", idempotency_key
            ).eq("status", "completed").single().execute()
            
            return response.data
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
        action_data = {
            "tenant_id": tenant_id,
            "type": "send_sms",
            "status": "pending",
            "triggered_by": triggered_by,
            "lead_id": lead_id,
            "input_data": {
                **input_data,
                "idempotency_key": idempotency_key
            },
            "started_at": datetime.utcnow().isoformat()
        }
        
        response = self.supabase.table("assistant_actions").insert(action_data).execute()
        
        action_id = response.data[0]["id"] if response.data else None
        logger.debug(f"Created SMS action record: {action_id}")
        
        return action_id
    
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
        
        update_data = {
            "status": status,
            "completed_at": datetime.utcnow().isoformat()
        }
        
        if output_data:
            update_data["output_data"] = output_data
        if error:
            update_data["error"] = error
        
        self.supabase.table("assistant_actions").update(update_data).eq("id", action_id).execute()


# Singleton instance helper
_sms_service: Optional[SMSService] = None


def get_sms_service(supabase: Client) -> SMSService:
    """Get or create SMSService instance."""
    global _sms_service
    if _sms_service is None:
        _sms_service = SMSService(supabase)
    return _sms_service
