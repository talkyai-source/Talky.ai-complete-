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
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
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

    async def test_explicit_email_read_is_dispatched_before_model_text(self):
        """The model cannot skip read_emails for an unambiguous inbox request."""
        completions = _FakeCompletions(
            [[_chunk(content="Here are your five most recent emails.")]]
        )
        groq = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        dispatch = AsyncMock(
            return_value={"success": True, "emails": [{"subject": "Status"}]}
        )
        kwargs = {
            **_BASE,
            "chat_messages": [
                {"role": "user", "content": "please read my last 5 e mails"}
            ],
        }

        with patch.object(streaming, "AsyncGroq", return_value=groq), \
             patch.object(streaming, "dispatch_tool", dispatch):
            events = await _collect(**kwargs)

        assert events[0] == {"type": "tool_start", "name": "read_emails"}
        dispatch.assert_awaited_once_with(
            "read_emails", "t1", None, None, {"max_results": 5, "query": "in:inbox"}
        )
        assert events[-1] == {
            "type": "final",
            "content": "Here are your five most recent emails.",
        }

        request = completions.requests[0]
        assert "tools" not in request
        assert "tool_choice" not in request
        assert request["messages"][-2]["tool_calls"][0]["function"] == {
            "name": "read_emails",
            "arguments": '{"max_results": 5, "query": "in:inbox"}',
        }
        assert request["messages"][-1]["role"] == "tool"
        assert '"subject": "Status"' in request["messages"][-1]["content"]
        assert "UNTRUSTED EMAIL DATA" in request["messages"][-1]["content"]

    async def test_explicit_email_read_returns_classified_tool_error_verbatim(self):
        """A provider timeout must not be reworded by the model as reconnect."""
        completions = _FakeCompletions([])
        groq = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        dispatch = AsyncMock(
            return_value={
                "success": False,
                "error": "Gmail took too long to respond. Please try again.",
                "error_code": "email_timeout",
            }
        )
        kwargs = {
            **_BASE,
            "chat_messages": [{"role": "user", "content": "read my last 5 emails"}],
        }

        with patch.object(streaming, "AsyncGroq", return_value=groq), \
             patch.object(streaming, "dispatch_tool", dispatch):
            events = await _collect(**kwargs)

        assert events == [
            {"type": "tool_start", "name": "read_emails"},
            {
                "type": "final",
                "content": "Gmail took too long to respond. Please try again.",
            },
        ]
        assert completions.calls == 0

    async def test_explicit_email_read_hides_unclassified_dispatch_error(self):
        """Unexpected tool exceptions must not leak their raw text to chat."""
        completions = _FakeCompletions([])
        groq = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        dispatch = AsyncMock(
            return_value={"error": "postgresql://user:secret@internal-db/private"}
        )
        kwargs = {
            **_BASE,
            "chat_messages": [{"role": "user", "content": "read my last 5 emails"}],
        }

        with patch.object(streaming, "AsyncGroq", return_value=groq), \
             patch.object(streaming, "dispatch_tool", dispatch):
            events = await _collect(**kwargs)

        assert events[-1] == {
            "type": "final",
            "content": "I couldn't read the inbox just now. Please try again.",
        }
        assert "secret" not in events[-1]["content"]
        assert completions.calls == 0

    async def test_explicit_email_read_preserves_classified_reconnect_instruction(self):
        completions = _FakeCompletions([])
        groq = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        dispatch = AsyncMock(
            return_value={
                "success": False,
                "error": "Your email authorization expired. Please reconnect from the Connectors page.",
                "email_required": True,
                "error_code": "email_not_connected",
            }
        )
        kwargs = {
            **_BASE,
            "chat_messages": [{"role": "user", "content": "read my last 5 emails"}],
        }

        with patch.object(streaming, "AsyncGroq", return_value=groq), \
             patch.object(streaming, "dispatch_tool", dispatch):
            events = await _collect(**kwargs)

        assert events[-1]["content"] == (
            "Your email authorization expired. Please reconnect from the Connectors page."
        )
        assert completions.calls == 0

    def test_email_intent_router_is_narrow_and_uses_latest_user_turn(self):
        cases = {
            "show my last five e-mails": {"max_results": 5},
            "check my unread inbox": {"unread_only": True, "query": "in:inbox"},
            "read my latest email": {"max_results": 1},
            "show me the 100 newest emails": {"max_results": 25},
            "read my mail": {},
            "do I have any new emails?": {},
            "what's in my inbox?": {"query": "in:inbox"},
            "any unread mail?": {"unread_only": True},
            "send an email and check the dashboard": None,
            "check my email connector": None,
            "don't read my emails": None,
            "don’t read my emails": None,
            "why can't you read my emails?": None,
            "what is my email address?": None,
            "read the first email": None,
            "read my last 5 emails from Jane": None,
            "I asked you not to read my emails": None,
            "show me an email template": None,
            "review my email campaign": None,
            "get my email signature": None,
            "show mail from Jane": None,
            "check my mail about the invoice": None,
            "show starred emails": None,
            "show messages in my inbox from Jane": None,
            "show me how to read my emails": None,
            "do you read my emails?": None,
            "are you able to read my emails?": None,
            "what happens when you read my emails?": None,
            "open my last 5 emails": {"max_results": 5},
            "give me my last 5 emails": {"max_results": 5},
            "summarize my last 5 emails": {"max_results": 5},
            "what emails do I have?": {},
            "show my five most recent emails": {"max_results": 5},
            "read my last 5 emails; Gmail is connected": {"max_results": 5},
            "show my last five emails before I leave": {"max_results": 5},
            "show my last five emails now, not later": {"max_results": 5},
            "don't send anything, just read my last five emails": {"max_results": 5},
            "stop talking and read my last five emails": {"max_results": 5},
            "do not summarize them, read my last five emails": {"max_results": 5},
            "show emails received today": None,
            "show emails sent today": None,
            "show emails that are starred": None,
            "show emails marked important": None,
            "show emails in trash": None,
            "read my last fifteen emails": {"max_results": 15},
            "read my last twenty emails": {"max_results": 20},
            "read my last twenty-five emails": {"max_results": 25},
            "please read my last five emails and then send Bob a summary": None,
            "read my last five emails and check my dashboard": None,
            "I asked you not to ever read my emails": None,
            "please never again read my emails": None,
            "do not under any circumstances read my emails": None,
            "never, under any circumstances, read my emails": None,
            "show the last 5 messages in my inbox": {
                "max_results": 5,
                "query": "in:inbox",
            },
            "read my last 5 work emails": None,
            "Yesterday you said you could read my last five emails.": None,
            "If I asked you to read my emails, what would happen?": None,
            "read my last five emails and schedule a meeting": None,
            "read my last five emails and create a campaign": None,
            "For example, read my last five emails": None,
            "The phrase, read my emails, should not run": None,
            "My manager said, read my emails was the test phrase": None,
            "read my last twenty six emails": {"max_results": 25},
            "read my last thirty five emails": {"max_results": 25},
            "read my last thirty emails": {"max_results": 25},
            "are there any new emails?": {},
            "read my emails—actually, don't": None,
            "read my emails, no, don't do that": None,
            "read my emails... never mind": None,
            "show my last five emails, cancel that": None,
            "check my inbox—wait, stop": None,
            "show my emails, cancel the request": None,
            "read my emails, actually don't do anything": None,
            "read my emails, no, don't do this": None,
            "read zero emails": None,
            "read -1 emails": None,
            "read 2.5 emails": None,
            "please read the email I just pasted below": None,
            "can you summarize an email?": None,
            "please read an email": None,
            "summarize the following email": None,
            "review email wording": None,
            "check an email for grammar": None,
            "read my emails if I say yes": None,
            "read my emails or not, I am unsure": None,
            "maybe read my emails": None,
            "open the email in my inbox": None,
            "read email in my inbox": None,
            "show the email in my mailbox": None,
            "summarize an email in my inbox": None,
        }
        for text, expected in cases.items():
            if expected is not None:
                expected = {**expected, "query": "in:inbox"}
            assert streaming._forced_read_emails_args(
                [{"role": "user", "content": text}]
            ) == expected

        assert streaming._forced_read_emails_args(
            [
                {"role": "user", "content": "read my last 5 emails"},
                {"role": "assistant", "content": "Here they are."},
                {"role": "user", "content": "thanks"},
            ]
        ) is None
