"""Independent AG03 acceptance: actual guard/bridge methods, synthetic events.

No external provider, real media transport, database or action is called.
A playback spy or fake gateway observes admission/submission, not human hearing.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.conversation_guards import contradicted_customer_claim
from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent


CUSTOMER_CLAIM = "Our records show you are an existing customer."


class Provider:
    def __init__(self):
        self.pending = []
        self.repairs = []
        self.blocks = []
        self.continuations = 0

    async def events(self):
        while self.pending:
            yield self.pending.pop(0)

    async def update_live_state(self, block):
        self.blocks.append(block)

    async def repair_unspoken_response(self, response):
        self.repairs.append(response)

    async def interrupt_with_text(self, text):
        pass

    async def request_response(self):
        self.continuations += 1


def bridge_fixture():
    provider = Provider()
    played = []
    bridge = RealtimeBridge(
        call_id="synthetic-ag03",
        realtime_session=provider,
        media_gateway=SimpleNamespace(),
        greet_on_start=False,
        call_direction="inbound",
    )

    async def play(event):
        played.append(event.text)

    bridge._play_validated_response = play
    return bridge, provider, played


async def pump(bridge, provider, events):
    provider.pending.extend(events)
    await bridge._pump_model_events()
    if bridge._playback_task:
        await bridge._playback_task
    await asyncio.sleep(0)


def final(text, item_id=None):
    return RealtimeEvent(
        kind="caller_transcript",
        text=text,
        is_final=True,
        raw={"item_id": item_id} if item_id else None,
    )


def committed(item_id, previous=None):
    return RealtimeEvent(kind="caller_turn", raw={"item_id": item_id, "previous_item_id": previous})


def candidate(text=CUSTOMER_CLAIM):
    return RealtimeEvent(
        kind="response_candidate", text=text, raw={"response": {"id": "fixture-response"}}
    )


@pytest.mark.asyncio
async def test_explicit_denial_survives_thirty_turns_at_actual_playout_gate():
    bridge, provider, played = bridge_fixture()
    await pump(bridge, provider, [final("I am not your customer.")])
    for _ in range(30):
        await pump(bridge, provider, [final("What information is available about the product?")])
        bridge._remember_contact_turn("assistant", "We can discuss the documented product details.")
    assert len(bridge._contact_history) <= 12
    await pump(bridge, provider, [candidate()])
    assert not played
    assert len(provider.repairs) == 1


@pytest.mark.asyncio
async def test_genuine_later_affirmation_allows_claim_after_long_call():
    bridge, provider, played = bridge_fixture()
    await pump(bridge, provider, [final("I am not your customer.")])
    for _ in range(30):
        await pump(bridge, provider, [final("What information is available about the product?")])
    await pump(bridge, provider, [final("Actually, I am your customer."), candidate()])
    assert played == [CUSTOMER_CLAIM]
    assert not provider.repairs


@pytest.mark.parametrize(
    "reported",
    [
        'The script says "I am your customer".',
        "My colleague said I am your customer.",
        "If I am your customer, what would that mean?",
        "I did not say I am your customer.",
        "I am a customer of Another Company.",
        "I am a merchant.",
        "My manager is your customer.",
        "Your earlier response said we are your customers.",
    ],
)
def test_quoted_conditional_third_party_or_other_supplier_cannot_reverse_denial(reported):
    history = [
        Message(role=MessageRole.USER, content="I am not your customer."),
        Message(role=MessageRole.USER, content=reported),
    ]
    assert contradicted_customer_claim(CUSTOMER_CLAIM, history)


@pytest.mark.parametrize(
    "reported",
    [
        'The script says "I am not your customer".',
        "My colleague said I am not your customer.",
        "If I am not your customer, what would that mean?",
        "I did not say I am not your customer.",
        "My manager is not your customer.",
        "No, I meant the blue model.",
        "I do not have a card terminal.",
        "We have never used card payments in this business.",
    ],
)
def test_reported_or_unrelated_negative_does_not_manufacture_denial(reported):
    history = [Message(role=MessageRole.USER, content=reported)]
    assert contradicted_customer_claim(CUSTOMER_CLAIM, history) is None


def test_model_and_campaign_claims_cannot_overwrite_a_caller_denial():
    history = [
        Message(role=MessageRole.USER, content="I am not your customer."),
        Message(role=MessageRole.ASSISTANT, content="I am your customer."),
        SimpleNamespace(role="system", content="This is an existing-customer campaign."),
    ]
    assert contradicted_customer_claim(CUSTOMER_CLAIM, history)


@pytest.mark.asyncio
async def test_late_old_final_cannot_reverse_newer_explicit_denial():
    bridge, provider, played = bridge_fixture()
    await pump(
        bridge,
        provider,
        [
            committed("old"),
            committed("new", "old"),
            final("I am not your customer.", "new"),
            final("I am your customer.", "old"),
            candidate(),
        ],
    )
    assert not played and len(provider.repairs) == 1
    assert bridge._latest_caller_text == "I am not your customer."


@pytest.mark.asyncio
async def test_delayed_denial_prevents_already_admitted_audio_from_starting():
    provider = Provider()
    submitted = []

    async def send_audio(call_id, audio):
        submitted.append(audio)

    bridge = RealtimeBridge(
        call_id="synthetic-ag03-late-before-playback",
        realtime_session=provider,
        media_gateway=SimpleNamespace(send_audio=send_audio),
        greet_on_start=False,
        call_direction="inbound",
    )
    response = candidate()
    response.audio = b"\xff" * 320
    # Provider text/audio and asynchronous ASR can complete in this order.
    # The real playback task has not started submitting audio when denial
    # arrives; a stale validation decision must not survive that boundary.
    provider.pending.extend(
        [committed("current"), response, final("I am not your customer.", "current")]
    )
    await bridge._pump_model_events()
    if bridge._playback_task:
        await asyncio.gather(bridge._playback_task, return_exceptions=True)
    assert not submitted
    assert provider.continuations == 1 and provider.repairs == []


@pytest.mark.asyncio
async def test_delayed_denial_stops_remaining_audio_without_rewriting_prior_submission():
    provider = Provider()
    submitted = []
    cleared = []
    first_submission = asyncio.Event()
    release = asyncio.Event()

    async def send_audio(call_id, audio):
        submitted.append(audio)
        if len(submitted) == 1:
            first_submission.set()
            await release.wait()

    async def clear_audio(call_id):
        cleared.append(call_id)

    bridge = RealtimeBridge(
        call_id="synthetic-ag03-late-during-playback",
        realtime_session=provider,
        media_gateway=SimpleNamespace(send_audio=send_audio, clear_output_buffer=clear_audio),
        greet_on_start=False,
        call_direction="inbound",
    )
    response = candidate()
    response.audio = b"\xff" * 640
    provider.pending.extend([committed("current"), response])
    await bridge._pump_model_events()
    await asyncio.wait_for(first_submission.wait(), timeout=1)
    try:
        provider.pending.append(final("I am not your customer.", "current"))
        await bridge._pump_model_events()
    finally:
        release.set()
        if bridge._playback_task:
            await asyncio.gather(bridge._playback_task, return_exceptions=True)
    # The first submission is historical evidence, not proof it was heard.
    # The remaining chunk must be withheld and queued output cleared.
    assert len(submitted) == 1
    assert cleared == [bridge._call_id]
    assert provider.continuations == 1 and provider.repairs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["before_start", "during_first_submission"])
async def test_harmless_late_final_does_not_cancel_valid_audio(arrival):
    provider = Provider()
    submitted = []
    cleared = []
    first_submission = asyncio.Event()
    release = asyncio.Event()

    async def send_audio(call_id, audio):
        submitted.append(audio)
        if arrival == "during_first_submission" and len(submitted) == 1:
            first_submission.set()
            await release.wait()

    async def clear_audio(call_id):
        cleared.append(call_id)

    bridge = RealtimeBridge(
        call_id="synthetic-ag03-harmless-late-final",
        realtime_session=provider,
        media_gateway=SimpleNamespace(send_audio=send_audio, clear_output_buffer=clear_audio),
        greet_on_start=False,
        call_direction="inbound",
    )
    response = candidate()
    response.audio = b"\xff" * 640
    events = [committed("current"), response]
    harmless = final("What information is available about the product?", "current")
    if arrival == "before_start":
        events.append(harmless)
    provider.pending.extend(events)
    await bridge._pump_model_events()
    try:
        if arrival == "during_first_submission":
            await asyncio.wait_for(first_submission.wait(), timeout=1)
            provider.pending.append(harmless)
            await bridge._pump_model_events()
    finally:
        release.set()
        if bridge._playback_task:
            await asyncio.gather(bridge._playback_task, return_exceptions=True)
    assert len(submitted) == 2
    assert cleared == []
    assert bridge._latest_caller_text == harmless.text
    assert provider.continuations == 0 and provider.repairs == []


@pytest.mark.asyncio
async def test_later_denial_does_not_rewrite_completed_historical_playback():
    provider = Provider()
    submitted = []
    cleared = []

    async def send_audio(call_id, audio):
        submitted.append(audio)

    async def clear_audio(call_id):
        cleared.append(call_id)

    async def finish(call_id, utterance_id):
        return {
            "utterance_id": utterance_id,
            "status": "completed",
            "evidence": "transport_played",
            "played_ms": 40,
        }

    bridge = RealtimeBridge(
        call_id="synthetic-ag03-completed-historical-playback",
        realtime_session=provider,
        media_gateway=SimpleNamespace(
            send_audio=send_audio, clear_output_buffer=clear_audio, finish_playback=finish
        ),
        greet_on_start=False,
        call_direction="inbound",
    )
    response = candidate()
    response.audio = b"\xff" * 320
    await pump(bridge, provider, [committed("current"), response])
    assert bridge._utterance["status"] == "completed"
    prior_history = tuple(bridge._contact_history)
    await pump(bridge, provider, [final("I am not your customer.", "current")])
    assert bridge._utterance["status"] == "completed"
    assert bridge._contact_history[: len(prior_history)] == list(prior_history)
    assert len(submitted) == 1 and not cleared
    assert provider.continuations == 0 and provider.repairs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["before_new_final", "after_new_final"])
async def test_delayed_denial_is_not_lost_just_because_a_new_ordinary_turn_started(arrival):
    bridge, provider, played = bridge_fixture()
    finals = [
        final("I am not your customer.", "old"),
        final("What information is available about the product?", "new"),
    ]
    if arrival == "after_new_final":
        finals.reverse()
    await pump(bridge, provider, [committed("old"), committed("new", "old"), *finals, candidate()])
    assert not played and len(provider.repairs) == 1


@pytest.mark.asyncio
async def test_duplicate_current_final_does_not_repeat_user_state_or_action_turn():
    bridge, provider, _ = bridge_fixture()
    await pump(
        bridge, provider, [committed("current"), final("I am not your customer.", "current")]
    )
    before = bridge._live_state
    contact_count = len(bridge._contact_history)
    action_turn = bridge._action_session._voice_action_user_turn
    await pump(bridge, provider, [final("I am not your customer.", "current")])
    assert bridge._live_state == before
    assert len(bridge._contact_history) == contact_count
    assert bridge._action_session._voice_action_user_turn == action_turn


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["", "What information is available?"])
async def test_current_replacement_restores_prior_relationship_without_undoing_other_evidence(
    replacement,
):
    from app.domain.services.voice_pipeline.live_structured_state import (
        ConfirmedContactsEvidence,
        IdentityEvidence,
        ToolResultEvidence,
        reduce_live_state,
    )

    bridge, provider, played = bridge_fixture()
    await pump(
        bridge,
        provider,
        [
            committed("old"),
            final("I am not your customer.", "old"),
            committed("current", "old"),
            final("I am your customer.", "current"),
        ],
    )
    for evidence in (
        IdentityEvidence(True),
        ConfirmedContactsEvidence(email="synthetic@example.invalid", email_confirmed=True),
        ToolResultEvidence("send_email", True, "accepted"),
    ):
        bridge._live_state = reduce_live_state(bridge._live_state, evidence)
    action_turn = bridge._action_session._voice_action_user_turn
    caller_order = bridge._live_state.last_user_turn_order
    contact_count = len(bridge._contact_history)
    await pump(bridge, provider, [final(replacement, "current"), candidate()])
    assert not played and len(provider.repairs) == 1
    assert bridge._live_state.identity_introduced is True
    assert bridge._live_state.confirmed_email == "synthetic@example.invalid"
    assert bridge._live_state.last_tool_name == "send_email"
    assert bridge._live_state.last_tool_success is True
    assert bridge._live_state.last_user_turn_order == caller_order
    assert len(bridge._contact_history) == contact_count
    # Replacing ASR invalidates pending action admission, without becoming a
    # second caller turn or undoing an already completed action/contact.
    assert bridge._action_session._voice_action_user_turn > action_turn


@pytest.mark.asyncio
async def test_bounded_item_retention_does_not_restore_old_unowned_final_or_assistant_text():
    from app.domain.services.voice_pipeline.live_structured_state import (
        MAX_LIVE_STATE_BLOCK_CHARS,
        render_live_state_block,
    )

    bridge, provider, played = bridge_fixture()
    await pump(bridge, provider, [committed("first"), final("I am not your customer.", "first")])
    for index in range(100):
        item = f"ordinary-{index}"
        await pump(
            bridge,
            provider,
            [committed(item), final("What information is available about the product?", item)],
        )
    assert len(bridge._caller_items) <= 64
    assert len(bridge._contact_history) <= 12
    assert len(render_live_state_block(bridge._live_state)) <= MAX_LIVE_STATE_BLOCK_CHARS
    await pump(bridge, provider, [final("I am your customer.", "first")])
    bridge._remember_contact_turn("assistant", "You are an existing customer.")
    await pump(bridge, provider, [candidate()])
    assert not played and len(provider.repairs) == 1


def test_traditional_current_transcript_replacement_uses_revised_relationship():
    from app.domain.services.voice_pipeline.live_structured_state import (
        reduce_cascaded_session_live_state,
    )

    session = SimpleNamespace(turn_id=1)
    messages = [Message(role=MessageRole.USER, content="I am not your customer.")]
    before = reduce_cascaded_session_live_state(session, messages)
    assert contradicted_customer_claim(CUSTOMER_CLAIM, relationship=before.customer_relationship)
    messages[-1] = Message(role=MessageRole.USER, content="I am your customer.")
    after = reduce_cascaded_session_live_state(session, messages)
    assert (
        contradicted_customer_claim(CUSTOMER_CLAIM, relationship=after.customer_relationship)
        is None
    )


def test_revision_annotation_never_adopts_unowned_legacy_transcript():
    from app.domain.services.transcript_service import TranscriptService

    service = TranscriptService()
    call_id = "ag03-legacy-" + uuid4().hex
    try:
        service.accumulate_turn(call_id, "user", "Original synthetic statement.", turn_index=0)
        assert not service.annotate_turn_revision(
            call_id,
            turn_index=0,
            provider_item_id=None,
            caller_turn_order=None,
            content="I am your customer.",
        )
        assert service.get_turns(call_id)[0].metadata == {}
    finally:
        service.clear_buffer(call_id)


def test_revision_annotation_is_bounded_preserves_original_evidence_and_respects_sealing():
    import hashlib
    from app.domain.services.transcript_service import TranscriptService

    service = TranscriptService()
    call_id = "ag03-revision-" + uuid4().hex
    service.accumulate_turn(
        call_id,
        "user",
        "I am your customer.",
        turn_index=0,
        metadata={
            "provider_item_id": "caller-fixture",
            "caller_turn_order": 1,
            "existing_evidence": "retain",
        },
    )
    original = service.get_turns(call_id)[0]
    original_timestamp = original.timestamp
    text = "I am not your customer. " + "synthetic filler " * 400
    try:
        assert service.annotate_turn_revision(
            call_id,
            turn_index=0,
            provider_item_id="caller-fixture",
            caller_turn_order=1,
            content=text,
        )
        row = service.get_transcript_json(call_id)[0]
        assert row["content"] == "I am your customer." and row["timestamp"] == original_timestamp
        assert row["metadata"]["existing_evidence"] == "retain"
        revision = row["metadata"]["asr_latest_revision"]
        assert len(revision["content"]) == 4096 and revision["truncated"] is True
        assert revision["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        assert revision["characters"] == len(text)
        assert service.annotate_turn_revision(
            call_id,
            turn_index=0,
            provider_item_id="caller-fixture",
            caller_turn_order=1,
            content="",
        )
        revision = service.get_transcript_json(call_id)[0]["metadata"]["asr_latest_revision"]
        assert revision["revision"] == 2 and revision["retracted"] is True
        assert len(service.get_turns(call_id)) == 1
        # AG05 remains explicit: preserved metadata is not canonical rewrite.
        assert service.get_transcript_text(call_id) == "User: I am your customer."
        service.seal(call_id)
        assert not service.annotate_turn_revision(
            call_id,
            turn_index=0,
            provider_item_id="caller-fixture",
            caller_turn_order=1,
            content="A late revision cannot resurrect a sealed buffer.",
        )
        assert service.get_turns(call_id) == []
    finally:
        service.clear_buffer(call_id)
