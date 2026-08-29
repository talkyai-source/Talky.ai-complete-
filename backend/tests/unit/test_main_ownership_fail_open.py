"""Startup must fail OPEN for outbound-only production, CLOSED for inbound.

Two boot guards in ``app.main``'s lifespan were wired to ``strict_validation``
alone while the behaviour they protect is only required when production inbound
is live:

1. Telephony ownership. The ``try`` takes the *non-strict* acquire whenever
   inbound is disabled (``strict_validation and _production_inbound_enabled``),
   but the ``except`` raised ``RuntimeError("Production telephony ownership
   could not be proven")`` on ``strict_validation`` alone — so a transient Redis
   error during an outbound-only production restart crashed the whole API on a
   path the surrounding comment says must fail OPEN. Same for the heartbeat
   ``except`` just below it.
2. The inbound admission watchdog. A falsy ``container.db_pool`` skipped it in
   complete silence, so production inbound could serve calls with nothing
   reconciling durable reservations (stranded billed minutes / concurrency
   leases).

Both decisions now live in ``app.core.inbound_startup`` next to the other
production inbound guards, and ``app.main`` calls them at all three sites.
"""
from __future__ import annotations

import pytest

from app.core.inbound_startup import (
    require_inbound_admission_watchdog,
    telephony_ownership_failure_is_fatal,
)


# ── 1. Ownership acquire / heartbeat failure ────────────────────────────


def test_outbound_only_production_ownership_failure_is_not_fatal():
    """ENVIRONMENT=production, inbound OFF: a Redis blip must not refuse
    startup — the acquire path above it already fails open here."""
    assert telephony_ownership_failure_is_fatal(True, False) is False


def test_production_inbound_ownership_failure_stays_fatal():
    """Inbound ON: an unprovable owner would split live calls — fail closed."""
    assert telephony_ownership_failure_is_fatal(True, True) is True


def test_non_production_is_never_fatal():
    assert telephony_ownership_failure_is_fatal(False, True) is False
    assert telephony_ownership_failure_is_fatal(False, False) is False


# ── 2. Inbound admission watchdog ───────────────────────────────────────


def test_missing_watchdog_refuses_production_inbound_startup():
    with pytest.raises(RuntimeError, match="Inbound admission watchdog"):
        require_inbound_admission_watchdog(None, True, True)


def test_missing_watchdog_only_warns_when_inbound_disabled(caplog):
    with caplog.at_level("WARNING"):
        require_inbound_admission_watchdog(None, True, False)
    assert "inbound_admission_watchdog_not_started" in caplog.text


def test_started_watchdog_passes_in_every_mode():
    sentinel = object()
    for strict in (True, False):
        for inbound in (True, False):
            require_inbound_admission_watchdog(sentinel, strict, inbound)
