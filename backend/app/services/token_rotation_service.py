"""
TokenRotationService - Automatic OAuth Token Refresh

Manages proactive token rotation for connector OAuth tokens.
Refreshes tokens before they expire to ensure uninterrupted service.
"""
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from app.core.postgres_adapter import Client

from app.services.audit_service import get_audit_service

logger = logging.getLogger(__name__)


class TokenRotationError(Exception):
    """Raised when token rotation fails."""
    def __init__(self, connector_id: str, reason: str):
        self.connector_id = connector_id
        self.reason = reason
        self.message = f"Token rotation failed for connector {connector_id}: {reason}"
        super().__init__(self.message)


class TokenRotationService:
    """
    Manages automatic OAuth token refresh.
    
    Features:
    - Proactive refresh before expiry (default 15 min buffer)
    - Batch refresh for multiple connectors
    - Audit logging for all rotations
    - Error handling with retry tracking
    
    Usage:
        service = get_token_rotation_service(db_client)
        
        # Refresh all expiring tokens
        count = await service.refresh_expiring_tokens()
        
        # Refresh specific connector
        success = await service.refresh_token(connector_id)
    """
    
    # Refresh tokens 15 minutes before expiry
    REFRESH_BUFFER_MINUTES = 15
    
    def __init__(self, db_client: Client):
        self.db_client = db_client
        self.audit = get_audit_service(db_client)
    
    async def refresh_expiring_tokens(self) -> int:
        """
        Refresh all tokens expiring within the buffer period.
        
        Returns:
            Number of tokens successfully refreshed
        """
        expiring_accounts = await self._get_expiring_accounts()
        
        if not expiring_accounts:
            logger.debug("No tokens expiring soon")
            return 0
        
        logger.info(f"Found {len(expiring_accounts)} tokens expiring within {self.REFRESH_BUFFER_MINUTES} minutes")
        
        refreshed = 0
        for account in expiring_accounts:
            try:
                success = await self.refresh_token(
                    connector_account_id=account["id"],
                    tenant_id=account["tenant_id"],
                    connector_id=account["connector_id"]
                )
                if success:
                    refreshed += 1
            except Exception as e:
                logger.error(f"Failed to refresh token for account {account['id']}: {e}")
                await self.audit.log_token_rotation(
                    tenant_id=account["tenant_id"],
                    connector_id=account["connector_id"],
                    success=False,
                    error=str(e)
                )
        
        logger.info(f"Refreshed {refreshed}/{len(expiring_accounts)} tokens")
        return refreshed
    
    async def refresh_token(
        self,
        connector_account_id: str,
        tenant_id: Optional[str] = None,
        connector_id: Optional[str] = None
    ) -> bool:
        """
        Refresh OAuth token for a specific connector account.
        
        Args:
            connector_account_id: Connector account ID
            tenant_id: Tenant ID (optional, fetched if not provided)
            connector_id: Connector ID (optional, fetched if not provided)
            
        Returns:
            True if refresh successful, False otherwise
        """
        try:
            # Get account details if not provided
            if not tenant_id or not connector_id:
                account = await self._get_account(connector_account_id)
                if not account:
                    logger.error(f"Connector account not found: {connector_account_id}")
                    return False
                tenant_id = account["tenant_id"]
                connector_id = account["connector_id"]
            
            # Get connector type and provider
            connector = await self._get_connector(connector_id)
            if not connector:
                logger.error(f"Connector not found: {connector_id}")
                return False
            
            provider = connector.get("provider", "").lower()
            
            # Route to provider-specific refresh
            if provider == "google":
                success = await self._refresh_google_token(connector_account_id)
            elif provider == "microsoft":
                success = await self._refresh_microsoft_token(connector_account_id)
            else:
                logger.warning(f"Unknown provider for refresh: {provider}")
                success = False
            
            if success:
                # Update rotation tracking
                await self._update_rotation_tracking(connector_account_id)
                
                # Log successful rotation
                await self.audit.log_token_rotation(
                    tenant_id=tenant_id,
                    connector_id=connector_id,
                    success=True
                )
                
                logger.info(f"Token refreshed for connector account {connector_account_id}")
            else:
                await self.audit.log_token_rotation(
                    tenant_id=tenant_id,
                    connector_id=connector_id,
                    success=False,
                    error="Provider refresh failed"
                )
            
            return success
            
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            if tenant_id and connector_id:
                await self.audit.log_token_rotation(
                    tenant_id=tenant_id,
                    connector_id=connector_id,
                    success=False,
                    error=str(e)
                )
            return False
    
    async def _get_expiring_accounts(self) -> List[Dict[str, Any]]:
        """Get all active accounts with tokens expiring soon."""
        try:
            expiry_threshold = datetime.utcnow() + timedelta(minutes=self.REFRESH_BUFFER_MINUTES)
            
            response = self.db_client.table("connector_accounts").select(
                "id, tenant_id, connector_id, token_expires_at"
            ).eq("status", "active").lt(
                "token_expires_at", expiry_threshold.isoformat()
            ).is_("revoked_at", "null").execute()
            
            return response.data or []
            
        except Exception as e:
            logger.error(f"Error fetching expiring accounts: {e}")
            return []
    
    async def _get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Get connector account by ID."""
        try:
            response = self.db_client.table("connector_accounts").select(
                "id, tenant_id, connector_id, status"
            ).eq("id", account_id).single().execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error fetching account: {e}")
            return None
    
    async def _get_connector(self, connector_id: str) -> Optional[Dict[str, Any]]:
        """Get connector by ID."""
        try:
            response = self.db_client.table("connectors").select(
                "id, type, provider"
            ).eq("id", connector_id).single().execute()
            
            return response.data
            
        except Exception as e:
            logger.error(f"Error fetching connector: {e}")
            return None
    
    async def _refresh_google_token(self, account_id: str) -> bool:
        """Refresh Google OAuth token using refresh_token."""
        try:
            from app.infrastructure.connectors.oauth.google import GoogleOAuthConnector
            
            # Get encrypted tokens
            account = self.db_client.table("connector_accounts").select(
                "refresh_token_encrypted"
            ).eq("id", account_id).single().execute()
            
            if not account.data or not account.data.get("refresh_token_encrypted"):
                logger.error(f"No refresh token for account {account_id}")
                return False
            
            # Decrypt and refresh
            oauth = GoogleOAuthConnector()
            new_tokens = await oauth.refresh_access_token(
                account.data["refresh_token_encrypted"]
            )
            
            if not new_tokens:
                return False
            
            # Update tokens in database
            update_data = {
                "access_token_encrypted": new_tokens["access_token_encrypted"],
                "token_expires_at": new_tokens["expires_at"],
                "last_refreshed_at": datetime.utcnow().isoformat()
            }
            
            self.db_client.table("connector_accounts").update(
                update_data
            ).eq("id", account_id).execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Google token refresh failed: {e}")
            return False
    
    async def _refresh_microsoft_token(self, account_id: str) -> bool:
        """Refresh Microsoft OAuth token using refresh_token."""
        try:
            from app.infrastructure.connectors.oauth.microsoft import MicrosoftOAuthConnector
            
            # Get encrypted tokens
            account = self.db_client.table("connector_accounts").select(
                "refresh_token_encrypted"
            ).eq("id", account_id).single().execute()
            
            if not account.data or not account.data.get("refresh_token_encrypted"):
                logger.error(f"No refresh token for account {account_id}")
                return False
            
            # Decrypt and refresh
            oauth = MicrosoftOAuthConnector()
            new_tokens = await oauth.refresh_access_token(
                account.data["refresh_token_encrypted"]
            )
            
            if not new_tokens:
                return False
            
            # Update tokens in database
            update_data = {
                "access_token_encrypted": new_tokens["access_token_encrypted"],
                "token_expires_at": new_tokens["expires_at"],
                "last_refreshed_at": datetime.utcnow().isoformat()
            }
            
            self.db_client.table("connector_accounts").update(
                update_data
            ).eq("id", account_id).execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Microsoft token refresh failed: {e}")
            return False
    
    async def _update_rotation_tracking(self, account_id: str) -> None:
        """Update rotation count and timestamp."""
        try:
            self.db_client.table("connector_accounts").update({
                "token_last_rotated_at": datetime.utcnow().isoformat(),
                "rotation_count": self.db_client.table("connector_accounts")
                    .select("rotation_count")
                    .eq("id", account_id)
                    .single()
                    .execute()
                    .data.get("rotation_count", 0) + 1
            }).eq("id", account_id).execute()
        except Exception as e:
            # Non-critical, just log
            logger.warning(f"Failed to update rotation tracking: {e}")
    
    async def get_rotation_stats(self, tenant_id: str) -> Dict[str, Any]:
        """Get token rotation statistics for a tenant."""
        try:
            response = self.db_client.table("connector_accounts").select(
                "id, connector_id, status, token_expires_at, token_last_rotated_at, rotation_count"
            ).eq("tenant_id", tenant_id).execute()
            
            accounts = response.data or []
            
            now = datetime.utcnow()
            expiring_soon = 0
            expired = 0
            active = 0
            
            for acc in accounts:
                if acc.get("status") != "active":
                    continue
                
                expires_at = acc.get("token_expires_at")
                if expires_at:
                    expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
                    if expires_dt < now:
                        expired += 1
                    elif expires_dt < now + timedelta(minutes=self.REFRESH_BUFFER_MINUTES):
                        expiring_soon += 1
                    else:
                        active += 1
            
            return {
                "total_accounts": len(accounts),
                "active": active,
                "expiring_soon": expiring_soon,
                "expired": expired,
                "accounts": accounts
            }
            
        except Exception as e:
            logger.error(f"Error getting rotation stats: {e}")
            return {
                "total_accounts": 0,
                "active": 0,
                "expiring_soon": 0,
                "expired": 0,
                "accounts": []
            }


# Singleton instance
_token_rotation_service: Optional[TokenRotationService] = None


def get_token_rotation_service(db_client: Client) -> TokenRotationService:
    """Get or create TokenRotationService instance."""
    global _token_rotation_service
    if _token_rotation_service is None:
        _token_rotation_service = TokenRotationService(db_client)
    return _token_rotation_service
