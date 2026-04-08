"""
Recording Service — S3-backed object storage.

Replaces the previous PostgreSQL Storage implementation with
S3-compatible object storage (AWS S3, Cloudflare R2, MinIO).

Why S3:
  - Recordings survive server restarts and container recreation
  - Enables horizontal scaling (multiple backend instances share one bucket)
  - Built-in lifecycle policies for retention enforcement
  - Presigned URLs serve audio directly from S3, removing backend from hot path

Configuration (all via environment variables):
  S3_BUCKET_NAME          = talky-recordings           (required)
  S3_REGION               = us-east-1                  (default)
  S3_ACCESS_KEY_ID        = ...                        (required)
  S3_SECRET_ACCESS_KEY    = ...                        (required)
  S3_ENDPOINT_URL         = https://...r2.cloudflarestorage.com
                            (omit for AWS S3, set for R2/MinIO)
  S3_PRESIGNED_URL_EXPIRY = 3600                       (seconds, default 1h)
  S3_STORAGE_CLASS        = STANDARD                   (or INTELLIGENT_TIERING)

Retention lifecycle (set on the bucket, not in this code):
  Basic plan    → 30 days  (set S3 lifecycle rule: Expiration 30 days)
  Professional  → 90 days
  Enterprise    → 365 days
"""
from __future__ import annotations

import io
import logging
import os
import struct
import wave
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional boto3 import — graceful fallback to local storage if not installed
# ---------------------------------------------------------------------------
try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    from botocore.config import Config as BotoCoreConfig
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    logger.warning(
        "boto3 not installed — S3 recording upload disabled. "
        "Install with: pip install boto3"
    )


# ---------------------------------------------------------------------------
# RecordingBuffer (unchanged from original — no behaviour change)
# ---------------------------------------------------------------------------

