"""Rolling back to an earlier prompt version (goals.md §6).

WHY THIS IS A FEATURE AND NOT A GIT REVERT
------------------------------------------
`cc19971a` gave every prompt version an identity, which answers *what did this
call run on*. It does not answer *put the old one back*. The bodies are Python
constants, so until now "roll back" meant revert-and-redeploy — a release at
precisely the moment you least want one, which is 9pm when a QA batch on
`lead_gen@2` is going badly.

So bodies are archived on first use and a campaign can be pinned to a version.
The tests below pin the two properties that make that safe:

  1. An unresolvable pin NEVER fails the call. It composes from the current code
     and logs loudly. A rollback mechanism that can prevent a call from happening
     is worse than none at all.

  2. A pinned call is reported under the PINNED version, not the shipped one.
     Logging a rolled-back call as `lead_gen@2` while it actually ran `@1` is
     exactly the mislabelling the identity mechanism was built to prevent — and
     it would be invisible.
"""

from __future__ import annotations

import pytest

from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.bodies import (
    _BODY_CACHE,
    body_sha,
    cache_size,
    current_bodies,
    prime_cache,
    resolve_body_sync,
)
from app.services.scripts.prompts.versions import known_personas, version_for

OLD_BODY = "You are {agent_name} at {company_name}. This is the ARCHIVED body."


@pytest.fixture(autouse=True)
def clean_cache():
    """The cache is module state; a leaked entry would make a later test lie."""
    saved = dict(_BODY_CACHE)
    _BODY_CACHE.clear()
    yield
    _BODY_CACHE.clear()
    _BODY_CACHE.update(saved)


# ── the archive ─────────────────────────────────────────────────────────────


def test_every_registered_persona_has_a_body_to_archive():
    bodies = current_bodies()
    for persona in known_personas():
        assert persona in bodies, f"{persona} has no body to record"
        assert len(bodies[persona]) > 100, f"{persona} body looks empty"


def test_bodies_are_templates_not_composed_prompts():
    """The archive stores the template with placeholders intact. Storing a
    composed prompt would bake one campaign's company name into the version."""
    for body in current_bodies().values():
        assert "{agent_name}" in body or "{company_name}" in body


def test_body_sha_is_stable_and_discriminating():
    assert body_sha("a") == body_sha("a")
    assert body_sha("a") != body_sha("a ")


# ── resolution ──────────────────────────────────────────────────────────────


def test_no_pin_means_compose_from_code():
    assert resolve_body_sync("lead_gen", None) == (None, None)
    assert resolve_body_sync("lead_gen", "") == (None, None)


def test_pinning_the_current_version_composes_from_code():
    """Not from an archived copy of itself — otherwise a pin could serve a stale
    snapshot of the version we already ship."""
    assert resolve_body_sync("lead_gen", version_for("lead_gen")) == (None, None)


def test_an_unresolvable_pin_degrades_to_code_and_does_not_raise(caplog):
    """THE LOAD-BEARING NEGATIVE. A pin naming a version that was never recorded
    must not stop the call."""
    with caplog.at_level("WARNING"):
        body, version = resolve_body_sync("lead_gen", "lead_gen@does_not_exist")
    assert (body, version) == (None, None)
    assert "prompt_pin_unresolved" in caplog.text, (
        "an ignored pin must be loud — a campaign held on an older prompt "
        "silently running the newer one is the failure this prevents"
    )


def test_a_recorded_version_resolves_to_its_archived_body():
    prime_cache([{"version": "lead_gen@1", "persona_type": "lead_gen", "body": OLD_BODY}])
    body, version = resolve_body_sync("lead_gen", "lead_gen@1")
    assert body == OLD_BODY
    assert version == "lead_gen@1"


def test_a_pin_for_the_wrong_persona_is_refused(caplog):
    """`receptionist@1` pinned on a lead_gen campaign would compose a
    receptionist prompt for a sales call."""
    prime_cache([{"version": "receptionist@1", "persona_type": "receptionist", "body": OLD_BODY}])
    with caplog.at_level("WARNING"):
        body, version = resolve_body_sync("lead_gen", "receptionist@1")
    assert (body, version) == (None, None)
    assert "prompt_pin_persona_mismatch" in caplog.text


def test_priming_is_idempotent_and_counts_what_it_holds():
    prime_cache([{"version": "v1", "persona_type": "lead_gen", "body": OLD_BODY}])
    prime_cache([{"version": "v1", "persona_type": "lead_gen", "body": OLD_BODY}])
    assert cache_size() == 1


def test_priming_ignores_incomplete_rows():
    """A half-written row must not become a pin target that composes an empty
    prompt."""
    prime_cache([
        {"version": "v1", "persona_type": "lead_gen", "body": ""},
        {"version": None, "persona_type": "lead_gen", "body": OLD_BODY},
        {"version": "v2", "persona_type": None, "body": OLD_BODY},
    ])
    assert cache_size() == 0


# ── composition actually uses it ────────────────────────────────────────────


def compose(body_override=None):
    return compose_prompt(
        persona_type="lead_gen",
        agent_name="Ava",
        company_name="Northwind Systems",
        campaign_slots={},
        additional_instructions=None,
        direction="outbound",
        knowledge_driven=True,
        body_override=body_override,
    )


def test_an_override_actually_changes_the_composed_prompt():
    assert "ARCHIVED body" not in compose()
    assert "ARCHIVED body" in compose(OLD_BODY)


def test_an_override_still_gets_the_campaign_placeholders_filled():
    """Rollback substitutes the TEMPLATE. A pinned campaign must still get its
    own agent and company name, not the ones from whenever it was archived."""
    out = compose(OLD_BODY)
    assert "Ava" in out and "Northwind Systems" in out
    assert "{agent_name}" not in out and "{company_name}" not in out


def test_the_surrounding_prompt_is_still_assembled_around_an_override():
    """Only the persona body is replaced — guardrails and the compliance floor
    are added by the composer and must survive a rollback."""
    overridden, normal = compose(OLD_BODY), compose()
    assert len(overridden) > len(OLD_BODY) + 200, "the rest of the prompt vanished"
    # the shared scaffolding appears in both
    tail = normal[-400:]
    assert any(line and line in overridden for line in tail.splitlines() if len(line) > 30)


def test_no_override_is_byte_for_byte_the_current_prompt():
    """An unpinned campaign must be completely unaffected by this feature."""
    assert compose(None) == compose()
