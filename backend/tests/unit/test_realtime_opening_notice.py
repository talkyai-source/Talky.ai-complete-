"""Realtime phone calls: the first sentence is checked before it plays and proven after.

Fake model events and a fake gateway through the real RealtimeBridge. The model's
transcript is the model's own account of its audio, so this proves the bridge logic,
not the live service.
"""
import asyncio
import logging
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import get_disclosure_state
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.voice_orchestrator import Direction
from app.domain.services.voice_pipeline.inbound_answer_greeting import says_it_is_ai
from app.realtime import opening_notice as on
from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import RealtimeEvent
from tests.unit.test_recording_disclosure import (
    _ANNOUNCE,
    _DISABLED,
    _NO_ANNOUNCE,
    _make_voice_session,
    _patched_policy,
)

TENANT = "22222222-2222-2222-2222-222222222222"
CALL = "call-opening-1234567890"
PLATFORM_OPENER = "Hi, this is Sarah, an AI assistant from Acme, on a recorded line. Am I speaking with John?"
PLAIN_OPENER = "Hi, this is Sarah, an AI assistant from Acme. Am I speaking with John?"
TRANSMITTED = lambda cid, uid: {"utterance_id": uid, "status": "transmitted", "evidence": "transmitted", "played_ms": 0}  # noqa: E731


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rps._DISCLOSURE_LEDGER.clear()
    monkeypatch.delenv(on.ENV_SWITCH, raising=False)
    yield
    rps._DISCLOSURE_LEDGER.clear()


def build(direction=Direction.OUTBOUND, greet=True, inbound=False, receipt=TRANSMITTED, opted_out=False,
          greeting=None, tenant=TENANT):
    sent = []

    async def send_audio(call_id, pcm):
        sent.append(pcm)

    gw = SimpleNamespace(
        send_audio=send_audio, begin_playback=AsyncMock(), flush_audio_buffer=AsyncMock(),
        finish_playback=AsyncMock(side_effect=receipt), send_control_event=AsyncMock(),
        set_recording_enabled=MagicMock())
    rt = SimpleNamespace(repair_unspoken_response=AsyncMock(), supports_response_metadata=False,
                         request_response=AsyncMock())
    agent_config = SimpleNamespace(agent_name="Sarah", company_name="Acme")
    call_session = SimpleNamespace(agent_config=agent_config, _caller_opted_out=opted_out, tenant_id=tenant,
                                   call_id=CALL)
    config = SimpleNamespace(direction=direction, agent_config=agent_config, callee_first_name="John",
                             tenant_id=tenant, realtime_opening_greeting=greeting)
    bridge = RealtimeBridge(call_id=CALL, realtime_session=rt, media_gateway=gw, action_session=call_session,
                            greet_on_start=greet)
    vs = SimpleNamespace(call_id=CALL, call_session=call_session, config=config, media_gateway=gw,
                         realtime_bridge=bridge, _dialer_tenant_id=tenant)
    if inbound:
        vs._inbound_admission = {"x": 1}
    return SimpleNamespace(bridge=bridge, rt=rt, gw=gw, sent=sent, vs=vs)


async def start(h, decision=_ANNOUNCE, greet=True, initialized=True):
    p_container, p_policy = _patched_policy(decision, initialized=initialized)
    with p_container, p_policy:
        gate = await on.decide_opening(h.vs, greet_on_start=greet)
    h.bridge.attach_opening_gate(gate)
    return gate


def cand(text):
    return RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320,
                         raw={"response": {"id": "r1", "output": []}})


async def feed(bridge, *texts):
    async def events():
        for text in texts:
            yield cand(text)
            if bridge._playback_task:
                await bridge._playback_task
    bridge._rt.events = events
    await bridge._pump_model_events()
    if bridge._playback_task:
        await bridge._playback_task


async def _sleep5(*a):
    await asyncio.sleep(5)


# 1
@pytest.mark.asyncio
async def test_agent_first_opener_carries_platform_notice_and_is_proven(caplog):
    h = build()
    order = []
    h.gw.set_recording_enabled.side_effect = lambda cid, on_: order.append(("gate", on_))
    h.gw.begin_playback.side_effect = lambda *a: order.append(("begin",))

    async def run():
        await feed(h.bridge, PLATFORM_OPENER)
    h.bridge.run = run
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with caplog.at_level(logging.INFO), p_container, p_policy:
        await on.run_realtime_bridge(h.vs)
    assert h.bridge._opening_gate.required_text == PLATFORM_OPENER == h.bridge._opening_line
    assert len(h.sent) == 1 and h.gw.begin_playback.await_count == 1
    assert get_disclosure_state(CALL) == "transmitted"
    assert order == [("gate", False), ("gate", True), ("begin",)]
    msgs = [r.getMessage() for r in caplog.records]
    assert any("realtime_opening_notice_wiring call_id=call-opening enabled=true" in m for m in msgs)
    assert any("realtime_opening_notice_delivered" in m and "evidence=transmitted" in m
               and "words=model_transcript ai_line=True" in m for m in msgs)


