"""The caller's own latest statement about being a customer is saved at hangup."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.lead_capture_service import LeadCaptureService
from app.services.scripts import call_transcript_persister as persister


def _history(*lines):
    return [Message(role=MessageRole.USER, content=line) for line in lines]


class Recorder:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def capture(self, **kwargs):
        if self.fail:
            raise RuntimeError("synthetic write failure")
        self.calls.append(kwargs)
        return True


@pytest.fixture
def rig(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(LeadCaptureService, "capture", lambda self, **kw: recorder.capture(**kw))

    async def flush(*lines, is_test=False, fail=False):
        recorder.fail = fail
        call_session = SimpleNamespace(conversation_history=_history(*lines), captured_slots=None,
                                       _lead_capture_is_test=is_test, campaign_id=None, lead_id=None)
        voice = SimpleNamespace(call_session=call_session, _dialer_campaign_id=str(uuid4()),
                                _dialer_lead_id=str(uuid4()))
        await persister._flush_lead_details_on_hangup(voice_session=voice, dialer_call_id=str(uuid4()),
                                                      tenant_id_str=str(uuid4()), db_pool=object())
        return recorder.calls, call_session
    return flush


def _rows(calls):
    return [c for c in calls if c["field_key"] == "customer_relationship"]


@pytest.mark.asyncio
async def test_denial_is_saved_as_denied(rig):
    calls, _ = await rig("Hi", "I am not a customer of yours.")
    [row] = _rows(calls)
    assert row["value"] == "denied" and row["source"] == "caller_stated" and row["confirmed"] is False
    assert row["evidence"]["status"] == "caller_statement"


@pytest.mark.asyncio
async def test_affirmation_is_saved_as_affirmed(rig):
    calls, _ = await rig("I'm an existing customer.")
    assert [r["value"] for r in _rows(calls)] == ["affirmed"]


@pytest.mark.asyncio
async def test_denial_then_reversal_saves_affirmed(rig):
    calls, _ = await rig("I'm not a customer.", "Oh wait, actually I am a customer.")
    assert [r["value"] for r in _rows(calls)] == ["affirmed"]


@pytest.mark.asyncio
async def test_nothing_said_saves_no_row(rig):
    calls, _ = await rig("Hello", "Call me tomorrow.")
    assert _rows(calls) == []


@pytest.mark.asyncio
async def test_quoted_words_are_ignored_as_the_guard_ignores_them(rig):
    calls, _ = await rig('She said "I am a customer" on the phone.')
    assert _rows(calls) == []


@pytest.mark.asyncio
async def test_test_calls_write_nothing(rig):
    calls, _ = await rig("I am not a customer.", is_test=True)
    assert _rows(calls) == []


@pytest.mark.asyncio
async def test_a_failed_write_never_breaks_the_hangup_path(rig):
    calls, _ = await rig("I am not a customer.", fail=True)  # must not raise
    assert calls == []
