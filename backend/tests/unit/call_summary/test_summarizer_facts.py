"""The summarizer must be told the truth, not just the transcript.

Production, 2026-09-23 (issue: "summary states unconfirmed data as fact"):

- Call 6aaeb4dd's summary stated "Phone: 92301625319" (an invalid number that
  was never confirmed) and "Email: hishamkhan62@gmail.com" (never confirmed
  -- the caller was still stuck in the re-spell loop) as plain key_points.
- Call a5e033c7's headline read "Qualified -- scheduled root canal
  appointment" while `action_results` was `{}` -- no booking tool exists, so
  nothing had actually executed.

`summarizer.summarize_transcript(transcript_text)` only ever saw the
transcript, which is exactly why it could not tell an agreed-upon read-back
from an executed booking. This module tests both halves of the fix: the
prompt-construction side (summarizer.py builds a FACTS preamble the model
cannot contradict) and the fact-loading side (store.py computes those facts
from call_lead_details / calls.action_results, the same tables the live call
gates on).

On the unmodified module, `summarize_transcript(text, confirmed_contacts={},
executed_actions=[])` raised ``TypeError: unexpected keyword argument
'confirmed_contacts'`` -- reproduced directly against the pre-fix file before
writing this test.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.call_summary.store import (
    _confirmed_contacts_for_call,
    _executed_actions_from_results,
    generate_and_store,
)
from app.domain.services.call_summary.summarizer import (
    _SYSTEM_PROMPT,
    _facts_block,
    summarize_transcript,
)

_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_CALL_ID = "00000000-0000-0000-0000-000000000002"

# The real, final-transcript loop from 6aaeb4dd.transcript.txt that ended with
# the agent stating an unconfirmed address as a fact.
_6AAEB4DD_EMAIL_LOOP = (
    "ASSISTANT: Please provide your email address.\n"
    "USER: at gmail dot com.\n"
    "ASSISTANT: Could you spell the part before @, then say “at gmail dot com”?\n"
    "USER: I get it?\n"
    "ASSISTANT: So the email is hishamkhan62 at gmail dot com, correct?\n"
)


# ---------------------------------------------------------------------------
# _facts_block (summarizer.py)
# ---------------------------------------------------------------------------


def test_facts_block_flags_no_confirmed_contact_and_no_executed_action():
    block = _facts_block(None, None)
    assert "No contact detail on this call was confirmed" in block
    assert "No booking, callback, or send action executed" in block


def test_facts_block_states_a_confirmed_contact_as_ground_truth():
    block = _facts_block({"phone": "+923016253193"}, [])
    assert "Caller-confirmed phone: +923016253193" in block
    # Still no action executed.
    assert "No booking, callback, or send action executed" in block


def test_facts_block_lists_an_executed_action():
    block = _facts_block({}, ["schedule_callback"])
    assert "Executed successfully: schedule_callback" in block
    assert "No contact detail" in block


def test_system_prompt_tells_the_model_to_defer_to_the_facts_block():
    """Static instruction, always present regardless of what facts exist."""
    lowered = _SYSTEM_PROMPT.lower()
    assert "facts block" in lowered
    assert "(unconfirmed)" in _SYSTEM_PROMPT
    assert "requested or discussed" in lowered


# ---------------------------------------------------------------------------
# summarize_transcript prompt construction
# ---------------------------------------------------------------------------


def _fake_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.mark.asyncio
async def test_unconfirmed_contacts_reach_the_model_as_unconfirmed():
    """The 6aaeb4dd scenario: nothing was confirmed, so the model must be told so."""
    captured = {}

    async def _create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _fake_completion(json.dumps({"headline": "ok"}))

    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=_create)))
    )
    with patch(
        "app.domain.services.call_summary.summarizer.AsyncGroq",
        return_value=mock_client,
    ):
        await summarize_transcript(
            _6AAEB4DD_EMAIL_LOOP,
            confirmed_contacts={},
            executed_actions=[],
        )

    user_content = captured["messages"][1]["content"]
    assert "No contact detail on this call was confirmed" in user_content
    # The facts precede the transcript in the same message.
    assert user_content.index("FACTS") < user_content.index(_6AAEB4DD_EMAIL_LOOP.strip()[:10])


@pytest.mark.asyncio
async def test_a_confirmed_phone_is_handed_to_the_model_as_fact():
    captured = {}

    async def _create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _fake_completion(json.dumps({"headline": "ok"}))

    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=_create)))
    )
    with patch(
        "app.domain.services.call_summary.summarizer.AsyncGroq",
        return_value=mock_client,
    ):
        await summarize_transcript(
            "Agent: what's your number?\nCaller: 555 123 4567",
            confirmed_contacts={"phone": "+15551234567"},
            executed_actions=[],
        )

    user_content = captured["messages"][1]["content"]
    assert "Caller-confirmed phone: +15551234567" in user_content


@pytest.mark.asyncio
async def test_no_facts_kwargs_still_works_backward_compatible():
    """Existing callers that pass only transcript_text must keep working."""
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_fake_completion(json.dumps({"headline": "ok"})))
            )
        )
    )
    with patch(
        "app.domain.services.call_summary.summarizer.AsyncGroq",
        return_value=mock_client,
    ):
        result = await summarize_transcript("Agent: Hi\nCaller: Hi")
    assert result["headline"] == "ok"


# ---------------------------------------------------------------------------
# Fact loading (store.py)
# ---------------------------------------------------------------------------


class TestExecutedActionsFromResults:
    def test_success_flagged_action_is_included(self):
        results = {"schedule_callback": {"success": True}, "send_email": {"success": False}}
        assert _executed_actions_from_results(results) == ["schedule_callback"]

    def test_json_string_is_parsed(self):
        results = json.dumps({"schedule_callback": {"success": True}})
        assert _executed_actions_from_results(results) == ["schedule_callback"]

    def test_empty_dict_yields_no_actions(self):
        # This is the observed prod shape: action_tools.py has no live
        # executor, so calls.action_results is always its `{}` default.
        assert _executed_actions_from_results({}) == []

    def test_malformed_input_never_raises(self):
        assert _executed_actions_from_results("not json") == []
        assert _executed_actions_from_results(None) == []
        assert _executed_actions_from_results(["not", "a", "dict"]) == []


class TestConfirmedContactsForCall:
    @pytest.mark.asyncio
    async def test_filters_to_confirmed_email_and_phone_only(self):
        rows = [
            {"field_key": "phone", "value": "+15551234567", "confirmed": True},
            {"field_key": "email", "value": "bob@acme.com", "confirmed": False},
            {"field_key": "follow_up", "value": "call back Tuesday", "confirmed": False},
        ]
        with patch(
            "app.domain.services.lead_capture_service.LeadCaptureService.details_for_call",
            AsyncMock(return_value=rows),
        ):
            result = await _confirmed_contacts_for_call(object(), _TENANT_ID, _CALL_ID)
        assert result == {"phone": "+15551234567"}

    @pytest.mark.asyncio
    async def test_fails_soft_when_the_lookup_raises(self):
        with patch(
            "app.domain.services.lead_capture_service.LeadCaptureService.details_for_call",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await _confirmed_contacts_for_call(object(), _TENANT_ID, _CALL_ID)
        assert result == {}

    @pytest.mark.asyncio
    async def test_fails_soft_on_a_none_pool(self):
        """generate_and_store's own tests run with pool=None."""
        result = await _confirmed_contacts_for_call(None, _TENANT_ID, _CALL_ID)
        assert result == {}


