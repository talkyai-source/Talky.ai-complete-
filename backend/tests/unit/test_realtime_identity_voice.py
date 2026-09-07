"""Identity follows the selected speech pipeline's existing voice catalogue."""
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony_session_config import build_telephony_session_config
from app.domain.services.global_ai_config import resolve_voice_gender
import pytest


def campaign(**script):
    return {"id": "identity-campaign", "script_config": {
        "company_name": "Acme", "agent_names": ["Sarah"],
        "agent_name_genders": {"Sarah": "female"}, **script,
    }}


@pytest.mark.parametrize("unused_tts_voice", ["aura-2-zeus-en", "aura-2-andromeda-en"])
def test_realtime_female_voice_retains_configured_female_name(unused_tts_voice):
    cfg = build_telephony_session_config(campaign=campaign(), ai_config_override=AIProviderConfig(
        pipeline_mode="realtime", realtime_voice="marin", tts_voice_id=unused_tts_voice,
    ))
    assert cfg.agent_config.agent_name == "Sarah"
    assert cfg.realtime_voice == "marin"


def test_campaign_realtime_voice_override_drives_name_selection():
    cfg = build_telephony_session_config(campaign=campaign(realtime_voice="marin"), ai_config_override=AIProviderConfig(
        pipeline_mode="realtime", realtime_voice="cedar", tts_voice_id="aura-2-zeus-en",
    ))
    assert cfg.agent_config.agent_name == "Sarah"


@pytest.mark.parametrize("voice,gender", [("marin", "female"), ("cedar", "male"), ("alloy", None), ("future-unknown", None)])
def test_realtime_catalogue_metadata_is_used_without_guessing(voice, gender):
    assert resolve_voice_gender(voice) == gender


def test_unknown_realtime_voice_does_not_borrow_cascaded_voice_gender():
    cfg = build_telephony_session_config(campaign=campaign(), ai_config_override=AIProviderConfig(
        pipeline_mode="realtime", realtime_voice="future-unknown", tts_voice_id="aura-2-zeus-en",
    ))
    assert cfg.agent_config.agent_name == "Sarah"
