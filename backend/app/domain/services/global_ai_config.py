"""
Global AI Configuration Service — PROCESS DEFAULT ONLY.

Provides the immutable process/env default :class:`AIProviderConfig` used for
genuinely tenant-less paths (Ask AI, browser tests, campaign-less dev dials).

IMPORTANT (multi-tenant safety): this is NOT per-call, per-tenant config. Real
calls source their provider selection (LLM model/provider/temperature/max-tokens,
STT engine, TTS, pipeline mode, realtime) PER-TENANT from ``tenant_ai_configs``
via :mod:`tenant_ai_config_resolver`, keyed on the call's own tenant_id. Using
this singleton as per-call config caused cross-tenant model bleed (tenant B
saving/viewing AI Options overwrote what tenant A's live call read). Do NOT call
``set_global_config`` from any request path or the app boot restore — it is kept
only as a test/seed hook. ``get_global_config`` is a pure read of the code
default.
"""
import os
import random
from typing import Optional
from app.domain.models.ai_config import (
    AIProviderConfig,
    CARTESIA_VOICES,
    DEEPGRAM_AURA2_VOICES,
    GOOGLE_CHIRP3_VOICES,
)

# Global configuration - applies to all voice interactions
_global_config: Optional[AIProviderConfig] = None

# Random names for variety in each session
MALE_NAMES = ["Alex", "James", "Michael", "David", "Ryan", "Daniel", "Chris", "Nathan", "Jake", "Ethan", "Marcus", "Leo", "Adam", "Tom", "Ben"]
FEMALE_NAMES = ["Sarah", "Emma", "Olivia", "Sophia", "Mia", "Isabella", "Ava", "Emily", "Grace", "Lily", "Chloe", "Zoe", "Anna", "Kate", "Maya"]


def get_global_config() -> AIProviderConfig:
    """
    Get the global AI configuration.
    
    Returns the saved config or default if none saved.
    This config is used by:
    - Dummy call WebSocket
    - Voice pipeline service
    - Any other voice interaction
    """
    global _global_config
    
    if _global_config is None:
        _global_config = AIProviderConfig()
    
    return _global_config


def set_global_config(config: AIProviderConfig) -> AIProviderConfig:
    """
    Set the global AI configuration.
    
    This immediately affects all voice interactions.
    
    Args:
        config: The new configuration to apply globally
    
    Returns:
        The saved configuration
    """
    global _global_config
    _global_config = config
    return _global_config


def get_selected_voice_info() -> dict:
    """
    Get details about the currently selected voice.
    Searches Cartesia, Google Chirp 3 HD, Deepgram Aura-2, and ElevenLabs voices.

    Returns:
        Dictionary with voice name, id, gender, and other info
    """
    config = get_global_config()

    # Build a combined static list — include ElevenLabs from in-memory cache
    # if available (populated after first /voices API call or prefetch).
    try:
        from app.infrastructure.tts.elevenlabs_catalog import _elevenlabs_voices_cache
        el_voices = list(_elevenlabs_voices_cache) if _elevenlabs_voices_cache else []
    except Exception:
        el_voices = []

    all_voices = [*CARTESIA_VOICES, *GOOGLE_CHIRP3_VOICES, *DEEPGRAM_AURA2_VOICES, *el_voices]
    for voice in all_voices:
        if voice.id == config.tts_voice_id:
            return {
                "id": voice.id,
                "name": voice.name,
                "description": voice.description,
                "gender": voice.gender,
                "accent": voice.accent,
                "accent_color": voice.accent_color,
            }
    
    # If voice not found, generate random name based on voice ID hints
    # Google voices have gender in the metadata, try to infer from ID
    voice_id = config.tts_voice_id.lower()
    
    # Known female Google voice names
    female_google = ["kore", "aoede", "leda", "zephyr"]
    # Known male Google voice names  
    male_google = ["orus", "charon", "fenrir", "puck"]
    
    is_female = any(f in voice_id for f in female_google)
    is_male = any(m in voice_id for m in male_google)
    
    if is_female:
        gender = "female"
        name = random.choice(FEMALE_NAMES)
    elif is_male:
        gender = "male"
        name = random.choice(MALE_NAMES)
    else:
        # Default to random gender
        gender = random.choice(["male", "female"])
        name = random.choice(MALE_NAMES if gender == "male" else FEMALE_NAMES)
    
    return {
        "id": config.tts_voice_id,
        "name": name,
        "description": f"{name} - AI Voice Assistant",
        "gender": gender,
        "accent": "American",
        "accent_color": "#6366f1",
    }


def resolve_voice_gender(voice_id: Optional[str]) -> Optional[str]:
    """Return 'male' | 'female' for a given voice id, or None if unknown.

    Looks the id up in the combined provider catalogs (Cartesia, Google,
    Deepgram, ElevenLabs cache); falls back to the Google name heuristic.
    Read-only over the static catalogs — does NOT touch the process-global
    config, so it's safe to call per-call/per-tenant.
    """
    if not voice_id:
        return None
    try:
        from app.infrastructure.tts.elevenlabs_catalog import _elevenlabs_voices_cache
        el_voices = list(_elevenlabs_voices_cache) if _elevenlabs_voices_cache else []
    except Exception:
        el_voices = []

    for voice in [*CARTESIA_VOICES, *GOOGLE_CHIRP3_VOICES, *DEEPGRAM_AURA2_VOICES, *el_voices]:
        if voice.id == voice_id:
            g = (getattr(voice, "gender", "") or "").strip().lower()
            return g if g in ("male", "female") else None

    low = voice_id.lower()
    if any(f in low for f in ("kore", "aoede", "leda", "zephyr")):
        return "female"
    if any(m in low for m in ("orus", "charon", "fenrir", "puck")):
        return "male"
    return None


def get_random_agent_name(gender: str = None) -> str:
    """
    Get a random agent name based on gender.
    
    Args:
        gender: 'male' or 'female', or None for random
        
    Returns:
        A random name appropriate for the gender
    """
    if gender == "female":
        return random.choice(FEMALE_NAMES)
    elif gender == "male":
        return random.choice(MALE_NAMES)
    else:
        return random.choice(MALE_NAMES + FEMALE_NAMES)


def get_selected_model_info() -> dict:
    """
    Get details about the currently selected LLM model.
    
    Returns:
        Dictionary with model name, id, and other info
    """
    from app.domain.models.ai_config import GROQ_MODELS
    
    config = get_global_config()
    
    for model in GROQ_MODELS:
        if model.id == config.llm_model:
            return {
                "id": model.id,
                "name": model.name,
                "description": model.description,
                "speed": model.speed,
                "price": model.price,
                "is_preview": model.is_preview,
            }
    
    return {
        "id": config.llm_model,
        "name": "Unknown Model",
    }