@dataclass
class RecordingBuffer:
    """
    Accumulates audio chunks during a call for later saving.

    Works with all MediaGateway implementations (Vonage, RTP, etc.)
    """
    call_id: str
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16

    chunks: List[bytes] = field(default_factory=list)
    total_bytes: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)

    def add_chunk(self, audio_data: bytes) -> None:
        self.chunks.append(audio_data)
        self.total_bytes += len(audio_data)

    def get_complete_audio(self) -> bytes:
        return b"".join(self.chunks)

    def get_duration_seconds(self) -> float:
        bps = self.sample_rate * self.channels * (self.bit_depth // 8)
        return (self.total_bytes / bps) if bps else 0.0

    def get_wav_bytes(self) -> bytes:
        # Telephony path pre-mixes a stereo WAV externally (mix_stereo_recording)
        # and stores it here to skip re-encoding. Use it if present.
        override = getattr(self, "_wav_bytes_override", None)
        if override:
            return override
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.bit_depth // 8)
            wf.setframerate(self.sample_rate)
            wf.writeframes(self.get_complete_audio())
        buf.seek(0)
        return buf.read()

    def clear(self) -> None:
        self.chunks.clear()
        self.total_bytes = 0

    def __repr__(self) -> str:
        return (
            f"RecordingBuffer(call_id={self.call_id}, "
            f"bytes={self.total_bytes}, "
            f"duration={self.get_duration_seconds():.1f}s)"
        )


# ---------------------------------------------------------------------------
# S3Client wrapper — thin layer over boto3
# ---------------------------------------------------------------------------

class S3Client:
    """
    Thin wrapper around boto3 S3 client.
    Handles configuration and provides typed methods used by RecordingService.
    """

    def __init__(self) -> None:
        self.bucket = os.getenv("S3_BUCKET_NAME", "talky-recordings")
        self.region = os.getenv("S3_REGION", "us-east-1")
        self.presigned_expiry = int(os.getenv("S3_PRESIGNED_URL_EXPIRY", "3600"))
        self.storage_class = os.getenv("S3_STORAGE_CLASS", "STANDARD")
        endpoint = os.getenv("S3_ENDPOINT_URL")  # None for AWS S3

        if not _BOTO3_AVAILABLE:
            self._client = None
            return

        kwargs: Dict[str, Any] = {
            "service_name": "s3",
            "region_name": self.region,
            "aws_access_key_id": os.getenv("S3_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.getenv("S3_SECRET_ACCESS_KEY"),
            "config": BotoCoreConfig(
                retries={"max_attempts": 3, "mode": "adaptive"},
                max_pool_connections=20,
            ),
        }
        if endpoint:
            kwargs["endpoint_url"] = endpoint

        self._client = boto3.client(**kwargs)

    def is_available(self) -> bool:
        return _BOTO3_AVAILABLE and self._client is not None

    def upload(self, key: str, data: bytes, content_type: str = "audio/wav") -> None:
        """Upload bytes to S3. Raises on failure."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            StorageClass=self.storage_class,
        )

    def presigned_url(self, key: str) -> str:
        """Generate a presigned GET URL valid for presigned_expiry seconds."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presigned_expiry,
        )

    def delete(self, key: str) -> None:
        """Delete an object. Used by retention cleanup."""
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        """Return object metadata or None if not found."""
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise


# ---------------------------------------------------------------------------
# RecordingService
# ---------------------------------------------------------------------------

class RecordingService:
    """
    Handles recording storage for call audio.

    Upload flow:
      1. Convert RecordingBuffer → WAV bytes
      2. Upload WAV to S3 under key: {tenant_id}/{campaign_id}/{call_id}.wav
      3. Insert a row into recordings_s3 with S3 metadata
      4. Update calls.recording_url with the internal stream path

    Retrieval flow:
      - API calls get_presigned_url() → returns a time-limited S3 URL
      - The frontend fetches audio directly from S3 (no backend in the hot path)

    Fallback:
      - If S3 is not configured, recordings are skipped and a warning is logged.
        This prevents call failures due to missing storage config.
    """

    def __init__(self, db_pool: Any, s3_client: Optional[S3Client] = None) -> None:
        self._db = db_pool
        self._s3 = s3_client or S3Client()

    # ── Key generation ────────────────────────────────────────────

    @staticmethod
    def _s3_key(tenant_id: str, campaign_id: str, call_id: str) -> str:
        """
        Build a structured S3 object key.
        Format: {tenant_id}/{campaign_id}/{call_id}.wav
        Sanitised to prevent path traversal.
        """
        def safe(s: str) -> str:
            return s.replace("/", "-").replace("\\", "-").replace("..", "-") if s else "unknown"
        return f"{safe(tenant_id)}/{safe(campaign_id)}/{safe(call_id)}.wav"

    # ── Main workflow ─────────────────────────────────────────────

    async def save_and_link(
        self,
        call_id: str,
        buffer: RecordingBuffer,
        tenant_id: str,
        campaign_id: str,
    ) -> Optional[str]:
        """
        Upload recording to S3 and create DB records.

        Returns recording UUID string on success, None on failure.
        Never raises — storage failures should not break call flow.
        """
        if not buffer or buffer.total_bytes == 0:
            logger.warning(f"No audio to save for call {call_id}")
            return None

        if not self._s3.is_available():
            logger.info(
                f"S3 not configured — saving recording locally for call {call_id}."
            )
            return await self._save_local(call_id, buffer, tenant_id, campaign_id)

        try:
            wav_data = buffer.get_wav_bytes()
            key = self._s3_key(tenant_id, campaign_id, call_id)
            upload_started = datetime.utcnow()

            logger.info(
                f"Uploading recording call={call_id} "
                f"size={len(wav_data)}B bucket={self._s3.bucket} key={key}"
            )
            self._s3.upload(key, wav_data, content_type="audio/wav")
            upload_finished = datetime.utcnow()

            logger.info(f"Recording uploaded: {key}")

            recording_id = await self._insert_recording_record(
                call_id=call_id,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                s3_key=key,
                file_size_bytes=len(wav_data),
                duration_seconds=int(buffer.get_duration_seconds()),
                upload_started=upload_started,
                upload_finished=upload_finished,
            )

            await self._update_call_recording_url(call_id, recording_id)
            return str(recording_id) if recording_id else None

        except Exception as exc:
            logger.error(f"Recording upload failed for call {call_id}: {exc}", exc_info=True)
            await self._mark_upload_failed(call_id, tenant_id, campaign_id, str(exc))
            return None

    # ── Local file fallback ───────────────────────────────────────

    async def _save_local(
        self,
        call_id: str,
        buffer: RecordingBuffer,
        tenant_id: str = "unknown",
        campaign_id: str = "unknown",
    ) -> Optional[str]:
        """
        Save recording to local filesystem when S3 is not configured.
        Also inserts a row into recordings_s3 (status='local') so the
        frontend can list and stream local recordings.

        Directory is controlled by the LOCAL_RECORDINGS_DIR environment variable
        (default: ./recordings relative to the working directory).

        Returns the recording UUID string on success, None on failure.
        """
        recordings_dir = os.getenv("LOCAL_RECORDINGS_DIR", "./recordings")
        os.makedirs(recordings_dir, exist_ok=True)
        abs_dir = os.path.abspath(recordings_dir)
        filepath = os.path.join(abs_dir, f"{call_id}.wav")
        try:
            wav_data = buffer.get_wav_bytes()
            if not wav_data:
                logger.warning(f"No WAV data to save locally for call {call_id}")
                return None
            with open(filepath, "wb") as fh:
                fh.write(wav_data)
            logger.info(
                f"Recording saved locally: {filepath} ({len(wav_data):,} bytes, "
                f"{buffer.get_duration_seconds():.1f}s)"
            )
        except Exception as exc:
            logger.error(f"Local recording save failed for call {call_id}: {exc}", exc_info=True)
            return None

        # Insert DB record so the frontend can find this recording.
        # s3_bucket='local' identifies it as local-disk storage.
        # status='uploaded' satisfies the DB check constraint; the stream
        # endpoint inspects s3_bucket to decide whether to serve locally.
        now = datetime.utcnow()
        recording_id = await self._insert_recording_record(
            call_id=call_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            s3_key=filepath,
            file_size_bytes=len(wav_data),
            duration_seconds=int(buffer.get_duration_seconds()),
            upload_started=now,
            upload_finished=now,
            s3_bucket="local",
            s3_region="local",
            status="uploaded",
        )

        if recording_id:
            await self._update_call_recording_url(call_id, recording_id)
            logger.info(f"Local recording registered in DB: recording_id={recording_id}")
            return str(recording_id)
        else:
            logger.warning(f"Local recording saved to disk but DB insert failed for {call_id}")
            return filepath

    # ── Presigned URL ─────────────────────────────────────────────

    async def get_presigned_url(
        self,
        recording_id: str,
        tenant_id: str,
    ) -> Optional[str]:
        """
        Generate a presigned S3 GET URL for a recording.
        Validates tenant ownership before generating URL.

        Returns URL string or None if not found / access denied.
        """
        if not self._s3.is_available():
            return None

        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT s3_key, status
                FROM recordings_s3
                WHERE id = $1 AND tenant_id = $2
                """,
                UUID(recording_id),
                UUID(tenant_id),
            )

        if not row:
            logger.warning(
                f"Recording {recording_id} not found or tenant {tenant_id} denied"
            )
            return None

        if row["status"] != "uploaded":
            logger.warning(f"Recording {recording_id} status={row['status']} — not accessible")
            return None

        try:
            return self._s3.presigned_url(row["s3_key"])
        except Exception as exc:
            logger.error(f"Failed to generate presigned URL for {recording_id}: {exc}")
            return None

    async def list_for_call(self, call_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        """Return all recording metadata rows for a call."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, call_id, s3_key, file_size_bytes,
                       duration_seconds, status, created_at
                FROM recordings_s3
                WHERE call_id = $1 AND tenant_id = $2
                ORDER BY created_at DESC
                """,
                UUID(call_id),
                UUID(tenant_id),
            )
        return [dict(r) for r in rows]

    # ── Private DB helpers ────────────────────────────────────────

    async def _insert_recording_record(
        self,
        call_id: str,
        tenant_id: str,
        campaign_id: str,
        s3_key: str,
        file_size_bytes: int,
        duration_seconds: int,
        upload_started: datetime,
        upload_finished: datetime,
        s3_bucket: Optional[str] = None,
        s3_region: Optional[str] = None,
        status: str = "uploaded",
    ) -> Optional[UUID]:
        """Insert a row into recordings_s3 and return the new UUID."""
        try:
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO recordings_s3 (
                        call_id, tenant_id, campaign_id,
                        s3_bucket, s3_key, s3_region,
                        file_size_bytes, duration_seconds,
                        status, upload_started_at, upload_finished_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    RETURNING id
                    """,
                    UUID(call_id),
                    UUID(tenant_id),
                    UUID(campaign_id) if campaign_id else None,
                    s3_bucket if s3_bucket is not None else self._s3.bucket,
                    s3_key,
                    s3_region if s3_region is not None else self._s3.region,
                    file_size_bytes,
                    duration_seconds,
                    status,
                    upload_started,
                    upload_finished,
                )
                return row["id"] if row else None
        except Exception as exc:
            logger.error(f"Failed to insert recording_s3 record: {exc}")
            return None

    async def _update_call_recording_url(
        self, call_id: str, recording_id: Optional[UUID]
    ) -> None:
        """Point calls.recording_url at the internal stream API path."""
        if not recording_id:
            return
        try:
            async with self._db.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE calls
                    SET recording_url = $1, updated_at = NOW()
                    WHERE id = $2
                    """,
                    f"/api/v1/recordings/{recording_id}/stream",
                    UUID(call_id),
                )
        except Exception as exc:
            logger.warning(f"Could not update calls.recording_url for {call_id}: {exc}")

    async def _mark_upload_failed(
        self,
        call_id: str,
        tenant_id: str,
        campaign_id: str,
        reason: str,
    ) -> None:
        """Insert a 'failed' row so the failure is visible in the admin panel."""
        try:
            async with self._db.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO recordings_s3 (
                        call_id, tenant_id, campaign_id,
                        s3_bucket, s3_key, s3_region, status
                    ) VALUES ($1,$2,$3,$4,$5,$6,'failed')
                    ON CONFLICT DO NOTHING
                    """,
                    UUID(call_id),
                    UUID(tenant_id),
                    UUID(campaign_id) if campaign_id else None,
                    self._s3.bucket,
                    f"failed/{call_id}.wav",
                    self._s3.region,
                )
        except Exception:
            pass  # Best-effort — don't shadow the original error


