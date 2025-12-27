"""
AI Provider Configuration Model

Defines the configuration structure for LLM, STT, and TTS providers.
This configuration is used in both the AI Options testing page and actual calls.
"""
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from enum import Enum


class LLMProvider(str, Enum):
    """Available LLM providers"""
    GROQ = "groq"


class STTProvider(str, Enum):
    """Available STT providers"""
    DEEPGRAM = "deepgram"


class TTSProvider(str, Enum):
    """Available TTS providers"""
    CARTESIA = "cartesia"
    GOOGLE = "google"
    # ELEVENLABS = "elevenlabs"  # Future


class GroqModel(str, Enum):
    """Available Groq models - Production models only"""
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    LLAMA_3_1_8B = "llama-3.1-8b-instant"
    GPT_OSS_120B = "openai/gpt-oss-120b"
    GPT_OSS_20B = "openai/gpt-oss-20b"


class DeepgramModel(str, Enum):
    """Available Deepgram STT models"""
    NOVA_3 = "nova-3"
    NOVA_2 = "nova-2"


class CartesiaModel(str, Enum):
    """Available Cartesia TTS models"""
    SONIC_3 = "sonic-3"
    SONIC_2 = "sonic-2"


class GoogleTTSModel(str, Enum):
    """Available Google TTS models"""
    CHIRP3_HD = "Chirp3-HD"


class ModelInfo(BaseModel):
    """Model metadata"""
    id: str
    name: str
    description: str
    speed: Optional[str] = None


class VoiceInfo(BaseModel):
    """TTS Voice metadata"""
    id: str
    name: str
    language: str = "en"
    description: str = ""
    gender: Optional[str] = None
    accent: Optional[str] = None


class AIProviderConfig(BaseModel):
    """
    AI Provider Configuration
    
    Used for both testing in AI Options and actual voice calls.
    Stored per-tenant in the database.
    """
    # LLM Configuration
    llm_provider: LLMProvider = LLMProvider.GROQ
    llm_model: str = GroqModel.LLAMA_3_3_70B.value
    llm_temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=150, ge=1, le=1000)
    
    # STT Configuration
    stt_provider: STTProvider = STTProvider.DEEPGRAM
    stt_model: str = DeepgramModel.NOVA_3.value
    stt_language: str = "en"
    
    # TTS Configuration
    tts_provider: TTSProvider = TTSProvider.CARTESIA
    tts_model: str = CartesiaModel.SONIC_3.value
    tts_voice_id: str = "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"  # Default Cartesia voice
    tts_sample_rate: int = 16000
    
    class Config:
        use_enum_values = True


class ProviderListResponse(BaseModel):
    """Response for available providers listing"""
    llm: Dict
    stt: Dict
    tts: Dict


class LatencyTestResult(BaseModel):
    """Result of a latency test"""
    provider: str
    model: str
    latency_ms: float
    first_token_ms: Optional[float] = None
    total_tokens: Optional[int] = None
    success: bool
    error: Optional[str] = None


class LLMTestRequest(BaseModel):
    """Request for LLM testing"""
    model: str = GroqModel.LLAMA_3_3_70B.value
    message: str
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    max_tokens: int = Field(default=150, ge=1, le=1000)


class LLMTestResponse(BaseModel):
    """Response from LLM testing"""
    response: str
    latency_ms: float
    first_token_ms: float
    total_tokens: int
    model: str


class TTSTestRequest(BaseModel):
    """Request for TTS testing"""
    model: str = CartesiaModel.SONIC_3.value
    voice_id: str
    text: str
    sample_rate: int = 16000


class TTSTestResponse(BaseModel):
    """Response from TTS testing"""
    audio_base64: str
    latency_ms: float
    first_audio_ms: float
    duration_seconds: float
    model: str
    voice_id: str


# Predefined model information - Current Groq Production Models
GROQ_MODELS = [
    ModelInfo(
        id=GroqModel.LLAMA_3_3_70B.value,
        name="Llama 3.3 70B Versatile",
        description="Best quality/speed balance for voice AI",
        speed="280 tokens/s"
    ),
    ModelInfo(
        id=GroqModel.LLAMA_3_1_8B.value,
        name="Llama 3.1 8B Instant",
        description="Fastest model, ideal for real-time applications",
        speed="560 tokens/s"
    ),
    ModelInfo(
        id=GroqModel.GPT_OSS_120B.value,
        name="GPT-OSS 120B",
        description="OpenAI's flagship open-weight model with reasoning",
        speed="500 tokens/s"
    ),
    ModelInfo(
        id=GroqModel.GPT_OSS_20B.value,
        name="GPT-OSS 20B",
        description="Fast and efficient OpenAI open-weight model",
        speed="1000 tokens/s"
    ),
]

DEEPGRAM_MODELS = [
    ModelInfo(
        id=DeepgramModel.NOVA_3.value,
        name="Nova 3",
        description="Best accuracy, real-time optimized, multilingual",
        speed="Real-time"
    ),
    ModelInfo(
        id=DeepgramModel.NOVA_2.value,
        name="Nova 2",
        description="Fast and cost-effective for batch processing",
        speed="Real-time"
    ),
]

CARTESIA_MODELS = [
    ModelInfo(
        id=CartesiaModel.SONIC_3.value,
        name="Sonic 3",
        description="Latest model with best quality and speed",
        speed="~90ms latency"
    ),
    ModelInfo(
        id=CartesiaModel.SONIC_2.value,
        name="Sonic 2",
        description="Previous generation, still highly capable",
        speed="~100ms latency"
    ),
]

GOOGLE_TTS_MODELS = [
    ModelInfo(
        id=GoogleTTSModel.CHIRP3_HD.value,
        name="Chirp 3: HD",
        description="Latest generation with realism and emotional resonance",
        speed="~200ms latency"
    ),
]
