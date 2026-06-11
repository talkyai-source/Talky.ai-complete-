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


def test_no_matching_gender_falls_back_to_builtin_gendered_name():
    # Pool has only female names but the voice is male → built-in male name.
    from app.domain.services.global_ai_config import MALE_NAMES
    name = pick_agent_name_for_voice(["Sarah", "Khadija"], {"Sarah": "female", "Khadija": "female"}, "male")
    assert name in MALE_NAMES


def test_unknown_voice_gender_uses_legacy_pool_pick():
    name = pick_agent_name_for_voice(["Azian", "Sarah"], GENDERS, None)
    assert name in {"Azian", "Sarah"}


def test_no_genders_map_with_known_voice_falls_back_to_builtin():
    from app.domain.services.global_ai_config import FEMALE_NAMES
    # Voice is female but no tags → can't match a pool name → built-in female.
    name = pick_agent_name_for_voice(["Bob", "Tom"], None, "female")
    assert name in FEMALE_NAMES


def test_case_insensitive_gender_match():
    name = pick_agent_name_for_voice(["azian"], {"Azian": "Male"}, "male")
    assert name == "azian"
