"""Which prompt text did this call actually use?

WHY THIS EXISTS (goals.md §6, 2026-08-22)
-----------------------------------------
`telephony_prompt_composed` recorded persona, agent, company, campaign and
prompt_chars — everything except the one thing a QA batch needs. Two calls could
run completely different instructions and be indistinguishable in the logs, so
none of these release-gate lines were checkable:

    "Every call has a call ID, recording/transcript, prompt version and score."
    "No old prompt used in any test call."
    "Freeze one prompt version for every test batch."

A prompt run for 30 controlled calls has to be provably the same prompt for all
30, and provably different from the one before it.

TWO IDENTIFIERS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS
--------------------------------------------------------
``prompt_version`` is a name a human chose: "lead_gen@2". It is what you say in
a standup and what you roll back to. It is also, on its own, a promise nobody
can keep — someone edits a persona and forgets to bump it, and every call after
that is mislabelled with no signal at all.

``prompt_hash`` is derived from the composed text. It cannot lie and it cannot
go stale, but it means nothing to a person.

So both are logged, and the registry below pins them **together**. The
accompanying test recomputes each version's hash from the live prompt modules
and fails when they diverge — which is what makes "versions are immutable once
used; changes create a new version" an enforced rule rather than a hope.

WHAT IS HASHED, AND WHAT DELIBERATELY IS NOT
--------------------------------------------
The hash covers the *stable* composed system prompt — persona body, guardrails,
compliance floor, campaign slots, tenant instructions. It is taken in
``telephony_session_config`` at the moment ``_compose()`` returns, which is
before ``build_call_target_block`` prepends the callee's name and company.

That boundary is the entire point. Per-lead context changes on every single
call, so folding it in would give every call a unique hash and answer "was this
the same prompt?" with "no" forever — a metric that varies for the wrong reason
is as useless as one that never varies at all.
"""

from __future__ import annotations

import hashlib
from typing import Final, Mapping, NamedTuple

# Truncated to 16 hex chars (64 bits). Long enough that an accidental collision
# between prompt revisions is not a thing that happens, short enough to read in
# a log line and paste into a spreadsheet next to a call id.
_HASH_CHARS: Final[int] = 16


class PromptIdentity(NamedTuple):
    """What identifies the instructions a call ran on."""

    #: Stable template name, e.g. "generic_lead_generation".
    template: str
    #: Human-chosen version, e.g. "lead_gen@1".
    version: str
    #: First 16 hex chars of sha256 over the composed stable prompt.
    hash: str


def hash_prompt(prompt: str | None) -> str:
    """Content hash of a composed system prompt.

    Whitespace is *not* normalised. Trailing-space churn is a real edit to the
    bytes we send the model, and pretending otherwise would let a change slip
    past the version check this module exists to enforce.
    """
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:_HASH_CHARS]


# ── the registry ────────────────────────────────────────────────────────────
#
# goals.md §6 names the master template `generic_lead_generation`; the other two
# personas get the same treatment so a campaign on any of them is attributable.
#
# TO CHANGE A PROMPT: edit the persona, run
# tests/unit/test_prompt_versions.py, take the hash it reports, and add a NEW
# entry here with the next version. Do not edit an existing entry — a version
# that has already appeared in a call log has to keep meaning what it meant.

_TEMPLATES: Final[Mapping[str, str]] = {
    "lead_gen": "generic_lead_generation",
    "customer_support": "generic_customer_support",
    "receptionist": "generic_receptionist",
}

#: Current version per persona. Bump on any change to the composed text.
_VERSIONS: Final[Mapping[str, str]] = {
    "lead_gen": "lead_gen@2",
    "customer_support": "customer_support@2",
    "receptionist": "receptionist@2",
}

#: Fallback for a persona that is not in the registry yet. Deliberately visible
#: rather than blank: an unregistered persona in a call log is a thing someone
#: should notice, not a field that quietly reads empty.
UNREGISTERED: Final[str] = "unregistered"


def template_name(persona_type: str) -> str:
    return _TEMPLATES.get(persona_type, f"unregistered_{persona_type or 'unknown'}")


def version_for(persona_type: str) -> str:
    return _VERSIONS.get(persona_type, UNREGISTERED)


def identify(persona_type: str, composed_prompt: str | None) -> PromptIdentity:
    """Full identity for a composed prompt. Never raises."""
    return PromptIdentity(
        template=template_name(persona_type),
        version=version_for(persona_type),
        hash=hash_prompt(composed_prompt),
    )


def known_personas() -> tuple[str, ...]:
    """Personas the registry covers, for tests and admin tooling."""
    return tuple(_VERSIONS)
