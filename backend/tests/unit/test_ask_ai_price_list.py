"""QA F11-05: the public Ask-AI demo quotes only the live price list.

Asked for prices, the demo said "Basic $29.00 a month, Professional $79.00"
from a hard-coded fact sheet, while the catalogue sold Basic $99 and Business
$299 a month plus pay-as-you-go at 40 cents a minute. The figure guard grounds
replies on the system prompt, so the remembered prices passed it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_OPTION = {
    "interval": "month",
    "interval_count": 1,
    "currency": "usd",
    "currency_exponent": 2,
    "active": True,
    "checkout_available": True,
}
_CATALOGUE = [
    {"name": "Basic", "minutes": 1000, "agents": 2,
     "price_options": [{**_OPTION, "amount_minor": 9900}]},
    {"name": "Business", "minutes": 4000, "agents": 5,
     "price_options": [{**_OPTION, "amount_minor": 29900}]},
]
_PAYG = {
    "plan": {"name": "Pay as you go"},
    "packages": [{"minutes": 250, "price_cents": 10000, "currency": "usd",
                  "currency_exponent": 2, "price_per_minute_cents": 40.0}],
}


def test_fact_sheet_holds_no_remembered_prices():
    from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO

    assert "$" not in TALKY_PRODUCT_INFO
    for stale_plan in ("Professional", "Enterprise"):
        assert stale_plan not in TALKY_PRODUCT_INFO


def test_price_list_is_built_from_the_live_catalogue():
    from app.domain.services.ask_ai_constants import format_price_list

    text = format_price_list(_CATALOGUE, _PAYG)
    assert "Basic $99 a month (1,000 minutes, 2 agents)" in text
    assert "Business $299 a month (4,000 minutes, 5 agents)" in text
    assert "250 minutes for $100" in text
    assert "40 cents" in text


def test_price_list_leaves_out_a_price_checkout_cannot_take():
    from app.domain.services.ask_ai_constants import format_price_list

    unverified = {**_CATALOGUE[0], "price_options": [{**_OPTION, "amount_minor": 9900,
                                                       "checkout_available": False}]}
    assert format_price_list([unverified], None) is None


def test_demo_withholds_a_price_that_is_not_on_the_live_list():
    from app.domain.services.ask_ai_constants import ask_ai_fact_sheet, format_price_list
    from app.domain.services.voice_pipeline.figure_grounding import (
        UNGROUNDED_FIGURE_LINE,
        guard_figures,
    )

    session = SimpleNamespace(call_id="ask-ai-test", _spoken_sentences=[])
    sheet = ask_ai_fact_sheet(format_price_list(_CATALOGUE, _PAYG))
    assert guard_figures(session, "Basic is $29 a month.", [sheet]) == UNGROUNDED_FIGURE_LINE
    assert guard_figures(session, "Basic is $99 a month.", [sheet]) == "Basic is $99 a month."
    # Without a readable list no price is grounded: the demo points to the page.
    no_list = ask_ai_fact_sheet(None)
    assert "pricing page" in no_list
    assert guard_figures(session, "Basic is $99 a month.", [no_list]) == UNGROUNDED_FIGURE_LINE


@pytest.mark.asyncio
async def test_session_price_list_reads_the_pricing_page_sources_once(monkeypatch):
    from app.api.v1.endpoints import ask_ai_ws, plans

    reads = []

    async def list_plans(pool):
        reads.append("plans")
        return _CATALOGUE

    async def pay_as_you_go(pool):
        return _PAYG

    monkeypatch.setattr(plans, "list_plans", list_plans)
    monkeypatch.setattr(plans, "pay_as_you_go", pay_as_you_go)
    monkeypatch.setattr(ask_ai_ws, "_price_list_cache", None)
    first = await ask_ai_ws._session_price_list(object())
    second = await ask_ai_ws._session_price_list(object())
    assert "Basic $99 a month" in first
    assert second == first
    assert reads == ["plans"]


@pytest.mark.asyncio
async def test_unreadable_prices_leave_the_demo_pointing_to_the_pricing_page(monkeypatch):
    from app.api.v1.endpoints import ask_ai_ws, plans

    async def unavailable(pool):
        raise HTTPException(status_code=503, detail="Pricing is temporarily unavailable.")

    monkeypatch.setattr(plans, "list_plans", unavailable)
    monkeypatch.setattr(ask_ai_ws, "_price_list_cache", None)
    assert await ask_ai_ws._session_price_list(object()) is None
    assert ask_ai_ws._price_list_cache is None  # a failure is retried next session
