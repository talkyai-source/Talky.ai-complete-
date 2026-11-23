"""Realtime phone calls: a reply the phone gateway transmitted counts as delivered.

A phone gateway can only say status "transmitted", evidence "transmitted", played_ms 0.
The ordinary reply must follow the same rule as the opening sentence
(opening_notice.receipt_evidence), except that a bare "yes" may authorise an action
only after real playout (evidence "transport_played").
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.realtime import opening_notice as on
from tests.unit.test_bh_realtime_late_final import _events, _final, _fixture, _response, _start
from tests.unit.test_realtime_opening_notice import TRANSMITTED, _sleep5, build, cand, feed

REPLY = "Would you like a free quote today?"
PLAYED = lambda cid, uid: {"utterance_id": uid, "status": "completed", "evidence": "transport_played", "played_ms": 900}  # noqa: E731
BAD = [
    lambda cid, uid: {"utterance_id": uid, "status": "unknown", "evidence": "unknown"},
    lambda cid, uid: {"utterance_id": uid, "status": "interrupted", "evidence": "transmitted"},
    lambda cid, uid: {"utterance_id": uid, "status": "failed", "evidence": "none"},
    lambda cid, uid: {"utterance_id": "someone-else", "status": "transmitted", "evidence": "transmitted"},
]
GW_TRANSMITTED = {"utterance_id": "rt-1", "status": "transmitted", "evidence": "transmitted", "played_ms": 0}


def _phone(gateway):
    gateway.finish_playback = AsyncMock(return_value=dict(GW_TRANSMITTED))


async def _settled(bridge, provider, *events):
    """Like _events, but lets each reply finish playing before the next event (a caller who
    speaks after the agent has finished)."""
    async def stream():
        for event in events:
            yield event
            if bridge._playback_task:
                await asyncio.gather(bridge._playback_task, return_exceptions=True)
    provider.events = stream
    await bridge._pump_model_events()
    if bridge._playback_task:
        await asyncio.gather(bridge._playback_task, return_exceptions=True)


def _last_agent_line(bridge):
    return next((m.content for m in reversed(bridge._contact_history)
                 if getattr(m.role, "value", m.role) == "assistant"), "")


# T1
@pytest.mark.asyncio
async def test_phone_transmitted_reply_counts_as_delivered():
    h = build()
    await feed(h.bridge, REPLY)
    assert h.bridge._utterance["status"] == "completed"
    assert _last_agent_line(h.bridge) == REPLY
    assert h.bridge._identity_opening_pending is False


# T2
@pytest.mark.asyncio
async def test_transmitted_reply_never_authorizes_a_bare_yes():
    h = build()
    await feed(h.bridge, REPLY)
    assert h.bridge._action_session._voice_action_delivered_text == ""
    h = build(receipt=PLAYED)
    await feed(h.bridge, REPLY)
    assert h.bridge._action_session._voice_action_delivered_text == REPLY


# T3
@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", BAD)
async def test_unproven_receipt_is_not_delivered(receipt):
    h = build(receipt=receipt)
    await feed(h.bridge, REPLY)
    assert h.bridge._utterance["status"] != "completed"
    assert _last_agent_line(h.bridge) == ""
    assert h.bridge._identity_opening_pending is True
    assert h.bridge._action_session._voice_action_delivered_text == ""


# T4
@pytest.mark.asyncio
async def test_barge_in_during_phone_reply_is_not_delivered():
    h = build()
    h.gw.finish_playback = AsyncMock(side_effect=_sleep5)

    async def events():
        yield cand(REPLY)
        for _ in range(100):
            if h.sent:
                break
            await asyncio.sleep(0)
        h.bridge._playback_task.cancel()
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    assert h.bridge._utterance["status"] == "interrupted"
    assert _last_agent_line(h.bridge) == ""


# T5
@pytest.mark.asyncio
@pytest.mark.parametrize("status,evidence,same_id", [
    ("transmitted", "transmitted", True), ("completed", "transport_played", True),
    ("completed", "transmitted", True), ("completed", "unknown", True),
    ("transmitted", "transport_played", True), ("unknown", "unknown", True),
    ("interrupted", "transmitted", True), ("failed", "none", True),
    ("transmitted", "transmitted", False), ("completed", "transport_played", False),
])
async def test_ordinary_rule_equals_opening_rule(status, evidence, same_id):
    def receipt(cid, uid):
        return {"utterance_id": uid if same_id else "other", "status": status, "evidence": evidence}
    h = build(receipt=receipt)
    await feed(h.bridge, REPLY)
    expected = on.receipt_evidence(receipt(None, h.bridge._utterance["id"]), h.bridge._utterance["id"]) is not None
    assert (_last_agent_line(h.bridge) == REPLY) is expected


# T6
@pytest.mark.asyncio
async def test_caller_turn_after_transmitted_reply_does_not_truncate_it():
    bridge, provider, gateway, _, _ = _fixture()
    _phone(gateway)
    bridge._identity_opening_pending = False
    await _settled(bridge, provider, _response(REPLY), _start("u1", 0))
    provider.truncate_response.assert_not_awaited()
    await bridge.stop()


# T7
@pytest.mark.asyncio
async def test_goodbye_transmitted_before_late_final_closes_without_15s():
    bridge, provider, gateway, ended, _ = _fixture()
    _phone(gateway)
    await _settled(bridge, provider, _start("u1", 0), _response("Goodbye."),
                  _final("u1", "Goodbye. Please end this call."))
    assert bridge._termination_task is not None
    await asyncio.wait_for(bridge._termination_task, 1)
    ended.assert_awaited_once()
    provider.close.assert_awaited_once()


# T8
@pytest.mark.asyncio
async def test_transmitted_opening_not_reported_interrupted():
    bridge, provider, gateway, _, _ = _fixture()
    _phone(gateway)
    bridge._identity_opening_pending = True
    await _settled(bridge, provider, _response("Hi, this is Sarah, an AI assistant from Acme."), _start("u1", 0))
    assert bridge._identity_opening_pending is False
    assert bridge._opening_interrupted is False
    await bridge.stop()


# T9
@pytest.mark.asyncio
async def test_soft_decline_scoped_to_transmitted_question():
    bridge, provider, gateway, ended, _ = _fixture()
    _phone(gateway)
    await _settled(bridge, provider, _response(REPLY))
    # The caller starts speaking: the boundary is taken, then "No thanks." arrives.
    bridge._snapshot_caller_question()
    bridge._latest_caller_text = "No thanks."
    assert bridge._arm_caller_end_call() is False
    assert bridge._termination_task is None
    ended.assert_not_awaited()
    await bridge.stop()


# T10
@pytest.mark.asyncio
async def test_a_transmitted_reply_after_a_played_one_cannot_be_authorised_by_a_bare_yes():
    played = "I will book Tuesday at ten. Shall I confirm that?"
    h = build(receipt=PLAYED)
    await feed(h.bridge, played)
    assert h.bridge._action_session._voice_action_delivered_text == played
    h.gw.finish_playback = AsyncMock(side_effect=TRANSMITTED)
    await feed(h.bridge, "Is there anything else I can help with?")
    assert h.bridge._utterance["status"] == "completed"
    assert h.bridge._action_session._voice_action_delivered_text == ""


# Added after the independent review.
@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [
    lambda cid, uid: {"utterance_id": uid, "status": "completed", "evidence": "unknown", "played_ms": 0},
    lambda cid, uid: {"utterance_id": "rt-other", "status": "completed", "evidence": "transport_played", "played_ms": 900},
])
async def test_a_receipt_that_says_completed_without_proof_is_not_completed(receipt):
    """A status word alone is not proof: no evidence, or another reply's id."""
    h = build(receipt=receipt)
    await feed(h.bridge, REPLY)
    assert h.bridge._utterance["status"] == "unknown"
    assert _last_agent_line(h.bridge) == ""
    assert h.bridge._identity_opening_pending is True
