"""
Email Service
Orchestrates email connectors with template rendering and audit logging.

Day 26: AI Email System
"""
import logging
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
import asyncpg  # migrated from db_client

from app.infrastructure.connectors.base import ConnectorFactory
from app.infrastructure.connectors.encryption import get_encryption_service
from app.domain.services.email_template_manager import (
    get_email_template_manager,
    EmailTemplateManager,
    EmailContentValidationError
)

logger = logging.getLogger(__name__)


class EmailNotConnectedError(Exception):
    """Raised when user attempts to send email without a connected email provider."""
    def __init__(self, message: str = "No email provider connected. Please connect Gmail from Settings > Integrations."):
        self.message = message
        super().__init__(self.message)


class EmailService:
    """
    Email sending service that bridges email connectors with templates and database.
    
    Responsibilities:
    - Get active email connector for tenant
    - Render email templates
    - Send emails via connector (Gmail API or SMTP fallback)
    - Log all email actions for audit trail
    - Validate email content
    
    Follows the pattern from MeetingService.
    """
    
    def __init__(self, db_pool: asyncpg.Pool, template_manager: Optional[EmailTemplateManager] = None):
        """
        Initialize EmailService.
        
        Args:
            db_pool: PostgreSQL connection pool
            template_manager: Optional template manager (uses singleton if not provided)
        """
        self.db_pool = db_pool
        self.encryption = get_encryption_service()
        self.template_manager = template_manager or get_email_template_manager()
    
    async def _get_active_email_connector(
        self,
        tenant_id: str
    ) -> tuple:
        """
        Get active email connector for tenant.
        
        Returns:
            Tuple of (connector_instance, connector_id, provider)
            
        Raises:
            EmailNotConnectedError: If no active email connector found
        """
        async with self.db_pool.acquire() as conn:
            # Query for active email connectors
            rows = await conn.fetch(
                """
                SELECT id, provider, status 
                FROM connectors 
                WHERE tenant_id = $1 AND type = 'email' AND status = 'active'
                """,
                tenant_id
            )
            
            if not rows:
                logger.warning(f"No active email connector for tenant {tenant_id[:8]}...")
                raise EmailNotConnectedError()
            
            connector_data = rows[0]
            connector_id = str(connector_data["id"])
            provider = connector_data["provider"]
            
            # Get account credentials
            account_row = await conn.fetchrow(
                """
                SELECT access_token_encrypted, refresh_token_encrypted, token_expires_at, account_email
                FROM connector_accounts
                WHERE connector_id = $1 AND status = 'active'
                """,
                connector_id
            )
            
            if not account_row:
                raise EmailNotConnectedError("Email connection expired. Please reconnect from Settings > Integrations.")
            
            account = dict(account_row)
            
            # Decrypt access token
            try:
                access_token = self.encryption.decrypt(account["access_token_encrypted"])
            except Exception as e:
                logger.error(f"Failed to decrypt email token: {e}")
                raise EmailNotConnectedError("Email connection error. Please reconnect.")
            
            # Check token expiry and refresh if needed
            if account.get("token_expires_at"):
                # Handle datetime or string from asyncpg
                expires_at = account["token_expires_at"]
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                
                if datetime.utcnow() >= expires_at:
                    # Token expired, attempt refresh
                    if account.get("refresh_token_encrypted"):
                        try:
                            access_token = await self._refresh_token(
                                connector_id=connector_id,
                                provider=provider,
                                refresh_token_encrypted=account["refresh_token_encrypted"]
                            )
                        except Exception as e:
                            logger.error(f"Token refresh failed: {e}")
                            raise EmailNotConnectedError("Email connection expired. Please reconnect.")
                    else:
                        raise EmailNotConnectedError("Email connection expired. Please reconnect.")
            
            # Create connector instance
            connector = ConnectorFactory.create(
                provider=provider,
                tenant_id=tenant_id,
                connector_id=connector_id
            )
            
            # Set the access token
            await connector.set_access_token(access_token)
            
            logger.info(f"Retrieved email connector for tenant {tenant_id[:8]}: {provider}")
            
            return connector, connector_id, provider
    
    async def _refresh_token(
        self,
        connector_id: str,
        provider: str,
        refresh_token_encrypted: str
    ) -> str:
        """Refresh OAuth token and update database."""
        refresh_token = self.encryption.decrypt(refresh_token_encrypted)
        
        # Create temporary connector for refresh
        temp_connector = ConnectorFactory.create(
            provider=provider,
            tenant_id="temp",
            connector_id=connector_id
        )
        
        new_tokens = await temp_connector.refresh_tokens(refresh_token)
        
        # Encrypt and save new tokens
        new_access_encrypted = self.encryption.encrypt(new_tokens.access_token)
        new_refresh_encrypted = self.encryption.encrypt(
            new_tokens.refresh_token or refresh_token
        )
        
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE connector_accounts SET
                    access_token_encrypted = $1,
                    refresh_token_encrypted = $2,
                    token_expires_at = $3,
                    last_refreshed_at = NOW()
                WHERE connector_id = $4
                """,
                new_access_encrypted,
                new_refresh_encrypted,
                new_tokens.expires_at,
                connector_id
            )
        
        return new_tokens.access_token
    
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
        triggered_by: str = "assistant"
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
                "template_name": template_name
            }
        )
        
        try:
            # Get connector
            connector, connector_id, provider = await self._get_active_email_connector(tenant_id)
            
            # Send email
            result = await connector.send_email(
                to=to,
                subject=subject,
                body=body,
                body_html=body_html,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to
            )
            
            # Update action as completed
            await self._update_action_status(
                action_id=action_id,
                status="completed",
                output_data={
                    "message_id": result.id,
                    "thread_id": result.thread_id,
                    "provider": provider,
                    "recipient_count": len(to)
                }
            )
            
            logger.info(f"Email sent successfully via {provider} to {len(to)} recipients")
            
            return {
                "success": True,
                "message_id": result.id,
                "thread_id": result.thread_id,
                "provider": provider,
                "recipients": to,
                "action_id": action_id
            }
            
        except EmailNotConnectedError:
            # Update action as failed
            await self._update_action_status(
                action_id=action_id,
                status="failed",
                error="No email provider connected"
            )
            raise
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            
            # Update action as failed
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
    
    async def send_templated_email(
        self,
        tenant_id: str,
        template_name: str,
        recipients: List[str],
        context: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Convenience method for sending templated emails.
        """
        return await self.send_email(
            tenant_id=tenant_id,
            to=recipients,
            subject="",  # Will be overridden by template
            body="",     # Will be overridden by template
            template_name=template_name,
            template_context=context,
            **kwargs
        )
    
    async def _create_action_record(
        self,
        tenant_id: str,
        conversation_id: Optional[str],
        lead_ids: Optional[List[str]],
        call_id: Optional[str],
        triggered_by: str,
        input_data: Dict[str, Any]
    ) -> str:
        """Create an action record for audit purposes."""
        import uuid
        action_id = str(uuid.uuid4())
        
        lead_id = lead_ids[0] if lead_ids and len(lead_ids) > 0 else None
        
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO assistant_actions (
                        id, tenant_id, type, status, triggered_by, 
                        conversation_id, call_id, lead_id,
                        input_data, started_at, created_at
                    ) VALUES ($1, $2, 'send_email', 'pending', $3, $4, $5, $6, $7, NOW(), NOW())
                    """,
                    action_id, tenant_id, triggered_by, 
                    conversation_id, call_id, lead_id,
                    json.dumps(input_data)
                )
                
                logger.debug(f"Created email action record: {action_id}")
                return action_id
                
        except Exception as e:
            logger.error(f"Failed to create email action record: {e}")
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
    if _email_service is None:
        _email_service = EmailService(db_pool)
    return _email_service
