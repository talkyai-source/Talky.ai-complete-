"""The voice first-token deadline is set from measured production turns.

194 turns over 14 days to 2026-09-23 (primary Cerebras gpt-oss-120b, secondary
Groq gpt-oss-20b): primary first token <=1.0s 180, 1.0-1.5s 4, 1.5-2.0s 0,
2.0-2.5s 2, >2.5s 8 (failovers). At 2500ms every failover turn waited the full
deadline in silence and landed at 3.3-3.8s - most of the slow tail. 1500ms makes
those ~1s faster and makes no turn slower, because nothing landed between 1.5
and 2.0s. Below ~1.5s it would start failing over the 1.0-1.5s turns.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain.services.resilient_llm import LLMFailoverPolicy

_SRC = (
    Path(__file__).resolve().parents[2]
    / "app" / "domain" / "services" / "voice_orchestrator.py"
).read_text(encoding="utf-8")


def _default_ms() -> float:
    m = re.search(r'os\.getenv\("LLM_FIRST_TOKEN_DEADLINE_MS",\s*"(\d+)"\)', _SRC)
    assert m, "the voice path must read LLM_FIRST_TOKEN_DEADLINE_MS"
    return float(m.group(1))


def test_voice_default_is_the_measured_value():
    assert _default_ms() == 1500


@pytest.mark.parametrize("bound", [1000, 2500])
def test_default_stays_inside_the_band_the_evidence_supports(bound):
    # Below 1000ms it would fail over healthy turns; at 2500ms it was the
    # slow tail. Changing it outside this band needs a new measurement.
    ms = _default_ms()
    assert (ms > bound) if bound == 1000 else (ms < bound)


def test_the_value_reaches_the_policy_as_seconds():
    policy = LLMFailoverPolicy(first_token_deadline_seconds=max(0.3, _default_ms() / 1000.0))
    assert policy.first_token_deadline_seconds == 1.5
