"""
Gmail Connector
OAuth 2.0 integration with Gmail API.

Day 24: Unified Connector System
"""
import os
import base64
import binascii
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx

from app.infrastructure.connectors.base import (
    ConnectorFactory,
    ConnectorProviderError,
    OAuthTokens,
)
from app.infrastructure.connectors.email.base import EmailProvider, EmailMessage

logger = logging.getLogger(__name__)

_GMAIL_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_MAX_SUBJECT_CHARS = 500
_MAX_FROM_HEADER_CHARS = 512
_MAX_RECIPIENT_HEADER_CHARS = 2048


def _bounded_header(value: Any, max_chars: int) -> str:
    """Normalize and bound provider-controlled RFC header text."""
    normalized = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1] + "…"


def _gmail_error_from_response(
    response: httpx.Response,
    operation: str,
    *,
    token_endpoint: bool = False,
) -> ConnectorProviderError:
    """Convert a Google response into a stable, non-stringly-typed error."""
    payload: Any = None
    try:
        payload = response.json()
    except Exception:
        payload = None

    provider_code = ""
    provider_message = ""
    if isinstance(payload, dict):
        raw_error = payload.get("error")
        if isinstance(raw_error, dict):
            provider_code = str(raw_error.get("status") or raw_error.get("code") or "")
            provider_message = str(raw_error.get("message") or "")
            details = raw_error.get("errors") or []
            if details and isinstance(details[0], dict):
                provider_code = str(details[0].get("reason") or provider_code)
        elif raw_error:
            provider_code = str(raw_error)
            provider_message = str(payload.get("error_description") or "")

    status = response.status_code
    normalized_code = provider_code.lower()
    # Token-endpoint client failures describe this deployment's OAuth client,
    # not the user's grant. Google commonly returns invalid_client with 401;
    # classify it before the generic 401 branch so we never expire a healthy
    # user connector for a rotated/misconfigured client secret.
    if token_endpoint and normalized_code in {
        "invalid_client",
        "unauthorized_client",
        "redirect_uri_mismatch",
    }:
        category = "configuration"
    elif status == 401 or (token_endpoint and normalized_code == "invalid_grant"):
        category = "authentication"
    elif status == 403 and normalized_code in {
        "ratelimitexceeded",
        "userratelimitexceeded",
        "quotaexceeded",
        "dailylimitexceeded",
    }:
        category = "rate_limit"
    elif status == 403:
        category = "permission"
    elif status == 404:
        category = "not_found"
    elif status == 429:
        category = "rate_limit"
    elif status >= 500:
        category = "temporary"
    else:
        category = "unknown"

    safe_message = provider_message or provider_code or f"Google returned HTTP {status}"
    return ConnectorProviderError(
        provider="gmail",
        operation=operation,
        category=category,
        message=safe_message,
        status_code=status,
        retry_after=response.headers.get("Retry-After"),
    )


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
        
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                error = _gmail_error_from_response(response, "exchange_code", token_endpoint=True)
                logger.error(
                    "Gmail token exchange failed status=%s category=%s",
                    response.status_code,
                    error.category,
                )
                raise error
            
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
        
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.post(
                self.OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                error = _gmail_error_from_response(response, "refresh_tokens", token_endpoint=True)
                logger.error(
                    "Gmail token refresh failed status=%s category=%s",
                    response.status_code,
                    error.category,
                )
                raise error
            
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
        
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{self.API_BASE_URL}/users/me/messages/send",
                json={"raw": raw_message},
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code not in (200, 201):
                error = _gmail_error_from_response(response, "send_email")
                logger.error(
                    "Gmail send failed status=%s category=%s",
                    response.status_code,
                    error.category,
                )
                raise error
            
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
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/messages/{message_id}",
                params={"format": "full"},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise _gmail_error_from_response(response, "get_email")
            
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
        
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/messages",
                params=params,
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise _gmail_error_from_response(response, "list_emails")
            
            data = response.json()
            messages = []
            
            for msg in data.get("messages", []):
                try:
                    # List mode only needs headers + Gmail's bounded snippet.
                    # Fetching format=full for 25 attacker-controlled messages
                    # can retain huge MIME bodies before the tool truncates the
                    # preview. Reserve full payloads for get_email(message_id).
                    summary_response = await client.get(
                        f"{self.API_BASE_URL}/users/me/messages/{msg['id']}",
                        params=[
                            ("format", "metadata"),
                            ("metadataHeaders", "From"),
                            ("metadataHeaders", "To"),
                            ("metadataHeaders", "Cc"),
                            ("metadataHeaders", "Subject"),
                        ],
                        headers=self._get_auth_headers(),
                    )
                    if summary_response.status_code != 200:
                        raise _gmail_error_from_response(
                            summary_response, "list_email_metadata"
                        )
                    messages.append(self._parse_message(summary_response.json()))
                except ConnectorProviderError as exc:
                    # A message can disappear between list and get; that one
                    # item is safe to skip.  Auth, permission, quota, and
                    # provider failures affect the whole operation and must be
                    # surfaced instead of returning a deceptive empty inbox.
                    if exc.category != "not_found":
                        raise
                    logger.info("Gmail message disappeared before fetch: %s", msg["id"])
                except (httpx.RequestError, TimeoutError):
                    # A transport failure is operation-wide, not evidence that
                    # every listed message vanished. Propagate it so callers do
                    # not report a deceptive successful empty/partial inbox.
                    raise
                except Exception as e:
                    logger.warning(
                        "Failed to parse Gmail message id=%s type=%s",
                        msg.get("id"),
                        type(e).__name__,
                    )
                    raise ConnectorProviderError(
                        provider="gmail",
                        operation="list_emails",
                        category="temporary",
                        message="A listed Gmail message could not be read safely.",
                    ) from e
            
            return messages
    
    def _parse_message(self, data: Dict[str, Any]) -> EmailMessage:
        """Parse Gmail API message response."""
        header_limits = {
            "subject": _MAX_SUBJECT_CHARS,
            "from": _MAX_FROM_HEADER_CHARS,
            "to": _MAX_RECIPIENT_HEADER_CHARS,
            "cc": _MAX_RECIPIENT_HEADER_CHARS,
        }
        headers: Dict[str, str] = {}
        raw_headers = data.get("payload", {}).get("headers", [])
        for header in raw_headers if isinstance(raw_headers, list) else []:
            if not isinstance(header, dict):
                continue
            name = str(header.get("name") or "").casefold()
            if name in header_limits and name not in headers:
                headers[name] = _bounded_header(
                    header.get("value"), header_limits[name]
                )

        plain_parts: List[str] = []
        html_parts: List[str] = []

        def decode_part(encoded: Any) -> Optional[str]:
            if not isinstance(encoded, str) or not encoded:
                return None
            try:
                padded = encoded + ("=" * (-len(encoded) % 4))
                raw = base64.urlsafe_b64decode(padded)
                # MIME charset metadata is inconsistent in Gmail payloads.
                # Replacement decoding preserves a readable result instead of
                # dropping the entire message on one non-UTF8 byte.
                return raw.decode("utf-8", errors="replace")
            except (ValueError, binascii.Error):
                return None

        def walk_part(part: Dict[str, Any]) -> None:
            mime_type = str(part.get("mimeType") or "").lower()
            decoded = decode_part((part.get("body") or {}).get("data"))
            if decoded is not None:
                if mime_type == "text/html":
                    html_parts.append(decoded)
                elif mime_type in {"", "text/plain"}:
                    plain_parts.append(decoded)
            for child in part.get("parts") or []:
                if isinstance(child, dict):
                    walk_part(child)

        payload = data.get("payload", {})
        if isinstance(payload, dict):
            walk_part(payload)
        body = "\n".join(part for part in plain_parts if part).strip()
        body_html = "\n".join(part for part in html_parts if part).strip() or None
        if not body:
            # Gmail's top-level snippet is already plain text and remains
            # available for attachment-only, malformed, or HTML-only messages.
            body = str(data.get("snippet") or "").strip()

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
        async with httpx.AsyncClient(timeout=_GMAIL_HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{self.API_BASE_URL}/users/me/profile",
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise _gmail_error_from_response(response, "get_profile")
            
            return response.json()


# Register with factory
ConnectorFactory.register("gmail", GmailConnector)
