"""The agent must not ask a question and hang up in the same turn.

The [[END_CALL]] sentinel path honoured a model hangup unconditionally (only a
wrong-person turn was exempt), while the JSON end-session path already required
the caller to have finished. In the 30 days to 2026-09-23 it hung up straight
after asking the caller something.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.services.end_session_action import agent_left_a_question_open


@pytest.mark.parametrize(
    "agent_line",
    [
        "Mike at example dot com — right?",                                    # 35d3fd2f
        "Right — pulling a report at day-end. Does that process ever hold you up?",  # 3aae86c6
        "When's a good time to call back?",                                    # 77531765
        "Got it. We'll send the sample. Anything else I can help with?",
        "Could I confirm the best phone number to reach you? [[END_CALL]]",
        'She said "is that right?"',
    ],
)
def test_production_hangups_after_a_question_are_recognised(agent_line):
    assert agent_left_a_question_open(agent_line)


@pytest.mark.parametrize(
    "agent_line",
    [
        "Got it. Thanks for your time—have a good day.",   # 06fbbda7, caller: "wasting my time"
        "Fair enough. Thanks for your time — have a good day.",  # 1c3a0c47
        "Thanks for your time. Goodbye.",
        "No problem, I'll take you off our list. Goodbye. [[END_CALL]]",
        "Is that okay? Great, take care then.",             # question mid-turn, closes on a statement
        "",
        None,
    ],
)
def test_legitimate_closes_are_left_alone(agent_line):
    assert not agent_left_a_question_open(agent_line)


def test_the_sentinel_gate_uses_it_and_dnc_and_goodbye_still_win():
    src = (
        Path(__file__).resolve().parents[2]
        / "app" / "domain" / "services" / "voice_pipeline" / "turn_ender.py"
    ).read_text(encoding="utf-8")
    gate = src[src.index("agent_left_a_question_open(response_text)") - 200 :]
    gate = gate[: gate.index("end_call_stripped_question_open")]
    assert "not contains_dnc(full_transcript)" in gate
    assert "not contains_explicit_goodbye(full_transcript)" in gate
    # the new branch sits BEFORE the branch that actually hangs up
    assert src.index("end_call_stripped_question_open") < src.index(
        '"agent_end_call call_id=%s — model requested hangup"'
    )
