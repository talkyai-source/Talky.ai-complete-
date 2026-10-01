"""Small operator-owned destinations/content for the existing campaign brief."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class VoiceEmailAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)


class VoiceFormAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    recipient: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    subject: str = Field(min_length=1, max_length=200)
    fields: list[Literal["email", "phone", "follow_up", "project_type", "bidding_active"]] = Field(min_length=1, max_length=5)


def normalize_action_config(brief):
    out = {}
    for name, schema in (("email_action", VoiceEmailAction), ("form_action", VoiceFormAction)):
        if brief.get(name) is not None:
            out[name] = schema.model_validate(brief[name]).model_dump()
    return out