# 2
@pytest.mark.asyncio
async def test_tenant_notice_text_comes_first_in_the_same_utterance():
    h = build()
    tenant_text = "Calls with Acme are recorded for training."
    decision = _ANNOUNCE.__class__(**{**_ANNOUNCE.__dict__, "announcement_text": tenant_text})
    gate = await start(h, decision)
    assert gate.notice == "tenant_text_first"
    assert gate.required_text == f"{tenant_text} {PLAIN_OPENER}"
    await feed(h.bridge, gate.required_text)
    assert get_disclosure_state(CALL) == "transmitted" and len(h.sent) == 1


# 3
@pytest.mark.asyncio
async def test_wrong_first_reply_is_withheld_and_repaired_then_proven():
    h = build()
    gate = await start(h)
    await feed(h.bridge, "Hello there, how are you?", PLATFORM_OPENER)
    h.rt.repair_unspoken_response.assert_awaited_once()
    kw = h.rt.repair_unspoken_response.await_args.kwargs
    assert kw["required_text"] == PLATFORM_OPENER and kw["required_reason"]
    assert h.gw.begin_playback.await_count == 1 and len(h.sent) == 1
    assert gate.state == "delivered" and get_disclosure_state(CALL) == "transmitted"


# 4
@pytest.mark.asyncio
async def test_second_mismatch_plays_and_marks_unconfirmed():
    h = build()
    gate = await start(h)
    await feed(h.bridge, "Hello there.", "Hi again.")
    assert h.rt.repair_unspoken_response.await_count == 1
    assert len(h.sent) == 1
    assert gate.state == "unconfirmed" and gate.reason == "text_mismatch"
    assert get_disclosure_state(CALL) == "failed"
    assert h.bridge._repair_attempted is False


# 5
@pytest.mark.asyncio
async def test_barge_in_after_audio_started_is_unconfirmed_and_not_restarted():
    h = build()
    gate = await start(h)

    async def events():
        yield cand(PLATFORM_OPENER)
        for _ in range(100):
            if h.sent:
                break
            await asyncio.sleep(0)
        h.bridge._playback_task.cancel()
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.gw.finish_playback = AsyncMock(side_effect=_sleep5)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    assert gate.state == "unconfirmed" and gate.reason == "interrupted"
    assert get_disclosure_state(CALL) == "failed"
    h.rt.repair_unspoken_response.assert_not_awaited()


# 6
@pytest.mark.asyncio
async def test_early_hello_before_any_audio_keeps_the_opening_owed():
    h = build()
    gate = await start(h)
    gate_open = asyncio.Event()

    async def slow_begin(*a):
        await gate_open.wait()
    h.gw.begin_playback = AsyncMock(side_effect=slow_begin)

    async def events():
        yield cand(PLATFORM_OPENER)
        for _ in range(100):
            if h.gw.begin_playback.await_count:
                break
            await asyncio.sleep(0)
        h.bridge._playback_task.cancel()
        await asyncio.gather(h.bridge._playback_task, return_exceptions=True)
    h.bridge._rt.events = events
    await h.bridge._pump_model_events()
    assert h.sent == [] and gate.state == "pending"
    assert get_disclosure_state(CALL) is None
    h.gw.begin_playback = AsyncMock()
    await feed(h.bridge, PLATFORM_OPENER)
    assert gate.state == "delivered"


# 7
@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [
    lambda cid, uid: {"utterance_id": uid, "status": "unknown", "evidence": "unknown"},
    lambda cid, uid: {"utterance_id": uid, "status": "interrupted", "evidence": "transmitted"},
    lambda cid, uid: {"utterance_id": uid, "status": "failed", "evidence": "none"},
    lambda cid, uid: {"utterance_id": "someone-else", "status": "transmitted", "evidence": "transmitted"},
])
async def test_receipt_not_transmitted_is_unconfirmed(receipt):
    h = build(receipt=receipt)
    gate = await start(h)
    await feed(h.bridge, PLATFORM_OPENER)
    assert gate.state == "unconfirmed" and gate.reason.startswith("receipt_")
    assert get_disclosure_state(CALL) == "failed"


