"""Receptionist role and business context without prescribed spoken lines."""
from __future__ import annotations

from app.services.scripts.prompts.policies import load_policy

RECEPTIONIST_OPENINGS = {
    "inbound": "OPENING CONTEXT\nThe caller contacted {company_name}. Welcome them naturally and help with their request.\n",
    "outbound": "OPENING CONTEXT\nYou are calling for {company_name}. Explain the approved purpose naturally and listen.\n",
}
RECEPTIONIST_BODY = load_policy("personas/receptionist")
RECEPTIONIST_PERSONA = RECEPTIONIST_OPENINGS["inbound"] + "\n" + RECEPTIONIST_BODY


def format_new_patient_info_needed(fields: list[str]) -> str:
    """Turn a plain list of intake field labels into the bulleted
    block the persona expects.
    """
    if not fields:
        return "  (no specific intake fields configured)"
    return "\n".join(f"  - {f}" for f in fields)


# Only the slots the campaign creator MUST provide. Other fields
# (client_term, prep_info, cancellation_notice, service_details,
# departments) have safe defaults applied by the composer.
REQUIRED_SLOTS = (
    "business_type",
    "business_address",
    "business_phone",
    "business_email",
    "website",
    "opening_hours",
    "services",
    "emergency_protocol",
    "new_patient_info_needed",
)
