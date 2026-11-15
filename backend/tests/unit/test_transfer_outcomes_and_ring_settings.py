"""Transfer ring settings in the brief, and the agent being told the real outcome."""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignBriefInput
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.campaign_brief import normalize_campaign_brief
from app.domain.services.telephony import adapter_registry
from app.domain.services.voice_pipeline import action_execution
from app.domain.services.voice_pipeline.action_tools import (
    action_tools_for_turn,
    run_voice_action,
)
from app.domain.services.voice_pipeline.speech_guard import (
    UNBACKED_ACTION_LINE,
    UNRESOLVED_ACTION_LINE,
    guard_spoken_sentence,
)
from app.realtime.bridge import RealtimeBridge
from app.realtime.tools import realtime_voice_action_tools

SALES = {"name": "Sales", "number": "+442071234567", "when": "The caller wants to buy."}
NUMBER = SALES["number"]


def raw(**extra):
    base = {"representative_name": "Alex", "brand": "Acme", "approved_next_actions": ["transfer"],
            "transfer_destinations": [SALES]}
    base.update(extra)
    return base


def norm(**extra):
    return normalize_campaign_brief(raw(**extra), company_name="Acme", agent_names=["Alex"])


def schema(**extra):
    return CampaignBriefInput(**raw(**extra))


def rejected_by_both(match, **extra):
    with pytest.raises(ValueError, match=f"(?i){match}"):
        norm(**extra)
    with pytest.raises(ValidationError, match=f"(?i){match}"):
        schema(**extra)


# ------------------------------------------------------------- brief fields
@pytest.mark.parametrize("seconds", [10, 30, 60])
@pytest.mark.parametrize("policy", ["return_to_agent", "end_call"])
def test_good_ring_settings_are_accepted_by_schema_and_normaliser(seconds, policy):
    brief = norm(transfer_ring_seconds=seconds, transfer_if_no_answer=policy)
    assert brief["transfer_ring_seconds"] == seconds
    assert brief["transfer_if_no_answer"] == policy
    model = schema(transfer_ring_seconds=seconds, transfer_if_no_answer=policy)
    assert model.transfer_ring_seconds == seconds and model.transfer_if_no_answer == policy


@pytest.mark.parametrize("seconds", [9, 61, 0, -5, 3600])
def test_ring_seconds_out_of_range_rejected(seconds):
    rejected_by_both("transfer_ring_seconds", transfer_ring_seconds=seconds)


@pytest.mark.parametrize("seconds", ["abc", 12.5, True, [30]])
def test_ring_seconds_must_be_an_integer(seconds):
    rejected_by_both("transfer_ring_seconds", transfer_ring_seconds=seconds)


@pytest.mark.parametrize("policy", ["hangup", "", "RETURN", 5])
def test_unknown_if_no_answer_rejected(policy):
    rejected_by_both("transfer_if_no_answer", transfer_if_no_answer=policy)


@pytest.mark.parametrize("extra", [{"transfer_ring_seconds": 20}, {"transfer_if_no_answer": "end_call"}])
def test_ring_settings_require_transfer_approved(extra):
    rejected_by_both("requires transfer", approved_next_actions=["end_call"],
                     transfer_destinations=None, **extra)


def test_brief_without_ring_settings_normalises_exactly_as_before():
    brief = norm()
    assert "transfer_ring_seconds" not in brief and "transfer_if_no_answer" not in brief
    one = norm(transfer_ring_seconds=20)
    assert "transfer_if_no_answer" not in one and one["transfer_ring_seconds"] == 20
    two = norm(transfer_if_no_answer="return_to_agent")
    assert "transfer_ring_seconds" not in two
    assert schema().transfer_ring_seconds is None and schema().transfer_if_no_answer is None


# --------------------------------------------------------- execution harness
class _FakeExecutor:
    def __init__(self, _pool):
        pass

    async def execute(self, *, action, executor, **_kw):
        result = dict(await executor())
        result["action"] = action
        result.setdefault("confirmation_allowed", False)
        return result


