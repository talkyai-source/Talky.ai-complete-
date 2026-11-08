"""D3-2: an abandoned reconnect's pending row never hides the real connector state.

The status endpoint reported the newest row of a card unless an active one
existed. A reconnect started and closed without finishing leaves a newer
"pending" row, which hid an older expired or error row (reading
"disconnected"). Pending rows are now skipped while any settled row exists.
"""
from __future__ import annotations

from app.api.v1.endpoints import connectors as ce
from tests.unit.test_connectors_card_keys import FakeDB, _resp, _user


def _rows(*rows):
    return FakeDB({("connectors", "select"): [_resp(list(rows))]})


async def _card(db, card="calendar"):
    out = await ce.list_connector_statuses(current_user=_user(), db_client=db)
    return {item.type: item for item in out.items}[card]


async def test_an_abandoned_reconnect_does_not_hide_an_expired_connector():
    db = _rows(
        {"id": "c-new", "type": "calendar", "provider": "google_calendar", "status": "pending", "created_at": "2026-11-08T10:00:00Z"},
        {"id": "c-old", "type": "calendar", "provider": "google_calendar", "status": "expired", "created_at": "2026-11-01T10:00:00Z"},
    )
    card = await _card(db)
    assert card.status == "expired"


async def test_an_abandoned_reconnect_does_not_hide_an_error_connector():
    db = _rows(
        {"id": "c-new", "type": "calendar", "provider": "google_calendar", "status": "pending", "created_at": "2026-11-08T10:00:00Z"},
        {"id": "c-old", "type": "calendar", "provider": "google_calendar", "status": "error", "created_at": "2026-11-01T10:00:00Z"},
    )
    assert (await _card(db)).status == "error"


async def test_only_pending_rows_still_read_disconnected():
    db = _rows(
        {"id": "c-new", "type": "calendar", "provider": "google_calendar", "status": "pending", "created_at": "2026-11-08T10:00:00Z"},
    )
    assert (await _card(db)).status == "disconnected"
