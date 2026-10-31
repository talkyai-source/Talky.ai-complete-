"""The knowledge budget must know the models the product actually runs, and a
call must say in the log whether the knowledge layer was switched on.

Two defects found while diagnosing a live "the agent does not know what I
uploaded" report on 2026-09-22:

1. budget.py carried its OWN copy of the model context windows. Every id in it
   had been removed from the product on 2026-09-07, so every live model fell
   through to the 8192-token default and each upload was budgeted as if it ran
   an 8k model. Worse, the upload endpoint read only the optional per-campaign
   override column, which is null for every campaign nothing has explicitly set
   it on, so the budget usually received no model at all.

2. Every refusal in the session-inject path was a silent return, and a campaign
   in plain retrieve mode logs nothing until a caller happens to ask a matching
   question. "Is knowledge even on for this call?" could not be answered from
   the logs.
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from app.services.scripts.knowledge import session_inject
from app.services.scripts.knowledge.budget import (
    _DEFAULT_CONTEXT_WINDOW,
    INLINE_BAKE_MAX_CHARS,
    choose_mode,
    context_window_for,
    inline_budget_for,
    max_inline_tokens,
    tokens_for_chars,
)

# --------------------------------------------------------------------------
# 1. the budget knows the live menu
# --------------------------------------------------------------------------


def test_a_model_declared_in_the_menu_uses_its_declared_window():
    # openai/gpt-oss-20b is the Groq fallback the product actually runs and the
    # menu declares 131072 for it. The old local table did not list it at all.
    assert context_window_for("openai/gpt-oss-20b") == 131072


def test_the_provider_prefix_is_optional():
    assert context_window_for("gpt-oss-20b") == context_window_for(
        "openai/gpt-oss-20b"
    )


def test_an_unknown_model_keeps_the_conservative_default():
    # Never invent a window for a model nobody declared.
    assert context_window_for("some-model-nobody-declared") == _DEFAULT_CONTEXT_WINDOW
    assert context_window_for(None) == _DEFAULT_CONTEXT_WINDOW


def test_the_cerebras_primary_is_declared_and_no_longer_falls_back():
    # This is the model every production tenant runs. Verified 2026-09-22 from
    # the Cerebras docs: 65k context on the free tier, 131k on paid. We declare
    # the free-tier floor so the number holds whatever the billing state is.
    # Before this it fell through to 8192 and the budget treated the primary as
    # an 8k model.
    assert context_window_for("gpt-oss-120b") == 65536
    assert context_window_for("gpt-oss-120b") != _DEFAULT_CONTEXT_WINDOW


def test_an_undeclared_menu_entry_would_still_fall_back_not_guess():
    # The rule that protected the Cerebras entry before it was verified still
    # stands for anything nobody has measured.
    assert context_window_for("cerebras-model-nobody-declared") == (
        _DEFAULT_CONTEXT_WINDOW
    )


def test_legacy_ids_still_resolve_through_the_old_table():
    # A tenant row stored before the menu changed must not regress to 8k.
    # llama-3.1-70b is gone from the menu entirely, so only the legacy table
    # can answer for it.
    assert context_window_for("llama-3.1-70b-versatile") == 131072


def test_the_menu_wins_over_the_legacy_table():
    # Both know gemini-2.5-flash. The menu is the source of truth and carries
    # the exact window, so its value must be the one used.
    assert context_window_for("gemini-2.5-flash") == 1_048_576


# --------------------------------------------------------------------------
# 2. a huge window must not inline a huge prompt
# --------------------------------------------------------------------------


def test_the_inline_ceiling_is_exactly_what_the_bake_can_emit():
    # The ceiling is not a latency knob and does not shrink any prompt: the
    # bake was ALWAYS truncated at INLINE_BAKE_MAX_CHARS. Tying the budget to
    # that number is what stops a big window marking a knowledge base "inline"
    # and then silently serving only the first 12 KB of it -- inline being the
    # one mode that runs no per-turn retrieval to recover the rest.
    assert max_inline_tokens() == tokens_for_chars(INLINE_BAKE_MAX_CHARS)
    assert max_inline_tokens() == 3000


def test_a_large_window_is_capped_rather_than_inlining_everything():
    cap = max_inline_tokens()
    assert inline_budget_for("gemini-2.5-flash") == cap
    assert inline_budget_for("openai/gpt-oss-20b") == cap
    assert inline_budget_for("gpt-oss-120b") == cap


def test_a_small_window_is_still_bounded_by_the_window_not_the_cap():
    budget = inline_budget_for(None)
    assert 0 < budget < max_inline_tokens()


def test_the_cap_is_tunable_without_a_redeploy(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_INLINE_TOKENS", "100")
    assert max_inline_tokens() == 100
    assert inline_budget_for("gemini-2.5-flash") == 100


@pytest.mark.parametrize("bad", ["", "not-a-number", "0", "-5"])
def test_a_nonsense_cap_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setenv("KNOWLEDGE_MAX_INLINE_TOKENS", bad)
    assert max_inline_tokens() == tokens_for_chars(INLINE_BAKE_MAX_CHARS)


def test_an_empty_knowledge_base_is_still_mode_none():
    assert choose_mode(0, "openai/gpt-oss-20b") == "none"


def test_modes_widen_as_the_knowledge_base_grows():
    model = "openai/gpt-oss-20b"
    budget = inline_budget_for(model)
    assert choose_mode(budget, model) == "inline"
    assert choose_mode(budget + 1, model) == "map_retrieve"
    assert choose_mode(budget * 4 + 1, model) == "retrieve"


# --------------------------------------------------------------------------
# 3. every call says whether knowledge was wired
# --------------------------------------------------------------------------


async def _fake_compact_tree(pool, tenant_id, campaign_id, *, skeleton_only=False, max_chars=12000):
    return "SKELETON_TOC" if skeleton_only else "FULL_TREE_BODY"


def _session(**kw):
    base = dict(
        call_id="call-abcdef123456",
        system_prompt="PERSONA",
        campaign_id="c1",
        tenant_id=None,
        knowledge_mode=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _kb_setup_lines(caplog):
    return [r.message for r in caplog.records if r.message.startswith("KB_SETUP")]


def test_outbound_says_why_it_is_off_when_the_feature_flag_is_off(monkeypatch, caplog):
    monkeypatch.delenv("CAMPAIGN_KNOWLEDGE_ENABLED", raising=False)
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        asyncio.run(
            session_inject.apply_campaign_knowledge(
                _session(), {"knowledge_mode": "inline", "tenant_id": "t1", "id": "c1"},
                pool=object(),
            )
        )
    lines = _kb_setup_lines(caplog)
    assert len(lines) == 1
    assert "OFF" in lines[0] and "reason=feature_flag_off" in lines[0]
    assert "path=outbound" in lines[0]


def test_outbound_says_why_it_is_off_when_the_campaign_mode_is_none(monkeypatch, caplog):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        asyncio.run(
            session_inject.apply_campaign_knowledge(
                _session(), {"knowledge_mode": "none", "tenant_id": "t1", "id": "c1"},
                pool=object(),
            )
        )
    lines = _kb_setup_lines(caplog)
    assert len(lines) == 1
    assert "reason=knowledge_mode_none" in lines[0]


def test_outbound_reports_on_for_a_live_campaign(monkeypatch, caplog):
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(session_inject, "compact_tree", _fake_compact_tree)
    cs = _session()
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        asyncio.run(
            session_inject.apply_campaign_knowledge(
                cs, {"knowledge_mode": "inline", "tenant_id": "t1", "id": "c1"},
                pool=object(),
            )
        )
    line = [ln for ln in _kb_setup_lines(caplog) if " ON " in ln][0]
    assert "effective=inline" in line and "configured=inline" in line
    assert cs.knowledge_mode == "inline"


def test_inbound_says_the_flag_was_off_at_admission(caplog):
    cs = _session()
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        session_inject.apply_pinned_campaign_knowledge(cs, {"enabled": False})
    line = _kb_setup_lines(caplog)[0]
    assert "reason=feature_flag_off" in line and "path=inbound" in line


def test_inbound_says_the_campaign_mode_was_none(caplog):
    cs = _session()
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        session_inject.apply_pinned_campaign_knowledge(
            cs, {"enabled": True, "mode": "none"}
        )
    line = _kb_setup_lines(caplog)[0]
    assert "reason=knowledge_mode_none" in line


def test_inbound_warns_when_the_mode_is_on_but_nothing_was_pinned(caplog):
    # The shape that looks healthy in the database and answers nothing on the
    # phone: a mode is set but no node reached the snapshot.
    cs = _session()
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        session_inject.apply_pinned_campaign_knowledge(
            cs,
            {
                "enabled": True,
                "mode": "retrieve",
                "nodes": [],
                "tenant_id": "t1",
                "campaign_id": "c1",
            },
        )
    lines = _kb_setup_lines(caplog)
    assert any("reason=no_nodes_pinned" in ln for ln in lines)
    assert any(" ON " in ln and "nodes=0" in ln for ln in lines)


def test_inbound_reports_on_with_the_node_count(caplog):
    cs = _session()
    with caplog.at_level(logging.INFO, logger=session_inject.logger.name):
        session_inject.apply_pinned_campaign_knowledge(
            cs,
            {
                "enabled": True,
                "mode": "retrieve",
                "nodes": [{"heading": "Pricing", "content": "x"}],
                "tenant_id": "t1",
                "campaign_id": "c1",
            },
        )
    line = [ln for ln in _kb_setup_lines(caplog) if " ON " in ln][0]
    assert "effective=retrieve" in line and "nodes=1" in line
    assert cs.knowledge_mode == "retrieve"


def test_the_setup_log_never_breaks_a_call():
    # A session object missing call_id entirely must not raise.
    session_inject._log_setup(object(), "OFF", reason="x")


# --------------------------------------------------------------------------
# 4. the two writers of campaigns.knowledge_mode must agree
# --------------------------------------------------------------------------


def test_both_writers_of_knowledge_mode_resolve_the_model_the_same_way():
    """Guard, not proof: publishing an upload and deleting a source are the
    only two writers of campaigns.knowledge_mode. They budget with choose_mode,
    so if they resolve the model differently they disagree, and deleting one
    source can demote a campaign the upload path had just promoted with no
    change to explain it. One shared statement makes that impossible.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "app" / "api" / "v1" / "endpoints" / "campaign_knowledge.py"
    ).read_text(encoding="utf-8")

    # Exactly one place builds the statement, and every fetch of the budget
    # model goes through it.
    assert source.count("_KNOWLEDGE_BUDGET_MODEL_SQL = ") == 1
    assert source.count("_KNOWLEDGE_BUDGET_MODEL_SQL,") == 2
    # and the old single-column read is gone from both writers
    assert "SELECT knowledge_model FROM campaigns " not in source


def test_the_shared_statement_falls_back_to_the_tenant_model():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "app" / "api" / "v1" / "endpoints" / "campaign_knowledge.py"
    ).read_text(encoding="utf-8")
    start = source.index("_KNOWLEDGE_BUDGET_MODEL_SQL = ")
    stmt = source[start:start + 400]
    assert "c.knowledge_model" in stmt
    assert "a.llm_model" in stmt
    assert "tenant_ai_configs" in stmt
    # both halves stay tenant-scoped
    assert stmt.count("tenant_id = $2") == 2
