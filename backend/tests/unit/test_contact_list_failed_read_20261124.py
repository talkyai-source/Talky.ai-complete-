"""The contact list route answers a failed read as a failure, not as "not found" or an empty list."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaigns as campaigns_ep
from tests.unit import test_contact_direction_boundaries as _b

MARKER = "SECRET-DRIVER-TEXT"
SENTENCE = "The request could not be completed."


class _Q(_b._Query):
    fail_table = None

    def range(self, *_a, **_k):
        return self

    def execute(self):
        if self.table == self.fail_table and self.operation == "select":
            return _b._Response([], error=MARKER)
        return super().execute()


class _DB(_b._DB):
    def __init__(self, fail_table=None):
        super().__init__(direction="outbound")
        self.fail_table = fail_table

    def table(self, name):
        q = _Q(self, name)
        q.fail_table = self.fail_table
        return q


def _list(db, user=_b.USER):
    return asyncio.run(campaigns_ep.list_campaign_contacts(
        campaign_id="c1", page=1, page_size=50, status=None, last_call_result=None,
        list_id=None, search=None, current_user=user, db_client=db))


def test_a_a_failed_campaign_read_is_a_500_not_a_404():
    with pytest.raises(HTTPException) as caught:
        _list(_DB("campaigns"))
    assert caught.value.status_code == 500
    assert caught.value.detail == SENTENCE
    assert MARKER not in repr(caught.value.detail)


def test_b_a_failed_contacts_read_is_a_500_not_an_empty_list():
    with pytest.raises(HTTPException) as caught:
        _list(_DB("leads"))
    assert caught.value.status_code == 500
    assert caught.value.detail == SENTENCE
    assert MARKER not in repr(caught.value.detail)


def test_c_a_healthy_read_returns_the_contacts_and_hides_removed_rows():
    db = _DB()
    db.tables["leads"].append({
        "id": "gone", "tenant_id": "t1", "campaign_id": "c1",
        "phone_number": "+442079460959", "status": "deleted",
    })
    out = _list(db)
    assert [row["id"] for row in out["items"]] == ["lead1"]
    assert out["total"] == 1


def test_d_another_tenants_campaign_is_still_a_404():
    db = _DB()
    db.tables["campaigns"][0]["tenant_id"] = "t2"
    with pytest.raises(HTTPException) as caught:
        _list(db)
    assert caught.value.status_code == 404
    assert caught.value.detail == "Campaign not found"
