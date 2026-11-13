"""Adding a Do-Not-Call entry through the API works (QA F2-08, QA-BE-008).

``POST /api/v1/dnc/`` read ``current_user.user_id``, which ``CurrentUser``
does not have, so every request failed with a 500 and no number could be added.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import dnc


@pytest.mark.asyncio
async def test_an_admin_can_add_a_number_and_is_recorded_as_its_author(monkeypatch):
    calls = []

    class Service:
        def __init__(self, _pool):
            pass

        async def add(self, **kwargs):
            calls.append(kwargs)
            now = datetime.now(timezone.utc)
            return SimpleNamespace(
                id="entry-1", tenant_id=kwargs["tenant_id"], normalized_number="+12125550100",
                source=kwargs["source"], reason=kwargs["reason"], expires_at=None,
                added_by=kwargs["added_by"], created_at=now, updated_at=now,
            )

    monkeypatch.setattr(dnc, "DNCService", Service)
    monkeypatch.setattr(dnc, "_to_response", lambda entry: entry)
    user = CurrentUser(id="user-1", email="admin@example.invalid", tenant_id="tenant-1", role="tenant_admin")
    payload = dnc.DNCAddRequest(e164="+12125550100", reason="asked not to be called")

    entry = await dnc.add_dnc_entry(payload, current_user=user, db_pool=object())

    assert entry.normalized_number == "+12125550100"
    assert calls[0]["added_by"] == "user-1"
