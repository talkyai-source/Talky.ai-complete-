"""Capability-gated strict structured outputs (constrained decoding).

WHAT THIS IS
------------
Groq's ``strict: true`` json_schema mode constrains the model AT THE TOKEN
LEVEL, so the response cannot fail to match the schema — no missing keys, no
wrong types, no invalid JSON, ever. That is categorically stronger than
``{"type": "json_object"}``, which only guarantees *syntactically* valid JSON
and says nothing about the shape inside it.

WHERE IT BELONGS — AND WHERE IT MUST NOT GO
-------------------------------------------
Strict mode forces the ENTIRE response to match the schema. That makes it
exactly right for a discrete call whose whole output is one object (summarise
this transcript; classify this utterance) and exactly WRONG for a conversational
turn, where the model must be free to produce speech and only occasionally an
action envelope. Forcing a voice turn into JSON would make the agent emit JSON
instead of talking.

So this is deliberately NOT applied to the per-turn voice path. The malformed
``{"action":"endsession"...}`` envelope that reached a caller on 2026-07-08 is
fixed in the parser and the streaming guard instead, which works on every model.

WHY IT IS CAPABILITY-GATED RATHER THAN ON EVERYWHERE
----------------------------------------------------
Groq supports strict mode on ``openai/gpt-oss-20b`` and ``openai/gpt-oss-120b``
ONLY. Sending a strict json_schema request to llama or qwen is an API error, not
a graceful degradation — so the capability has to be checked before use, and the
caller needs a working fallback. Hence: ask for the request kwargs, get either
the strict form or today's json_object form, and never branch at the call site.

The gpt-oss family is a poor fit for CONVERSATIONAL voice (documented in
``groq.py``'s own ``_is_gpt_oss_model`` handling, and it is excluded from the
KB tool path for that reason). But an offline, non-latency-critical extraction
job is precisely where that weakness does not apply and its strict-mode support
does — which is why the summariser is the first consumer.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Models on which Groq accepts ``strict: true`` json_schema requests.
#: Anything else must use the json_object fallback — a strict request to an
#: unsupported model is a hard API error.
STRICT_SCHEMA_MODELS: frozenset[str] = frozenset(
    {
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        # Bare ids, since some call sites configure the model without the
        # provider prefix.
        "gpt-oss-20b",
        "gpt-oss-120b",
    }
)


def supports_strict_schema(model: Optional[str]) -> bool:
    """True when `model` accepts strict json_schema (constrained decoding)."""
    if not model:
        return False
    return str(model).strip().lower() in STRICT_SCHEMA_MODELS


def strict_object_schema(
    properties: dict[str, Any], *, name: str
) -> dict[str, Any]:
    """Build a schema that satisfies Groq's strict-mode requirements.

    Strict mode demands that EVERY property is listed in ``required`` and that
    ``additionalProperties`` is false. That is not a formality: it is what lets
    the decoder mask invalid tokens, and it is also what removes the "key is
    missing" failure mode that the json_object path has to defend against with
    post-hoc coercion.
    """
    return {
        "name": name,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
            "additionalProperties": False,
        },
    }


def response_format_for(
    model: Optional[str], *, properties: dict[str, Any], name: str
) -> dict[str, Any]:
    """The strongest ``response_format`` this model can honour.

    Returns the strict json_schema form when the model supports constrained
    decoding, else the json_object form that every Groq model accepts. Callers
    pass the result straight through and do not branch — the point is that
    adding a strict-capable model later needs no change at the call site.
    """
    if supports_strict_schema(model):
        return {
            "type": "json_schema",
            "json_schema": strict_object_schema(properties, name=name),
        }
    return {"type": "json_object"}


def strict_mode_active(model: Optional[str]) -> bool:
    """Whether the response is schema-GUARANTEED for this model.

    Callers use this to skip defensive work that cannot fire under strict mode
    — most usefully the reparse-and-retry that otherwise costs a second full
    LLM round trip on every malformed response.
    """
    return supports_strict_schema(model)


def summariser_model() -> str:
    """Model for the offline call-summary extraction.

    Overridable so an operator can move this job onto a strict-capable model
    without a code change. The default stays llama-3.3-70b — changing the model
    that writes customer-visible summaries is not a decision to make silently
    on someone's behalf.
    """
    return (
        os.getenv("CALL_SUMMARY_MODEL", "").strip()
        or "llama-3.3-70b-versatile"
    )
