"""Caller words still waiting for an answer are answered together with the newest.

Synthetic caller on the deployed agent (scripts/synthetic_caller.py,
2026-10-09, Estimation campaign, run 3): "What do you do?" was queued behind
the reply to "Can I know about your company?". The caller then said "Okay.
Thank you. Bye." over that reply. Stopping the reply released the queued
"What do you do?" as its own turn, and the goodbye was queued behind it: the
agent spoke about the company for 20 s, and only then reached the goodbye.

Separately, a third utterance used to overwrite the queued one, so the
earlier words never reached the model at all.

Now the waiting words and the newest ones form one turn, newest last, and
each keeps its own caller evidence for record_contact.
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from tests.unit.test_bargein_cancel_keeps_caller_turn import _make_service, _make_session
from tests.unit.test_turn_survives_its_own_barge_in import _Chunk, _deliver, _Pipeline, _agent_speaking


def _waiting(session, text, order):
    session._queued_next_turn = {"text": text, "seq": 3, "caller_turn_order": order,
                                 "queued_monotonic": 0.0, "confidence": None, "alternatives": ()}


async def test_a_goodbye_over_the_reply_is_answered_with_the_words_waiting_behind_it():
    p = _Pipeline()
    s, reply = _agent_speaking(p)
    _waiting(s, "What do you do?", 5)

    async def reply_body():
        try:
            await asyncio.sleep(30)
        finally:  # turn_ender's finally: a stopped reply dispatches what waited
            queued = getattr(s, "_queued_next_turn", None)
            if queued is not None:
                s._queued_next_turn = None
                released = asyncio.ensure_future(p.handle_turn_end(s, None, source="queued", user_text=queued["text"]))
                released._caller_turn_order, released._source_text = queued["caller_turn_order"], queued["text"]
                p._pending_llm_tasks[s.call_id] = released

    reply.cancel()
    await asyncio.gather(reply, return_exceptions=True)
    stopped = asyncio.ensure_future(reply_body())
    stopped._turn_type, stopped._utterance_seq, stopped._caller_turn_order = "final", 3, 4
    stopped._source_text = "Can I know about your company?"
    p._pending_llm_tasks[s.call_id] = stopped
    await asyncio.sleep(0)

    await _deliver(p, s, [_Chunk("Okay.", is_final=False), _Chunk("Okay. Thank you. Bye."), _Chunk("")])

    assert [t["user_text"] for t in p.turns] == ["What do you do? Okay. Thank you. Bye."]
    turn_task = next(iter(p._pending_llm_tasks.values()))
    assert turn_task._prior_caller_turn_orders == (5,)
    assert getattr(s, "_queued_next_turn", None) is None


async def test_words_arriving_behind_a_queued_utterance_join_it_instead_of_replacing_it():
    p = _Pipeline()
    s, thinking = _agent_speaking(p)
    s.tts_active = False  # the reply is still being written, nothing is playing
    _waiting(s, "What are the opening hours?", 2)
    s.current_user_input = ""
    try:
        await TranscriptHandler(p).handle(s, _Chunk("Actually, I am your customer."))
        await TranscriptHandler(p).handle(s, _Chunk(""))
        queued = s._queued_next_turn
        assert queued["text"] == "What are the opening hours? Actually, I am your customer."
        assert queued["prior_caller_turn_orders"] == (2,)
        assert queued["caller_turn_order"] == s._accepted_caller_turn_order
    finally:
        thinking.cancel()
        await asyncio.gather(thinking, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_joined_turn_keeps_each_fragments_caller_evidence():
    svc, session = _make_service(), _make_session()
    said = {5: "My email is alex at example.", 6: "Dot com, sorry."}

    def evidence(call_id, order):
        text = said.get(order)
        return None if text is None else {"text": text, "source": {
            "provider_item_id": f"traditional:{order}", "caller_turn_order": order,
            "revision_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}}

    svc.transcript_service = MagicMock()
    svc.transcript_service.caller_evidence.side_effect = evidence
    bound = {}

    async def reply(session_, websocket=None):
        bound["current"] = session_._contact_turn.source.caller_turn_order
        bound["all"] = [t.source.caller_turn_order for t in session_._contact_turns]
        return ("Thanks, alex at example dot com, is that right?", 0.0, 0.0)

    svc._stream_llm_and_tts = reply

    async def run():
        task = asyncio.current_task()
        task._caller_turn_order, task._prior_caller_turn_orders = 6, (5,)
        await svc._run_turn(session, "My email is alex at example. Dot com, sorry.", AsyncMock(), turn_id=3)

    await asyncio.ensure_future(run())
    assert bound == {"current": 6, "all": [5, 6]}
