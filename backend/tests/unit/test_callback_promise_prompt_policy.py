"""Runtime callback availability and requested time are facts, not a script."""
import json
import pytest

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompt_builder import compose_system_prompt


@pytest.mark.parametrize("available", [False, True])
def test_callback_capability_is_visible_without_inventing_an_outcome(available):
    base = "Use the operator's own wording."
    prompt = compose_system_prompt(base, CallState(), has_callback_executor=available)
    _, data, unchanged = prompt.split("\n", 2)
    facts = json.loads(data)
    assert facts == {"callback_scheduling_available": available}
    assert unchanged == "\n" + base


def test_requested_time_is_not_a_scheduled_callback_receipt():
    prompt = compose_system_prompt("BASE", CallState(follow_up="Tuesday afternoon"))
    facts = json.loads(prompt.split("\n", 2)[1])
    assert facts["requested_follow_up_not_scheduled"] == "Tuesday afternoon"
    assert facts["callback_scheduling_available"] is False
    assert "scheduled" not in facts
