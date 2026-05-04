"""Cross-cutting helpers used by more than one ai_options sub-module.

Kept narrow on purpose — anything tied to a specific feature (voice
catalog state, preview disk cache, etc.) lives in the module that owns
that feature.
"""
from __future__ import annotations

import struct
from typing import Optional

from app.domain.models.ai_config import AIProviderConfig


# --- tenant DB helpers -------------------------------------------------
# Plain SQL with no ORM; read by /config and /test endpoints, written by
# /config. Re-exported from the package __init__ because campaigns.py
# imports `_fetch_tenant_config` directly.

async def _fetch_tenant_config(conn, tenant_id: str) -> Optional[AIProviderConfig]:
    row = await conn.fetchrow(
        """
        SELECT
            llm_provider,
            llm_model,
            llm_temperature,
            llm_max_tokens,
            stt_provider,
            stt_model,
            stt_language,
            tts_provider,
            tts_model,
            tts_voice_id,
            tts_sample_rate
        FROM tenant_ai_configs
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not row:
        return None

    return AIProviderConfig(
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        llm_temperature=row["llm_temperature"],
        llm_max_tokens=row["llm_max_tokens"],
        stt_provider=row["stt_provider"],
        stt_model=row["stt_model"],
        stt_language=row["stt_language"],
        tts_provider=row["tts_provider"],
        tts_model=row["tts_model"],
        tts_voice_id=row["tts_voice_id"],
        tts_sample_rate=row["tts_sample_rate"],
    )


async def _upsert_tenant_config(conn, tenant_id: str, config: AIProviderConfig) -> None:
    await conn.execute(
        """
        INSERT INTO tenant_ai_configs (
            tenant_id,
            llm_provider,
            llm_model,
            llm_temperature,
            llm_max_tokens,
            stt_provider,
            stt_model,
            stt_language,
            tts_provider,
            tts_model,
            tts_voice_id,
            tts_sample_rate
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
        )
        ON CONFLICT (tenant_id) DO UPDATE SET
            llm_provider = EXCLUDED.llm_provider,
            llm_model = EXCLUDED.llm_model,
            llm_temperature = EXCLUDED.llm_temperature,
            llm_max_tokens = EXCLUDED.llm_max_tokens,
            stt_provider = EXCLUDED.stt_provider,
            stt_model = EXCLUDED.stt_model,
            stt_language = EXCLUDED.stt_language,
            tts_provider = EXCLUDED.tts_provider,
            tts_model = EXCLUDED.tts_model,
            tts_voice_id = EXCLUDED.tts_voice_id,
            tts_sample_rate = EXCLUDED.tts_sample_rate,
            updated_at = NOW()
        """,
        tenant_id,
        config.llm_provider,
        config.llm_model,
        config.llm_temperature,
        config.llm_max_tokens,
        config.stt_provider,
        config.stt_model,
        config.stt_language,
        config.tts_provider,
        config.tts_model,
        config.tts_voice_id,
        config.tts_sample_rate,
    )


# --- audio format helper ----------------------------------------------
# Used by both preview and testing endpoints — Deepgram and ElevenLabs
# return linear16 PCM, while the frontend preview player expects float32.

def _linear16_to_float32le_bytes(pcm16_data: bytes) -> bytes:
    """
    Convert little-endian linear16 PCM bytes to float32 little-endian bytes.
    Frontend preview playback expects float32 PCM payload.
    """
    if not pcm16_data:
        return b""
    sample_count = len(pcm16_data) // 2
    if sample_count == 0:
        return b""
    samples = struct.unpack(f"<{sample_count}h", pcm16_data[: sample_count * 2])
    return b"".join(
        struct.pack("<f", max(-1.0, min(1.0, sample / 32768.0)))
        for sample in samples
    )
