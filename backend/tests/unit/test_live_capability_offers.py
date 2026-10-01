"""Observed model offers must respect actual routes and supplied resources."""
import pytest

from app.domain.models.conversation import MessageRole
from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.voice_pipeline.action_tools import action_tool_system_addendum, safe_failure_speech
from app.domain.services.voice_pipeline.grounded_links import ground_spoken_links, UNAVAILABLE_RESOURCE
from app.services.scripts.call_state_tracker import CallState, update_state_from_user_turn
from tests.unit.test_reply_grounding_guards import _spoken


@pytest.mark.parametrize("offer", [
    "I can connect you with a sales specialist who can give you the exact monthly cost.",
    "Would you like me to connect you with our sales team for pricing details?",
    "I'll transfer you to the sales team.",
])
def test_offers_need_a_real_transfer_capability(offer):
    guard = LLMGuardrails()
    assert guard.validate_response(offer, available_actions={"end_call"}) == (False, "unavailable_action:transfer_call")
    assert guard.validate_response(offer, available_actions={"end_call", "transfer_call"}) == (True, None)


def test_honest_unavailability_is_not_blocked():
    assert LLMGuardrails().validate_response("I can't connect you with sales from this call.", available_actions=set()) == (True, None)


@pytest.mark.parametrize("action", ["send_email", "schedule_callback", "submit_form", "transfer_call"])
def test_fixed_failure_speech_is_itself_honest(action):
    assert LLMGuardrails().validate_response(safe_failure_speech(action), action_results={}) == (True, None)


def test_curly_apostrophe_failure_and_later_positive_claim_are_separate():
    guard = LLMGuardrails()
    assert guard.validate_response("The email wasn’t sent.") == (True, None)
    assert guard.validate_response("I can’t confirm that the email was sent. I have sent it now.") == (False, "unconfirmed_action:send_email")


def test_download_offer_requires_a_supplied_resource_not_just_a_homepage():
    offer = "If you'd like, I can give you a link to download it from our website."
    for sources in ([], ["Visit https://example.test for company information."]):
        assert ground_spoken_links(offer, sources)[0] == UNAVAILABLE_RESOURCE
    facts = ["Download the brochure at https://example.test/brochure.pdf."]
    assert ground_spoken_links(offer, facts) == (offer, [])


@pytest.mark.parametrize("offer", [
    "I can give you a link to download it.",
    "I can give you a brochure link.",
])
def test_policy_download_words_cannot_turn_a_homepage_into_a_resource(offer):
    source = ["Company homepage: https://example.test.",
              "Offer a brochure or download only when its existence is confirmed."]
    assert ground_spoken_links(offer, source)[0] == UNAVAILABLE_RESOURCE


def test_capability_prompt_lists_actual_routes_and_does_not_invent_fallbacks():
    prompt = action_tool_system_addendum({"end_call"})
    assert "Available actions for this call: end_call." in prompt
    assert "unlisted transfer or team follow-up route is unavailable" in prompt
    assert "do not invent a fallback resource" in prompt


@pytest.mark.asyncio
async def test_actual_tts_boundary_cannot_reassert_records_after_customer_denial(monkeypatch):
    spoken = await _spoken([
        "I'm reaching out because we have an upgrade for existing terminal customers, and our records show you may qualify."
    ], monkeypatch, history=[(MessageRole.USER, "I am not your customer. Why are you calling?")])
    assert spoken == ["Thanks for correcting me. I won't assume you're a customer."]


def test_latest_caller_relationship_statement_wins_without_blocking_product_questions():
    from app.domain.models.conversation import Message
    from app.domain.services.voice_pipeline.conversation_guards import contradicted_customer_claim
    denied = [Message(role=MessageRole.USER, content="I'm not your customer.")]
    assert contradicted_customer_claim("Our records show you may qualify.", denied)
    assert contradicted_customer_claim("Would you like product information?", denied) is None
    assert contradicted_customer_claim("I can't confirm you're a customer.", denied) is None
    assert contradicted_customer_claim("I won't assume you're a customer.", denied) is None
    assert contradicted_customer_claim("If you're a customer, support can help.", denied) is None
    assert contradicted_customer_claim("Our records show you may qualify.", []) is None
    corrected = denied + [Message(role=MessageRole.USER, content="Actually, I am your customer.")]
    assert contradicted_customer_claim("You are an existing customer.", corrected) is None


@pytest.mark.asyncio
async def test_actual_tts_boundary_rewrites_unconfigured_transfer(monkeypatch):
    spoken = await _spoken([
        "I can't confirm the monthly price. Would you like me to connect you with our sales team?"
    ], monkeypatch, prompt="No approved price is available.")
    assert "connect you" not in " ".join(spoken)
    assert any("can't transfer" in line for line in spoken)


@pytest.mark.asyncio
async def test_actual_tts_boundary_rewrites_invented_download_offer(monkeypatch):
    spoken = await _spoken([
        "I couldn't send the brochure. If you'd like, I can give you a link to download it from our website."
    ], monkeypatch, prompt="Northwind sells payment terminals.")
    assert UNAVAILABLE_RESOURCE in spoken
    assert not any("I can give you a link" in line for line in spoken)


@pytest.mark.asyncio
async def test_actual_tts_boundary_respects_paused_contact_on_goodbye(monkeypatch):
    state = update_state_from_user_turn(CallState(), "My email is anna@example.com")
    state = update_state_from_user_turn(state, "Leave it unconfirmed.")
    spoken = await _spoken(["Have a good day."], monkeypatch, slots=state,
        history=[(MessageRole.USER, "Leave it unconfirmed.")])
    assert spoken == ["Have a good day."]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_output", ["", "[[END_CALL]]", '{"action":"end_call"}'])
async def test_empty_or_control_only_goodbye_never_asks_caller_to_repeat(monkeypatch, model_output):
    spoken = await _spoken([model_output], monkeypatch,
        history=[(MessageRole.USER, "No thanks. Goodbye.")])
    assert spoken == ["Goodbye."]
