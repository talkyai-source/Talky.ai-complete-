"""
SMS Service
Orchestrates SMS sending with templates and audit logging.

Day 27: Timed Communication System
"""
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg
import json

from app.core.db_utils import acquire_with_tenant
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
        self.db_pool = getattr(db_pool, "pool", db_pool) if hasattr(db_pool, "table") else db_pool
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
        if idempotency_key:
            existing = await self._check_idempotency(tenant_id, idempotency_key)
            if existing:
                receipt_id = str(existing.get("external_message_id") or "")
                confirmed = (existing.get("status") == "completed" and bool(receipt_id)
                             and not receipt_id.startswith("sim-"))
                return {"success": confirmed, "status": "accepted" if confirmed else "unknown",
                    "confirmation_allowed": confirmed, "message_id": existing.get("external_message_id"),
                    "idempotent": True, "action_id": str(existing["id"])}
            from app.services.action_execution import DurableActionExecutor
            return await DurableActionExecutor(self.db_pool).execute(
                tenant_id=tenant_id, idempotency_key="sms:"+idempotency_key, action="send_sms",
                payload={"to_number": to_number, "message": message, "template_name": template_name,
                         "template_context": template_context}, lead_id=lead_id, triggered_by=triggered_by,
                executor=lambda: self.send_sms(tenant_id=tenant_id, to_number=to_number, message=message,
                    template_name=template_name, template_context=template_context, lead_id=lead_id,
                    meeting_id=meeting_id, reminder_id=reminder_id, triggered_by=triggered_by))

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

        receipt = {}
        try:
            result = await self._provider.send_sms(to_number=to_number, message=message,
                metadata={"action_id": action_id, "tenant_id": tenant_id})
            metadata = result.metadata or {}
            if result.success and result.message_id and not metadata.get("simulated"):
                receipt = {"message_id": result.message_id, "provider": result.provider, "cost": result.cost}
                await self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                    status="completed", output_data=receipt)
                return {"success": True, "status": "accepted", "confirmation_allowed": True,
                    **receipt, "to_number": to_number, "action_id": action_id}
            state = "unknown" if metadata.get("status") == "unknown" or result.success else "failed"
            await self._update_action_status(tenant_id=tenant_id, action_id=action_id, status=state,
                error=result.error or "No real provider receipt")
            return {"success": False, "status": state, "confirmation_allowed": False,
                "error": result.error or "No real provider receipt", "action_id": action_id}
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                    status="unknown", error="Send cancelled; review provider receipt before retrying"))
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                await self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                    status="unknown", output_data=receipt or None, error=type(exc).__name__)
            except Exception:
                logger.error("SMS receipt persistence failed action=%s", action_id)
            return {"success": False, "status": "unknown", "confirmation_allowed": False,
                "error": "SMS outcome is unconfirmed; do not resend automatically", "action_id": action_id, **receipt}

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

    async def _check_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """Check if an action with this idempotency key already exists."""
        try:
            async with acquire_with_tenant(self.db_pool, str(tenant_id)) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, output_data->>'message_id' AS external_message_id, status
                    FROM assistant_actions
                    WHERE type = 'send_sms'
                    AND tenant_id = $1
                    AND input_data->>'idempotency_key' = $2
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    tenant_id,
                    idempotency_key
                )
                return dict(row) if row else None
        except Exception:
            # A failed lookup is not proof that a previous send is absent.
            raise

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
            "idempotency_key": idempotency_key,
            "meeting_id": meeting_id, "reminder_id": reminder_id
        }

        try:
            async with acquire_with_tenant(self.db_pool, str(tenant_id)) as conn:
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
            raise

    async def _update_action_status(
        self,
        tenant_id: str,
        action_id: str,
        status: str,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> None:
        """Update action record status."""
        if not action_id:
            return

        try:
            async with acquire_with_tenant(self.db_pool, str(tenant_id)) as conn:
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

                query += f" WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}"
                params.append(action_id)
                params.append(tenant_id)

                updated = await conn.execute(query, *params)
                if updated != "UPDATE 1":
                    raise RuntimeError("SMS receipt was not persisted")

        except Exception as e:
            logger.error("Failed to update SMS action status: %s", type(e).__name__)
            raise


# Singleton instance helper
_sms_service: Optional[SMSService] = None


def get_sms_service(db_pool: asyncpg.Pool) -> SMSService:
    """Get or create SMSService instance."""
    global _sms_service
    effective_pool = getattr(db_pool, "pool", db_pool) if hasattr(db_pool, "table") else db_pool
    if _sms_service is None or _sms_service.db_pool is not effective_pool:
        _sms_service = SMSService(db_pool)
    return _sms_service
