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
    DEEPGRAM = "deepgram"
    ELEVENLABS = "elevenlabs"


class GroqModel(str, Enum):
    """Available Groq models - Production and Preview"""
    # Production Models
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    LLAMA_3_1_8B = "llama-3.1-8b-instant"
    GPT_OSS_120B = "openai/gpt-oss-120b"
    GPT_OSS_20B = "openai/gpt-oss-20b"
    # Preview Models
    LLAMA_4_MAVERICK = "meta-llama/llama-4-maverick-17b-128e-instruct"
    LLAMA_4_SCOUT = "meta-llama/llama-4-scout-17b-16e-instruct"
    QWEN_3_32B = "qwen/qwen3-32b"
    KIMI_K2 = "moonshotai/kimi-k2-instruct-0905"


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


class DeepgramTTSModel(str, Enum):
    """Available Deepgram TTS models"""
    AURA_2 = "aura-2"


class ModelInfo(BaseModel):
    """Model metadata"""
    id: str
    name: str
    description: str
    speed: Optional[str] = None
    price: Optional[str] = None
    context_window: Optional[int] = None
    is_preview: bool = False
    provider: Optional[str] = None


class VoiceInfo(BaseModel):
    """TTS Voice metadata with preview support"""
    id: str
    name: str
    language: str = "en"
    description: str = ""
    gender: Optional[str] = None
    accent: Optional[str] = None
    accent_color: str = "#6366f1"  # For UI avatar display
    preview_text: str = "Hello, I am your AI voice assistant. How can I help you today?"
    provider: str = "cartesia"
    tags: List[str] = []
    preview_url: Optional[str] = None


class AIProviderConfig(BaseModel):
    """
    AI Provider Configuration
    
    Used for both testing in AI Options and actual voice calls.
    Stored per-tenant in the database.
    """
    # LLM Configuration
    llm_provider: LLMProvider = LLMProvider.GROQ
    llm_model: str = GroqModel.LLAMA_3_1_8B.value  # llama-3.1-8b-instant — Groq's recommended voice model (560 t/s, ~90ms TTFT)
    llm_temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=90, ge=1, le=1000)  # 90 tokens ≈ 2 sentences; voice guideline for low latency
    
    # STT Configuration
    stt_provider: STTProvider = STTProvider.DEEPGRAM
    stt_model: str = DeepgramModel.NOVA_3.value
    stt_language: str = "en"
    
    # TTS Configuration - Using Deepgram Aura-2 (fast and high quality)
    tts_provider: TTSProvider = TTSProvider.DEEPGRAM
    tts_model: str = DeepgramTTSModel.AURA_2.value  # Deepgram Aura-2
    tts_voice_id: str = "aura-zeus-en"  # Zeus - professional male voice
    tts_sample_rate: int = 24000  # Deepgram Aura-2 sample rate
    
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
    sample_rate: int = 24000  # Official Cartesia recommended


class TTSTestResponse(BaseModel):
    """Response from TTS testing"""
    audio_base64: str
    latency_ms: float
    first_audio_ms: float
    duration_seconds: float
    model: str
    voice_id: str


# =============================================================================
# GROQ MODELS - Production + Preview
# =============================================================================

