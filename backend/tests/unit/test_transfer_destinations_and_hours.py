"""Named transfer destinations and business hours (brief, tool, execution, prompt)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignBriefInput
from app.domain.models.conversation import Message, MessageRole
from app.domain.services.campaign_brief import (
    normalize_campaign_brief,
    render_campaign_brief,
    transfer_open_now,
)
from app.domain.services.telephony import adapter_registry
from app.domain.services.voice_pipeline import action_execution
from app.domain.services.voice_pipeline.action_execution import (
    _capabilities,
    _parameters,
    enabled_voice_actions,
)
from app.domain.services.voice_pipeline.action_tools import action_tools_for_turn
from app.realtime.tools import realtime_voice_action_tools

SALES = {"name": "Sales", "number": "+442071234567", "when": "The caller wants prices or wants to buy."}
SUPPORT = {"name": "Support", "number": "+442079876543", "when": "The caller has a problem with an existing order."}
HOURS = {"timezone": "Europe/London", "days": ["mon", "tue", "wed", "thu", "fri"],
         "start": "09:00", "end": "17:30"}


def raw(**extra):
    base = {"representative_name": "Alex", "brand": "Acme", "approved_next_actions": ["transfer"]}
    base.update(extra)
    return base


def norm(**extra):
    return normalize_campaign_brief(raw(**extra), company_name="Acme", agent_names=["Alex"])


def schema(**extra):
    return CampaignBriefInput(**raw(**extra))


def both(**extra):
    """The same input must be accepted by the schema and the normaliser."""
    return schema(**extra), norm(**extra)


def rejected_by_both(match, **extra):
    with pytest.raises(ValueError, match=f"(?i){match}"):
        norm(**extra)
    with pytest.raises(ValidationError, match=f"(?i){match}"):
        schema(**extra)


# ---------------------------------------------------------------- validation
def test_good_list_is_accepted_and_first_number_becomes_legacy_field():
    model, brief = both(transfer_destinations=[SALES, SUPPORT], transfer_hours=HOURS)
    assert brief["transfer_destinations"] == [SALES, SUPPORT]
    assert brief["transfer_destination"] == SALES["number"]
    assert brief["transfer_hours"] == HOURS
    assert len(model.transfer_destinations) == 2


def test_too_many_destinations_rejected():
    many = [{"name": f"Team {i}", "number": f"+44207123456{i}", "when": "x"} for i in range(6)]
    rejected_by_both("at most 5", transfer_destinations=many)


def test_duplicate_names_ignoring_case_rejected():
    rejected_by_both("unique", transfer_destinations=[SALES, {**SUPPORT, "name": " sales "}])


def test_name_length_is_enforced():
    rejected_by_both("name", transfer_destinations=[{**SALES, "name": "x" * 41}])
    rejected_by_both("name", transfer_destinations=[{**SALES, "name": "   "}])


@pytest.mark.parametrize("bad", ["not a number!", "+1; hangup", "sip:bad", ""])
def test_bad_number_rejected_when_saved(bad):
    rejected_by_both("number", transfer_destinations=[{**SALES, "number": bad}])


def test_when_required_with_two_but_optional_with_one():
    rejected_by_both("when", transfer_destinations=[{**SALES, "when": ""}, SUPPORT])
    rejected_by_both("when", transfer_destinations=[SALES, {"name": "Support", "number": SUPPORT["number"]}])
    _, brief = both(transfer_destinations=[{"name": "Sales", "number": SALES["number"]}])
    assert brief["transfer_destination"] == SALES["number"]


def test_when_length_enforced():
    rejected_by_both("when", transfer_destinations=[{**SALES, "when": "x" * 201}])


def test_destinations_without_transfer_approved_rejected():
    rejected_by_both("requires transfer", approved_next_actions=["end_call"],
                     transfer_destinations=[SALES])


def test_transfer_approved_without_any_destination_rejected():
    rejected_by_both("required")
    rejected_by_both("required", transfer_destinations=[])


def test_both_forms_must_agree():
    _, brief = both(transfer_destination=SUPPORT["number"], transfer_destinations=[SALES, SUPPORT])
    assert brief["transfer_destination"] == SALES["number"]
    rejected_by_both("one of", transfer_destination="+442070000000",
                     transfer_destinations=[SALES, SUPPORT])


@pytest.mark.parametrize("hours,match", [
    ({**HOURS, "timezone": "Mars/Base"}, "timezone"),
    ({**HOURS, "days": []}, "days"),
    ({**HOURS, "days": ["mon", "mon"]}, "days"),
    ({**HOURS, "days": ["monday"]}, "days"),
    ({**HOURS, "start": "9:00"}, "start"),
    ({**HOURS, "end": "25:00"}, "end"),
    ({**HOURS, "start": "17:30", "end": "09:00"}, "overnight"),
    ({**HOURS, "start": "09:00", "end": "09:00"}, "overnight"),
])
def test_bad_transfer_hours_rejected(hours, match):
    rejected_by_both(match, transfer_destinations=[SALES], transfer_hours=hours)


def test_hours_require_transfer_approved():
    rejected_by_both("transfer", approved_next_actions=["end_call"], transfer_hours=HOURS)


# ------------------------------------------------------ premium-rate numbers
PREMIUM = "+449012345678"


def test_a_premium_rate_number_in_the_list_is_refused_on_save():
    bad = {"name": "Chat line", "number": PREMIUM, "when": ""}
    rejected_by_both("premium-rate", transfer_destinations=[bad])
    rejected_by_both("premium-rate", transfer_destinations=[SALES, {**bad, "when": "Anything."}])


def test_a_premium_rate_number_written_loosely_is_refused_on_save():
    bad = {"name": "Chat line", "number": "+44 (0)90 1234 5678", "when": ""}
    with pytest.raises(ValueError):
        norm(transfer_destinations=[bad])


@pytest.mark.parametrize("legacy", [PREMIUM, "+44 90 1234 5678"])
def test_a_premium_rate_number_in_the_legacy_single_field_is_refused_on_save(legacy):
    with pytest.raises(ValueError, match="(?i)premium-rate"):
        norm(transfer_destination=legacy)


@pytest.mark.parametrize("number", ["+442071234567", "+448451234567", "1001", "sales_queue"])
def test_an_ordinary_number_or_an_extension_is_accepted_on_save(number):
    brief = norm(transfer_destinations=[{"name": "Sales", "number": number, "when": ""}])
    assert brief["transfer_destinations"][0]["number"] == number
    assert norm(transfer_destination=number)["transfer_destination"] == number


# ------------------------------------------------------------- compatibility
def test_legacy_only_brief_normalises_as_before():
    brief = norm(transfer_destination="Sales desk")
    assert brief["transfer_destination"] == "Sales desk"
    assert "transfer_destinations" not in brief and "transfer_hours" not in brief


# ------------------------------------------------------------ tool definition
def make_session(brief, *, direction="outbound", numbers=None):
    context = {"brief": brief, "call_direction": direction, "direction": direction}
    if numbers is not None:
        context["inbound_transfer_numbers"] = set(numbers)
    return SimpleNamespace(_voice_action_context=context,
                           _voice_action_capabilities={"transfer_call": "x"})


PROVIDER = SimpleNamespace(supports_tools=True, stream_chat_with_tools=lambda: None)


def cascaded_transfer(session, now=None):
    tools = action_tools_for_turn([Message(role=MessageRole.USER, content="hi")], PROVIDER,
                                  session=session, now=now)
    return next((t["function"] for t in tools if t["function"]["name"] == "transfer_call"), None)


def realtime_transfer(session):
    return next((t for t in realtime_voice_action_tools(session) if t.get("name") == "transfer_call"), None)


def test_one_destination_keeps_optional_parameter():
    spec = cascaded_transfer(make_session(norm(transfer_destinations=[SALES])))
    assert "required" not in spec["parameters"]
    assert "enum" not in spec["parameters"]["properties"]["destination"]


def test_two_destinations_require_enum_and_list_conditions():
    spec = cascaded_transfer(make_session(norm(transfer_destinations=[SALES, SUPPORT])))
    params = spec["parameters"]
    assert params["required"] == ["destination"]
    assert params["properties"]["destination"]["enum"] == ["Sales", "Support"]
    assert f"Sales: {SALES['when'].rstrip('.')}" in spec["description"]
    assert f"Support: {SUPPORT['when'].rstrip('.')}" in spec["description"]


def test_cascaded_and_realtime_definitions_match():
    for destinations in ([SALES], [SALES, SUPPORT]):
        session = make_session(norm(transfer_destinations=destinations))
        spec, rt = cascaded_transfer(session), realtime_transfer(session)
        assert spec["parameters"] == rt["parameters"]
        if len(destinations) > 1:
            for item in destinations:
                assert f"{item['name']}: {item['when'].rstrip('.')}" in rt["description"]
                assert f"{item['name']}: {item['when'].rstrip('.')}" in spec["description"]
    legacy = make_session(norm(transfer_destination="Sales desk"))
    assert cascaded_transfer(legacy)["parameters"] == realtime_transfer(legacy)["parameters"]


# ----------------------------------------------------------------- execution
def ctx(brief, **extra):
    return {"brief": brief, "call_direction": "outbound", "direction": "outbound", **extra}


def test_name_resolves_to_number_ignoring_case_and_spaces():
    context = ctx(norm(transfer_destinations=[SALES, SUPPORT]))
    payload, summary = _parameters(None, context, "transfer_call", {"destination": "  sUPPORT "})
    assert payload == {"destination": SUPPORT["number"]}
    assert "Support" in summary


def test_unknown_name_names_valid_choices():
    context = ctx(norm(transfer_destinations=[SALES, SUPPORT]))
    with pytest.raises(ValueError, match="Sales, Support"):
        _parameters(None, context, "transfer_call", {"destination": "billing"})
    with pytest.raises(ValueError, match="Sales, Support"):
        _parameters(None, context, "transfer_call", {})


@pytest.mark.parametrize("supplied", ["+442070000000", "sip:evil@example.com", "+442071234567"])
def test_model_supplied_number_is_never_used(supplied):
    context = ctx(norm(transfer_destinations=[SALES, SUPPORT]))
    with pytest.raises(ValueError):
        _parameters(None, context, "transfer_call", {"destination": supplied})
    single = ctx(norm(transfer_destinations=[SALES]))
    with pytest.raises(ValueError):
        _parameters(None, single, "transfer_call", {"destination": supplied})


def test_single_destination_omitted_empty_or_named_all_work():
    context = ctx(norm(transfer_destinations=[SALES]))
    for args in ({}, {"destination": ""}, {"destination": None}, {"destination": "sales"}):
        assert _parameters(None, context, "transfer_call", args)[0] == {"destination": SALES["number"]}


def test_legacy_single_destination_still_works_when_omitted():
    context = ctx(norm(transfer_destination="Sales desk"))
    assert _parameters(None, context, "transfer_call", {})[0] == {"destination": "Sales desk"}
    assert _parameters(None, context, "transfer_call", {"destination": "Sales desk"})[0] == {"destination": "Sales desk"}
    with pytest.raises(ValueError):
        _parameters(None, context, "transfer_call", {"destination": "elsewhere"})


@pytest.mark.asyncio
async def test_unknown_name_returns_needs_details_and_dials_nothing(monkeypatch):
    brief = norm(transfer_destinations=[SALES, SUPPORT])
    context = ctx(brief, call_status="in_progress", provider="asterisk", transfer_connected=True,
                  email_connected=False, call_id="c1", provider_call_id="p1", campaign_id="x")

    async def fake_pool(_session):
        return object()

    async def fake_context(_session, _pool):
        return context

    spy = AsyncMock()
    monkeypatch.setattr(action_execution, "_pool", fake_pool)
    monkeypatch.setattr(action_execution, "_context", fake_context)
    monkeypatch.setattr(adapter_registry, "execute_transfer", spy)
    session = SimpleNamespace(turn_id=1)
    result = await action_execution.execute_connected_voice_action(
        session, "transfer_call", {"destination": "+442070000000"}, "yes")
    assert result["status"] == "needs_details" and result["success"] is False
    assert "Sales, Support" in result["message"]
    spy.assert_not_awaited()


# ------------------------------------------------------------------- inbound
def test_inbound_offers_only_allow_listed_destinations():
    brief = norm(transfer_destinations=[SALES, SUPPORT])
    session = make_session(brief, direction="inbound", numbers=[SUPPORT["number"]])
    spec = cascaded_transfer(session)
    assert "Sales" not in spec["description"] and "Support" in spec["description"]
    assert "required" not in spec["parameters"]  # one left: parameter optional
    assert _parameters(session, session._voice_action_context, "transfer_call", {})[0] == {
        "destination": SUPPORT["number"]}
    with pytest.raises(ValueError, match="Support"):
        _parameters(session, session._voice_action_context, "transfer_call", {"destination": "Sales"})


def test_inbound_with_none_allow_listed_offers_no_tool():
    brief = norm(transfer_destinations=[SALES, SUPPORT])
    base = ctx(brief, call_status="in_progress", provider="asterisk", transfer_connected=True,
               email_connected=False, call_direction="inbound", direction="inbound",
               inbound_transfer_available=True, inbound_transfer_numbers=set())
    assert "transfer_call" not in _capabilities(base)
    base["inbound_transfer_numbers"] = {SALES["number"]}
    assert "transfer_call" in _capabilities(base)


def test_inbound_policy_check_runs_per_destination(monkeypatch):
    from app.domain.services.telephony import inbound_transfer
    monkeypatch.setattr(inbound_transfer, "inbound_transfer_scope_available", lambda **kw: True)
    brief = norm(transfer_destinations=[SALES, SUPPORT])
    context = {"brief": brief, "route_snapshot": {
        "route": {"config_id": "c", "called_did": "+442070000001"},
        "inbound_config": {"transfer_policy": {"enabled": True, "destinations": [SUPPORT["number"]]}}},
        "admission_status": "allowed", "processing_status": "active",
        "billing_status": "reserved", "reserved_seconds": 60}
    assert action_execution._inbound_transfer_policy_available(context, "t", SALES["number"]) is False
    assert action_execution._inbound_transfer_policy_available(context, "t", SUPPORT["number"]) is True


# --------------------------------------------------------------------- hours
def at(text):  # a UTC instant
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_open_closed_boundaries_and_days():
    brief = norm(transfer_destinations=[SALES], transfer_hours=HOURS)
    # 2026-11-16 is a Monday; London is UTC+0 in November.
    assert transfer_open_now(brief, at("2026-11-16T09:00")) is True
    assert transfer_open_now(brief, at("2026-11-16T08:59")) is False
    assert transfer_open_now(brief, at("2026-11-16T17:29")) is True
    assert transfer_open_now(brief, at("2026-11-16T17:30")) is False
    assert transfer_open_now(brief, at("2026-11-14T12:00")) is False  # Saturday
    assert transfer_open_now({"approved_next_actions": ["transfer"]}, at("2026-11-14T03:00")) is True


def test_hours_use_the_brief_timezone_not_utc():
    hours = {"timezone": "Pacific/Auckland", "days": ["tue"], "start": "09:00", "end": "17:00"}
    brief = norm(transfer_destinations=[SALES], transfer_hours=hours)
    # Mon 2026-11-16 22:00 UTC is Tue 11:00 in Auckland (UTC+13).
    assert transfer_open_now(brief, at("2026-11-16T22:00")) is True
    # Tue 12:00 UTC is Wed 01:00 in Auckland.
    assert transfer_open_now(brief, at("2026-11-17T12:00")) is False


def test_naive_now_is_treated_as_utc():
    brief = norm(transfer_destinations=[SALES], transfer_hours=HOURS)
    assert transfer_open_now(brief, datetime(2026, 11, 16, 10, 0)) is True


def test_closed_means_no_tool_and_capability_dropped():
    brief = norm(transfer_destinations=[SALES], transfer_hours=HOURS)
    context = ctx(brief, call_status="in_progress", provider="asterisk", transfer_connected=True,
                  email_connected=False)
    assert "transfer_call" in _capabilities(context, now=at("2026-11-16T10:00"))
    assert "transfer_call" not in _capabilities(context, now=at("2026-11-16T18:00"))


def test_call_crossing_closing_time_stops_offering_the_tool():
    session = make_session(norm(transfer_destinations=[SALES, SUPPORT], transfer_hours=HOURS))
    assert cascaded_transfer(session, now=at("2026-11-16T17:29")) is not None
    assert "transfer_call" in enabled_voice_actions(session, now=at("2026-11-16T17:29"))
    assert cascaded_transfer(session, now=at("2026-11-16T17:31")) is None
    assert "transfer_call" not in enabled_voice_actions(session, now=at("2026-11-16T17:31"))


def test_prompt_open_lists_destinations_and_conditions():
    brief = norm(transfer_destinations=[SALES, SUPPORT], transfer_hours=HOURS)
    text = render_campaign_brief(brief, now=at("2026-11-16T10:00"))
    assert "Sales: The caller wants prices or wants to buy." in text
    assert "Support: The caller has a problem with an existing order." in text
    assert "not available right now" not in text
    assert "+44207" not in text  # numbers never go to the model


def test_prompt_closed_says_unavailable_and_when_available():
    brief = norm(transfer_destinations=[SALES, SUPPORT], transfer_hours=HOURS)
    text = render_campaign_brief(brief, now=at("2026-11-14T12:00"))
    assert "not available right now" in text
    assert "09:00" in text and "17:30" in text and "Europe/London" in text
    assert "Monday to Friday" in text
    assert "message" in text and "do not promise a transfer" in text.lower()
    assert "Sales: The caller wants" not in text


def test_prompt_legacy_unchanged():
    text = render_campaign_brief(norm(transfer_destination="Sales desk"))
    assert "- Approved transfer destination: Sales desk" in text


# -- lead review additions ----------------------------------------------------
def test_tool_description_separates_destinations_even_without_full_stops():
    from types import SimpleNamespace

    from app.domain.services.voice_pipeline.action_tools import action_description_suffix

    session = SimpleNamespace(_voice_action_context={"brief": {"transfer_destinations": [
        {"name": "Sales", "number": "+442071234567", "when": "The caller wants prices"},
        {"name": "Support", "number": "+442079876543", "when": "The caller has a problem."},
    ]}})
    assert action_description_suffix("transfer_call", session) == (
        " Destinations: Sales: The caller wants prices; Support: The caller has a problem."
    )


def test_wrong_value_for_a_legacy_destination_never_echoes_the_stored_number():
    from app.domain.services.voice_pipeline import action_execution

    context = {"brief": {"transfer_destination": "+442071234567"}}
    with pytest.raises(ValueError) as error:
        action_execution._parameters(None, context, "transfer_call", {"destination": "sales"})
    assert "+442071234567" not in str(error.value)
    assert "configured transfer destination" in str(error.value)