def _install(monkeypatch, brief, receipt):
    context = {"brief": brief, "call_direction": "outbound", "direction": "outbound",
               "call_status": "in_progress", "provider": "asterisk", "transfer_connected": True,
               "email_connected": False, "call_id": "c1", "provider_call_id": "p1",
               "campaign_id": "camp"}

    async def fake_context(_session, _pool):
        return context

    spy = AsyncMock(return_value=receipt)
    monkeypatch.setattr(action_execution, "_context", fake_context)
    monkeypatch.setattr(action_execution, "_call_ids", lambda _s: ("tenant", "camp", "c1"))
    monkeypatch.setattr(action_execution, "DurableActionExecutor", _FakeExecutor)
    monkeypatch.setattr(adapter_registry, "execute_transfer", spy)
    session = SimpleNamespace(turn_id=1, _voice_action_pool=object(), tenant_id="tenant",
                              campaign_id="camp", call_id="c1")
    return session, spy


CONFIRM = "Yes, transfer this call to Sales"


async def _run(monkeypatch, receipt, **brief_extra):
    session, spy = _install(monkeypatch, norm(**brief_extra), receipt)
    first = await run_voice_action(session, "transfer_call", {}, user_text="please")
    assert first["status"] == "needs_confirmation"
    session.turn_id = 2
    result = await run_voice_action(session, "transfer_call", {}, user_text=CONFIRM)
    return session, spy, result


def receipt(status, error=None, **extra):
    out = {"status": status, "call_id": "p1", "destination": NUMBER, "mode": "blind", **extra}
    if error:
        out["error"] = error
    return out


def failed(error, **extra):
    return receipt("failed", error, target_termination_confirmed=True,
                   caller_media_retained=True, **extra)


# ------------------------------------------------------- ring kwargs forwarded
@pytest.mark.asyncio
async def test_brief_without_settings_makes_todays_call(monkeypatch):
    _, spy, _ = await _run(monkeypatch, receipt("success"))
    args, kwargs = spy.await_args
    assert args == ("p1", NUMBER, "blind")
    assert set(kwargs) == {"idempotency_key", "actor_type", "actor_role"}
    assert "ring_seconds" not in kwargs and "if_no_answer" not in kwargs


@pytest.mark.asyncio
async def test_settings_are_forwarded_only_when_set(monkeypatch):
    _, spy, _ = await _run(monkeypatch, receipt("success"), transfer_ring_seconds=25,
                           transfer_if_no_answer="end_call")
    kwargs = spy.await_args.kwargs
    assert kwargs["ring_seconds"] == 25 and kwargs["if_no_answer"] == "end_call"
    assert spy.await_args.args == ("p1", NUMBER, "blind")
    _, spy, _ = await _run(monkeypatch, receipt("success"), transfer_ring_seconds=40)
    assert spy.await_args.kwargs["ring_seconds"] == 40
    assert "if_no_answer" not in spy.await_args.kwargs
    _, spy, _ = await _run(monkeypatch, receipt("success"), transfer_if_no_answer="return_to_agent")
    assert spy.await_args.kwargs["if_no_answer"] == "return_to_agent"
    assert "ring_seconds" not in spy.await_args.kwargs


# ------------------------------------------------------------------ outcomes
# (receipt, status, success, confirmation_allowed, text the message must contain)
STILL = "still with the caller"
OUTCOMES = {
    "completed": (receipt("completed", answered=True, handoff_confirmed=True),
                  "connected", True, True, "handed over"),
    "legacy_success": (receipt("success"), "provider_accepted", True, False,
                       "connection is unconfirmed"),
    "no_answer": (failed("no_answer"), "failed", False, False, "did not answer"),
    "noanswer": (failed("noanswer"), "failed", False, False, "did not answer"),
    "busy": (failed("busy"), "failed", False, False, "busy"),
    "chanunavail": (failed("chanunavail"), "failed", False, False, "could not be reached"),
    "congestion": (failed("congestion"), "failed", False, False, "could not be reached"),
    "cancel": (failed("cancel"), "failed", False, False, "could not be reached"),
    "outside_hours": (failed("transfer_closed"), "failed", False, False, "outside"),
    "no_line": (failed("outbound_trunk_unavailable"), "failed", False, False, "No line is available"),
    "cleanup_pending": (receipt("cleanup_pending", "target_termination_unconfirmed"),
                        "unknown", False, False, "not confirmed"),
    "failed_no_proof": (receipt("failed", "no_answer"), "unknown", False, False, "not confirmed"),
    "failed_closed_no_proof": (receipt("failed", "busy", caller_media_retained=False),
                               "unknown", False, False, "not confirmed"),
    "already_in_progress": (failed("transfer_already_in_progress"),
                            "unknown", False, False, "not confirmed"),
    "unknown_reason": (failed("something_new"), "failed", False, False,
                       "Transfer has not been confirmed."),
    "unknown_status": (receipt("weird"), "unknown", False, False,
                       "Transfer has not been confirmed."),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(OUTCOMES))
