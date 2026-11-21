"""An opt-out does not cut off a request made in the same breath (2026-11-21).

"Don't call me again, but I need help with my account." The do-not-call entry
is written (once) and the agent must not be authorised to hang up on the help
the caller just asked for. An opt-out with a real goodbye, or alone, still
closes the call.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.action_tools import run_voice_action
from app.domain.services.voice_pipeline.end_call import model_end_call_allowed

REQUEST = "Don't call me again, but I need help with my account."
REQUEST_QUESTION = "Do not call me again, but what are your opening hours?"
WITH_GOODBYE = "Don't call me again. Actually, goodbye."
ALONE = "Stop calling me."


def _session(**flags):
    return SimpleNamespace(conversation_history=[], **flags)


@pytest.fixture
def purge(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.domain.services.dialer.opt_out.purge_opt_out_before_farewell", mock
    )
    return mock


# ------------------------------------------------------------ text-marker path

@pytest.mark.parametrize("text", [REQUEST, REQUEST_QUESTION])
def test_marker_path_opt_out_with_a_request_does_not_authorise_close(text):
    assert model_end_call_allowed(_session(_caller_opted_out=True), text) is False


@pytest.mark.parametrize("text", [WITH_GOODBYE, ALONE])
def test_marker_path_opt_out_with_goodbye_or_alone_still_closes(text):
    assert model_end_call_allowed(_session(_caller_opted_out=True), text) is True


def test_marker_path_later_turn_without_a_request_closes_after_opt_out():
    assert model_end_call_allowed(_session(_caller_opted_out=True), "Thanks, bye.") is True


# ----------------------------------------------------------------- tool path

@pytest.mark.asyncio
@pytest.mark.parametrize("text", [REQUEST, REQUEST_QUESTION])
async def test_tool_path_records_opt_out_once_but_does_not_close(purge, text):
    session = _session()
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=text)
    assert purge.await_count == 1
    assert session._caller_opted_out is True
    assert result["success"] is False
    assert result["status"] == "caller_intent_unconfirmed"
    assert getattr(session, "_end_call_requested", False) is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [WITH_GOODBYE, ALONE])
async def test_tool_path_opt_out_with_goodbye_or_alone_still_closes(purge, text):
    session = _session()
    result = await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=text)
    assert purge.await_count == 1
    assert result["success"] is True
    assert result["opt_out"] == "recorded"
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_tool_path_later_turn_closes_normally_after_the_request_turn(purge):
    session = _session()
    await run_voice_action(session, "end_call", {"do_not_call": True}, user_text=REQUEST)
    result = await run_voice_action(session, "end_call", {}, user_text="No, that's all, goodbye.")
    assert result["success"] is True
    assert session._end_call_requested is True
    assert purge.await_count == 1
