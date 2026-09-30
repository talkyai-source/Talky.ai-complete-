"""Independent Realtime campaign setup; never composes a cascaded prompt."""
from app.domain.models.agent_config import AgentConfig, AgentGoal
from app.realtime.config import RealtimePrompt, validate_realtime


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
    model = script.get("realtime_model") or source.realtime_model
    voice = script.get("realtime_voice") or source.realtime_voice
    settings = script.get("realtime_settings")
    if settings is None:
        settings = source.realtime_settings
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
