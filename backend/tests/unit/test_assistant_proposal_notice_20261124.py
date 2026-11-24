"""A tool's plain-language ``notice`` reaches the customer through the confirm card and the spoken note."""
from app.api.v1.endpoints import assistant_ws
from app.api.v1.endpoints.assistant_ws import proposal_apply_result, proposal_outcome_note

PROPOSAL = {"tool": "start_campaign", "changes": [{"field": "status"}], "campaigns": [{"campaign_id": "c1"}]}
NOTICE = "No contact was queued. 3 contacts were not queued because no permission to call is recorded."


def test_applied_with_notice_is_carried_and_ends_the_note():
    outcome = proposal_apply_result("p1", PROPOSAL, {"success": True, "notice": f"  {NOTICE}  "})
    assert outcome["applied"] is True
    assert outcome["notice"] == NOTICE
    note = proposal_outcome_note(outcome)
    assert note == f"Applied the proposed changes. {NOTICE}"


def test_applied_without_notice_is_exactly_todays_outcome():
    outcome = proposal_apply_result("p1", PROPOSAL, {"success": True})
    assert outcome == {
        "type": "proposal_result", "proposal_id": "p1", "applied": True,
        "changes": [{"field": "status"}], "campaigns": [{"campaign_id": "c1"}],
        "action_id": None, "status": "completed", "confirmation_allowed": True,
        "receipt": {}, "error": None,
    }
    assert "notice" not in outcome
    assert proposal_outcome_note(outcome) == "Applied the proposed changes."


def test_not_applied_ignores_notice():
    outcome = proposal_apply_result("p1", PROPOSAL, {"success": False, "error": "Nope", "notice": NOTICE})
    assert outcome["applied"] is False
    assert "notice" not in outcome
    assert outcome["error"] == "Nope"
    assert proposal_outcome_note(outcome) == "Nope"


def test_non_string_and_blank_notice_are_ignored():
    for bad in (None, 5, ["x"], {"a": 1}, "", "   \n"):
        outcome = proposal_apply_result("p1", PROPOSAL, {"success": True, "notice": bad})
        assert "notice" not in outcome, bad
        assert proposal_outcome_note(outcome) == "Applied the proposed changes."


def test_long_notice_is_cut_to_600():
    outcome = proposal_apply_result("p1", PROPOSAL, {"success": True, "notice": "a" * 900})
    assert outcome["notice"] == "a" * 600


def test_notice_survives_saved_receipt_merge(monkeypatch):
    monkeypatch.setattr(assistant_ws, "public_action_receipt", lambda row: {
        "action_id": "act-1", "success": True, "confirmation_allowed": True,
        "status": "completed", "receipt": {}, "error": None})
    outcome = proposal_apply_result("p1", PROPOSAL, {"success": True, "action_id": "act-1", "notice": NOTICE})
    assert outcome["action_id"] == "act-1"
    assert outcome["notice"] == NOTICE


def test_provider_accepted_note_keeps_its_sentence_and_appends_notice():
    outcome = {"applied": True, "receipt": {"provider_status": "accepted"}, "notice": NOTICE}
    assert proposal_outcome_note(outcome) == f"The provider accepted the request. Delivery is not confirmed. {NOTICE}"


# ---- the real start tool through the real channel (both halves together) ----

import pytest  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("enqueued, withheld, selected, starts_with", [
    (0, 3, 0, "No contact was queued. 3 contacts were not queued"),   # every contact held back
    (2, 1, 2, "1 contact was not queued"),                            # some held back
])
async def test_a_start_with_held_back_contacts_reaches_the_customer(
        monkeypatch, enqueued, withheld, selected, starts_with):
    from app.infrastructure.assistant.tools.campaigns import start_campaign
    from tests.unit.test_assistant_campaign_scoping import (
        CAMPAIGN_A, TENANT, _fake_start_service, _start_tool_client,
    )

    _fake_start_service(monkeypatch, enqueued=enqueued, withheld=withheld, selected=selected)
    result = await start_campaign(TENANT, _start_tool_client(), CAMPAIGN_A, confirm=True)
    outcome = proposal_apply_result("p1", PROPOSAL, result)

    assert outcome["applied"] is True and outcome["error"] is None
    assert outcome["notice"].startswith(starts_with)
    assert proposal_outcome_note(outcome) == "Applied the proposed changes. " + outcome["notice"]


@pytest.mark.asyncio
async def test_a_start_with_the_check_off_carries_no_notice(monkeypatch):
    from app.infrastructure.assistant.tools.campaigns import start_campaign
    from tests.unit.test_assistant_campaign_scoping import (
        CAMPAIGN_A, TENANT, _fake_start_service, _start_tool_client,
    )

    _fake_start_service(monkeypatch, enqueued=5, withheld=0)
    result = await start_campaign(TENANT, _start_tool_client(), CAMPAIGN_A, confirm=True)
    outcome = proposal_apply_result("p1", PROPOSAL, result)

    assert outcome["applied"] is True and "notice" not in outcome
    assert proposal_outcome_note(outcome) == "Applied the proposed changes."
