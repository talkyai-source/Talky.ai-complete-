"""
ConnectorRevocationService - OAuth Connector Revocation

Handles proper revocation of OAuth connectors including:
- Revoking tokens with provider APIs
- Updating connector status
- Cleaning up pending actions
- Audit logging
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import httpx
from app.core.postgres_adapter import Client

from app.services.audit_service import get_audit_service

logger = logging.getLogger(__name__)


class ConnectorRevocationError(Exception):
    """Raised when connector revocation fails."""
    def __init__(self, connector_id: str, reason: str):
        self.connector_id = connector_id
        self.reason = reason
        self.message = f"Connector revocation failed: {reason}"
        super().__init__(self.message)


class ConnectorRevocationService:
    """
    Handles OAuth connector revocation.
    
    Features:
    - Token revocation with provider APIs
    - Status transition to 'revoked'
    - Cleanup of pending reminders/actions
    - Full audit trail
    
    Usage:
        service = get_connector_revocation_service(db_client)
        
        result = await service.revoke_connector(
            tenant_id="...",
            connector_id="...",
            reason="user_requested"
        )
    """
    
    # Provider revocation endpoints
    PROVIDER_REVOKE_ENDPOINTS = {
        "google": "https://oauth2.googleapis.com/revoke",
        "microsoft": "https://login.microsoftonline.com/common/oauth2/v2.0/logout"
    }
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
        self.audit = get_audit_service(db_client)
    
    async def revoke_connector(
        self,
        tenant_id: str,
        connector_id: str,
        reason: str = "user_requested",
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Revoke a connector and its OAuth tokens.
        
        Args:
            tenant_id: Tenant ID
            connector_id: Connector ID to revoke
            reason: Reason for revocation (user_requested, security, expired)
            user_id: User who initiated revocation
            
        Returns:
            Result dict with success status and details
        """
        try:
            # Get connector and account details
            connector = await self._get_connector(connector_id, tenant_id)
            if not connector:
                return {
                    "success": False,
                    "error": "Connector not found or access denied"
                }
            
            if connector.get("status") == "revoked":
                return {
                    "success": False,
                    "error": "Connector already revoked"
                }
            
            # Get associated account
            account = await self._get_connector_account(connector_id)
            
            # Revoke with provider (best effort)
            provider_revoked = False
            if account:
                try:
                    provider_revoked = await self._revoke_with_provider(
                        provider=connector.get("provider", ""),
                        access_token_encrypted=account.get("access_token_encrypted")
                    )
                except Exception as e:
                    logger.warning(f"Provider revocation failed (continuing anyway): {e}")
            
            # Update connector status
            await self._update_connector_status(connector_id, reason)
            
            # Update account status
            if account:
                await self._update_account_status(account["id"], reason)
            
            # Cancel pending reminders using this connector
            cancelled_reminders = await self._cancel_pending_reminders(connector_id)
            
            # Log the revocation
            await self.audit.log_connector_event(
                tenant_id=tenant_id,
                connector_id=connector_id,
                event="revoked",
                reason=reason,
                user_id=user_id
            )
            
            logger.info(f"Connector {connector_id} revoked (reason: {reason})")
            
            return {
                "success": True,
                "connector_id": connector_id,
                "provider_revoked": provider_revoked,
                "cancelled_reminders": cancelled_reminders,
                "reason": reason,
                "revoked_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error revoking connector: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def disconnect_connector(
        self,
        tenant_id: str,
        connector_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Disconnect a connector (softer than revoke).
        
        Does not revoke tokens with provider, just marks as disconnected.
        Tokens can still be used to cleanup/sync before full deletion.
        
        Args:
            tenant_id: Tenant ID
            connector_id: Connector ID
            user_id: User who initiated disconnect
            
        Returns:
            Result dict
        """
        try:
            connector = await self._get_connector(connector_id, tenant_id)
            if not connector:
                return {
                    "success": False,
                    "error": "Connector not found"
                }
            
            # Update status to disconnected
            self.db_client.table("connectors").update({
                "status": "disconnected",
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", connector_id).execute()
            
            # Log the disconnect
            await self.audit.log_connector_event(
                tenant_id=tenant_id,
                connector_id=connector_id,
                event="disconnected",
                user_id=user_id
            )
            
            return {
                "success": True,
                "connector_id": connector_id,
                "status": "disconnected"
            }
            
        except Exception as e:
            logger.error(f"Error disconnecting connector: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _get_connector(self, connector_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get connector by ID with tenant verification."""
        try:
            response = self.db_client.table("connectors").select(
                "id, tenant_id, type, provider, status"
            ).eq("id", connector_id).eq("tenant_id", tenant_id).single().execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error fetching connector: {e}")
            return None
    
    async def _get_connector_account(self, connector_id: str) -> Optional[Dict[str, Any]]:
        """Get active connector account."""
        try:
            response = self.db_client.table("connector_accounts").select(
                "id, access_token_encrypted, status"
            ).eq("connector_id", connector_id).eq("status", "active").single().execute()
            
            return response.data
            
        except Exception:
            return None
    
    async def _revoke_with_provider(
        self,
        provider: str,
        access_token_encrypted: Optional[str]
    ) -> bool:
        """
        Revoke token with the OAuth provider.
        
        Args:
            provider: Provider name (google, microsoft)
            access_token_encrypted: Encrypted access token
            
        Returns:
            True if revocation successful
        """
        if not access_token_encrypted:
            return False
        
        provider = provider.lower()
        if provider not in self.PROVIDER_REVOKE_ENDPOINTS:
            logger.warning(f"No revocation endpoint for provider: {provider}")
            return False
        
        try:
            from app.infrastructure.connectors.encryption import decrypt_token
            
            access_token = decrypt_token(access_token_encrypted)
            
            async with httpx.AsyncClient() as client:
                if provider == "google":
                    # Google uses POST with token in body
                    response = await client.post(
                        self.PROVIDER_REVOKE_ENDPOINTS["google"],
                        data={"token": access_token}
                    )
                    return response.status_code == 200
                    
                elif provider == "microsoft":
                    # Microsoft logout endpoint
                    response = await client.get(
                        self.PROVIDER_REVOKE_ENDPOINTS["microsoft"],
                        params={"post_logout_redirect_uri": ""}
                    )
                    # Microsoft returns 200 even if token is invalid
                    return response.status_code in [200, 302]
            
            return False
            
        except Exception as e:
            logger.error(f"Provider revocation error: {e}")
            return False
    
    async def _update_connector_status(self, connector_id: str, reason: str) -> None:
        """Update connector status to revoked."""
        self.db_client.table("connectors").update({
            "status": "revoked",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", connector_id).execute()
    
    async def _update_account_status(self, account_id: str, reason: str) -> None:
        """Update account status to revoked."""
        self.db_client.table("connector_accounts").update({
            "status": "revoked",
            "revoked_at": datetime.utcnow().isoformat(),
            "revoked_reason": reason
        }).eq("id", account_id).execute()
    
    async def _cancel_pending_reminders(self, connector_id: str) -> int:
        """Cancel pending reminders that use this connector."""
        try:
            # Get meetings using this connector
            meetings_response = self.db_client.table("meetings").select(
                "id"
            ).eq("connector_id", connector_id).execute()
            
            meeting_ids = [m["id"] for m in (meetings_response.data or [])]
            
            if not meeting_ids:
                return 0
            
            # Cancel pending reminders for these meetings
            update_response = self.db_client.table("reminders").update({
                "status": "cancelled",
                "error": "Connector revoked"
            }).in_("meeting_id", meeting_ids).eq("status", "pending").execute()
            
            cancelled = len(update_response.data) if update_response.data else 0
            logger.info(f"Cancelled {cancelled} pending reminders for revoked connector")
            
            return cancelled
            
        except Exception as e:
            logger.error(f"Error cancelling reminders: {e}")
            return 0
    
    async def list_connectors(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        connector_type: Optional[str] = None
    ) -> list:
        """List connectors for a tenant."""
        try:
            query = self.db_client.table("connectors").select(
                "id, type, provider, name, status, created_at, updated_at"
            ).eq("tenant_id", tenant_id)
            
            if status:
                query = query.eq("status", status)
            if connector_type:
                query = query.eq("type", connector_type)
            
            response = query.order("created_at", desc=True).execute()
            
            return response.data or []
            
        except Exception as e:
            logger.error(f"Error listing connectors: {e}")
            return []


# Singleton instance
_revocation_service: Optional[ConnectorRevocationService] = None


def get_connector_revocation_service(db_client: Client) -> ConnectorRevocationService:
    """Get or create ConnectorRevocationService instance."""
    global _revocation_service
    if _revocation_service is None:
        _revocation_service = ConnectorRevocationService(db_client)
    return _revocation_service
