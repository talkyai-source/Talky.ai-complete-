"""Unit tests for the assistant streaming ReAct loop (streaming.py).

Covers the two behaviours that are easy to get wrong:
  1. A text turn streams its content as `token` events and ends with one
     `final` (no tool_start).
  2. A tool turn whose tool-call is split across multiple stream deltas is
     reassembled correctly (name + JSON args), dispatched once, and followed
     by the streamed text answer.
Plus the fail-soft path: a fatal Groq error yields a terminal `error` event.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.infrastructure.assistant import streaming


# ---------------------------------------------------------------------------
# Fake Groq streaming primitives
# ---------------------------------------------------------------------------

def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _tc(index, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


class _FakeCompletions:
    """Returns a pre-baked async stream per create() call (one per turn)."""

    def __init__(self, turns):
        self._turns = turns
        self.calls = 0

    async def create(self, **kwargs):
        chunks = self._turns[self.calls]
        self.calls += 1

        async def gen():
            for c in chunks:
                yield c

        return gen()


def _fake_groq(turns):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(turns)))


async def _collect(**kwargs):
    events = []
    async for ev in streaming.stream_assistant_reply(**kwargs):
        events.append(ev)
    return events


_BASE = dict(
    chat_messages=[{"role": "user", "content": "hi"}],
    tenant_id="t1",
    user_id="u1",
    conversation_id=None,
    db_client=None,
    model=None,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStreaming:
    async def test_text_turn_streams_tokens_then_final(self):
        turns = [[_chunk(content="Hel"), _chunk(content="lo"), _chunk(content="!")]]
        with patch.object(streaming, "AsyncGroq", return_value=_fake_groq(turns)):
            events = await _collect(**_BASE)

        tokens = [e["delta"] for e in events if e["type"] == "token"]
        finals = [e for e in events if e["type"] == "final"]
        assert tokens == ["Hel", "lo", "!"]
        assert len(finals) == 1
        assert finals[0]["content"] == "Hello!"
        assert not any(e["type"] == "tool_start" for e in events)

    async def test_tool_turn_then_text_turn(self):
        # Turn 1: one tool call split across deltas. Turn 2: the text answer.
        turn1 = [
            _chunk(tool_calls=[_tc(0, id="call_1", name="get_campaigns", arguments="")]),
            _chunk(tool_calls=[_tc(0, arguments='{"sta')]),
            _chunk(tool_calls=[_tc(0, arguments='tus":"active"}')]),
        ]
        turn2 = [_chunk(content="You have "), _chunk(content="2 campaigns.")]
        dispatch = AsyncMock(return_value={"campaigns": ["a", "b"]})

        with patch.object(streaming, "AsyncGroq", return_value=_fake_groq([turn1, turn2])), \
             patch.object(streaming, "dispatch_tool", dispatch):
            events = await _collect(**_BASE)

        # tool_start carries the reassembled name
        assert [e for e in events if e["type"] == "tool_start"] == [
            {"type": "tool_start", "name": "get_campaigns"}
        ]
        # dispatched once with the reassembled name + parsed args (positional call)
        dispatch.assert_awaited_once()
        call = dispatch.await_args
        assert call.args[0] == "get_campaigns"
        assert call.args[4] == {"status": "active"}
        # the text answer is streamed and finalized
        assert "".join(e["delta"] for e in events if e["type"] == "token") == "You have 2 campaigns."
        assert [e for e in events if e["type"] == "final"][-1]["content"] == "You have 2 campaigns."

    async def test_fatal_error_yields_error_event(self):
        boom = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("groq down")))
            )
        )
        with patch.object(streaming, "AsyncGroq", return_value=boom):
            events = await _collect(**_BASE)

        assert events and events[-1]["type"] == "error"
        assert "groq down" in events[-1]["content"]
