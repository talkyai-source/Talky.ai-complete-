"""
Global AI Configuration Service

Stores the user-selected LLM model and TTS voice that applies
globally across the entire voice pipeline - dummy calls, real calls,
SIP calls, and all other interactions.

This is a singleton service that maintains the current configuration.
"""
import os
import random
from typing import Optional
from app.domain.models.ai_config import AIProviderConfig, CARTESIA_VOICES, GOOGLE_CHIRP3_VOICES

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
    - SIP bridge
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
    Searches both Cartesia and Google Chirp 3 HD voices.
    
    Returns:
        Dictionary with voice name, id, gender, and other info
    """
    config = get_global_config()
    
    # Search Cartesia voices
    for voice in CARTESIA_VOICES:
        if voice.id == config.tts_voice_id:
            return {
                "id": voice.id,
                "name": voice.name,
                "description": voice.description,
                "gender": voice.gender,
                "accent": voice.accent,
                "accent_color": voice.accent_color,
            }
    
    # Search Google Chirp 3 HD voices
    for voice in GOOGLE_CHIRP3_VOICES:
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