# 8
@pytest.mark.asyncio
@pytest.mark.parametrize("status,evidence,same_id", [
    ("transmitted", "transmitted", True), ("completed", "transport_played", True),
    ("completed", "transmitted", True), ("completed", "unknown", True),
    ("transmitted", "transport_played", True), ("unknown", "unknown", True),
    ("interrupted", "transmitted", True), ("failed", "none", True),
    ("transmitted", "transmitted", False), ("completed", "transport_played", False),
])
async def test_receipt_rule_matches_cascaded(status, evidence, same_id):
    class Pipe:
        async def synthesize_and_send_audio(self, session, text, websocket=None, **k):
            session._tts_delivery_status = "submitted"
            session._tts_playback_utterance_id = "u-1"
            session._tts_playback_receipt = {"utterance_id": "u-1" if same_id else "other",
                                             "status": status, "evidence": evidence}
            return False

        def clear_barge_in_event(self, session):
            pass
    vs, _ = _make_voice_session([])
    vs.pipeline = Pipe()
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await _speak_recording_disclosure(vs)
    cascaded = get_disclosure_state(vs.call_id)
    mine = on.receipt_evidence(
        {"utterance_id": "u-1" if same_id else "other", "status": status, "evidence": evidence}, "u-1")
    expected = {None: "failed", "transport_played": "spoken", "transmitted": "transmitted"}[mine]
    assert cascaded == expected


# 9
@pytest.mark.asyncio
async def test_no_notice_owed_still_gates_the_ai_line():
    h = build()
    gate = await start(h, _NO_ANNOUNCE)
    assert gate.notice == "not_required" and gate.required_text == PLAIN_OPENER
    assert "recorded line" not in gate.required_text
    assert get_disclosure_state(CALL) == "not_required"
    await feed(h.bridge, PLAIN_OPENER)
    assert gate.state == "delivered" and get_disclosure_state(CALL) == "not_required"
    h.gw.set_recording_enabled.assert_not_called()


# 10
@pytest.mark.asyncio
async def test_disabled_policy_closes_the_recording_gate_at_answer():
    h = build()
    gate = await start(h, _DISABLED)
    h.gw.set_recording_enabled.assert_called_once_with(CALL, False)
    assert h.vs._recording_refused_by_policy is True and h.vs._recording_policy_decided is True
    assert get_disclosure_state(CALL) == "not_required" and gate.required_text == PLAIN_OPENER


# 11
@pytest.mark.asyncio
@pytest.mark.parametrize("no_pool", [True, False])
async def test_policy_unavailable_marks_failed_and_keeps_ai_gate(no_pool):
    h = build(tenant=None if not no_pool else TENANT)
    gate = await start(h, initialized=not no_pool)
    assert gate.notice == "policy_unavailable" and gate.required_text == PLAIN_OPENER
    assert get_disclosure_state(CALL) == "failed"
    await feed(h.bridge, PLAIN_OPENER)
    assert gate.state == "delivered" and get_disclosure_state(CALL) == "failed"


# 12
@pytest.mark.asyncio
async def test_caller_first_outbound_first_reply_becomes_the_notice_opener():
    h = build(greet=False)
    gate = await start(h, greet=False)
    assert h.bridge._opening_line is None
    await feed(h.bridge, "Yes, hello, who is this?", PLATFORM_OPENER)
    assert h.rt.repair_unspoken_response.await_args.kwargs["required_text"] == PLATFORM_OPENER
    assert gate.state == "delivered" and get_disclosure_state(CALL) == "transmitted"
    h.rt.request_response.assert_not_awaited()


# 13
@pytest.mark.asyncio
async def test_inbound_agent_first_greeting_says_ai_and_writes_no_ledger():
    h = build(direction=Direction.INBOUND, greet=True, inbound=True, greeting="Welcome to Acme, how can I help?")
    gate = await start(h)
    assert says_it_is_ai(gate.required_text) and gate.notice == "not_recorded"
    assert h.bridge._opening_line == gate.required_text
    await feed(h.bridge, gate.required_text)
    assert gate.state == "delivered" and get_disclosure_state(CALL) is None
    h.gw.set_recording_enabled.assert_not_called()


