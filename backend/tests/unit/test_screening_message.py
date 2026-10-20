"""Screening endgame: leave ONE short callback message, then end the call.

Owner decision 2026-10-21: when a call-screening bot (armed earlier on the call)
says the person is unavailable, the agent speaks a one-line callback message via
the pipeline's normal end-of-call mechanism instead of hanging up silently.
Precision guards are unchanged: no prior screening bot -> nothing happens.
"""
import asyncio
from types import SimpleNamespace

from app.domain.services.voice_pipeline import machine_detection as md

SCREEN = "Hi, if you record your name and reason for calling, I'll see if this person is available."
UNAVAILABLE = (
    "I'm sorry, this person is not available. If you would like to leave an "
    "additional message, please reply after the tone."
)


class FakePipeline:
    def __init__(self):
        self.calls = []

    async def _shutdown_session_for_end_action(self, session, websocket, reason, farewell):
        self.calls.append((reason, farewell))


class FakeGateway:
    def __init__(self):
        self.hangups = []

    async def hangup_call(self, call_id, reason=""):
        self.hangups.append((call_id, reason))
        return True


def _session(agent="Sarah Jones", company="All State Estimation", reason=None):
    cfg = SimpleNamespace(agent_name=agent, company_name=company, call_reason=reason)
    return SimpleNamespace(
        call_id="c1", turn_id=2, direction="outbound", agent_config=cfg
    )


def _run(session, text, pipe, gw):
    return asyncio.run(
        md.handle_machine_interim(
            "c1", session, text, media_gateway=gw, pipeline=pipe, websocket=None
        )
    )


def test_message_spoken_then_call_ended_after_armed_screener():
    s, pipe, gw = _session(), FakePipeline(), FakeGateway()
    assert _run(s, SCREEN, pipe, gw) is False
    assert _run(s, UNAVAILABLE, pipe, gw) is True
    assert len(pipe.calls) == 1
    reason, msg = pipe.calls[0]
    assert "Sarah" in msg and "All State Estimation" in msg
    assert len(msg) < 200
    assert gw.hangups == []  # ended through the shared shutdown, not a silent hangup
    assert s._amd_voicemail is True


def test_reason_included_when_known():
    s = _session(reason="estimating support")
    pipe, gw = FakePipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    _run(s, UNAVAILABLE, pipe, gw)
    assert "estimating support" in pipe.calls[0][1]


def test_live_receptionist_no_prior_screener_no_message_no_hangup():
    s, pipe, gw = _session(), FakePipeline(), FakeGateway()
    assert _run(s, "Sorry, he's not available, can I take a message?", pipe, gw) is False
    assert _run(s, "He is not available right now, leave a message", pipe, gw) is False
    assert pipe.calls == [] and gw.hangups == []


def test_missing_names_fall_back_to_generic_line():
    s, pipe, gw = _session(agent="", company=None), FakePipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    assert _run(s, UNAVAILABLE, pipe, gw) is True
    msg = pipe.calls[0][1]
    assert msg == "We'll try again another time. Thank you, goodbye."


def test_no_agent_config_falls_back_to_generic_line():
    s = SimpleNamespace(call_id="c1", turn_id=2, direction="outbound")
    pipe, gw = FakePipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    _run(s, UNAVAILABLE, pipe, gw)
    assert pipe.calls[0][1] == "We'll try again another time. Thank you, goodbye."


def test_message_spoken_at_most_once():
    s, pipe, gw = _session(), FakePipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    for _ in range(3):
        assert _run(s, UNAVAILABLE, pipe, gw) is True
    assert len(pipe.calls) == 1
    assert gw.hangups == []


def test_failed_shutdown_falls_back_to_hangup():
    class Boom(FakePipeline):
        async def _shutdown_session_for_end_action(self, *a, **k):
            raise RuntimeError("tts down")

    s, pipe, gw = _session(), Boom(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    assert _run(s, UNAVAILABLE, pipe, gw) is True
    assert len(gw.hangups) == 1


def test_mailbox_menu_wording_stays_silent_hangup():
    s, pipe, gw = _session(), FakePipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    assert _run(s, "To listen to your message, press one", pipe, gw) is True
    assert pipe.calls == [] and len(gw.hangups) == 1


def test_a_reply_still_speaking_is_stopped_before_the_message():
    """Review 2026-10-21: the agent may still be saying "I'll hold..." when the
    screener comes back; that turn is cancelled first so the message does not
    overlap it."""
    order = []

    class OrderedPipeline(FakePipeline):
        async def cancel_active_turn(self, call_id):
            order.append(("cancel", call_id))

        async def _shutdown_session_for_end_action(self, session, websocket, reason, farewell):
            order.append(("message", reason))

    s, pipe, gw = _session(), OrderedPipeline(), FakeGateway()
    _run(s, SCREEN, pipe, gw)
    assert _run(s, UNAVAILABLE, pipe, gw) is True
    assert order == [("cancel", "c1"), ("message", "screening_message")]
