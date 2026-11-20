"""An opt-out or a call ending that was missed (inspector findings, 2026-11-20).

1. "Stop calling me right now" / "Take me off your list today" are opt-outs; the
   scheduling exception is only for "don't call me <future time>".
2. An opt-out held only as unprocessed text when the caller hangs up is recorded
   at teardown.
3. A first-turn opt-out heard with low confidence is not discarded by the turn-0
   floor.
4. The end_call tool accepts wrong-number wording and the voicemail flags.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.end_session_action import verified_opt_out
from app.domain.services.voice_pipeline.action_tools import run_voice_action
from app.domain.services.voice_pipeline.identity_disposition import contains_dnc

# --------------------------------------------------------------------------- 1

OPT_OUTS = [
    "Stop calling me right now.",
    "Stop calling me now",
    "Stop calling me today",
    "Stop calling me at work",
    "Take me off your list today",
    "Take me off your list right now",
    "Remove me from your list tonight",
    "Unsubscribe me right now",
    "Never call me again today",
    "I told you, stop calling me",
    "I said stop calling me",
    "I already asked you to stop calling me",
    "Please stop calling me at the moment",
]

NOT_OPT_OUTS = [
    "Don't call me tomorrow, call me next week",
    "Don't call me right now, I'm at work, call later",
    "Don't call me today",
    "Don't call me until Monday",
    "He said don't call me",
    "She told me they said stop calling",
    "What does take me off your list mean",
    "what time is it",
]


@pytest.mark.parametrize("text", OPT_OUTS)
def test_phrase_is_an_opt_out(text):
    assert contains_dnc(text) is True, text


@pytest.mark.parametrize("text", NOT_OPT_OUTS)
def test_phrase_is_not_an_opt_out(text):
    assert contains_dnc(text) is False, text


def test_model_quote_with_now_is_a_verified_opt_out():
    said = "Take me off your list right now."
    action = {"do_not_call": True, "opt_out_quote": "Take me off your list right now"}
    assert verified_opt_out(action, said) is True


def test_model_quote_for_a_paraphrase_with_now_is_verified():
    said = "Remove my number right now please"
    action = {"do_not_call": True, "opt_out_quote": "Remove my number right now"}
    assert verified_opt_out(action, said) is True


def test_model_quote_of_a_scheduling_request_is_still_refused():
    said = "Not now, call me tomorrow"
    action = {"do_not_call": True, "opt_out_quote": "Not now call me tomorrow"}
    assert verified_opt_out(action, said) is False
    said = "Don't call me today"
    action = {"do_not_call": True, "opt_out_quote": "Don't call me today"}
    assert verified_opt_out(action, said) is False


# --------------------------------------------------------------------------- 2

PBX_ID = "1790168826.2670-optout-unprocessed"


async def _teardown(monkeypatch, *, current_user_input, queued=None, campaign_id="camp-1"):
    from app.domain.services.dialer import opt_out
    from app.domain.services.telephony import lifecycle

    purge = AsyncMock(return_value={
        "dnc_written": True, "jobs_cancelled": 0, "lead_marked": True, "ok": True,
    })
    monkeypatch.setattr(opt_out, "purge_lead_on_opt_out_with_retry", purge)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object(), db_client=object()),
    )
    call_session = SimpleNamespace(
        call_id="cs-1", campaign_id=campaign_id,
        current_user_input=current_user_input, _queued_next_turn=queued,
    )
    voice = SimpleNamespace(
        _dialer_tenant_id="20000000-0000-4000-8000-000000000017",
        _dialer_lead_id="30000000-0000-4000-8000-000000000017",
        _dialer_phone="+15555550107", _dialer_call_id="10000000-0000-4000-8000-000000000017",
        call_session=call_session,
        pipeline=SimpleNamespace(cancel_active_turn=AsyncMock()),
    )
    state = SimpleNamespace(
        clear_first_speaker=lambda _: None, clear_ringing_started_at=lambda _: None,
        pop_ringing_warmup=lambda _: None, pop_voice_session=lambda _: voice,
        remove_gateway_sessions_for_call=lambda _: None,
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: state)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "_save_call_recording", AsyncMock())
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: SimpleNamespace(end_session=AsyncMock()))
    monkeypatch.setattr("app.domain.services.global_concurrency.release_lease", AsyncMock())
    monkeypatch.setattr("app.domain.services.call_status.record_call_state_by_provider_id", AsyncMock())
    try:
        await lifecycle._on_call_ended(PBX_ID, terminal_at_monotonic=100, acknowledge_ledger=False)
    finally:
        lifecycle._clear_ended_marker(PBX_ID, lifecycle._ended_call_marker_tokens.get(PBX_ID))
    return purge, call_session


@pytest.mark.asyncio
async def test_partial_opt_out_at_hangup_is_purged(monkeypatch):
    purge, cs = await _teardown(monkeypatch, current_user_input="Take me off your list")
    assert purge.await_count == 1
    assert cs._caller_opted_out is True


@pytest.mark.asyncio
async def test_queued_opt_out_at_hangup_is_purged(monkeypatch):
    purge, _ = await _teardown(
        monkeypatch, current_user_input="",
        queued={"text": "stop calling me", "latest_text": "stop calling me", "seq": 2},
    )
    assert purge.await_count == 1


@pytest.mark.asyncio
async def test_ordinary_partial_at_hangup_is_not_purged(monkeypatch):
    purge, cs = await _teardown(monkeypatch, current_user_input="what time is it")
    assert purge.await_count == 0
    assert getattr(cs, "_caller_opted_out", False) is False


@pytest.mark.asyncio
async def test_ask_ai_session_partial_is_not_purged(monkeypatch):
    purge, _ = await _teardown(
        monkeypatch, current_user_input="Take me off your list", campaign_id="ask-ai")
    assert purge.await_count == 0


# --------------------------------------------------------------------------- 3

@pytest.mark.asyncio
async def test_first_turn_opt_out_with_low_confidence_is_recorded():
    from tests.unit.test_voice_pipeline_service import (
        _make_service_for_disposition, _make_session,
    )

    service = _make_service_for_disposition(["Understood."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Stop calling me"
    session._last_transcript_confidence = 0.2
    assert not session.conversation_history

    await service.handle_turn_end(session, AsyncMock())

    assert getattr(session, "_caller_opted_out", False) is True


@pytest.mark.asyncio
async def test_first_turn_low_confidence_ordinary_speech_is_still_rejected():
    from tests.unit.test_voice_pipeline_service import (
        _make_service_for_disposition, _make_session,
    )

    service = _make_service_for_disposition(["Sure."])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "tell me about the price"
    session._last_transcript_confidence = 0.2

    await service.handle_turn_end(session, AsyncMock())

    assert getattr(session, "_caller_opted_out", False) is False
    assert any("didn't catch" in m.content for m in session.conversation_history)


# --------------------------------------------------------------------------- 4

def _live_session(**flags):
    return SimpleNamespace(conversation_history=[], **flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "You've got the wrong number.",
    "You have the wrong number",
    "wrong number",
    "There is nobody here by that name",
    "There's no Sarah here",
])
async def test_end_call_accepts_wrong_number_wording(text):
    session = _live_session()
    result = await run_voice_action(session, "end_call", {}, user_text=text)
    assert result["success"] is True, (text, result)
    assert session._end_call_requested is True


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["_amd_voicemail", "_machine_screening"])
async def test_end_call_accepts_voicemail_flags(flag):
    session = _live_session(**{flag: True})
    result = await run_voice_action(session, "end_call", {}, user_text="")
    assert result["success"] is True, result
    assert session._end_call_requested is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Tell me more about the price",
    "There is no problem with the price",
    "I think that is the wrong price",
])
async def test_end_call_still_refused_for_ordinary_speech(text):
    session = _live_session()
    result = await run_voice_action(session, "end_call", {}, user_text=text)
    assert result["success"] is False
    assert result["status"] == "caller_intent_unconfirmed"
    assert getattr(session, "_end_call_requested", False) is not True
