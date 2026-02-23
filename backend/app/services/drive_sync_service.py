"""
Drive Sync Service
Orchestrates Google Drive synchronization for call recordings and transcripts.

Day 30: CRM & Drive Integration
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel
import re

logger = logging.getLogger(__name__)


class DriveSyncResult(BaseModel):
    """Result of Drive synchronization attempt."""
    success: bool
    recording_file_id: Optional[str] = None
    recording_link: Optional[str] = None
    transcript_file_id: Optional[str] = None
    transcript_link: Optional[str] = None
    folder_id: Optional[str] = None
    warning_message: Optional[str] = None
    error_message: Optional[str] = None
    skipped: bool = False
    skipped_reason: Optional[str] = None


class DriveNotConnectedWarning:
    """Warning messages for missing Drive connection."""
    
    MISSING_DRIVE = (
        "⚠️ Google Drive not connected. Recordings and transcripts were saved locally but not uploaded to Drive. "
        "Connect Google Drive in Settings > Integrations to enable:\n"
        "• Automatic recording upload\n"
        "• Transcript backup\n"
        "• Shareable links for team access"
    )
    
    TOKEN_EXPIRED = (
        "⚠️ Google Drive connection expired. Please reconnect in Settings > Integrations "
        "to resume automatic file uploads."
    )


class DriveSyncService:
    """
    Orchestrates Google Drive synchronization after calls.
    
    Features:
    - Create tenant-specific folder structure: Talky.ai Calls/{tenant_name}/{YYYY-MM-DD}/
    - Upload call recordings as audio files
    - Upload transcripts as viewer-safe .md files
    - Generate shareable links
    - Store file IDs back to database
    
    Day 30: CRM & Drive Integration
    """
    
    # Root folder name for all Talky.ai calls
    ROOT_FOLDER_NAME = "Talky.ai Calls"
    
    def __init__(self, db_client):
        """
        Initialize Drive sync service.
        
        Args:
            db_client: PostgreSQL client for database operations
        """
        self.db_client = db_client
        self._connector_cache: Dict[str, Any] = {}
    
    async def sync_call_files(
        self,
        tenant_id: str,
        call_id: str,
        recording_bytes: Optional[bytes] = None,
        transcript_text: Optional[str] = None,
        lead_name: Optional[str] = None,
        call_timestamp: Optional[datetime] = None
    ) -> DriveSyncResult:
        """
        Main Drive sync method called from _save_call_data().
        
        Args:
            tenant_id: Tenant UUID
            call_id: Internal call UUID
            recording_bytes: Audio recording as bytes (WAV format)
            transcript_text: Full transcript text
            lead_name: Optional lead name for file naming
            call_timestamp: Call timestamp (defaults to now)
            
        Returns:
            DriveSyncResult with file IDs and links
        """
        try:
            # 1. Check if Drive connector is available
            connector = await self._get_drive_connector(tenant_id)
            
            if connector is None:
                logger.info(f"No Drive connector for tenant {tenant_id[:8]}... - skipping sync")
                return DriveSyncResult(
                    success=False,
                    skipped=True,
                    skipped_reason="no_drive_connected",
                    warning_message=DriveNotConnectedWarning.MISSING_DRIVE
                )
            
            if not recording_bytes and not transcript_text:
                logger.info(f"No files to upload for call {call_id}")
                return DriveSyncResult(
                    success=True,
                    skipped=True,
                    skipped_reason="no_files"
                )
            
            # 2. Get tenant info for folder naming
            tenant = await self._get_tenant(tenant_id)
            tenant_name = tenant.get("business_name", tenant_id[:8]) if tenant else tenant_id[:8]
            tenant_name = self._sanitize_folder_name(tenant_name)
            
            # 3. Create folder hierarchy: Talky.ai Calls/{tenant_name}/{YYYY-MM-DD}/
            if call_timestamp is None:
                call_timestamp = datetime.utcnow()
            date_str = call_timestamp.strftime("%Y-%m-%d")
            
            folder_id = await self._ensure_folder_hierarchy(
                connector=connector,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                date_str=date_str
            )
            
            recording_file_id = None
            recording_link = None
            transcript_file_id = None
            transcript_link = None
            
            # 4. Upload recording if available
            if recording_bytes and len(recording_bytes) > 0:
                recording_name = f"{call_id}.wav"
                
                recording_file = await connector.upload_file(
                    name=recording_name,
                    content=recording_bytes,
                    mime_type="audio/wav",
                    parent_folder_id=folder_id
                )
                recording_file_id = recording_file.id
                recording_link = recording_file.web_link
                
                # Update recordings table
                await self._update_recording_drive_info(
                    call_id=call_id,
                    drive_file_id=recording_file_id,
                    drive_web_link=recording_link
                )
                
                logger.info(f"Uploaded recording to Drive: {recording_name}")
            
            # 5. Upload transcript as viewer-safe .md file
            if transcript_text:
                transcript_name = f"{call_id}_transcript.md"
                
                # Format transcript as viewer-safe markdown
                safe_transcript = self._format_transcript_for_viewer(
                    transcript_text=transcript_text,
                    call_id=call_id,
                    lead_name=lead_name,
                    call_timestamp=call_timestamp
                )
                
                transcript_file = await connector.upload_file(
                    name=transcript_name,
                    content=safe_transcript.encode("utf-8"),
                    mime_type="text/markdown",
                    parent_folder_id=folder_id
                )
                transcript_file_id = transcript_file.id
                transcript_link = transcript_file.web_link
                
                # Update transcripts table
                await self._update_transcript_drive_info(
                    call_id=call_id,
                    drive_file_id=transcript_file_id,
                    drive_web_link=transcript_link
                )
                
                logger.info(f"Uploaded transcript to Drive: {transcript_name}")
            
            logger.info(
                f"Drive sync complete for call {call_id}: "
                f"recording={recording_file_id}, transcript={transcript_file_id}"
            )
            
            return DriveSyncResult(
                success=True,
                recording_file_id=recording_file_id,
                recording_link=recording_link,
                transcript_file_id=transcript_file_id,
                transcript_link=transcript_link,
                folder_id=folder_id
            )
            
        except Exception as e:
            logger.error(f"Drive sync failed for call {call_id}: {e}", exc_info=True)
            return DriveSyncResult(
                success=False,
                error_message=str(e)
            )
    
    async def _get_drive_connector(self, tenant_id: str):
        """Get active Drive connector for tenant."""
        try:
            cache_key = f"drive_{tenant_id}"
            if cache_key in self._connector_cache:
                return self._connector_cache[cache_key]
            
            result = self.db_client.table("connectors").select(
                "id, provider, type, status, encrypted_tokens"
            ).eq(
                "tenant_id", tenant_id
            ).eq(
                "type", "drive"
            ).eq(
                "status", "active"
            ).limit(1).execute()
            
            if not result.data:
                return None
            
            connector_data = result.data[0]
            
            from app.infrastructure.connectors.base import ConnectorFactory
            from app.infrastructure.connectors.encryption import decrypt_tokens
            
            provider = connector_data["provider"]
            connector = ConnectorFactory.create(
                provider=provider,
                tenant_id=tenant_id,
                connector_id=connector_data["id"]
            )
            
            tokens = decrypt_tokens(connector_data["encrypted_tokens"])
            await connector.set_access_token(
                token=tokens.get("access_token"),
                expires_at=tokens.get("expires_at")
            )
            
            if connector.is_token_expired():
                refresh_token = tokens.get("refresh_token")
                if refresh_token:
                    new_tokens = await connector.refresh_tokens(refresh_token)
                    await connector.set_access_token(
                        token=new_tokens.access_token,
                        expires_at=new_tokens.expires_at
                    )
                    await self._update_connector_tokens(
                        connector_data["id"],
                        new_tokens
                    )
            
            self._connector_cache[cache_key] = connector
            return connector
            
        except Exception as e:
            logger.error(f"Failed to get Drive connector for tenant {tenant_id[:8]}...: {e}")
            return None
    
    async def _get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant info for folder naming."""
        try:
            result = self.db_client.table("tenants").select(
                "id, business_name"
            ).eq("id", tenant_id).limit(1).execute()
            
            if result.data:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get tenant {tenant_id}: {e}")
            return None
    
    async def _ensure_folder_hierarchy(
        self,
        connector,
        tenant_id: str,
        tenant_name: str,
        date_str: str
    ) -> str:
        """
        Ensure folder hierarchy exists and return date folder ID.
        
        Structure: Talky.ai Calls/{tenant_name}/{YYYY-MM-DD}/
        """
        # Check if we have cached root folder ID
        settings = await self._get_tenant_settings(tenant_id)
        root_folder_id = settings.get("drive_root_folder_id") if settings else None
        
        # Level 1: Root folder "Talky.ai Calls"
        if not root_folder_id:
            root_files = await connector.list_files(
                query=self.ROOT_FOLDER_NAME,
                max_results=5
            )
            root_folder = next(
                (f for f in root_files if f.name == self.ROOT_FOLDER_NAME and f.is_folder),
                None
            )
            if not root_folder:
                root_folder = await connector.create_folder(self.ROOT_FOLDER_NAME)
            root_folder_id = root_folder.id
            
            # Save root folder ID for future use
            await self._update_tenant_drive_folder(tenant_id, root_folder_id)
        
        # Level 2: Tenant folder
        tenant_folder_id = None
        tenant_files = await connector.list_files(
            folder_id=root_folder_id,
            query=tenant_name,
            max_results=10
        )
        tenant_folder = next(
            (f for f in tenant_files if f.name == tenant_name and f.is_folder),
            None
        )
        if not tenant_folder:
            tenant_folder = await connector.create_folder(
                name=tenant_name,
                parent_folder_id=root_folder_id
            )
        tenant_folder_id = tenant_folder.id
        
        # Level 3: Date folder
        date_files = await connector.list_files(
            folder_id=tenant_folder_id,
            query=date_str,
            max_results=5
        )
        date_folder = next(
            (f for f in date_files if f.name == date_str and f.is_folder),
            None
        )
        if not date_folder:
            date_folder = await connector.create_folder(
                name=date_str,
                parent_folder_id=tenant_folder_id
            )
        
        return date_folder.id
    
    async def _get_tenant_settings(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant settings including Drive folder ID."""
        try:
            result = self.db_client.table("tenant_settings").select(
                "drive_root_folder_id"
            ).eq("tenant_id", tenant_id).limit(1).execute()
            
            if result.data:
                return result.data[0]
            return None
        except Exception:
            return None
    
    async def _update_tenant_drive_folder(self, tenant_id: str, folder_id: str) -> None:
        """Update tenant settings with Drive root folder ID."""
        try:
            # Upsert tenant settings
            self.db_client.table("tenant_settings").upsert({
                "tenant_id": tenant_id,
                "drive_root_folder_id": folder_id,
                "updated_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to update tenant Drive folder: {e}")
    
    async def _update_recording_drive_info(
        self,
        call_id: str,
        drive_file_id: str,
        drive_web_link: str
    ) -> None:
        """Update recordings table with Drive file info."""
        try:
            self.db_client.table("recordings").update({
                "drive_file_id": drive_file_id,
                "drive_web_link": drive_web_link
            }).eq("call_id", call_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update recording Drive info: {e}")
    
    async def _update_transcript_drive_info(
        self,
        call_id: str,
        drive_file_id: str,
        drive_web_link: str
    ) -> None:
        """Update transcripts table with Drive file info."""
        try:
            self.db_client.table("transcripts").update({
                "drive_file_id": drive_file_id,
                "drive_web_link": drive_web_link
            }).eq("call_id", call_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update transcript Drive info: {e}")
    
    async def _update_connector_tokens(self, connector_id: str, tokens) -> None:
        """Update connector with refreshed tokens."""
        try:
            from app.infrastructure.connectors.encryption import encrypt_tokens
            
            encrypted = encrypt_tokens({
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None
            })
            
            self.db_client.table("connectors").update({
                "encrypted_tokens": encrypted,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", connector_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update connector tokens: {e}")
    
    def _sanitize_folder_name(self, name: str) -> str:
        """Sanitize folder name for Drive compatibility."""
        # Remove/replace invalid characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
        # Limit length
        return sanitized[:50].strip()
    
    def _format_transcript_for_viewer(
        self,
        transcript_text: str,
        call_id: str,
        lead_name: Optional[str],
        call_timestamp: datetime
    ) -> str:
        """
        Format transcript as viewer-safe markdown.
        
        Escapes raw markdown symbols that could cause display issues.
        """
        # Escape markdown special characters that might be in transcript
        # But preserve intentional formatting
        def escape_markdown(text: str) -> str:
            """Escape markdown special characters."""
            # Replace potential markdown in user speech that shouldn't render
            text = text.replace('`', "'")  # Backticks to single quotes
            text = text.replace('*', "\\*")  # Asterisks
            text = text.replace('_', "\\_")  # Underscores
            text = text.replace('#', "\\#")  # Hash symbols (but not at start of lines)
            return text
        
        # Build safe markdown content
        lines = [
            "# Call Transcript",
            "",
            f"**Call ID:** `{call_id}`",
            f"**Date:** {call_timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        
        if lead_name:
            lines.append(f"**Lead:** {escape_markdown(lead_name)}")
        
        lines.extend([
            "",
            "---",
            "",
            "## Conversation",
            "",
        ])
        
        # Format transcript with proper speaker labels
        # Assuming format: "User: text" or "Assistant: text"
        for line in transcript_text.split('\n'):
            line = line.strip()
            if not line:
                lines.append("")
                continue
            
            if line.startswith("User:"):
                content = escape_markdown(line[5:].strip())
                lines.append(f"> **User:** {content}")
            elif line.startswith("Assistant:"):
                content = escape_markdown(line[10:].strip())
                lines.append(f"**Assistant:** {content}")
            else:
                lines.append(escape_markdown(line))
        
        lines.extend([
            "",
            "---",
            "",
            f"*Generated by Talky.ai on {datetime.utcnow().strftime('%Y-%m-%d')}*"
        ])
        
        return "\n".join(lines)


# Singleton instance
_drive_sync_service: Optional[DriveSyncService] = None


def get_drive_sync_service(db_client) -> DriveSyncService:
    """Get or create Drive sync service singleton."""
    global _drive_sync_service
    if _drive_sync_service is None:
        _drive_sync_service = DriveSyncService(db_client)
    return _drive_sync_service