async def test_agent_is_told_the_real_outcome(monkeypatch, name):
    rec, status, success, allowed, text = OUTCOMES[name]
    _, _, result = await _run(monkeypatch, dict(rec))
    assert result["status"] == status
    assert result["success"] is success
    assert result["confirmation_allowed"] is allowed
    assert text in result["message"]
    assert not re.search(r"\+?\d{5,}", result["message"]), result["message"]
    assert NUMBER not in result["message"]
    if "connected" in result["message"].lower() and name != "legacy_success":
        # "unconfirmed" and "not connected" are the only places the word may appear.
        assert re.search(r"(?:un|not )\s*confirmed|not connected", result["message"].lower()) or \
            name == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["no_answer", "noanswer", "busy", "chanunavail", "congestion",
                                  "cancel", "outside_hours", "no_line"])
async def test_proved_failures_say_the_caller_is_still_there(monkeypatch, name):
    _, _, result = await _run(monkeypatch, dict(OUTCOMES[name][0]))
    assert STILL in result["message"]
    assert "connected" not in result["message"].lower()
    assert "another attempt" in result["message"]  # "do not promise another attempt"


@pytest.mark.asyncio
async def test_both_no_answer_spellings_give_the_same_message(monkeypatch):
    _, _, one = await _run(monkeypatch, dict(OUTCOMES["no_answer"][0]))
    _, _, two = await _run(monkeypatch, dict(OUTCOMES["noanswer"][0]))
    assert one["message"] == two["message"]


@pytest.mark.asyncio
async def test_completed_tells_the_agent_its_side_of_the_call_is_over(monkeypatch):
    _, _, result = await _run(monkeypatch, dict(OUTCOMES["completed"][0]))
    text = result["message"].lower()
    assert "answered" in text and "no longer" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["cleanup_pending", "failed_no_proof", "already_in_progress"])
async def test_unproved_states_never_invite_another_try(monkeypatch, name):
    _, _, result = await _run(monkeypatch, dict(OUTCOMES[name][0]))
    text = result["message"].lower()
    assert "did not complete" in text
    assert "do not try" in text and "again" in text  # explicit "do not try again"
    assert "still with the caller" not in text
    assert "offer another transfer" in text and "do not" in text  # the retry is forbidden, not invited


# --------------------------------------------------------------- speech guard
CONNECTED_CLAIMS = ["You are now connected to the team.", "I've transferred you.",
                    "I'm connecting you now."]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(OUTCOMES))
async def test_only_completed_lets_a_connected_sentence_through(monkeypatch, name):
    session, _, _ = await _run(monkeypatch, dict(OUTCOMES[name][0]))
    for sentence in CONNECTED_CLAIMS[:2]:
        spoken = guard_spoken_sentence(session, sentence)
        if name == "completed":
            assert spoken == sentence
        else:
            assert spoken in {UNBACKED_ACTION_LINE, UNRESOLVED_ACTION_LINE}


@pytest.mark.asyncio
async def test_before_any_result_the_short_line_passes_and_connected_does_not(monkeypatch):
    session, _ = _install(monkeypatch, norm(), receipt("success"))
    line = "One moment while I try them."
    assert guard_spoken_sentence(session, line) == line
    await run_voice_action(session, "transfer_call", {}, user_text="please")  # needs_confirmation
    assert guard_spoken_sentence(session, line) == line
    for sentence in CONNECTED_CLAIMS:
        assert guard_spoken_sentence(session, sentence) != sentence


