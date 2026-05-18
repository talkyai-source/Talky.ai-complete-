"""Tests for app.domain.services.provider_cost_ledger."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.domain.services import provider_cost_ledger as L


@pytest.fixture(autouse=True)
def _clean_ledger(monkeypatch):
    L._buffer.clear()
    monkeypatch.setenv("COST_LEDGER_ENABLED", "true")
    yield
    L._buffer.clear()


def _ev(**kw):
    base = dict(
        tenant_id="t-1", provider="groq", provider_role="llm",
        unit="tokens_in", quantity=10.0,
    )
    base.update(kw)
    return L.CostEvent(**base)


def test_record_appends_to_buffer():
    L.record(_ev())
    L.record(_ev(provider="elevenlabs", provider_role="tts", unit="characters"))
    assert L.buffer_size() == 2


def test_record_drops_oldest_when_full(monkeypatch):
    monkeypatch.setattr(L, "MAX_BUFFER_SIZE", 3)
    for i in range(5):
        L.record(_ev(quantity=float(i)))
    assert L.buffer_size() == 3
    # Oldest entries (0 and 1) were dropped.
    assert [e.quantity for e in L._buffer] == [2.0, 3.0, 4.0]


def test_record_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("COST_LEDGER_ENABLED", "false")
    L.record(_ev())
    assert L.buffer_size() == 0


def test_redact_key_fp_handles_short_and_long():
    assert L.redact_key_fp(None) is None
    assert L.redact_key_fp("short") == "sh***"
    assert L.redact_key_fp("sk_abcdefghijklmnop") == "sk_a…mnop"


def test_groq_usage_parser_emits_two_events():
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
    out = L.parse_groq_usage(usage)
    assert len(out) == 2
    units = sorted(e.unit for e in out)
    assert units == ["tokens_in", "tokens_out"]


def test_elevenlabs_usage_parser_counts_chars():
    out = L.parse_elevenlabs_usage("hello world")
    assert out[0].unit == "characters"
    assert out[0].quantity == 11.0


def test_deepgram_usage_parser_zero_seconds_is_skipped():
    assert L.parse_deepgram_usage(0) == []
    assert L.parse_deepgram_usage(-1.0) == []


@pytest.mark.asyncio
async def test_flush_once_with_no_pool_keeps_buffer():
    L.record(_ev())
    L.record(_ev())
    written = await L._flush_once(pool=None)
    assert written == 0
    # Still in buffer for next attempt.
    assert L.buffer_size() == 2


@pytest.mark.asyncio
async def test_flush_once_writes_via_copy_records():
    L.record(_ev())
    L.record(_ev(provider="elevenlabs", provider_role="tts", unit="characters"))

    class _FakeConn:
        def __init__(self):
            self.calls = []
        async def execute(self, *a, **kw):
            self.calls.append(("execute", a, kw))
        async def copy_records_to_table(self, *a, **kw):
            self.calls.append(("copy", a, kw))

    fake = _FakeConn()

    class _FakePool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(_self):
                    return fake
                async def __aexit__(_self, *a):
                    return None
            return _Ctx()

    written = await L._flush_once(pool=_FakePool())
    assert written == 2
    assert L.buffer_size() == 0
    # Both the RLS bypass and the COPY ran.
    assert any(c[0] == "execute" and "bypass_rls" in c[1][0] for c in fake.calls)
    assert any(c[0] == "copy" for c in fake.calls)
