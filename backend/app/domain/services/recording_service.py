"""
Recording Service
Handles recording buffer management and storage for call recordings.
Provider-agnostic - works with any MediaGateway implementation.
"""
import os
import io
import wave
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RecordingBuffer:
    """
    Accumulates audio chunks during a call for later saving.
    
    Used by all MediaGateway implementations (Vonage, RTP, etc.)
    to buffer incoming audio for recording.
    
    Attributes:
        call_id: Unique call identifier
        sample_rate: Audio sample rate (16000 for Vonage, 8000 for RTP/G.711)
        channels: Number of audio channels (1 = mono)
        bit_depth: Bits per sample (16 for PCM16)
    """
    call_id: str
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16
    
    chunks: List[bytes] = field(default_factory=list)
    total_bytes: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
    
    def add_chunk(self, audio_data: bytes) -> None:
        """Add an audio chunk to the buffer."""
        self.chunks.append(audio_data)
        self.total_bytes += len(audio_data)
    
    def get_complete_audio(self) -> bytes:
        """Get all accumulated audio as a single bytes object."""
        return b''.join(self.chunks)
    
    def get_duration_seconds(self) -> float:
        """Calculate total duration in seconds."""
        bytes_per_second = self.sample_rate * self.channels * (self.bit_depth // 8)
        if bytes_per_second == 0:
            return 0.0
        return self.total_bytes / bytes_per_second
    
    def get_wav_bytes(self) -> bytes:
        """
        Convert raw PCM audio to WAV format.
        
        Returns:
            WAV file bytes ready for storage
        """
        audio_data = self.get_complete_audio()
        
        # Create WAV file in memory
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(self.bit_depth // 8)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_data)
        
        wav_buffer.seek(0)
        return wav_buffer.read()
    
    def clear(self) -> None:
        """Clear all accumulated audio data."""
        self.chunks.clear()
        self.total_bytes = 0
    
    def __repr__(self) -> str:
        return (
            f"RecordingBuffer(call_id={self.call_id}, "
            f"bytes={self.total_bytes}, "
            f"duration={self.get_duration_seconds():.1f}s, "
            f"sample_rate={self.sample_rate})"
        )


class RecordingService:
    """
    Handles recording storage operations.
    
    Provider-agnostic service that works with any MediaGateway.
    Uploads recordings to Supabase Storage and links them to call records.
    """
    
    # Storage bucket name in Supabase
    BUCKET_NAME = "recordings"
    
    def __init__(self, supabase_client):
        """
        Initialize the recording service.
        
        Args:
            supabase_client: Initialized Supabase client
        """
        self._supabase = supabase_client
    
    def _generate_storage_path(
        self, 
        call_id: str, 
        tenant_id: str, 
        campaign_id: str
    ) -> str:
        """
        Generate storage path for a recording.
        
        Format: {tenant_id}/{campaign_id}/{call_id}.wav
        
        Args:
            call_id: Call identifier
            tenant_id: Tenant identifier
            campaign_id: Campaign identifier
            
        Returns:
            Storage path string
        """
        # Sanitize IDs to prevent path traversal
        safe_tenant = tenant_id.replace("/", "_").replace("\\", "_") if tenant_id else "default"
        safe_campaign = campaign_id.replace("/", "_").replace("\\", "_") if campaign_id else "unknown"
        safe_call = call_id.replace("/", "_").replace("\\", "_")
        
        return f"{safe_tenant}/{safe_campaign}/{safe_call}.wav"
    
    async def save_recording(
        self,
        call_id: str,
        buffer: RecordingBuffer,
        tenant_id: str,
        campaign_id: str
    ) -> Optional[str]:
        """
        Save recording to Supabase Storage.
        
        Args:
            call_id: Call identifier
            buffer: RecordingBuffer with accumulated audio
            tenant_id: Tenant identifier
            campaign_id: Campaign identifier
            
        Returns:
            Storage path if successful, None otherwise
        """
        if not buffer or buffer.total_bytes == 0:
            logger.warning(f"No audio data to save for call {call_id}")
            return None
        
        try:
            # Convert to WAV format
            wav_data = buffer.get_wav_bytes()
            storage_path = self._generate_storage_path(call_id, tenant_id, campaign_id)
            
            logger.info(
                f"Uploading recording for call {call_id}: "
                f"{len(wav_data)} bytes to {storage_path}"
            )
            
            # Upload to Supabase Storage
            self._supabase.storage.from_(self.BUCKET_NAME).upload(
                path=storage_path,
                file=wav_data,
                file_options={"content-type": "audio/wav"}
            )
            
            logger.info(f"Recording uploaded successfully: {storage_path}")
            return storage_path
            
        except Exception as e:
            logger.error(f"Failed to upload recording for call {call_id}: {e}")
            return None
    
    async def create_recording_record(
        self,
        call_id: str,
        storage_path: str,
        duration_seconds: float,
        file_size_bytes: int,
        tenant_id: str
    ) -> Optional[str]:
        """
        Create a record in the recordings table.
        
        Args:
            call_id: Call identifier
            storage_path: Path in storage bucket
            duration_seconds: Recording duration
            file_size_bytes: File size in bytes
            tenant_id: Tenant identifier
            
        Returns:
            Recording ID if successful, None otherwise
        """
        try:
            result = self._supabase.table("recordings").insert({
                "call_id": call_id,
                "storage_path": storage_path,
                "duration_seconds": int(duration_seconds),
                "file_size_bytes": file_size_bytes,
                "tenant_id": tenant_id,
                "status": "completed",
                "mime_type": "audio/wav"
            }).execute()
            
            if result.data and len(result.data) > 0:
                recording_id = result.data[0].get("id")
                logger.info(f"Recording record created: {recording_id}")
                return recording_id
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to create recording record: {e}")
            return None
    
    async def update_call_recording_url(
        self, 
        call_id: str, 
        storage_path: str
    ) -> bool:
        """
        Update the calls table with recording URL.
        
        Args:
            call_id: Call identifier
            storage_path: Path to recording in storage
            
        Returns:
            True if successful
        """
        try:
            # Generate public URL for the recording
            recording_url = f"/api/v1/recordings/stream/{storage_path}"
            
            self._supabase.table("calls").update({
                "recording_url": recording_url,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", call_id).execute()
            
            logger.info(f"Updated call {call_id} with recording_url")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update call recording_url: {e}")
            return False
    
    async def save_and_link(
        self,
        call_id: str,
        buffer: RecordingBuffer,
        tenant_id: str,
        campaign_id: str
    ) -> Optional[str]:
        """
        Save recording and link to call record.
        
        Complete workflow:
        1. Upload to Supabase Storage
        2. Insert into recordings table
        3. Update calls.recording_url
        
        Args:
            call_id: Call identifier
            buffer: RecordingBuffer with audio
            tenant_id: Tenant identifier
            campaign_id: Campaign identifier
            
        Returns:
            Recording ID if successful, None otherwise
        """
        # Step 1: Upload to storage
        storage_path = await self.save_recording(
            call_id, buffer, tenant_id, campaign_id
        )
        
        if not storage_path:
            return None
        
        # Step 2: Create recording record
        recording_id = await self.create_recording_record(
            call_id=call_id,
            storage_path=storage_path,
            duration_seconds=buffer.get_duration_seconds(),
            file_size_bytes=len(buffer.get_wav_bytes()),
            tenant_id=tenant_id
        )
        
        # Step 3: Update calls table
        await self.update_call_recording_url(call_id, storage_path)
        
        return recording_id
    
    def get_recording_url(self, storage_path: str) -> str:
        """
        Get a public URL for a recording.
        
        Args:
            storage_path: Path in storage bucket
            
        Returns:
            Public URL string
        """
        try:
            # Get signed URL (valid for 1 hour)
            result = self._supabase.storage.from_(self.BUCKET_NAME).create_signed_url(
                path=storage_path,
                expires_in=3600
            )
            return result.get("signedURL", "")
        except Exception as e:
            logger.error(f"Failed to get signed URL: {e}")
            return ""
