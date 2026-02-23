"""
HubSpot CRM Connector
OAuth 2.0 integration with HubSpot CRM API.

Day 24: Unified Connector System
Day 30: Added log_call() and create_note() methods
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
import httpx

from app.infrastructure.connectors.base import ConnectorFactory, OAuthTokens
from app.infrastructure.connectors.crm.base import CRMProvider

logger = logging.getLogger(__name__)


class HubSpotConnector(CRMProvider):
    """
    HubSpot CRM integration using OAuth 2.0.

    Setup Required:
    - Create a HubSpot developer app
    - Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET env vars

    OAuth Scopes:
    - crm.objects.contacts.read
    - crm.objects.contacts.write
    - crm.objects.deals.read
    - crm.objects.deals.write
    """

    OAUTH_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
    OAUTH_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
    API_BASE_URL = "https://api.hubapi.com"

    @property
    def provider_name(self) -> str:
        return "hubspot"

    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.deals.read",
            "crm.objects.deals.write",
        ]

    def _get_client_credentials(self) -> tuple[str, str]:
        """Get HubSpot OAuth client credentials from environment."""
        client_id = os.getenv("HUBSPOT_CLIENT_ID")
        client_secret = os.getenv("HUBSPOT_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ValueError(
                "HubSpot OAuth credentials not configured. "
                "Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET environment variables."
            )
        return client_id, client_secret

    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None,
    ) -> str:
        """Generate HubSpot OAuth authorization URL."""
        client_id, _ = self._get_client_credentials()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth_scopes),
            "state": state,
        }
        return f"{self.OAUTH_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuthTokens:
        """Exchange authorization code for tokens."""
        client_id, client_secret = self._get_client_credentials()
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                logger.error(f"HubSpot token exchange failed: {response.text}")
                raise ValueError(f"Token exchange failed: {response.text}")
            token_data = response.json()
            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])
            return OAuthTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in"),
                expires_at=expires_at,
                scope=token_data.get("scope"),
            )

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh access token using refresh token."""
        client_id, client_secret = self._get_client_credentials()
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                logger.error(f"HubSpot token refresh failed: {response.text}")
                raise ValueError(f"Token refresh failed: {response.text}")
            token_data = response.json()
            expires_at = None
            if "expires_in" in token_data:
                expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])
            return OAuthTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", refresh_token),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in"),
                expires_at=expires_at,
                scope=token_data.get("scope"),
            )

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise ValueError("Access token not set. Call set_access_token() first.")
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # CRM-specific methods
    # ------------------------------------------------------------------

    async def search_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Search for a contact in HubSpot by email or phone."""
        filters = []
        if email:
            filters.append({"propertyName": "email", "operator": "EQ", "value": email})
        if phone:
            filters.append({"propertyName": "phone", "operator": "EQ", "value": phone})
        if not filters:
            return None

        body = {
            "filterGroups": [{"filters": filters}],
            "properties": ["email", "firstname", "lastname", "phone"],
            "limit": 1,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/crm/v3/objects/contacts/search",
                json=body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"},
            )
            if response.status_code != 200:
                logger.error(f"HubSpot contact search failed: {response.text}")
                return None
            results = response.json().get("results", [])
            if results:
                r = results[0]
                return {"id": r["id"], **r.get("properties", {})}
            return None

    async def create_contact(
        self,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new contact in HubSpot."""
        props: Dict[str, Any] = {"email": email}
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        if phone:
            props["phone"] = phone
        if properties:
            props.update(properties)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/crm/v3/objects/contacts",
                json={"properties": props},
                headers={**self._get_auth_headers(), "Content-Type": "application/json"},
            )
            if response.status_code not in (200, 201):
                logger.error(f"HubSpot create contact failed: {response.text}")
                raise ValueError(f"Create contact failed: {response.text}")
            data = response.json()
            return {"id": data["id"], **data.get("properties", {})}

    async def log_call(
        self,
        contact_id: str,
        call_body: str,
        duration_seconds: int,
        outcome: str = "COMPLETED",
        call_direction: str = "OUTBOUND",
        timestamp: Optional[datetime] = None,
    ) -> str:
        """Log a call activity in HubSpot using POST /crm/v3/objects/calls."""
        ts = timestamp or datetime.utcnow()
        call_props = {
            "hs_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "hs_call_body": call_body,
            "hs_call_duration": str(int(duration_seconds * 1000)),  # HubSpot uses ms
            "hs_call_status": outcome,
            "hs_call_direction": call_direction,
        }
        body = {
            "properties": call_props,
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 194}],
                }
            ],
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/crm/v3/objects/calls",
                json=body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"},
            )
            if response.status_code not in (200, 201):
                logger.error(f"HubSpot log call failed: {response.text}")
                raise ValueError(f"Log call failed: {response.text}")
            return response.json()["id"]

    async def create_note(
        self,
        contact_id: str,
        note_body: str,
        timestamp: Optional[datetime] = None,
    ) -> str:
        """Create a note attached to a contact using POST /crm/v3/objects/notes."""
        ts = timestamp or datetime.utcnow()
        body = {
            "properties": {
                "hs_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "hs_note_body": note_body,
            },
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }
            ],
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/crm/v3/objects/notes",
                json=body,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"},
            )
            if response.status_code not in (200, 201):
                logger.error(f"HubSpot create note failed: {response.text}")
                raise ValueError(f"Create note failed: {response.text}")
            return response.json()["id"]


# Register with factory
ConnectorFactory.register("hubspot", HubSpotConnector)

