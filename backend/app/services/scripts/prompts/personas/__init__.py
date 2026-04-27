"""Persona templates. Brand-free — every template uses {slot} placeholders
filled at composition time by composer.compose_prompt().
"""
from __future__ import annotations

from typing import Literal

from app.services.scripts.prompts.personas.customer_support import (
    CUSTOMER_SUPPORT_PERSONA,
    format_common_issues,
    format_escalate_triggers,
)
from app.services.scripts.prompts.personas.customer_support import (
    REQUIRED_SLOTS as SUPPORT_REQUIRED_SLOTS,
)
from app.services.scripts.prompts.personas.lead_gen import (
    LEAD_GEN_PERSONA,
    format_qualification_questions,
)
from app.services.scripts.prompts.personas.lead_gen import (
    REQUIRED_SLOTS as LEAD_GEN_REQUIRED_SLOTS,
)
from app.services.scripts.prompts.personas.receptionist import (
    RECEPTIONIST_PERSONA,
    format_new_patient_info_needed,
)
from app.services.scripts.prompts.personas.receptionist import (
    REQUIRED_SLOTS as RECEPTIONIST_REQUIRED_SLOTS,
)

PersonaType = Literal["lead_gen", "customer_support", "receptionist"]

# Registry of every persona. Add a new persona here and composer picks it
# up automatically.
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
    "PersonaType",
    "REQUIRED_SLOTS_BY_PERSONA",
    "format_common_issues",
    "format_escalate_triggers",
    "format_new_patient_info_needed",
    "format_qualification_questions",
]
