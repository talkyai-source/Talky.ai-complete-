"""bughunt knowledge-prompts-5: {first_name}-style slots must survive save-time
sanitising in a form the callee-name handling can still see."""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import (
    find_unfilled_slots,
    script_greets_callee_by_name,
)
from app.services.scripts.prompts.prompt_safety import sanitize_tenant_text


@pytest.mark.parametrize(
    "script",
    [
        "Hi, is that {first_name}?",
        "Hi, is that {{name}}?",
        "Hello {First Name}, it's Sam.",
        "Good morning { full_name }.",
        "Is that {{ their name }}?",
    ],
)
def test_brace_name_slot_is_still_a_callee_name_slot_after_sanitising(script):
    cleaned = sanitize_tenant_text(script)
    assert "{" not in cleaned and "}" not in cleaned
    assert script_greets_callee_by_name(cleaned) is True
    assert find_unfilled_slots(cleaned), cleaned


def test_other_braces_are_still_neutralised_to_parentheses():
    out = sanitize_tenant_text("We do {roofing} and {undefined_slot}")
    assert out == "We do (roofing) and (undefined_slot)"


def test_parenthesised_name_prose_is_not_treated_as_a_slot():
    cleaned = sanitize_tenant_text("Ask for their (name) and note it.")
    assert cleaned == "Ask for their (name) and note it."
    assert script_greets_callee_by_name(cleaned) is False
    assert find_unfilled_slots(cleaned) == []