@pytest.mark.asyncio
async def test_after_cleanup_pending_a_retry_line_is_not_spoken(monkeypatch):
    session, _, _ = await _run(monkeypatch, dict(OUTCOMES["cleanup_pending"][0]))
    assert guard_spoken_sentence(session, "Let me transfer you again.") == UNRESOLVED_ACTION_LINE


# ---------------------------------------------------------- tool announcement
def _cascaded_description(session):
    provider = SimpleNamespace(supports_tools=True, stream_chat_with_tools=lambda: None)
    tools = action_tools_for_turn([Message(role=MessageRole.USER, content="hi")], provider,
                                  session=session)
    return next(t["function"] for t in tools if t["function"]["name"] == "transfer_call")["description"]


def _tool_session():
    return SimpleNamespace(
        _voice_action_context={"brief": norm(), "call_direction": "outbound", "direction": "outbound"},
        _voice_action_capabilities={"transfer_call": "x"})


def test_tool_descriptions_ask_for_a_short_line_before_the_wait():
    session = _tool_session()
    cascaded = _cascaded_description(session).lower()
    realtime = next(t for t in realtime_voice_action_tools(session)
                    if t.get("name") == "transfer_call")["description"].lower()
    for text in (cascaded, realtime):
        assert "one moment while i try them" in text
        assert "answered" in text  # connected only once the result says so


# -------------------------------------------------------------------- realtime
@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["completed", "legacy_success", "no_answer", "busy", "cleanup_pending",
                                  "unknown_reason"])
async def test_realtime_model_sees_the_same_result(monkeypatch, name):
    rec, status, success, allowed, _ = OUTCOMES[name]
    session, spy = _install(monkeypatch, norm(), dict(rec))
    cascaded_first = await run_voice_action(session, "transfer_call", {}, user_text="please")
    assert cascaded_first["status"] == "needs_confirmation"
    session.turn_id = 2

    sent = []

    class _RT:
        async def update_live_state(self, _block):
            pass

        async def send_function_result(self, _call_id, output):
            sent.append(output)

    bridge = RealtimeBridge(call_id="rt", realtime_session=_RT(), media_gateway=object(),
                            action_session=session)
    bridge._latest_caller_text = CONFIRM
    await bridge._handle_function_call(SimpleNamespace(
        name="transfer_call", call_id="tool-1", parsed_arguments=lambda: {}))
    assert sent, "the realtime path sent no tool result"
    realtime_result = sent[0]

    # The cascaded path on a fresh session over the same receipt.
    cascaded_session, _ = _install(monkeypatch, norm(), dict(rec))
    await run_voice_action(cascaded_session, "transfer_call", {}, user_text="please")
    cascaded_session.turn_id = 2
    cascaded = await run_voice_action(cascaded_session, "transfer_call", {}, user_text=CONFIRM)
    for key in ("message", "status", "success", "confirmation_allowed"):
        assert realtime_result[key] == cascaded[key], key
    assert realtime_result["message"] and (status, success, allowed) == (
        realtime_result["status"], realtime_result["success"], realtime_result["confirmation_allowed"])
    assert spy.await_count == 1


# ------------------------------------------- pre-dial refusals, fallback_action
PRE_DIAL = {
    "outbound_trunk_unavailable": "No line is available for transfers right now.",
    "invalid_trunk_endpoint": "No line is available for transfers right now.",
    "invalid_caller_id": "No line is available for transfers right now.",
    "inbound_trunk_unavailable": "No line is available for transfers right now.",
    "inbound_trunk_identity_unavailable": "No line is available for transfers right now.",
    "invalid_transfer_destination": "A transfer is not available for this call.",
    "transfer_destination_not_allowed": "A transfer is not available for this call.",
    "transfer_closed": "The team is outside its hours for transfers.",
    "outbound_transfer_not_enabled": "A transfer is not available for this call.",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", sorted(PRE_DIAL))
async def test_pre_dial_refusal_without_flag_gives_the_real_reason(monkeypatch, error):
    _, _, result = await _run(monkeypatch, receipt("failed", error))  # no caller_media_retained
    assert result["status"] == "failed" and result["success"] is False
    assert result["confirmation_allowed"] is False
    assert result["message"].startswith(PRE_DIAL[error])
    assert STILL in result["message"]
    assert not re.search(r"\+?\d{5,}", result["message"])


@pytest.mark.asyncio
async def test_pre_dial_refusal_still_not_confirmed_when_termination_unproved(monkeypatch):
    _, _, result = await _run(monkeypatch, receipt("failed", "transfer_closed",
                                                   target_termination_confirmed=False))
    assert result["status"] == "unknown" and "not confirmed" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["no_answer", "busy", "chanunavail", "provider_error",
                                   "transfer_already_in_progress"])