# ---------------------------------------------------------------------------
# End-to-end: generate_and_store wires both facts into summarize_transcript
# ---------------------------------------------------------------------------


def _make_conn(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value=None)
    return conn


@asynccontextmanager
async def _fake_acquire(conn):
    yield conn


@pytest.mark.asyncio
async def test_generate_and_store_passes_confirmed_contact_and_executed_action_through():
    """The two facts sources (call_lead_details, calls.action_results) both
    reach summarize_transcript for a real (non-empty) call."""
    conn = _make_conn({
        "transcript": "Agent: Hi\nCaller: Hi",
        "summary_json": None,
        "action_results": {"schedule_callback": {"success": True}},
    })
    mock_summarize = AsyncMock(return_value={"headline": "ok", "outcome": "callback"})

    with patch(
        "app.domain.services.call_summary.store.acquire_with_tenant",
        side_effect=lambda pool, tenant_id: _fake_acquire(conn),
    ):
        with patch(
            "app.domain.services.lead_capture_service.LeadCaptureService.details_for_call",
            AsyncMock(return_value=[
                {"field_key": "phone", "value": "+15551234567", "confirmed": True},
            ]),
        ):
            with patch(
                "app.domain.services.call_summary.store.summarize_transcript",
                mock_summarize,
            ):
                await generate_and_store(object(), _TENANT_ID, _CALL_ID)

    mock_summarize.assert_awaited_once_with(
        "Agent: Hi\nCaller: Hi",
        confirmed_contacts={"phone": "+15551234567"},
        executed_actions=["schedule_callback"],
    )
