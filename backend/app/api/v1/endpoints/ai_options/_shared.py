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
            tts_sample_rate,
            voice_tuning,
            stt_engine,
            pipeline_mode,
            realtime_model,
            realtime_voice,
            realtime_settings
        FROM tenant_ai_configs
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if not row:
        return None

    # voice_tuning is JSONB; asyncpg returns it as dict on the modern driver
    # but as a str if the pool is configured without a json codec. Handle
    # both so this works regardless of pool config (the dead shadow file
    # had the same coercion — preserve it on the port).
    raw_tuning = row["voice_tuning"]
    if isinstance(raw_tuning, str):
        import json as _json
        try:
            tuning_dict = _json.loads(raw_tuning)
        except (ValueError, TypeError):
            tuning_dict = None
    elif isinstance(raw_tuning, dict):
        tuning_dict = raw_tuning if raw_tuning else None
    else:
        tuning_dict = None

    # realtime_settings is JSONB — same dict-or-str coercion as voice_tuning.
    raw_rt = dict(row).get("realtime_settings")
    if isinstance(raw_rt, str):
        import json as _json
        try:
            rt_settings = _json.loads(raw_rt)
        except (ValueError, TypeError):
            rt_settings = None
    elif isinstance(raw_rt, dict):
        rt_settings = raw_rt if raw_rt else None
    else:
        rt_settings = None

    return AIProviderConfig(
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        llm_temperature=row["llm_temperature"],
        llm_max_tokens=row["llm_max_tokens"],
        stt_provider=row["stt_provider"],
        stt_model=row["stt_model"],
        stt_engine=(dict(row).get("stt_engine") or "deepgram_flux"),
        stt_language=row["stt_language"],
        tts_provider=row["tts_provider"],
        tts_model=row["tts_model"],
        tts_voice_id=row["tts_voice_id"],
        tts_sample_rate=row["tts_sample_rate"],
        voice_tuning=tuning_dict,
        pipeline_mode=(dict(row).get("pipeline_mode") or "cascaded"),
        realtime_model=(dict(row).get("realtime_model") or "gpt-realtime-2"),
        realtime_voice=(dict(row).get("realtime_voice") or "marin"),
        realtime_settings=rt_settings,
    )


async def _upsert_tenant_config(conn, tenant_id: str, config: AIProviderConfig) -> None:
    # Coerce voice_tuning through the resolver's public validator so
    # malformed keys are dropped here instead of polluting the JSONB
    # blob. Empty / None round-trips to '{}' so the NOT NULL DEFAULT
    # constraint holds.
    import json as _json
    from app.domain.services.voice_tuning import get_voice_tuning_resolver

    if config.voice_tuning:
        coerced = get_voice_tuning_resolver().coerce_user_partial(
            config.voice_tuning
        )
        voice_tuning_json = _json.dumps(coerced)
    else:
        voice_tuning_json = "{}"

    realtime_settings_json = (
        _json.dumps(config.realtime_settings) if config.realtime_settings else None
    )

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
            tts_sample_rate,
            voice_tuning,
            stt_engine,
            pipeline_mode,
            realtime_model,
            realtime_voice,
            realtime_settings
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14,
            $15, $16, $17, $18::jsonb
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
            voice_tuning = EXCLUDED.voice_tuning,
            stt_engine = EXCLUDED.stt_engine,
            pipeline_mode = EXCLUDED.pipeline_mode,
            realtime_model = EXCLUDED.realtime_model,
            realtime_voice = EXCLUDED.realtime_voice,
            realtime_settings = EXCLUDED.realtime_settings,
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
        voice_tuning_json,
        config.stt_engine,
        config.pipeline_mode,
        config.realtime_model,
        config.realtime_voice,
        realtime_settings_json,
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
