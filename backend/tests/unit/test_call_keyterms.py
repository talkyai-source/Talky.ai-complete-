"""Unit tests for per-call Deepgram Flux keyterm building + brand correction."""
from app.domain.services.telephony_session_config import _build_call_keyterms
from app.services.scripts.prompts.composer import brand_correction_line


def test_company_and_agent_included():
    terms = _build_call_keyterms("Dojo", "Alex")
    lower = [t.lower() for t in terms]
    assert "dojo" in lower
    assert "alex" in lower


def test_multiword_brand_split_into_words():
    # "Dojo" must appear as its own keyterm even inside a longer legal name,
    # because that's the word the caller actually says (and Flux mis-hears).
    terms = _build_call_keyterms("Dojo Payments Ltd", "Sam")
    lower = [t.lower() for t in terms]
    assert "dojo payments ltd" in lower   # full name
    assert "dojo" in lower                 # significant word
    assert "payments" in lower


def test_dedup_case_insensitive():
    terms = _build_call_keyterms("Dojo", "dojo")
    # "Dojo" and "dojo" collapse to one entry.
    assert sum(1 for t in terms if t.lower() == "dojo") == 1


def test_defaults_are_merged(monkeypatch):
    monkeypatch.setattr(
        "app.domain.services.voice_orchestrator._default_flux_keyterms",
        lambda: ["estimating", "contractor"],
    )
    terms = _build_call_keyterms("Dojo", "Alex")
    lower = [t.lower() for t in terms]
    assert "dojo" in lower            # campaign term
    assert "estimating" in lower      # static default merged in


def test_empty_inputs_safe():
    # No company/agent -> still returns the defaults without crashing.
    terms = _build_call_keyterms("", "")
    assert isinstance(terms, list)


def test_capped_length(monkeypatch):
    monkeypatch.setattr(
        "app.domain.services.voice_orchestrator._default_flux_keyterms",
        lambda: [f"term{i}" for i in range(200)],
    )
    terms = _build_call_keyterms("Dojo", "Alex")
    assert len(terms) <= 60
    # Campaign terms are kept (prepended before the cap).
    assert "Dojo" in terms


# --- brand-correction guardrail -------------------------------------------

def test_brand_correction_includes_company_name():
    # b4d25876: brand_correction_line is a deliberate empty stub (composer.py:142-144);
    # the brand is stated once, in the conversation guide ("You are <agent>, an AI assistant for <company>").
    assert brand_correction_line("Dojo") == ""
    from app.services.scripts.prompts.composer import compose_prompt
    from tests.unit.test_prompt_composer import LEAD_GEN_SLOTS
    assert "Dojo" in compose_prompt("lead_gen", "Alex", "Dojo", LEAD_GEN_SLOTS)


def test_brand_correction_is_per_campaign():
    # Different campaigns -> different brand in the composed prompt, no hardcoding.
    from app.services.scripts.prompts.composer import compose_prompt
    from tests.unit.test_prompt_composer import LEAD_GEN_SLOTS
    prompt = compose_prompt("lead_gen", "Alex", "Acme Roofing", LEAD_GEN_SLOTS)
    assert "Acme Roofing" in prompt and "Dojo" not in prompt


def test_brand_correction_empty_company_is_blank():
    assert brand_correction_line("") == ""
    assert brand_correction_line("   ") == ""
    assert brand_correction_line(None) == ""  # type: ignore[arg-type]
