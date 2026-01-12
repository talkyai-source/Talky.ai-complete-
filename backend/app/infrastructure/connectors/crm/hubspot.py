"""
HubSpot CRM Connector
OAuth 2.0 integration with HubSpot CRM API.

Day 24: Unified Connector System
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
import httpx

from app.infrastructure.connectors.base import ConnectorFactory, OAuthTokens
from app.infrastructure.connectors.crm.base import CRMProvider, CRMContact, CRMDeal

logger = logging.getLogger(__name__)


class HubSpotConnector(CRMProvider):
    """
    HubSpot CRM integration using OAuth 2.0.
    
    Setup Required:
    - Create HubSpot Developer Account
    - Create OAuth App
    - Set HUBSPOT_CLIENT_ID and HUBSPOT_CLIENT_SECRET env vars
    
    OAuth Scopes:
    - crm.objects.contacts.read
    - crm.objects.contacts.write
    - crm.objects.deals.read
    - crm.objects.deals.write
    """
    
    OAUTH_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
    OAUTH_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
    API_BASE_URL = "https://api.hubapi.com/crm/v3"
    
    @property
    def provider_name(self) -> str:
        return "hubspot"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "crm.objects.contacts.read",
            "crm.objects.contacts.write",
            "crm.objects.deals.read",
            "crm.objects.deals.write"
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
        code_challenge: Optional[str] = None
    ) -> str:
        """Generate HubSpot OAuth authorization URL."""
        client_id, _ = self._get_client_credentials()
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.oauth_scopes),
            "state": state
        }
        
        # HubSpot doesn't support PKCE yet, but we include state for CSRF
        return f"{self.OAUTH_AUTH_URL}?{urlencode(params)}"
    
    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None
    ) -> OAuthTokens:
        """Exchange authorization code for tokens."""
        client_id, client_secret = self._get_client_credentials()
        
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                logger.error(f"Token exchange failed: {response.text}")
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
                expires_at=expires_at
            )
    
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh access token using refresh token."""
        client_id, client_secret = self._get_client_credentials()
        
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed: {response.text}")
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
                expires_at=expires_at
            )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers for API requests."""
        if not self._access_token:
            raise ValueError("Access token not set. Call set_access_token() first.")
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json"
        }
    
    async def create_contact(
        self,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMContact:
        """Create a new HubSpot contact."""
        props = {"email": email}
        
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        if phone:
            props["phone"] = phone
        if company:
            props["company"] = company
        if properties:
            props.update(properties)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/objects/contacts",
                json={"properties": props},
                headers=self._get_auth_headers()
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Create contact failed: {response.text}")
                raise ValueError(f"Create contact failed: {response.text}")
            
            data = response.json()
            return self._parse_contact(data)
    
    async def update_contact(
        self,
        contact_id: str,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        company: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMContact:
        """Update an existing HubSpot contact."""
        props = {}
        
        if email:
            props["email"] = email
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        if phone:
            props["phone"] = phone
        if company:
            props["company"] = company
        if properties:
            props.update(properties)
        
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.API_BASE_URL}/objects/contacts/{contact_id}",
                json={"properties": props},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Update contact failed: {response.text}")
            
            data = response.json()
            return self._parse_contact(data)
    
    async def get_contact(self, contact_id: str) -> CRMContact:
        """Get a contact by ID."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/objects/contacts/{contact_id}",
                params={"properties": "email,firstname,lastname,phone,company"},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Get contact failed: {response.text}")
            
            return self._parse_contact(response.json())
    
    async def list_contacts(
        self,
        limit: int = 100,
        search: Optional[str] = None
    ) -> List[CRMContact]:
        """List contacts with optional search."""
        if search:
            # Use search API
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.API_BASE_URL}/objects/contacts/search",
                    json={
                        "query": search,
                        "limit": limit,
                        "properties": ["email", "firstname", "lastname", "phone", "company"]
                    },
                    headers=self._get_auth_headers()
                )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.API_BASE_URL}/objects/contacts",
                    params={
                        "limit": limit,
                        "properties": "email,firstname,lastname,phone,company"
                    },
                    headers=self._get_auth_headers()
                )
        
        if response.status_code != 200:
            raise ValueError(f"List contacts failed: {response.text}")
        
        data = response.json()
        return [self._parse_contact(c) for c in data.get("results", [])]
    
    async def find_contact_by_email(self, email: str) -> Optional[CRMContact]:
        """Find a contact by email address."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/objects/contacts/search",
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email
                        }]
                    }],
                    "properties": ["email", "firstname", "lastname", "phone", "company"]
                },
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Search contact failed: {response.text}")
            
            data = response.json()
            results = data.get("results", [])
            
            if results:
                return self._parse_contact(results[0])
            return None
    
    async def create_deal(
        self,
        name: str,
        stage: str,
        amount: Optional[float] = None,
        close_date: Optional[datetime] = None,
        contact_ids: Optional[List[str]] = None,
        properties: Optional[Dict[str, Any]] = None
    ) -> CRMDeal:
        """Create a new HubSpot deal."""
        props = {
            "dealname": name,
            "dealstage": stage
        }
        
        if amount is not None:
            props["amount"] = str(amount)
        if close_date:
            props["closedate"] = close_date.strftime("%Y-%m-%d")
        if properties:
            props.update(properties)
        
        body = {"properties": props}
        
        if contact_ids:
            body["associations"] = [
                {
                    "to": {"id": cid},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}]
                }
                for cid in contact_ids
            ]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/objects/deals",
                json=body,
                headers=self._get_auth_headers()
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Create deal failed: {response.text}")
                raise ValueError(f"Create deal failed: {response.text}")
            
            data = response.json()
            return self._parse_deal(data)
    
    def _parse_contact(self, data: Dict[str, Any]) -> CRMContact:
        """Parse HubSpot contact response."""
        props = data.get("properties", {})
        
        created_at = None
        if "createdate" in props:
            try:
                created_at = datetime.fromisoformat(props["createdate"].replace("Z", "+00:00"))
            except:
                pass
        
        return CRMContact(
            id=data.get("id"),
            email=props.get("email"),
            first_name=props.get("firstname"),
            last_name=props.get("lastname"),
            phone=props.get("phone"),
            company=props.get("company"),
            properties=props,
            created_at=created_at
        )
    
    def _parse_deal(self, data: Dict[str, Any]) -> CRMDeal:
        """Parse HubSpot deal response."""
        props = data.get("properties", {})
        
        amount = None
        if props.get("amount"):
            try:
                amount = float(props["amount"])
            except:
                pass
        
        close_date = None
        if props.get("closedate"):
            try:
                close_date = datetime.fromisoformat(props["closedate"].replace("Z", "+00:00"))
            except:
                pass
        
        return CRMDeal(
            id=data.get("id"),
            name=props.get("dealname", ""),
            stage=props.get("dealstage"),
            amount=amount,
            close_date=close_date,
            properties=props
        )


# Register with factory
ConnectorFactory.register("hubspot", HubSpotConnector)
