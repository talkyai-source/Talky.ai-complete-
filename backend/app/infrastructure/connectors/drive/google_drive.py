"""
Google Drive Connector
OAuth 2.0 integration with Google Drive API.

Day 24: Unified Connector System
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode
import httpx

from app.infrastructure.connectors.base import ConnectorFactory, OAuthTokens
from app.infrastructure.connectors.drive.base import DriveProvider, DriveFile

logger = logging.getLogger(__name__)


class GoogleDriveConnector(DriveProvider):
    """
    Google Drive integration using OAuth 2.0.
    
    Setup Required:
    - Enable Google Drive API in Google Cloud Console
    - Create OAuth 2.0 credentials
    - Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars
    
    OAuth Scopes:
    - https://www.googleapis.com/auth/drive.file
    - https://www.googleapis.com/auth/drive.readonly
    """
    
    OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_BASE_URL = "https://www.googleapis.com/drive/v3"
    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"
    
    @property
    def provider_name(self) -> str:
        return "google_drive"
    
    @property
    def oauth_scopes(self) -> List[str]:
        return [
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.readonly"
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
    
    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        parent_folder_id: Optional[str] = None
    ) -> DriveFile:
        """Upload a file to Google Drive."""
        metadata = {"name": name}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]
        
        # Use multipart upload for simplicity
        import json
        
        boundary = "foo_bar_baz"
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--".encode()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.UPLOAD_URL}/files?uploadType=multipart",
                content=body,
                headers={
                    **self._get_auth_headers(),
                    "Content-Type": f"multipart/related; boundary={boundary}"
                }
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"Upload file failed: {response.text}")
                raise ValueError(f"Upload file failed: {response.text}")
            
            data = response.json()
            return self._parse_file(data)
    
    async def download_file(self, file_id: str) -> bytes:
        """Download a file from Google Drive."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/files/{file_id}",
                params={"alt": "media"},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Download file failed: {response.text}")
            
            return response.content
    
    async def list_files(
        self,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        max_results: int = 100
    ) -> List[DriveFile]:
        """List files in Google Drive."""
        params = {
            "pageSize": min(max_results, 1000),
            "fields": "files(id,name,mimeType,size,parents,webViewLink,createdTime,modifiedTime)"
        }
        
        q_parts = ["trashed = false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if query:
            q_parts.append(f"name contains '{query}'")
        
        params["q"] = " and ".join(q_parts)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/files",
                params=params,
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"List files failed: {response.text}")
            
            data = response.json()
            return [self._parse_file(f) for f in data.get("files", [])]
    
    async def create_folder(
        self,
        name: str,
        parent_folder_id: Optional[str] = None
    ) -> DriveFile:
        """Create a folder in Google Drive."""
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE_URL}/files",
                json=metadata,
                headers={**self._get_auth_headers(), "Content-Type": "application/json"}
            )
            
            if response.status_code not in (200, 201):
                raise ValueError(f"Create folder failed: {response.text}")
            
            data = response.json()
            return self._parse_file(data)
    
    async def delete_file(self, file_id: str) -> bool:
        """Delete a file or folder from Google Drive."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.API_BASE_URL}/files/{file_id}",
                headers=self._get_auth_headers()
            )
            
            return response.status_code == 204
    
    async def get_file(self, file_id: str) -> DriveFile:
        """Get file metadata from Google Drive."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.API_BASE_URL}/files/{file_id}",
                params={"fields": "id,name,mimeType,size,parents,webViewLink,createdTime,modifiedTime"},
                headers=self._get_auth_headers()
            )
            
            if response.status_code != 200:
                raise ValueError(f"Get file failed: {response.text}")
            
            return self._parse_file(response.json())
    
    def _parse_file(self, data: Dict[str, Any]) -> DriveFile:
        """Parse Google Drive file response."""
        created_at = None
        if data.get("createdTime"):
            try:
                created_at = datetime.fromisoformat(data["createdTime"].replace("Z", "+00:00"))
            except:
                pass
        
        modified_at = None
        if data.get("modifiedTime"):
            try:
                modified_at = datetime.fromisoformat(data["modifiedTime"].replace("Z", "+00:00"))
            except:
                pass
        
        return DriveFile(
            id=data.get("id"),
            name=data.get("name", ""),
            mime_type=data.get("mimeType"),
            size=int(data["size"]) if data.get("size") else None,
            parent_id=data.get("parents", [None])[0] if data.get("parents") else None,
            web_link=data.get("webViewLink"),
            created_at=created_at,
            modified_at=modified_at,
            is_folder=data.get("mimeType") == "application/vnd.google-apps.folder"
        )


# Register with factory
ConnectorFactory.register("google_drive", GoogleDriveConnector)
