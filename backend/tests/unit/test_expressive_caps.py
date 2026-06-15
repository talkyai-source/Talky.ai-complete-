"""Unit tests for the TTS expressive-capability gate: audio tags must be
performed only by capable voices and physically stripped for the rest, while
plain-word fillers (um/hmm/ah) always survive."""
from app.domain.services.voice_pipeline import expressive_caps as ec
from app.domain.services.llm_guardrails import get_guardrails


# ── capability registry ───────────────────────────────────────────
def test_supports_audio_tags():
    assert ec.supports_audio_tags("eleven_v3") is True
    assert ec.supports_audio_tags("ELEVEN_V3") is True   # case-insensitive
    assert ec.supports_audio_tags("eleven_flash_v2_5") is False
    assert ec.supports_audio_tags("sonic-3") is False
    assert ec.supports_audio_tags("") is False
    assert ec.supports_audio_tags(None) is False


# ── strip_audio_tags ──────────────────────────────────────────────
def test_strip_removes_bracket_tags():
    out = ec.strip_audio_tags("[laughs] That's great. [pause] Let me check.")
    assert "[laughs]" not in out and "[pause]" not in out
    assert "That's great." in out and "Let me check." in out


def test_strip_keeps_plain_fillers():
    # Plain-word fillers are NOT bracketed → must survive.
    s = "Um, hmm, yeah — let me think. Ah, got it."
    assert ec.strip_audio_tags(s) == s


def test_strip_noop_without_brackets():
    assert ec.strip_audio_tags("No tags here at all.") == "No tags here at all."
    assert ec.strip_audio_tags("") == ""


# ── clean_response gating ─────────────────────────────────────────
def test_clean_response_strips_tags_by_default():
    g = get_guardrails()
    out = g.clean_response("[laughs] Hello there, [sighs] how are you?")
    assert "[laughs]" not in out and "[sighs]" not in out
    assert "Hello there" in out and "how are you?" in out


def test_clean_response_preserves_tags_when_allowed():
    g = get_guardrails()
    out = g.clean_response("[laughs] Hello there!", preserve_audio_tags=True)
    assert "[laughs]" in out


def test_clean_response_keeps_fillers_either_way():
    g = get_guardrails()
    for preserve in (True, False):
        out = g.clean_response("Hmm, yeah — let me see. Oh, got it.", preserve_audio_tags=preserve)
        assert "Hmm" in out and "yeah" in out and "got it" in out


def test_clean_response_markdown_links_still_collapse():
    # Links must become plain text (not be treated as / stripped like tags).
    g = get_guardrails()
    out = g.clean_response("Check [our site](https://x.com) for details.")
    assert "our site" in out and "https://x.com" not in out
