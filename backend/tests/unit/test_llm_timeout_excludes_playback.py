"""The LLM timeout budget must measure provider wait, not wall clock.

WHY THIS EXISTS (2026-08-06)
----------------------------
`stream_chat_with_timeout` is a generator the voice pipeline PULLS from.
Between pulls, `turn_streamer` awaits `synthesize_and_send_audio`, which paces
audio playback in REAL TIME — seconds per sentence.

Gemini and Cerebras computed their remaining budget as

    remaining = timeout_seconds - (loop.time() - t_start)

i.e. wall clock from stream start. That charged the caller's own audio playback
against the LLM's allowance. On a telephony session, which allows up to 5
sentences per turn (`telephony_session_config.py`), several seconds of playback
were deducted from a 10s budget, so a healthy multi-sentence reply hit
`remaining <= 0` partway through and took the mid-stream `break`: the reply was
truncated mid-thought, logged only as "treating as stream end", with no error
raised and no fallback line spoken. The caller just heard the agent stop.

Groq already did this correctly and its docstring says why:

    "The timeout_seconds budget measures ONLY the time spent awaiting the
     [provider] ... Counting playback time against the LLM budget was
     truncating healthy [replies]."

These tests drive the real generators with a deliberately SLOW consumer — the
thing that actually broke it — rather than asserting on source text.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.models.conversation import Message, MessageRole


def _messages():
    return [Message(role=MessageRole.USER, content="hello")]


class _FakeStream:
    """Yields `n` tokens, each after a small real provider delay."""

    def __init__(self, n: int, per_token_delay: float = 0.01):
        self.n = n
        self.delay = per_token_delay
        self.yielded = 0

    def __call__(self, messages, **kwargs):
        return self._gen()

    async def _gen(self):
        for i in range(self.n):
            await asyncio.sleep(self.delay)
            self.yielded += 1
            yield f"tok{i} "


async def _drain_with_slow_consumer(
    provider, *, timeout_seconds: float, consumer_delay: float
) -> list[str]:
    """Pull every token, sleeping `consumer_delay` between pulls.

    That sleep stands in for `synthesize_and_send_audio` pacing real audio.
    """
    out: list[str] = []
    async for tok in provider.stream_chat_with_timeout(
        _messages(), timeout_seconds=timeout_seconds
    ):
        out.append(tok)
        await asyncio.sleep(consumer_delay)
    return out


def _make_gemini(stream):
    from app.infrastructure.llm.gemini import GeminiLLMProvider

    p = GeminiLLMProvider.__new__(GeminiLLMProvider)
    p.stream_chat = stream
    return p


def _make_cerebras(stream):
    from app.infrastructure.llm.cerebras import CerebrasLLMProvider

    p = CerebrasLLMProvider.__new__(CerebrasLLMProvider)
    p.stream_chat = stream
    return p


@pytest.mark.parametrize("factory", [_make_gemini, _make_cerebras])
@pytest.mark.asyncio
async def test_slow_consumer_does_not_truncate_the_reply(factory):
    """THE REGRESSION.

    8 tokens, 10ms of real provider wait each = 80ms of actual LLM time.
    The consumer sleeps 60ms between pulls (~420ms of "playback"), so total
    wall clock (~0.5s) far exceeds the 0.3s budget while true provider wait
    (80ms) stays well under it.

    Before the fix the wall-clock budget expired mid-stream and the reply was
    silently cut short. All 8 tokens must survive.
    """
    stream = _FakeStream(n=8, per_token_delay=0.01)
    provider = factory(stream)

    out = await _drain_with_slow_consumer(
        provider, timeout_seconds=0.3, consumer_delay=0.06
    )

    assert len(out) == 8, (
        f"reply truncated to {len(out)}/8 tokens — consumer playback time is "
        "being charged against the LLM budget again"
    )


@pytest.mark.parametrize("factory", [_make_gemini, _make_cerebras])
@pytest.mark.asyncio
async def test_a_genuinely_slow_provider_still_times_out(factory):
    """The budget must still bite when the PROVIDER is the slow one.

    Without this, "fix the truncation" could degenerate into "never time out",
    which would strand a caller in silence on a hung provider.
    """
    from app.infrastructure.llm.groq import LLMTimeoutError

    class _Hang:
        def __call__(self, messages, **kwargs):
            return self._gen()

        async def _gen(self):
            await asyncio.sleep(10)
            yield "never"

    provider = factory(_Hang())

    with pytest.raises(LLMTimeoutError):
        await _drain_with_slow_consumer(
            provider, timeout_seconds=0.2, consumer_delay=0.0
        )


@pytest.mark.parametrize("factory", [_make_gemini, _make_cerebras])
@pytest.mark.asyncio
async def test_fast_path_is_unchanged(factory):
    """A normal, fast turn must behave exactly as before."""
    stream = _FakeStream(n=4, per_token_delay=0.001)
    provider = factory(stream)

    out = await _drain_with_slow_consumer(
        provider, timeout_seconds=5.0, consumer_delay=0.0
    )
    assert len(out) == 4
    assert "".join(out).startswith("tok0")


def test_all_three_providers_budget_provider_wait_not_wall_clock():
    """Structural guard: none of the three may reintroduce a wall-clock budget.

    The bug shape is `timeout_seconds - (now - t_start)`. Groq was always
    correct; Gemini and Cerebras were not. Pin all three so a future provider
    copy-pastes the right one.
    """
    from tests.unit._source_scan import code, function_body

    for rel, accum in (
        ("app/infrastructure/llm/groq.py", "groq_wait_accumulated"),
        ("app/infrastructure/llm/gemini.py", "gemini_wait_accumulated"),
        ("app/infrastructure/llm/cerebras.py", "cerebras_wait_accumulated"),
    ):
        body = function_body(code(rel), "stream_chat_with_timeout")
        assert f"remaining = timeout_seconds - {accum}" in body, (
            f"{rel}: budget must be computed from accumulated provider wait"
        )
        assert "_wait_t0" in body, f"{rel}: must measure each await span"
