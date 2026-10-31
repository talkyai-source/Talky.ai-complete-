"""Observed model offers must respect actual routes and supplied resources."""

from app.domain.services.voice_pipeline.action_tools import action_tool_system_addendum


def test_capability_prompt_lists_actual_routes_and_does_not_invent_fallbacks():
    prompt = action_tool_system_addendum({"end_call"})
    assert "Available actions for this call: end_call." in prompt
    assert "unlisted transfer or team follow-up route is unavailable" in prompt
    assert "do not invent a fallback resource" in prompt
