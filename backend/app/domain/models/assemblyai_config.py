"""Supported Universal-3.6 Pro settings; model and language are not user overrides.

The fields mirror AssemblyAI's streaming WebSocket API, reviewed 2026-10-10.
Nullable tuning values deliberately preserve the provider's mode-specific defaults.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ASSEMBLYAI_MODEL = "universal-3-6-pro"
AssemblyAIKeyterm = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]
ASSEMBLYAI_PII_POLICIES = frozenset(
    {
        "account_number",
        "banking_information",
        "blood_type",
        "credit_card_cvv",
        "credit_card_expiration",
        "credit_card_number",
        "date",
        "date_interval",
        "date_of_birth",
        "drivers_license",
        "drug",
        "duration",
        "email_address",
        "event",
        "filename",
        "gender_sexuality",
        "healthcare_number",
        "injury",
        "ip_address",
        "language",
        "location",
        "location_address",
        "location_address_street",
        "location_city",
        "location_coordinate",
        "location_country",
        "location_state",
        "location_zip",
        "marital_status",
        "medical_condition",
        "medical_process",
        "money_amount",
        "nationality",
        "number_sequence",
        "occupation",
        "organization",
        "passport_number",
        "password",
        "person_age",
        "person_name",
        "phone_number",
        "physical_attribute",
        "political_affiliation",
        "religion",
        "statistics",
        "time",
        "url",
        "us_social_security_number",
        "username",
        "vehicle_id",
        "zodiac_sign",
    }
)


class AssemblyAISettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    region: Literal["global", "us", "eu"] = "global"
    mode: Literal["balanced", "min_latency", "max_accuracy"] = "balanced"
    min_turn_silence: int | None = Field(default=None, ge=50, le=10000)
    max_turn_silence: int | None = Field(default=None, gt=0)
    interruption_delay: int | None = Field(default=None, ge=0, le=1000)
    vad_threshold: float | None = Field(default=None, ge=0, le=1)
    include_partial_turns: bool | None = None
    prompt: str = Field(default="", max_length=1750)
    keyterms_prompt: list[AssemblyAIKeyterm] = Field(default_factory=list, max_length=100)
    auto_agent_context: bool = True
    previous_context_n_turns: int | None = Field(default=None, ge=0, le=100)
    language_detection: bool = False
    voice_focus: Literal["near-field", "far-field"] | None = None
    voice_focus_threshold: float | None = Field(default=None, ge=0, le=1)
    domain: Literal["medical-v1"] | None = None
    speaker_labels: bool = False
    max_speakers: int | None = Field(default=None, ge=1, le=10)
    speaker_labels_revision_interval_ms: int | None = Field(default=None, ge=0)
    redact_pii: bool = False
    redact_pii_policies: list[str] = Field(
        default_factory=list, max_length=len(ASSEMBLYAI_PII_POLICIES)
    )
    redact_pii_sub: Literal["hash", "entity_name"] = "hash"
    filter_profanity: bool = False
    session_heartbeat: bool = True
    inactivity_timeout: int | None = Field(default=None, ge=5, le=3600)

    @field_validator("redact_pii_policies")
    @classmethod
    def validate_pii_policies(cls, value: list[str]) -> list[str]:
        if any(policy not in ASSEMBLYAI_PII_POLICIES for policy in value):
            raise ValueError("Unsupported AssemblyAI PII policy")
        return list(dict.fromkeys(value))

    @field_validator("speaker_labels_revision_interval_ms")
    @classmethod
    def validate_revision_interval(cls, value: int | None) -> int | None:
        if value is not None and 0 < value < 120000:
            raise ValueError("Speaker revision interval must be zero or at least 120000 ms")
        return value

    @model_validator(mode="after")
    def validate_silence_order(self) -> Self:
        if (
            self.min_turn_silence is not None
            and self.max_turn_silence is not None
            and self.max_turn_silence < self.min_turn_silence
        ):
            raise ValueError("Maximum turn silence must be at least minimum turn silence")
        return self

    def connection_parameters(self) -> dict:
        """Provider fields only; retain disabled child settings in saved UI state.

        Omitting partials is important: PII redaction defaults them off at the
        provider. Sending an unconditional true here would expose unredacted PII.
        """
        params = self.model_dump(exclude_none=True)
        for key in ("region", "auto_agent_context"):
            params.pop(key)
        if not self.prompt.strip():
            params.pop("prompt")
        if not self.keyterms_prompt:
            params.pop("keyterms_prompt")
        if not self.voice_focus:
            params.pop("voice_focus_threshold", None)
        if not self.speaker_labels:
            params.pop("max_speakers", None)
            params.pop("speaker_labels_revision_interval_ms", None)
        if not self.redact_pii:
            params.pop("redact_pii_policies", None)
            params.pop("redact_pii_sub", None)
        elif not self.redact_pii_policies:
            params.pop("redact_pii_policies")
        return params
