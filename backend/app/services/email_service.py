"""
Email Service
Orchestrates email connectors with template rendering and audit logging.

Day 26: AI Email System
"""

import logging
import asyncio
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg  # migrated from db_client

from app.core.db_utils import acquire_with_tenant
from app.infrastructure.connectors.base import ConnectorProviderError
from app.core.postgres_adapter import PostgresClient
from app.services.connector_resolver import resolve_active_connector, ConnectorNotConnectedError, connector_identity, verify_reviewed_connector
from app.infrastructure.connectors.encryption import get_encryption_service
from app.domain.services.email_template_manager import (
    get_email_template_manager,
    EmailTemplateManager,
    EmailContentValidationError,
)

logger = logging.getLogger(__name__)


class EmailNotConnectedError(Exception):
    """Raised when user attempts to send email without a connected email provider."""

    def __init__(
        self,
        message: str = "No email provider connected. Please connect Gmail from Settings > Integrations.",
    ):
        self.message = message
        super().__init__(self.message)


class EmailService:
    """
    Email sending service that bridges email connectors with templates and database.

    Responsibilities:
    - Get active email connector for tenant
    - Render email templates
    - Send emails through the selected tenant email connector
    - Log all email actions for audit trail
    - Validate email content

    Follows the pattern from MeetingService.
    """

    def __init__(
        self, db_pool: asyncpg.Pool, template_manager: Optional[EmailTemplateManager] = None
    ):
        """
        Initialize EmailService.

        Args:
            db_pool: PostgreSQL connection pool
            template_manager: Optional template manager (uses singleton if not provided)
        """
        self.db_pool = getattr(db_pool, "pool", db_pool) if hasattr(db_pool, "table") else db_pool
        self.supabase = db_pool
        self.encryption = get_encryption_service()
        self.template_manager = template_manager or get_email_template_manager()

    async def _get_active_email_connector(self, tenant_id: str, *, force_refresh=False, connector_id=None) -> tuple:
        """Use the canonical expiry/refresh/write-back path for both DB clients."""
        client = self.supabase if hasattr(self.supabase, "table") else PostgresClient(self.db_pool)
        try:
            options = {"force_refresh": force_refresh}
            if connector_id:
                options["connector_id"] = connector_id
            return await resolve_active_connector(client, tenant_id, "email", **options)
        except ConnectorNotConnectedError as exc:
            raise EmailNotConnectedError(str(exc)) from exc

    async def send_email(
        self,
        tenant_id: str,
        to: List[str],
        subject: str,
        body: str,
        body_html: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        template_name: Optional[str] = None,
        template_context: Optional[Dict[str, Any]] = None,
        lead_ids: Optional[List[str]] = None,
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        triggered_by: str = "assistant",
        connector_id: Optional[str] = None,
        reviewed_connector: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Send an email via connected email provider.
        """
        # Render template if specified
        if template_name and template_context:
            rendered = self.template_manager.render_email(template_name, **template_context)
            subject = rendered.subject
            body = rendered.body
            body_html = rendered.body_html or body_html
            logger.info(f"Rendered email template: {template_name}")

        # Validate content
        self.template_manager.validate_content(subject, body)

        # Create action record
        action_id = await self._create_action_record(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            lead_ids=lead_ids,
            call_id=call_id,
            triggered_by=triggered_by,
            input_data={
                "to": to,
                "cc": cc,
                "bcc": bcc,
                "subject": subject,
                "template_name": template_name,
                "reviewed_connector": reviewed_connector,
            },
        )

        attempted = False
        receipt = {}
        try:
            if reviewed_connector is not None:
                connector_id = reviewed_connector.get("connector_id")
            connector, connector_id, provider = await self._get_active_email_connector(
                tenant_id, **({"connector_id": connector_id} if connector_id else {}))
            identity = (verify_reviewed_connector(connector, connector_id, provider, reviewed_connector)
                        if reviewed_connector is not None else connector_identity(connector, connector_id, provider))
            receipt = dict(identity)
            send_args = dict(to=to, subject=subject, body=body, body_html=body_html, cc=cc, bcc=bcc, reply_to=reply_to)
            attempted = True
            try:
                result = await connector.send_email(**send_args)
            except ConnectorProviderError as exc:
                # Only a definite authentication rejection is safe to resend.
                if exc.category != "authentication" or exc.status_code != 401:
                    raise
                attempted = False
                connector, connector_id, provider = await self._get_active_email_connector(
                    tenant_id, force_refresh=True, connector_id=connector_id)
                identity = verify_reviewed_connector(connector, connector_id, provider, identity)
                attempted = True
                result = await connector.send_email(**send_args)
            message_id = getattr(result, "id", None)
            if not message_id:
                raise RuntimeError("Provider did not return a message receipt")
            receipt = {"message_id": message_id, "thread_id": getattr(result, "thread_id", None),
                       "provider": provider, "connector_id": connector_id, "recipient_count": len(to)}
            if identity:
                receipt["external_account_id"] = identity["external_account_id"]
            await self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                status="completed", output_data=receipt)
            return {"success": True, "status": "accepted", "confirmation_allowed": True,
                    **receipt, "recipients": to, "action_id": action_id}
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                    status="unknown" if attempted else "failed", output_data=receipt or None,
                    error="Send cancelled; review provider receipt before retrying"))
            except Exception:
                pass
            raise
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            rejected = code is not None and 400 <= code < 500 and code != 408
            state = "unknown" if attempted and not rejected else "failed"
            try:
                await self._update_action_status(tenant_id=tenant_id, action_id=action_id,
                    status=state, output_data=receipt or None, error=type(exc).__name__)
            except Exception:
                # The pre-send pending record stays a fence, never a success.
                logger.error("email receipt persistence failed action=%s", action_id)
            if isinstance(exc, EmailNotConnectedError):
                raise
            return {"success": False, "status": state, "confirmation_allowed": False,
                    "error": "Email outcome is unconfirmed; do not resend automatically" if state == "unknown" else str(exc),
                    "action_id": action_id, **receipt}

    async def review_connector(self, tenant_id, connector_id=None):
        return connector_identity(*await self._get_active_email_connector(
            tenant_id, **({"connector_id": connector_id} if connector_id else {})))

    async def send_templated_email(
        self,
        tenant_id: str,
        template_name: str,
        recipients: List[str],
        context: Dict[str, Any],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Convenience method for sending templated emails.
        """
        return await self.send_email(
            tenant_id=tenant_id,
            to=recipients,
            subject="",  # Will be overridden by template
            body="",  # Will be overridden by template
            template_name=template_name,
            template_context=context,
            **kwargs,
        )

    async def _create_action_record(
        self,
        tenant_id: str,
        conversation_id: Optional[str],
        lead_ids: Optional[List[str]],
        call_id: Optional[str],
        triggered_by: str,
        input_data: Dict[str, Any],
    ) -> str:
        """Create an action record for audit purposes."""
        import uuid

        action_id = str(uuid.uuid4())

        lead_id = lead_ids[0] if lead_ids and len(lead_ids) > 0 else None

        try:
            if hasattr(self.supabase, "table"):
                response = (
                    self.supabase.table("assistant_actions")
                    .insert(
                        {
                            "id": action_id,
                            "tenant_id": tenant_id,
                            "type": "send_email",
                            "status": "pending",
                            "triggered_by": triggered_by,
                            "conversation_id": conversation_id,
                            "call_id": call_id,
                            "lead_id": lead_id,
                            "input_data": input_data,
                        }
                    )
                    .execute()
                )
                if getattr(response, "error", None) or not getattr(response, "data", None):
                    raise RuntimeError("Email action was not persisted")
                return str(response.data[0]["id"])

            async with acquire_with_tenant(self.db_pool, str(tenant_id)) as conn:
                await conn.execute(
                    """
                    INSERT INTO assistant_actions (
                        id, tenant_id, type, status, triggered_by, 
                        conversation_id, call_id, lead_id,
                        input_data, started_at, created_at
                    ) VALUES ($1, $2, 'send_email', 'pending', $3, $4, $5, $6, $7, NOW(), NOW())
                    """,
                    action_id,
                    tenant_id,
                    triggered_by,
                    conversation_id,
                    call_id,
                    lead_id,
                    json.dumps(input_data),
                )

                logger.debug(f"Created email action record: {action_id}")
                return action_id

        except Exception as e:
            logger.error(f"Failed to create email action record: {e}")
            raise

    async def _update_action_status(
        self,
        tenant_id: str,
        action_id: str,
        status: str,
        output_data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update action record status."""
        if not action_id:
            return

        try:
            if hasattr(self.supabase, "table"):
                payload: Dict[str, Any] = {"status": status}
                if output_data:
                    payload["output_data"] = output_data
                if error:
                    payload["error"] = error
                response = self.supabase.table("assistant_actions").update(payload).eq(
                    "id", action_id
                ).eq("tenant_id", tenant_id).execute()
                if getattr(response, "error", None) or not getattr(response, "data", None):
                    raise RuntimeError("Email receipt was not persisted")
                return

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
                    raise RuntimeError("Email receipt was not persisted")

        except Exception as e:
            logger.error("Failed to update email action status: %s", type(e).__name__)
            raise

    def list_templates(self) -> List[Dict[str, Any]]:
        """List available email templates."""
        return [
            self.template_manager.get_template_info(name)
            for name in self.template_manager.list_templates()
        ]


# Singleton instance helper
_email_service: Optional[EmailService] = None


def get_email_service(db_pool: asyncpg.Pool) -> EmailService:
    """Get or create EmailService instance."""
    global _email_service
    if _email_service is None or _email_service.supabase is not db_pool:
        _email_service = EmailService(db_pool)
    return _email_service