# ---------------------------------------------------------------------------
# mix_stereo_recording — telephony two-channel recording mixer
# ---------------------------------------------------------------------------

def mix_stereo_recording(
    caller_chunks: List[bytes],
    agent_chunks: List[Tuple[int, bytes]],
    sample_rate: int = 8000,
) -> bytes:
    """
    Mix caller (left) and agent (right) PCM16 audio into a stereo WAV.

    Parameters
    ----------
    caller_chunks : list[bytes]
        Sequential 16-bit little-endian PCM audio from the caller.
        Chunks are concatenated in order to form the left channel.
    agent_chunks : list[tuple[int, bytes]]
        Agent TTS audio as (sample_offset, pcm_bytes) pairs where
        sample_offset is the absolute sample position (MixMonitor cursor)
        at which each chunk should be placed.
        Chunks may overlap the right channel only at their offset position.
    sample_rate : int
        Sample rate in Hz (must be 8000 for telephony PCMU path).

    Returns
    -------
    bytes
        Stereo WAV file (2 channels, 16-bit, sample_rate Hz).
    """
    # Build left channel (caller) as a flat bytearray of int16 samples
    caller_pcm = b"".join(caller_chunks)
    caller_samples = len(caller_pcm) // 2  # int16 = 2 bytes per sample

    # Determine the length needed for the right channel (agent)
    max_agent_end = 0
    for offset, chunk in agent_chunks:
        end = offset + (len(chunk) // 2)
        if end > max_agent_end:
            max_agent_end = end

    total_samples = max(caller_samples, max_agent_end)
    if total_samples == 0:
        # Nothing to mix — return an empty stereo WAV
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"")
        buf.seek(0)
        return buf.read()

    # Build right channel (agent) buffer — zero-initialised (silence)
    agent_buf = bytearray(total_samples * 2)
    for offset, chunk in agent_chunks:
        byte_pos = offset * 2
        end_pos = byte_pos + len(chunk)
        if end_pos > len(agent_buf):
            # Trim to fit (guards against cursor drift)
            chunk = chunk[: len(agent_buf) - byte_pos]
            end_pos = len(agent_buf)
        if byte_pos < len(agent_buf):
            agent_buf[byte_pos:end_pos] = chunk

    # Pad left channel with silence if shorter than total
    left_pad = total_samples * 2 - len(caller_pcm)
    left_buf = caller_pcm + bytes(left_pad) if left_pad > 0 else caller_pcm

    # Interleave left/right samples into stereo PCM16
    stereo = bytearray(total_samples * 4)  # 2 channels × 2 bytes
    for i in range(total_samples):
        l_off = i * 2
        s_off = i * 4
        stereo[s_off:s_off + 2] = left_buf[l_off:l_off + 2]
        stereo[s_off + 2:s_off + 4] = agent_buf[l_off:l_off + 2]

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(stereo))
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Backwards-compatible factory
# ---------------------------------------------------------------------------

def make_recording_service(db_pool: Any) -> RecordingService:
    """
    Factory used by the DI container and existing call sites.

    Previously: RecordingService(db_client)  — Supabase client
    Now:        make_recording_service(db_pool) — asyncpg pool
    """
    return RecordingService(db_pool=db_pool)
