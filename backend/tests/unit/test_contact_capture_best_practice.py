"""Contact context, known-line lookup and durable outcome regressions.

Legacy clarification utility checks remain diagnostic only. The model now
chooses contact wording and invokes record_contact; no provider-first or
line-number yes/no parser controls the live workflow."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline import lead_slot_capture as lsc
from app.domain.services.voice_pipeline import turn_runner as tr
from app.domain.services.voice_pipeline.contact_capture import (
    MAX_CLARIFICATION_ATTEMPTS,
    CaptureStatus,
    advance_capture,
)
from app.services.scripts.call_state_tracker import (
    CallState,
)
from app.services.scripts.prompt_builder import compose_system_prompt
from app.services.scripts.prompts import guardrails


# ── 1. the number the call is on ──────────────────────────────────────────


def test_the_agent_is_told_the_number_as_a_detail_not_an_action():
    prompt = compose_system_prompt("BASE", CallState(line_phone="+923120750496"))
    assert '"call_line_number_unconfirmed": "+923120750496"' in prompt
    assert "ACTION THIS TURN" not in prompt and '"status": "confirmed"' not in prompt
    done = compose_system_prompt("BASE", CallState(line_phone="+923120750496", phone="+923120750496", phone_confirmed=True))
    assert '"status": "confirmed"' in done and '"call_line_number_unconfirmed"' in done


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

def test_the_shared_rules_accept_complete_contacts_and_clarify_only_unclear_parts():
    text = " ".join(guardrails.GENERIC_GUARDRAILS.split())
    assert "record_contact" in text and "own email or phone" in text
    assert "confirm its accuracy naturally" in text
    assert "known line number is context, not automatically" in text


# ── 4. two strikes, and a given-up field stays given up ───────────────────

def test_two_failed_asks_then_the_agent_moves_on():
    assert MAX_CLARIFICATION_ATTEMPTS == 2
    state = None
    for _ in range(6):
        state = advance_capture(state, kind="phone", utterance="2079460958", mode_active=True)
    assert state.status is CaptureStatus.CANCELLED  # did not restart the loop


def test_a_complete_number_still_reopens_after_giving_up():
    state = None
    for _ in range(4):
        state = advance_capture(state, kind="phone", utterance="2079460958", mode_active=True)
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
        if "FROM calls" in sql and "INSERT INTO" not in sql:
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
