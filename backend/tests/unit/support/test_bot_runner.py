import asyncio
import json
from types import SimpleNamespace as NS

from app.domain.services.support.bot import runner
from app.domain.services.support.bot.diagnostics import ToolOutcome
from app.domain.services.support.bot.types import BotRequest, Source, ToolTrace

GOOD = json.dumps({"answer": "ok", "sources": ["s", "ghost"], "resolution_prompt": True,
                   "needs_clarification": False, "propose_escalation": False,
                   "escalation_reason": "", "category": "billing"})


def call(i, name, args="{}"):
    return NS(id=f"c{i}", function=NS(name=name, arguments=args))


def resp(content=None, calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))])


class FakeClient:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.requests.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item


class FakeExec:
    def __init__(self):
        self.ran = []

    async def execute(self, name, raw):
        self.ran.append(name)
        srcs = (Source("s", "T", "/docs/s"),) if name == "search_help_articles" else ()
        return ToolOutcome({"ok": 1}, ToolTrace(name, "x"), sources=srcs)


def req(**kw):
    return BotRequest(tenant_id="t", user_id="u", user_text="help", model="m", **kw)


def factory(client):
    return lambda model: (client, lambda a: a)


async def test_happy_path_sources_filtered():
    ex = FakeExec()
    c = FakeClient([resp(calls=[call(1, "search_help_articles", '{"query":"abc"}')]), resp(content="done"), resp(GOOD)])
    turn = await runner.run_turn(req(), executor=ex, client_factory=factory(c))
    assert turn.error is None and [s.slug for s in turn.sources] == ["s"] and turn.resolution_prompt
    assert [t.name for t in turn.tool_calls] == ["search_help_articles"] and turn.model == "m"
    assert "tools" not in c.requests[-1] and "response_format" in c.requests[-1]
    assert c.requests[0]["messages"][0]["role"] == "system" and c.requests[0]["messages"][1]["content"] == "help"


async def test_history_limited_to_12():
    hist = tuple(("user", str(i)) for i in range(30))
    c = FakeClient([resp(content="x"), resp(GOOD)])
    await runner.run_turn(req(history=hist), executor=FakeExec(), client_factory=factory(c))
    assert len(c.requests[0]["messages"]) == 1 + 12 + 1


async def test_budget_of_three():
    ex = FakeExec()
    c = FakeClient([resp(calls=[call(i, "get_account_overview") for i in range(5)]), resp(GOOD)])
    turn = await runner.run_turn(req(), executor=ex, client_factory=factory(c))
    assert len(ex.ran) == 3 and len(turn.tool_calls) == 3
    tool_msgs = [m for m in c.requests[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 5 and json.loads(tool_msgs[-1]["content"]) == {"error": "tool_budget_exhausted"}


async def test_provider_error():
    c = FakeClient([RuntimeError("boom")])
    turn = await runner.run_turn(req(), executor=FakeExec(), client_factory=factory(c))
    assert turn.error == "provider_error" and turn.answer


async def test_not_configured():
    def f(m):
        raise ValueError("no key")

    assert (await runner.run_turn(req(), executor=FakeExec(), client_factory=f)).error == "not_configured"


async def test_invalid_output():
    c = FakeClient([resp(content="x"), resp("not json")])
    assert (await runner.run_turn(req(), executor=FakeExec(), client_factory=factory(c))).error == "invalid_output"


async def test_timeout(monkeypatch):
    monkeypatch.setattr(runner, "BOT_TIMEOUT_SECONDS", 0.05)

    async def slow():
        await asyncio.sleep(1)

    c = FakeClient([slow])
    assert (await runner.run_turn(req(), executor=FakeExec(), client_factory=factory(c))).error == "timeout"
