"""
CRM Sync Service
Orchestrates CRM synchronization after calls.

Day 30: CRM & Drive Integration
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CRMSyncResult(BaseModel):
    """Result of CRM synchronization attempt."""
    success: bool
    crm_contact_id: Optional[str] = None
    crm_call_id: Optional[str] = None
    crm_note_id: Optional[str] = None
    warning_message: Optional[str] = None
    error_message: Optional[str] = None
    skipped: bool = False
    skipped_reason: Optional[str] = None


class CRMNotConnectedWarning:
    """Warning messages for missing CRM connection."""
    
    MISSING_CRM = (
        "⚠️ CRM not connected. Call data was saved locally but not synced to your CRM. "
        "Connect HubSpot in Settings > Integrations to enable:\n"
        "• Automatic lead creation/updates\n"
        "• Call logging with summaries\n"
        "• Meeting attachment to contacts"
    )
    
    TOKEN_EXPIRED = (
        "⚠️ CRM connection expired. Please reconnect HubSpot in Settings > Integrations "
        "to resume automatic call logging."
    )


class CRMSyncService:
    """
    Orchestrates CRM synchronization after calls.
    
    Features:
    - Find or create contact by email/phone
    - Log call with summary and duration
    - Attach meeting details if applicable
    - Handle missing CRM connector gracefully (hybrid approach)
    
    Day 30: CRM & Drive Integration
    """
    
    def __init__(self, db_client):
        """
        Initialize CRM sync service.
        
        Args:
            db_client: PostgreSQL client for database operations
        """
        self.db_client = db_client
        self._connector_cache: Dict[str, Any] = {}
    
    async def sync_call(
        self,
        tenant_id: str,
        call_id: str,
        lead_data: Dict[str, Any],
        call_summary: str,
        duration_seconds: int,
        outcome: str = "completed",
        meeting_id: Optional[str] = None,
        drive_recording_link: Optional[str] = None,
        drive_transcript_link: Optional[str] = None
    ) -> CRMSyncResult:
        """
        Main CRM sync method called from _save_call_data().
        
        Uses hybrid approach:
        - If CRM connected: sync call data
        - If CRM not connected: return warning for user display
        
        Args:
            tenant_id: Tenant UUID
            call_id: Internal call UUID
            lead_data: Lead information (lead_id, email, phone, name)
            call_summary: Call transcript summary (first 500 chars)
            duration_seconds: Call duration
            outcome: Call outcome (completed, no_answer, etc.)
            meeting_id: Optional meeting ID if booking occurred
            drive_recording_link: Optional Google Drive link to recording
            drive_transcript_link: Optional Google Drive link to transcript
            
        Returns:
            CRMSyncResult with success status and any warnings
        """
        try:
            # 1. Check if CRM connector is available
            connector = await self._get_crm_connector(tenant_id)
            
            if connector is None:
                # No CRM connected - return warning (hybrid approach)
                logger.info(f"No CRM connector for tenant {tenant_id[:8]}... - skipping sync")
                return CRMSyncResult(
                    success=False,
                    skipped=True,
                    skipped_reason="no_crm_connected",
                    warning_message=CRMNotConnectedWarning.MISSING_CRM
                )
            
            # 2. Get or create lead in database
            lead_id = lead_data.get("lead_id")
            lead = await self._get_lead(lead_id)
            
            if not lead:
                logger.warning(f"Lead {lead_id} not found for CRM sync")
                return CRMSyncResult(
                    success=False,
                    error_message=f"Lead {lead_id} not found"
                )
            
            # 3. Find or create contact in CRM
            crm_contact_id = lead.get("crm_contact_id")
            
            if not crm_contact_id:
                # Try to find by email or create new
                email = lead.get("email")
                if email:
                    existing = await connector.find_contact_by_email(email)
                    if existing:
                        crm_contact_id = existing.id
                    else:
                        # Create new contact
                        new_contact = await connector.create_contact(
                            email=email,
                            first_name=lead.get("first_name"),
                            last_name=lead.get("last_name"),
                            phone=lead.get("phone_number")
                        )
                        crm_contact_id = new_contact.id
                        logger.info(f"Created CRM contact {crm_contact_id} for lead {lead_id}")
                    
                    # Update lead with CRM contact ID
                    await self._update_lead_crm_id(lead_id, crm_contact_id)
                else:
                    logger.warning(f"Lead {lead_id} has no email - cannot sync to CRM")
                    return CRMSyncResult(
                        success=False,
                        skipped=True,
                        skipped_reason="no_email",
                        warning_message="Lead has no email address - CRM sync skipped"
                    )
            
            # 4. Log call in CRM
            crm_call_id = await connector.log_call(
                contact_id=crm_contact_id,
                call_body=call_summary,
                duration_seconds=duration_seconds,
                outcome=self._map_outcome(outcome),
                call_direction="OUTBOUND",
                timestamp=datetime.utcnow()
            )
            
            # 5. Create note with Drive links if available
            crm_note_id = None
            if drive_recording_link or drive_transcript_link:
                note_body = self._build_note_body(
                    call_id=call_id,
                    duration_seconds=duration_seconds,
                    recording_link=drive_recording_link,
                    transcript_link=drive_transcript_link,
                    meeting_id=meeting_id
                )
                crm_note_id = await connector.create_note(
                    contact_id=crm_contact_id,
                    note_body=note_body
                )
            
            # 6. Update call record with CRM IDs
            await self._update_call_crm_ids(
                call_id=call_id,
                crm_call_id=crm_call_id,
                crm_note_id=crm_note_id
            )
            
            logger.info(
                f"CRM sync complete for call {call_id}: "
                f"contact={crm_contact_id}, call={crm_call_id}, note={crm_note_id}"
            )
            
            return CRMSyncResult(
                success=True,
                crm_contact_id=crm_contact_id,
                crm_call_id=crm_call_id,
                crm_note_id=crm_note_id
            )
            
        except Exception as e:
            logger.error(f"CRM sync failed for call {call_id}: {e}", exc_info=True)
            return CRMSyncResult(
                success=False,
                error_message=str(e)
            )
    
    async def _get_crm_connector(self, tenant_id: str):
        """Get active CRM connector for tenant."""
        try:
            # Check cache first
            cache_key = f"crm_{tenant_id}"
            if cache_key in self._connector_cache:
                return self._connector_cache[cache_key]
            
            # Query for active CRM connector
            result = self.db_client.table("connectors").select(
                "id, provider, type, status, encrypted_tokens"
            ).eq(
                "tenant_id", tenant_id
            ).eq(
                "type", "crm"
            ).eq(
                "status", "active"
            ).limit(1).execute()
            
            if not result.data:
                return None
            
            connector_data = result.data[0]
            
            # Create connector instance
            from app.infrastructure.connectors.base import ConnectorFactory
            from app.infrastructure.connectors.encryption import decrypt_tokens
            
            provider = connector_data["provider"]
            connector = ConnectorFactory.create(
                provider=provider,
                tenant_id=tenant_id,
                connector_id=connector_data["id"]
            )
            
            # Decrypt and set tokens
            tokens = decrypt_tokens(connector_data["encrypted_tokens"])
            await connector.set_access_token(
                token=tokens.get("access_token"),
                expires_at=tokens.get("expires_at")
            )
            
            # Check if token needs refresh
            if connector.is_token_expired():
                refresh_token = tokens.get("refresh_token")
                if refresh_token:
                    new_tokens = await connector.refresh_tokens(refresh_token)
                    await connector.set_access_token(
                        token=new_tokens.access_token,
                        expires_at=new_tokens.expires_at
                    )
                    # Update stored tokens
                    await self._update_connector_tokens(
                        connector_data["id"],
                        new_tokens
                    )
            
            # Cache connector
            self._connector_cache[cache_key] = connector
            return connector
            
        except Exception as e:
            logger.error(f"Failed to get CRM connector for tenant {tenant_id[:8]}...: {e}")
            return None
    
    async def _get_lead(self, lead_id: str) -> Optional[Dict[str, Any]]:
        """Get lead data from database."""
        try:
            result = self.db_client.table("leads").select(
                "id, email, first_name, last_name, phone_number, crm_contact_id"
            ).eq("id", lead_id).limit(1).execute()
            
            if result.data:
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to get lead {lead_id}: {e}")
            return None
    
    async def _update_lead_crm_id(self, lead_id: str, crm_contact_id: str) -> None:
        """Update lead with CRM contact ID."""
        try:
            self.db_client.table("leads").update({
                "crm_contact_id": crm_contact_id,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", lead_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update lead CRM ID: {e}")
    
    async def _update_call_crm_ids(
        self,
        call_id: str,
        crm_call_id: Optional[str],
        crm_note_id: Optional[str]
    ) -> None:
        """Update call record with CRM IDs."""
        try:
            update_data = {
                "crm_synced_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            if crm_call_id:
                update_data["crm_call_id"] = crm_call_id
            if crm_note_id:
                update_data["crm_note_id"] = crm_note_id
            
            self.db_client.table("calls").update(update_data).eq("id", call_id).execute()
        except Exception as e:
            logger.warning(f"Failed to update call CRM IDs: {e}")
    
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
    
    def _map_outcome(self, outcome: str) -> str:
        """Map internal outcome to HubSpot status."""
        mapping = {
            "completed": "COMPLETED",
            "answered": "COMPLETED",
            "no_answer": "NO_ANSWER",
            "busy": "BUSY",
            "failed": "FAILED",
            "canceled": "CANCELED"
        }
        return mapping.get(outcome.lower(), "COMPLETED")
    
    def _build_note_body(
        self,
        call_id: str,
        duration_seconds: int,
        recording_link: Optional[str],
        transcript_link: Optional[str],
        meeting_id: Optional[str]
    ) -> str:
        """Build note body with Drive links."""
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        
        lines = [
            f"📞 **Call Summary**",
            f"Duration: {minutes}m {seconds}s",
            ""
        ]
        
        if recording_link:
            lines.append(f"🎙️ [Recording]({recording_link})")
        
        if transcript_link:
            lines.append(f"📝 [Transcript]({transcript_link})")
        
        if meeting_id:
            lines.append(f"📅 Meeting booked: {meeting_id}")
        
        lines.append("")
        lines.append(f"_Call ID: {call_id[:8]}..._")
        
        return "\n".join(lines)


# Singleton instance
_crm_sync_service: Optional[CRMSyncService] = None


def get_crm_sync_service(db_client) -> CRMSyncService:
    """Get or create CRM sync service singleton."""
    global _crm_sync_service
    if _crm_sync_service is None:
        _crm_sync_service = CRMSyncService(db_client)
    return _crm_sync_service
