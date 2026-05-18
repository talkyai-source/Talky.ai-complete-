"""Tests for Groq usage / prompt-cache observability (T4-B1).

These cover the small pure helpers that pull Groq's per-request token
usage out of the SDK response object. The streaming integration (which
plumbs ``stream_options.include_usage=True`` into the request and
captures the final usage chunk) is exercised by the existing Groq
streaming tests; here we lock the contract for the extraction logic
that turns Groq's possibly-shifting SDK shapes into a stable log line.

The shape Groq returns today (May 2026) is the OpenAI-compatible one:
``usage.prompt_tokens_details.cached_tokens``. Older / future SDK
versions may expose a flat ``usage.cached_tokens`` field. The
extractor tolerates both so a SDK upgrade does not silently drop the
metric — that would be a worse failure than the SDK-error case.
"""
from types import SimpleNamespace

import pytest

from app.infrastructure.llm.groq import (
    _coerce_int,
    _emit_usage_log,
    _extract_cached_tokens,
)


class TestCoerceInt:
    @pytest.mark.parametrize("value,expected", [
        (None, 0),
        (0, 0),
        (42, 42),
        ("42", 42),
        ("not-a-number", 0),
        ("", 0),
        (3.7, 3),
        ([], 0),
    ])
    def test_handles_common_inputs_safely(self, value, expected):
        assert _coerce_int(value) == expected


class TestExtractCachedTokens:
    """Both shapes Groq might serve up — locked here so an SDK upgrade
    that flips the field path doesn't silently zero out the metric."""

    def test_flat_field_legacy_shape(self):
        usage = SimpleNamespace(cached_tokens=128)
        assert _extract_cached_tokens(usage) == 128

    def test_nested_prompt_tokens_details_current_shape(self):
        """The OpenAI-compatible shape Groq uses today."""
        usage = SimpleNamespace(
            prompt_tokens_details=SimpleNamespace(cached_tokens=512),
        )
        assert _extract_cached_tokens(usage) == 512

    def test_dict_with_flat_field(self):
        """Some SDKs expose usage as a dict rather than a typed object."""
        assert _extract_cached_tokens({"cached_tokens": 64}) == 64

    def test_dict_with_nested_details(self):
        usage = {"prompt_tokens_details": {"cached_tokens": 256}}
        assert _extract_cached_tokens(usage) == 256

    def test_none_returns_zero(self):
        assert _extract_cached_tokens(None) == 0

    def test_missing_field_returns_zero(self):
        usage = SimpleNamespace(prompt_tokens=2048)
        assert _extract_cached_tokens(usage) == 0

    def test_malformed_value_returns_zero(self):
        usage = SimpleNamespace(cached_tokens="something_weird")
        assert _extract_cached_tokens(usage) == 0


class TestEmitUsageLog:
    """The log line is the single source of truth operators read to know
    if prompt caching is firing. Lock its exact structured fields."""

    def test_emits_all_fields(self, caplog):
        usage = SimpleNamespace(
            prompt_tokens=2000,
            completion_tokens=80,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1500),
        )
        with caplog.at_level("INFO", logger="app.infrastructure.llm.groq"):
            _emit_usage_log(usage, model="llama-3.3-70b-versatile")

        msgs = [r.getMessage() for r in caplog.records if "llm_usage" in r.getMessage()]
        assert len(msgs) == 1
        m = msgs[0]
        assert "model=llama-3.3-70b-versatile" in m
        assert "prompt_tokens=2000" in m
        assert "cached_tokens=1500" in m
        assert "completion_tokens=80" in m
        # Cache hit ratio = 1500 / 2000 = 0.75
        assert "cache_hit_ratio=0.75" in m

    def test_zero_prompt_tokens_does_not_divide_by_zero(self, caplog):
        usage = SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            prompt_tokens_details=None,
        )
        with caplog.at_level("INFO", logger="app.infrastructure.llm.groq"):
            _emit_usage_log(usage, model="any")

        msgs = [r.getMessage() for r in caplog.records if "llm_usage" in r.getMessage()]
        assert len(msgs) == 1
        # No exception, ratio renders as 0.00.
        assert "cache_hit_ratio=0.00" in msgs[0]

    def test_dict_usage_is_supported(self, caplog):
        """Some SDK versions deliver usage as a dict; the log emitter
        must not crash and must extract the same fields."""
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 500},
        }
        with caplog.at_level("INFO", logger="app.infrastructure.llm.groq"):
            _emit_usage_log(usage, model="any")

        msgs = [r.getMessage() for r in caplog.records if "llm_usage" in r.getMessage()]
        assert len(msgs) == 1
        assert "cached_tokens=500" in msgs[0]
        assert "cache_hit_ratio=0.50" in msgs[0]
