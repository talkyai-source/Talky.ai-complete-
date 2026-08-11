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


def test_an_unusable_pool_is_substituted_not_spoken():
    """2026-08-12 — THIS TEST WAS INVERTED, deliberately.

    It used to assert the pool wins even when nothing in it matches the voice
    ("never invent a name the campaign did not configure" — the 2026-07-09
    "Emily" bug). That protection is real, but it was implemented far too
    broadly: any gender tag switched the conflict check off entirely, and once
    campaign forms began AUTO-tagging names, every campaign became untouchable.
    Production shipped a male voice introducing itself as "Sarah" 21 times in
    14 days.

    A female voice whose only configured name is explicitly tagged male cannot
    be satisfied — speaking "James" through a female voice is the exact defect
    this whole module exists to prevent. So we substitute.

    The 2026-07-09 protection is NOT lost: `name_is_referenced_in` still blocks
    substitution when the campaign's own ROLE/GOAL text names the agent, which
    is the case that actually produced a self-contradicting prompt. That guard
    is exercised directly below (see the script_text= cases).
    """
    camp = _campaign(FEMALE_VOICE, agent_names=["James"], genders={"James": "male"})
    name = _cfg(camp).agent_config.agent_name
    assert name != "James", (
        "a male-tagged name on a female voice must not be spoken — this is "
        "the male-voice-says-Sarah defect in the other direction"
    )
    assert name, "substitution must yield a usable name, not nothing"


def test_a_pool_tagged_to_MATCH_the_voice_is_still_honoured():
    """The escape hatch, intact: tag a name with the VOICE's gender and it is
    a deliberate casting choice we never override."""
    camp = _campaign(FEMALE_VOICE, agent_names=["James"], genders={"James": "female"})
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


# ---------------------------------------------------------------------------
# When the pool CANNOT satisfy the voice at all
# ---------------------------------------------------------------------------
# Reported after a live call on campaign 50847cc9 (2026-07-29): the campaign's
# only name was "Sarah jones" and the effective voice was Petros (male,
# british), so every call opened with a male London voice saying "this is
# Sarah". Warning-only was not enough — the operator wants the name taken
# "accordingly".
#
# The narrow rule: substitute a matching built-in name ONLY when the voice
# gender is known AND every configured name positively conflicts. That is the
# one case the pool cannot be satisfied, so preferring it preserves nothing.
#
# Both guards below exist to protect the 2026-07-09 decision that made the pool
# authoritative (the agent said "Emily" while the campaign script said "You are
# James" — a self-contradicting prompt).

import pytest

from app.domain.services.telephony_session_config import resolve_name_against_voice
from app.services.scripts.prompts.agent_name_rotator import (
    name_is_referenced_in,
    pool_wholly_conflicts,
    substitute_name_for_voice,
)


@pytest.mark.parametrize(
    "pool,genders,voice_gender,expected",
    [
        (["Sarah jones"], None, "male", True),      # the reported case
        (["Sarah", "Emily"], None, "male", True),   # every name conflicts
        (["Sarah", "James"], None, "male", False),  # one name fits
        (["Sarah", "Alex"], None, "male", False),   # unknown != conflict
        (["Sarah"], {"Sarah": "male"}, "male", False),  # explicit tag wins
        # 2026-08-12 — THIS EXPECTATION WAS INVERTED. It used to be False, on
        # the premise that "tagging is a deliberate casting choice". That
        # premise died: campaign forms began auto-tagging names with their
        # obvious gender, so `{'Sarah': 'female'}` is not a casting decision,
        # it is the form recording what Sarah is. Production ran campaign
        # 50847cc9 with exactly this config against a MALE voice and logged
        # agent_name_voice_gender_mismatch 21 times in 14 days while this
        # function reported no conflict. The escape hatch is now a tag that
        # MATCHES the voice — which is what resolve_name_against_voice's own
        # docstring always said it was.
        (["Sarah"], {"Sarah": "female"}, "male", True),
        (["Sarah"], None, None, False),             # unknown voice -> never
        ([], None, "male", False),                  # empty pool
    ],
)
def test_pool_wholly_conflicts(pool, genders, voice_gender, expected):
    assert pool_wholly_conflicts(pool, genders, voice_gender) is expected


def test_unisex_name_is_never_treated_as_a_conflict():
    """"Alex"/"Sam" are usable with either voice. Treating unknown as a
    conflict would discard a perfectly good configured name."""
    for name in ("Sam", "Jordan", "Riley", "Casey", "Taylor"):
        assert pool_wholly_conflicts([name], None, "male") is False
        assert pool_wholly_conflicts([name], None, "female") is False


def test_substitution_happens_and_is_deterministic():
    """A retry must not introduce itself with a different name than the
    attempt before it."""
    first = resolve_name_against_voice(
        "Sarah jones", ["Sarah jones"], None, "male", seed="campaign-1"
    )
    assert first[1] == "Sarah jones", "should report what it replaced"
    assert first[0] != "Sarah jones"
    for _ in range(5):
        assert resolve_name_against_voice(
            "Sarah jones", ["Sarah jones"], None, "male", seed="campaign-1"
        ) == first


def test_substituted_name_matches_the_voice():
    name, replaced = resolve_name_against_voice(
        "Sarah jones", ["Sarah jones"], None, "male", seed="c"
    )
    assert replaced and name in _MALE_AGENT_NAMES + list(
        __import__(
            "app.domain.services.global_ai_config", fromlist=["MALE_NAMES"]
        ).MALE_NAMES
    )


def test_operator_script_naming_the_agent_blocks_substitution():
    """THE regression guard. If the campaign's own ROLE/GOAL text names the
    agent, substituting would contradict it — keep the name, warn instead."""
    name, replaced = resolve_name_against_voice(
        "Sarah jones", ["Sarah jones"], None, "male",
        script_text="You are Sarah, a friendly estimator for All-state.",
        seed="c",
    )
    assert (name, replaced) == ("Sarah jones", None)


def test_script_match_is_word_bounded():
    """"Sam" must not be considered referenced by the word "same"."""
    assert name_is_referenced_in("we do the same thing every time", ["Sam"]) is False
    assert name_is_referenced_in("You are Sam, an estimator", ["Sam"]) is True
    assert name_is_referenced_in(None, ["Sam"]) is False


@pytest.mark.parametrize(
    "chosen,pool,genders,voice_gender,script",
    [
        ("James", ["Sarah", "James"], None, "male", None),   # pool satisfiable
        ("Sarah", ["Sarah"], {"Sarah": "male"}, "male", None),  # tagged
        ("Sarah", ["Sarah"], None, None, None),              # voice unknown
        ("Sarah", ["Sarah"], None, "female", None),          # already matches
    ],
)
def test_no_substitution_when_not_wholly_conflicting(
    chosen, pool, genders, voice_gender, script
):
    assert resolve_name_against_voice(
        chosen, pool, genders, voice_gender, script_text=script, seed="c"
    ) == (chosen, None)


def test_substitute_name_for_voice_refuses_unknown_gender():
    assert substitute_name_for_voice(None) is None
    assert substitute_name_for_voice("neutral") is None
    assert substitute_name_for_voice("male", seed="s") is not None


def test_resolver_never_raises_on_garbage():
    """It runs on every live call — a bad pool must degrade, never throw."""
    for pool in (None, [], [""], [None], ["Sarah", None]):
        name, _ = resolve_name_against_voice("Sarah", pool, None, "male", seed="c")
        assert isinstance(name, str)
