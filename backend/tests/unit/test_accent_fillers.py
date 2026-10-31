"""Unit tests for accent-aware filler selection."""
import pytest

from app.services.scripts.prompts.accent_fillers import (
    AMERICAN, BRITISH, AUSTRALIAN, IRISH, INDIAN, NEUTRAL,
    normalize_accent, resolve_accent, accent_filler_block, filler_block_for_voice,
)


@pytest.mark.parametrize("raw,expected", [
    ("British", BRITISH),
    ("Received Pronunciation", BRITISH),
    ("en-GB", BRITISH),
    ("American", AMERICAN),
    ("en-US", AMERICAN),
    ("Australian", AUSTRALIAN),
    ("Irish", IRISH),
    ("Indian", INDIAN),
    ("Global", NEUTRAL),
    ("", NEUTRAL),
    (None, NEUTRAL),
    ("klingon", NEUTRAL),
])
def test_normalize_accent(raw, expected):
    assert normalize_accent(raw) == expected


@pytest.mark.parametrize("voice_id,expected", [
    ("en-GB-Chirp3-HD-Aoede", BRITISH),
    ("en-US-Chirp3-HD-Orus", AMERICAN),
    ("en-AU-Chirp3-HD-Puck", AUSTRALIAN),
    ("en-IN-Wavenet-A", INDIAN),
])
def test_resolve_accent_from_locale_in_id(voice_id, expected):
    assert resolve_accent(voice_id) == expected


def test_resolve_accent_from_cartesia_static_catalog():
    # James is a hardcoded British Cartesia voice.
    from app.domain.models.ai_config import CARTESIA_VOICES
    james = next((v for v in CARTESIA_VOICES if (v.accent or "").lower() == "british"), None)
    assert james is not None, "expected a British Cartesia voice in the catalog"
    assert resolve_accent(james.id) == BRITISH


def test_resolve_accent_from_elevenlabs_cache(monkeypatch):
    from app.domain.models.ai_config import VoiceInfo
    import app.services.scripts.prompts.accent_fillers as af

    fake = [VoiceInfo(id="el-british-123", name="Sloane", accent="British", provider="elevenlabs")]
    monkeypatch.setattr(
        "app.infrastructure.tts.elevenlabs_catalog.get_cached_elevenlabs_voices",
        lambda: fake,
    )
    assert af.resolve_accent("el-british-123") == BRITISH


def test_resolve_accent_unknown_is_neutral():
    assert resolve_accent("totally-unknown-voice-xyz") == NEUTRAL
    assert resolve_accent(None) == NEUTRAL


def test_british_block_uses_er_not_um():
    block = accent_filler_block(BRITISH)
    assert "er" in block and "erm" in block
    # British must steer away from the American spelling.
    assert "NEVER the American" in block


def test_american_block_uses_um():
    block = accent_filler_block(AMERICAN)
    assert '"um"' in block and '"uh"' in block


def test_blocks_instruct_full_dialect_not_just_fillers():
    # The LLM must write the WHOLE reply in-dialect, not only the fillers.
    for acc in (AMERICAN, BRITISH, AUSTRALIAN, IRISH, INDIAN):
        block = accent_filler_block(acc)
        assert "ENTIRE reply" in block, f"{acc} block must mandate full-dialect output"


def test_british_block_has_vocab_and_spelling():
    block = accent_filler_block(BRITISH)
    assert "mobile" in block            # vocabulary swap
    assert "colour" in block            # British spelling
    assert "cell phone" in block        # listed as something to AVOID


def test_american_block_has_vocab_and_spelling():
    block = accent_filler_block(AMERICAN)
    assert "color" in block             # American spelling
    assert "cell" in block              # American vocabulary


def test_neutral_block_is_empty():
    # Neutral / unknown -> no override (generic guardrails apply).
    assert accent_filler_block(NEUTRAL) == ""
    assert accent_filler_block("nonsense") == ""


@pytest.mark.asyncio
async def test_resolve_accent_async_warms_elevenlabs_cache(monkeypatch):
    """For an EL voice with a cold cache, the async resolver warms the catalog
    then resolves the accent."""
    import app.services.scripts.prompts.accent_fillers as af
    from app.domain.models.ai_config import VoiceInfo

    warmed = {"called": False}
    fake = [VoiceInfo(id="el-george", name="George", accent="British", provider="elevenlabs")]

    async def fake_fetch():
        warmed["called"] = True
        # Simulate the fetch populating the cache that _accent_from_catalogs reads.
        monkeypatch.setattr(
            "app.infrastructure.tts.elevenlabs_catalog.get_cached_elevenlabs_voices",
            lambda: fake,
        )
        return fake

    monkeypatch.setattr(
        "app.infrastructure.tts.elevenlabs_catalog.get_elevenlabs_voices_for_current_key",
        fake_fetch,
    )
    # cold cache initially
    monkeypatch.setattr(
        "app.infrastructure.tts.elevenlabs_catalog.get_cached_elevenlabs_voices",
        lambda: None,
    )

    acc = await af.resolve_accent_async("el-george", provider="elevenlabs")
    assert warmed["called"] is True
    assert acc == BRITISH


@pytest.mark.asyncio
async def test_resolve_accent_async_no_network_for_known_voice(monkeypatch):
    """A locale-coded id resolves without touching the EL catalog."""
    import app.services.scripts.prompts.accent_fillers as af

    def boom():
        raise AssertionError("should not fetch EL catalog for a locale-coded id")

    monkeypatch.setattr(
        "app.infrastructure.tts.elevenlabs_catalog.get_elevenlabs_voices_for_current_key",
        boom,
    )
    assert await af.resolve_accent_async("en-GB-Chirp3-HD-Aoede", provider="google") == BRITISH


def test_filler_block_for_voice_end_to_end():
    assert "British" in filler_block_for_voice("en-GB-Chirp3-HD-Aoede")
    assert filler_block_for_voice("unknown-xyz") == ""
