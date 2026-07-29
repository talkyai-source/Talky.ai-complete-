"""The agent NAME must match the gender of the VOICE the callee hears.

Regression for the production report "male voices are picking female names"
(a male TTS voice introducing itself as "Sarah").

Root cause: build_telephony_session_config picked the agent name with the
gender-BLIND `pick_agent_name` (and a mixed-gender `random.choice(AGENT_NAMES)`
fallback), so any path that did NOT pass an agent_name_override — inbound
calls, the campaign "Test agent" WS, campaigns with no configured name pool —
paired a name with a voice by coin flip.

These tests pin the fix at the ONE place every telephony/browser session is
built, so they cover every one of those paths at once.
"""
from __future__ import annotations

from app.domain.services.telephony_session_config import (
    AGENT_NAMES,
    _FEMALE_AGENT_NAMES,
    _MALE_AGENT_NAMES,
    agent_name_voice_mismatch,
    build_telephony_session_config,
)
from app.domain.services.voice_orchestrator import Direction

MALE_VOICE = "aura-2-apollo-en"
FEMALE_VOICE = "aura-2-amalthea-en"


def _cfg(campaign, **kw):
    return build_telephony_session_config(
        gateway_type="telephony",
        campaign=campaign,
        direction=Direction.OUTBOUND,
        **kw,
    )


def _campaign(voice_id, *, agent_names=None, genders=None):
    # knowledge_driven → no strict per-persona slot requirements; this test is
    # about name/voice pairing, not prompt slot completeness.
    script: dict = {"company_name": "Acme", "knowledge_driven": True}
    if agent_names is not None:
        script["agent_names"] = agent_names
    if genders is not None:
        script["agent_name_genders"] = genders
    return {"id": "camp-1", "voice_id": voice_id, "script_config": script}


# ── the built-in fallback (no configured name pool) ──────────────────
def test_no_pool_male_voice_never_picks_a_female_name():
    # THE reported bug. Repeat: the old code was a coin flip over a
    # mixed-gender list, so a single run could pass by luck.
    for _ in range(60):
        cfg = _cfg(_campaign(MALE_VOICE))
        assert cfg.agent_config.agent_name in _MALE_AGENT_NAMES


def test_no_pool_female_voice_never_picks_a_male_name():
    for _ in range(60):
        cfg = _cfg(_campaign(FEMALE_VOICE))
        assert cfg.agent_config.agent_name in _FEMALE_AGENT_NAMES


def test_unknown_voice_keeps_the_legacy_mixed_fallback():
    # An uncatalogued voice must not start failing or guessing.
    cfg = _cfg(_campaign("some-unknown-voice-id"))
    assert cfg.agent_config.agent_name in AGENT_NAMES


def test_gendered_fallback_lists_partition_agent_names():
    # Documented invariant: every built-in name is classified exactly once.
    assert sorted(_MALE_AGENT_NAMES + _FEMALE_AGENT_NAMES) == sorted(AGENT_NAMES)
    assert not set(_MALE_AGENT_NAMES) & set(_FEMALE_AGENT_NAMES)


# ── a configured pool: the OPERATOR's names always win ───────────────
def test_pool_prefers_the_name_matching_the_voice():
    camp = _campaign(
        MALE_VOICE,
        agent_names=["Sarah", "James"],
        genders={"Sarah": "female", "James": "male"},
    )
    for _ in range(30):
        assert _cfg(camp).agent_config.agent_name == "James"


def test_pool_wins_even_when_nothing_matches_the_voice():
    # Never invent a name the campaign did not configure (the "Emily" bug):
    # an all-female pool on a male voice still uses the configured names.
    camp = _campaign(FEMALE_VOICE, agent_names=["James"], genders={"James": "male"})
    assert _cfg(camp).agent_config.agent_name == "James"


def test_untagged_pool_is_inferred_against_the_voice():
    # No gender tags saved (the classic campaign form never sent them) —
    # inference still steers the pick toward the voice.
    camp = _campaign(MALE_VOICE, agent_names=["Sarah", "James"])
    for _ in range(30):
        assert _cfg(camp).agent_config.agent_name == "James"


def test_explicit_override_is_respected_unchanged():
    # The dialer's durable per-job name must survive retries untouched.
    camp = _campaign(MALE_VOICE, agent_names=["James"])
    cfg = _cfg(camp, agent_name_override="Sarah")
    assert cfg.agent_config.agent_name == "Sarah"


def test_campaign_without_voice_id_still_builds():
    # Falls back to the tenant/global voice; must not raise.
    cfg = _cfg(_campaign(None, agent_names=["Alex"]))
    assert cfg.agent_config.agent_name == "Alex"


# ── the mismatch reporter (drives the log + the save-time warning) ────
def test_mismatch_detected_from_an_explicit_tag():
    assert agent_name_voice_mismatch("Sarah", {"Sarah": "female"}, "male") == "female"


def test_mismatch_detected_by_inference_without_tags():
    assert agent_name_voice_mismatch("Sarah", None, "male") == "female"


def test_no_mismatch_when_genders_agree():
    assert agent_name_voice_mismatch("James", {"James": "male"}, "male") is None


def test_unisex_or_unknown_names_are_never_flagged():
    # A false alarm on a legitimate unisex name is worse than silence.
    assert agent_name_voice_mismatch("Azian", None, "male") is None
    assert agent_name_voice_mismatch("Azian", None, "female") is None


def test_unknown_voice_gender_is_never_flagged():
    assert agent_name_voice_mismatch("Sarah", {"Sarah": "female"}, None) is None
    assert agent_name_voice_mismatch("Sarah", {"Sarah": "female"}, "") is None


def test_explicit_tag_beats_inference():
    # Operator says "Sarah" is male in their market → trust them, no warning.
    assert agent_name_voice_mismatch("Sarah", {"Sarah": "male"}, "male") is None
