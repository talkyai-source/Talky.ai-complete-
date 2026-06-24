"""Tests for the PER-PROVIDER TTS expressive-cue registry.

The old model was binary: eleven_v3 kept tags, EVERY other engine had all tags
stripped — so on Cartesia / Deepgram / ElevenLabs-flash (the engines actually
live) every gesture was stripped → flat robotic read. This makes it per-provider
and default-deny: each engine keeps ONLY the inline tags it performs, so a cue
can never be read aloud on an engine that can't perform it.

LOCAL ONLY — not committed.
"""
from app.domain.services.voice_pipeline.expressive_caps import (
    expressive_profile,
    strip_unsupported_audio_tags,
)


# ── profile mapping ──────────────────────────────────────────────────────────

def test_profile_mapping():
    assert expressive_profile("eleven_v3").name == "eleven_v3"
    assert expressive_profile("eleven_v3").allow_all_tags is True
    assert expressive_profile("sonic-3").name == "cartesia"
    assert expressive_profile("sonic-2").name == "cartesia"
    assert expressive_profile("eleven_flash_v2_5").name == "none"
    assert expressive_profile("aura-2-andromeda-en").name == "none"
    assert expressive_profile("").name == "none"
    assert expressive_profile(None).name == "none"


# ── eleven_v3: keep everything (the only full-surface engine) ────────────────

def test_eleven_v3_keeps_all_tags():
    t = "That's great [laughs] — and [sighs] I understand. [whispers] between us."
    assert strip_unsupported_audio_tags(t, "eleven_v3") == t


# ── cartesia: keep [laughter], strip the rest ────────────────────────────────

def test_cartesia_keeps_laughter_strips_others():
    out = strip_unsupported_audio_tags(
        "Oh [laughter] that's funny. [sighs] but [excited] let's go [pause] now.",
        "sonic-3",
    )
    assert "[laughter]" in out               # performed by Cartesia
    assert "[sighs]" not in out              # would be read aloud → stripped
    assert "[excited]" not in out
    assert "[pause]" not in out
    assert "that's funny." in out and "let's go" in out  # spoken words survive


def test_cartesia_keeps_laughs_variant():
    assert "[laughs]" in strip_unsupported_audio_tags("ha [laughs] ok", "sonic-3")


# ── deepgram / flash / unknown: strip ALL (never leak) ───────────────────────

def test_deepgram_strips_all_tags():
    out = strip_unsupported_audio_tags(
        "Sure [laughs] [laughter] [sighs] done.", "aura-2-andromeda-en",
    )
    assert "[" not in out


def test_elevenlabs_flash_strips_all_tags():
    # flash is NOT eleven_v3 → tags would be read aloud → strip everything.
    out = strip_unsupported_audio_tags("Hi [laughs] [laughter] there.", "eleven_flash_v2_5")
    assert "[" not in out


def test_unknown_model_strips_all():
    assert strip_unsupported_audio_tags("[laughter] hi", "mystery-tts") == " hi"


# ── plain-word fillers (um/hmm) are never bracketed → never touched ──────────

def test_plain_fillers_untouched_on_every_engine():
    t = "Um, hmm, ah — let me think."
    for model in ("eleven_v3", "sonic-3", "aura-2-andromeda-en", "eleven_flash_v2_5"):
        assert strip_unsupported_audio_tags(t, model) == t


def test_no_brackets_is_noop():
    assert strip_unsupported_audio_tags("just plain text", "sonic-3") == "just plain text"


# ── integration: clean_response routes through the per-provider strip ─────────

def test_clean_response_per_provider_strip():
    from app.domain.services.llm_guardrails import get_guardrails
    g = get_guardrails()
    txt = "Hello [laughter] there. [sighs] ok."
    out_cart = g.clean_response(txt, tts_model_id="sonic-3")
    assert "[laughter]" in out_cart and "[sighs]" not in out_cart
    out_dg = g.clean_response(txt, tts_model_id="aura-2-andromeda-en")
    assert "[" not in out_dg
    out_v3 = g.clean_response(txt, tts_model_id="eleven_v3")
    assert "[laughter]" in out_v3 and "[sighs]" in out_v3
