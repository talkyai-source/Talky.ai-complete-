"""Tests for call_service._update_campaign_counters.

Asserts that every terminal outcome bumps EXACTLY ONE counter:

  GOAL_ACHIEVED                                        -> calls_completed
  ANSWERED, GOAL_NOT_ACHIEVED, VOICEMAIL,
  BUSY, NO_ANSWER, TIMEOUT, FAILED                     -> calls_completed
  SPAM, INVALID, UNAVAILABLE, DISCONNECTED, REJECTED   -> calls_failed

This is the regression that fixes "campaign counters stay at 0 forever
because retryable outcomes were silently dropped".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.models.dialer_job import CallOutcome
from app.domain.services.call_service import CallService


def _service_with_rpc_recorder():
    """Return (service, rpc_calls) where rpc_calls is a list of
    (rpc_name, params) tuples recorded for every db_client.rpc(...).execute()
    invocation."""
    rpc_calls: list[tuple[str, dict]] = []

    def fake_rpc(name: str, params: dict):
        builder = MagicMock()
        builder.execute.side_effect = lambda: rpc_calls.append((name, params)) or MagicMock()
        return builder

    db_client = MagicMock()
    db_client.rpc = fake_rpc
    service = CallService(
        db_client=db_client,
        queue_service=None,
        call_repo=MagicMock(),
        lead_repo=MagicMock(),
    )
    return service, rpc_calls


@pytest.mark.parametrize(
    "outcome,expected_counter",
    [
        # Goal-achieved is the only "true success" — own bucket.
        (CallOutcome.GOAL_ACHIEVED, "calls_completed"),
        # We DID reach the lead — count toward "we tried this lead".
        (CallOutcome.ANSWERED, "calls_completed"),
        (CallOutcome.GOAL_NOT_ACHIEVED, "calls_completed"),
        (CallOutcome.VOICEMAIL, "calls_completed"),
        (CallOutcome.BUSY, "calls_completed"),
        (CallOutcome.NO_ANSWER, "calls_completed"),
        (CallOutcome.TIMEOUT, "calls_completed"),
        (CallOutcome.FAILED, "calls_completed"),
        # Could not reach the lead at all.
        (CallOutcome.SPAM, "calls_failed"),
        (CallOutcome.INVALID, "calls_failed"),
        (CallOutcome.UNAVAILABLE, "calls_failed"),
        (CallOutcome.DISCONNECTED, "calls_failed"),
        (CallOutcome.REJECTED, "calls_failed"),
    ],
)
def test_counter_dispatch_is_exhaustive(outcome, expected_counter):
    service, rpc_calls = _service_with_rpc_recorder()
    service._update_campaign_counters("camp-1", outcome)
    assert len(rpc_calls) == 1, (
        f"Outcome {outcome!r} silently dropped the counter increment — "
        f"campaign progress would never tick. Every terminal outcome must "
        f"land on exactly one of calls_completed / calls_failed."
    )
    name, params = rpc_calls[0]
    assert name == "increment_campaign_counter"
    assert params["p_campaign_id"] == "camp-1"
    assert params["p_counter"] == expected_counter


def test_counter_dispatch_swallows_db_errors():
    """If the RPC raises (e.g. transient DB issue), we log and move on
    rather than tear down the hangup teardown chain."""

    db_client = MagicMock()
    db_client.rpc.side_effect = RuntimeError("db down")
    service = CallService(
        db_client=db_client,
        queue_service=None,
        call_repo=MagicMock(),
        lead_repo=MagicMock(),
    )
    # Must not raise.
    service._update_campaign_counters("camp-1", CallOutcome.ANSWERED)
