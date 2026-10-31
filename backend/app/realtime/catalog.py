"""Realtime model and voice catalog."""
REALTIME_MODEL = "gpt-realtime-2"

REALTIME_VOICES = [
    {"id": "marin", "name": "Marin", "description": "Warm, natural, expressive — recommended default.", "gender": "female"},
    {"id": "cedar", "name": "Cedar", "description": "Warm, grounded, natural male voice.", "gender": "male"},
    {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced, general-purpose.", "gender": "neutral"},
    {"id": "ash", "name": "Ash", "description": "Clear, measured, professional.", "gender": "male"},
    {"id": "ballad", "name": "Ballad", "description": "Soft, expressive, storytelling tone.", "gender": "male"},
    {"id": "coral", "name": "Coral", "description": "Bright, friendly, upbeat.", "gender": "female"},
    {"id": "sage", "name": "Sage", "description": "Calm, reassuring, thoughtful.", "gender": "female"},
    {"id": "verse", "name": "Verse", "description": "Lively, dynamic, conversational.", "gender": "male"},
]

# Selectable knobs surfaced to the frontend (map 1:1 to session builder options).
REALTIME_TURN_DETECTION = ["low", "medium", "high"]
REALTIME_NOISE_REDUCTION = ["near_field", "far_field", "none"]
