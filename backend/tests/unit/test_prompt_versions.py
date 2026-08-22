"""A prompt version that nobody remembers to bump is worse than no version.

WHY THIS TEST IS THE WHOLE MECHANISM
------------------------------------
`versions.py` claims "lead_gen@1" identifies a specific set of instructions.
Nothing in Python enforces that. Someone edits a line in `personas/lead_gen.py`,
the version constant stays "lead_gen@1", and every call afterwards is logged
under a version that no longer describes what ran — with no error, no warning,
and no way to tell from the logs which of two different prompts a batch used.

That is exactly the failure this project keeps hitting: a field that looks
populated and healthy while meaning nothing. So the version is pinned to a
recomputed content hash here. Change the prompt text without adding a new
version entry and this test fails, and tells you what to do about it.

goals.md §6: "Keep prompt versions immutable after use; create a new version
for changes."

WHEN THIS TEST FAILS
--------------------
It is not broken. You changed a prompt. Do this:

  1. Read the reported hash.
  2. In `versions.py`, add a NEW version (e.g. lead_gen@2) — do not edit the
     existing one, because it has already appeared in call logs and has to keep
     meaning what it meant.
  3. Update _EXPECTED_HASHES below to the reported value.
  4. Note the change in the release notes; QA batches are keyed on this.
"""

from __future__ import annotations

import pytest

from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.versions import (
    hash_prompt,
    identify,
    known_personas,
    template_name,
    version_for,
)

# Canonical inputs. The hash of a real call also varies with the campaign's own
# company/agent/slots — that is correct and intended, since a different campaign
# genuinely runs different instructions. These fixed values exist only so the
# registry itself has something reproducible to pin.
AGENT = "Ava"
COMPANY = "Northwind Systems"
SLOTS: dict = {}


def compose(persona: str, *, knowledge_driven: bool = True) -> str:
    return compose_prompt(
        persona_type=persona,
        agent_name=AGENT,
        company_name=COMPANY,
        campaign_slots=SLOTS,
        additional_instructions=None,
        direction="outbound",
        knowledge_driven=knowledge_driven,
    )


# version -> hash of the canonically-composed prompt. Regenerate on purpose,
# never casually; see the module docstring.
_EXPECTED_HASHES: dict[str, str] = {
    # Recorded 2026-08-22 against the prompts as deployed at 2a111ae5.
    # lead_gen composes to ~24,961 chars — worth remembering, since prompt SIZE
    # is the dominant term in per-turn latency on this stack.
    "lead_gen@1": "ea102992398cf3c3",
    "customer_support@1": "e024291ac232950b",
    "receptionist@1": "8e8098110acf3651",
}


@pytest.mark.parametrize("persona", known_personas())
def test_the_registered_version_still_describes_the_actual_prompt(persona):
    """THE LOAD-BEARING TEST. See the module docstring before "fixing" this."""
    version = version_for(persona)
    actual = hash_prompt(compose(persona))
    expected = _EXPECTED_HASHES.get(version)

    assert expected and expected != "__FILL_ME__", (
        f"{persona}: version {version} has no pinned hash. Add one: {actual!r}"
    )
    assert actual == expected, (
        f"\n\n  The {persona} prompt text CHANGED but its version did not.\n"
        f"    registered version : {version}\n"
        f"    pinned hash        : {expected}\n"
        f"    actual hash now    : {actual}\n\n"
        f"  Add a NEW version to versions.py (do not edit {version} — it is\n"
        f"  already in call logs) and pin {actual!r} to it.\n"
    )


# ── the identity itself ─────────────────────────────────────────────────────


def test_personas_do_not_share_a_hash():
    """If every persona hashed the same, the field would be decorative.

    Guarding this explicitly because a signal that never varies is this
    project's recurring defect, and it always looks healthy in a log.
    """
    hashes = {p: hash_prompt(compose(p)) for p in known_personas()}
    assert len(set(hashes.values())) == len(hashes), f"personas collide: {hashes}"


