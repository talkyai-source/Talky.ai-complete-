"""The agent never hangs up while the caller is talking.

Call d644f0ea (2026-09-28 20:02): the caller was correcting their WhatsApp
number ("No. It's zero three one...") when a re-issued reply ("Thanks, I'll
pass that on...") carried a hangup. Their words streamed in from 20:02:28 and
the call was cut at 20:02:31.
"""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

from app.domain.services.voice_pipeline import turn_ender
from app.domain.services.voice_pipeline.turn_ender import caller_talking_over_close


def _session(**kw):
    base = dict(current_user_input="", _caller_speaking=False,
                _caller_speaking_since=None, _caller_last_text_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_words_during_the_reply_block_the_hangup():
    started = time.monotonic() - 5.0
    s = _session(current_user_input="No. It's zero three one",
                 _caller_last_text_at=started + 3.0)
    assert caller_talking_over_close(s, started)


def test_the_callers_floor_blocks_the_hangup():
    now = time.monotonic()
    assert caller_talking_over_close(_session(_caller_speaking=True, _caller_speaking_since=now - 1), now)


def test_a_stuck_floor_flag_cannot_keep_the_call_open_forever():
    now = time.monotonic()
    assert not caller_talking_over_close(_session(_caller_speaking=True, _caller_speaking_since=now - 60), now)


def test_trailing_copies_of_the_triggering_words_do_not_count():
    started = time.monotonic()
    assert not caller_talking_over_close(_session(_caller_last_text_at=started + 0.4), started)


def test_a_goodbye_or_backchannel_over_the_goodbye_still_closes():
    started = time.monotonic() - 5.0
    for words in ("Bye bye.", "okay", "thank you"):
        s = _session(current_user_input=words, _caller_last_text_at=started + 3.0,
                     _caller_speaking=True, _caller_speaking_since=time.monotonic())
        assert not caller_talking_over_close(s, started), words


def test_a_quiet_caller_lets_the_agent_close():
    assert not caller_talking_over_close(_session(), time.monotonic())


def test_the_rule_sits_on_the_hangup_path():
    src = inspect.getsource(turn_ender.TurnEnder.handle)
    assert "caller_talking_over_close(session, turn_started_at)" in src
    assert "end_call_stripped_caller_talking" in src
    assert src.index("turn_started_at = time.monotonic()") < src.index("caller_talking_over_close(")
