"""Caller relationship is durable state, never inferred from campaign or prose."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.conversation_guards import contradicted_customer_claim
from app.domain.services.voice_pipeline.live_structured_state import (
    LiveConversationState,
    evidence_from_transcript,
    reduce_live_state,
    reduce_cascaded_session_live_state,
    render_live_state_block,
)

CLAIM = "You are an existing customer."


def user(text):
    return SimpleNamespace(role="user", content=text)


@pytest.mark.parametrize("reported", [
    'You said "I am your customer", but that was wrong.',
    "He said I am your customer.",
    "The script says we are your customers.",
    "My friend says I am your customer.",
    "I am not saying I am your customer.",
    "It is not true that I am your customer.",
    "If I am your customer, what plan is it?",
    "I am a customer of AnotherCo.",
    "I am a customer support adviser.",
    "I'm a merchant.",
])
def test_reported_or_third_party_affirmation_cannot_cancel_direct_denial(reported):
    history = [user("I am not your customer."), user(reported)]
    assert contradicted_customer_claim(CLAIM, history)


@pytest.mark.parametrize("reported", [
    'The previous caller said "I am not your customer".',
    "He said I am not your customer.",
    "The script says we are not your customers.",
    "It is not true that I am not your customer.",
    "If I am not your customer, can you explain?",
    "I am not a customer of AnotherCo.",
    "I'm not a merchant.",
])
def test_quoted_reported_or_third_party_denial_never_creates_caller_relationship(reported):
    assert contradicted_customer_claim(CLAIM, [user(reported)]) is None


def test_existing_history_api_keeps_genuine_reversal_and_last_assertion():
    assert contradicted_customer_claim(CLAIM, [user("I am not your customer."), user("Actually, we are your customers.")]) is None
    assert contradicted_customer_claim(CLAIM, [user("I am your customer, but actually I am not your customer.")])


def test_relationship_denial_survives_thirty_ordinary_caller_turns():
    state = LiveConversationState()
    for order, text in enumerate(["I am not your customer."] + ["What are your opening hours?"] * 30, 1):
        event = evidence_from_transcript(role="user", text=text, turn_id=f"caller:{order}")
        state = reduce_live_state(state, event)
    position = getattr(state, "customer_relationship", None)
    assert getattr(position, "value", position) == "denied"
    assert state.relationship_turn_id == "caller:1"
    assert "customer_relationship=denied" in render_live_state_block(state)


def test_assistant_claims_do_not_become_relationship_evidence():
    assert evidence_from_transcript(role="assistant", text=CLAIM, turn_id="model:1") is None


def test_relationship_order_prevents_delayed_duplicate_from_overriding_newer_denial():
    state = LiveConversationState()
    for order, text in [(1, "I am your customer."), (3, "I am not your customer."), (1, "I am your customer.")]:
        event = evidence_from_transcript(role="user", text=text, turn_id=f"item:{order}", caller_turn_order=order)
        state = reduce_live_state(state, event)
    assert state.customer_relationship.value == "denied"
    assert state.relationship_turn_order == 3
    assert state.last_user_turn_order == 3


def test_current_item_replacement_can_restore_prior_denial_without_resetting_other_state():
    denied = reduce_live_state(LiveConversationState(), evidence_from_transcript(
        role="user", text="I am not your customer.", turn_id="item:1", caller_turn_order=1))
    affirmed = reduce_live_state(denied, evidence_from_transcript(
        role="user", text="I am your customer.", turn_id="item:2", caller_turn_order=2))
    # Bridge owns the single pre-current-item snapshot and merges only these
    # relationship fields after a corrected transcript replaces its assertion.
    current = replace(affirmed, confirmed_email="confirmed@example.invalid")
    revised = reduce_live_state(denied, evidence_from_transcript(
        role="user", text="Tell me the opening hours.", turn_id="item:2", caller_turn_order=2))
    merged = replace(current, customer_relationship=revised.customer_relationship,
                     relationship_turn_id=revised.relationship_turn_id,
                     relationship_turn_order=revised.relationship_turn_order)
    assert merged.customer_relationship.value == "denied"
    assert merged.relationship_turn_id == "item:1"
    assert merged.confirmed_email == "confirmed@example.invalid"


def test_delayed_first_final_keeps_relationship_but_never_replays_old_action():
    state = reduce_live_state(LiveConversationState(), evidence_from_transcript(
        role="user", text="What are the opening hours?", turn_id="item:2", caller_turn_order=2))
    revised = reduce_live_state(state, evidence_from_transcript(
        role="user", text="I am not your customer. Email me.", turn_id="item:1", caller_turn_order=1))
    assert revised.customer_relationship.value == "denied"
    assert revised.relationship_turn_order == 1
    assert revised.last_user_turn_id == "item:2"
    assert revised.last_user_turn_order == 2
    assert revised.requested_next_action == state.requested_next_action


def test_initial_traditional_history_bootstraps_relationship_once_then_survives_pruning():
    session = SimpleNamespace(turn_id=1)
    history = [user("I am not your customer."), user("What are the opening hours?")]
    state = reduce_cascaded_session_live_state(session, history)
    assert state.customer_relationship.value == "denied"
    assert state.relationship_turn_id == "history:1"
    session.turn_id = 2
    state = reduce_cascaded_session_live_state(session, [user("Tell me the product details.")])
    assert state.customer_relationship.value == "denied"


@pytest.mark.parametrize("prefix", ["Actually, ", "To be clear, ", "Well, ", "Listen, ", "Look, "])
def test_normal_direct_correction_prefixes_are_not_discarded(prefix):
    history = [user("I am your customer."), user(prefix + "I am not your customer.")]
    assert contradicted_customer_claim(CLAIM, history)


def test_explicit_unknown_state_does_not_reimport_retracted_history():
    from app.domain.services.voice_pipeline.conversation_guards import CustomerRelationship
    assert contradicted_customer_claim(CLAIM, [user("I am not your customer.")],
                                       relationship=CustomerRelationship.UNKNOWN) is None


@pytest.mark.parametrize("affirmation", ["I am a customer of yours.", "I am your merchant."])
def test_direct_company_relationship_can_genuinely_reverse_denial(affirmation):
    assert contradicted_customer_claim(CLAIM, [user("I am not your customer."), user(affirmation)]) is None


def test_reason_after_direct_correction_does_not_remove_the_assertion():
    assert contradicted_customer_claim(CLAIM, [user("I am not your customer because you called the wrong business.")])


def test_first_traditional_denial_retracted_to_neutral_restores_unknown():
    session = SimpleNamespace(turn_id=1)
    reduce_cascaded_session_live_state(session, [user("I am not your customer.")])
    revised = reduce_cascaded_session_live_state(session, [user("What are your opening hours?")])
    assert revised.customer_relationship.value == "unknown"
    assert revised.relationship_turn_id is None


def test_traditional_affirmation_retraction_restores_earlier_denial_without_replaying_other_fields():
    from app.domain.services.voice_pipeline.live_structured_state import ToolResultEvidence

    session = SimpleNamespace(turn_id=1)
    reduce_cascaded_session_live_state(session, [user("I am not your customer.")])
    session.turn_id = 2
    first = reduce_cascaded_session_live_state(session, [user("I am your customer.")])
    assert first.customer_relationship.value == "affirmed"
    session._live_structured_state = reduce_live_state(first, ToolResultEvidence("send_email", True, "accepted"))
    revised = reduce_cascaded_session_live_state(session, [user("Please email me the details.")])
    assert revised.customer_relationship.value == "denied"
    assert revised.last_tool_name == "send_email"
    assert revised.last_tool_success is True
    assert revised.requested_next_action == first.requested_next_action
    assert revised.refusal_count == first.refusal_count


def test_explicit_current_user_override_bootstraps_only_earlier_history():
    session = SimpleNamespace(turn_id=1)
    historical = [user("I am not your customer.")]
    state = reduce_cascaded_session_live_state(session, historical, user_text="Actually, I am your customer.")
    assert state.customer_relationship.value == "affirmed"
    revised = reduce_cascaded_session_live_state(session, historical, user_text="Tell me the product details.")
    assert revised.customer_relationship.value == "denied"


@pytest.mark.asyncio
async def test_owned_caller_turn_orderuence_distinguishes_barge_in_before_session_counter_advances():
    session = SimpleNamespace(turn_id=1)
    task = asyncio.current_task()
    sentinel = object()
    previous = getattr(task, "_caller_turn_order", sentinel)
    try:
        task._caller_turn_order = 1
        reduce_cascaded_session_live_state(session, [user("I am not your customer.")])
        task._caller_turn_order = 2
        newer = reduce_cascaded_session_live_state(session, [user("What are your opening hours?")])
        assert newer.customer_relationship.value == "denied"
        revised = reduce_cascaded_session_live_state(session, [user("Actually, I am your customer.")])
        assert revised.customer_relationship.value == "affirmed"
    finally:
        if previous is sentinel:
            delattr(task, "_caller_turn_order")
        else:
            task._caller_turn_order = previous


def test_relationship_line_fits_existing_prompt_budget_without_dropping_other_facts():
    from app.domain.services.voice_pipeline.live_structured_state import (
        CustomerRelationship, DecisionMakerStatus, InterestLevel, PainPriority,
        RequestedNextAction, SalesStage, MAX_LIVE_STATE_BLOCK_CHARS,
    )

    state = LiveConversationState(
        identity_introduced=True, decision_maker=DecisionMakerStatus.SHARED,
        current_provider="A" * 48, pain_priority=PainPriority.RELIABILITY,
        interest_level=InterestLevel.MEDIUM, refusal_count=1000,
        requested_next_action=RequestedNextAction.MORE_INFORMATION,
        confirmed_email="a" * 115 + "@example.test", confirmed_phone="1" * 32,
        last_tool_name="a" * 48, last_tool_success=False, last_tool_code="x" * 32,
        sales_stage=SalesStage.QUALIFICATION,
        customer_relationship=CustomerRelationship.AFFIRMED,
    )
    block = render_live_state_block(state)
    assert len(block) <= MAX_LIVE_STATE_BLOCK_CHARS == 768
    assert state.confirmed_email in block
    assert state.current_provider in block
    assert state.last_tool_code in block
    assert "customer_relationship=affirmed" in block


@pytest.mark.asyncio
async def test_older_owned_traditional_task_cannot_reverse_newer_relationship_or_replace_current_baseline():
    session = SimpleNamespace(turn_id=1)
    task = asyncio.current_task()
    sentinel = object()
    previous = getattr(task, "_caller_turn_order", sentinel)
    try:
        task._caller_turn_order = 1
        reduce_cascaded_session_live_state(session, [user("I am not your customer.")])
        task._caller_turn_order = 3
        state = reduce_cascaded_session_live_state(session, [user("Actually, I am your customer.")])
        assert state.customer_relationship.value == "affirmed"
        task._caller_turn_order = 2
        state = reduce_cascaded_session_live_state(session, [user("I am not your customer.")])
        assert state.customer_relationship.value == "affirmed"
        assert state.relationship_turn_order == 3
        task._caller_turn_order = 3
        revised = reduce_cascaded_session_live_state(session, [user("What are the opening hours?")])
        assert revised.customer_relationship.value == "denied"
        # The delayed first final at order2 is now the newest valid evidence
        # preceding current order3, even though it could not overwrite order3.
        assert revised.relationship_turn_order == 2
    finally:
        if previous is sentinel:
            delattr(task, "_caller_turn_order")
        else:
            task._caller_turn_order = previous


@pytest.mark.asyncio
async def test_late_first_traditional_assertion_updates_current_replacement_baseline_only():
    session = SimpleNamespace(turn_id=1)
    task = asyncio.current_task()
    sentinel = object()
    previous = getattr(task, "_caller_turn_order", sentinel)
    try:
        task._caller_turn_order = 3
        reduce_cascaded_session_live_state(session, [user("What are the opening hours?")])
        task._caller_turn_order = 2
        delayed = reduce_cascaded_session_live_state(session, [user("I am not your customer. Email me.")])
        assert delayed.customer_relationship.value == "denied"
        assert delayed.relationship_turn_order == 2
        assert delayed.last_user_turn_order == 3
        assert delayed.requested_next_action.value == "unknown"
        task._caller_turn_order = 3
        revised = reduce_cascaded_session_live_state(session, [user("Tell me the product details.")])
        assert revised.customer_relationship.value == "denied"
        assert revised.relationship_turn_order == 2
    finally:
        if previous is sentinel:
            delattr(task, "_caller_turn_order")
        else:
            task._caller_turn_order = previous
