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
