"""Browser test 98b83aaf (2026-09-30, Dojo-PC): "Hi, is that Alex?" then "I'm Alex".

The campaign script opens with 'Say: "Hi, is that [First Name]?"'. A browser
test has no contact, so no name was on file; the blank-slot guard only knew
lower-case slots, and the model filled "[First Name]" with the one name in its
prompt -- its own. With a name slot and no name, the agent is now told to
greet without one and never to put its own name there.
"""
from __future__ import annotations

from app.domain.services.telephony_session_config import (
    UNKNOWN_CALLEE_NAME_BLOCK,
    build_telephony_session_config,
    find_unfilled_slots,
    script_greets_callee_by_name,
)
from app.domain.services.voice_orchestrator import Direction

SCRIPT = 'OPENING Say: "Hi, is that [First Name]?" WAIT. If yes: "I\'m Alex, an AI assistant."'


def _cfg(**kw):
    campaign = {
        "id": "c-dojo",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "script_config": {
            "company_name": "Dojo",
            "agent_names": ["Alex"],
            "additional_instructions": SCRIPT,
        },
    }
    return build_telephony_session_config(
        gateway_type="browser",
        campaign=campaign,
        direction=Direction.OUTBOUND,
        **kw,
    )


def test_the_name_slot_is_recognised_in_any_case():
    assert script_greets_callee_by_name(SCRIPT)
    assert script_greets_callee_by_name("Hello {first_name}, quick one")
    assert not script_greets_callee_by_name("Say [repeat email slowly]")
    assert find_unfilled_slots(SCRIPT) == ["[First Name]"]


def test_no_name_on_file_tells_the_agent_to_greet_without_one():
    cfg = _cfg()
    assert cfg.system_prompt.endswith(UNKNOWN_CALLEE_NAME_BLOCK)
    assert "Never put your own name" in cfg.system_prompt


def test_with_a_name_on_file_the_name_is_used_instead():
    cfg = _cfg(lead_first_name="Uzair", lead_last_name="Khan")
    assert "PERSON YOU'RE CALLING: Uzair Khan" in cfg.system_prompt
    assert UNKNOWN_CALLEE_NAME_BLOCK not in cfg.system_prompt
