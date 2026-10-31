"""Tests for the CallState tracker — slots: email, follow_up, project_type,
bidding_active, declined_count.

Regression-anchored on the 2026-04-22 live call where the agent failed to
notice a captured email and looped asking for it."""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState


def test_empty_state_is_empty():
    st = CallState()
    assert st.email is None
    assert st.follow_up is None
    assert st.bidding_active is None
    assert st.declined_count == 0
