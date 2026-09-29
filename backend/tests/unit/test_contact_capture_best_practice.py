"""Contact capture the way production voice agents do it (2026-09-30).

Research across Amazon Lex, Dialogflow CX, Vapi/Retell guidance and
call-centre practice converged on a few moves, adopted here:

1. Phone: confirm the number the call is already on ("Is this number the best
   one to reach you on?") instead of taking digits by voice.
2. Email: ask for the provider first, so only the name part is spoken.
3. Ask about only the unclear part, with example-word letter checks.
4. Two failed rounds, then move on -- and leave a visible "confirm it" note.
7. One outcome line per call, so capture success can be measured.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import lead_slot_capture as lsc
from app.domain.services.voice_pipeline import turn_runner as tr
from app.domain.services.voice_pipeline.contact_capture import (
    MAX_CLARIFICATION_ATTEMPTS,
    CaptureStatus,
    advance_capture,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_agent_turn,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import compose_system_prompt
from app.services.scripts.prompts import guardrails


def _history(*lines):
    return [Message(role=MessageRole.ASSISTANT if w == "agent" else MessageRole.USER, content=t)
            for w, t in lines]


# ── 1. the number the call is on ──────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Is this number the best one to reach you on?",
    "Can we reach you on the number you're calling from?",
    "Should Azian call you back on this number?",
])
def test_a_yes_to_this_number_confirms_the_line_number(question):
    state = CallState(line_phone="+923120750496")
    pending, offered = tr.phone_on_the_table(state, _history(("agent", question), ("user", "Yes.")))
    assert offered is True
    state = update_state_from_user_turn(
        pending, "Yes.", phone_readback_issued=offered, phone_confirmation_verdict="affirm",
    )
    assert (state.phone, state.phone_confirmed) == ("+923120750496", True)


def test_asking_for_a_new_number_does_not_offer_the_line_number():
    state = CallState(line_phone="+923120750496")
    _, offered = tr.phone_on_the_table(state, _history(("agent", "What's the best number to reach you?")))
    assert offered is False


def test_a_no_leaves_nothing_confirmed():
    state = CallState(line_phone="+923120750496")
    pending, offered = tr.phone_on_the_table(state, _history(("agent", "Is this number the best one to reach you on?")))
    state = update_state_from_user_turn(
        pending, "No.", phone_readback_issued=offered, phone_confirmation_verdict="reject",
    )
    assert state.phone_confirmed is False


def test_a_confirmed_phone_is_never_replaced_by_the_line_number():
    state = CallState(line_phone="+923120750496", phone="+447429916656", phone_confirmed=True)
    pending, offered = tr.phone_on_the_table(state, _history(("agent", "Is this number the best one to reach you on?")))
    assert offered is False and pending.phone == "+447429916656"


def test_the_agent_is_told_the_number_as_a_detail_not_an_action():
    prompt = compose_system_prompt("BASE", CallState(line_phone="+923120750496"))
    assert "CALL DETAILS (use only when you need them)" in prompt
    assert "plus 9 2, 3 1 2, 0 7 5, 0 4 9 6" in prompt
    assert "ACTION THIS TURN" not in prompt
    # Once confirmed it is a captured fact, and the offer disappears.
    done = compose_system_prompt("BASE", CallState(line_phone="+923120750496", phone="+923120750496", phone_confirmed=True))
    assert "CALL DETAILS" not in done


def test_the_line_number_is_only_used_when_it_is_a_real_number():
    class _Conn:
        async def fetchrow(self, *_a):
            return self.row

    class _CM:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *_a):
            return None

    async def run(row, monkeypatch):
        conn = _Conn()
        conn.row = row
        monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", lambda *a, **k: _CM(conn))
        monkeypatch.setattr("app.core.container.get_container",
                            lambda: SimpleNamespace(is_initialized=True, db_pool=object()))
        session = SimpleNamespace(_dialer_call_id="11111111-1111-1111-1111-111111111111",
                                  _dialer_tenant_id="22222222-2222-2222-2222-222222222222")
        return await tr.known_line_number(session)

    mp = pytest.MonkeyPatch()
    try:
        assert asyncio.run(run({"direction": "outbound", "phone_number": "+923120750496", "caller_ani": None}, mp)) == "+923120750496"
        assert asyncio.run(run({"direction": "inbound", "phone_number": "ext:940003", "caller_ani": "+447429916656"}, mp)) == "+447429916656"
        assert asyncio.run(run({"direction": "outbound", "phone_number": "940007", "caller_ani": None}, mp)) is None
        assert asyncio.run(run({"direction": "inbound", "phone_number": "x", "caller_ani": "ext:940007"}, mp)) is None
    finally:
        mp.undo()


# ── 2 + 3. provider first, ask only the unclear part ──────────────────────

def test_the_shared_rules_ask_provider_first_and_only_the_unclear_part():
    text = guardrails.GUARDRAILS if hasattr(guardrails, "GUARDRAILS") else "".join(
        v for k, v in vars(guardrails).items() if isinstance(v, str)
    )
    assert "Gmail, Outlook, Hotmail, Yahoo or a work address" in text
    assert '"one word, or with a dot?"' in text
    assert '"B as in Bravo?"' in text
    assert '"Is this number the best one to reach you on?"' in text


def test_provider_first_then_name_then_yes_is_captured():
    history: list[Message] = []
    state = CallState()
    turns = [
        ("agent", "Is your email a Gmail, Outlook, Hotmail, Yahoo or work address?"),
        ("user", "Gmail."),
        ("agent", "And the part before the at?"),
        ("user", "Allstate estimation, all one word."),
        ("agent", "So that's allstateestimation at gmail dot com. Is that right?"),
        ("user", "Yes."),
    ]
    for who, text in turns:
        if who == "agent":
            history.append(Message(role=MessageRole.ASSISTANT, content=text))
            state = update_state_from_agent_turn(state, text)
            continue
        history.append(Message(role=MessageRole.USER, content=text))
        pending, read_back = tr.email_on_the_table(state, text, history)
        gate = bool(pending.email and not pending.email_confirmed and read_back)
        verdict = tr._classify_core_confirmation(text) if gate else None
        state = update_state_from_user_turn(pending, text, readback_issued=read_back, confirmation_verdict=verdict)
    assert (state.email, state.email_confirmed) == ("allstateestimation@gmail.com", True)


# ── 4. two strikes, and a given-up field stays given up ───────────────────

def test_two_failed_asks_then_the_agent_moves_on():
    assert MAX_CLARIFICATION_ATTEMPTS == 2
    state = None
    for _ in range(6):
        state = advance_capture(state, kind="phone", utterance="923016253193", mode_active=True)
    assert state.status is CaptureStatus.CANCELLED  # did not restart the loop


def test_a_complete_number_still_reopens_after_giving_up():
    state = None
    for _ in range(4):
        state = advance_capture(state, kind="phone", utterance="923016253193", mode_active=True)
    state = advance_capture(state, kind="phone", utterance="call me on +1 415 555 2671", mode_active=True)
    assert state.status is CaptureStatus.AWAITING_CONFIRMATION


# ── 4 + 7. outcome line and the follow-up note ────────────────────────────

class _NoteConn:
    def __init__(self, is_test=False):
        self.writes: list[tuple] = []
        self.is_test = is_test

    def transaction(self):
        return _Ctx(None)

    async def execute(self, *_a):
        return None

    async def fetchrow(self, sql, *args):
        if "FROM calls" in sql:
            return {"is_test": self.is_test}
        self.writes.append(args)
        return {"id": "row"}


class _Ctx:
    def __init__(self, v):
        self.v = v

    async def __aenter__(self):
        return self.v

    async def __aexit__(self, *_a):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self, **_k):
        return _Ctx(self.conn)


def _unresolved_email_state():
    state = None
    for _ in range(4):
        state = advance_capture(state, kind="email", utterance="Allstate estimation at Gmail dot com.", mode_active=True)
    return CallState(email_capture=state)


def test_an_unconfirmed_email_leaves_a_follow_up_note_and_an_outcome_line(caplog):
    conn = _NoteConn()
    session = SimpleNamespace(captured_slots=_unresolved_email_state())
    with caplog.at_level(logging.INFO):
        asyncio.run(lsc.record_contact_outcome(
            session, pool=_Pool(conn),
            call_id="11111111-1111-1111-1111-111111111111",
            tenant_id="22222222-2222-2222-2222-222222222222",
        ))
    assert "contact_capture_outcome" in caplog.text and "email=unconfirmed" in caplog.text
    assert conn.writes, "a follow-up note is written"
    args = conn.writes[0]
    assert args[4] == lsc.FOLLOWUP_FIELD
    assert "email address could not be confirmed" in args[6]


def test_a_confirmed_email_writes_no_note():
    conn = _NoteConn()
    session = SimpleNamespace(captured_slots=CallState(email="bob@gmail.com", email_confirmed=True))
    asyncio.run(lsc.record_contact_outcome(
        session, pool=_Pool(conn),
        call_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
    ))
    assert conn.writes == []


def test_a_browser_test_call_writes_no_note():
    conn = _NoteConn(is_test=True)
    session = SimpleNamespace(captured_slots=_unresolved_email_state())
    asyncio.run(lsc.record_contact_outcome(
        session, pool=_Pool(conn),
        call_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
    ))
    assert conn.writes == []


def test_the_outcome_runs_at_hangup():
    import inspect

    from app.services.scripts import call_transcript_persister as p

    assert "record_contact_outcome(" in inspect.getsource(p)
