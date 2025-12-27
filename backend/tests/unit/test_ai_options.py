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
    DEEPGRAM_MODELS,
    CARTESIA_MODELS,
    GroqModel,
    DeepgramModel,
    CartesiaModel,
)


class TestAIConfig:
    """Test AI configuration models"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = AIProviderConfig()
        
        assert config.llm_provider == "groq"
        assert config.llm_model == "llama-3.3-70b-versatile"
        assert config.stt_provider == "deepgram"
        assert config.stt_model == "nova-3"
        assert config.tts_provider == "cartesia"
        assert config.tts_model == "sonic-3"
        assert config.llm_temperature == 0.6
        assert config.llm_max_tokens == 150
    
    def test_custom_config(self):
        """Test custom configuration values"""
        config = AIProviderConfig(
            llm_model="llama-3.1-8b-instant",
            llm_temperature=0.3,
            stt_model="nova-2",
            tts_voice_id="custom-voice-id"
        )
        
        assert config.llm_model == "llama-3.1-8b-instant"
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
        """Test max_tokens must be between 1 and 1000"""
        # Valid values
        config = AIProviderConfig(llm_max_tokens=1)
        assert config.llm_max_tokens == 1
        
        config = AIProviderConfig(llm_max_tokens=1000)
        assert config.llm_max_tokens == 1000
        
        # Invalid values should raise validation error
        with pytest.raises(Exception):
            AIProviderConfig(llm_max_tokens=0)
        
        with pytest.raises(Exception):
            AIProviderConfig(llm_max_tokens=1001)


class TestModelInfo:
    """Test model information constants"""
    
    def test_groq_models_exist(self):
        """Verify Groq model list contains expected models"""
        model_ids = [m.id for m in GROQ_MODELS]
        
        assert GroqModel.LLAMA_3_3_70B.value in model_ids
        assert GroqModel.LLAMA_3_1_8B.value in model_ids
        assert GroqModel.MIXTRAL_8X7B.value in model_ids
        assert GroqModel.GEMMA2_9B.value in model_ids
        
        # Verify each model has required fields
        for model in GROQ_MODELS:
            assert model.id is not None
            assert model.name is not None
            assert model.description is not None
    
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
        assert request.model == GroqModel.LLAMA_3_3_70B.value
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
        assert request.sample_rate == 16000  # Default


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
        assert config_dict["llm_provider"] == "groq"
        assert config_dict["stt_provider"] == "deepgram"
        assert config_dict["tts_provider"] == "cartesia"
    
    def test_config_from_dict(self):
        """Test configuration can be loaded from dict"""
        config_dict = {
            "llm_provider": "groq",
            "llm_model": "llama-3.1-8b-instant",
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
        
        assert config.llm_model == "llama-3.1-8b-instant"
        assert config.llm_temperature == 0.5
        assert config.stt_model == "nova-2"
        assert config.stt_language == "es"
        assert config.tts_model == "sonic-2"
        assert config.tts_sample_rate == 22050