async def test_post_dial_failure_without_flag_is_not_confirmed(monkeypatch, error):
    _, _, result = await _run(monkeypatch, receipt("failed", error))
    assert result["status"] == "unknown" and result["success"] is False
    assert "not confirmed" in result["message"] and STILL.lower() not in result["message"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["no_answer", "noanswer", "busy", "chanunavail", "congestion"])
async def test_end_call_fallback_changes_only_the_last_sentence(monkeypatch, name):
    _, _, base = await _run(monkeypatch, dict(OUTCOMES[name][0]))
    for value, expect_end in (("end_call", True), ("return_to_agent", False), (None, False)):
        rec = dict(OUTCOMES[name][0])
        if value:
            rec["fallback_action"] = value
        _, _, result = await _run(monkeypatch, rec)
        head = base["message"].split(" You are still with the caller.")[0]
        assert result["message"].startswith(head + " You are still with the caller.")
        assert result["status"] == "failed" and result["confirmation_allowed"] is False
        if expect_end:
            assert result["message"].endswith(
                " Tell the caller, then end the call politely; do not promise another attempt.")
            assert "message or arrange a callback" not in result["message"]
        else:
            assert result["message"] == base["message"]
        assert not re.search(r"\+?\d{5,}", result["message"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["cleanup_pending", "failed_no_proof", "unknown_reason", "unknown_status"])
async def test_fallback_action_does_not_change_unproved_or_unknown_messages(monkeypatch, name):
    _, _, base = await _run(monkeypatch, dict(OUTCOMES[name][0]))
    _, _, result = await _run(monkeypatch, {**OUTCOMES[name][0], "fallback_action": "end_call"})
    assert result["message"] == base["message"] and result["status"] == base["status"]


# ------------------------------------------- outbound switch off: not offered
def _offer_context(numbers, **extra):
    brief = {"approved_next_actions": ["transfer"], "transfer_destinations": [
        {"name": f"Team {index}", "number": number, "when": "x"}
        for index, number in enumerate(numbers)]}
    return {"brief": brief, "call_direction": "outbound", "direction": "outbound",
            "call_status": "in_progress", "provider": "asterisk", "transfer_connected": True,
            "email_connected": False, **extra}


def test_phone_number_is_not_offered_on_an_outbound_call_while_the_switch_is_off():
    context = _offer_context([NUMBER], outbound_pstn_transfer=False)
    assert action_execution.offered_transfer_destinations(context) == []
    assert "transfer_call" not in action_execution._capabilities(context)


def test_phone_number_is_offered_on_an_outbound_call_when_the_switch_is_on():
    context = _offer_context([NUMBER], outbound_pstn_transfer=True)
    assert [item["number"] for item in action_execution.offered_transfer_destinations(context)] == [NUMBER]
    assert "transfer_call" in action_execution._capabilities(context)


def test_switch_off_keeps_an_extension_and_drops_only_the_phone_number():
    context = _offer_context([NUMBER, "1001"], outbound_pstn_transfer=False)
    assert [item["number"] for item in action_execution.offered_transfer_destinations(context)] == ["1001"]
    assert "transfer_call" in action_execution._capabilities(context)


def test_a_context_without_the_switch_reading_offers_as_before():
    context = _offer_context([NUMBER])
    assert [item["number"] for item in action_execution.offered_transfer_destinations(context)] == [NUMBER]


def test_switch_reading_does_not_change_the_inbound_list():
    context = _offer_context([NUMBER], outbound_pstn_transfer=False)
    context.update(call_direction="inbound", direction="inbound", inbound_transfer_numbers={NUMBER})
    assert [item["number"] for item in action_execution.offered_transfer_destinations(context)] == [NUMBER]
