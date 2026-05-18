"""Persona templates. Brand-free — every template uses {slot} placeholders
filled at composition time by composer.compose_prompt().

Direction-aware exports (T4-A1):
* ``PERSONA_BODIES``    — direction-agnostic body templates with a
                          ``{direction_opening}`` slot the composer fills
                          based on the call's Direction.
* ``PERSONA_OPENINGS``  — per-persona dict of direction → opening block.
* ``PERSONAS``          — backward-compat full-template view, defaulting
                          to each persona's natural direction (lead_gen
                          outbound, customer_support inbound, receptionist
                          inbound). Existing callers that don't pass
                          direction get the historical behaviour.
"""
from __future__ import annotations

from typing import Literal

from app.services.scripts.prompts.personas.customer_support import (
    CUSTOMER_SUPPORT_BODY,
    CUSTOMER_SUPPORT_OPENINGS,
    CUSTOMER_SUPPORT_PERSONA,
    format_common_issues,
    format_escalate_triggers,
)
from app.services.scripts.prompts.personas.customer_support import (
    REQUIRED_SLOTS as SUPPORT_REQUIRED_SLOTS,
)
from app.services.scripts.prompts.personas.lead_gen import (
    LEAD_GEN_BODY,
    LEAD_GEN_OPENINGS,
    LEAD_GEN_PERSONA,
    format_qualification_questions,
)
from app.services.scripts.prompts.personas.lead_gen import (
    REQUIRED_SLOTS as LEAD_GEN_REQUIRED_SLOTS,
)
from app.services.scripts.prompts.personas.receptionist import (
    RECEPTIONIST_BODY,
    RECEPTIONIST_OPENINGS,
    RECEPTIONIST_PERSONA,
    format_new_patient_info_needed,
)
from app.services.scripts.prompts.personas.receptionist import (
    REQUIRED_SLOTS as RECEPTIONIST_REQUIRED_SLOTS,
)

PersonaType = Literal["lead_gen", "customer_support", "receptionist"]
DirectionStr = Literal["outbound", "inbound"]


# Direction-agnostic persona bodies — each contains a ``{direction_opening}``
# slot the composer fills based on call direction. Add a new persona here
# (and to PERSONA_OPENINGS) and the composer picks it up automatically.
PERSONA_BODIES: dict[str, str] = {
    "lead_gen": LEAD_GEN_BODY,
    "customer_support": CUSTOMER_SUPPORT_BODY,
    "receptionist": RECEPTIONIST_BODY,
}

# Per-persona OPENING blocks keyed by direction. Both directions are
# supported for every persona; the natural / historical direction
# (outbound for lead_gen, inbound for the other two) is what the
# backward-compat PERSONAS view exposes.
PERSONA_OPENINGS: dict[str, dict[str, str]] = {
    "lead_gen": LEAD_GEN_OPENINGS,
    "customer_support": CUSTOMER_SUPPORT_OPENINGS,
    "receptionist": RECEPTIONIST_OPENINGS,
}


# Backward-compat: full-template view defaulting to each persona's
# natural direction. Callers that don't pass `direction` to
# compose_prompt fall back to this view.
PERSONAS: dict[str, str] = {
    "lead_gen": LEAD_GEN_PERSONA,
    "customer_support": CUSTOMER_SUPPORT_PERSONA,
    "receptionist": RECEPTIONIST_PERSONA,
}

REQUIRED_SLOTS_BY_PERSONA: dict[str, tuple[str, ...]] = {
    "lead_gen": LEAD_GEN_REQUIRED_SLOTS,
    "customer_support": SUPPORT_REQUIRED_SLOTS,
    "receptionist": RECEPTIONIST_REQUIRED_SLOTS,
}

__all__ = [
    "PERSONAS",
    "PERSONA_BODIES",
    "PERSONA_OPENINGS",
    "PersonaType",
    "DirectionStr",
    "REQUIRED_SLOTS_BY_PERSONA",
    "format_common_issues",
    "format_escalate_triggers",
    "format_new_patient_info_needed",
    "format_qualification_questions",
]
