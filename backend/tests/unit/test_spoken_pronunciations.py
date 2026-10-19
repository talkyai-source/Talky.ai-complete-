"""Campaign pronunciations reach the voice, not only the AI prompt (2026-10-20).

The campaign's `pronunciations` slot used to become a prompt block only, so
the voice could still misread a name the AI spelled correctly. ElevenLabs and
Cartesia apply pronunciations at the voice layer; LiveKit's
text_transforms.replace() does it on the text sent to the voice, but matches
inside words ("API" would also hit "rAPId"). Here the swap is whole-word,
case-insensitive and longest-first, and applies only to what is spoken:
transcripts, history and speech-recognition context keep the real spelling.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.services.voice_pipeline.spoken_pronunciations import (
    apply_pronunciations,
    tts_pronunciation_pairs,
)


def test_whole_word_case_insensitive_swap():
    pairs = [("Talky", "Talkee"), ("API", "A P I")]
    assert apply_pronunciations("Welcome to talky. Our API is rapid.", pairs) == (
        "Welcome to Talkee. Our A P I is rapid."
    )


def test_longest_term_wins_and_replacement_is_literal():
    pairs = [("Allstate", "All state"), ("Allstate Estimation", "All state Es-ti-mation"), ("Dr", r"\1Doctor")]
    assert apply_pronunciations("Allstate Estimation and Allstate.", pairs) == (
        "All state Es-ti-mation and All state."
    )
    assert apply_pronunciations("Dr Khan", pairs) == r"\1Doctor Khan"


def test_no_pairs_is_a_no_op():
    assert apply_pronunciations("Hello there.", []) == "Hello there."
    assert apply_pronunciations("Hello there.", None) == "Hello there."


def test_only_sayable_spellings_reach_the_voice():
    # IPA would be read aloud symbol by symbol; it stays prompt-only.
    pairs = tts_pronunciation_pairs({
        "Talky": "Talkee",
        "Nguyen": "/ŋwɪən/",
        "Siobhan": "shi-VAWN",
        " ": "ignored",
    })
    assert pairs == [("Talky", "Talkee"), ("Siobhan", "shi-VAWN")]
    assert tts_pronunciation_pairs([{"name": "Aoife", "say": "EE-fa"}, {"name": "X", "ipa": "ɛks"}]) == [
        ("Aoife", "EE-fa")
    ]
    assert tts_pronunciation_pairs(None) == []
    assert tts_pronunciation_pairs("not a mapping") == []


@pytest.mark.asyncio
async def test_the_voice_hears_the_spelling_but_recognition_keeps_the_name(monkeypatch):
    from app.domain.services.voice_pipeline_service import VoicePipelineService
    from tests.unit.test_voice_pipeline_service import _make_session

    tts_provider = AsyncMock()
    seen = []

    async def _stream(text, **kwargs):
        seen.append(text)
        yield MagicMock(data=b"\x00" * 3200)

    tts_provider.stream_synthesize = _stream
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=AsyncMock(), tts_provider=tts_provider,
        media_gateway=media_gateway, mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    context = AsyncMock()
    monkeypatch.setattr("app.domain.services.stt_context.update_stt_agent_context", context)
    session = _make_session()
    session.tts_active = True
    session.tts_pronunciations = [("Talky", "Talkee")]

    await service.synthesize_and_send_audio(session, "Thanks for calling Talky.", AsyncMock())

    assert seen == ["Thanks for calling Talkee."]
    # Speech recognition is primed with what the agent meant to say.
    context.assert_awaited_once()
    assert context.await_args.args[2] == "Thanks for calling Talky."


def test_the_call_config_carries_the_campaign_pronunciations(monkeypatch):
    from app.domain.services.telephony_session_config import build_telephony_session_config
    from tests.unit.test_telephony_session_config import TestBuildTelephonySessionConfig

    campaign = {
        "id": "pron-campaign",
        "script_config": {
            "persona_type": "lead_gen",
            "company_name": "Talky",
            "agent_names": ["Alex"],
            "campaign_slots": {"industry": "software", "pronunciations": {"Talky": "Talkee"}},
        },
    }
    monkeypatch.setenv("TELEPHONY_PROMPT_STRICT_MODE", "0")
    with patch(
        "app.domain.services.telephony_session_config.get_global_config",
        return_value=TestBuildTelephonySessionConfig()._mock_global_config(),
    ):
        config = build_telephony_session_config(campaign=campaign)
    assert list(config.tts_pronunciations) == [("Talky", "Talkee")]
