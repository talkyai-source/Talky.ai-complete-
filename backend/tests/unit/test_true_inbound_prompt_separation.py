"""Direction is a composition boundary, not a contradictory prefix."""
import copy
from types import SimpleNamespace

import pytest

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.telephony import lifecycle
from app.domain.services.telephony.modes.caller_first import select_inbound_base_prompt
from app.domain.services.voice_orchestrator import Direction, VoiceOrchestrator
from app.services.scripts.prompts.composer import compose_prompt, PromptCompositionError
from app.services.scripts.prompts.versions import hash_prompt
from app.services.scripts.prompts.guardrails import compliance_floor
from app.services.scripts.realtime_instructions import build_realtime_instructions
from tests.unit.test_prompt_composer_direction import LEAD_GEN_SLOTS, SUPPORT_SLOTS, RECEPTIONIST_SLOTS


PERSONAS = [("lead_gen", LEAD_GEN_SLOTS), ("customer_support", SUPPORT_SLOTS), ("receptionist", RECEPTIONIST_SLOTS)]
FORBIDDEN = ("OUTBOUND CALL", "You dialed this person", "Do NOT answer like a receptionist",
             "still YOUR outbound call", "Own that it's a cold call", "WHO YOU'RE TRYING TO REACH",
             "## HOW YOU SELL", "WRONG PERSON / GATEKEEPER", "we call\n  back another time")


@pytest.mark.parametrize("persona,slots", PERSONAS)
@pytest.mark.parametrize("knowledge", [True, False])
@pytest.mark.parametrize("opening", ["agent_first", "callee_first"])
def test_inbound_never_contains_outbound_platform_instructions(persona, slots, knowledge, opening):
    text = compose_prompt(persona, "Sam", "Acme", slots, "Keep fixture-guidance intact.",
                          direction="inbound", opening_mode=opening, knowledge_driven=knowledge)
    assert text.startswith("TRUE INBOUND CALL")
    for phrase in FORBIDDEN:
        assert phrase not in text
    assert "Keep fixture-guidance intact." in text
    assert compliance_floor("Acme") in text
    assert "[[END_CALL]]" in text


@pytest.mark.parametrize("persona,slots", PERSONAS)
def test_callee_first_outbound_never_uses_receptionist_opening(persona, slots):
    text = compose_prompt(persona, "Sam", "Acme", slots, direction="outbound", opening_mode="callee_first")
    assert text.startswith("OUTBOUND CALL")
    assert "TRUE INBOUND CALL" not in text
    assert "Thanks for calling Acme" not in text
    assert "Thank you for calling Acme" not in text
    assert "pickup greeting already" not in " ".join(text.split())


def admission(mode="caller_first", pipeline="cascaded"):
    ai = AIProviderConfig().model_dump()
    ai.update(id="fixture-ai", pipeline_mode=pipeline, tts_model="fixture-tts")
    return {"opening_mode": mode, "config_snapshot": {
        "campaign": {"id": "fixture-campaign", "tenant_id": "fixture-tenant", "direction": "inbound",
                     "script_config": {"company_name": "Acme", "agent_names": ["Sam"],
                                       "persona_type": "lead_gen", "knowledge_driven": True,
                                       "additional_instructions": "Preserve fixture-guidance exactly."}},
        "inbound_config": {"opening_mode": mode, "greeting": "Welcome to Acme.",
                           "after_hours_message": "Acme is closed. Please leave a message.",
                           "qualification_config": {}},
        "tenant_ai_config": ai,
    }}


@pytest.mark.parametrize("mode", ["caller_first", "agent_first"])
@pytest.mark.parametrize("action", ["agent", "voicemail"])
@pytest.mark.parametrize("pipeline", ["cascaded", "realtime"])
def test_real_pinned_builder_separates_direction_and_hashes_final_prompt(mode, action, pipeline):
    payload = admission(mode, pipeline)
    before = copy.deepcopy(payload)
    config, _ = lifecycle._build_pinned_inbound_config(payload, gateway_type="browser", selected_action=action)
    assert payload == before
    assert config.direction == Direction.INBOUND
    assert config.prompt_hash == hash_prompt(config.system_prompt)
    assert config.system_prompt.count("TRUE INBOUND CALL") == 1
    for phrase in FORBIDDEN:
        assert phrase not in config.system_prompt
    if pipeline == "realtime":
        text = build_realtime_instructions(VoiceOrchestrator._build_realtime_persona(config))
        assert "Preserve fixture-guidance exactly." in text
        assert "caller contacted the company" in text
        for phrase in FORBIDDEN:
            assert phrase not in text
    assert config.realtime_greet_on_start == (mode == "agent_first" or action == "voicemail")
    if action == "voicemail":
        assert "AFTER-HOURS AI MESSAGE INTAKE" in config.system_prompt


def test_legacy_caller_first_shaper_cannot_turn_inbound_into_outbound():
    config, _ = lifecycle._build_pinned_inbound_config(admission(), gateway_type="browser", selected_action="agent")
    session = SimpleNamespace(config=config, call_session=SimpleNamespace(system_prompt=config.system_prompt))
    before = session.call_session.system_prompt
    select_inbound_base_prompt(session)
    assert session.call_session.system_prompt == before


def test_inbound_refuses_directionless_archived_body_instead_of_reintroducing_outbound():
    with pytest.raises(PromptCompositionError, match="inbound"):
        compose_prompt("lead_gen", "Sam", "Acme", {}, direction="inbound", knowledge_driven=True,
                       body_override="You dialed this person. Archived outbound playbook.")
