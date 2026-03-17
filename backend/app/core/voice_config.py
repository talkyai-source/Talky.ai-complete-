"""
Voice Pipeline Configuration

Centralized config for all voice pipeline settings.
Loads from environment variables and .env file.

Based on:
- FastAPI Settings: https://fastapi.tiangolo.com/advanced/settings/
- Pydantic Settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- 12-Factor App: https://12factor.net/config

Usage:
    from app.core.voice_config import get_voice_config
    config = get_voice_config()
    print(config.media_gateway_type)  # "browser"
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class VoicePipelineConfig(BaseSettings):
    """
    Voice pipeline settings loaded from environment variables.

    All fields can be overridden via env vars (case-insensitive).

    This config covers the Python-side voice pipeline: media gateways,
    TTS settings, and worker configuration. PBX-specific settings
    (Asterisk ARI, FreeSWITCH ESL) are read by their respective
    adapters from env vars or providers.yaml.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── TTS source audio ───────────────────────────────────────
    tts_source_sample_rate: int = 24000
    tts_source_format: str = "pcm_s16le"

    # ── API server ──────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── Workers ─────────────────────────────────────────────────
    max_concurrent_pipelines: int = 50
    worker_log_level: str = "INFO"
    worker_heartbeat_interval: int = 60  # seconds

    # ── Provider selection ──────────────────────────────────────
    tts_provider: str = "google"
    media_gateway_type: str = "browser"


@lru_cache
def get_voice_config() -> VoicePipelineConfig:
    """
    Get cached VoicePipelineConfig singleton.

    Config is read once at startup and cached — zero runtime overhead.
    """
    return VoicePipelineConfig()
