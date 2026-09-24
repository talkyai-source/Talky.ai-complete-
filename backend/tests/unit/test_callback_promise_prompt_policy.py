"""The agent must never promise a callback/booking it cannot execute.

Production evidence (issues_all.txt: inbound-callback-promises-no-record,
VERDICT confirmed proven, call a5e033c7, 2026-09-23):

    Turn 14 (07:50:51.575) ASSISTANT: "I'll arrange a callback to confirm
        the appointment -- is that okay?"
    caller: "Okay. Perfect." (07:50:54.863)
    07:50:55.151512 WARNING llm_guardrails "Blocked unconfirmed voice action
        claim: schedule_callback"
    Turn 15 (07:50:59.433) ASSISTANT (forced retraction): "I can't schedule
        a callback from this call, but I can take the details for the team."

Root cause (issues_all.txt): action_tools.py has never had a live executor
for schedule_callback -- every campaign fails it closed -- yet nothing told
the model that BEFORE it spoke, so it promised a callback, the caller
agreed, and llm_guardrails.py (correctly) blocked the completion claim,
forcing an audible mid-call retraction. The retraction is a symptom of a
correct guardrail; the fix belongs upstream of it, in the prompt.

This module tests the prompt-layer fix (prompt_builder.py) and confirms it
is compatible with the existing enforcement layer (llm_guardrails.py),
which is unchanged -- its blocking behaviour is already covered by
test_voice_action_contract.py::test_guardrail_rejects_callback_confirmation_
without_tool_result and is not touched here.
"""
from __future__ import annotations

from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.voice_pipeline.action_tools import (
    ACTION_SCHEDULE_CALLBACK,
    safe_failure_speech,
)
from app.services.scripts.call_state_tracker import CallState, update_state_from_user_turn
from app.services.scripts.prompt_builder import compose_system_prompt

BASE = "You are a helpful receptionist."


def test_prompt_tells_the_model_not_to_promise_a_callback_by_default():
    """Default (no executor -- true for every campaign today, per
    action_tools.py's own module docstring): the policy line is always
    present, campaign-neutral, no campaign data touched."""
    out = compose_system_prompt(BASE, CallState())
    lowered = out.lower()
    assert "never say a callback or booking has been scheduled, booked, or confirmed" in lowered
    assert "pass the caller's details to the team" in lowered


def test_prompt_rule_survives_alongside_a_realistic_call_state():
    """Reproduces the a5e033c7 shape: name/phone/date already captured, the
    exact point at which the live agent made the unfulfillable promise."""
    state = update_state_from_user_turn(CallState(), "call me on +1 312 075 0496")
    state = update_state_from_user_turn(
        state,
        "yes",
        readback_issued=True,
        confirmation_verdict="affirm",
    )
    out = compose_system_prompt(BASE, state)
    assert "never say a callback or booking has been scheduled, booked, or confirmed" in out.lower()


def test_policy_still_allows_asking_for_and_noting_a_preferred_callback_time():
    """Round-2 reviewer finding (2026-09-24): the round-1 wording ('Never
    promise, schedule, or confirm a callback or booking yourself') reads as
    banning the agent from even asking for or noting a preferred callback
    day/time -- but inbound campaign 6cc54935's approved_next_actions
    includes schedule_callback, and prompt_builder's own CAPTURED block
    prints 'Follow-up time (already agreed): X' once one is captured. The
    policy must not contradict either: the agent may still ask for and note
    a preferred day/time, it just may never say a callback/booking is
    actually scheduled, booked, or confirmed."""
    out = compose_system_prompt(BASE, CallState())
    lowered = out.lower()
    assert "ask for and note" in lowered
    assert "callback day or time" in lowered


def test_policy_wording_does_not_itself_promise_a_call_back():
    """Round-2 reviewer finding (2026-09-24): 'so they can call back' is
    itself a promise that someone will call the caller back -- the same
    class of unfulfillable claim this whole policy exists to prevent. The
    honest framing is that the team will follow up, not that a call back is
    coming."""
    out = compose_system_prompt(BASE, CallState())
    lowered = out.lower()
    assert "so they can call back" not in lowered
    assert "so the team can follow up" in lowered


def test_policy_line_is_irrelevant_once_a_real_executor_exists():
    """Forward-compatible: the moment action_tools.py gets a live executor,
    passing has_callback_executor=True drops the now-unneeded line -- no
    campaign prompt edit required."""
    out = compose_system_prompt(BASE, CallState(), has_callback_executor=True)
    assert out == BASE
    assert "callback policy" not in out.lower()


def test_the_honest_fallback_line_the_policy_points_to_is_never_blocked():
    """Cross-check with the (unchanged) enforcement layer: the phrasing the
    new prompt rule steers the model toward -- action_tools.py's own
    SAFE_FAILURE_SPEECH, the honest "I'll pass your details to the team"
    line -- must never itself trip llm_guardrails.py's completion-claim gate,
    or the fix would just move the retraction earlier instead of removing it.
    """
    honest_line = safe_failure_speech(ACTION_SCHEDULE_CALLBACK)
    assert "team" in honest_line.lower()

    valid, reason = LLMGuardrails().validate_response(
        honest_line, None, action_results={}
    )
    assert valid is True
    assert reason is None


def test_the_completion_claim_a_promise_invites_is_what_the_guardrail_blocks():
    """Documents WHY the fix belongs in the prompt, not the guardrail.

    a5e033c7's turn 14 offer ("I'll arrange a callback... is that okay?")
    stayed in future/conditional tense and did not itself trip the
    completion-claim gate; it was the model's NEXT line, after the caller
    agreed, that tried to state the callback as done -- and that IS a
    completion claim the guardrail is right to block. Once the caller has
    agreed to an offer that was never going to be fulfilled, some later turn
    saying so is the only honest continuation, and the guardrail forces
    exactly that (the "I can't schedule a callback..." retraction turn 15
    actually spoke). So the only way to remove the retraction is to stop the
    model from making the unfulfillable offer in the first place -- which is
    what the new prompt rule does.
    """
    claim_after_the_caller_agreed = "Great, I've scheduled your callback."
    valid, reason = LLMGuardrails().validate_response(
        claim_after_the_caller_agreed, None, action_results={}
    )
    assert valid is False
    assert reason == f"unconfirmed_action:{ACTION_SCHEDULE_CALLBACK}"
