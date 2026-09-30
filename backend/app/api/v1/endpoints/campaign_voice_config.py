"""Persist independent voice-engine settings using the existing campaign JSONB."""
from fastapi import HTTPException
from app.realtime.config import RealtimePrompt, validate_realtime


async def build_campaign_voice_config(data, ai_config, *, existing=None):
    from app.api.v1.endpoints.campaigns import _build_validated_script_config, _valid_voice_ids_for_provider
    previous = existing or {}
    selection = {k: previous[k] for k in (
        "pipeline_mode", "realtime_model", "realtime_voice", "realtime_settings", "realtime_prompt"
    ) if k in previous}
    for key in data.model_fields_set:
        if key in {"pipeline_mode", "realtime_model", "realtime_voice", "realtime_settings", "realtime_prompt"}:
            value = getattr(data, key)
            selection[key] = value.model_dump() if hasattr(value, "model_dump") else value
    mode = selection.get("pipeline_mode") or ai_config.pipeline_mode
    selection["pipeline_mode"] = mode
    voice_id = data.voice_id.strip()
    if mode == "realtime":
        selection["realtime_model"] = selection.get("realtime_model") or ai_config.realtime_model
        selection["realtime_voice"] = selection.get("realtime_voice") or ai_config.realtime_voice
        if selection.get("realtime_settings") is None:
            selection["realtime_settings"] = ai_config.realtime_settings
        try:
            validate_realtime(selection["realtime_model"], selection["realtime_voice"], selection["realtime_settings"])
            selection["realtime_prompt"] = RealtimePrompt.model_validate(selection.get("realtime_prompt") or (ai_config.realtime_settings or {}).get("prompt") or {}).model_dump()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Identity and transport remain shared. Traditional guidance/slots are
        # retained for switching back but never composed or read by Realtime.
        script = {
            "persona_type": data.persona_type, "company_name": data.company_name.strip(),
            "agent_names": data.agent_names, "campaign_slots": data.campaign_slots,
            "additional_instructions": data.system_prompt, "knowledge_driven": data.knowledge_driven,
            "campaign_brief": data.campaign_brief.model_dump() if data.campaign_brief else None,
        }
        voice_id = voice_id or ai_config.tts_voice_id
    else:
        provider = data.tts_provider or ai_config.tts_provider
        if voice_id not in await _valid_voice_ids_for_provider(provider):
            raise HTTPException(400, f"Voice '{voice_id}' is not available for TTS provider '{provider}'. Pick a matching voice or change the provider.")
        script = _build_validated_script_config(
            persona_type=data.persona_type, company_name=data.company_name,
            agent_names=data.agent_names, campaign_slots=data.campaign_slots,
            additional_instructions=data.system_prompt, knowledge_driven=data.knowledge_driven,
            campaign_brief=data.campaign_brief.model_dump() if data.campaign_brief else None,
        )
    script.update(selection)
    return script, voice_id
