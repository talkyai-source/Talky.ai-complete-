"""The calls list must carry the contact the caller GAVE during the call.

2026-09-25: call_lead_details held b97ce4c5's phone (+923085397539) and
4291700f's email, but GET /calls never returned them — the list showed only
the line the caller rang from ("ext:940007" / "Private caller"), and a caller
who left their number never showed as a hot lead.
"""
from __future__ import annotations

import pytest

from app.api.v1.endpoints.calls import CallListItem, _captured_contact_sql


@pytest.mark.parametrize("kind", ["phone", "email"])
def test_the_subquery_is_tenant_scoped_and_skips_rejected_values(kind):
    sql = " ".join(_captured_contact_sql(kind).split())
    assert "FROM call_lead_details d" in sql
    assert "d.call_id = c.id" in sql
    # The list query runs under bypass_rls: the tenant predicate is the guard.
    assert "d.tenant_id = c.tenant_id" in sql
    assert f"d.field_type = '{kind}' OR d.field_key = '{kind}'" in sql
    assert "NOT IN ('invalid', 'cancelled', 'needs_clarification')" in sql
    assert "ORDER BY d.confirmed DESC" in sql


def test_only_known_kinds_are_accepted():
    with pytest.raises(ValueError):
        _captured_contact_sql("phone'; DROP TABLE calls; --")


def test_the_list_item_carries_the_captured_contact():
    item = CallListItem(
        id="b97ce4c5", timestamp="2026-09-23T12:41:32Z", to_number="ext:940003",
        status="ended", captured_phone="+923085397539", captured_email=None,
    )
    assert item.model_dump()["captured_phone"] == "+923085397539"


@pytest.mark.parametrize("kind", ["phone", "email"])
def test_the_confirmed_flag_comes_from_the_same_row_as_the_value(kind):
    """2026-09-28: a stated-but-unconfirmed contact is listed, so the list must
    say which. The flag subquery picks the SAME row (same filter + order)."""
    value_sql = " ".join(_captured_contact_sql(kind).split())
    flag_sql = " ".join(_captured_contact_sql(kind, confirmed_flag=True).split())
    assert flag_sql.startswith("(SELECT d.confirmed ")
    assert value_sql.split(" FROM ", 1)[1] == flag_sql.split(" FROM ", 1)[1]


def test_the_list_item_says_when_a_contact_is_unconfirmed():
    item = CallListItem(
        id="4291700f", timestamp="2026-09-24T11:10:00Z", to_number="+923001234567",
        status="ended", captured_phone="+923085397539", captured_phone_confirmed=False,
    )
    dumped = item.model_dump()
    assert dumped["captured_phone_confirmed"] is False
    assert dumped["captured_email_confirmed"] is None
