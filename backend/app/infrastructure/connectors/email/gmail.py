"""
Gmail Connector
OAuth 2.0 integration with Gmail API.

Day 24: Unified Connector System
"""
import os
import base64
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx

from app.infrastructure.connectors.base import ConnectorFactory, OAuthTokens
from app.infrastructure.connectors.email.base import EmailProvider, EmailMessage

logger = logging.getLogger(__name__)


class GmailConnector(EmailProvider):
    """
    Gmail integration using OAuth 2.0.
    
    Setup Required:
    - Enable Gmail API in Google Cloud Console
    - Create OAuth 2.0 credentials
    - Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars
    
    OAuth Scopes:
    - https://www.googleapis.com/auth/gmail.send
    - https://www.googleapis.com/auth/gmail.readonly
    """
    
    OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
    
    @property
    def provider_name(self) -> str:
        return "gmail"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/userinfo.email"
        ]
    
    def _get_client_credentials(self) -> tuple[str, str]:
        """Get Google OAuth client credentials from environment."""
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise ValueError(
                "Google OAuth credentials not configured. "
                "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
            )
        
        return client_id, client_secret
    
    def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: Optional[str] = None
    ) -> str:
        """Generate Google OAuth authorization URL."""
        client_id, _ = self._get_client_credentials()
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.oauth_scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent"
        }
        
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        
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
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        
        if code_verifier:
            data["code_verifier"] = code_verifier
        
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
                expires_at=expires_at,
                scope=token_data.get("scope")
            )
    
    async def refresh_tokens(self, refresh_token: str) -> OAuthTokens:
        """Refresh access token using refresh token."""
        client_id, client_secret = self._get_client_credentials()
        
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
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
                expires_at=expires_at,
                scope=token_data.get("scope")
            )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers for API requests."""
        if not self._access_token:
            raise ValueError("Access token not set. Call set_access_token() first.")
        return {"Authorization": f"Bearer {self._access_token}"}
    
    async def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        body_html: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> EmailMessage:
        """Send an email via Gmail API."""
        # Create message
        if body_html:
            message = MIMEMultipart("alternative")
            message.attach(MIMEText(body, "plain"))
            message.attach(MIMEText(body_html, "html"))
        else:
            message = MIMEText(body)
        
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        
        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)
        if reply_to:
            message["Reply-To"] = reply_to
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/users/me/messages/send",
                json={"raw": raw_message},
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Send email failed: {response.text}")
                raise ValueError(f"Send email failed: {response.text}")
            
            data = response.json()
            
            return EmailMessage(
                id=data["id"],
                thread_id=data.get("threadId"),
                subject=subject,
                body=body,
                body_html=body_html,
                to=to,
                cc=cc or [],
                bcc=bcc or [],
                sent_at=datetime.utcnow()
            )
    
    async def get_email(self, message_id: str) -> EmailMessage:
        """Get a single email by ID."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/messages/{message_id}",
                params={"format": "full"},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Get email failed: {response.text}")
            
            data = response.json()
            return self._parse_message(data)
    
    async def list_emails(
        self,
        max_results: int = 20,
        query: Optional[str] = None,
        unread_only: bool = False
    ) -> List[EmailMessage]:
        """List emails with optional filtering."""
        params = {"maxResults": max_results}
        
        q_parts = []
        if query:
            q_parts.append(query)
        if unread_only:
            q_parts.append("is:unread")
        if q_parts:
            params["q"] = " ".join(q_parts)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/messages",
                params=params,
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"List emails failed: {response.text}")
            
            data = response.json()
            messages = []
            
            for msg in data.get("messages", []):
                try:
                    full_msg = await self.get_email(msg["id"])
                    messages.append(full_msg)
                except Exception as e:
                    logger.warning(f"Failed to get message {msg['id']}: {e}")
            
            return messages
    
    def _parse_message(self, data: Dict[str, Any]) -> EmailMessage:
        """Parse Gmail API message response."""
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        
        body = ""
        body_html = None
        
        payload = data.get("payload", {})
        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode()
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode()
                elif part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                    body_html = base64.urlsafe_b64decode(part["body"]["data"]).decode()
        
        return EmailMessage(
            id=data["id"],
            thread_id=data.get("threadId"),
            subject=headers.get("subject", ""),
            body=body,
            body_html=body_html,
            from_email=headers.get("from"),
            to=[headers.get("to", "")],
            cc=[headers.get("cc", "")] if headers.get("cc") else [],
            sent_at=datetime.utcfromtimestamp(int(data["internalDate"]) / 1000) if data.get("internalDate") else None
        )
    
    async def get_profile(self) -> Dict[str, str]:
        """Get user's Gmail profile (email address)."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/profile",
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Get profile failed: {response.text}")
            
            return response.json()


# Register with factory
ConnectorFactory.register("gmail", GmailConnector)
