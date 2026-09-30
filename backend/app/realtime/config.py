"""Realtime-only settings. No traditional prompt or TTS dependencies."""
from typing import Literal, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class RealtimePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: Literal["assistant", "sales", "support", "receptionist"] = "assistant"
    goal: str = Field(default="Help the caller using verified company information.", max_length=1000)
    instructions: str = Field(default="", max_length=6000)
    opening_greeting: str = Field(default="", max_length=500)


class RealtimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai"] = "openai"
    prompt: RealtimePrompt = Field(default_factory=RealtimePrompt)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "none"] = "low"
    speed: float = Field(default=1.0, ge=0.25, le=1.5)
    max_output_tokens: int = Field(default=1024, ge=64, le=4096)
    turn_detection: Literal["low", "medium", "high", "auto"] = "medium"
    noise_reduction: Literal["near_field", "far_field", "none", "off"] = "near_field"


def validate_realtime(model: str, voice: str, settings: dict | None = None) -> None:
    from app.realtime.catalog import REALTIME_MODEL, REALTIME_VOICES
    # Preserve the existing opt-in xAI adapter configuration. Its protocol
    # settings remain provider-specific and it is not advertised in the GPT UI.
    provider = str((settings or {}).get("provider") or "openai").lower()
    if provider == "xai":
        return
    if provider != "openai":
        raise ValueError("Unsupported Realtime provider")
    if model != REALTIME_MODEL:
        raise ValueError("Unsupported Realtime model")
    if voice not in {v["id"] for v in REALTIME_VOICES}:
        raise ValueError("Select a supported Realtime voice")
    if settings is not None:
        RealtimeSettings.model_validate(settings)


class RealtimeProviderConfig(BaseModel):
    pipeline_mode: str = "cascaded"  # "cascaded" | "realtime"
    # Realtime-only knobs (ignored entirely when pipeline_mode == "cascaded").
    realtime_model: str = "gpt-realtime-2"
    realtime_voice: str = "marin"
    # Persisted as the existing tenant realtime_settings JSONB.
    # Validated with RealtimeSettings when saving an active Realtime config.
    realtime_settings: Optional[Dict[str, Any]] = Field(default=None)

    class Config:
        use_enum_values = True
