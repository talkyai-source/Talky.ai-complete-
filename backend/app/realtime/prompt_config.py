"""Map shared call identity into the independent Realtime prompt."""
def build_realtime_persona(config):
    """Map campaign/agent config onto a RealtimePersona. Tolerant of
    missing fields — realtime should work even on a bare campaign."""
    from app.realtime.prompts import RealtimePersona

    direction_value = getattr(config.direction, "value", config.direction)
    direction_text = str(direction_value or "outbound").strip().lower()
    ac = config.agent_config
    agent_name = getattr(ac, "agent_name", None) or getattr(ac, "name", None) or "Alex"
    company = getattr(ac, "company_name", None) or getattr(ac, "company", None) or "the company"
    # "Who you're calling" for the realtime pipeline. The cascaded path
    # gets this via system_prompt; realtime builds instructions from the
    # persona, so surface it as extra_notes. Fail-soft: no name → no note.
    extra_notes = None
    first = (getattr(config, "callee_first_name", None) or "").strip()
    last = (getattr(config, "callee_last_name", None) or "").strip()
    callee_company = (getattr(config, "callee_company", None) or "").strip()
    full = " ".join(p for p in (first, last) if p).strip()
    if full and direction_text != "inbound":
        greet = first or full
        comp_clause = f" from {callee_company}" if callee_company else ""
        extra_notes = (
            f"You are calling {full}{comp_clause}. You haven't spoken to "
            "them yet, so treat the name as who you expect to reach, not a "
            "confirmed fact. Greet them by first name and check you've "
            f'reached the right person (e.g. "Hi, is this {greet}?") before '
            "getting into it. Don't recite their details back robotically."
        )

    return RealtimePersona(
        agent_name=str(agent_name),
        company_name=str(company),
        role="a friendly voice assistant",
        goal=(config.realtime_prompt or {}).get("goal") or "Help the caller using verified company information.",
        extra_notes=extra_notes,
        campaign_guidance=(config.realtime_prompt or {}).get("instructions", ""),
        persona_type=(config.realtime_prompt or {}).get("persona", "assistant"),
        call_direction=direction_text,
        opening_greeting=getattr(
            config, "realtime_opening_greeting", None
        ),
        message_intake=bool(
            getattr(config, "realtime_message_intake", False)
        ),
    )


def prepare_realtime_prompt(config):
    """Compose and identify the exact independent instructions sent to the model."""
    import hashlib
    from app.realtime.prompts import PROMPT_VERSION, build_realtime_instructions
    instructions = build_realtime_instructions(build_realtime_persona(config))
    config.system_prompt = instructions
    config.prompt_template = "realtime_voice"
    config.prompt_version = PROMPT_VERSION
    config.prompt_hash = hashlib.sha256(instructions.encode()).hexdigest()[:16]
    return instructions
