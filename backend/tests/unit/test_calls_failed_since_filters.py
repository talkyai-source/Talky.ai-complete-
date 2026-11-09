"""D4-11: GET /calls/ can return only failed calls, and only those after a time.

The list filtered by exact status, direction and date only, so the app's
failed-call alerts scanned the newest 100 calls and missed older failures
when more than 100 calls happened while it was closed. ``failed`` uses the
backend's one definition of a failed call (FAILED_OUTCOMES: did not reach a
live human), and ``since`` is an exclusive instant. Both join the cursor
scope only when given, so cursors issued before this change stay valid.
"""
from datetime import datetime, timezone

import pytest

from app.domain.services.call_history_cursor import encode_history_cursor, history_scope
from app.domain.services.call_outcomes import FAILED_OUTCOME_LIST
from tests.unit.test_bh_calls_recordings_calls_endpoint import _Conn, _db, _list, _user

SINCE = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)


async def test_failed_true_returns_only_calls_that_did_not_reach_a_person():
    conn = _Conn()
    await _list(conn, failed=True)
    query, params = conn.fetch_calls[0]
    assert "c.outcome = ANY($2::text[])" in query
    assert params[1] == FAILED_OUTCOME_LIST
    count_query, count_params = conn.fetchval_calls[0]
    assert "c.outcome = ANY($2::text[])" in count_query and count_params == params[:2]


async def test_failed_false_excludes_them_and_keeps_calls_without_an_outcome():
    conn = _Conn()
    await _list(conn, failed=False)
    query, params = conn.fetch_calls[0]
    assert "(c.outcome IS NULL OR NOT c.outcome = ANY($2::text[]))" in query


async def test_since_is_an_exclusive_instant_after_the_other_filters():
    conn = _Conn()
    await _list(conn, status="ended", failed=True, since=SINCE)
    query, params = conn.fetch_calls[0]
    assert "c.status = $2" in query
    assert "c.outcome = ANY($3::text[])" in query
    assert "c.created_at > $4" in query and params[3] == SINCE


async def test_a_since_without_a_zone_is_utc():
    conn = _Conn()
    await _list(conn, since=datetime(2026, 11, 7, 1, 0))
    _, params = conn.fetch_calls[0]
    assert params[1] == SINCE


async def test_cursors_issued_before_the_new_filters_stay_valid():
    conn = _Conn()
    user = _user()
    old_scope = history_scope(str(user.tenant_id), {
        "status": None, "direction": None, "inbound_campaign_id": None,
        "from": None, "to": None, "search": None,
    })
    cursor = encode_history_cursor("11111111-2222-4333-8444-555555555555", SINCE, old_scope)
    await _list(conn, current_user=user, pagination="cursor", cursor=cursor)
    assert conn.fetch_calls, "the old cursor was accepted"


async def test_a_cursor_cannot_carry_over_to_different_failed_or_since_filters():
    from fastapi import HTTPException

    user = _user()
    plain = history_scope(str(user.tenant_id), {
        "status": None, "direction": None, "inbound_campaign_id": None,
        "from": None, "to": None, "search": None,
    })
    cursor = encode_history_cursor("11111111-2222-4333-8444-555555555555", SINCE, plain)
    with pytest.raises(HTTPException) as refused:
        await _list(_Conn(), current_user=user, pagination="cursor", cursor=cursor, failed=True)
    assert refused.value.status_code == 422


def test_the_frontend_and_backend_agree_on_what_failed_means():
    assert set(FAILED_CALL_OUTCOMES_IN_THE_APP) == set(FAILED_OUTCOME_LIST)


# Talk-Leee/src/lib/call-outcome-label.ts FAILED_CALL_OUTCOMES (origin/main).
FAILED_CALL_OUTCOMES_IN_THE_APP = (
    "no_answer", "busy", "rejected", "unreachable", "network_failure", "failed", "cancelled", "voicemail",
)


def test_db_harness_is_shared():
    assert callable(_db)
