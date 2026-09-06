"""The voice pipeline tuned for the two production models — Cerebras
gpt-oss-120b and Groq openai/gpt-oss-20b — after the 2026-09-06 audit.

Covers: the Cerebras reasoning reserve (F01), two-way failover between the
pair (F06), the campaign cache key on every turn path (F10) and the knowledge
entry fitting that keeps a node's spoken answer when its source is trimmed.
"""
from __future__ import annotations

import inspect

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_orchestrator import VoiceOrchestrator
from app.domain.services.voice_pipeline import kb_budget
from app.domain.services.voice_pipeline.kb_budget import fit_kb_body
from app.infrastructure.llm import cerebras as cerebras_module
from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.services.scripts.knowledge.retrieval import render_node_answer


# ---------------------------------------------------------------------------
# Cerebras: reasoning reserve
# ---------------------------------------------------------------------------

def _provider(model: str, max_tokens: int = 90) -> CerebrasLLMProvider:
    p = CerebrasLLMProvider()
    p._model = model
    p._temperature = 0.6
    p._max_tokens = max_tokens
    return p


def test_gpt_oss_gets_the_reasoning_reserve_on_top_of_the_answer_budget():
    req = _provider("gpt-oss-120b")._build_request(
        messages=[Message(role=MessageRole.USER, content="hi")],
        system_prompt="s", temperature=None, max_tokens=90, model=None, tools=None,
    )
    assert req["reasoning_effort"] == "low"
    assert req["max_completion_tokens"] == 90 + cerebras_module._THINKING_RESERVE_TOKENS


def test_confirmation_probe_budget_is_no_longer_three_tokens_total():
    """confirm_llm asks for max_tokens=3; on Cerebras that returned an EMPTY
    'length' completion because reasoning consumed the whole cap."""
    req = _provider("gpt-oss-120b")._build_request(
        messages=[Message(role=MessageRole.USER, content="yes or no?")],
        system_prompt="s", temperature=0.0, max_tokens=3, model=None, tools=None,
    )
    assert req["max_completion_tokens"] >= 3 + 1024


def test_models_with_reasoning_off_keep_the_exact_budget():
    req = _provider("gemma-4-31b")._build_request(
        messages=[Message(role=MessageRole.USER, content="hi")],
        system_prompt="s", temperature=None, max_tokens=120, model=None, tools=None,
    )
    assert req["reasoning_effort"] == "none"
    assert req["max_completion_tokens"] == 120


# ---------------------------------------------------------------------------
# Failover pairing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "primary, env, expected",
    [
        # prod today: Cerebras primary, env secondary = Groq 20B
        (("cerebras", "gpt-oss-120b"), ("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b")),
        # tenant picks Groq 20B while env still names Groq 20B -> counterpart
        (("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b"), ("cerebras", "gpt-oss-120b")),
        # no env at all: same-provider default equals the primary -> counterpart
        (("groq", "openai/gpt-oss-20b"), (None, None), ("cerebras", "gpt-oss-120b")),
        (("cerebras", "gpt-oss-120b"), (None, None), ("groq", "openai/gpt-oss-20b")),
        # explicit different secondary is honoured verbatim
        (("cerebras", "gpt-oss-120b"), ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash")),
    ],
)
def test_secondary_selection_pairs_the_two_models_both_ways(primary, env, expected):
    picked = VoiceOrchestrator._pick_secondary_llm(
        primary_provider=primary[0], primary_model=primary[1],
        env_provider=env[0], env_model=env[1],
    )
    assert picked == expected


def test_secondary_selection_gives_up_only_when_nothing_distinct_exists():
    # Unknown provider, env points at itself, no counterpart known.
    assert VoiceOrchestrator._pick_secondary_llm(
        primary_provider="mystery", primary_model="m1", env_provider="mystery", env_model="m1",
    ) is None


# ---------------------------------------------------------------------------
# Cache key on every turn path
# ---------------------------------------------------------------------------

def test_turn_streamer_threads_the_campaign_cache_key_on_both_llm_paths():
    from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer

    src = inspect.getsource(TurnStreamer)
    assert src.count('campaign_id=getattr(session, "campaign_id", None)') >= 2


# ---------------------------------------------------------------------------
# Knowledge entry fitting
# ---------------------------------------------------------------------------

_RATES_NODE = {
    "heading": '"What are your rates?"',
    "content": (
        "CRITICAL RULE: NEVER quote a specific rate without knowing monthly volume. "
        "Rates are not publicly published. This is by design - your rate depends on: "
        "- Monthly card turnover - Card mix (debit vs credit vs corporate vs international) "
        "- Business type - Contract type. "
        "Script: Good question, and I want to be straight with you. Dojo does not publish "
        "a single rate because it depends on your monthly volume and card mix. "
        "Typical blended rates land between 1.2% and 1.9% for most restaurants."
    ),
    "voice_answer": "We don't publish a single rate as it depends on your monthly volume and card mix. I can get the right number for your setup.",
}


def test_fit_keeps_the_spoken_answer_when_the_source_is_trimmed():
    rendered = render_node_answer(_RATES_NODE)
    body = fit_kb_body(rendered, _RATES_NODE, 350)
    assert len(body) <= 350 + 2
    assert "…" in body                                   # the model is told the source was cut
    assert "depends on your monthly volume" in body      # the answer itself survived
    # The old path (trim only) lost it:
    assert "depends on your monthly volume" not in kb_budget._trim_kb_body(rendered, 350)


def test_fit_leaves_short_nodes_untouched():
    node = {"content": "We are open 9 to 5.", "voice_answer": "Open nine to five."}
    rendered = render_node_answer(node)
    assert fit_kb_body(rendered, node, 600) == rendered.replace("\n", " ")


def test_fit_without_a_voice_answer_degrades_to_the_plain_trim():
    node = {"content": "x " * 400}
    assert fit_kb_body(render_node_answer(node), node, 100) == kb_budget._trim_kb_body("x " * 400, 100)


def test_budget_defaults_fit_the_live_rates_node():
    # 600 chars of a 1,501-char node reaches the script; 350 did not.
    assert kb_budget._KB_CHUNK_CHARS >= 600
    assert kb_budget._KB_TOTAL_CHARS >= 3 * 600 + 200 or kb_budget._KB_TOTAL_CHARS >= 2000
