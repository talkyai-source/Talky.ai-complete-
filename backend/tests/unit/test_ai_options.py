"""
Unit tests for AI Options endpoints

Tests:
- Provider listing
- Configuration save/load
- Model validation
"""
import pytest
from app.domain.models.ai_config import (
    AIProviderConfig,
    ProviderListResponse,
    LLMTestRequest,
    TTSTestRequest,
    GROQ_MODELS,
    GEMINI_MODELS,
    DEEPGRAM_MODELS,
    CARTESIA_MODELS,
    GroqModel,
    GeminiModel,
    DeepgramModel,
    CartesiaModel,
    LLMProvider,
)


class TestAIConfig:
    """Test AI configuration models"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = AIProviderConfig()
        
        # MVP pair, 2026-08-24 — see docs/MODEL-SELECTION.md. Was
        # groq/qwen3.6-27b, measured at 640ms p50 first-token against a
        # production-sized prompt versus 269ms for this one, and the cause of
        # 49 of 51 production turns being tagged [SLOW] on 2026-08-23.
        assert config.llm_provider == "cerebras"
        assert config.llm_model == "gpt-oss-120b"
        assert config.stt_provider == "deepgram"
        assert config.stt_model == "nova-3"
        assert config.tts_provider == "deepgram"
        assert config.tts_model == "aura-2"
        assert config.llm_temperature == 0.6
        assert config.llm_max_tokens == 90
    
    def test_custom_config(self):
        """Test custom configuration values"""
        config = AIProviderConfig(
            llm_model="openai/gpt-oss-120b",
            llm_temperature=0.3,
            stt_model="nova-2",
            tts_voice_id="custom-voice-id"
        )

        # An explicit choice overrides the default — that is what this test is
        # for, so the value must NOT be the default one.
        assert config.llm_model == "openai/gpt-oss-120b"
        assert config.llm_temperature == 0.3
        assert config.stt_model == "nova-2"
        assert config.tts_voice_id == "custom-voice-id"
    
    def test_temperature_validation(self):
        """Test temperature must be between 0 and 2"""
        # Valid values
        config = AIProviderConfig(llm_temperature=0.0)
        assert config.llm_temperature == 0.0
        
        config = AIProviderConfig(llm_temperature=2.0)
        assert config.llm_temperature == 2.0
        
        # Invalid values should raise validation error
        with pytest.raises(Exception):
            AIProviderConfig(llm_temperature=-0.1)
        
        with pytest.raises(Exception):
            AIProviderConfig(llm_temperature=2.1)
    
    def test_max_tokens_validation(self):
        """Test max_tokens must be between 1 and 5000 (ceiling raised for
        consultative replies; per-turn length still capped by the persona +
        sentence budget)."""
        # Valid values
        config = AIProviderConfig(llm_max_tokens=1)
        assert config.llm_max_tokens == 1

        config = AIProviderConfig(llm_max_tokens=5000)
        assert config.llm_max_tokens == 5000

        # Invalid values should raise validation error
        with pytest.raises(Exception):
            AIProviderConfig(llm_max_tokens=0)

        with pytest.raises(Exception):
            AIProviderConfig(llm_max_tokens=5001)


class TestModelInfo:
    """Test model information constants"""
    
    def test_groq_models_exist(self):
        """Verify Groq model list contains expected models"""
        model_ids = [m.id for m in GROQ_MODELS]

        # The two Llama ids this used to assert were removed 2026-08-17: they
        # 404 on the account. Asserting their PRESENCE was actively harmful —
        # it would have blocked the fix. test_groq_model_menu.py now asserts
        # their absence, with the probe output that proved it.
        # Narrowed to ONE model per provider for the MVP (2026-08-24). Qwen and
        # the 120b moved to GROQ_MODELS_HIDDEN — still valid if a tenant has one
        # stored, just no longer offered. test_groq_model_menu.py asserts that
        # nobody is locked out by the narrowing.
        assert model_ids == [GroqModel.GPT_OSS_20B.value]

        # Verify each model has required fields
        for model in GROQ_MODELS:
            assert model.id is not None
            assert model.name is not None
            assert model.description is not None

    def test_gemini_models_exist(self):
        """Verify Gemini model list contains gemini-2.5-flash with metadata."""
        model_ids = [m.id for m in GEMINI_MODELS]
        assert GeminiModel.GEMINI_2_5_FLASH.value in model_ids

        for model in GEMINI_MODELS:
            assert model.id is not None
            assert model.name is not None
            assert model.description is not None
            # Each Gemini entry must declare its provider so the frontend can
            # group / badge them in the dropdown.
            assert model.provider == "gemini"

    def test_llm_provider_enum_includes_gemini(self):
        """Catch regressions where Gemini is removed from the provider enum."""
        assert LLMProvider.GEMINI.value == "gemini"
        assert LLMProvider.GROQ.value == "groq"
    
    def test_deepgram_models_exist(self):
        """Verify Deepgram model list contains expected models"""
        model_ids = [m.id for m in DEEPGRAM_MODELS]
        
        assert DeepgramModel.NOVA_3.value in model_ids
        assert DeepgramModel.NOVA_2.value in model_ids
        
        for model in DEEPGRAM_MODELS:
            assert model.id is not None
            assert model.name is not None
            assert model.description is not None
    
    def test_cartesia_models_exist(self):
        """Verify Cartesia model list contains expected models"""
        model_ids = [m.id for m in CARTESIA_MODELS]
        
        assert CartesiaModel.SONIC_3.value in model_ids
        assert CartesiaModel.SONIC_2.value in model_ids
        
        for model in CARTESIA_MODELS:
            assert model.id is not None
            assert model.name is not None
            assert model.description is not None


class TestRequestModels:
    """Test request model validation"""
    
    def test_llm_test_request_defaults(self):
        """Test LLM test request with defaults"""
        request = LLMTestRequest(message="Hello")
        
        assert request.message == "Hello"
        assert request.model == GroqModel.QWEN_3_6_27B.value
        assert request.temperature == 0.6
        assert request.max_tokens == 150
    
    def test_llm_test_request_custom(self):
        """Test LLM test request with custom values"""
        request = LLMTestRequest(
            model="llama-3.1-8b-instant",
            message="Test message",
            temperature=0.8,
            max_tokens=200
        )
        
        assert request.model == "llama-3.1-8b-instant"
        assert request.message == "Test message"
        assert request.temperature == 0.8
        assert request.max_tokens == 200
    
    def test_tts_test_request(self):
        """Test TTS test request"""
        request = TTSTestRequest(
            model="sonic-3",
            voice_id="test-voice",
            text="Hello world"
        )
        
        assert request.model == "sonic-3"
        assert request.voice_id == "test-voice"
        assert request.text == "Hello world"
        assert request.sample_rate == 24000  # Default (Cartesia recommended)


class TestConfigSerialization:
    """Test configuration serialization"""
    
    def test_config_to_dict(self):
        """Test configuration can be serialized to dict"""
        config = AIProviderConfig()
        config_dict = config.model_dump()
        
        assert "llm_provider" in config_dict
        assert "llm_model" in config_dict
        assert "stt_provider" in config_dict
        assert "tts_provider" in config_dict
        
        # Values should be strings (enum values)
        assert config_dict["llm_provider"] == "cerebras"
        assert config_dict["stt_provider"] == "deepgram"
        assert config_dict["tts_provider"] == "deepgram"
    
    def test_config_from_dict(self):
        """Test configuration can be loaded from dict"""
        config_dict = {
            "llm_provider": "groq",
            "llm_model": "openai/gpt-oss-120b",
            "llm_temperature": 0.5,
            "llm_max_tokens": 100,
            "stt_provider": "deepgram",
            "stt_model": "nova-2",
            "stt_language": "es",
            "tts_provider": "cartesia",
            "tts_model": "sonic-2",
            "tts_voice_id": "custom-id",
            "tts_sample_rate": 22050
        }
        
        config = AIProviderConfig(**config_dict)

        # An EXPLICIT value must survive, unlike the two default assertions
        # above — this test is about deserialisation, not about the default.
        assert config.llm_model == "openai/gpt-oss-120b"
        assert config.llm_temperature == 0.5
        assert config.stt_model == "nova-2"
        assert config.stt_language == "es"
        assert config.tts_model == "sonic-2"
        assert config.tts_sample_rate == 22050
