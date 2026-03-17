"""
Tests for VoicePipelineConfig

Validates:
- Default values match expected config
- Environment variable overrides work correctly
- Config is cached (singleton via @lru_cache)
"""
import os
import pytest
from unittest.mock import patch


class TestVoicePipelineConfigDefaults:
    """Verify defaults are correct."""

    def test_tts_defaults(self):
        """TTS defaults match VoiceSessionConfig and orchestrator values."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()

        assert config.tts_source_sample_rate == 24000
        assert config.tts_source_format == "pcm_s16le"

    def test_worker_defaults(self):
        """Worker defaults match expected values."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()

        assert config.max_concurrent_pipelines == 50
        assert config.worker_log_level == "INFO"
        assert config.worker_heartbeat_interval == 60

    def test_provider_defaults(self):
        """Provider defaults are correct."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()

        assert config.tts_provider == "google"
        assert config.media_gateway_type == "browser"

    def test_server_defaults(self):
        """Server defaults match current deployment."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()

        assert config.api_host == "0.0.0.0"
        assert config.api_port == 8000


class TestVoicePipelineConfigEnvOverrides:
    """Verify env var overrides work correctly."""

    @patch.dict(os.environ, {"TTS_SOURCE_SAMPLE_RATE": "16000"})
    def test_sample_rate_override(self):
        """Env var overrides TTS source sample rate."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()
        assert config.tts_source_sample_rate == 16000

    @patch.dict(os.environ, {"WORKER_LOG_LEVEL": "DEBUG"})
    def test_log_level_override(self):
        """Env var overrides worker log level."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()
        assert config.worker_log_level == "DEBUG"

    @patch.dict(os.environ, {"MAX_CONCURRENT_PIPELINES": "200"})
    def test_concurrency_override(self):
        """Env var overrides max concurrent pipelines."""
        from app.core.voice_config import VoicePipelineConfig
        config = VoicePipelineConfig()
        assert config.max_concurrent_pipelines == 200

class TestVoicePipelineConfigCaching:
    """Verify @lru_cache produces a singleton."""

    def test_get_voice_config_returns_same_instance(self):
        """get_voice_config() returns the same cached object."""
        from app.core.voice_config import get_voice_config
        get_voice_config.cache_clear()

        config1 = get_voice_config()
        config2 = get_voice_config()
        assert config1 is config2

    def test_cache_can_be_cleared(self):
        """Cache can be cleared for testing."""
        from app.core.voice_config import get_voice_config
        get_voice_config.cache_clear()

        config1 = get_voice_config()
        get_voice_config.cache_clear()
        config2 = get_voice_config()

        assert config1 is not config2
        assert config1.tts_source_sample_rate == config2.tts_source_sample_rate