def test_the_hash_is_deterministic():
    """Same text, same hash — or a QA batch can never be shown to be one batch."""
    assert hash_prompt(compose("lead_gen")) == hash_prompt(compose("lead_gen"))


def test_a_different_company_produces_a_different_hash():
    """A per-campaign identity has to actually vary per campaign."""
    a = hash_prompt(compose("lead_gen"))
    b = hash_prompt(
        compose_prompt(
            persona_type="lead_gen",
            agent_name=AGENT,
            company_name="Totally Different Ltd",
            campaign_slots=SLOTS,
            additional_instructions=None,
            direction="outbound",
            knowledge_driven=True,
        )
    )
    assert a != b


# Strict (slot-based) composition refuses to run without every required slot —
# that is goals.md §6's "fail validation when required variables are missing",
# and it is already enforced. Supplied here so the strict-vs-KD comparison below
# has something to compare.
LEAD_GEN_SLOTS: dict = {
    "industry": "commercial cleaning",
    "services_description": "scheduled office cleaning and washroom supplies",
    "coverage_area": "Greater Manchester",
    "value_proposition": "fixed monthly price with no lock-in",
    "call_reason": "to see whether a fixed-price clean would suit them",
    "qualification_questions": "How many sites? Who handles cleaning today?",
    "disqualifying_answers": "under 5 staff; already in a 24-month contract",
    "calendar_booking_type": "15-minute intro call",
}


def test_strict_mode_refuses_to_compose_with_missing_slots():
    """A half-filled prompt must never reach the model.

    Pinned because the failure mode it prevents is silent: an unresolved
    placeholder reads as ordinary text to an LLM, so the call sounds subtly
    wrong rather than erroring.
    """
    from app.services.scripts.prompts import PromptCompositionError

    with pytest.raises(PromptCompositionError) as exc:
        compose("lead_gen", knowledge_driven=False)
    assert "Missing required slots" in str(exc.value)


def test_knowledge_driven_mode_is_a_different_prompt():
    """The PromptCompositionError path re-composes with knowledge_driven=True.

    It produces different instructions, so it must produce a different hash —
    otherwise a retried call is silently attributed to the prompt it failed to
    build, which is the mislabelling the whole mechanism exists to prevent.
    """
    strict = hash_prompt(
        compose_prompt(
            persona_type="lead_gen",
            agent_name=AGENT,
            company_name=COMPANY,
            campaign_slots=LEAD_GEN_SLOTS,
            additional_instructions=None,
            direction="outbound",
            knowledge_driven=False,
        )
    )
    kd = hash_prompt(compose("lead_gen", knowledge_driven=True))
    assert strict != kd


def test_no_unresolved_placeholder_survives_composition():
    """goals.md §6: never send an unresolved {{variable}} to the LLM."""
    for persona in known_personas():
        text = compose(persona)
        assert "{{" not in text, f"{persona} shipped an unresolved placeholder"


def test_whitespace_is_not_normalised_away():
    """Trailing-space churn is a real edit to the bytes we send the model."""
    assert hash_prompt("hello") != hash_prompt("hello ")


def test_identify_never_raises_on_an_unknown_persona():
    """An unregistered persona must degrade to a visible marker, not an
    exception and not an empty string that reads as 'fine' in a log."""
    ident = identify("something_new", "body")
    assert ident.version == "unregistered"
    assert "something_new" in ident.template
    assert ident.hash == hash_prompt("body")


def test_a_missing_prompt_still_yields_a_hash():
    """Never blow up a call to compute a log field."""
    assert hash_prompt(None) == hash_prompt("")
    assert len(hash_prompt(None)) == 16


def test_template_names_follow_the_goals_document():
    """goals.md §6 names the master template `generic_lead_generation`."""
    assert template_name("lead_gen") == "generic_lead_generation"


def test_every_known_persona_has_a_template_and_a_version():
    for persona in known_personas():
        assert template_name(persona).startswith("generic_"), persona
        assert version_for(persona) != "unregistered", persona
        assert "@" in version_for(persona), persona