GROQ_MODELS = [
    # Production Models (recommended for production use)
    ModelInfo(
        id=GroqModel.LLAMA_3_3_70B.value,
        name="Llama 3.3 70B Versatile",
        description="Best quality/speed balance for voice AI. Recommended for production.",
        speed="280 tokens/s",
        price="$0.59 input / $0.79 output per 1M tokens",
        context_window=131072,
        is_preview=False,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.LLAMA_3_1_8B.value,
        name="Llama 3.1 8B Instant",
        description="Fastest model, ideal for real-time voice applications.",
        speed="560 tokens/s",
        price="$0.05 input / $0.08 output per 1M tokens",
        context_window=131072,
        is_preview=False,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.GPT_OSS_120B.value,
        name="OpenAI GPT-OSS 120B",
        description="OpenAI's flagship open-weight model with reasoning capabilities.",
        speed="500 tokens/s",
        price="$0.15 input / $0.60 output per 1M tokens",
        context_window=131072,
        is_preview=False,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.GPT_OSS_20B.value,
        name="OpenAI GPT-OSS 20B",
        description="Fast and efficient OpenAI open-weight model.",
        speed="1000 tokens/s",
        price="$0.075 input / $0.30 output per 1M tokens",
        context_window=131072,
        is_preview=False,
        provider="groq",
    ),
    # Preview Models (for evaluation, may change)
    ModelInfo(
        id=GroqModel.LLAMA_4_MAVERICK.value,
        name="Llama 4 Maverick 17B",
        description="Latest Llama 4 with 128 experts for complex reasoning.",
        speed="600 tokens/s",
        price="$0.20 input / $0.60 output per 1M tokens",
        context_window=131072,
        is_preview=True,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.LLAMA_4_SCOUT.value,
        name="Llama 4 Scout 17B",
        description="Fast Llama 4 variant with 16 experts.",
        speed="750 tokens/s",
        price="$0.11 input / $0.34 output per 1M tokens",
        context_window=131072,
        is_preview=True,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.QWEN_3_32B.value,
        name="Qwen 3 32B",
        description="Alibaba's powerful multilingual model.",
        speed="400 tokens/s",
        price="$0.29 input / $0.59 output per 1M tokens",
        context_window=131072,
        is_preview=True,
        provider="groq",
    ),
    ModelInfo(
        id=GroqModel.KIMI_K2.value,
        name="Kimi K2",
        description="Moonshot AI's large context model with 262K context.",
        speed="200 tokens/s",
        price="$1.00 input / $3.00 output per 1M tokens",
        context_window=262144,
        is_preview=True,
        provider="groq",
    ),
]

DEEPGRAM_MODELS = [
    ModelInfo(
        id=DeepgramModel.NOVA_3.value,
        name="Nova 3",
        description="Best accuracy, real-time optimized, multilingual",
        speed="Real-time",
        provider="deepgram",
    ),
    ModelInfo(
        id=DeepgramModel.NOVA_2.value,
        name="Nova 2",
        description="Fast and cost-effective for batch processing",
        speed="Real-time",
        provider="deepgram",
    ),
]

CARTESIA_MODELS = [
    ModelInfo(
        id=CartesiaModel.SONIC_3.value,
        name="Sonic 3",
        description="Latest model with best quality and speed",
        speed="~90ms latency",
        provider="cartesia",
    ),
    ModelInfo(
        id=CartesiaModel.SONIC_2.value,
        name="Sonic 2",
        description="Previous generation, still highly capable",
        speed="~100ms latency",
        provider="cartesia",
    ),
]

GOOGLE_TTS_MODELS = [
    ModelInfo(
        id=GoogleTTSModel.CHIRP3_HD.value,
        name="Chirp 3: HD",
        description="Latest generation with realism and emotional resonance",
        speed="~200ms latency",
        provider="google",
    ),
]

DEEPGRAM_TTS_MODELS = [
    ModelInfo(
        id=DeepgramTTSModel.AURA_2.value,
        name="Aura-2",
        description="Deepgram's latest low-latency neural text-to-speech model family.",
        speed="Streaming optimized",
        provider="deepgram",
    ),
]

ELEVENLABS_TTS_MODELS = [
    ModelInfo(
        id="eleven_flash_v2_5",
        name="Flash v2.5",
        description="ElevenLabs real-time model tuned for the lowest latency voice interactions.",
        speed="~75ms latency",
        provider="elevenlabs",
    ),
    ModelInfo(
        id="eleven_multilingual_v2",
        name="Multilingual v2",
        description="Highest-quality ElevenLabs model with strong multilingual support and richer expression.",
        speed="High quality",
        provider="elevenlabs",
    ),
    ModelInfo(
        id="eleven_turbo_v2_5",
        name="Turbo v2.5",
        description="Fast ElevenLabs model with a quality/latency balance suited to production voice agents.",
        speed="Low latency",
        provider="elevenlabs",
    ),
]


# =============================================================================
# CARTESIA VOICES - Curated for Voice Agents
# =============================================================================

# Official Cartesia voice IDs for voice agents
# Reference: https://docs.cartesia.ai/build-with-cartesia/voices

