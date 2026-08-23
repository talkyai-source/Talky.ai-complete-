"""The menu must only offer models the account can actually serve.

2026-08-17: the curated Groq menu listed `llama-3.3-70b-versatile` and
`llama-3.1-8b-instant`. Both return HTTP 404 on this account — they are gone
from the platform, not deselected by us:

    $ GET https://api.groq.com/openai/v1/models
    13 models: allam-2-7b, canopylabs/orpheus-arabic-saudi,
    canopylabs/orpheus-v1-english, groq/compound, groq/compound-mini,
    meta-llama/llama-prompt-guard-2-22m, meta-llama/llama-prompt-guard-2-86m,
    openai/gpt-oss-120b, openai/gpt-oss-20b, openai/gpt-oss-safeguard-20b,
    qwen/qwen3.6-27b, whisper-large-v3, whisper-large-v3-turbo

    $ POST /chat/completions model=llama-3.1-8b-instant
    404 "The model `llama-3.1-8b-instant` does not exist or you do not have
         access to it."

Five tenant configs were pointing at the dead id. Production absorbed it —
LLM_SECONDARY_PROVIDER=gemini means those calls fail over rather than fail — but
"the tenant silently gets a different model than they chose" is not a state
anything should be able to reach through the UI.

These tests cannot call the network, so they cannot prove a model exists. What
they CAN do is stop the two ids that were verified dead from coming back, and
keep the menu honest about what it is offering.
"""
from __future__ import annotations

import pytest

from app.domain.models.ai_config import GROQ_MODELS, GroqModel

# Verified 404 on this account, 2026-08-17.
_RETIRED = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"}

# Verified present in GET /v1/models on this account, 2026-08-17. Not a claim
# that everything here is SUITABLE for voice — only that it resolves.
_AVAILABLE = {
    "allam-2-7b", "canopylabs/orpheus-arabic-saudi", "canopylabs/orpheus-v1-english",
    "groq/compound", "groq/compound-mini",
    "meta-llama/llama-prompt-guard-2-22m", "meta-llama/llama-prompt-guard-2-86m",
    "openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b",
    "qwen/qwen3.6-27b", "whisper-large-v3", "whisper-large-v3-turbo",
}

# Groq supports prompt caching on the GPT-OSS family only (Groq docs,
# console.groq.com/docs/prompt-caching, checked 2026-08-17). Measured on this
# account at a 7,281-token prompt: 102ms warm TTFT vs 672ms for Qwen.
_CACHING = {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b"}


def _menu_ids() -> set[str]:
    return {m.id for m in GROQ_MODELS}


def test_no_retired_model_is_offered():
    """THE REGRESSION. Offering an id the account cannot serve is worse than
    offering nothing: with failover on, the caller silently gets a different
    model than the tenant configured."""
    offered = _menu_ids() & _RETIRED
    assert not offered, f"menu offers models that 404 on this account: {offered}"


def test_no_retired_model_survives_in_the_enum():
    """The enum feeds config validation. A dead id left here would still be
    accepted on save even after the menu stopped showing it."""
    lingering = {m.value for m in GroqModel} & _RETIRED
    assert not lingering, f"GroqModel still defines retired ids: {lingering}"


def test_every_offered_model_exists_on_the_account():
    unknown = _menu_ids() - _AVAILABLE
    assert not unknown, (
        f"menu offers ids not present in GET /v1/models: {unknown}. "
        "Re-run the probe before adding a model."
    )


def test_the_menu_is_not_empty():
    """The failure mode of over-correcting: pruning dead ids until nothing is
    selectable would take every tenant's config down on save."""
    assert _menu_ids(), "no Groq model is offered at all"


def test_at_least_one_caching_model_is_offered():
    """Prompt caching is the only lever that removes prefill cost without
    touching the tuned prompt — measured 672ms -> 102ms. If the menu ever loses
    all of them again, that option quietly disappears from the product."""
    assert _menu_ids() & _CACHING, (
        "no prompt-caching model is selectable; prefill cost becomes unavoidable"
    )


def test_no_tenant_is_locked_out_by_the_narrowed_menu():
    """Hidden means "cannot pick it any more", NOT "cannot save" (2026-08-24).

    The menu narrowed to the MVP pair, but validation reads offered + hidden.
    Dropping an id that tenants still store would 400 them on a value they
    never chose to have — locking them out of their own settings page to
    enforce a menu change. `llama-3.1-8b-instant` is deliberately in the hidden
    list despite 404ing on the account: 5 tenants store it, and blocking their
    save does not repair them, it only traps them.
    """
    from app.domain.models.ai_config import GROQ_MODELS_HIDDEN

    accepted = set(_menu_ids()) | set(GROQ_MODELS_HIDDEN)
    for stored in ("qwen/qwen3.6-27b", "llama-3.1-8b-instant", "openai/gpt-oss-120b"):
        assert stored in accepted, (
            f"{stored} is stored by real tenants and must still validate"
        )


@pytest.mark.parametrize("model", sorted(_CACHING & {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}))
def test_the_offered_caching_model_is_production_ready(model):
    """The June "stacks questions / spells things out on voice" finding was
    RE-TESTED on 2026-08-24 against the current prompt and did not reproduce —
    gpt-oss scored 10/10 on the voice battery, including those two checks
    specifically. So the offered entry is no longer flagged preview.

    See docs/MODEL-SELECTION.md. Anything still hidden keeps whatever flag it
    had; this only governs what we actively offer.
    """
    entry = next((m for m in GROQ_MODELS if m.id == model), None)
    if entry is None:
        pytest.skip(f"{model} not offered")
    assert entry.is_preview is False, (
        f"{model} is offered but flagged preview — the June objection was "
        "re-tested and did not reproduce, so either un-flag it or stop "
        "offering it"
    )


def test_descriptions_disclose_the_caching_difference():
    """A menu whose entries differ by 6x in time-to-first-token has to say so,
    or the choice is being made blind."""
    for m in GROQ_MODELS:
        blurb = (m.description or "").lower()
        assert "cach" in blurb, f"{m.id} description says nothing about caching"