# 14
@pytest.mark.asyncio
async def test_inbound_caller_first_identity_line_then_model_continues():
    h = build(direction=Direction.INBOUND, greet=False, inbound=True)
    gate = await start(h, greet=False)
    assert gate.required_text == "Thank you for calling Acme. This is Sarah, an AI assistant."
    await feed(h.bridge, gate.required_text)
    assert gate.state == "delivered" and get_disclosure_state(CALL) is None
    h.rt.request_response.assert_awaited_once()


# 15
@pytest.mark.asyncio
async def test_opt_out_releases_the_gate():
    h = build(opted_out=True)
    h.bridge._opt_out_acknowledged = True  # the existing opt-out gate has admitted the acknowledgement
    gate = await start(h)
    await feed(h.bridge, "Understood, I will not call again.")
    assert gate.state == "unconfirmed" and gate.reason == "opt_out"
    assert get_disclosure_state(CALL) == "failed"
    h.rt.repair_unspoken_response.assert_not_awaited()


# 16
@pytest.mark.asyncio
async def test_call_ending_before_opening_is_unconfirmed():
    h = build()
    h.bridge.run = AsyncMock()
    p_container, p_policy = _patched_policy(_ANNOUNCE)
    with p_container, p_policy:
        await on.run_realtime_bridge(h.vs)
    assert h.bridge._opening_gate.state == "unconfirmed"
    assert h.bridge._opening_gate.reason == "call_ended_before_opening"
    assert get_disclosure_state(CALL) == "failed"


# 17
@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "false", "No", "OFF"])
async def test_switch_off_runs_todays_bridge(monkeypatch, caplog, value):
    monkeypatch.setenv(on.ENV_SWITCH, value)
    h = build()
    before = h.bridge._opening_line
    h.bridge.run = AsyncMock()
    with caplog.at_level(logging.INFO):
        await on.run_realtime_bridge(h.vs)
    h.bridge.run.assert_awaited_once()
    assert h.bridge._opening_gate is None and h.bridge._opening_line == before
    assert get_disclosure_state(CALL) is None
    assert any("realtime_opening_notice_wiring" in r.getMessage() and "enabled=false" in r.getMessage()
               for r in caplog.records)
    await feed(h.bridge, "Anything at all.")
    assert len(h.sent) == 1


# 18
@pytest.mark.parametrize("heard,required,ok", [
    ("Hi, this is Sarah, an A.I. assistant from Acme & Sons.", "hi this is sarah an AI assistant from acme and sons", True),
    ("HI, THIS IS SARAH!", "Hi this is Sarah", True),
    ("Hi, this is Sarah’s line.", "Hi this is Sarah's line", True),
    ("Hi, this is Sarah, an AI assistant.", "Hi, this is Sarah, an AI assistant from Acme.", False),
    ("Hi, this is Sarah, an AI assistant from Acme. Okay?", "Hi, this is Sarah, an AI assistant from Acme.", False),
    ("", "Hi", False), ("Hi", "", False), (None, None, False),
])
def test_words_match_table(heard, required, ok):
    assert on.words_match(heard, required) is ok


# 19
def test_test_agent_bridge_has_no_gate():
    h = build()
    assert h.bridge._opening_gate is None
    import app.api.v1.endpoints.campaign_test_ws as ws
    import inspect
    assert "opening_notice" not in inspect.getsource(ws)


# Added on review, 23 Nov 2026.
@pytest.mark.asyncio
async def test_a_failure_while_deciding_never_leaves_the_call_without_an_agent(monkeypatch, caplog):
    h = build()
    h.bridge.run = AsyncMock()

    async def boom(*args, **kwargs):
        raise RuntimeError("policy lookup blew up before its own guard")

    monkeypatch.setattr(on, "decide_opening", boom)
    with caplog.at_level(logging.ERROR):
        await on.run_realtime_bridge(h.vs)
    h.bridge.run.assert_awaited_once()
    assert h.bridge._opening_gate is None
    assert "realtime_opening_notice_decide_failed" in caplog.text
    # nothing was marked delivered
    assert get_disclosure_state(CALL) not in {"spoken", "transmitted"}


@pytest.mark.asyncio
async def test_a_recording_gate_closed_by_the_accounts_policy_is_not_reopened_by_the_opening():
    h = build()
    gate = await start(h, _ANNOUNCE)
    assert gate.notice_owed is True
    h.vs._recording_refused_by_policy = True  # the account's policy says: do not record
    h.gw.set_recording_enabled.reset_mock()
    await feed(h.bridge, PLATFORM_OPENER)
    assert all(call.args != (CALL, True) for call in h.gw.set_recording_enabled.call_args_list)
    assert h.vs._recording_policy_decided is True
