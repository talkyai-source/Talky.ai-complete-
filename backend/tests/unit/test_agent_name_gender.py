"""Agent name must match the selected voice's gender (male voice -> male name).
Pins pick_agent_name_for_voice's resolution order."""
from __future__ import annotations

from app.services.scripts.prompts.agent_name_rotator import pick_agent_name_for_voice


GENDERS = {"Azian": "male", "Sarah": "female", "Khadija": "female"}


def test_male_voice_picks_a_male_tagged_name():
    for _ in range(20):
        name = pick_agent_name_for_voice(["Azian", "Sarah", "Khadija"], GENDERS, "male")
        assert name == "Azian"  # only male-tagged name in the pool


def test_female_voice_picks_a_female_tagged_name():
    for _ in range(20):
        name = pick_agent_name_for_voice(["Azian", "Sarah", "Khadija"], GENDERS, "female")
        assert name in {"Sarah", "Khadija"}  # the female-tagged names


def test_no_matching_gender_STILL_uses_the_configured_pool():
    # THE POOL ALWAYS WINS (2026-07-09): pool has only female-tagged names and
    # the voice is male — the agent must STILL use a configured name, never a
    # built-in invention ("Emily" bug: campaign said "You are James", rotator
    # introduced a name the campaign never configured).
    name = pick_agent_name_for_voice(["Sarah", "Khadija"], {"Sarah": "female", "Khadija": "female"}, "male")
    assert name in {"Sarah", "Khadija"}


def test_unknown_voice_gender_uses_legacy_pool_pick():
    name = pick_agent_name_for_voice(["Azian", "Sarah"], GENDERS, None)
    assert name in {"Azian", "Sarah"}


def test_no_genders_map_with_known_voice_uses_pool_via_inference():
    # No tags → infer from built-in name lists, but NEVER leave the pool.
    # "Sarah jones" + female voice → "Sarah jones" (the exact prod scenario).
    name = pick_agent_name_for_voice(["Sarah jones"], None, "female")
    assert name == "Sarah jones"
    # Mixed untagged pool + female voice → inference prefers the female name
    # ("Emma" is in the built-in FEMALE_NAMES list, so it is inferable).
    for _ in range(20):
        assert pick_agent_name_for_voice(["Bob", "Emma"], None, "female") == "Emma"
    # Nothing infers to the voice gender → still the configured pool.
    name = pick_agent_name_for_voice(["Bob", "Tom"], None, "female")
    assert name in {"Bob", "Tom"}


def test_case_insensitive_gender_match():
    name = pick_agent_name_for_voice(["azian"], {"Azian": "Male"}, "male")
    assert name == "azian"


# ── 2026-08-12: the escape hatch was too wide and switched the guard off ────
#
# Production, campaign 50847cc9 "Estimation":
#     pool    = ['Sarah']
#     genders = {'Sarah': 'female'}      <- the campaign FORM auto-tagged it
#     voice   = lfPTQbwnu1oXQ9g6V0r4     -> male
#
# The journal logged agent_name_voice_gender_mismatch 21 times in 14 days
# while pool_wholly_conflicts reported NO conflict, so nothing was ever
# substituted and a male voice kept introducing itself as "Sarah".
#
# Cause: pool_wholly_conflicts returned False for ANY tag on ANY pool name,
# on the stated premise that "campaign forms never sent tags, so
# agent_name_genders is null on real campaigns". The forms started sending
# tags, the premise died silently, and the guard disabled itself.
#
# It also contradicted its own caller: resolve_name_against_voice documents
# the escape hatch as tagging "WITH THE VOICE'S GENDER".

from app.services.scripts.prompts.agent_name_rotator import pool_wholly_conflicts


def test_THE_REGRESSION_a_female_tag_on_a_male_voice_is_a_conflict():
    """The exact production configuration that shipped a male 'Sarah'."""
    assert pool_wholly_conflicts(
        ["Sarah"], {"Sarah": "female"}, "male"
    ) is True


def test_the_escape_hatch_still_works_when_the_tag_MATCHES_the_voice():
    """Tagging a name with the VOICE's gender is the documented, deliberate
    casting choice — 'yes, use Sarah on this male voice'. Hands off."""
    assert pool_wholly_conflicts(
        ["Sarah"], {"Sarah": "male"}, "male"
    ) is False


def test_a_matching_tag_anywhere_in_the_pool_still_frees_the_whole_pool():
    """One deliberately-cast name means the pool IS satisfiable."""
    assert pool_wholly_conflicts(
        ["Sarah", "Emily"], {"Emily": "male"}, "male"
    ) is False


def test_untagged_inference_is_unchanged():
    """The original untagged failure must still be caught.

    NB "John" is deliberately NOT used here — _inferred_gender("John") is
    None (it is not in the rotator's built-in list), so a pool containing it
    is satisfiable by design. Using it would make this test pass for the
    wrong reason.
    """
    assert pool_wholly_conflicts(["Sarah", "Emily"], None, "male") is True
    assert pool_wholly_conflicts(["David", "James"], None, "female") is True


def test_a_usable_name_in_the_pool_is_still_not_a_conflict():
    assert pool_wholly_conflicts(["Sarah", "James"], None, "male") is False


def test_unisex_names_are_still_never_a_conflict():
    """Unknown is not a conflict — throwing away 'Sam'/'Jordan' would be worse
    than the bug this guards."""
    assert pool_wholly_conflicts(["Sam"], None, "male") is False
    assert pool_wholly_conflicts(["Jordan"], None, "female") is False


def test_unknown_voice_gender_still_disables_everything():
    """We never guess against an uncatalogued voice."""
    assert pool_wholly_conflicts(["Sarah"], {"Sarah": "female"}, None) is False
    assert pool_wholly_conflicts(["Sarah"], {"Sarah": "female"}, "") is False


def test_the_dojo_pool_is_correctly_left_alone():
    """Campaign 8893d8bd: ['Alexia','Jordan','Sam'] with male tags on Sam and
    Jordan. On a male voice those are deliberate and usable — no conflict."""
    pool = ["Alexia", "Jordan", "Sam"]
    genders = {"Sam": "male", "Alexia": "female", "Jordan": "male"}
    assert pool_wholly_conflicts(pool, genders, "male") is False
    # On a FEMALE voice, Alexia is tagged female and usable — also fine.
    assert pool_wholly_conflicts(pool, genders, "female") is False
