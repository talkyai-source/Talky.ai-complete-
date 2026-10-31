"""Canonical vocabulary for ``tenants.subscription_status``.

WHY THIS MODULE EXISTS
----------------------
The column was written with Stripe's American spelling and read with the
British one:

    billing_service.cancel_subscription     -> "canceled"   (one L)
    billing_service._handle_subscription_deleted -> "canceled"
    call_guard._check_tenant_active         -> blocks on "cancelled" (two Ls)
    call_guard._check_partner_active        -> blocks on "cancelled"
    call_guard._check_subscription          -> blocks on "cancelled"

Nothing tied the two literals together, so a tenant cancelled through Stripe
passed every guard check and kept placing calls. Two further writes
(``cancel_subscription`` outside mock mode and ``_sync_subscription``) passed
Stripe's ``Subscription.status`` through unmodified, so the column could also
receive ``unpaid`` and ``incomplete_expired`` — values no read has ever
blocked on either.

The shape here is the one ``call_outcomes.py`` already uses for the outcome
vocabulary: one module spells the strings, both sides import it, and
``test_subscription_status_vocabulary`` fails if a second copy appears.

THE TWO RULES
-------------
* **Writes are canonical.** Everything that stores a status runs it through
  :func:`canonical` first, so only the values below ever enter the column.
  ``cancelled`` (two Ls) is canonical because ``admin/tenants.py`` already
  archives tenants with it and its input-validation regex admits only that
  spelling — the guard has always agreed with the admin path, and it is the
  billing path that was the outlier.
* **Reads are tolerant.** Production rows written before this module already
  carry ``canceled``. The blocking sets therefore contain BOTH spellings;
  fixing only the writes would leave every already-cancelled tenant dialling.
"""
from __future__ import annotations

from typing import Any

# ── canonical values ────────────────────────────────────────────────────────

ACTIVE = "active"
TRIALING = "trialing"
INACTIVE = "inactive"
PAST_DUE = "past_due"
SUSPENDED = "suspended"
CANCELLED = "cancelled"
UNPAID = "unpaid"
INCOMPLETE = "incomplete"
INCOMPLETE_EXPIRED = "incomplete_expired"
PAUSED = "paused"

# Stripe's ``Subscription.status`` spelling of :data:`CANCELLED`. Kept as a
# private name so no consumer can accidentally store it.
_STRIPE_CANCELED = "canceled"

#: Legacy/provider spellings folded onto a canonical value by :func:`canonical`.
_ALIASES = {
    _STRIPE_CANCELED: CANCELLED,
}

#: Every value the column is expected to hold, canonical plus the legacy
#: spelling still present in production rows.
KNOWN_STATUSES = frozenset({
    ACTIVE,
    TRIALING,
    INACTIVE,
    PAST_DUE,
    SUSPENDED,
    CANCELLED,
    _STRIPE_CANCELED,
    UNPAID,
    INCOMPLETE,
    INCOMPLETE_EXPIRED,
    PAUSED,
})

#: Both spellings of "cancelled" — what a *read* must treat as cancelled.
CANCELLED_STATUSES = frozenset({CANCELLED, _STRIPE_CANCELED})


# ── blocking sets (the read side) ───────────────────────────────────────────

#: The tenant/partner is switched off entirely: no calls at all.
TENANT_BLOCKED_STATUSES = frozenset({SUSPENDED}) | CANCELLED_STATUSES

#: The tenant has no subscription we can bill against. Adds the states where
#: Stripe has stopped collecting: ``past_due`` (retries in flight, already
#: blocked before this module), ``unpaid`` (retries exhausted) and
#: ``incomplete_expired`` (the first invoice never succeeded, so the
#: subscription never started). ``incomplete`` and ``paused`` are deliberately
#: NOT here: the first is the transient state between checkout and the first
#: payment, the second is an operator-chosen collection pause.
SUBSCRIPTION_BLOCKED_STATUSES = TENANT_BLOCKED_STATUSES | {
    PAST_DUE,
    UNPAID,
    INCOMPLETE_EXPIRED,
}


def canonical(status: Any) -> str:
    """Normalise a raw status to the canonical vocabulary.

    Folds case and surrounding whitespace, maps ``canceled`` -> ``cancelled``,
    and passes anything else through unchanged (an unknown provider value must
    reach the column verbatim rather than be silently rewritten). ``None``
    becomes the empty string.
    """
    value = str(status or "").strip().lower()
    return _ALIASES.get(value, value)


def is_blocked(status: Any, blocked: frozenset[str]) -> bool:
    """True if ``status`` — in either spelling, any case — is in ``blocked``."""
    value = str(status or "").strip().lower()
    return value in blocked or canonical(value) in blocked
