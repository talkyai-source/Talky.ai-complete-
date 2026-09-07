"""Regression cases reproduced during the production integrity audit."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle


@pytest.mark.parametrize("seconds,text,role,cause", [
    (2, None, "user", "normal_clearing"),
    (30, "Can I leave a message for your team?", "user", None),
    (30, "I can help you leave a message.", "assistant", None),
    (30, "Hello", "user", "no_answer"),
])
def test_answered_inbound_never_uses_outbound_engagement_or_voicemail_heuristics(seconds, text, role, cause):
    session = SimpleNamespace(
        _inbound_selected_action="agent",
        _answered_at_monotonic=100.0,
        call_session=SimpleNamespace(
            started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds),
            conversation_history=[] if text is None else [SimpleNamespace(role=role, content=text)],
        ),
    )
    assert lifecycle._resolve_inbound_terminal_outcome(session, {}, hangup_reason=cause) == "answered"


@pytest.mark.parametrize("flags,expected", [
    ({"_goal_achieved": True}, "goal_achieved"),
    ({"_goal_failed": True}, "goal_not_achieved"),
    ({"_pipeline_failed": True, "_goal_achieved": True}, "failed"),
])
def test_inbound_retains_explicit_business_and_failure_outcomes(flags, expected):
    assert lifecycle._resolve_inbound_terminal_outcome(SimpleNamespace(**flags), {}) == expected

