"""Every outbound call opens with who is calling and that it is an AI (QA F12-03).

QA found outbound calls opening with a bare "Hello?" or "Hi there." and the
disclosure left to the model. Owner decision 2026-11-05: the first line is
"Hi, this is Sarah, an AI assistant from All State Estimation, on a recorded
line. Am I speaking with John?" -- with ", on a recorded line" only where it
stands in for the platform's own recording notice, and "Who am I speaking
with?" when no name is on file. A tenant's own notice wording is still
spoken as its own line.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services import recording_policy_service as rps
from app.domain.services.recording_policy_service import (
    DISCLOSURE_FAILED,
    DISCLOSURE_SPOKEN,
    RecordingDecision,
    get_disclosure_state,
)
from app.domain.services.telephony.config import _build_call_greeting
from app.domain.services.telephony.modes.agent_first import (
    _send_outbound_greeting,
    prepare_pre_originate_greeting,
)

TENANT = "22222222-2222-2222-2222-222222222222"
CALL_UUID = "11111111-1111-1111-1111-111111111111"

OPENER = "Hi, this is Sarah, an AI assistant from All State Estimation. Am I speaking with John?"
OPENER_RECORDED = (
    "Hi, this is Sarah, an AI assistant from All State Estimation, on a recorded line. "
    "Am I speaking with John?"
)


def _decision(required, text=None):
    return RecordingDecision(
        should_record=True,
        announcement_required=required,
        announcement_text=text,
        opt_out_dtmf_digit=None,
        retention_days=90,
        reason="test",
    )


PLATFORM_NOTICE = _decision(True, "This call may be recorded for quality and training purposes. "
                                  "Press 9 at any time to opt out of recording.")
TENANT_NOTICE = _decision(True, "Acme records calls to train its team.")
NO_NOTICE = _decision(False)


@pytest.fixture(autouse=True)
def _clean_ledger():
    rps._DISCLOSURE_LEDGER.clear()
    yield
    rps._DISCLOSURE_LEDGER.clear()


def _call_session(first_name="John", **extra):
    return types.SimpleNamespace(
        call_id=CALL_UUID,
        tenant_id=TENANT,
        llm_active=False,
        tts_active=False,
        barge_in_event=None,
        conversation_history=[],
        persona_type="lead_gen",
        agent_config=types.SimpleNamespace(
            agent_name="Sarah", company_name="All State Estimation", call_reason=None,
        ),
        config=types.SimpleNamespace(direction="outbound", callee_first_name=first_name),
        **extra,
    )


@pytest.mark.parametrize("first_name, expected", [
    ("John", OPENER),
    (None, "Hi, this is Sarah, an AI assistant from All State Estimation. Who am I speaking with?"),
])
def test_the_outbound_greeting_names_the_agent_the_company_and_the_ai(first_name, expected):
    assert _build_call_greeting(_call_session(first_name), first_speaker="agent") == expected


def test_the_flag_gated_llm_opener_cannot_replace_the_disclosure():
    # llm_opener.py was written to hide the AI ("You are a person").
    session = _call_session(_llm_opener_text="Hey John, Sarah here from All State -- got a minute?")
    assert _build_call_greeting(session, first_speaker="agent") == OPENER


class _Log:
    def __init__(self):
        self.events = []


class _Pipeline:
    def __init__(self, log, interrupted=False):
        self._log, self._interrupted = log, interrupted

    def clear_barge_in_event(self, session):
        pass

    async def synthesize_and_send_audio(self, session, text, websocket=None, **_):
        self._log.events.append(("tts", text))
        session._tts_delivery_status = "partial" if self._interrupted else "submitted"
        session._tts_playback_utterance_id = "u1"
        session._tts_playback_receipt = {"utterance_id": "u1", "status": "transmitted",
                                         "evidence": "transmitted"}
        return self._interrupted


class _Gateway:
    def __init__(self, log):
        self._log = log

    async def send_audio(self, call_id, chunk):
        self._log.events.append(("presynth_audio", chunk))

    async def flush_tts_buffer(self, call_id):
        pass

    async def clear_output_buffer(self, call_id):
        pass


class _Tts:
    def __init__(self, log):
        self._log = log

    async def stream_synthesize(self, text, **_):
        self._log.events.append(("presynth_text", text))
        yield b"\x00\x01" * 8


async def _answer(decision, *, interrupted=False):
    log = _Log()
    session = _call_session()
    voice_session = types.SimpleNamespace(
        call_id=CALL_UUID, call_session=session,
        pipeline=_Pipeline(log, interrupted=interrupted), media_gateway=_Gateway(log),
        tts_provider=_Tts(log), config=types.SimpleNamespace(voice_id="v", tts_sample_rate=8000),
    )
    await prepare_pre_originate_greeting(voice_session, "agent")
    container = types.SimpleNamespace(is_initialized=True, db_pool=object())
    with patch("app.core.container.get_container", return_value=container), patch.object(
        rps.RecordingPolicyService, "decide", new=AsyncMock(return_value=decision),
    ):
        await _send_outbound_greeting(voice_session)
    spoken = [e for e in log.events if e[0] in ("tts", "presynth_audio")]
    return spoken, session


async def test_the_platform_notice_is_said_inside_the_opener():
    spoken, session = await _answer(PLATFORM_NOTICE)
    assert spoken == [("tts", OPENER_RECORDED)]          # one line, and it is the notice
    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_SPOKEN or \
        rps.disclosure_delivered(CALL_UUID)
    assert [m.content for m in session.conversation_history] == [OPENER_RECORDED]
    assert session._has_introduced is True


async def test_an_interrupted_opener_still_suppresses_the_recording_and_is_not_restarted():
    spoken, session = await _answer(PLATFORM_NOTICE, interrupted=True)
    assert spoken == [("tts", OPENER_RECORDED)]          # never played again from the top
    assert get_disclosure_state(CALL_UUID) == DISCLOSURE_FAILED


async def test_a_tenants_own_notice_is_spoken_then_the_opener():
    spoken, session = await _answer(TENANT_NOTICE)
    assert spoken[0] == ("tts", "Acme records calls to train its team.")
    assert [kind for kind, _ in spoken[1:]] and all(kind == "presynth_audio" for kind, _ in spoken[1:])
    assert [m.content for m in session.conversation_history] == [
        "Acme records calls to train its team.", OPENER,
    ]


async def test_with_no_notice_owed_the_opener_is_the_first_audio():
    spoken, session = await _answer(NO_NOTICE)
    assert spoken and all(kind == "presynth_audio" for kind, _ in spoken)
    assert [m.content for m in session.conversation_history] == [OPENER]
    assert session._has_introduced is True


def test_the_callee_name_stays_out_of_greeting_logs():
    from app.domain.services.telephony.config import opener_for_log

    assert opener_for_log(OPENER) == (
        "Hi, this is Sarah, an AI assistant from All State Estimation. Am I speaking with <name>?"
    )
