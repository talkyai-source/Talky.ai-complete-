"""Independent Realtime campaign setup; never composes a cascaded prompt."""
from app.domain.models.agent_config import AgentConfig, AgentGoal
from app.realtime.config import RealtimePrompt, validate_realtime


_PERSONA_FOR_CAMPAIGN = {
    "lead_gen": "sales",
    "customer_support": "support",
    "receptionist": "receptionist",
}


def _with_campaign_context(prompt, script, attr):
    """A campaign with no Realtime-specific instructions runs on its own script.

    Browser tests 980a2caa/0457b4b9 (2026-09-30): Dojo-PC has a full campaign
    script (who Azian is, why we call, what to ask) but no Realtime prompt, so
    the Realtime agent was given only "Help the caller using verified company
    information." and opened with a generic line that had nothing to do with
    the campaign. Realtime-specific instructions, when set, still win.
    """
    campaign_rt = script.get("realtime_prompt") or {}
    if isinstance(campaign_rt, dict) and str(campaign_rt.get("instructions") or "").strip():
        # A Realtime prompt written FOR THIS CAMPAIGN replaces its script.
        return prompt
    guidance = (
        script.get("additional_instructions")
        or attr("system_prompt")
        or attr("goal")
        or ""
    )
    guidance = str(guidance or "").strip()
    if not guidance:
        return prompt
    # Account-wide Realtime notes (AI Options) apply to every campaign, so
    # they are added to the campaign's script, never swapped in for it.
    # Browser test 94f47f14 (2026-09-30): the account note "be precise and
    # specific and to the point" replaced Dojo-PC's whole script.
    account_notes = str(prompt.instructions or "").strip()
    if account_notes:
        guidance = f"{guidance}\n\nAccount-wide notes: {account_notes}"
    from app.domain.services.telephony_session_config import (
        campaign_guidance_char_budget,
    )

    budget = campaign_guidance_char_budget()
    if len(guidance) > budget:
        guidance = guidance[:budget].rsplit(" ", 1)[0]
    update = {"instructions": guidance}
    brief = script.get("campaign_brief") or {}
    objective = str(brief.get("opening_objective") or "").strip() if isinstance(brief, dict) else ""
    if objective and prompt.goal == type(prompt)().goal:
        update["goal"] = objective[:1000]
    if prompt.persona == type(prompt)().persona:
        persona = _PERSONA_FOR_CAMPAIGN.get(str(script.get("persona_type") or "").strip())
        if persona:
            update["persona"] = persona
    return prompt.model_copy(update=update)


def build_realtime_campaign_config(*, source, campaign, script, gateway_type,
                                   agent_name_override, direction, opening_mode,
                                   lead_first_name=None, lead_last_name=None,
                                   lead_company=None, **_unused):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    def attr(name, default=None):
        return campaign.get(name, default) if isinstance(campaign, dict) else getattr(campaign, name, default)

    def clean(value):
        return " ".join(str(value or "").replace("{", "").replace("}", "").split())[:160]

    prompt = RealtimePrompt.model_validate(script.get("realtime_prompt") or (source.realtime_settings or {}).get("prompt") or {})
    prompt = _with_campaign_context(prompt, script, attr)
    model = script.get("realtime_model") or source.realtime_model
    voice = script.get("realtime_voice") or source.realtime_voice
    settings = script.get("realtime_settings")
    if settings is None:
        settings = source.realtime_settings
    try:
        validate_realtime(model, voice, settings)
    except ValueError as exc:
        # A saved voice the catalog no longer lists must not stop a call from
        # starting; saving is where an unsupported voice is refused. Use the
        # default voice and say so.
        if "voice" not in str(exc).lower():
            raise
        import logging
        logging.getLogger(__name__).warning(
            "realtime_voice_unsupported voice=%r -- using the default voice", voice
        )
        voice = "marin"
        validate_realtime(model, voice, settings)
    names = script.get("agent_names") or ["Alex"]
    name = clean(agent_name_override or names[0])
    company = clean(script.get("company_name")) or "the company"
    config = VoiceSessionConfig(
        pipeline_mode="realtime", realtime_model=model, realtime_voice=voice,
        realtime_settings=settings, realtime_prompt=prompt.model_dump(),
        gateway_type=gateway_type, gateway_sample_rate=8000,
        gateway_input_sample_rate=8000, gateway_target_buffer_ms=40,
        session_type="telephony", voice_id=voice,
        campaign_id=str(attr("id", "telephony")), tenant_id=str(attr("tenant_id")) if attr("tenant_id") else None,
        lead_id="sip-caller", direction=direction, opening_mode=opening_mode,
        persona_type=script.get("persona_type") or "lead_gen",
        agent_config=AgentConfig(agent_name=name, company_name=company,
                                 goal=AgentGoal.INFORMATION_GATHERING, business_type="Realtime campaign"),
        realtime_opening_greeting=prompt.opening_greeting or None,
        contact_phone_region=script.get("contact_phone_region") or script.get("default_country_code"),
        callee_first_name=clean(lead_first_name) or None,
        callee_last_name=clean(lead_last_name) or None,
        callee_company=clean(lead_company) or None,
    )
    from app.realtime.prompt_config import prepare_realtime_prompt
    prepare_realtime_prompt(config)
    return config
