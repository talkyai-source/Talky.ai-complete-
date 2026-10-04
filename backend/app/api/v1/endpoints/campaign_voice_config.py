"""Persist independent voice-engine settings using the existing campaign JSONB."""
from fastapi import HTTPException
from app.realtime.config import RealtimePrompt, validate_realtime


async def build_campaign_voice_config(data, ai_config, *, existing=None, pool=None, tenant_id=None):
    from app.api.v1.endpoints.campaigns import _build_validated_script_config, _valid_voice_ids_for_provider
    previous = existing or {}
    selection = {k: previous[k] for k in (
        "pipeline_mode", "realtime_model", "realtime_voice", "realtime_settings", "realtime_prompt"
    ) if k in previous}
    for key in data.model_fields_set:
        if key in {"pipeline_mode", "realtime_model", "realtime_voice", "realtime_settings", "realtime_prompt"}:
            value = getattr(data, key)
            selection[key] = value.model_dump(exclude_unset=(key == "realtime_settings")) if hasattr(value, "model_dump") else value
    mode = selection.get("pipeline_mode") or ai_config.pipeline_mode
    selection["pipeline_mode"] = mode
    voice_id = data.voice_id.strip()
    if mode == "realtime":
        selection["realtime_model"] = selection.get("realtime_model") or ai_config.realtime_model
        selection["realtime_voice"] = selection.get("realtime_voice") or ai_config.realtime_voice
        if selection.get("realtime_settings") is None:
            selection["realtime_settings"] = ai_config.realtime_settings
        try:
            # An explicit nested prompt update supersedes the previously saved
            # alias; an explicit top-level prompt in this request still wins.
            incoming_settings = getattr(data, "realtime_settings", None)
            if hasattr(incoming_settings, "model_dump"):
                incoming_settings = incoming_settings.model_dump(exclude_unset=True)
            if ("realtime_prompt" not in data.model_fields_set
                    and "realtime_settings" in data.model_fields_set
                    and isinstance(incoming_settings, dict) and "prompt" in incoming_settings):
                selection["realtime_prompt"] = incoming_settings["prompt"]
            prompt = selection.get("realtime_prompt")
            if prompt is None:
                prompt = (selection["realtime_settings"] or {}).get("prompt")
            if prompt is None:
                prompt = (ai_config.realtime_settings or {}).get("prompt")
            selection["realtime_settings"] = validate_realtime(selection["realtime_model"], selection["realtime_voice"], selection["realtime_settings"])
            selection["realtime_prompt"] = RealtimePrompt.model_validate(prompt or {}).model_dump()
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
        if voice_id not in await _valid_voice_ids_for_provider(provider, pool=pool, tenant_id=tenant_id):
            raise HTTPException(400, f"Voice '{voice_id}' is not available for TTS provider '{provider}'. Pick a matching voice or change the provider.")
        script = _build_validated_script_config(
            persona_type=data.persona_type, company_name=data.company_name,
            agent_names=data.agent_names, campaign_slots=data.campaign_slots,
            additional_instructions=data.system_prompt, knowledge_driven=data.knowledge_driven,
            campaign_brief=data.campaign_brief.model_dump() if data.campaign_brief else None,
        )
    script.update(selection)
    return script, voice_id
