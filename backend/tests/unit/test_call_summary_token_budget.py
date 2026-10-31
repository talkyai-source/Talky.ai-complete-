"""A long call must still get a summary.

Production, call 2427af7e, 2026-09-22 -- 243 seconds, 353 transcript rows:

    400 json_validate_failed - 'max completion tokens reached before
    generating a valid document'

max_tokens was a flat 1500 however long the call was. Under constrained
decoding an exhausted output budget is a hard error, not a short answer, so the
entire summary was thrown away. And because lead qualification reads the
summary (store._summary_supports_lead), that call also got no lead decision.

This is the same shape as the knowledge enricher's flat 2048-token budget fixed
on 2026-09-22: a fixed output allowance for a variable-sized input. The lesson
did not travel between the two call sites, so it is pinned here.
"""
from __future__ import annotations

import pytest

from app.domain.services.call_summary.summarizer import (
    _SUMMARY_MAX_TOKENS,
    _SUMMARY_MIN_TOKENS,
    _TOKENS_EXHAUSTED,
    _summary_token_budget,
)

# Roughly the size of the transcript that failed: 353 rows of a 243-second call.
_FAILED_CALL_CHARS = 14_000


def test_the_transcript_that_failed_now_gets_more_than_the_old_flat_budget():
    assert _summary_token_budget("x" * _FAILED_CALL_CHARS) > 1500


def test_budget_grows_with_the_transcript():
    short = _summary_token_budget("x" * 500)
    medium = _summary_token_budget("x" * _FAILED_CALL_CHARS)
    long = _summary_token_budget("x" * 60_000)
    assert short < medium < long


def test_budget_never_falls_below_the_floor():
    """A 10-second call still has to produce the whole fixed-shape document.

    The floor is a minimum, not a target: a short transcript still adds its own
    small increment on top, which is why this asserts >= rather than ==.
    """
    for text in ("", "   ", "Hello.", "x" * 200, "x" * 2000):
        assert _summary_token_budget(text) >= _SUMMARY_MIN_TOKENS, text[:20]


def test_an_empty_transcript_gets_exactly_the_floor():
    for text in ("", "   "):
        assert _summary_token_budget(text) == _SUMMARY_MIN_TOKENS


def test_budget_is_capped():
    """Sub-linear and bounded: a 40-minute call does not ask for 100k tokens."""
    assert _summary_token_budget("x" * 5_000_000) == _SUMMARY_MAX_TOKENS


def test_growth_is_sub_linear():
    """The summary's shape is fixed; only its list fields grow, and they track
    what happened on the call rather than its length."""
    ten_x_input = _summary_token_budget("x" * 200_000)
    one_x_input = _summary_token_budget("x" * 20_000)
    assert ten_x_input < one_x_input * 10


def test_none_and_empty_are_safe():
    assert _summary_token_budget(None) == _SUMMARY_MIN_TOKENS


@pytest.mark.asyncio
async def test_an_exhausted_budget_is_retried_at_the_ceiling(monkeypatch):
    """The failure that lost the summary must not lose it twice.

    The first attempt raises the provider's budget error; the retry must go out
    at the ceiling and its result must be what gets returned.
    """
    import app.domain.services.call_summary.summarizer as mod

    seen: list[int] = []

    class _FakeCompletions:
        async def create(self, **kwargs):
            seen.append(kwargs["max_tokens"])
            if len(seen) == 1:
                raise RuntimeError(
                    "Error code: 400 - {'error': {'code': 'json_validate_failed', "
                    "'failed_generation': '" + _TOKENS_EXHAUSTED + " before "
                    "generating a valid document'}}"
                )
            return type(
                "R",
                (),
                {
                    "choices": [
                        type(
                            "C",
                            (),
                            {
                                "message": type(
                                    "M", (), {"content": '{"headline": "recovered"}'}
                                )()
                            },
                        )()
                    ]
                },
            )()

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.chat = type("Chat", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(mod, "AsyncGroq", _FakeClient)

    result = await mod.summarize_transcript("caller said a great deal " * 400)

    assert len(seen) == 2, "the budget error must trigger exactly one retry"
    assert seen[0] < seen[1], "the retry must ask for more room than the first try"
    assert seen[1] == _SUMMARY_MAX_TOKENS
    assert result["headline"] == "recovered"


@pytest.mark.asyncio
async def test_an_unrelated_error_is_not_retried(monkeypatch):
    """Only the budget error earns a second call; everything else fails soft.

    Retrying a schema error or an auth failure would double the cost of every
    outage for nothing.
    """
    import app.domain.services.call_summary.summarizer as mod

    calls: list[int] = []

    class _FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs["max_tokens"])
            raise RuntimeError("Error code: 401 - invalid api key")

    class _FakeClient:
        def __init__(self, *a, **kw):
            self.chat = type("Chat", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(mod, "AsyncGroq", _FakeClient)

    result = await mod.summarize_transcript("hello there")

    assert len(calls) == 1
    assert result["headline"] == mod.SUMMARY_UNAVAILABLE_HEADLINE
