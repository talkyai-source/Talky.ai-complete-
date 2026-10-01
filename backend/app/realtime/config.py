"""Realtime-only settings. No traditional prompt or TTS dependencies."""
from typing import Literal, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RealtimePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    persona: Literal["assistant", "sales", "support", "receptionist"] = "assistant"
    goal: str = Field(default="Help the caller using verified company information.", max_length=1000)
    instructions: str = Field(default="", max_length=6000)
    opening_greeting: str = Field(default="", max_length=500)


class RealtimeTurnDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["semantic_vad", "server_vad"] = "semantic_vad"
    eagerness: Literal["low", "medium", "high", "auto"] = "medium"
    threshold: float = Field(default=0.5, ge=0, le=1)
    prefix_padding_ms: int = Field(default=300, ge=0, le=5000)
    silence_duration_ms: int = Field(default=700, ge=100, le=10000)

    def wire(self):
        if self.type == "semantic_vad":
            return {"type": self.type, "eagerness": self.eagerness}
        return {"type": self.type, "threshold": self.threshold,
                "prefix_padding_ms": self.prefix_padding_ms,
                "silence_duration_ms": self.silence_duration_ms}


class RealtimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai"] = "openai"
    prompt: RealtimePrompt = Field(default_factory=RealtimePrompt)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "none"] = "low"
    speed: float = Field(default=1.0, ge=0.25, le=1.5)
    max_output_tokens: int = Field(default=1024, ge=64, le=4096)
    turn_detection: Literal["low", "medium", "high", "auto"] | RealtimeTurnDetection = "medium"
    noise_reduction: Literal["near_field", "far_field", "none", "off"] = "near_field"
    transcription_model: Literal["gpt-realtime-whisper", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"] = "gpt-realtime-whisper"

    @model_validator(mode="before")
    @classmethod
    def migrate_saved_settings(cls, value):
        data = dict(value or {})
        # GA removed temperature; retaining a legacy slider value must not
        # prevent an existing campaign from starting or send an invalid field.
        data.pop("temperature", None)
        if data.get("max_output_tokens") == "inf":
            data["max_output_tokens"] = 1024
        if data.get("noise_reduction") == "off":
            data["noise_reduction"] = "none"
        if isinstance(data.get("noise_reduction"), dict):
            data["noise_reduction"] = data["noise_reduction"].get("type", "none")
        if "reasoning_effort" in data and (not data["reasoning_effort"] or str(data["reasoning_effort"]).lower() == "none"):
            data["reasoning_effort"] = "none"
        return data


def normalize_realtime_settings(settings: dict | None = None) -> dict:
    """One effective OpenAI configuration for save, preview and runtime."""
    data = dict(settings or {})
    if str(data.get("provider") or "openai").lower() == "xai":
        # xAI has its own serializer/capability contract; do not inject OpenAI
        # transcription, semantic VAD or reasoning defaults into its payload.
        data["provider"] = "xai"
        allowed = {"provider", "model", "agent_id", "prompt", "turn_detection", "speed", "reasoning_effort"}
        data.pop("temperature", None)  # Obsolete shared control; never send it.
        if set(data) - allowed:
            raise ValueError("Unsupported xAI Realtime setting")
        td = data.get("turn_detection", {"type": "server_vad", "threshold": .85})
        if not isinstance(td, dict) or td.get("type") != "server_vad":
            raise ValueError("xAI requires server_vad settings")
        if set(td) - {"type", "threshold", "prefix_padding_ms", "silence_duration_ms"}:
            raise ValueError("Unsupported xAI VAD setting")
        if not .1 <= float(td.get("threshold", .85)) <= .9:
            raise ValueError("xAI VAD threshold must be between 0.1 and 0.9")
        for key in ("prefix_padding_ms", "silence_duration_ms"):
            if key in td and not 0 <= int(td[key]) <= 10000:
                raise ValueError("xAI VAD duration is outside the supported bounds")
        if data.get("reasoning_effort", "high") not in {"high", "none"}:
            raise ValueError("xAI reasoning effort must be high or none")
        if not .7 <= float(data.get("speed", 1)) <= 1.5:
            raise ValueError("xAI speech speed must be between 0.7 and 1.5")
        if "prompt" in data:
            data["prompt"] = RealtimePrompt.model_validate(data["prompt"]).model_dump()
        return data
    value = RealtimeSettings.model_validate(data)
    result = value.model_dump()
    if isinstance(value.turn_detection, RealtimeTurnDetection):
        result["turn_detection"] = value.turn_detection.wire()
    return result


def validate_realtime(model: str, voice: str, settings: dict | None = None) -> dict:
    from app.realtime.catalog import REALTIME_MODEL, REALTIME_VOICES
    # Preserve the existing opt-in xAI adapter configuration. Its protocol
    # settings remain provider-specific and it is not advertised in the GPT UI.
    provider = str((settings or {}).get("provider") or "openai").lower()
    if provider == "xai":
        return normalize_realtime_settings(settings)
    if provider != "openai":
        raise ValueError("Unsupported Realtime provider")
    if model != REALTIME_MODEL:
        raise ValueError("Unsupported Realtime model")
    if voice not in {v["id"] for v in REALTIME_VOICES}:
        raise ValueError("Select a supported Realtime voice")
    return normalize_realtime_settings(settings)


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