CARTESIA_VOICES = [
    # Professional Female Voices
    VoiceInfo(
        id="f786b574-daa5-4673-aa0c-cbe3e8534c02",
        name="Katie",
        description="Professional, warm female voice. Ideal for business calls.",
        gender="female",
        accent="American",
        accent_color="#ec4899",  # Pink
        tags=["professional", "warm", "business"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        name="Aurora",
        description="Energetic, friendly female voice. Great for customer service.",
        gender="female",
        accent="American",
        accent_color="#f97316",  # Orange
        tags=["energetic", "friendly", "customer-service"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="a0e99841-438c-4a64-b679-ae501e7d6091",
        name="Sarah",
        description="Calm, reassuring female voice. Perfect for support calls.",
        gender="female",
        accent="American",
        accent_color="#8b5cf6",  # Purple
        tags=["calm", "reassuring", "support"],
        provider="cartesia"
    ),
    # Lily (d46abd1d) removed — returns no audio with current API key (deprecated)
    # Professional Male Voices
    VoiceInfo(
        id="228fca29-3a0a-435c-8728-5cb483251068",
        name="Kiefer",
        description="Confident, authoritative male voice. Great for sales calls.",
        gender="male",
        accent="American",
        accent_color="#3b82f6",  # Blue
        tags=["confident", "authoritative", "sales"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="41534e16-2966-4c6b-9670-111411def906",
        name="Ryan",
        description="Youthful, cool & confident male voice. 20-30 sound.",
        gender="male",
        accent="American",
        accent_color="#10b981",  # Emerald
        tags=["youthful", "cool", "confident"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="b7d50908-b17c-442d-ad8d-810c63997ed9",
        name="James",
        description="British professional male voice. Smooth and articulate.",
        gender="male",
        accent="British",
        accent_color="#6366f1",  # Indigo
        tags=["british", "professional", "articulate"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="c45bc5ec-dc68-4feb-8829-6e6b2748095d",
        name="Adam",
        description="Deep, American male voice. Brooding and tough.",
        gender="male",
        accent="American",
        accent_color="#ef4444",  # Red
        tags=["deep", "tough", "american"],
        provider="cartesia"
    ),
    # Storytelling / Warm Voices
    # Veda Sky (5345cf08) removed — returns no audio with current API key (deprecated)
    VoiceInfo(
        id="bd9120b6-7761-47a6-a446-77ca49132781",
        name="Susie",
        description="Neutral young narrator. Soothing middle-aged female.",
        gender="female",
        accent="American",
        accent_color="#14b8a6",  # Teal
        tags=["narrator", "neutral", "soothing"],
        provider="cartesia"
    ),
]


# =============================================================================
# GOOGLE CHIRP 3 HD VOICES - Ultra-Realistic Streaming TTS
# =============================================================================

# Google Cloud Chirp 3: HD voices optimized for gRPC streaming
# Reference: https://cloud.google.com/text-to-speech/docs/voices

GOOGLE_CHIRP3_VOICES = [
    # Male Voices
    VoiceInfo(
        id="en-US-Chirp3-HD-Orus",
        name="Orus",
        description="Deep, authoritative male voice. Commanding presence for professional calls.",
        gender="male",
        accent="American",
        accent_color="#1e40af",  # Deep Blue
        tags=["authoritative", "deep", "professional"],
        provider="google",
        preview_text="Hello, I'm calling from your voice assistant. How may I help you today?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Charon",
        name="Charon",
        description="Mature, reassuring male voice. Trustworthy and reliable tone.",
        gender="male",
        accent="American",
        accent_color="#1d4ed8",  # Blue  
        tags=["mature", "reassuring", "trustworthy"],
        provider="google",
        preview_text="Good day, this is your AI assistant. I'm here to assist you."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Fenrir",
        name="Fenrir",
        description="Energetic, confident male voice. Great for sales and outreach.",
        gender="male",
        accent="American",
        accent_color="#2563eb",  # Bright Blue
        tags=["energetic", "confident", "sales"],
        provider="google",
        preview_text="Hi there! I'm reaching out to help you with an exciting opportunity."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Puck",
        name="Puck",
        description="Friendly, approachable male voice. Perfect for customer service.",
        gender="male",
        accent="American",
        accent_color="#3b82f6",  # Sky Blue
        tags=["friendly", "approachable", "service"],
        provider="google",
        preview_text="Hello! Thanks for calling. Let me help you with that right away."
    ),
    # Female Voices
    VoiceInfo(
        id="en-US-Chirp3-HD-Kore",
        name="Kore",
        description="Warm, professional female voice. Ideal for business communications.",
        gender="female",
        accent="American",
        accent_color="#be185d",  # Rose
        tags=["warm", "professional", "business"],
        provider="google",
        preview_text="Hello, I'm your AI assistant. How can I help you today?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Aoede",
        name="Aoede",
        description="Clear, articulate female voice. Excellent for appointments and reminders.",
        gender="female",
        accent="American",
        accent_color="#db2777",  # Pink
        tags=["clear", "articulate", "appointments"],
        provider="google",
        preview_text="Hi, I'm calling to confirm your appointment. Do you have a moment?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Leda",
        name="Leda",
        description="Soothing, empathetic female voice. Perfect for support and healthcare.",
        gender="female",
        accent="American",
        accent_color="#ec4899",  # Bright Pink
        tags=["soothing", "empathetic", "support"],
        provider="google",
        preview_text="Hi, I'm here to help. Please tell me what you need assistance with."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Zephyr",
        name="Zephyr",
        description="Youthful, vibrant female voice. Great for engagement and outreach.",
        gender="female",
        accent="American",
        accent_color="#f472b6",  # Light Pink
        tags=["youthful", "vibrant", "engagement"],
        provider="google",
        preview_text="Hey! I wanted to reach out and share some exciting news with you!"
    ),
]

# =============================================================================
# DEEPGRAM AURA-2 VOICES - Official Voice IDs
# =============================================================================
# Source: https://developers.deepgram.com/docs/tts-models
# Includes all currently documented Aura-2 voices across supported languages.
_DEEPGRAM_AURA2_VOICE_SPECS = [
    # English (all available)
    ("aura-2-amalthea-en", "Amalthea", "en", "female"),
    ("aura-2-andromeda-en", "Andromeda", "en", "female"),
    ("aura-2-apollo-en", "Apollo", "en", "male"),
    ("aura-2-arcas-en", "Arcas", "en", "male"),
    ("aura-2-aries-en", "Aries", "en", "male"),
    ("aura-2-asteria-en", "Asteria", "en", "female"),
    ("aura-2-athena-en", "Athena", "en", "female"),
    ("aura-2-atlas-en", "Atlas", "en", "male"),
    ("aura-2-aurora-en", "Aurora", "en", "female"),
    ("aura-2-callista-en", "Callista", "en", "female"),
    ("aura-2-cora-en", "Cora", "en", "female"),
    ("aura-2-cordelia-en", "Cordelia", "en", "female"),
    ("aura-2-delia-en", "Delia", "en", "female"),
    ("aura-2-draco-en", "Draco", "en", "male"),
    ("aura-2-electra-en", "Electra", "en", "female"),
    ("aura-2-harmonia-en", "Harmonia", "en", "female"),
    ("aura-2-helena-en", "Helena", "en", "female"),
    ("aura-2-hera-en", "Hera", "en", "female"),
    ("aura-2-hermes-en", "Hermes", "en", "male"),
    ("aura-2-hyperion-en", "Hyperion", "en", "male"),
    ("aura-2-iris-en", "Iris", "en", "female"),
    ("aura-2-janus-en", "Janus", "en", "female"),
    ("aura-2-juno-en", "Juno", "en", "female"),
    ("aura-2-jupiter-en", "Jupiter", "en", "male"),
    ("aura-2-luna-en", "Luna", "en", "female"),
    ("aura-2-mars-en", "Mars", "en", "male"),
    ("aura-2-minerva-en", "Minerva", "en", "female"),
    ("aura-2-neptune-en", "Neptune", "en", "male"),
    ("aura-2-odysseus-en", "Odysseus", "en", "male"),
    ("aura-2-ophelia-en", "Ophelia", "en", "female"),
    ("aura-2-orion-en", "Orion", "en", "male"),
    ("aura-2-orpheus-en", "Orpheus", "en", "male"),
    ("aura-2-pandora-en", "Pandora", "en", "female"),
    ("aura-2-phoebe-en", "Phoebe", "en", "female"),
    ("aura-2-pluto-en", "Pluto", "en", "male"),
    ("aura-2-saturn-en", "Saturn", "en", "male"),
    ("aura-2-selene-en", "Selene", "en", "female"),
    ("aura-2-thalia-en", "Thalia", "en", "female"),
    ("aura-2-theia-en", "Theia", "en", "female"),
    ("aura-2-vesta-en", "Vesta", "en", "female"),
    ("aura-2-zeus-en", "Zeus", "en", "male"),
    # Spanish (all available)
    ("aura-2-sirio-es", "Sirio", "es", "male"),
    ("aura-2-nestor-es", "Nestor", "es", "male"),
    ("aura-2-carina-es", "Carina", "es", "female"),
    ("aura-2-celeste-es", "Celeste", "es", "female"),
    ("aura-2-alvaro-es", "Alvaro", "es", "male"),
    ("aura-2-diana-es", "Diana", "es", "female"),
    ("aura-2-aquila-es", "Aquila", "es", "male"),
    ("aura-2-selena-es", "Selena", "es", "female"),
    ("aura-2-estrella-es", "Estrella", "es", "female"),
    ("aura-2-javier-es", "Javier", "es", "male"),
    ("aura-2-agustina-es", "Agustina", "es", "female"),
    ("aura-2-antonia-es", "Antonia", "es", "female"),
    ("aura-2-gloria-es", "Gloria", "es", "female"),
    ("aura-2-luciano-es", "Luciano", "es", "male"),
    ("aura-2-olivia-es", "Olivia", "es", "female"),
    ("aura-2-silvia-es", "Silvia", "es", "female"),
    ("aura-2-valerio-es", "Valerio", "es", "male"),
    # Dutch (all available)
    ("aura-2-beatrix-nl", "Beatrix", "nl", "female"),
    ("aura-2-daphne-nl", "Daphne", "nl", "female"),
    ("aura-2-cornelia-nl", "Cornelia", "nl", "female"),
    ("aura-2-sander-nl", "Sander", "nl", "male"),
    ("aura-2-hestia-nl", "Hestia", "nl", "female"),
    ("aura-2-lars-nl", "Lars", "nl", "male"),
    ("aura-2-roman-nl", "Roman", "nl", "male"),
    ("aura-2-rhea-nl", "Rhea", "nl", "female"),
    ("aura-2-leda-nl", "Leda", "nl", "female"),
    # French (all available)
    ("aura-2-agathe-fr", "Agathe", "fr", "female"),
    ("aura-2-hector-fr", "Hector", "fr", "male"),
    # German (all available)
    ("aura-2-elara-de", "Elara", "de", "female"),
    ("aura-2-aurelia-de", "Aurelia", "de", "female"),
    ("aura-2-lara-de", "Lara", "de", "female"),
    ("aura-2-julius-de", "Julius", "de", "male"),
    ("aura-2-fabian-de", "Fabian", "de", "male"),
    ("aura-2-kara-de", "Kara", "de", "female"),
    ("aura-2-viktoria-de", "Viktoria", "de", "female"),
    # Italian (all available)
    ("aura-2-melia-it", "Melia", "it", "female"),
    ("aura-2-elio-it", "Elio", "it", "male"),
    ("aura-2-flavio-it", "Flavio", "it", "male"),
    ("aura-2-maia-it", "Maia", "it", "female"),
    ("aura-2-cinzia-it", "Cinzia", "it", "female"),
    ("aura-2-cesare-it", "Cesare", "it", "male"),
    ("aura-2-livia-it", "Livia", "it", "female"),
    ("aura-2-perseo-it", "Perseo", "it", "male"),
    ("aura-2-dionisio-it", "Dionisio", "it", "male"),
    ("aura-2-demetra-it", "Demetra", "it", "female"),
    # Japanese (all available)
    ("aura-2-uzume-ja", "Uzume", "ja", "female"),
    ("aura-2-ebisu-ja", "Ebisu", "ja", "male"),
    ("aura-2-fujin-ja", "Fujin", "ja", "male"),
    ("aura-2-izanami-ja", "Izanami", "ja", "female"),
    ("aura-2-ama-ja", "Ama", "ja", "female"),
]

_DEEPGRAM_LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish",
    "nl": "Dutch",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
}


def _deepgram_accent_color(gender: Optional[str]) -> str:
    if gender == "female":
        return "#db2777"
    if gender == "male":
        return "#1d4ed8"
    return "#64748b"


DEEPGRAM_AURA2_VOICES = [
    VoiceInfo(
        id=voice_id,
        name=name,
        language=language_code,
        description=f"Deepgram Aura-2 {(_DEEPGRAM_LANGUAGE_LABELS.get(language_code, language_code)).title()} voice.",
        gender=gender,
        accent="Global",
        accent_color=_deepgram_accent_color(gender),
        tags=["aura-2", language_code],
        provider="deepgram",
    )
    for voice_id, name, language_code, gender in _DEEPGRAM_AURA2_VOICE_SPECS
]
